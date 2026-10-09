"""Lease, heartbeat and shutdown -- the four V1 operational failures.

Each test names the failure it closes. In particular:

* two workers could run with the same hardcoded id and overwrite each other's
  heartbeat, making duplicate orders invisible;
* the heartbeat was written only on a SUCCESSFUL cycle, so "failing" and "dead"
  were the same observation;
* the worker had no error reporting at all, so a crash left no trace.
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel
from fiboki.workers.base import (
    EXIT_FATAL,
    EXIT_LEASE_HELD,
    EXIT_OK,
    CycleResult,
    LeaseLost,
    LeaseNotAcquired,
    Worker,
    WorkerConfig,
    WorkerLease,
    WorkerState,
    WorkerStore,
    force_release,
    worker_id,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "state.db"


@pytest.fixture
def store(db_path):
    s = WorkerStore.sqlite_at(db_path)
    yield s
    s.close()


class ManualClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 3, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ScriptedWorker(Worker):
    """Runs a scripted sequence of outcomes, one per cycle."""

    def __init__(self, script, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.script = list(script)
        self.calls = 0
        self.resumed = 0
        self.torn_down = 0
        self.abandoned = 0

    def resume(self) -> None:
        self.resumed += 1

    def teardown(self) -> None:
        self.torn_down += 1

    def on_abandon(self) -> None:
        self.abandoned += 1

    def run_cycle(self) -> CycleResult:
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        action = self.script[index]
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action()
        return action


def _config(**kwargs) -> WorkerConfig:
    base = {
        "kind": "research",
        "idle_sleep_seconds": 0.0,
        "busy_sleep_seconds": 0.0,
        "failure_backoff_seconds": 0.0,
        "max_cycles": 1,
        "shutdown_grace_seconds": 0.0,
    }
    base.update(kwargs)
    return WorkerConfig(**base)


# ------------------------------------------------------------------ identity


def test_worker_id_is_distinct_per_process():
    """V1 hardcoded ``WORKER_ID = "worker-1"``; two processes then shared a row."""
    a = worker_id("research", host="mac", pid=101)
    b = worker_id("research", host="mac", pid=102)
    assert a != b
    assert a == "research@mac:101"


# -------------------------------------------------------------------- lease


def test_second_lease_holder_is_refused(store, db_path):
    """Two engines, one file -- a genuine two-connection contention."""
    other = WorkerStore.sqlite_at(db_path)
    try:
        first = WorkerLease(store, "research", "a@host:1", ttl_seconds=60)
        second = WorkerLease(other, "research", "b@host:2", ttl_seconds=60)
        state = first.acquire()
        assert state.holder == "a@host:1"
        assert state.fence == 1

        with pytest.raises(LeaseNotAcquired) as exc:
            second.acquire()
        # The message must name the holder: "somebody else has it" is useless
        # at 03:00 when you need to know which machine.
        assert "a@host:1" in str(exc.value)
        assert second.held is False
    finally:
        other.close()


def test_lease_is_reclaimed_after_it_expires(store):
    clock = ManualClock()
    first = WorkerLease(store, "research", "a@h:1", ttl_seconds=30, clock=clock)
    first.acquire()
    second = WorkerLease(store, "research", "b@h:2", ttl_seconds=30, clock=clock)
    with pytest.raises(LeaseNotAcquired):
        second.acquire()

    clock.advance(31)  # the holder stopped renewing, e.g. it was SIGKILLed
    state = second.acquire()
    assert state.holder == "b@h:2"
    # The fence INCREMENTS, so a resumed zombie's writes are detectable.
    assert state.fence == 2


def test_the_same_holder_reclaims_its_own_lease_after_a_restart(store):
    lease = WorkerLease(store, "research", "a@h:1", ttl_seconds=60)
    lease.acquire()
    again = WorkerLease(store, "research", "a@h:1", ttl_seconds=60)
    state = again.acquire()
    assert state.holder == "a@h:1"
    assert state.fence == 2


def test_renew_raises_when_the_lease_was_taken(store, db_path):
    clock = ManualClock()
    first = WorkerLease(store, "research", "a@h:1", ttl_seconds=10, clock=clock)
    first.acquire()
    clock.advance(11)
    other_store = WorkerStore.sqlite_at(db_path)
    try:
        second = WorkerLease(other_store, "research", "b@h:2", ttl_seconds=10, clock=clock)
        second.acquire()
        with pytest.raises(LeaseLost):
            first.renew()
        assert first.held is False
    finally:
        other_store.close()


def test_release_lets_the_next_worker_start_immediately(store):
    first = WorkerLease(store, "research", "a@h:1", ttl_seconds=3600)
    first.acquire()
    first.release()
    second = WorkerLease(store, "research", "b@h:2", ttl_seconds=3600)
    assert second.acquire().holder == "b@h:2"


def test_force_release_is_available_to_an_operator(store):
    lease = WorkerLease(store, "research", "a@h:1", ttl_seconds=3600)
    lease.acquire()
    assert force_release(store, "research") is True
    assert WorkerLease(store, "research", "b@h:2").acquire().holder == "b@h:2"


def test_a_second_worker_process_exits_with_a_distinct_code(store, db_path):
    """THE test. Start two; the second must EXIT, not proceed."""
    other = WorkerStore.sqlite_at(db_path)
    try:
        first = ScriptedWorker([CycleResult.worked(1)], _config(), store, worker="a@h:1")
        second = ScriptedWorker([CycleResult.worked(1)], _config(), other, worker="b@h:2")

        first.lease.acquire()  # first is "running"
        code = second.run(install_signals=False)

        assert code == EXIT_LEASE_HELD
        # It must not have done ANY work.
        assert second.calls == 0
        assert second.resumed == 0
    finally:
        other.close()


def test_the_refused_worker_alerts_on_contention(store, db_path):
    other = WorkerStore.sqlite_at(db_path)
    try:
        channel = MemoryChannel()
        dispatcher = AlertDispatcher([channel])
        WorkerLease(store, "research", "a@h:1", ttl_seconds=600).acquire()
        second = ScriptedWorker(
            [CycleResult.idle()], _config(), other, worker="b@h:2", dispatcher=dispatcher
        )
        second.run(install_signals=False)
        assert AlertEvent.WORKER_LEASE_CONTENDED in channel.events()
        # Retries under launchd get new pids; the incident is the (lease, holder).
        alert = next(a for a in channel.sent if a.event is AlertEvent.WORKER_LEASE_CONTENDED)
        assert alert.dedupe_key == "lease_contended:research:a@h:1"
        third = ScriptedWorker(
            [CycleResult.idle()], _config(), other, worker="c@h:3", dispatcher=dispatcher
        )
        third.run(install_signals=False)
        keys = {a.dedupe_key for a in channel.sent if a.event is AlertEvent.WORKER_LEASE_CONTENDED}
        assert keys == {"lease_contended:research:a@h:1"}, "same holder, same incident"
    finally:
        other.close()


# ---------------------------------------------------------------- heartbeat


def test_heartbeat_is_written_on_a_FAILED_cycle_with_the_error(store):
    """V1 wrote the heartbeat only after a successful cycle.

    A failing worker therefore went stale exactly like a dead one, so the
    operator could not tell "it is crashing every cycle" from "it is gone",
    and learned to ignore both.
    """
    worker = ScriptedWorker(
        [RuntimeError('provider returned "504 Gateway Timeout"')],
        _config(max_cycles=1),
        store,
        worker="a@h:1",
    )
    code = worker.run(install_signals=False)
    assert code == EXIT_OK

    rows = {r["worker_id"]: r for r in store.heartbeat_rows()}
    row = rows["a@h:1"]
    assert row["cycles_failed"] == 1
    assert row["cycles_ok"] == 0
    # The error text is ON the heartbeat, including its quotes.
    assert "504 Gateway Timeout" in row["last_error"]
    assert row["last_error_at"] is not None


def test_a_failing_cycle_leaves_a_fresh_heartbeat(store):
    """Freshness must distinguish 'failing' from 'dead'."""
    worker = ScriptedWorker([RuntimeError("boom")], _config(max_cycles=1), store, worker="a@h:1")
    worker.run(install_signals=False)
    views = {v.worker_id: v for v in store.heartbeats()}
    assert views["a@h:1"].age_seconds < 5.0


def test_the_error_reporter_is_called_for_every_failed_cycle(store):
    """V1's worker had NO error reporting; a bare except swallowed everything."""
    captured = []

    def reporter(exc, context):
        captured.append((type(exc).__name__, dict(context)))

    worker = ScriptedWorker(
        [RuntimeError("one"), RuntimeError("two")],
        _config(max_cycles=2),
        store,
        worker="a@h:1",
        error_reporter=reporter,
    )
    worker.run(install_signals=False)
    assert len(captured) == 2
    assert captured[0][0] == "RuntimeError"
    assert captured[0][1]["worker_id"] == "a@h:1"
    assert captured[0][1]["phase"] == "cycle"


def test_heartbeat_records_success_counts(store):
    worker = ScriptedWorker(
        [CycleResult.worked(3, "three jobs")], _config(max_cycles=2), store, worker="a@h:1"
    )
    worker.run(install_signals=False)
    row = store.heartbeat_rows()[0]
    assert row["cycles_ok"] == 2
    assert row["jobs_done"] == 6
    assert row["status"] == WorkerState.STOPPED.value


def test_two_workers_write_two_heartbeat_rows(store, db_path):
    """V1's shared id meant one row that always looked fresh."""
    other = WorkerStore.sqlite_at(db_path)
    try:
        a = ScriptedWorker([CycleResult.idle()], _config(), store, worker="a@h:1")
        a.run(install_signals=False)
        b = ScriptedWorker([CycleResult.idle()], _config(), other, worker="b@h:2")
        b.run(install_signals=False)
        ids = {r["worker_id"] for r in store.heartbeat_rows()}
        assert ids == {"a@h:1", "b@h:2"}
    finally:
        other.close()


# ------------------------------------------------------------------ control


def test_resume_runs_once_after_the_lease_and_before_the_first_cycle(store):
    order = []

    class Ordered(ScriptedWorker):
        def resume(self):
            order.append(("resume", self.lease.held))

        def run_cycle(self):
            order.append(("cycle", self.lease.held))
            return CycleResult.idle()

    worker = Ordered([], _config(max_cycles=2), store, worker="a@h:1")
    worker.run(install_signals=False)
    assert order[0] == ("resume", True)
    assert order[1] == ("cycle", True)
    assert sum(1 for step in order if step[0] == "resume") == 1


def test_a_failure_inside_resume_is_fatal_and_releases_the_lease(store):
    class BadResume(ScriptedWorker):
        def resume(self):
            raise RuntimeError("could not reclaim abandoned work")

    worker = BadResume([], _config(), store, worker="a@h:1")
    assert worker.run(install_signals=False) == EXIT_FATAL
    row = store.heartbeat_rows()[0]
    assert row["status"] == WorkerState.CRASHED.value
    # The lease must be free, or a restart would be locked out for the TTL.
    assert WorkerLease(store, "research", "b@h:2").acquire().holder == "b@h:2"


def test_request_stop_ends_the_loop_after_the_current_cycle(store):
    worker = None

    def stop_after_this_one():
        worker.request_stop("test")
        return CycleResult.worked(1)

    worker = ScriptedWorker(
        [stop_after_this_one, CycleResult.worked(1)],
        _config(max_cycles=0, shutdown_grace_seconds=0.0),
        store,
        worker="a@h:1",
    )
    assert worker.run(install_signals=False) == EXIT_OK
    assert worker.calls == 1  # the second cycle never ran
    assert worker.torn_down == 1


def test_the_lease_is_released_on_a_clean_stop(store):
    worker = ScriptedWorker([CycleResult.idle()], _config(), store, worker="a@h:1")
    worker.run(install_signals=False)
    state = WorkerLease(store, "research", "b@h:2").current()
    assert state is not None
    assert state.expires_at <= datetime.now(tz=UTC) + timedelta(seconds=1)


def test_the_worker_gives_up_after_too_many_consecutive_failures(store):
    worker = ScriptedWorker(
        [RuntimeError("always")],
        _config(max_cycles=0, max_consecutive_failures=3),
        store,
        worker="a@h:1",
    )
    assert worker.run(install_signals=False) == EXIT_FATAL
    assert worker.consecutive_failures == 3


def test_a_feed_outage_is_one_broker_incident_across_restarts(store):
    """A launchd restart changes the pid. It must not open a new incident,
    and a candle-fetch failure must not be labelled as the strategy degrading.
    """
    channel = MemoryChannel()
    dispatcher = AlertDispatcher().add_channel(channel)

    class ProviderError(Exception):
        pass

    class ConnectError(Exception):
        pass

    feed = ProviderError("every candle fetch failed")
    feed.__cause__ = ConnectError("[Errno 8] nodename nor servname provided")
    for pid in (101, 202):
        worker = ScriptedWorker(
            [feed],
            _config(max_cycles=1, kind="paper"),
            store,
            dispatcher=dispatcher,
            worker=f"paper@host:{pid}",
        )
        worker.run(install_signals=False)
    alerts = channel.sent
    assert alerts
    assert {a.event for a in alerts} == {AlertEvent.BROKER_UNHEALTHY}
    assert {a.key() for a in alerts} == {"cycle_fail:paper:broker_unhealthy"}


def test_an_ordinary_cycle_error_stays_a_strategy_degradation(store):
    channel = MemoryChannel()
    dispatcher = AlertDispatcher().add_channel(channel)
    worker = ScriptedWorker(
        [RuntimeError("indicator blew up")],
        _config(max_cycles=1, kind="paper"),
        store,
        dispatcher=dispatcher,
        worker="paper@host:1",
    )
    worker.run(install_signals=False)
    (alert,) = channel.sent
    assert alert.event is AlertEvent.STRATEGY_DEGRADED
    assert alert.key() == "cycle_fail:paper:strategy_degraded"


def test_a_successful_cycle_resets_the_failure_streak(store):
    worker = ScriptedWorker(
        [RuntimeError("one"), CycleResult.worked(1), RuntimeError("two")],
        _config(max_cycles=3, max_consecutive_failures=10),
        store,
        worker="a@h:1",
    )
    worker.run(install_signals=False)
    assert worker.consecutive_failures == 1


def test_idle_sleep_is_interrupted_by_a_stop_request(store):
    """A SIGTERM during an idle sleep must take effect at once."""
    worker = ScriptedWorker(
        [CycleResult.idle()],
        _config(max_cycles=0, idle_sleep_seconds=30.0, shutdown_grace_seconds=0.0),
        store,
        worker="a@h:1",
    )
    started = time.monotonic()
    thread = threading.Thread(target=lambda: worker.run(install_signals=False))
    thread.start()
    time.sleep(0.2)
    worker.request_stop("sigterm")
    thread.join(timeout=5)
    assert not thread.is_alive(), "the worker slept through its stop request"
    assert time.monotonic() - started < 5.0


class ConnectError(Exception):
    """Named like httpx's: classified as an outage by cycle_failure_event."""


def test_an_outage_is_ridden_out_when_exit_on_outage_is_off(store):
    script = [ConnectError("offline")] * 5 + [CycleResult.idle()]
    worker = ScriptedWorker(
        script,
        _config(max_cycles=6, max_consecutive_failures=3, exit_on_outage=False),
        store,
        worker="a@h:1",
    )
    assert worker.run(install_signals=False) == EXIT_OK
    assert worker.calls == 6
    assert worker.consecutive_failures == 0


def test_an_outage_still_ends_the_worker_by_default(store):
    worker = ScriptedWorker(
        [ConnectError("offline")],
        _config(max_cycles=0, max_consecutive_failures=3),
        store,
        worker="a@h:1",
    )
    assert worker.run(install_signals=False) == EXIT_FATAL


def test_a_non_outage_failure_still_ends_the_worker_with_exit_on_outage_off(store):
    worker = ScriptedWorker(
        [RuntimeError("poisoned state")],
        _config(max_cycles=0, max_consecutive_failures=3, exit_on_outage=False),
        store,
        worker="a@h:1",
    )
    assert worker.run(install_signals=False) == EXIT_FATAL
    assert worker.consecutive_failures == 3
