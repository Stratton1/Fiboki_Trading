"""Resource-aware admission: keep the operator's Mac usable.

Throughput you cannot leave running overnight is not throughput. Every test
here uses a StaticProbe, so nothing sleeps and nothing samples the real
machine.
"""
from __future__ import annotations

import threading

import pytest

from fiboki.workers.scheduler import (
    JobPriority,
    LocalScheduler,
    ResourceSnapshot,
    SchedulerConfig,
    StaticProbe,
    SystemProbe,
    throttled_sequence,
)


def snapshot(**kwargs) -> ResourceSnapshot:
    base = {
        "cpu_count": 8,
        "load_1m": 1.0,
        "memory_available_fraction": 0.60,
        "memory_available_bytes": 8 * 1024**3,
        "gpu_count": 0,
        "on_battery": False,
        "disk_free_fraction": 0.5,
    }
    base.update(kwargs)
    return ResourceSnapshot(**base)


def scheduler(snap: ResourceSnapshot | None = None, **config) -> LocalScheduler:
    return LocalScheduler(SchedulerConfig(**config), StaticProbe(snap or snapshot()))


# ------------------------------------------------------------------ priority


def test_higher_priority_work_is_dispatched_first():
    """A reconciliation job must not queue behind 4,000 sweep cells."""
    sched = scheduler()
    for i in range(5):
        sched.submit(f"sweep{i}", priority=JobPriority.BULK)
    sched.submit("reconcile", priority=JobPriority.CRITICAL)
    sched.submit("validate", priority=JobPriority.HIGH)

    assert sched.queued_keys()[:2] == ["reconcile", "validate"]
    work, decision = sched.next_work()
    assert decision.admit
    assert work.key == "reconcile"


def test_equal_priority_keeps_submission_order_deterministically():
    sched = scheduler()
    for i in range(6):
        sched.submit(f"c{i}", priority=JobPriority.BULK)
    assert sched.queued_keys() == [f"c{i}" for i in range(6)]


# ------------------------------------------------------------------ capacity


def test_cpu_reserve_leaves_cores_for_the_operator():
    sched = scheduler(snapshot(cpu_count=8), max_parallel=16, cpu_reserve=0.25)
    # 8 cores, reserve 25% -> 6 usable, capped by max_parallel.
    assert sched.slots(snapshot(cpu_count=8)) == 6


def test_max_parallel_is_a_hard_ceiling():
    sched = scheduler(snapshot(cpu_count=64), max_parallel=4)
    assert sched.slots(snapshot(cpu_count=64)) == 4


def test_memory_pressure_stops_admission_entirely():
    """Swapping is what makes a Mac feel broken; CPU contention does not."""
    sched = scheduler(memory_floor_fraction=0.15)
    assert sched.slots(snapshot(memory_available_fraction=0.10)) == 0

    sched.submit("cell", priority=JobPriority.BULK)
    tight = LocalScheduler(
        SchedulerConfig(memory_floor_fraction=0.15),
        StaticProbe(snapshot(memory_available_fraction=0.10)),
    )
    tight.submit("cell", priority=JobPriority.BULK)
    decision = tight.admit()
    assert not decision.admit
    assert decision.retry_after_seconds > 0


def test_approaching_the_memory_floor_halves_parallelism_before_the_cliff():
    sched = scheduler(max_parallel=8, cpu_reserve=0.0, memory_floor_fraction=0.15)
    full = sched.slots(snapshot(memory_available_fraction=0.60))
    tight = sched.slots(snapshot(memory_available_fraction=0.20))
    assert tight < full
    assert tight >= 1


def test_high_load_average_blocks_new_work():
    """Something else (Xcode, Docker) already owns the machine."""
    sched = scheduler(load_ceiling_per_core=1.5)
    assert sched.slots(snapshot(cpu_count=8, load_1m=20.0)) == 0


def test_battery_caps_parallelism():
    sched = scheduler(max_parallel=8, battery_max_parallel=1, cpu_reserve=0.0)
    assert sched.slots(snapshot(on_battery=True)) == 1
    assert sched.slots(snapshot(on_battery=False)) == 8


def test_a_full_disk_refuses_everything_that_writes_results():
    tight = LocalScheduler(
        SchedulerConfig(disk_floor_fraction=0.05),
        StaticProbe(snapshot(disk_free_fraction=0.01)),
    )
    tight.submit("cell")
    decision = tight.admit()
    assert not decision.admit
    assert "disk" in decision.reason


# ------------------------------------------------------------------- gating


def test_critical_work_is_admitted_even_on_a_saturated_machine():
    """Throttling a reconciliation job because a sweep is running is backwards."""
    busy = LocalScheduler(
        SchedulerConfig(max_parallel=2, throttle_from_priority=int(JobPriority.NORMAL)),
        StaticProbe(snapshot(load_1m=50.0, memory_available_fraction=0.12)),
    )
    busy.submit("sweep", priority=JobPriority.BULK)
    assert not busy.admit().admit

    busy.submit("flatten", priority=JobPriority.CRITICAL)
    decision = busy.admit()
    assert decision.admit, decision.reason


def test_a_gpu_job_is_refused_when_there_is_no_gpu():
    sched = scheduler(snapshot(gpu_count=0))
    sched.submit("train", needs_gpu=True)
    decision = sched.admit()
    assert not decision.admit
    assert "GPU" in decision.reason


def test_a_gpu_job_is_admitted_when_a_gpu_exists():
    sched = scheduler(snapshot(gpu_count=1))
    sched.submit("train", needs_gpu=True)
    assert sched.admit().admit


def test_in_flight_jobs_consume_slots():
    sched = scheduler(snapshot(cpu_count=8), max_parallel=2, cpu_reserve=0.0)
    for i in range(4):
        sched.submit(f"c{i}", priority=JobPriority.BULK)
    first, _ = sched.next_work()
    second, _ = sched.next_work()
    assert first and second
    assert sched.in_flight() == 2

    third, decision = sched.next_work()
    assert third is None
    assert "in flight" in decision.reason

    sched.finish(first.key)
    fourth, _ = sched.next_work()
    assert fourth is not None


def test_requeue_puts_work_back_at_its_priority():
    sched = scheduler(max_parallel=1)
    sched.submit("a", priority=JobPriority.BULK)
    work, _ = sched.next_work()
    sched.requeue(work)
    assert sched.in_flight() == 0
    assert sched.queued_keys() == ["a"]


def test_an_empty_queue_refuses_without_a_retry_delay():
    sched = scheduler()
    decision = sched.admit()
    assert not decision.admit
    assert decision.retry_after_seconds == 0.0


# -------------------------------------------------------------- throttling


def test_throttled_sequence_paces_work_and_honours_the_stop_event():
    sched = scheduler(max_parallel=4)
    stop = threading.Event()
    out = list(throttled_sequence(["a", "b", "c"], sched, stop))
    assert out == ["a", "b", "c"]

    stop.set()
    assert list(throttled_sequence(["a"], sched, stop)) == []


def test_wait_for_capacity_returns_when_stopped():
    tight = LocalScheduler(
        SchedulerConfig(memory_floor_fraction=0.9, backoff_seconds=0.01),
        StaticProbe(snapshot(memory_available_fraction=0.1)),
    )
    tight.submit("cell")
    stop = threading.Event()
    timer = threading.Timer(0.1, stop.set)
    timer.start()
    try:
        assert tight.wait_for_capacity(stop, timeout=5.0) is False
    finally:
        timer.cancel()


# ---------------------------------------------------------------- the probe


def test_the_real_probe_returns_a_coherent_snapshot():
    snap = SystemProbe()()
    assert snap.cpu_count >= 1
    assert 0.0 <= snap.memory_available_fraction <= 1.0
    assert 0.0 <= snap.disk_free_fraction <= 1.0
    assert snap.load_per_core >= 0.0
    assert "cpus=" in snap.describe()


def test_an_unknown_memory_reading_is_conservative_not_optimistic(monkeypatch):
    """A wrong-but-optimistic guess wedges the desktop; conservative throttles."""
    probe = SystemProbe()
    monkeypatch.setattr(probe, "_macos_memory", lambda: None)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    available, fraction = probe._memory()
    assert fraction <= 0.25


def test_status_reports_what_it_refused_and_why():
    tight = LocalScheduler(
        SchedulerConfig(memory_floor_fraction=0.9),
        StaticProbe(snapshot(memory_available_fraction=0.1)),
    )
    tight.submit("cell", priority=JobPriority.BULK)
    tight.admit()
    status = tight.status()
    assert status["queued"] == 1
    assert status["slots_now"] == 0
    assert status["refusals"]


def test_scheduler_config_rejects_nonsense():
    with pytest.raises(ValueError):
        SchedulerConfig(max_parallel=0)
    with pytest.raises(ValueError):
        SchedulerConfig(cpu_reserve=1.0)
