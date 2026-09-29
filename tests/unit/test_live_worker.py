"""The live worker: closed candles, one route to the venue, no Order anywhere.

``tests/unit/test_no_gateway_bypass.py`` proves structurally that only
``ExecutionService.submit`` constructs an ``Order``. These tests prove the
behaviour that structure is protecting: the worker evaluates only closed bars,
reconciles before trading after a restart, refuses to start in an unauthorised
mode, and never swallows a submit error.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import (
    BarBatch,
    LiveWorker,
    LiveWorkerConfig,
    StartupReconciliationError,
)

LIVE_WORKER_SOURCE = Path(__file__).resolve().parents[2] / "src/fiboki/workers/live_worker.py"


# ------------------------------------------------------------------- doubles


class Plan:
    def __init__(self, plan_id, instrument):
        self.plan_id = plan_id
        self.instrument = instrument


class Outcome:
    def __init__(self, accepted, reason="", blocked_by_risk=False):
        self.accepted = accepted
        self.reason = reason
        self.blocked_by_risk = blocked_by_risk


class Adapter:
    name = "fake"


class Recon:
    def __init__(self, unknown=(), orphans=(), mismatches=()):
        self.still_unknown = tuple(unknown)
        self.orphan_broker_refs = tuple(orphans)
        self.size_mismatches = tuple(mismatches)
        self.errors = ()

    @property
    def clean(self):
        return not (self.still_unknown or self.orphan_broker_refs or self.size_mismatches)

    def summary(self):
        return "recon"


class Mode:
    def __init__(self, value):
        self.value = value


class FakeExecution:
    """Stands in for ExecutionService. Records calls; constructs no order."""

    def __init__(self, outcomes=None, mode="paper", recon=None, raises=None):
        self.adapter = Adapter()
        self.mode = Mode(mode)
        self.submitted = []
        self._outcomes = outcomes or {}
        self._recon = recon
        self._raises = raises
        self.reconcile_calls = 0

    def submit(self, plan, context):
        self.submitted.append((plan, context))
        if self._raises is not None:
            raise self._raises
        return self._outcomes.get(plan.instrument, Outcome(True))

    def reconcile(self):
        self.reconcile_calls += 1
        if isinstance(self._recon, Exception):
            raise self._recon
        return self._recon or Recon()


class Feed:
    def __init__(self, batches):
        self.batches = list(batches)
        self.polls = 0

    def poll(self):
        batch = self.batches[min(self.polls, len(self.batches) - 1)]
        self.polls += 1
        return batch


class Evaluator:
    def __init__(self, plans_by_instrument=None):
        self.seen = []
        self.plans = plans_by_instrument or {}

    def evaluate(self, instruments, batch):
        self.seen.append(list(instruments))
        out = []
        for symbol in instruments:
            out.extend(self.plans.get(symbol, []))
        return out


class State:
    def __init__(self):
        self.updates = 0

    def update(self, batch):
        self.updates += 1


@pytest.fixture
def store(tmp_path):
    s = WorkerStore.sqlite_at(tmp_path / "state.db")
    yield s
    s.close()


def build(store, *, execution=None, feed=None, evaluator=None, config=None, **kwargs):
    return LiveWorker(
        execution_service=execution or FakeExecution(),
        feed=feed or Feed([BarBatch()]),
        evaluator=evaluator or Evaluator(),
        context_builder=lambda plan: {"plan": plan},
        store=store,
        config=config or LiveWorkerConfig(max_cycles=1, idle_sleep_seconds=0.0),
        worker="live@h:1",
        **kwargs,
    )


# ------------------------------------------------- the structural guarantee


def test_the_live_worker_constructs_no_order():
    """Belt and braces alongside the repo-wide AST test.

    The rule is not stylistic. V1's risk engine had zero call sites: the checks
    existed, passed their unit tests, and were never invoked by the code that
    placed orders.
    """
    tree = ast.parse(LIVE_WORKER_SOURCE.read_text(encoding="utf-8"))
    constructed = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "Order" not in constructed


def test_submit_is_reached_through_exactly_one_call_site():
    source = LIVE_WORKER_SOURCE.read_text(encoding="utf-8")
    assert source.count("self.execution.submit(") == 1


# -------------------------------------------------------------- closed bars


def test_only_CLOSED_instruments_are_evaluated():
    """A signal on a forming candle uses data that has not finished happening,
    and no backtest can reproduce it."""
    evaluator = Evaluator()
    batch = BarBatch(
        frames={"EURUSD": object(), "GBPUSD": object()},
        closed_instruments=frozenset({"EURUSD"}),
        ages={"EURUSD": 30.0, "GBPUSD": 5.0},
        timeframe="H1",
    )
    store = WorkerStore.from_url("sqlite://")
    worker = build(store, feed=Feed([batch]), evaluator=evaluator)
    worker.run_cycle()
    assert evaluator.seen == [["EURUSD"]]


def test_a_cycle_with_no_closed_bars_is_idle():
    store = WorkerStore.from_url("sqlite://")
    worker = build(store, feed=Feed([BarBatch(frames={"EURUSD": object()})]))
    result = worker.run_cycle()
    assert result.jobs == 0
    assert "no closed bars" in result.detail


def test_market_state_is_updated_even_when_nothing_is_evaluated():
    state = State()
    store = WorkerStore.from_url("sqlite://")
    worker = LiveWorker(
        execution_service=FakeExecution(),
        feed=Feed([BarBatch(frames={"EURUSD": object()})]),
        evaluator=Evaluator(),
        context_builder=lambda p: {},
        store=store,
        market_state=state,
        config=LiveWorkerConfig(max_cycles=1),
        worker="live@h:1",
    )
    worker.run_cycle()
    assert state.updates == 1


# ------------------------------------------------------------------ submits


def test_a_plan_goes_to_the_execution_service_unchanged(store):
    plan = Plan("p1", "EURUSD")
    execution = FakeExecution()
    batch = BarBatch(closed_instruments=frozenset({"EURUSD"}))
    worker = build(
        store,
        execution=execution,
        feed=Feed([batch]),
        evaluator=Evaluator({"EURUSD": [plan]}),
    )
    result = worker.run_cycle()
    assert result.jobs == 1
    assert execution.submitted[0][0] is plan  # NOT re-sized, NOT rebuilt


def test_a_risk_block_is_recorded_and_alerted(store):
    channel = MemoryChannel()
    execution = FakeExecution(
        outcomes={"EURUSD": Outcome(False, "max_account_risk", blocked_by_risk=True)}
    )
    worker = build(
        store,
        execution=execution,
        feed=Feed([BarBatch(closed_instruments=frozenset({"EURUSD"}))]),
        evaluator=Evaluator({"EURUSD": [Plan("p1", "EURUSD")]}),
        dispatcher=AlertDispatcher([channel]),
    )
    worker.run_cycle()
    assert AlertEvent.RISK_LIMIT_BREACH in channel.events()
    assert worker.submissions[0].blocked_by_risk is True


def test_repeated_rejections_escalate_to_their_own_event(store):
    channel = MemoryChannel()
    execution = FakeExecution(outcomes={"EURUSD": Outcome(False, "MARKET_CLOSED")})
    worker = build(
        store,
        execution=execution,
        feed=Feed([BarBatch(closed_instruments=frozenset({"EURUSD"}))]),
        evaluator=Evaluator({"EURUSD": [Plan("p1", "EURUSD")]}),
        dispatcher=AlertDispatcher([channel]),
        config=LiveWorkerConfig(max_cycles=5, reject_alert_threshold=3),
    )
    for _ in range(4):
        worker.run_cycle()
    assert AlertEvent.ORDER_REJECTED_REPEATEDLY in channel.events()


def test_a_submit_error_is_NOT_swallowed(store):
    """V1's bare except turned every execution error into silence."""
    execution = FakeExecution(raises=ConnectionError("venue closed the socket"))
    worker = build(
        store,
        execution=execution,
        feed=Feed([BarBatch(closed_instruments=frozenset({"EURUSD"}))]),
        evaluator=Evaluator({"EURUSD": [Plan("p1", "EURUSD")]}),
    )
    with pytest.raises(ConnectionError):
        worker.run_cycle()


def test_a_submit_error_reaches_the_heartbeat_through_the_loop(store):
    execution = FakeExecution(raises=ConnectionError("socket closed"))
    worker = build(
        store,
        execution=execution,
        feed=Feed([BarBatch(closed_instruments=frozenset({"EURUSD"}))]),
        evaluator=Evaluator({"EURUSD": [Plan("p1", "EURUSD")]}),
        config=LiveWorkerConfig(
            max_cycles=1, idle_sleep_seconds=0.0, failure_backoff_seconds=0.0
        ),
    )
    worker.run(install_signals=False)
    row = store.heartbeat_rows()[0]
    assert "socket closed" in row["last_error"]
    assert row["cycles_failed"] == 1


# ------------------------------------------------------------- kill switch


def test_the_kill_switch_stops_evaluation_but_not_ingestion(store):
    class Blocked:
        def blocks_new_risk(self):
            return True

    state = State()
    execution = FakeExecution()
    worker = LiveWorker(
        execution_service=execution,
        feed=Feed([BarBatch(frames={"EURUSD": object()}, closed_instruments=frozenset({"EURUSD"}))]),
        evaluator=Evaluator({"EURUSD": [Plan("p1", "EURUSD")]}),
        context_builder=lambda p: {},
        store=store,
        market_state=state,
        kill_switch=Blocked(),
        config=LiveWorkerConfig(max_cycles=1),
        worker="live@h:1",
    )
    result = worker.run_cycle()
    assert result.jobs == 0
    assert execution.submitted == []
    # Data still flows: a kill switch that blinds you is the wrong kill switch.
    assert state.updates == 1


# ---------------------------------------------------------- reconciliation


def test_startup_reconciles_BEFORE_trading(store):
    """A predecessor that died mid-order may have a real position behind it."""
    execution = FakeExecution()
    worker = build(store, execution=execution)
    worker.resume()
    assert execution.reconcile_calls == 1


def test_a_divergent_reconciliation_alerts_with_the_counts(store):
    channel = MemoryChannel()
    execution = FakeExecution(recon=Recon(unknown=("c1",), orphans=("B123",)))
    worker = build(store, execution=execution, dispatcher=AlertDispatcher([channel]))
    # Fails closed as well as alerting: see test_live_worker_reconcile.py.
    with pytest.raises(StartupReconciliationError):
        worker.resume()
    alert = next(a for a in channel.sent if a.event is AlertEvent.RECONCILIATION_DIVERGENCE)
    assert alert.context["orphans"] == 1
    assert alert.context["unknown"] == 1
    # An orphan is a REAL position with no local record and no stop.
    assert "no stop attached" in alert.message


def test_a_reconciliation_error_alerts_rather_than_passing_silently(store):
    channel = MemoryChannel()
    execution = FakeExecution(recon=ConnectionError("gateway down"))
    worker = build(store, execution=execution, dispatcher=AlertDispatcher([channel]))
    # "Could not check" must not read as "clean": the worker refuses to start.
    with pytest.raises(StartupReconciliationError):
        worker.resume()
    assert AlertEvent.BROKER_UNHEALTHY in channel.events()


def test_reconciliation_runs_periodically_not_only_on_demand(store):
    execution = FakeExecution()
    worker = build(
        store,
        execution=execution,
        config=LiveWorkerConfig(max_cycles=10, reconcile_every_cycles=3),
    )
    for _ in range(6):
        worker.run_cycle()
    assert execution.reconcile_calls == 2


# -------------------------------------------------------------- mode gating


def test_the_worker_refuses_to_start_in_an_unauthorised_mode(store):
    """V1 shipped FIBOKEI_LIVE_EXECUTION_ENABLED: "true" in render.yaml.

    The environment was the only gate, and the environment was wrong.
    """
    execution = FakeExecution(mode="live")
    worker = build(store, execution=execution)  # allowed_modes defaults to ("paper",)
    with pytest.raises(RuntimeError, match="refuses to start"):
        worker.setup()


def test_paper_mode_starts(store):
    build(store, execution=FakeExecution(mode="paper")).setup()


def test_live_mode_starts_only_when_explicitly_allowed(store):
    worker = build(
        store,
        execution=FakeExecution(mode="live"),
        config=LiveWorkerConfig(allowed_modes=("paper", "live"), max_cycles=1),
    )
    worker.setup()


# -------------------------------------------------------------- data staleness


def test_stale_bars_raise_DATA_STALE(store):
    """Stale means the NEXT bar is overdue by the threshold: on H1 with a 900 s
    threshold, a bar that closed 4,600 s ago (next close 1,000 s late)."""
    channel = MemoryChannel()
    worker = build(
        store,
        feed=Feed([BarBatch(ages={"EURUSD": 4600.0}, timeframe="H1")]),
        dispatcher=AlertDispatcher([channel]),
        config=LiveWorkerConfig(max_cycles=1, data_stale_after_seconds=900),
    )
    worker.run_cycle()
    assert AlertEvent.DATA_STALE in channel.events()
    alert = next(a for a in channel.sent if a.event is AlertEvent.DATA_STALE)
    assert "EURUSD=4600s (next bar 1000s overdue)" in alert.message


def test_the_current_bar_is_not_stale_on_a_slow_timeframe(store):
    """The defect seen on the first forward paper cycle (2026-09-29 20:59 UTC):
    an H4 bar that closed 3,545 s earlier was reported stale against a fixed
    900 s threshold, which on H4 fires ~94% of the time. Bars younger than
    timeframe + threshold are current; a bar with an unknown timeframe keeps
    the bare threshold."""
    channel = MemoryChannel()
    worker = build(
        store,
        feed=Feed([BarBatch(ages={"XAUUSD": 3545.0, "EURUSD": 14400.0 + 899.0}, timeframe="H4")]),
        dispatcher=AlertDispatcher([channel]),
        config=LiveWorkerConfig(max_cycles=1, data_stale_after_seconds=900),
    )
    worker.run_cycle()
    assert AlertEvent.DATA_STALE not in channel.events()
    channel2 = MemoryChannel()
    worker2 = build(
        store,
        feed=Feed([BarBatch(ages={"EURUSD": 901.0}, timeframe="")]),
        dispatcher=AlertDispatcher([channel2]),
        config=LiveWorkerConfig(max_cycles=1, data_stale_after_seconds=900),
    )
    worker2.run_cycle()
    assert AlertEvent.DATA_STALE in channel2.events()


def test_abandoning_a_cycle_says_a_position_may_exist(store):
    channel = MemoryChannel()
    worker = build(store, dispatcher=AlertDispatcher([channel]))
    worker.on_abandon()
    alert = next(a for a in channel.sent if a.event is AlertEvent.ORDER_UNKNOWN)
    assert "reconcile before restarting" in alert.message
