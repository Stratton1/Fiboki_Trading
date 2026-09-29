"""The evaluation cache key includes the blackout source's CONTENT.

Before this, ``EngineEvaluator.engine_config_hash`` recorded only the blackout
source's class name in per-evaluation metadata and nothing in the key, so an
evaluation computed with a calendar and one computed without (or with a
different calendar) shared a cache key. ``validation/run.py`` worked round it
by giving every calendar its own cache subdirectory; any other caller sharing a
cache would have been served the other calendar's answer.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.core.enums import Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.marketstate.calendar import EconomicEvent, ImpactLevel, InMemoryEconomicCalendar
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluationCache,
    EvaluatorConfig,
    UnfingerprintableBlackout,
    blackout_fingerprint,
)
from fiboki.validation.evaluation import DateWindow

ROOT = Path(__file__).resolve().parents[2]
DATASET = "synthetic_xauusd_h4_blackout_key_v1"


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(3600, with_volume=False) * 1000.0


@pytest.fixture(scope="module")
def document() -> StrategyDocument:
    doc = StrategyDocument.from_json(
        (ROOT / "research" / "strategies" / "donchian_breakout_atr.json").read_text()
    )
    assert doc.events.block_minutes_before > 0, "the probe must declare a blackout"
    return doc


@pytest.fixture(scope="module")
def window(bars) -> DateWindow:
    return DateWindow("probe", bars.index[900], bars.index[-1])


def _evaluator(document, bars, *, blackout=None, cache=None) -> EngineEvaluator:
    return EngineEvaluator(
        document=document,
        frame=bars,
        dataset_version_id=DATASET,
        config=EvaluatorConfig(
            instrument="XAUUSD",
            timeframe=Timeframe.H4,
            account_ccy="USD",
            initial_balance=100_000.0,
        ),
        fx=IdentityFxSource(),
        cache=cache,
        blackout=blackout,
    )


def _calendar_at(times) -> InMemoryEconomicCalendar:
    return InMemoryEconomicCalendar(
        EconomicEvent(
            event_time=pd.Timestamp(t),
            currency="USD",
            name="Synthetic release",
            impact=ImpactLevel.HIGH,
        )
        for t in times
    )


def test_no_blackout_keeps_the_hash_it_always_had(document, bars) -> None:
    """No key is added for a no-calendar run, so its cache entries stay valid."""
    evaluator = _evaluator(document, bars)
    assert "blackout" not in evaluator.engine_fingerprint()


def test_the_calendar_content_is_part_of_the_engine_hash(document, bars) -> None:
    first = pd.Timestamp("2021-06-01 13:00", tz="UTC")
    a = _calendar_at([first])
    a_again = _calendar_at([first])
    b = _calendar_at([first + pd.Timedelta(hours=4)])

    none = _evaluator(document, bars).engine_config_hash()
    with_a = _evaluator(document, bars, blackout=a).engine_config_hash()
    with_a_again = _evaluator(document, bars, blackout=a_again).engine_config_hash()
    with_b = _evaluator(document, bars, blackout=b).engine_config_hash()
    with_empty = _evaluator(
        document, bars, blackout=InMemoryEconomicCalendar.empty()
    ).engine_config_hash()

    assert with_a == with_a_again, "same events, same question"
    assert len({none, with_a, with_b, with_empty}) == 4
    assert blackout_fingerprint(a)["n_events"] == 1


def test_a_shared_cache_never_serves_one_calendars_answer_for_another(
    document, bars, window, tmp_path
) -> None:
    cache = EvaluationCache(tmp_path / "cache")
    params = document.default_values()

    blind = _evaluator(document, bars, cache=cache)
    baseline = blind(params, window)
    assert baseline.trades, "the probe must trade or this test proves nothing"

    # A release at every entry the blind run took: the blackout must remove them.
    calendar = _calendar_at(t.entry_time for t in baseline.trades)
    aware = _evaluator(document, bars, cache=cache, blackout=calendar)
    blacked_out = aware(params, window)
    assert aware.n_engine_runs == 1, "served from the no-calendar entry"
    assert blacked_out.meta["ledger_sha256"] != baseline.meta["ledger_sha256"]
    assert blacked_out.meta["cache_key"] != baseline.meta["cache_key"]

    # The same calendar content in a new object is the same question: a hit.
    again = _evaluator(document, bars, cache=cache, blackout=_calendar_at(
        t.entry_time for t in baseline.trades
    ))
    replay = again(params, window)
    assert again.n_engine_runs == 0
    assert replay.n_trades == blacked_out.n_trades


def test_an_unhashable_blackout_source_is_refused_behind_a_cache(
    document, bars, tmp_path
) -> None:
    class Opaque:
        def in_blackout(self, instrument, ts, **kwargs) -> bool:
            return False

    # Without a cache it is merely recorded as unhashable.
    uncached = _evaluator(document, bars, blackout=Opaque())
    assert uncached.engine_fingerprint()["blackout"]["content"] == "unhashable"
    with pytest.raises(UnfingerprintableBlackout):
        _evaluator(document, bars, blackout=Opaque(), cache=EvaluationCache(tmp_path))
