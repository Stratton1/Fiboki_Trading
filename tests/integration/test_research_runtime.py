"""The research composition root, end to end, offline.

``docs/v2/ARCHITECTURE.md`` §12 recorded that no process registered the
research job handlers or ran the agent research cycle. These tests pin the
composition that now does:

* composed with ``EchoProvider`` in a temporary state directory, one full
  research cycle runs and leaves an audit chain on disk that verifies;
* every deterministic handler is registered on the orchestrator it was given;
* each run's tool context is pinned to the instant the run started;
* with ``FIBOKI_AGENT_CYCLES`` off there is no schedule entry, and a research
  worker composes nothing at all;
* the nightly slot is claimed before it runs, fires once, and does not fire on
  the first start;
* an incident alert that names a backtest queues a failure investigation;
* the worker's heartbeat keeps beating, and its lease keeps being renewed,
  while a long agent cycle runs.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from fiboki.agents.audit import JsonlAuditLedger
from fiboki.agents.jobs import HANDLERS
from fiboki.agents.orchestrator import Orchestrator
from fiboki.agents.providers import EchoProvider
from fiboki.agents.tools import InMemoryBarSource
from fiboki.agents.workflows import offline_research_script
from fiboki.obs.alerts import AlertDispatcher, AlertEvent
from fiboki.strategy.registry import StrategyRegistry
from fiboki.workers.base import WorkerStore
from fiboki.workers.research_runtime import (
    CycleTarget,
    DailyCycleSchedule,
    ResearchRuntimeSettings,
    RuntimeConfigError,
    compose_research_runtime,
    research_runtime_from_env,
)
from fiboki.workers.research_worker import ResearchWorker, ResearchWorkerConfig
from tests.agents_fixtures import ema_crossover_document, trending_bars

TARGET = CycleTarget("ema_cross_fixture", "EURUSD", "H1", "Does stop width carry it?")
NOW = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)


def _script() -> dict[str, str]:
    return offline_research_script(
        instrument="EURUSD",
        timeframe="H1",
        new_strategy_id="ema_cross_wide_stop",
        train_start="2024-01-01",
        train_end="2024-02-01",
        test_start="2024-02-01",
        test_end="2024-03-03",
    )


def _settings(tmp_path: Path, **overrides) -> ResearchRuntimeSettings:
    payload = {
        "agent_cycles": True,
        "state_dir": tmp_path / "state",
        "strategies_dir": tmp_path / "no-strategies-here",
        "cycle_target": TARGET,
        "cycle_at": time(2, 15, tzinfo=UTC),
        # These tests pin the nightly cycle and incidents. The 15-minute event
        # scan (agentic plan Wave 4) has its own tests in
        # tests/integration/test_event_scan_runtime.py; off here so a tick runs
        # exactly what each test names.
        "event_scan_minutes": 0,
    }
    payload.update(overrides)
    return ResearchRuntimeSettings(**payload)


def _parts():
    strategies = StrategyRegistry()
    strategies.register(ema_crossover_document())
    bars = InMemoryBarSource({("EURUSD", "H1"): trending_bars()})
    return strategies, bars


def _compose(tmp_path: Path, *, clock=lambda: NOW, **overrides):
    strategies, bars = _parts()
    return compose_research_runtime(
        _settings(tmp_path, **overrides),
        provider=EchoProvider(_script()),
        strategies=strategies,
        bars=bars,
        clock=clock,
    )


# --------------------------------------------------------------- the cycle


def test_one_full_research_cycle_runs_and_leaves_a_verifying_audit_chain(tmp_path) -> None:
    runtime = _compose(tmp_path)
    result = runtime.run_research_cycle(workflow_id="wf_runtime_e2e")

    assert result.ok, result.failed_steps
    assert result.backtest_id and result.validation_report_id and result.note_id
    # The worker, not the agent, produced the backtest.
    assert runtime.research.get_backtest(result.backtest_id).created_by == "worker"

    path = tmp_path / "state" / "agents" / "audit.jsonl"
    assert path.exists() and path.stat().st_size > 0
    reloaded = JsonlAuditLedger(path)  # a fresh reader: what is on DISK
    assert reloaded.verify_chain()
    records = reloaded.by_workflow("wf_runtime_e2e")
    assert len(records) >= 10
    assert len(reloaded) == len(runtime.ledger)


def test_every_deterministic_handler_is_registered_on_the_given_orchestrator(
    tmp_path,
) -> None:
    orchestrator = Orchestrator()
    strategies, bars = _parts()
    runtime = compose_research_runtime(
        _settings(tmp_path),
        orchestrator=orchestrator,
        provider=EchoProvider(),
        strategies=strategies,
        bars=bars,
        clock=lambda: NOW,
    )
    assert runtime.orchestrator is orchestrator
    assert set(orchestrator.handled_types()) == set(HANDLERS)
    assert runtime.cycle_orchestrator is not orchestrator
    assert set(runtime.cycle_orchestrator.handled_types()) == set(HANDLERS)


def test_a_cycle_never_drains_the_workers_own_queue(tmp_path) -> None:
    """An operator's queued job must not be run, or adopted, by an agent cycle."""
    from fiboki.agents.orchestrator import JobSpec, JobStatus, JobType

    runtime = _compose(tmp_path)
    operator_job = runtime.orchestrator.submit(
        JobSpec(
            job_type=JobType.DATA_QUALITY_SCAN,
            queue="research",
            payload={"instrument": "EURUSD", "timeframe": "H1"},
            idempotency_key="operator:dq:1",
            submitted_by="joe",
        )
    )
    result = runtime.run_research_cycle()
    assert result.ok, result.failed_steps
    assert runtime.orchestrator.get(operator_job.job_id).status is JobStatus.PENDING
    assert all(r.job_id != operator_job.job_id for r in result.job_records)


def test_each_run_pins_the_agent_clock_to_its_own_start(tmp_path) -> None:
    runtime = _compose(tmp_path)
    seen: list[datetime | None] = []
    original = runtime.deps

    def spy(as_of):
        deps = original(as_of)
        seen.append(deps.context.as_of)
        return deps

    runtime.deps = spy  # type: ignore[method-assign]
    runtime.run_research_cycle(now=NOW)
    assert seen == [NOW]


# ----------------------------------------------------------- the flag


def test_flag_off_means_no_schedule_entry(tmp_path) -> None:
    runtime = _compose(tmp_path, agent_cycles=False)
    assert runtime.schedule == ()
    assert not runtime.has_work(NOW + timedelta(days=3))
    assert not (tmp_path / "state" / "agents" / "schedule.json").exists()


def test_flag_off_in_the_environment_composes_nothing() -> None:
    assert research_runtime_from_env(Orchestrator(), environ={}) is None
    assert research_runtime_from_env(
        Orchestrator(), environ={"FIBOKI_AGENT_CYCLES": "false"}
    ) is None


def test_a_default_research_worker_is_unchanged(tmp_path) -> None:
    with WorkerStore.sqlite_at(tmp_path / "w.sqlite") as store:
        orchestrator = Orchestrator()
        worker = ResearchWorker(
            orchestrator, store, ResearchWorkerConfig(max_cycles=1, idle_sleep_seconds=0),
            environ={},
        )
        assert worker.run(install_signals=False) == 0
    assert worker.agent_runtime is None
    assert orchestrator.handled_types() == ()


def test_the_flag_is_strict_and_misconfiguration_refuses_to_start(tmp_path) -> None:
    with pytest.raises(RuntimeConfigError, match="not a boolean"):
        ResearchRuntimeSettings.from_env({"FIBOKI_AGENT_CYCLES": "ture"})
    with pytest.raises(RuntimeConfigError, match="CYCLE_TARGET is unset"):
        _compose(tmp_path, cycle_target=None)
    with pytest.raises(RuntimeConfigError, match="not in the strategy registry"):
        _compose(tmp_path, cycle_target=CycleTarget("nope", "EURUSD", "H1"))
    with pytest.raises(RuntimeConfigError, match="LOCAL_MODEL"):
        compose_research_runtime(
            _settings(tmp_path, provider="local"), strategies=_parts()[0], bars=_parts()[1]
        )
    # The worker reads the flag: on, with no bar source, it refuses to start.
    with WorkerStore.sqlite_at(tmp_path / "w.sqlite") as store:
        worker = ResearchWorker(
            Orchestrator(),
            store,
            ResearchWorkerConfig(max_cycles=1),
            environ={
                "FIBOKI_AGENT_CYCLES": "on",
                "FIBOKI_STATE_DIR": str(tmp_path / "state"),
                "FIBOKI_AGENT_CYCLE_TARGET": "donchian_breakout_atr:XAUUSD:H4",
                "FIBOKI_STRATEGIES_DIR": str(
                    Path(__file__).resolve().parents[2] / "research" / "strategies"
                ),
            },
        )
        with pytest.raises(RuntimeConfigError, match="FIBOKI_DATA_ROOT"):
            worker.run(install_signals=False)


def test_settings_parse_the_declared_variables() -> None:
    settings = ResearchRuntimeSettings.from_env(
        {
            "FIBOKI_AGENT_CYCLES": "yes",
            "FIBOKI_AGENT_PROVIDER": "LOCAL",
            "FIBOKI_AGENT_LOCAL_MODEL": "qwen2.5:7b-instruct",
            "FIBOKI_AGENT_CYCLE_UTC": "03:40",
            "FIBOKI_AGENT_CYCLE_TARGET": "donchian_breakout_atr:xauusd:h4",
            "FIBOKI_STATE_DIR": "/srv/fiboki/state",
        }
    )
    assert settings.agent_cycles and settings.provider == "local"
    assert settings.cycle_at == time(3, 40, tzinfo=UTC)
    assert settings.cycle_target == CycleTarget("donchian_breakout_atr", "XAUUSD", "H4")
    assert settings.audit_path == Path("/srv/fiboki/state/agents/audit.jsonl")


# --------------------------------------------------------- the schedule


def test_the_nightly_slot_fires_once_and_not_on_first_start(tmp_path) -> None:
    entry = DailyCycleSchedule("n", time(2, 15, tzinfo=UTC), tmp_path / "s.json")
    first_start = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    entry.initialise(first_start)
    assert entry.due(first_start) is None, "enabling the flag must not fire at once"

    next_night = datetime(2026, 9, 29, 2, 15, tzinfo=UTC)
    assert entry.due(next_night - timedelta(minutes=1)) is None
    slot = entry.due(next_night)
    assert slot == next_night
    entry.claim(slot)
    assert entry.due(next_night + timedelta(hours=5)) is None

    # Down for three nights: the missed slot runs ONCE, not three times.
    back = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)
    assert entry.due(back) == datetime(2026, 10, 2, 2, 15, tzinfo=UTC)


def test_tick_claims_and_runs_the_due_cycle(tmp_path) -> None:
    clock = {"now": NOW}
    runtime = _compose(tmp_path, clock=lambda: clock["now"])
    assert not runtime.has_work(), "first start initialises, it does not fire"

    clock["now"] = NOW + timedelta(days=1)
    assert runtime.has_work()
    results = runtime.tick()
    assert len(results) == 1 and results[0].ok, results[0].failed_steps
    assert results[0].workflow_id == "wf_nightly_research_cycle_20260929T0215Z"
    assert not runtime.has_work()
    assert runtime.schedule[0].last_claimed() == datetime(2026, 9, 29, 2, 15, tzinfo=UTC)


# ---------------------------------------------------------- incidents


def test_an_incident_alert_naming_a_backtest_queues_an_investigation(tmp_path) -> None:
    runtime = _compose(tmp_path, agent_cycles=False)
    cycle = runtime.run_research_cycle()
    assert cycle.backtest_id

    dispatcher = AlertDispatcher([runtime.incidents])
    dispatcher.fire(AlertEvent.STRATEGY_DEGRADED, "no backtest named", source="lifecycle")
    dispatcher.fire(AlertEvent.KILL_SWITCH_ACTIVATED, "not an incident type", source="x")
    dispatcher.fire(
        AlertEvent.STRATEGY_HALTED,
        "stopping rule fired",
        source="lifecycle",
        dedupe_key="halt-1",
        backtest_id=cycle.backtest_id,
    )
    assert len(runtime.incidents.inbox) == 1
    assert len(runtime.incidents.inbox.skipped) == 1
    # A second request for the same backtest on the same day is not re-queued.
    assert runtime.raise_incident(cycle.backtest_id, source="manual") is False

    results = runtime.tick()
    assert len(results) == 1
    investigation = results[0]
    assert investigation.backtest_id == cycle.backtest_id
    assert [s.step for s in investigation.steps] == ["drawdown", "worst_trades", "diagnosis"]
    assert investigation.ok, investigation.failed_steps
    assert JsonlAuditLedger(runtime.settings.audit_path).verify_chain()


# ---------------------------------------------------- worker liveness


def test_the_research_worker_runs_the_due_cycle_in_its_own_loop(tmp_path) -> None:
    clock = {"now": NOW}
    runtime = _compose(tmp_path, clock=lambda: clock["now"])
    clock["now"] = NOW + timedelta(days=1)
    with WorkerStore.sqlite_at(tmp_path / "w.sqlite") as store:
        worker = ResearchWorker(
            runtime.orchestrator,
            store,
            ResearchWorkerConfig(max_cycles=1, idle_sleep_seconds=0, pulse_seconds=0.05),
            agent_runtime=runtime,
        )
        assert worker.run(install_signals=False) == 0
        row = next(r for r in store.heartbeat_rows() if r["worker_id"] == worker.worker_id)
    assert len(worker.agent_results) == 1 and worker.agent_results[0].ok
    assert worker.heartbeat.jobs_done == 1
    assert row["cycles_ok"] == 1


class _SlowRuntime:
    """Stands in for a runtime whose cycle takes longer than the lease TTL."""

    schedule = ()

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.started = threading.Event()
        self.ran = 0

    def has_work(self, now=None) -> bool:
        return self.ran == 0

    def tick(self, now=None, *, should_stop=lambda: False):
        self.started.set()
        _time.sleep(self.seconds)
        self.ran += 1
        return []


def test_the_heartbeat_beats_and_the_lease_holds_during_a_long_cycle(tmp_path) -> None:
    runtime = _SlowRuntime(seconds=1.5)
    with WorkerStore.sqlite_at(tmp_path / "w.sqlite") as store:
        worker = ResearchWorker(
            Orchestrator(),
            store,
            ResearchWorkerConfig(
                max_cycles=1,
                idle_sleep_seconds=0,
                lease_ttl_seconds=1.0,  # SHORTER than the cycle
                pulse_seconds=0.1,
            ),
            agent_runtime=runtime,
        )
        seen: list[tuple[datetime, str]] = []
        thread = threading.Thread(target=lambda: worker.run(install_signals=False))
        thread.start()
        assert runtime.started.wait(5.0)
        deadline = _time.monotonic() + 1.2
        while _time.monotonic() < deadline:
            row = next(r for r in store.heartbeat_rows() if r["worker_id"] == worker.worker_id)
            seen.append((row["beat_at"], row["detail"]))
            _time.sleep(0.1)
        thread.join(10.0)
        assert not thread.is_alive()

    in_cycle = [beat for beat, detail in seen if detail == "agent research work running"]
    assert len(set(in_cycle)) >= 3, f"the heartbeat did not advance mid-cycle: {seen}"
    assert worker.last_pulse is not None and worker.last_pulse.beats >= 5
    assert worker.last_pulse.lease_lost == ""
    assert worker.exit_code == 0
