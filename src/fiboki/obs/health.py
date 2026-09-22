"""Health checks that actually check something.

The distinction this module exists to enforce
---------------------------------------------
V1's ``/health`` returned ``{"status": "ok"}`` from a handler that did no work.
It was therefore a test of whether the web server could serve a route, which
was never the thing in doubt.  The worker died inside the same process and the
endpoint stayed green, which is how a dead worker survived a weekend.

So every check here performs I/O against the thing it claims to check, and a
check that cannot perform its I/O reports ``FAIL``, never ``OK``.  A check that
throws is caught and reported as ``FAIL`` with the exception text -- an
exception during a health check is a health result, not a missing one.

Three-state, on purpose
-----------------------
``OK`` / ``DEGRADED`` / ``FAIL``.  Two states forces a judgement call into the
check ("is 45-minute-old data a failure?") and the answer is usually "no, but
somebody should know".  ``DEGRADED`` is the honest answer, and the aggregate
is the worst state present, so a degraded component cannot be averaged away.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "BrokerReachableCheck",
    "DataFreshnessCheck",
    "DatabaseCheck",
    "HealthCheck",
    "HealthReport",
    "HealthResult",
    "HealthStatus",
    "MigrationCheck",
    "QueueDepthCheck",
    "WorkerHeartbeatCheck",
    "run_checks",
]


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAIL = "fail"

    @property
    def rank(self) -> int:
        return {"ok": 0, "degraded": 1, "fail": 2}[self.value]

    @staticmethod
    def worst(statuses: Iterable[HealthStatus]) -> HealthStatus:
        worst = HealthStatus.OK
        for status in statuses:
            if status.rank > worst.rank:
                worst = status
        return worst


@dataclass(frozen=True, slots=True)
class HealthResult:
    name: str
    status: HealthStatus
    detail: str = ""
    #: What to DO about it. A health check that says "fail" and nothing else
    #: costs the operator the same diagnosis every time.
    remedy: str = ""
    duration_ms: float = 0.0
    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is HealthStatus.OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "remedy": self.remedy,
            "duration_ms": round(self.duration_ms, 2),
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    at: datetime
    results: tuple[HealthResult, ...]

    @property
    def status(self) -> HealthStatus:
        return HealthStatus.worst(r.status for r in self.results)

    @property
    def healthy(self) -> bool:
        return self.status is HealthStatus.OK

    def failures(self) -> tuple[HealthResult, ...]:
        return tuple(r for r in self.results if r.status is HealthStatus.FAIL)

    def degraded(self) -> tuple[HealthResult, ...]:
        return tuple(r for r in self.results if r.status is HealthStatus.DEGRADED)

    def get(self, name: str) -> HealthResult | None:
        for result in self.results:
            if result.name == name:
                return result
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "at": self.at.astimezone(UTC).isoformat(),
            "checks": [r.to_dict() for r in self.results],
        }

    #: The HTTP status an endpoint should return. 200 for ok/degraded so a
    #: load balancer does not remove a degraded-but-serving instance; 503 for
    #: fail. The BODY always carries the truth.
    @property
    def http_status(self) -> int:
        return 503 if self.status is HealthStatus.FAIL else 200


@runtime_checkable
class HealthCheck(Protocol):
    name: str

    def __call__(self) -> HealthResult: ...


def run_checks(
    checks: Sequence[HealthCheck], *, now: Callable[[], datetime] | None = None
) -> HealthReport:
    """Run every check, never letting one failure hide the others."""
    results: list[HealthResult] = []
    for check in checks:
        name = getattr(check, "name", getattr(check, "__name__", "check"))
        start = time.perf_counter()
        try:
            result = check()
        except Exception as exc:
            results.append(
                HealthResult(
                    name=name,
                    status=HealthStatus.FAIL,
                    detail=f"check raised {type(exc).__name__}: {exc}",
                    remedy="The check itself is broken. Fix the check before trusting the report.",
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            )
            continue
        if result.duration_ms == 0.0:
            result = HealthResult(
                name=result.name,
                status=result.status,
                detail=result.detail,
                remedy=result.remedy,
                duration_ms=(time.perf_counter() - start) * 1000,
                data=result.data,
            )
        results.append(result)
    stamp = (now or (lambda: datetime.now(tz=UTC)))()
    return HealthReport(at=stamp, results=tuple(results))


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@dataclass
class DatabaseCheck:
    """Executes ``SELECT 1``.  Anything less is not a database check."""

    probe: Callable[[], Any]
    name: str = "database"
    slow_ms: float = 500.0

    def __call__(self) -> HealthResult:
        start = time.perf_counter()
        try:
            self.probe()
        except Exception as exc:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL,
                detail=f"database unreachable: {type(exc).__name__}: {exc}",
                remedy=(
                    "Check FIBOKI_DATABASE_URL and that the file/server exists. "
                    "`fiboki system doctor` prints the resolved URL."
                ),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > self.slow_ms:
            return HealthResult(
                name=self.name,
                status=HealthStatus.DEGRADED,
                detail=f"database answered in {elapsed:.0f}ms (slow)",
                remedy="Check for a long-running transaction or a saturated disk.",
                duration_ms=elapsed,
            )
        return HealthResult(
            name=self.name,
            status=HealthStatus.OK,
            detail=f"reachable in {elapsed:.0f}ms",
            duration_ms=elapsed,
        )


@dataclass
class MigrationCheck:
    """Compares the applied revision against the expected head.

    A schema one revision behind the code is the failure mode that produces
    "column does not exist" at 03:00 rather than at deploy time, so it is FAIL
    and not DEGRADED.
    """

    current: Callable[[], str]
    expected: Callable[[], str]
    name: str = "migration"

    def __call__(self) -> HealthResult:
        try:
            applied = (self.current() or "").strip()
            head = (self.expected() or "").strip()
        except Exception as exc:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL,
                detail=f"could not read migration state: {type(exc).__name__}: {exc}",
                remedy="Run `fiboki data migrate-v1 --check` / `alembic current` by hand.",
            )
        if not head:
            return HealthResult(
                name=self.name,
                status=HealthStatus.DEGRADED,
                detail="no migration head declared; cannot verify the schema",
                remedy="Declare the expected head so this check can mean something.",
            )
        if applied != head:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL,
                detail=f"schema is at {applied or '<none>'}, code expects {head}",
                remedy="Run `alembic upgrade head` BEFORE starting workers.",
                data={"applied": applied, "expected": head},
            )
        return HealthResult(
            name=self.name,
            status=HealthStatus.OK,
            detail=f"at {head}",
            data={"applied": applied, "expected": head},
        )


@dataclass
class WorkerHeartbeatCheck:
    """Freshness of each worker heartbeat.

    Unlike V1, this is not the ONLY thing that evaluates staleness -- the
    watchdog does it on a timer.  This check exists so a human asking
    "is it alive?" gets the same answer the watchdog is acting on.
    """

    heartbeats: Callable[[], Iterable[Any]]
    stale_after_seconds: float = 120.0
    down_after_seconds: float = 300.0
    expected_workers: Sequence[str] = ()
    name: str = "worker_heartbeat"

    def __call__(self) -> HealthResult:
        views = list(self.heartbeats())
        seen = {getattr(v, "worker_id", "") for v in views}
        missing = [w for w in self.expected_workers if w not in seen]
        stale: list[str] = []
        down: list[str] = []
        detail_rows: dict[str, Any] = {}
        for view in views:
            worker = getattr(view, "worker_id", "?")
            age = float(getattr(view, "age_seconds", 0.0))
            detail_rows[worker] = round(age, 1)
            if age >= self.down_after_seconds:
                down.append(worker)
            elif age >= self.stale_after_seconds:
                stale.append(worker)

        if down or missing:
            names = ", ".join(sorted([*down, *missing])) or "?"
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL,
                detail=f"worker(s) down or never started: {names}",
                remedy=(
                    "Start the worker PROCESS (`fiboki worker run ...` or the "
                    "launchd/systemd unit). It is not a thread inside the API; "
                    "a green API says nothing about it."
                ),
                data={"ages": detail_rows, "down": down, "missing": missing},
            )
        if stale:
            return HealthResult(
                name=self.name,
                status=HealthStatus.DEGRADED,
                detail=f"stale heartbeat: {', '.join(sorted(stale))}",
                remedy="Check the worker log; it may be stuck inside a long cycle.",
                data={"ages": detail_rows, "stale": stale},
            )
        if not views:
            return HealthResult(
                name=self.name,
                status=HealthStatus.DEGRADED,
                detail="no worker heartbeats recorded and none expected",
                remedy="If a worker should be running, add it to expected_workers.",
            )
        return HealthResult(
            name=self.name,
            status=HealthStatus.OK,
            detail=f"{len(views)} worker(s) fresh",
            data={"ages": detail_rows},
        )


@dataclass
class DataFreshnessCheck:
    """Age of the newest bar, per instrument.

    ``ages`` returns ``{instrument: seconds}``.  An instrument whose market is
    closed is expected to be stale, so the caller supplies per-instrument
    budgets rather than one global number; a missing budget falls back to
    ``default_budget_seconds``.
    """

    ages: Callable[[], Mapping[str, float]]
    budgets: Mapping[str, float] = field(default_factory=dict)
    default_budget_seconds: float = 3600.0
    fail_multiple: float = 4.0
    name: str = "data_freshness"

    def __call__(self) -> HealthResult:
        ages = dict(self.ages())
        if not ages:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL,
                detail="no instruments reported a newest bar at all",
                remedy="Run `fiboki data ingest` -- the store looks empty.",
            )
        stale: list[str] = []
        very_stale: list[str] = []
        for instrument, age in sorted(ages.items()):
            budget = float(self.budgets.get(instrument, self.default_budget_seconds))
            if age >= budget * self.fail_multiple:
                very_stale.append(f"{instrument}={age:.0f}s>{budget * self.fail_multiple:.0f}s")
            elif age >= budget:
                stale.append(f"{instrument}={age:.0f}s>{budget:.0f}s")
        if very_stale:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL,
                detail="data badly stale: " + ", ".join(very_stale),
                remedy="Ingestion has stopped. Check the provider credentials and the worker.",
                data={"ages": ages},
            )
        if stale:
            return HealthResult(
                name=self.name,
                status=HealthStatus.DEGRADED,
                detail="data stale: " + ", ".join(stale),
                remedy="Expected outside session hours; investigate if the market is open.",
                data={"ages": ages},
            )
        return HealthResult(
            name=self.name,
            status=HealthStatus.OK,
            detail=f"{len(ages)} instrument(s) within budget",
            data={"ages": ages},
        )


@dataclass
class BrokerReachableCheck:
    """Calls the adapter's own health probe.  Never assumes reachable."""

    probe: Callable[[], Any]
    name: str = "broker"
    required: bool = True
    slow_ms: float = 2000.0

    def __call__(self) -> HealthResult:
        start = time.perf_counter()
        try:
            outcome = self.probe()
        except Exception as exc:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL if self.required else HealthStatus.DEGRADED,
                detail=f"broker unreachable: {type(exc).__name__}: {exc}",
                remedy=(
                    "Paper mode does not need a broker. If this is demo/live, check "
                    "the credentials and `fiboki broker status`."
                ),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        elapsed = (time.perf_counter() - start) * 1000
        healthy = True if outcome is None else bool(outcome)
        if not healthy:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL if self.required else HealthStatus.DEGRADED,
                detail="broker probe returned unhealthy",
                remedy="Check `fiboki broker status` for the venue's own message.",
                duration_ms=elapsed,
            )
        if elapsed > self.slow_ms:
            return HealthResult(
                name=self.name,
                status=HealthStatus.DEGRADED,
                detail=f"broker answered in {elapsed:.0f}ms (slow)",
                remedy="Latency this high will widen realised slippage. Watch the spread metric.",
                duration_ms=elapsed,
            )
        return HealthResult(
            name=self.name,
            status=HealthStatus.OK,
            detail=f"reachable in {elapsed:.0f}ms",
            duration_ms=elapsed,
        )


@dataclass
class QueueDepthCheck:
    """A queue that only grows means the worker is gone or wedged."""

    depths: Callable[[], Mapping[str, int]]
    warn_at: int = 100
    fail_at: int = 1000
    name: str = "queue_depth"

    def __call__(self) -> HealthResult:
        depths = dict(self.depths())
        over_fail = {q: d for q, d in depths.items() if d >= self.fail_at}
        over_warn = {q: d for q, d in depths.items() if self.warn_at <= d < self.fail_at}
        if over_fail:
            return HealthResult(
                name=self.name,
                status=HealthStatus.FAIL,
                detail="queue backed up: "
                + ", ".join(f"{q}={d}" for q, d in sorted(over_fail.items())),
                remedy="No worker is draining this queue. Check `fiboki worker status`.",
                data={"depths": depths},
            )
        if over_warn:
            return HealthResult(
                name=self.name,
                status=HealthStatus.DEGRADED,
                detail="queue growing: "
                + ", ".join(f"{q}={d}" for q, d in sorted(over_warn.items())),
                remedy="Throughput is below arrival rate. Raise parallelism or reduce the sweep.",
                data={"depths": depths},
            )
        return HealthResult(
            name=self.name,
            status=HealthStatus.OK,
            detail=f"{sum(depths.values())} job(s) queued across {len(depths)} queue(s)",
            data={"depths": depths},
        )
