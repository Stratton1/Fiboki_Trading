"""The fail-open calendar default is now gated at the two run entry points.

* ``fiboki.validation.run.run_validation``: a supplied calendar must cover the
  bars and the instrument's currencies, or the run refuses with
  ``USER_ACTION_NOTE`` unless ``allow_empty_calendar=True``.
* ``scripts/run_paper_session.py``: the official calendar must cover the replay
  or the script exits, unless ``--allow-empty-calendar``.

Each validation test stops the run immediately AFTER the guard by choosing an
instrument outside the document's universe, which ``EngineEvaluator`` refuses.
Reaching that refusal proves the guard let the run through without paying for
a ladder run.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.marketstate.calendar import (
    USER_ACTION_NOTE,
    CalendarError,
    InMemoryEconomicCalendar,
    load_official_calendar,
)
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.run import run_validation

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "research" / "strategies" / "rsi_band_mean_reversion.json"
UNIVERSE_REFUSAL = "not in the document's universe"


def _bars(start: str, periods: int = 500) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="4h", tz="UTC")
    close = 2000.0 + np.cumsum(np.random.default_rng(3).normal(0, 1, periods))
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close}, index=index
    )


@pytest.fixture(scope="module")
def document() -> StrategyDocument:
    doc = StrategyDocument.from_json(SEED.read_text(encoding="utf-8"))
    assert doc.events.block_minutes_before > 0  # the seed declares a blackout
    assert "XAUUSD" not in doc.universe
    return doc


def _run(document, bars, **kwargs):
    return run_validation(
        document=document,
        bars=bars,
        dataset_version_id="calendar_guard_v1",
        instrument="XAUUSD",
        timeframe=Timeframe.H4,
        registry=HoldoutRegistry.in_memory(),
        account_ccy="USD",
        **kwargs,
    )


def test_an_empty_calendar_is_refused_with_the_user_action_note(document) -> None:
    with pytest.raises(CalendarError) as info:
        _run(document, _bars("2024-02-01"), calendar=InMemoryEconomicCalendar.empty())
    assert USER_ACTION_NOTE in str(info.value)


def test_a_calendar_that_does_not_span_the_bars_is_refused(document) -> None:
    with pytest.raises(CalendarError, match="does not span"):
        _run(document, _bars("2015-01-01"), calendar=load_official_calendar())


def test_the_explicit_opt_out_lets_the_run_proceed(document) -> None:
    with pytest.raises(ValueError, match=UNIVERSE_REFUSAL):
        _run(
            document,
            _bars("2015-01-01"),
            calendar=InMemoryEconomicCalendar.empty(),
            allow_empty_calendar=True,
        )


def test_a_covering_calendar_passes_the_guard(document) -> None:
    with pytest.raises(ValueError, match=UNIVERSE_REFUSAL):
        _run(document, _bars("2024-02-01"), calendar=load_official_calendar())


def test_no_calendar_with_a_declared_blackout_is_loud(document, caplog) -> None:
    """The known gap: not refused (every current caller passes none), but logged."""
    with (
        caplog.at_level(logging.WARNING, logger="fiboki.validation.run"),
        pytest.raises(ValueError, match=UNIVERSE_REFUSAL),
    ):
        _run(document, _bars("2015-01-01"))
    assert any("without an economic calendar" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------- paper script


@pytest.fixture(scope="module")
def paper_script():
    spec = importlib.util.spec_from_file_location(
        "run_paper_session_under_test", REPO / "scripts" / "run_paper_session.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_paper_session_refuses_an_uncovered_replay(paper_script) -> None:
    with pytest.raises(SystemExit) as info:
        paper_script._calendar_guard("XAUUSD", _bars("2015-01-01"), allow_empty=False)
    assert "USER ACTION REQUIRED" in str(info.value.code)
    assert "--allow-empty-calendar" in str(info.value.code)


def test_paper_session_refuses_an_uncovered_currency(paper_script) -> None:
    with pytest.raises(SystemExit, match="AUD"):
        paper_script._calendar_guard("AUDUSD", _bars("2024-02-01"), allow_empty=False)


def test_paper_session_opt_out_is_recorded(paper_script) -> None:
    """``--allow-empty-calendar`` lifts the coverage refusal, not the wiring:
    the calendar still reaches the gateway wherever it has events."""
    record = paper_script._calendar_guard("XAUUSD", _bars("2015-01-01"), allow_empty=True)
    assert record["covered"] is False
    assert record["allow_empty_calendar"] is True
    assert record["wired_into_gateway"] is True


def test_paper_session_no_calendar_is_recorded_as_unwired(paper_script) -> None:
    record = paper_script._calendar_guard(
        "XAUUSD", _bars("2015-01-01"), allow_empty=False, no_calendar=True
    )
    assert record["no_calendar"] is True
    assert record["wired_into_gateway"] is False
    assert record["covered"] is False


def test_paper_session_covered_replay_passes(paper_script) -> None:
    record = paper_script._calendar_guard("XAUUSD", _bars("2024-02-01"), allow_empty=False)
    assert record["covered"] is True
    assert record["allow_empty_calendar"] is False
