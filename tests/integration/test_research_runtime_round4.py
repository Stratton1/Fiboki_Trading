"""Research runtime, round 4: the experiment ledger reaches the job handlers,
and thesis debates are scheduled on H4/D1 closes when agent cycles are on.

EchoProvider plays every model; everything is offline and in a temporary
state directory.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from fiboki.agents.orchestrator import JobType
from fiboki.agents.providers import EchoProvider
from fiboki.agents.tools import InMemoryBarSource, ThesisStore
from fiboki.agents.workflows import offline_thesis_script
from fiboki.core.enums import Timeframe
from fiboki.strategy.registry import StrategyRegistry
from fiboki.workers.research_runtime import (
    CycleTarget,
    ResearchRuntimeSettings,
    RuntimeConfigError,
    ThesisDebateSchedule,
    compose_research_runtime,
)
from tests.agents_fixtures import ema_crossover_document, trending_bars

START = datetime(2024, 8, 1, 0, 5, tzinfo=UTC)


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
        "cycle_target": CycleTarget("ema_cross_fixture", "EURUSD", "H4"),
        # Out of the way of the H4 closes these tests step through.
        "cycle_at": time(23, 0, tzinfo=UTC),
        "event_scan_minutes": 0,
        "thesis_debate_instruments": ("EURUSD",),
    }
    payload.update(overrides)
    return ResearchRuntimeSettings(**payload)


def _compose(tmp_path: Path, clock: Clock, **overrides):
    strategies = StrategyRegistry()
    strategies.register(ema_crossover_document())
    bars = InMemoryBarSource(
        {("EURUSD", "H4"): trending_bars(timeframe=Timeframe.H4, periods=1600)}
    )
    return compose_research_runtime(
        _settings(tmp_path, **overrides),
        provider=EchoProvider(offline_thesis_script()),
        strategies=strategies,
        bars=bars,
        clock=clock,
    )


def test_the_experiment_ledger_is_injected_into_every_handler(tmp_path: Path) -> None:
    runtime = _compose(tmp_path, Clock(START), thesis_debate_instruments=())
    for orchestrator in (runtime.orchestrator, runtime.cycle_orchestrator):
        registration = orchestrator._handlers[JobType.VALIDATION]
        assert registration.services["experiments"] is runtime.research.ledger
        assert registration.services["experiments"] is not None


def test_thesis_debates_run_once_per_h4_close(tmp_path: Path) -> None:
    clock = Clock(START)
    runtime = _compose(tmp_path, clock)
    (entry,) = runtime.debate_schedule
    assert entry.name == "thesis_debate_EURUSD_H4"
    assert isinstance(runtime.thesis, ThesisStore)
    assert runtime.thesis.path == tmp_path / "state" / "agents" / "thesis.sqlite"
    assert not runtime.has_work(), "first start records the close, it does not debate"

    clock.now = START + timedelta(hours=3, minutes=50)  # 03:55: no new close
    assert not runtime.has_work()

    clock.now = START + timedelta(hours=4)  # 04:05: the 04:00 close
    assert runtime.has_work()
    (result,) = runtime.tick()
    assert result.workflow_id == "wf_thesis_debate_EURUSD_H4_20240801T0400Z"
    assert result.ok, [(s.step, s.error) for s in result.steps]
    assert result.conviction_id
    assert len(runtime.thesis.convictions("EURUSD")) == 1
    assert entry.last_claimed() == datetime(2024, 8, 1, 4, 0, tzinfo=UTC)
    assert not runtime.has_work(), "claimed: the same close is not debated twice"

    # Down for a day: the missed closes produce ONE debate, not six.
    clock.now = START + timedelta(days=1, hours=4)
    debates = [r for r in runtime.tick() if r.workflow_id.startswith("wf_thesis_debate")]
    assert len(debates) == 1
    assert entry.last_claimed() == datetime(2024, 8, 2, 4, 0, tzinfo=UTC)


def test_no_instruments_or_cycles_off_schedules_no_debate(tmp_path: Path) -> None:
    assert _compose(tmp_path / "a", Clock(START), thesis_debate_instruments=()).debate_schedule == ()
    off = _compose(tmp_path / "b", Clock(START), agent_cycles=False)
    assert off.debate_schedule == () and off.thesis is None
    with pytest.raises(RuntimeConfigError, match="not composed"):
        off.run_thesis_debate("EURUSD")


def test_the_debate_variables_are_parsed_and_validated() -> None:
    settings = ResearchRuntimeSettings.from_env({
        "FIBOKI_THESIS_DEBATE_INSTRUMENTS": " xauusd, EURUSD ,,",
        "FIBOKI_THESIS_DEBATE_TIMEFRAME": "d1",
    })
    assert settings.thesis_debate_instruments == ("EURUSD", "XAUUSD")
    assert settings.thesis_debate_timeframe == "D1"
    assert ResearchRuntimeSettings.from_env({}).thesis_debate_instruments == ()
    with pytest.raises(RuntimeConfigError, match="THESIS_DEBATE_TIMEFRAME"):
        ResearchRuntimeSettings.from_env({"FIBOKI_THESIS_DEBATE_TIMEFRAME": "H1"})


def test_a_d1_schedule_is_due_on_the_daily_close(tmp_path: Path) -> None:
    entry = ThesisDebateSchedule("EURUSD", "D1", tmp_path / "s.json")
    entry.initialise(START)
    assert entry.due(START + timedelta(hours=20)) is None
    assert entry.due(START + timedelta(days=1)) == datetime(2024, 8, 2, 0, 0, tzinfo=UTC)
