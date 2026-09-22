"""The lifecycle timer, fired by the worker that owns it.

``lifecycle/service.py`` was implemented and tested and **nothing called it**.
Its own docstring said "the worker owns the timer"; no worker did, so the
monitors never ran against forward data, no pre-registered stopping rule was
ever evaluated in a live process and no strategy was ever demoted automatically.

These tests drive the real :class:`~fiboki.workers.live_worker.LiveWorker` loop
and assert that the timer fires on its schedule, that a constructed divergence
demotes the strategy, that the demotion reaches the worker's own record, and
that the two NEW alert events dispatch to a channel rather than being folded
into ``STRATEGY_DEGRADED`` with only a severity to tell them apart.
"""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from fiboki.broker.paper import PaperConfig
from fiboki.core.enums import StrategyLifecycle
from fiboki.core.money import IdentityFxSource
from fiboki.lifecycle.monitor import ForwardMonitor, MonitorConfig
from fiboki.lifecycle.service import LifecycleService
from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel, Severity
from fiboki.portfolio.sizing import SizingPolicy
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import LiveWorkerConfig
from fiboki.workers.runtime import LifecycleTimer, build_replay_session
from tests.exec_fixtures import synthetic_frame
from tests.integration.test_live_worker_runtime import BreakoutSource
from tests.lifecycle_fixtures import (
    HASH_A,
    T0,
    expectation,
    machine_at,
    observation,
    rule_parameters,
)


@pytest.fixture()
def store(tmp_path) -> WorkerStore:
    with WorkerStore.sqlite_at(tmp_path / "workers.sqlite") as handle:
        yield handle


def _service(channel: MemoryChannel) -> LifecycleService:
    service = LifecycleService(
        machine=machine_at(StrategyLifecycle.PAPER),
        dispatcher=AlertDispatcher([channel], min_severity=Severity.INFO),
        monitor=ForwardMonitor(MonitorConfig()),
        clock=lambda: T0,
    )
    service.pre_register_rules(
        HASH_A,
        parameters=rule_parameters(backtest_sharpe=0.40, drawdown_threshold=0.25),
        registered_by="joe",
        reason="pre-registered before the first paper trade",
        at=T0,
    )
    return service


def _collapsed_inputs():
    """A strategy whose forward returns are nothing like its backtest.

    Constructed, not sampled until it fails: a mean of -0.004 against a declared
    backtest Sharpe of 0.40 is a collapse by any reading, and the seed is fixed
    so the demotion is the same demotion every run.
    """
    returns = np.random.default_rng(9).normal(-0.004, 0.010, 250)
    return lambda: {HASH_A: (expectation(), observation(returns=returns))}


def _session(store: WorkerStore, service, inputs, *, every: int, cycles: int):
    frame = synthetic_frame(n=200, seed=5)
    return build_replay_session(
        frames={"EURUSD": frame},
        signal_source=BreakoutSource(),
        store=store,
        paper_config=PaperConfig(
            initial_balance=50_000.0, account_ccy="USD", strategy_id="ichimoku_baseline"
        ),
        sizing_policy=SizingPolicy(risk_fraction=0.005),
        fx=IdentityFxSource(),
        timeframe="H1",
        warmup=30,
        lifecycle_service=service,
        lifecycle_inputs=inputs,
        worker_config=LiveWorkerConfig(
            max_cycles=cycles,
            idle_sleep_seconds=0.0,
            busy_sleep_seconds=0.0,
            reconcile_every_cycles=1000,
            lifecycle_every_cycles=every,
            lease_name=f"lifecycle-test-{every}-{cycles}",
        ),
    )


# --------------------------------------------------------------------------


def test_the_timer_fires_on_its_schedule(store) -> None:
    channel = MemoryChannel()
    service = _service(channel)
    session = _session(store, service, lambda: {}, every=5, cycles=20)
    session.worker.run(install_signals=False)

    assert session.lifecycle is not None
    # Cycles 5, 10, 15 and 20.
    assert session.lifecycle.ticks == 4
    assert session.worker.summary()["lifecycle_ticks"] == 4


def test_nothing_calls_evaluate_when_no_timer_is_wired(store) -> None:
    """The defect, stated as a test: no timer, no monitoring. Not 'fine'."""
    frame = synthetic_frame(n=120, seed=5)
    session = build_replay_session(
        frames={"EURUSD": frame},
        signal_source=BreakoutSource(),
        store=store,
        paper_config=PaperConfig(initial_balance=50_000.0, account_ccy="USD"),
        sizing_policy=SizingPolicy(risk_fraction=0.005),
        fx=IdentityFxSource(),
        timeframe="H1",
        warmup=30,
        worker_config=LiveWorkerConfig(
            max_cycles=20,
            idle_sleep_seconds=0.0,
            busy_sleep_seconds=0.0,
            reconcile_every_cycles=1000,
            lease_name="lifecycle-test-none",
        ),
    )
    session.worker.run(install_signals=False)
    assert session.lifecycle is None
    assert session.worker.summary()["lifecycle_ticks"] == 0


def test_the_timer_demotes_on_a_constructed_divergence(store) -> None:
    channel = MemoryChannel()
    service = _service(channel)
    session = _session(store, service, _collapsed_inputs(), every=1, cycles=3)
    session.worker.run(install_signals=False)

    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.QUARANTINED
    assert session.worker.demotions, "the worker recorded no demotion"
    status = service.status(HASH_A)
    assert status.degraded is True
    assert status.latched_halts, "no stopping rule latched"
    assert status.health < 1.0


def test_the_new_alert_events_dispatch(store) -> None:
    channel = MemoryChannel()
    service = _service(channel)
    session = _session(store, service, _collapsed_inputs(), every=1, cycles=2)
    session.worker.run(install_signals=False)

    events = {alert.event for alert in channel.sent}
    assert AlertEvent.STRATEGY_HALTED in events, (
        "a pre-registered stopping rule fired and no STRATEGY_HALTED was raised"
    )
    assert AlertEvent.STRATEGY_QUARANTINED in events, (
        "a strategy was quarantined and no STRATEGY_QUARANTINED was raised"
    )
    halted = next(a for a in channel.sent if a.event is AlertEvent.STRATEGY_HALTED)
    assert halted.severity is Severity.CRITICAL
    assert halted.context["requires_operator_release"] is True
    quarantined = next(
        a for a in channel.sent if a.event is AlertEvent.STRATEGY_QUARANTINED
    )
    assert quarantined.context["to_state"] == StrategyLifecycle.QUARANTINED.value
    assert quarantined.context["automatic"] is True


def test_a_demoted_strategy_is_what_the_gateway_then_reads(store) -> None:
    """The join that closes the loop: demotion must reach the risk decision.

    ``lifecycle/`` refuses to import ``risk/``, so the worker's context builder
    performs the join. If it did not, a quarantined strategy would keep trading
    and the monitors would be decoration.
    """
    channel = MemoryChannel()
    service = _service(channel)
    session = _session(store, service, _collapsed_inputs(), every=1, cycles=2)
    session.worker.run(install_signals=False)

    view = session.context_builder.strategy_view("ichimoku_baseline")
    assert view.lifecycle is StrategyLifecycle.QUARANTINED
    assert view.degraded is True

    from fiboki.risk.gateway import RiskGateway
    from fiboki.risk.limits import PAPER_LIMITS
    from tests.integration.test_live_worker_runtime import _any_plan

    plan = _any_plan(session, symbol="EURUSD", strategy_id="ichimoku_baseline")
    context = session.context_builder(plan)
    decision = RiskGateway(limits=PAPER_LIMITS).evaluate(context)
    assert not decision.allowed
    assert any("strategy_lifecycle_blocks:quarantined" in r for r in decision.reasons)


def test_a_failing_monitor_alerts_rather_than_passing_silently(store) -> None:
    """A monitor that stopped running looks exactly like a healthy fleet."""
    channel = MemoryChannel()
    service = _service(channel)
    session = _session(store, service, lambda: {}, every=1, cycles=2)

    class _Broken:
        ticks = 0

        def tick(self):
            raise RuntimeError("the monitor store is unreachable")

    session.worker.lifecycle = _Broken()
    session.worker.dispatcher = AlertDispatcher([channel], min_severity=Severity.INFO)
    session.worker.run(install_signals=False)

    messages = [a.message for a in channel.sent]
    assert any("lifecycle monitors could not run" in m for m in messages)
    assert session.worker.summary()["lifecycle_ticks"] == 0


def test_the_timer_evaluates_a_running_strategy_with_no_observations(store) -> None:
    """Silence is not agreement, and the evaluation says so in a note."""
    channel = MemoryChannel()
    service = _service(channel)
    timer = LifecycleTimer(service, lambda: {}, clock=lambda: datetime.now(tz=UTC))
    results = timer.tick()
    assert results, "a RUNNING strategy with no inputs was skipped entirely"
    assert any("was not evaluated" in n or "not the same as agreeing" in n
               for n in results[0].notes)
    assert pd.Timestamp(results[0].at).tzinfo is not None
