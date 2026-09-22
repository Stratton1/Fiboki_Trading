"""``MarketStateEngine.replay`` — the O(n) path, held to the O(n^2) answer.

``ingest_bar`` recomputes over the retained history, so replaying ``n`` bars
through it costs ``O(n^2)``. On the 26,837-bar XAUUSD H4 file that is hours,
which is why the state engine had to be dropped from the long demo.

:meth:`MarketStateEngine.replay` computes the history once and reads each bar's
state off the prefix that ends at it. That is only legitimate if the two paths
agree *exactly*, so this module compares them snapshot by snapshot, field by
field, including the notes — because a note is part of what a snapshot says
about itself, and a replay that quietly reported whole-history notes would
mis-describe every early bar.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.core.enums import Timeframe
from fiboki.marketstate.features import FeatureConfig
from fiboki.marketstate.regime import RegimeConfig
from fiboki.marketstate.state_engine import (
    EngineConfig,
    MarketStateEngine,
    StateEngineError,
)

XAUUSD_H4 = Path("data/canonical/histdata/XAUUSD/xauusd_h4.parquet")
requires_xauusd = pytest.mark.skipif(
    not XAUUSD_H4.exists(), reason=f"XAUUSD H4 not present at {XAUUSD_H4}"
)

FAST = FeatureConfig(
    trend_horizons=(5, 10, 20),
    momentum_horizons=(5, 10),
    vol_periods=(10, 20),
    variance_ratio_window=30,
    autocorr_window=30,
    moment_window=30,
    gap_window=30,
    abnormal_window=30,
    percentile_min_periods=20,
    primary_vol_period=10,
    primary_trend_horizon=10,
)
FAST_ENGINE = EngineConfig(
    timeframe=Timeframe.H4,
    features=FAST,
    regime=RegimeConfig(direction_horizon=10, volatility_period=10),
)


def ingest_one_at_a_time(
    config: EngineConfig, instrument: str, frame: pd.DataFrame
) -> list[dict | None]:
    """What a paper bot would have seen, bar by bar, with no shortcuts."""
    engine = MarketStateEngine(config=config)
    out: list[dict | None] = []
    for ts, row in frame.iterrows():
        snap = engine.ingest_bar(instrument, row, timestamp=ts)
        out.append(None if snap is None else snap.to_dict())
    return out


def replayed(
    config: EngineConfig, instrument: str, frame: pd.DataFrame
) -> list[dict | None]:
    engine = MarketStateEngine(config=config)
    return [
        None if s is None else s.to_dict() for s in engine.replay(instrument, frame)
    ]


def assert_snapshots_identical(
    truth: list[dict | None], got: list[dict | None]
) -> None:
    assert len(truth) == len(got), "replay must yield one entry per input bar"
    for i, (a, b) in enumerate(zip(truth, got, strict=True)):
        if a is None or b is None:
            assert a == b, (
                f"bar {i}: one path produced a snapshot and the other did not "
                f"(bar-by-bar={'None' if a is None else 'snapshot'}, "
                f"replay={'None' if b is None else 'snapshot'})"
            )
            continue
        for field in sorted(set(a) | set(b)):
            assert a[field] == b[field], (
                f"bar {i}: field {field!r} differs\n"
                f"  bar-by-bar: {a[field]!r}\n"
                f"  replay:     {b[field]!r}"
            )


# =====================================================================
# Exact equivalence
# =====================================================================


def test_replay_equals_bar_by_bar_ingestion_from_cold() -> None:
    """The load-bearing test: every bar, every field, including notes."""
    frame = synthetic_ohlcv(n=320)
    assert_snapshots_identical(
        ingest_one_at_a_time(FAST_ENGINE, "EURUSD", frame),
        replayed(FAST_ENGINE, "EURUSD", frame),
    )


def test_replay_reports_none_for_exactly_the_bars_the_engine_cannot_classify() -> None:
    frame = synthetic_ohlcv(n=320)
    truth = ingest_one_at_a_time(FAST_ENGINE, "EURUSD", frame)
    got = replayed(FAST_ENGINE, "EURUSD", frame)
    assert [s is None for s in truth] == [s is None for s in got]
    assert any(s is None for s in truth), "a cold start must have silent bars"
    assert got[-1] is not None


def test_replay_survives_a_sentinel_bar_the_way_ingestion_does() -> None:
    """A non-price bar is dropped from the features but still counted as a bar.

    The EURUSD H1 file carries exactly one of these. Replay has to reproduce
    both halves of that: the feature row that does not exist, and the
    ``bars_seen``/``as_of``/dropped-bar note that do.
    """
    frame = synthetic_ohlcv(n=320).copy()
    for column in ("open", "high", "low", "close"):
        frame.iloc[150, frame.columns.get_loc(column)] = -0.0001
    assert_snapshots_identical(
        ingest_one_at_a_time(FAST_ENGINE, "EURUSD", frame),
        replayed(FAST_ENGINE, "EURUSD", frame),
    )
    got = replayed(FAST_ENGINE, "EURUSD", frame)
    late = [s for s in got if s is not None][-1]
    assert any("dropped 1 invalid bar" in n for n in late["notes"])
    assert late["bars_seen"] == 320, "a dropped bar is still a bar that arrived"


def test_replay_leaves_the_engine_usable_afterwards() -> None:
    frame = synthetic_ohlcv(n=320)
    engine = MarketStateEngine(config=FAST_ENGINE)
    list(engine.replay("EURUSD", frame))
    assert engine.snapshot("EURUSD").bars_seen == 320
    assert len(engine.history("EURUSD")) == 320


# =====================================================================
# Refusals — replay says no rather than approximating
# =====================================================================


def test_replay_refuses_a_warm_instrument() -> None:
    frame = synthetic_ohlcv(n=320)
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", frame.iloc[:100])
    with pytest.raises(StateEngineError, match="must start from a cold instrument"):
        list(engine.replay("EURUSD", frame))


def test_replay_refuses_a_capped_history() -> None:
    """Under a cap the percentiles are rolling, and replay would not be that."""
    capped = EngineConfig(
        timeframe=Timeframe.H4,
        features=FAST,
        regime=RegimeConfig(direction_horizon=10, volatility_period=10),
        max_history_bars=500,
    )
    engine = MarketStateEngine(config=capped)
    with pytest.raises(StateEngineError, match="max_history_bars"):
        list(engine.replay("EURUSD", synthetic_ohlcv(n=320)))


# =====================================================================
# On the real file the demo could not replay
# =====================================================================


@requires_xauusd
def test_replay_matches_bar_by_bar_on_real_xauusd_bars() -> None:
    """Real prices, default (slow-warming) config, one bar at a time.

    Capped at 1,500 bars: the bar-by-bar reference is quadratic and this is
    already ~1 minute of honest work against ~0.1s of replay.
    """
    frame = pd.read_parquet(XAUUSD_H4).iloc[:1500]
    config = EngineConfig()
    assert_snapshots_identical(
        ingest_one_at_a_time(config, "XAUUSD", frame),
        replayed(config, "XAUUSD", frame),
    )


@requires_xauusd
@pytest.mark.slow
def test_replay_handles_the_whole_xauusd_h4_file() -> None:
    """26,837 bars — the run that had to be dropped from the demo."""
    frame = pd.read_parquet(XAUUSD_H4)
    assert len(frame) == 26837
    engine = MarketStateEngine(config=EngineConfig())
    snapshots = list(engine.replay("XAUUSD", frame))
    assert len(snapshots) == len(frame)
    classified = [s for s in snapshots if s is not None]
    assert len(classified) > 26000
    assert classified[-1].bars_seen == 26837
    assert classified[-1].warm is True
    assert classified[-1].history_capped is False
    assert classified[-1].regime_key.count("|") == 4
