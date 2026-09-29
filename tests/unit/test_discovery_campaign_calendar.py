"""A campaign cell runs against the official economic calendar by default.

Before this, ``run_cell`` never passed ``calendar=`` to ``run_validation``, so
every campaign cell ran with no blackout source at all: all five seed documents
declare an event blackout, and none of them was ever enforced in research.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

import fiboki.discovery.campaign as campaign_module
from fiboki.core.enums import Timeframe
from fiboki.discovery.campaign import (
    CampaignCheckpoint,
    CampaignRunner,
    CampaignSpec,
    CandidateCell,
    run_cell,
)
from fiboki.marketstate.calendar import InMemoryEconomicCalendar, load_official_calendar
from fiboki.research.experiment import ExperimentLedger
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig
from tests.discovery_fixtures import a_hypothesis, bar_source, one_barset, seed

BARS = one_barset(instrument="XAUUSD", version="ds_test_calendar")


def _spec(**overrides: Any) -> CampaignSpec:
    payload: dict[str, Any] = {
        "campaign_id": "k_calendar",
        "universe": ("XAUUSD",),
        "timeframes": (Timeframe.H4,),
        "hypotheses": (a_hypothesis(),),
        "actor": "tests:campaign-calendar",
        "account_ccy": "USD",
        "gate_set": GATE_SET_V2,
        "max_generations": 0,
        "include_prior_trials": False,
        "sweep_parameters": {
            "donchian_breakout_atr": ("channel_period", "stop_atr_multiple", "trail_atr_multiple")
        },
    }
    payload.update(overrides)
    return CampaignSpec(**payload)


def _cell() -> CandidateCell:
    return CandidateCell(
        document=seed("donchian_breakout_atr"),
        instrument="XAUUSD",
        timeframe=Timeframe.H4,
        origin="seed",
    )


@pytest.fixture
def captured(monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def spy(**kwargs: Any):
        seen.update(kwargs)
        raise RuntimeError("spy stops the run")

    monkeypatch.setattr(campaign_module, "run_validation", spy)
    return seen


def _run(spec: CampaignSpec, **kwargs: Any):
    return run_cell(
        cell=_cell(),
        bars=BARS,
        spec=spec,
        ladder_config=LadderConfig(min_trades=400),
        registry=HoldoutRegistry.in_memory(),
        **kwargs,
    )


def test_run_cell_passes_the_official_calendar_by_default(captured) -> None:
    outcome = _run(_spec())
    assert outcome.error.startswith("RuntimeError: spy")
    calendar = captured["calendar"]
    assert calendar is not None
    assert len(calendar.all_events()) == len(load_official_calendar().all_events())
    assert captured["allow_empty_calendar"] is False


def test_the_opt_out_is_explicit_and_serialised(captured) -> None:
    spec = _spec(allow_empty_calendar=True)
    assert spec.to_dict()["allow_empty_calendar"] is True
    assert _spec().to_dict()["allow_empty_calendar"] is False
    _run(spec)
    # Lifting the coverage refusal does not switch the calendar off.
    assert captured["calendar"] is not None
    assert captured["allow_empty_calendar"] is True


def test_an_explicitly_empty_calendar_means_no_blackout_source(captured) -> None:
    _run(_spec(allow_empty_calendar=True), calendar=InMemoryEconomicCalendar.empty())
    assert captured["calendar"] is None


def test_an_uncovered_cell_is_refused_not_validated_blind() -> None:
    """The real run_validation: bars the calendar does not span are a refusal."""
    first = BARS.frame.index[0]
    assert first < pd.Timestamp("2024-01-01", tz="UTC"), "fixture must predate coverage"
    outcome = _run(_spec())
    assert outcome.report is None
    assert "CalendarError" in outcome.error
    assert "does not span" in outcome.error


def test_the_runner_loads_the_calendar_once_and_hands_it_to_every_cell(
    monkeypatch,
) -> None:
    seen: list[Any] = []

    def spy(**kwargs: Any):
        seen.append(kwargs["calendar"])
        return campaign_module.CellOutcome(error="spy")

    monkeypatch.setattr(campaign_module, "run_cell", spy)
    with ExperimentLedger.in_memory() as ledger:
        runner = CampaignRunner(
            _spec(),
            bars=bar_source({("XAUUSD", "H4"): BARS}),
            ledger=ledger,
            registry=HoldoutRegistry.in_memory(),
            checkpoint=CampaignCheckpoint.in_memory(),
        )
        runner.run(runner.plan([seed("donchian_breakout_atr")]))
    assert seen, "no cell reached the validator"
    assert all(c is seen[0] for c in seen)
    assert len(seen[0].all_events()) > 0
