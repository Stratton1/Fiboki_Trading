"""The live worker's reconciliation schedule, cycle budget and feed wait.

Startup: reconcile once, before the first cycle, and REFUSE TO TRADE unless the
report is clean. Afterwards: reconcile on a wall-time interval as well as a
cycle count, and alert on anything unclean. Fakes only; no venue, no network.
"""
from __future__ import annotations

import pytest

from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel, Severity
from fiboki.workers.base import EXIT_FATAL, EXIT_LEASE_HELD, WorkerStore, log_once, reset_log_once
from fiboki.workers.live_worker import (
    EXIT_RECONCILE_BLOCKED,
    LIVE_CYCLE_BUDGET_EXCEEDED,
    BarBatch,
    LiveWorker,
    LiveWorkerConfig,
    StartupReconciliationError,
    timeframe_seconds,
)


class Mode:
    value = "paper"


class Adapter:
    name = "fake"


class Recon:
    def __init__(self, unknown=(), orphans=(), mismatches=(), errors=()):
        self.still_unknown = tuple(unknown)
        self.orphan_broker_refs = tuple(orphans)
        self.size_mismatches = tuple(mismatches)
        self.errors = tuple(errors)

    @property
    def clean(self):
        return not (
            self.still_unknown or self.orphan_broker_refs or self.size_mismatches or self.errors
        )

    def summary(self):
        return "recon"


class Execution:
    def __init__(self, reports=None, raises=None):
        self.adapter = Adapter()
        self.mode = Mode()
        self.reports = list(reports or [Recon()])
        self.raises = raises
        self.reconcile_calls = 0
        self.submitted = []

    def reconcile(self):
        self.reconcile_calls += 1
        if self.raises is not None:
            raise self.raises
        return self.reports[min(self.reconcile_calls - 1, len(self.reports) - 1)]

    def submit(self, plan, context):  # pragma: no cover - never reached here
        self.submitted.append(plan)
        raise AssertionError("a worker that failed startup reconciliation submitted")


class NoReconcile:
    adapter = Adapter()
    mode = Mode()


class Divergence:
    def __init__(self, kind):
        self.kind = kind


class PositionReconciler:
    def __init__(self, found=(), raises=None):
        self.found = tuple(found)
        self.raises = raises
        self.calls: list[bool] = []

    def reconcile(self, *, repair=True):
        self.calls.append(repair)
        if self.raises is not None:
            raise self.raises
        return self.found


class Feed:
    def __init__(self, batch=None, timeframe="H1"):
        self.batch = batch or BarBatch(timeframe=timeframe)
        self.polls = 0

    def poll(self):
        self.polls += 1
        return self.batch


class Evaluator:
    def evaluate(self, instruments, batch):
        return []


class Mono:
    """A controllable monotonic clock for the worker."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    s = WorkerStore.sqlite_at(tmp_path / "state.db")
    yield s
    s.close()


def build(store, *, execution=None, feed=None, config=None, **kwargs):
    return LiveWorker(
        execution_service=execution or Execution(),
        feed=feed or Feed(),
        evaluator=Evaluator(),
        context_builder=lambda plan: {},
        store=store,
        config=config or LiveWorkerConfig(max_cycles=1, idle_sleep_seconds=0.0),
        worker="live@h:1",
        **kwargs,
    )


# ------------------------------------------------------ startup: fail closed


def test_a_clean_startup_reconciliation_lets_the_worker_run(store):
    execution = Execution()
    feed = Feed()
    worker = build(store, execution=execution, feed=feed)
    assert worker.run(install_signals=False) == 0
    assert execution.reconcile_calls == 1
    assert feed.polls == 1


def test_reconcile_runs_BEFORE_the_first_poll(store):
    order: list[str] = []

    class Ordered(Execution):
        def reconcile(self):
            order.append("reconcile")
            return Recon()

    class OrderedFeed(Feed):
        def poll(self):
            order.append("poll")
            return super().poll()

    build(store, execution=Ordered(), feed=OrderedFeed()).run(install_signals=False)
    assert order[:2] == ["reconcile", "poll"]


@pytest.mark.parametrize(
    "report",
    [
        Recon(unknown=("FBK-1",)),
        Recon(orphans=("4711",)),
        Recon(mismatches=("FBK-2:local=1 venue=2",)),
    ],
)
def test_a_divergent_startup_refuses_to_trade_and_exits_do_not_restart(store, report):
    channel = MemoryChannel()
    execution = Execution(reports=[report])
    feed = Feed()
    worker = build(
        store, execution=execution, feed=feed, dispatcher=AlertDispatcher([channel])
    )
    code = worker.run(install_signals=False)
    assert code == EXIT_RECONCILE_BLOCKED == EXIT_LEASE_HELD
    assert feed.polls == 0  # not one cycle ran
    assert worker.startup_error is not None and worker.startup_error.blocking
    row = store.heartbeat_rows()[0]
    assert row["status"] == "crashed"
    assert "REFUSING TO TRADE" in row["last_error"]
    refusal = next(
        a for a in channel.sent
        if a.event is AlertEvent.RECONCILIATION_DIVERGENCE and "REFUSING" in a.message
    )
    assert refusal.severity is Severity.CRITICAL


def test_a_startup_that_could_not_reconcile_fails_closed_but_may_restart(store):
    """Venue unreachable is 'could not check', not 'clean', and not a divergence."""
    execution = Execution(reports=[Recon(errors=("venue_unreachable:ConnectionError:x",))])
    feed = Feed()
    worker = build(store, execution=execution, feed=feed)
    assert worker.run(install_signals=False) == EXIT_FATAL
    assert feed.polls == 0
    assert worker.startup_error is not None and not worker.startup_error.blocking


def test_a_reconcile_that_raises_at_startup_fails_closed(store):
    feed = Feed()
    worker = build(store, execution=Execution(raises=ConnectionError("down")), feed=feed)
    assert worker.run(install_signals=False) == EXIT_FATAL
    assert feed.polls == 0


def test_an_execution_service_without_reconcile_cannot_start(store):
    worker = build(store, execution=NoReconcile())
    with pytest.raises(StartupReconciliationError, match="no reconcile"):
        worker.resume()


def test_a_position_manager_divergence_also_blocks_startup(store):
    reconciler = PositionReconciler(found=[Divergence("unmanaged_at_venue")])
    feed = Feed()
    worker = build(store, feed=feed, position_reconciler=reconciler)
    assert worker.run(install_signals=False) == EXIT_RECONCILE_BLOCKED
    assert feed.polls == 0
    # Detect only: the worker never asks the manager to amend at the venue.
    assert reconciler.calls == [False]


# --------------------------------------------------------- periodic timer


def test_reconcile_runs_on_the_wall_clock_interval(store):
    mono = Mono()
    execution = Execution()
    worker = build(
        store,
        execution=execution,
        config=LiveWorkerConfig(reconcile_every_cycles=10_000, reconcile_interval_seconds=900),
        monotonic=mono,
    )
    worker.resume()
    assert execution.reconcile_calls == 1
    for _ in range(5):
        mono.t += 60
        worker.run_cycle()
    assert execution.reconcile_calls == 1
    mono.t += 900
    worker.run_cycle()
    assert execution.reconcile_calls == 2


def test_the_timer_runs_even_while_the_feed_is_waiting(store):
    """A weekend has no bars. It must still reconcile."""

    class NeverDue(Feed):
        def wait_until_due(self, should_stop=None):
            return False

    mono = Mono()
    execution = Execution()
    feed = NeverDue()
    worker = build(
        store,
        execution=execution,
        feed=feed,
        config=LiveWorkerConfig(reconcile_interval_seconds=900),
        monotonic=mono,
    )
    worker.resume()
    mono.t += 901
    result = worker.run_cycle()
    assert "waiting" in result.detail
    assert feed.polls == 0
    assert execution.reconcile_calls == 2


def test_a_periodic_divergence_alerts_but_does_not_stop_the_worker(store):
    channel = MemoryChannel()
    mono = Mono()
    execution = Execution(reports=[Recon(), Recon(orphans=("4711",))])
    worker = build(
        store,
        execution=execution,
        config=LiveWorkerConfig(reconcile_interval_seconds=900),
        dispatcher=AlertDispatcher([channel]),
        monotonic=mono,
    )
    worker.resume()
    mono.t += 901
    worker.run_cycle()
    alert = next(a for a in channel.sent if a.event is AlertEvent.RECONCILIATION_DIVERGENCE)
    assert alert.context["orphans"] == 1


def test_a_report_carrying_only_errors_is_NOT_read_as_clean(store):
    """ExecutionService.reconcile returns venue failures as ``errors``."""
    channel = MemoryChannel()
    mono = Mono()
    execution = Execution(reports=[Recon(), Recon(errors=("venue_unreachable:x",))])
    worker = build(
        store,
        execution=execution,
        config=LiveWorkerConfig(reconcile_interval_seconds=900),
        dispatcher=AlertDispatcher([channel]),
        monotonic=mono,
    )
    worker.resume()
    mono.t += 901
    worker.run_cycle()
    assert AlertEvent.BROKER_UNHEALTHY in channel.events()


# --------------------------------------------------------- feed + freshness


def test_a_feed_that_is_not_due_yields_an_idle_cycle_without_polling(store):
    class NotYet(Feed):
        def wait_until_due(self, should_stop=None):
            return False

    feed = NotYet()
    worker = build(store, feed=feed)
    result = worker.run_cycle()
    assert result.jobs == 0 and feed.polls == 0
    assert worker.summary()["cycles"] == 0  # a wait is not an evaluation cycle


def test_a_closed_market_is_not_reported_stale(store):
    channel = MemoryChannel()
    batch = BarBatch(
        ages={"EURUSD": 150_000.0, "XAUUSD": 150_000.0},
        timeframe="H1",
        market_closed=frozenset({"EURUSD"}),
    )
    worker = build(store, feed=Feed(batch), dispatcher=AlertDispatcher([channel]))
    worker.run_cycle()
    alert = next(a for a in channel.sent if a.event is AlertEvent.DATA_STALE)
    assert "XAUUSD" in alert.message
    assert "EURUSD" not in alert.message


# ---------------------------------------------------------- cycle budget


def test_a_cycle_over_a_quarter_of_the_timeframe_warns(store):
    channel = MemoryChannel()
    mono = Mono()

    class SlowFeed(Feed):
        def poll(self):
            mono.t += 16.0  # M1 budget is 15 s
            return super().poll()

    before = LIVE_CYCLE_BUDGET_EXCEEDED.value(worker="live@h:1", timeframe="M1")
    worker = build(
        store,
        feed=SlowFeed(timeframe="M1"),
        dispatcher=AlertDispatcher([channel]),
        monotonic=mono,
    )
    worker.run_cycle()
    alert = next(a for a in channel.sent if "budget" in a.message)
    assert alert.severity is Severity.WARNING
    assert LIVE_CYCLE_BUDGET_EXCEEDED.value(worker="live@h:1", timeframe="M1") == before + 1
    assert worker.summary()["cycle_budget_breaches"] == 1


def test_a_cycle_inside_its_budget_is_silent(store):
    channel = MemoryChannel()
    mono = Mono()

    class QuickFeed(Feed):
        def poll(self):
            mono.t += 14.0
            return super().poll()

    worker = build(
        store,
        feed=QuickFeed(timeframe="M1"),
        dispatcher=AlertDispatcher([channel]),
        monotonic=mono,
    )
    worker.run_cycle()
    assert not [a for a in channel.sent if "budget" in a.message]


def test_the_wait_for_the_bar_is_not_charged_to_the_budget(store):
    channel = MemoryChannel()
    mono = Mono()

    class WaitingFeed(Feed):
        def wait_until_due(self, should_stop=None):
            mono.t += 50.0  # waiting, not working
            return True

    worker = build(
        store,
        feed=WaitingFeed(timeframe="M1"),
        dispatcher=AlertDispatcher([channel]),
        monotonic=mono,
    )
    worker.run_cycle()
    assert not [a for a in channel.sent if "budget" in a.message]


def test_timeframe_seconds() -> None:
    assert timeframe_seconds("H4") == 14_400.0
    assert timeframe_seconds("") is None
    assert timeframe_seconds("W1") is None


# --------------------------------------------------------------- log_once


def test_log_once_suppresses_repeats_until_the_ttl_passes() -> None:
    reset_log_once("t:")
    t = {"now": 0.0}
    assert log_once("t:a", 60, clock=lambda: t["now"]) is True
    assert log_once("t:a", 60, clock=lambda: t["now"]) is False
    assert log_once("t:b", 60, clock=lambda: t["now"]) is True
    t["now"] = 60.0
    assert log_once("t:a", 60, clock=lambda: t["now"]) is True
    assert log_once("t:c", 0, clock=lambda: t["now"]) is True
    assert log_once("t:c", 0, clock=lambda: t["now"]) is True
