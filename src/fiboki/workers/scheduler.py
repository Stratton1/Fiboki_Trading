"""Resource-aware local scheduling: keep the operator's Mac usable.

The constraint this module encodes
----------------------------------
Fiboki V2 is local-first.  The machine running a 10,000-cell sweep is the same
machine the operator is reading charts on, answering email on, and watching a
live paper session on.  A scheduler that runs ``cpu_count()`` workers at full
tilt is correct on a cloud box and wrong here: it makes the desktop unusable,
so the operator kills the sweep, so the sweep never finishes, so the research
never happens.  Throughput you cannot leave running overnight is not
throughput.

So admission is a function of *observed* resource state, not a fixed number:

* **CPU**: leave ``cpu_reserve`` of the cores free.  The default reserves
  enough that the UI stays responsive on a laptop.
* **Load average**: a 1-minute load above ``load_ceiling * cores`` means the
  machine is already saturated by something else (Xcode, a browser, Docker).
  Admit nothing more until it drops.
* **Memory**: below ``memory_floor_fraction`` available, admit nothing.  On a
  Mac this matters more than CPU: swapping is what makes the machine feel
  broken, and a pandas sweep will happily consume everything.
* **GPU**: reported, and gates only jobs that declare they need one.  Nothing
  here pretends to schedule GPU memory; it reports availability and refuses
  GPU jobs when there is none, which is the honest scope.
* **Battery / thermal**: a laptop on battery admits fewer jobs.

Priority
--------
A live-trading-adjacent job must not sit behind 4,000 sweep cells.  Jobs carry
a :class:`JobPriority`; the ready queue is ordered by priority then submission
order, so ordering is deterministic and a test can assert it.

Honest about what this is not
-----------------------------
This is an admission controller and a priority queue, in one process.  It is
not a cluster scheduler, it does not preempt a running job, and it cannot
reclaim memory from one.  ``nice``/``ionice`` hints are applied where the
platform supports them; on macOS ``ionice`` does not exist and the call is a
no-op, which is stated rather than silently swallowed.
"""
from __future__ import annotations

import heapq
import itertools
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol, runtime_checkable

from fiboki.obs.logging import get_logger

__all__ = [
    "AdmissionDecision",
    "JobPriority",
    "LocalScheduler",
    "ResourceProbe",
    "ResourceSnapshot",
    "ScheduledWork",
    "SchedulerConfig",
    "StaticProbe",
    "SystemProbe",
    "apply_nice",
]

_log = get_logger("fiboki.workers.scheduler")


class JobPriority(IntEnum):
    """Lower runs first.  Named so a call site does not pass a bare int."""

    CRITICAL = 0  # reconciliation, kill-switch follow-ups
    HIGH = 10  # validation of a promoted candidate
    NORMAL = 20  # ordinary backtests
    BULK = 30  # sweep cells, ablations -- the thing that must yield
    IDLE = 40  # only when the machine is otherwise doing nothing


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """What the machine looks like right now.  All fields are observations."""

    cpu_count: int
    load_1m: float
    memory_available_fraction: float
    memory_available_bytes: int = 0
    gpu_count: int = 0
    on_battery: bool = False
    disk_free_fraction: float = 1.0

    @property
    def load_per_core(self) -> float:
        return self.load_1m / self.cpu_count if self.cpu_count else self.load_1m

    def describe(self) -> str:
        return (
            f"cpus={self.cpu_count} load1m={self.load_1m:.2f} "
            f"({self.load_per_core:.2f}/core) mem_free={self.memory_available_fraction:.0%} "
            f"gpus={self.gpu_count} battery={self.on_battery} "
            f"disk_free={self.disk_free_fraction:.0%}"
        )


@runtime_checkable
class ResourceProbe(Protocol):
    def __call__(self) -> ResourceSnapshot: ...


@dataclass
class StaticProbe:
    """A fixed snapshot.  Every scheduler test uses this; nothing is sampled."""

    snapshot: ResourceSnapshot

    def __call__(self) -> ResourceSnapshot:
        return self.snapshot


class SystemProbe:
    """Real measurement, standard library only.

    ``psutil`` would be nicer and is not a dependency this project will take
    for observability.  Where a number is genuinely unavailable the probe
    returns a CONSERVATIVE value (assume memory is tight) rather than an
    optimistic one, because an optimistic guess here is what makes the desktop
    unusable.
    """

    def __init__(self, *, root: str = "/") -> None:
        self.root = root
        self._page_size = 4096

    def __call__(self) -> ResourceSnapshot:
        cpus = os.cpu_count() or 1
        try:
            load = os.getloadavg()[0]
        except (OSError, AttributeError):  # pragma: no cover - platform dependent
            load = float(cpus)
        available, fraction = self._memory()
        disk_free = self._disk_free_fraction()
        return ResourceSnapshot(
            cpu_count=cpus,
            load_1m=load,
            memory_available_fraction=fraction,
            memory_available_bytes=available,
            gpu_count=self._gpu_count(),
            on_battery=self._on_battery(),
            disk_free_fraction=disk_free,
        )

    # -- memory -----------------------------------------------------------

    def _memory(self) -> tuple[int, float]:
        meminfo = "/proc/meminfo"
        if os.path.exists(meminfo):
            try:
                values: dict[str, int] = {}
                with open(meminfo, encoding="ascii") as handle:
                    for line in handle:
                        key, _, rest = line.partition(":")
                        parts = rest.split()
                        if parts:
                            values[key] = int(parts[0]) * 1024
                total = values.get("MemTotal", 0)
                available = values.get("MemAvailable", values.get("MemFree", 0))
                if total:
                    return available, available / total
            except (OSError, ValueError):  # pragma: no cover - defensive
                pass
        mac = self._macos_memory()
        if mac is not None:
            return mac
        # Unknown. Assume tight: 25% free. A wrong-but-conservative number
        # throttles a sweep; a wrong-but-optimistic one wedges the desktop.
        return 0, 0.25

    def _macos_memory(self) -> tuple[int, float] | None:
        if not shutil.which("sysctl") or not shutil.which("vm_stat"):
            return None
        try:
            total = int(
                subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=True,
                ).stdout.strip()
            )
            out = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=2, check=True
            ).stdout
        except (OSError, ValueError, subprocess.SubprocessError):  # pragma: no cover
            return None
        page = self._page_size
        free_pages = 0
        for line in out.splitlines():
            if "page size of" in line:
                digits = "".join(ch for ch in line.split("page size of")[1] if ch.isdigit())
                if digits:
                    page = int(digits)
            for label in ("Pages free", "Pages inactive", "Pages speculative"):
                if line.startswith(label):
                    digits = "".join(ch for ch in line if ch.isdigit())
                    if digits:
                        free_pages += int(digits)
        available = free_pages * page
        return (available, available / total) if total else None

    # -- gpu / power / disk ----------------------------------------------

    def _gpu_count(self) -> int:
        if os.environ.get("FIBOKI_GPU_COUNT"):
            try:
                return int(os.environ["FIBOKI_GPU_COUNT"])
            except ValueError:
                return 0
        if shutil.which("nvidia-smi"):
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--list-gpus"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=True,
                ).stdout
                return len([ln for ln in out.splitlines() if ln.strip()])
            except (OSError, subprocess.SubprocessError):  # pragma: no cover
                return 0
        return 0

    def _on_battery(self) -> bool:
        for path in ("/sys/class/power_supply/AC/online", "/sys/class/power_supply/ACAD/online"):
            try:
                with open(path, encoding="ascii") as handle:
                    return handle.read().strip() == "0"
            except OSError:
                continue
        if shutil.which("pmset"):
            try:
                out = subprocess.run(
                    ["pmset", "-g", "batt"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=True,
                ).stdout
                return "Battery Power" in out
            except (OSError, subprocess.SubprocessError):  # pragma: no cover
                return False
        return False

    def _disk_free_fraction(self) -> float:
        try:
            usage = shutil.disk_usage(self.root)
        except OSError:  # pragma: no cover
            return 1.0
        return usage.free / usage.total if usage.total else 1.0


# ---------------------------------------------------------------------------
# Configuration and decisions
# ---------------------------------------------------------------------------


@dataclass
class SchedulerConfig:
    #: Never run more than this many jobs at once, whatever the machine says.
    max_parallel: int = 4
    #: Fraction of cores to leave for the operator. 0.25 on a 8-core Mac keeps
    #: two cores for the UI.
    cpu_reserve: float = 0.25
    #: 1-minute load per core above which nothing new is admitted.
    load_ceiling_per_core: float = 1.5
    #: Below this fraction of available memory, admit nothing.
    memory_floor_fraction: float = 0.15
    #: Below this fraction, admit only CRITICAL work.
    memory_critical_fraction: float = 0.08
    #: Below this disk fraction, refuse everything that writes results.
    disk_floor_fraction: float = 0.05
    #: On battery, cap parallelism here.
    battery_max_parallel: int = 1
    #: Priorities at or above this number are throttled by resource pressure.
    #: CRITICAL work is admitted even on a busy machine.
    throttle_from_priority: int = JobPriority.NORMAL
    #: Seconds to wait before re-checking when admission was refused.
    backoff_seconds: float = 5.0
    #: ``nice`` applied to bulk work. 10 is "yield to anything interactive".
    bulk_nice: int = 10

    def __post_init__(self) -> None:
        if self.max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        if not 0.0 <= self.cpu_reserve < 1.0:
            raise ValueError("cpu_reserve must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    admit: bool
    reason: str
    slots_total: int = 0
    slots_in_use: int = 0
    retry_after_seconds: float = 0.0
    snapshot: ResourceSnapshot | None = None

    def __bool__(self) -> bool:
        return self.admit


@dataclass(order=True, slots=True)
class ScheduledWork:
    """One queued unit.  Ordered by (priority, sequence) -- deterministic."""

    priority: int
    sequence: int
    key: str = field(compare=False)
    payload: Mapping[str, Any] = field(default_factory=dict, compare=False)
    needs_gpu: bool = field(default=False, compare=False)
    estimated_memory_bytes: int = field(default=0, compare=False)


class LocalScheduler:
    """Priority queue plus an admission controller.

    The scheduler does not run anything.  It answers "may I start another job,
    and which one?"  That keeps it a pure function of (queue, resources,
    in-flight count), which is why every branch below is testable with a
    :class:`StaticProbe` and no sleeping.
    """

    def __init__(
        self,
        config: SchedulerConfig | None = None,
        probe: ResourceProbe | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or SchedulerConfig()
        self.probe: ResourceProbe = probe or SystemProbe()
        self.monotonic = monotonic
        self._heap: list[ScheduledWork] = []
        self._counter = itertools.count()
        self._in_flight: dict[str, ScheduledWork] = {}
        self._lock = threading.RLock()
        self.admitted = 0
        self.refusals: dict[str, int] = {}

    # -- queue -----------------------------------------------------------

    def submit(
        self,
        key: str,
        *,
        priority: JobPriority | int = JobPriority.NORMAL,
        payload: Mapping[str, Any] | None = None,
        needs_gpu: bool = False,
        estimated_memory_bytes: int = 0,
    ) -> ScheduledWork:
        work = ScheduledWork(
            priority=int(priority),
            sequence=next(self._counter),
            key=key,
            payload=dict(payload or {}),
            needs_gpu=needs_gpu,
            estimated_memory_bytes=estimated_memory_bytes,
        )
        with self._lock:
            heapq.heappush(self._heap, work)
        return work

    def submit_many(self, items: Iterable[tuple[str, JobPriority]]) -> list[ScheduledWork]:
        return [self.submit(key, priority=priority) for key, priority in items]

    def depth(self) -> int:
        with self._lock:
            return len(self._heap)

    def in_flight(self) -> int:
        with self._lock:
            return len(self._in_flight)

    def peek(self) -> ScheduledWork | None:
        with self._lock:
            return self._heap[0] if self._heap else None

    def queued_keys(self) -> list[str]:
        """Keys in the order they will be dispatched.  For assertions."""
        with self._lock:
            return [w.key for w in sorted(self._heap)]

    # -- capacity --------------------------------------------------------

    def slots(self, snapshot: ResourceSnapshot) -> int:
        """How many concurrent jobs this machine should run right now."""
        cfg = self.config
        usable = int(snapshot.cpu_count * (1.0 - cfg.cpu_reserve))
        allowed = max(1, min(cfg.max_parallel, usable))
        if snapshot.on_battery:
            allowed = min(allowed, cfg.battery_max_parallel)
        if snapshot.memory_available_fraction < cfg.memory_floor_fraction:
            allowed = 0
        elif snapshot.memory_available_fraction < cfg.memory_floor_fraction * 2:
            # Approaching the floor: halve rather than wait for the cliff.
            allowed = max(1, allowed // 2)
        if snapshot.load_per_core > cfg.load_ceiling_per_core:
            allowed = 0
        return allowed

    def admit(self, work: ScheduledWork | None = None) -> AdmissionDecision:
        """May another job start now?  Pure, apart from the probe call."""
        snapshot = self.probe()
        cfg = self.config
        with self._lock:
            in_use = len(self._in_flight)
        candidate = work or self.peek()
        priority = candidate.priority if candidate is not None else int(JobPriority.NORMAL)
        exempt = priority < cfg.throttle_from_priority

        if candidate is None:
            return self._refuse("queue empty", snapshot, 0, in_use, retry=0.0)

        if candidate.needs_gpu and snapshot.gpu_count <= 0:
            return self._refuse(
                "job needs a GPU and none is present", snapshot, 0, in_use, retry=0.0
            )

        if snapshot.disk_free_fraction < cfg.disk_floor_fraction:
            return self._refuse(
                f"disk {snapshot.disk_free_fraction:.0%} free, below the "
                f"{cfg.disk_floor_fraction:.0%} floor; results would be unwritable",
                snapshot,
                0,
                in_use,
            )

        if snapshot.memory_available_fraction < cfg.memory_critical_fraction and not exempt:
            return self._refuse(
                f"memory {snapshot.memory_available_fraction:.0%} free is critical "
                f"(<{cfg.memory_critical_fraction:.0%}); only CRITICAL work runs",
                snapshot,
                0,
                in_use,
            )

        total = self.config.max_parallel if exempt else self.slots(snapshot)
        if exempt:
            total = max(total, in_use + 1)

        if in_use >= total:
            reason = (
                f"{in_use} job(s) in flight, machine supports {total} right now "
                f"({snapshot.describe()})"
            )
            return self._refuse(reason, snapshot, total, in_use)

        return AdmissionDecision(
            admit=True,
            reason="capacity available",
            slots_total=total,
            slots_in_use=in_use,
            snapshot=snapshot,
        )

    def _refuse(
        self,
        reason: str,
        snapshot: ResourceSnapshot,
        total: int,
        in_use: int,
        retry: float | None = None,
    ) -> AdmissionDecision:
        self.refusals[reason.split(",")[0].split("(")[0].strip()] = (
            self.refusals.get(reason.split(",")[0].split("(")[0].strip(), 0) + 1
        )
        return AdmissionDecision(
            admit=False,
            reason=reason,
            slots_total=total,
            slots_in_use=in_use,
            retry_after_seconds=self.config.backoff_seconds if retry is None else retry,
            snapshot=snapshot,
        )

    # -- dispatch --------------------------------------------------------

    def next_work(self) -> tuple[ScheduledWork | None, AdmissionDecision]:
        """Pop the highest-priority job IF the machine can take it."""
        decision = self.admit()
        if not decision.admit:
            return None, decision
        with self._lock:
            if not self._heap:
                return None, self._refuse(
                    "queue emptied between admit and pop", decision.snapshot or self.probe(), 0, 0
                )
            work = heapq.heappop(self._heap)
            self._in_flight[work.key] = work
        self.admitted += 1
        _log.debug(
            "admitted",
            extra={"key": work.key, "priority": work.priority, "slots": decision.slots_total},
        )
        return work, decision

    def finish(self, key: str) -> None:
        with self._lock:
            self._in_flight.pop(key, None)

    def requeue(self, work: ScheduledWork) -> None:
        with self._lock:
            self._in_flight.pop(work.key, None)
            heapq.heappush(self._heap, work)

    def wait_for_capacity(self, stop: threading.Event, *, timeout: float | None = None) -> bool:
        """Block until admission succeeds, the queue empties, or ``stop`` is set."""
        deadline = None if timeout is None else self.monotonic() + timeout
        while not stop.is_set():
            decision = self.admit()
            if decision.admit or self.depth() == 0:
                return decision.admit
            if deadline is not None and self.monotonic() >= deadline:
                return False
            stop.wait(decision.retry_after_seconds or self.config.backoff_seconds)
        return False

    def status(self) -> dict[str, Any]:
        snapshot = self.probe()
        return {
            "queued": self.depth(),
            "in_flight": self.in_flight(),
            "slots_now": self.slots(snapshot),
            "max_parallel": self.config.max_parallel,
            "admitted_total": self.admitted,
            "resources": snapshot.describe(),
            "refusals": dict(self.refusals),
        }


def apply_nice(level: int | None = None) -> int | None:
    """Lower this process's scheduling priority.  Returns the new nice, or None.

    On macOS and Linux ``os.nice`` works and is enough to stop a sweep starving
    the UI.  It cannot be undone without privileges, so a worker calls it once
    at startup when it knows it is bulk work.  ``ionice`` is deliberately NOT
    attempted: it does not exist on macOS and pretending otherwise would put a
    silent no-op in the logs.
    """
    if level is None:
        return None
    try:
        return os.nice(level)
    except (OSError, AttributeError):  # pragma: no cover - platform dependent
        _log.warning("could not apply nice=%s on this platform", level)
        return None


def throttled_sequence(
    items: Sequence[Any],
    scheduler: LocalScheduler,
    stop: threading.Event,
) -> Iterable[Any]:
    """Yield ``items`` no faster than the machine can take them.

    Used by the sweep runner so a long cell list is paced rather than blasted.
    """
    for item in items:
        if stop.is_set():
            return
        while not stop.is_set():
            decision = scheduler.admit(
                ScheduledWork(int(JobPriority.BULK), 0, key=str(item))
            )
            if decision.admit:
                break
            stop.wait(decision.retry_after_seconds or scheduler.config.backoff_seconds)
        if stop.is_set():
            return
        yield item
