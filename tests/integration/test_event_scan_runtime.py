"""The event scan on the research runtime's schedule (FIBOKI_EVENT_SCAN_MINUTES).

Composed with EchoProvider in a temporary state directory:

* the scan slot is claimed before it runs, does not fire on the first start,
  and a missed run of many slots runs once;
* each scan reads the headlines since the previous LOGGED scan and writes one
  ``scan_log`` heartbeat row, even when it found nothing;
* the annotations land in ``<state_dir>/events/annotations.sqlite`` and the
  audit chain on disk verifies;
* the heartbeat is what makes the event veto source "available".
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from fiboki.agents.audit import JsonlAuditLedger
from fiboki.agents.providers import EchoProvider
from fiboki.agents.tools import InMemoryBarSource
from fiboki.agents.workflows import WORKFLOW_START, event_classification_step
from fiboki.marketstate.events import EventVetoSource
from fiboki.strategy.registry import StrategyRegistry
from fiboki.workers.research_runtime import (
    CycleTarget,
    IntervalSchedule,
    ResearchRuntimeSettings,
    RuntimeConfigError,
    compose_research_runtime,
)
from tests.agents_fixtures import ema_crossover_document, trending_bars
from tests.event_fixtures import headline_store

START = datetime(2026, 9, 29, 12, 1, tzinfo=UTC)  # slot 12:00 is claimed at compose


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _settings(tmp_path: Path, **overrides) -> ResearchRuntimeSettings:
    payload = {
        "agent_cycles": True,
        "state_dir": tmp_path / "state",
        "strategies_dir": tmp_path / "none",
        "cycle_target": CycleTarget("ema_cross_fixture", "EURUSD", "H1"),
        "cycle_at": time(2, 15, tzinfo=UTC),
        "event_scan_minutes": 15,
    }
    payload.update(overrides)
    return ResearchRuntimeSettings(**payload)


def _answer() -> str:
    return json.dumps(
        {
            "annotations": [
                {"source_ids": ["h1"], "event_type": "central_bank", "currencies": ["USD"],
                 "severity": 3, "scheduled": False, "confidence": 0.9,
                 "rationale": "unscheduled statement"},
            ]
        }
    )


def _compose(tmp_path: Path, clock: Clock, **overrides):
    strategies = StrategyRegistry()
    strategies.register(ema_crossover_document())
    return compose_research_runtime(
        _settings(tmp_path, **overrides),
        provider=EchoProvider({event_classification_step(0): _answer()}),
        strategies=strategies,
        bars=InMemoryBarSource({("EURUSD", "H1"): trending_bars()}),
        clock=clock,
    )


def test_the_scan_is_scheduled_claimed_logged_and_feeds_the_veto(tmp_path) -> None:
    clock = Clock(START)
    runtime = _compose(tmp_path, clock)
    settings = runtime.settings
    assert [s.name for s in runtime.event_schedule] == ["event_scan"]
    assert runtime.tick() == [], "the first start records the slot, it does not fire"

    headline_store(
        settings.news_path,
        [("Emergency Fed statement", START + timedelta(minutes=5))],
    ).close()
    clock.now = START + timedelta(minutes=14)  # 12:15
    assert runtime.has_work()
    (result,) = runtime.tick()
    assert result.ok, [(s.step, s.error) for s in result.steps]
    assert result.workflow_id == "wf_event_scan_20260929T1215Z"
    assert runtime.tick() == [], "claimed: the same slot never runs twice"

    scans = runtime.events.scans()
    assert len(scans) == 1 and scans[0]["outcome"] == "ok"
    assert (scans[0]["n_headlines"], scans[0]["n_annotations"]) == (1, 1)
    (ann,) = runtime.events.annotations()
    assert ann.source_ids == ("h1",)
    assert settings.events_path == tmp_path / "state" / "events" / "annotations.sqlite"
    assert settings.events_path.is_file()

    reloaded = JsonlAuditLedger(settings.audit_path)
    assert reloaded.verify_chain()
    assert reloaded.by_workflow(result.workflow_id)

    at = clock.now + timedelta(minutes=1)
    verdict = EventVetoSource(settings.events_path).assess("USDJPY", at)
    assert verdict.available and verdict.veto is not None


def test_the_next_window_starts_after_the_last_logged_scan(tmp_path) -> None:
    clock = Clock(START)
    runtime = _compose(tmp_path, clock)
    clock.now = START + timedelta(minutes=14)
    (first,) = runtime.tick()
    clock.now = START + timedelta(minutes=29)
    (second,) = runtime.tick()

    def since(result) -> str:
        start = next(
            r for r in runtime.ledger.by_workflow(result.workflow_id) if r.tool == WORKFLOW_START
        )
        return start.inputs["params"]["headlines_since"]

    assert since(first) == (START + timedelta(minutes=14) - timedelta(minutes=15)).isoformat()
    assert since(second) == (START + timedelta(minutes=14, microseconds=1)).isoformat()
    # Nothing was recorded, and the heartbeat still says so.
    assert [s["outcome"] for s in runtime.events.scans()] == ["ok", "ok"]
    assert all(s["n_headlines"] == 0 for s in runtime.events.scans())


def test_a_long_outage_runs_once_and_clamps_the_window_to_seven_days(tmp_path) -> None:
    clock = Clock(START)
    runtime = _compose(tmp_path, clock)
    clock.now = START + timedelta(minutes=14)
    runtime.tick()
    clock.now = START + timedelta(days=9)
    ran = runtime.tick()  # the nightly cycle is also due; count the scans only
    assert [r.workflow_id for r in ran if r.workflow_id.startswith("wf_event_scan")] == [
        "wf_event_scan_20261008T1200Z"
    ]
    last = runtime.events.scans()[-1]
    assert "window_clamped" in last["detail"]


def test_a_failed_classification_is_logged_as_error_and_is_not_a_heartbeat(tmp_path) -> None:
    clock = Clock(START)
    strategies = StrategyRegistry()
    strategies.register(ema_crossover_document())
    runtime = compose_research_runtime(
        _settings(tmp_path),
        provider=EchoProvider({}),  # no script: the model output does not parse
        strategies=strategies,
        bars=InMemoryBarSource({("EURUSD", "H1"): trending_bars()}),
        clock=clock,
    )
    headline_store(runtime.settings.news_path, [("x", START + timedelta(minutes=5))]).close()
    clock.now = START + timedelta(minutes=14)
    (result,) = runtime.tick()
    assert not result.ok
    (scan,) = runtime.events.scans()
    assert scan["outcome"] == "error" and "failed batches" in scan["detail"]
    verdict = EventVetoSource(runtime.settings.events_path).assess("EURUSD", clock.now)
    assert not verdict.available and verdict.detail == "no_successful_scan_on_file"


def test_zero_minutes_turns_the_scan_off(tmp_path) -> None:
    runtime = _compose(tmp_path, Clock(START), event_scan_minutes=0)
    assert runtime.event_schedule == () and runtime.news is None and runtime.events is None
    with pytest.raises(RuntimeConfigError, match="not composed"):
        runtime.run_event_scan()


def test_the_env_variable_is_parsed_strictly() -> None:
    base = {"FIBOKI_AGENT_CYCLES": "true"}
    assert ResearchRuntimeSettings.from_env(base).event_scan_minutes == 15
    assert ResearchRuntimeSettings.from_env(
        {**base, "FIBOKI_EVENT_SCAN_MINUTES": "0"}
    ).event_scan_minutes == 0
    for bad in ("abc", "-1", "1441", "1.5"):
        with pytest.raises(RuntimeConfigError):
            ResearchRuntimeSettings.from_env({**base, "FIBOKI_EVENT_SCAN_MINUTES": bad})


def test_interval_slots_are_epoch_aligned(tmp_path) -> None:
    s = IntervalSchedule("x", timedelta(minutes=15), tmp_path / "s.json")
    assert s.slot_for(datetime(2026, 9, 29, 12, 14, 59, tzinfo=UTC)) == datetime(
        2026, 9, 29, 12, 0, tzinfo=UTC
    )
    s.initialise(datetime(2026, 9, 29, 12, 1, tzinfo=UTC))
    assert s.due(datetime(2026, 9, 29, 12, 14, tzinfo=UTC)) is None
    assert s.due(datetime(2026, 9, 29, 15, 0, tzinfo=UTC)) == datetime(
        2026, 9, 29, 15, 0, tzinfo=UTC
    )
