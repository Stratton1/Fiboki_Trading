"""The market-state engine end to end, including on the real HistData files.

The load-bearing test here is :func:`test_bar_by_bar_equals_bulk`. A paper bot
receives one bar at a time; research computes everything at once. If those two
paths disagree by even a label, every regime-conditional result produced in the
laboratory is a statement about a thing the live system never sees. They are
asserted equal bar for bar, not approximately.

The real-data tests are skipped when the V1 HistData store is absent, and are
deliberately written to exercise the file's known defects rather than to avoid
them: bid-only prices, a fixed EST clock, identically-zero volume, and the
negative-price sentinel bar in EURUSD H1.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from data_fixtures import V1_HISTDATA_ROOT, requires_v1_data
from synthetic_prices import synthetic_ohlcv

from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction, Timeframe
from fiboki.marketstate.calendar import (
    EconomicEvent,
    ImpactLevel,
    InMemoryEconomicCalendar,
)
from fiboki.marketstate.features import FeatureConfig, invalid_bar_mask
from fiboki.marketstate.regime import (
    RegimeAxis,
    RegimeConfig,
    regime_column,
)
from fiboki.marketstate.state_engine import (
    EngineConfig,
    MarketStateEngine,
    StateEngineError,
    build_engine,
)

FAST = FeatureConfig(
    trend_horizons=(10, 25, 50),
    momentum_horizons=(5, 10, 25),
    vol_periods=(10, 50),
    variance_ratio_window=50,
    autocorr_window=50,
    moment_window=50,
    gap_window=50,
    abnormal_window=50,
    percentile_min_periods=50,
    primary_vol_period=10,
    primary_trend_horizon=25,
)
FAST_ENGINE = EngineConfig(
    timeframe=Timeframe.H4,
    features=FAST,
    regime=RegimeConfig(direction_horizon=25, volatility_period=10),
)


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(n=900)


# =====================================================================
# Bulk / incremental parity — the whole point of the engine
# =====================================================================


def test_bar_by_bar_equals_bulk(bars: pd.DataFrame) -> None:
    """The paper-bot path and the research path must produce identical state."""
    bulk = MarketStateEngine(config=FAST_ENGINE)
    bulk.ingest_frame("EURUSD", bars)

    streamed = MarketStateEngine(config=FAST_ENGINE)
    streamed.ingest_frame("EURUSD", bars.iloc[:860])
    for ts, row in bars.iloc[860:].iterrows():
        streamed.ingest_bar("EURUSD", row, timestamp=ts)

    pd.testing.assert_frame_equal(
        bulk.history("EURUSD"), streamed.history("EURUSD")
    )
    assert bulk.snapshot("EURUSD").to_dict() == streamed.snapshot("EURUSD").to_dict()


def test_streaming_from_scratch_matches_bulk_on_a_short_series() -> None:
    """The same property with no bulk priming at all."""
    frame = synthetic_ohlcv(n=320)
    cfg = EngineConfig(
        timeframe=Timeframe.H4,
        features=FeatureConfig(
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
        ),
        regime=RegimeConfig(direction_horizon=10, volatility_period=10),
    )
    bulk = MarketStateEngine(config=cfg)
    bulk.ingest_frame("EURUSD", frame)
    streamed = MarketStateEngine(config=cfg)
    seen_none = False
    for ts, row in frame.iterrows():
        if streamed.ingest_bar("EURUSD", row, timestamp=ts) is None:
            seen_none = True
    assert seen_none, "a cold-started engine must admit it has nothing to say"
    pd.testing.assert_frame_equal(bulk.history("EURUSD"), streamed.history("EURUSD"))


# =====================================================================
# Ingestion hygiene
# =====================================================================


def test_out_of_order_bars_are_refused(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    stale = bars.iloc[100]
    with pytest.raises(StateEngineError, match="not after the last held bar"):
        engine.ingest_bar("EURUSD", stale, timestamp=bars.index[100])


def test_naive_timestamps_are_refused(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    naive = bars.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(StateEngineError, match="tz-aware"):
        engine.ingest_frame("EURUSD", naive)


def test_re_ingesting_a_block_replaces_the_overlap(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    revised = bars.iloc[-10:].copy()
    revised["close"] = revised["close"] * 1.01
    engine.ingest_frame("EURUSD", revised)
    assert len(engine.bars("EURUSD")) == len(bars)
    assert engine.bars("EURUSD")["close"].iloc[-1] == pytest.approx(
        bars["close"].iloc[-1] * 1.01
    )


def test_unknown_instrument_is_reported() -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    with pytest.raises(StateEngineError, match="no state for"):
        engine.snapshot("EURUSD")


def test_too_few_bars_is_reported_not_faked() -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    assert engine.ingest_frame("EURUSD", synthetic_ohlcv(n=2)) is None
    with pytest.raises(StateEngineError, match="nothing to say yet"):
        engine.snapshot("EURUSD")


def test_history_cap_is_flagged(bars: pd.DataFrame) -> None:
    cfg = EngineConfig(
        timeframe=Timeframe.H4,
        features=FAST,
        regime=RegimeConfig(direction_horizon=25, volatility_period=10),
        max_history_bars=600,
    )
    engine = MarketStateEngine(config=cfg)
    engine.ingest_frame("EURUSD", bars)
    snap = engine.snapshot("EURUSD")
    assert snap.history_capped is True
    assert snap.bars_seen == 600


def test_a_cap_below_the_warmup_is_refused() -> None:
    with pytest.raises(StateEngineError, match="cannot support the feature warmups"):
        EngineConfig(max_history_bars=100)


# =====================================================================
# Snapshots
# =====================================================================


def test_snapshot_shape(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    snap = engine.ingest_frame("EURUSD", bars)
    assert snap.instrument == "EURUSD"
    assert snap.as_of == bars.index[-1]
    assert snap.warm is True
    assert snap.regime_key.count("|") == 4
    assert snap.regime.key == snap.regime_key
    assert snap.bars_since_regime_change is not None
    assert snap.bars_since_regime_change >= 0
    assert len(snap.feature_fingerprint) == 32
    assert len(snap.regime_fingerprint) == 32
    assert snap.feature("rv_10") == pytest.approx(snap.features["rv_10"], nan_ok=True)
    with pytest.raises(KeyError):
        snap.feature("not_a_feature")
    assert json.loads(json.dumps(snap.to_dict()))["instrument"] == "EURUSD"


def test_bars_since_regime_change_counts_up(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars.iloc[:-3])
    seen = []
    for ts, row in bars.iloc[-3:].iterrows():
        snap = engine.ingest_bar("EURUSD", row, timestamp=ts)
        seen.append((snap.regime_key, snap.bars_since_regime_change))
    for i in range(1, len(seen)):
        if seen[i][0] == seen[i - 1][0]:
            assert seen[i][1] == seen[i - 1][1] + 1
        else:
            assert seen[i][1] == 0


def test_snapshots_skips_instruments_that_are_not_ready(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    assert engine.ingest_frame("GBPUSD", bars.iloc[:2]) is None
    snaps = engine.snapshots()
    assert set(snaps) == {"EURUSD"}


# =====================================================================
# History, joins and persistence
# =====================================================================


def test_history_carries_regime_and_features(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    hist = engine.history("EURUSD")
    assert "regime_key" in hist.columns
    assert "warm" in hist.columns
    assert "rv_10" in hist.columns
    assert hist["instrument"].eq("EURUSD").all()
    assert hist["warm"].sum() == len(hist) - engine.regimes("EURUSD").warmup
    lean = engine.history("EURUSD", include_features=False)
    assert "rv_10" not in lean.columns


def test_regime_at_returns_the_state_that_was_current(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    r = engine.regimes("EURUSD")
    warm = r.frame.iloc[r.warmup :]
    ts = warm.index[50]
    assert engine.regime_at("EURUSD", ts).key == warm["regime_key"].iloc[50]
    # Mid-bar: still the last closed bar's regime.
    assert engine.regime_at("EURUSD", ts + pd.Timedelta(hours=1)).key == (
        warm["regime_key"].iloc[50]
    )
    assert engine.regime_at("EURUSD", bars.index[0]) is None


def test_attach_regime_joins_signals_to_the_state_they_fired_in(
    bars: pd.DataFrame,
) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    r = engine.regimes("EURUSD")
    warm = r.frame.iloc[r.warmup :]
    signals = [
        Signal(
            strategy_id="s1",
            instrument="EURUSD",
            timeframe="H4",
            direction=Direction.LONG,
            bar_time=ts + pd.Timedelta(hours=1),
            reference_price=1.1,
            stop_price=1.09,
        )
        for ts in warm.index[10:20]
    ]
    out = engine.attach_regime(signals)
    assert len(out) == 10
    assert out["regime_key"].notna().all()
    assert list(out["regime_key"]) == list(warm["regime_key"].iloc[10:20])
    # The signal's own direction survives; the regime axis is prefixed.
    assert out["direction"].eq("long").all()
    assert regime_column(RegimeAxis.DIRECTION) in out.columns


def test_attach_regime_leaves_pre_warmup_signals_unclassified(
    bars: pd.DataFrame,
) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    early = Signal(
        strategy_id="s1",
        instrument="EURUSD",
        timeframe="H4",
        direction=Direction.SHORT,
        bar_time=bars.index[2],
        reference_price=1.1,
        stop_price=1.11,
    )
    out = engine.attach_regime([early])
    assert out["regime_key"].isna().all()


def test_attach_regime_handles_an_unknown_instrument(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    sig = Signal(
        strategy_id="s1",
        instrument="GBPUSD",
        timeframe="H4",
        direction=Direction.LONG,
        bar_time=bars.index[-1],
        reference_price=1.3,
        stop_price=1.29,
    )
    out = engine.attach_regime([sig])
    assert out["regime_key"].isna().all()


def test_attach_regime_accepts_a_frame(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    r = engine.regimes("EURUSD")
    warm = r.frame.iloc[r.warmup :]
    frame = pd.DataFrame(
        {"instrument": ["eurusd"] * 3, "bar_time": list(warm.index[5:8])}
    )
    out = engine.attach_regime(frame)
    assert out["regime_key"].notna().all()


def test_persist_and_reload(tmp_path, bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    root = engine.persist(tmp_path / "state")
    manifest = MarketStateEngine.load_manifest(root)
    assert manifest["instruments"]["EURUSD"]["rows"] == len(bars)
    assert manifest["config"]["timeframe"] == "H4"
    assert manifest["instruments"]["EURUSD"]["feature_fingerprint"] == (
        engine.features("EURUSD").fingerprint
    )
    back = MarketStateEngine.load_history(root, "EURUSD")
    assert len(back) == len(bars)
    assert list(back["regime_key"]) == list(engine.history("EURUSD")["regime_key"])
    with pytest.raises(StateEngineError, match="no persisted history"):
        MarketStateEngine.load_history(root, "GBPUSD")


# =====================================================================
# Calendar wiring
# =====================================================================


def test_blackout_is_false_without_a_calendar(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    assert engine.blackout("EURUSD", bars.index[-1]) is False


def test_blackout_uses_the_attached_calendar(bars: pd.DataFrame) -> None:
    ts = bars.index[-1]
    cal = InMemoryEconomicCalendar(
        [EconomicEvent(ts, "USD", "FOMC Rate Decision", ImpactLevel.HIGH)]
    )
    engine = MarketStateEngine(config=FAST_ENGINE, calendar=cal)
    engine.ingest_frame("EURUSD", bars)
    assert engine.blackout("EURUSD", ts) is True
    assert engine.blackout("EURUSD", ts + pd.Timedelta(hours=3)) is False


# =====================================================================
# Cross-asset wiring
# =====================================================================


def test_cross_asset_requires_two_instruments(bars: pd.DataFrame) -> None:
    engine = MarketStateEngine(config=FAST_ENGINE)
    engine.ingest_frame("EURUSD", bars)
    assert engine.update_cross_asset() is None
    assert engine.cross_asset is None


def test_build_engine_wires_cross_asset_stress(bars: pd.DataFrame) -> None:
    other = bars.copy()
    other["close"] = other["close"] * 1.2
    other["open"] = other["open"] * 1.2
    other["high"] = other["high"] * 1.2
    other["low"] = other["low"] * 1.2
    engine = build_engine(
        {"EURUSD": bars, "GBPUSD": other},
        config=FAST_ENGINE,
        with_cross_asset=True,
    )
    assert engine.cross_asset is not None
    assert engine.cross_asset.panel.coverage.common_bars == len(bars)
    notes = engine.snapshot("EURUSD").notes
    assert any("cross_asset:" in n for n in notes)


# =====================================================================
# Real HistData — defects and all
# =====================================================================


@pytest.fixture(scope="module")
def histdata_provider():
    from fiboki.data.providers.histdata import HistDataParquetProvider

    return HistDataParquetProvider(V1_HISTDATA_ROOT)


@requires_v1_data
def test_real_xauusd_h4_runs_end_to_end(histdata_provider) -> None:
    batch = histdata_provider.fetch_bars("XAUUSD", Timeframe.H4)
    engine = MarketStateEngine(config=EngineConfig(timeframe=Timeframe.H4))
    snap = engine.ingest_frame("XAUUSD", batch.frame)
    assert snap.warm
    assert snap.bars_seen > 20_000
    # The file has no volume and no quotes; both must be flagged, not filled in.
    assert snap.features["volume_available"] == 0.0
    assert snap.features["spread_available"] == 0.0
    assert np.isnan(snap.features["volume_z"])
    assert np.isnan(snap.features["spread_rel"])
    # The high/low spread estimator still works, which is why it is there.
    assert np.isfinite(snap.features["cs_spread_rel"])

    described = engine.describe("XAUUSD")
    assert described["bars_classified"] > 20_000
    assert described["estimated_flap_rate"] < 0.25
    assert described["mean_key_duration_bars"] > 1.0


@requires_v1_data
def test_real_timestamps_are_corrected_to_utc(histdata_provider) -> None:
    """The +5h EST correction must be visible in the session features."""
    batch = histdata_provider.fetch_bars("XAUUSD", Timeframe.H4)
    engine = MarketStateEngine(config=EngineConfig(timeframe=Timeframe.H4))
    engine.ingest_frame("XAUUSD", batch.frame)
    hist = engine.history("XAUUSD")
    # H4 bars land on 01/05/09/13/17/21 UTC after the correction. The 13:00 UTC
    # bar is inside the London/New York overlap; the 01:00 bar never is.
    hours = hist.index.hour
    overlap = hist["is_london_ny_overlap"].to_numpy()
    assert overlap[hours == 13].mean() > 0.9
    assert overlap[hours == 1].sum() == 0


@requires_v1_data
def test_real_eurusd_h1_sentinel_is_handled_not_absorbed(histdata_provider) -> None:
    """The 2001-09-11 20:00 EST bar with OHLC all -0.0001."""
    batch = histdata_provider.fetch_bars("EURUSD", Timeframe.H1)
    mask = invalid_bar_mask(batch.frame)
    assert mask.sum() == 1
    bad_ts = batch.frame.index[mask][0]
    # +5h from the documented 2001-09-11 20:00 EST stamp.
    assert bad_ts == pd.Timestamp("2001-09-12 01:00", tz="UTC")

    strict = MarketStateEngine(
        config=EngineConfig(timeframe=Timeframe.H1, on_invalid_bars="raise")
    )
    with pytest.raises(Exception, match="not prices"):
        strict.ingest_frame("EURUSD", batch.frame)

    engine = MarketStateEngine(
        config=EngineConfig(timeframe=Timeframe.H1, on_invalid_bars="drop")
    )
    engine.ingest_frame("EURUSD", batch.frame.loc[:"2002-01-01"])
    fs = engine.features("EURUSD")
    assert fs.dropped_bars.count == 1
    assert fs.dropped_bars.timestamps[0] == bad_ts
    # Nothing downstream is poisoned: a retained -0.0001 close produces log
    # returns of order 1e4 and a realised volatility to match.
    warm = fs.valid()
    assert np.isfinite(warm["rv_20"].dropna().to_numpy()).all()
    assert warm["rv_20"].max() < 1.0
    assert warm["ret_z"].abs().max() < 100.0


@requires_v1_data
def test_real_cross_asset_panel_reports_coverage(histdata_provider) -> None:
    eur = histdata_provider.fetch_bars("EURUSD", Timeframe.H4).frame
    xau = histdata_provider.fetch_bars("XAUUSD", Timeframe.H4).frame
    engine = build_engine(
        {"EURUSD": eur.loc["2010":"2012"], "XAUUSD": xau.loc["2010":"2012"]},
        config=EngineConfig(timeframe=Timeframe.H4),
        with_cross_asset=True,
    )
    cross = engine.cross_asset
    assert cross is not None
    cov = cross.panel.coverage
    assert cov.common_bars > 3_000
    # The two files do not share a clock exactly; the report must say so rather
    # than the code forward-filling the difference away.
    assert 0.0 < cov.worst_coverage <= 1.0
    assert cross.risk is None  # no AUD/NZD in this panel
    assert any("risk appetite unavailable" in note for note in cross.notes)
