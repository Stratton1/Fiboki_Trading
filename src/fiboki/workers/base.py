"""A worker that is a PROCESS, with a single-writer lease and a real heartbeat.

The five V1 failures this module closes, in order
-------------------------------------------------
1. **The worker was a daemon thread inside the API process.**  When it died --
   an unhandled exception in the loop body, a segfault in a native dependency,
   a thread killed by an OOM reaper -- the API kept serving and kept returning
   ``{"status": "ok"}``.  The system was, from every angle anybody looked at,
   healthy.  A worker here is ``python -m fiboki.workers.<kind>``: its own pid,
   its own exit code, its own supervisor.  When it dies, something notices,
   because there is a process-shaped hole where it used to be.

2. **No Sentry, no error reporting of any kind, in the worker.**  Exceptions
   were caught by a bare ``except: pass`` in the loop so the thread would not
   die, which converted every bug into silence.  Here :meth:`Worker.run_cycle`
   is allowed to raise; the loop catches it, RECORDS it on the heartbeat,
   counts it, alerts on it, hands it to the injected error reporter, and only
   then continues.  Nothing is swallowed.

3. **Heartbeat freshness was computed when a human loaded a page.**  See
   :class:`fiboki.obs.alerts.HeartbeatWatchdog`.  This module's contribution is
   that the heartbeat is WRITTEN on every cycle including a failed one, with
   the error text on it, so there is something true for the watchdog to read.
   A heartbeat written only on success is worse than none: it goes stale for
   both "dead" and "failing", and the operator learns to ignore it.

4. **Two workers could run with the same hardcoded ``worker_id``.**  They
   overwrote each other's heartbeat -- so the row looked fresh -- while both
   processed the same queue and placed duplicate orders.  Two defences here,
   and both are needed:

   * the id is ``{hostname}:{pid}``, so two processes CANNOT collide; and
   * a single-writer :class:`WorkerLease` in the database, so the second
     process exits loudly instead of racing.

   The id alone would only have made the duplication visible.  The lease is
   what makes it impossible.

5. **Shutdown was a ``kill -9``.**  A worker terminated mid-job left the job
   marked RUNNING forever, and a lease that never expired.  Here SIGTERM and
   SIGINT set an event; the loop finishes the current cycle if it can and
   abandons it safely if it cannot, releases the lease, writes a final
   heartbeat, and exits 0.

Lease semantics, stated precisely
---------------------------------
The lease is a leased ROW, not a mutex.  It is held for ``lease_ttl_seconds``
and renewed every cycle.  A holder that stops renewing loses it after the TTL,
which is what makes a hard-killed worker recoverable without human action.

A lease is NOT a distributed-systems guarantee.  It is safe under the failure
mode it is built for -- a second worker started by mistake on a machine whose
clock is roughly right -- and it is NOT safe against arbitrary pauses (a laptop
suspended mid-cycle can wake holding a lease another process has taken).  That
is why the fencing token exists: it increments on every acquisition, so a
resumed zombie's writes carry a stale token and can be rejected.  On PostgreSQL
a session-level advisory lock is taken in addition, which IS mutual exclusion
for as long as the connection lives.

Do not read "lease" as "exactly-once".  Read it as "the second worker exits
instead of silently doubling your position sizes".
"""
from __future__ import annotations

import abc
import contextlib
import os
import signal
import socket
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from fiboki.obs import metrics as _metrics
from fiboki.obs.alerts import AlertDispatcher, AlertEvent, HeartbeatView
from fiboki.obs.logging import bind, get_logger, new_correlation_id

__all__ = [
    "EXIT_FATAL",
    "EXIT_LEASE_HELD",
    "EXIT_OK",
    "CycleOutcome",
    "CycleResult",
    "ErrorReporter",
    "Heartbeat",
    "LeaseLost",
    "LeaseNotAcquired",
    "LoggingErrorReporter",
    "Worker",
    "WorkerConfig",
    "WorkerLease",
    "WorkerState",
    "WorkerStore",
    "log_once",
    "reset_log_once",
    "worker_id",
]

_log = get_logger("fiboki.workers.base")

#: Exit codes a supervisor can act on. A supervisor must NOT restart a worker
#: that exited because another holds the lease -- that is a restart loop.
EXIT_OK = 0
EXIT_FATAL = 1
EXIT_LEASE_HELD = 75  # EX_TEMPFAIL, conventionally "do not hammer"

_METADATA = MetaData()

#: One row per RESOURCE ("research", "live"), not per worker. That is what
#: makes "only one writer" expressible at all: a row per worker could never
#: be contended, which is exactly the shape V1's heartbeat table had.
WORKER_LEASE = Table(
    "worker_lease",
    _METADATA,
    Column("lease_name", String(128), primary_key=True),
    Column("holder", String(256), nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    # Increments on every acquisition. A resumed zombie's stale token is
    # detectable, so its writes can be rejected rather than merged.
    Column("fence", Integer, nullable=False, default=0),
    Column("host", String(256), default=""),
    Column("pid", Integer, default=0),
)

WORKER_HEARTBEAT = Table(
    "worker_heartbeat",
    _METADATA,
    Column("worker_id", String(256), primary_key=True),
    Column("kind", String(64), nullable=False, default=""),
    Column("lease_name", String(128), default=""),
    Column("beat_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("status", String(32), default=""),
    Column("cycle", Integer, default=0),
    Column("cycles_ok", Integer, default=0),
    Column("cycles_failed", Integer, default=0),
    Column("jobs_done", Integer, default=0),
    Column("last_error", Text, default=""),
    Column("last_error_at", DateTime(timezone=True), nullable=True),
    Column("fence", Integer, default=0),
    Column("host", String(256), default=""),
    Column("pid", Integer, default=0),
    Column("detail", Text, default=""),
)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def worker_id(kind: str = "", *, host: str | None = None, pid: int | None = None) -> str:
    """``{kind}@{hostname}:{pid}`` -- distinct per PROCESS, by construction.

    V1 used a module-level constant ``WORKER_ID = "worker-1"``.  Two processes
    then wrote the same heartbeat row, each refreshing it, so the row was
    always fresh and the duplication was invisible.  A pid cannot collide with
    a live pid on the same host, so this id cannot.
    """
    hostname = host if host is not None else socket.gethostname()
    process = pid if pid is not None else os.getpid()
    prefix = f"{kind}@" if kind else ""
    return f"{prefix}{hostname}:{process}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class WorkerStore:
    """Owns the engine and the two operational tables.

    SQLite is configured with WAL and a busy timeout because the whole point of
    the lease test is two processes hitting the same file at once; the default
    ``database is locked`` behaviour would make the lease look like it worked
    when it had actually just errored.
    """

    def __init__(self, engine: Engine, *, create: bool = True) -> None:
        self.engine = engine
        if create:
            _METADATA.create_all(engine, tables=[WORKER_LEASE, WORKER_HEARTBEAT])

    @classmethod
    def from_url(cls, url: str, *, create: bool = True, echo: bool = False) -> WorkerStore:
        kwargs: dict[str, Any] = {"echo": echo, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"timeout": 30.0}
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite") and ":memory:" not in url:
            with engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                conn.exec_driver_sql("PRAGMA busy_timeout=30000")
                conn.exec_driver_sql("PRAGMA synchronous=FULL")
        return cls(engine, create=create)

    @classmethod
    def sqlite_at(cls, path: str | os.PathLike[str], **kwargs: Any) -> WorkerStore:
        return cls.from_url(f"sqlite:///{os.fspath(path)}", **kwargs)

    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> WorkerStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    # -- heartbeat reads (used by the watchdog and health checks) ---------

    def heartbeats(self, *, now: datetime | None = None) -> list[HeartbeatView]:
        stamp = now or _utcnow()
        with self.engine.connect() as conn:
            rows = conn.execute(select(WORKER_HEARTBEAT)).mappings().all()
        views: list[HeartbeatView] = []
        for row in rows:
            beat = _as_utc(row["beat_at"]) or stamp
            views.append(
                HeartbeatView(
                    worker_id=row["worker_id"],
                    kind=row["kind"] or "",
                    age_seconds=max(0.0, (stamp - beat).total_seconds()),
                    status=row["status"] or "",
                    last_error=row["last_error"] or "",
                )
            )
        return views

    def heartbeat_rows(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(WORKER_HEARTBEAT)).mappings().all()]

    def lease_rows(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(WORKER_LEASE)).mappings().all()]

    def forget_worker(self, worker: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                delete(WORKER_HEARTBEAT).where(WORKER_HEARTBEAT.c.worker_id == worker)
            )


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


class LeaseNotAcquired(RuntimeError):
    """Another live holder has the lease.  The correct response is to exit."""

    def __init__(self, lease_name: str, holder: str, expires_at: datetime) -> None:
        super().__init__(
            f"lease {lease_name!r} is held by {holder!r} until {expires_at.isoformat()}. "
            "REFUSING TO START. Two workers on one queue is how V1 placed duplicate "
            "orders that nothing could see. If that holder is genuinely dead, wait for "
            "the lease to expire or run `fiboki worker status --release`."
        )
        self.lease_name = lease_name
        self.holder = holder
        self.expires_at = expires_at


class LeaseLost(RuntimeError):
    """The lease was taken by somebody else while we held it.  Stop NOW."""


@dataclass(frozen=True, slots=True)
class LeaseState:
    lease_name: str
    holder: str
    acquired_at: datetime
    expires_at: datetime
    fence: int
    host: str = ""
    pid: int = 0

    def live_at(self, now: datetime) -> bool:
        return self.expires_at > now


class WorkerLease:
    """Single-writer lease over a named resource.

    Acquisition is one conditional UPDATE inside a transaction:

    ``UPDATE worker_lease SET holder=:me, fence=fence+1, expires_at=:exp
      WHERE lease_name=:n AND (expires_at <= :now OR holder=:me)``

    Whichever transaction commits first flips ``holder``; the other's WHERE no
    longer matches, its rowcount is 0, and it raises.  On PostgreSQL a session
    advisory lock is taken first, which makes the window genuinely zero rather
    than merely small.
    """

    def __init__(
        self,
        store: WorkerStore,
        lease_name: str,
        holder: str,
        *,
        ttl_seconds: float = 60.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("lease ttl must be positive")
        self.store = store
        self.lease_name = lease_name
        self.holder = holder
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.fence: int = 0
        self._held = False
        self._advisory_conn: Any = None

    @property
    def held(self) -> bool:
        return self._held

    @property
    def _is_postgres(self) -> bool:
        return self.store.engine.dialect.name.startswith("postgres")

    def _advisory_key(self) -> int:
        digest = uuid.uuid5(uuid.NAMESPACE_URL, f"fiboki-lease:{self.lease_name}").int
        # Postgres advisory locks take a signed 64-bit key.
        return (digest % (2**63)) - (2**62)

    def _take_advisory(self) -> bool:
        if not self._is_postgres:
            return True
        conn = self.store.engine.connect()
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": self._advisory_key()}
        ).scalar()
        if not got:
            conn.close()
            return False
        self._advisory_conn = conn
        return True

    def _drop_advisory(self) -> None:
        if self._advisory_conn is None:
            return
        try:
            self._advisory_conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": self._advisory_key()}
            )
        finally:
            self._advisory_conn.close()
            self._advisory_conn = None

    def current(self) -> LeaseState | None:
        with self.store.engine.connect() as conn:
            row = (
                conn.execute(
                    select(WORKER_LEASE).where(WORKER_LEASE.c.lease_name == self.lease_name)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return LeaseState(
            lease_name=row["lease_name"],
            holder=row["holder"],
            acquired_at=_as_utc(row["acquired_at"]) or _utcnow(),
            expires_at=_as_utc(row["expires_at"]) or _utcnow(),
            fence=int(row["fence"] or 0),
            host=row["host"] or "",
            pid=int(row["pid"] or 0),
        )

    def acquire(self) -> LeaseState:
        """Take the lease or raise :class:`LeaseNotAcquired`.  Never blocks."""
        if not self._take_advisory():
            existing = self.current()
            raise LeaseNotAcquired(
                self.lease_name,
                existing.holder if existing else "<advisory-lock-holder>",
                existing.expires_at if existing else self.clock(),
            )

        now = self.clock()
        expires = now + timedelta(seconds=self.ttl_seconds)
        try:
            with self.store.engine.begin() as conn:
                result = conn.execute(
                    update(WORKER_LEASE)
                    .where(
                        WORKER_LEASE.c.lease_name == self.lease_name,
                        # Free if expired, or already ours (a restart reclaims).
                        (WORKER_LEASE.c.expires_at <= now)
                        | (WORKER_LEASE.c.holder == self.holder),
                    )
                    .values(
                        holder=self.holder,
                        acquired_at=now,
                        expires_at=expires,
                        fence=WORKER_LEASE.c.fence + 1,
                        host=socket.gethostname(),
                        pid=os.getpid(),
                    )
                )
                if result.rowcount == 0:
                    # Either no row exists yet, or somebody live holds it.
                    try:
                        conn.execute(
                            insert(WORKER_LEASE).values(
                                lease_name=self.lease_name,
                                holder=self.holder,
                                acquired_at=now,
                                expires_at=expires,
                                fence=1,
                                host=socket.gethostname(),
                                pid=os.getpid(),
                            )
                        )
                    except IntegrityError as exc:
                        raise _held_by_other(self, now) from exc
        except LeaseNotAcquired:
            self._drop_advisory()
            raise
        except Exception:
            self._drop_advisory()
            raise

        state = self.current()
        if state is None or state.holder != self.holder:
            self._drop_advisory()
            raise _held_by_other(self, now)
        self.fence = state.fence
        self._held = True
        return state

    def renew(self) -> LeaseState:
        """Extend the lease.  Raises :class:`LeaseLost` if it is no longer ours."""
        now = self.clock()
        expires = now + timedelta(seconds=self.ttl_seconds)
        with self.store.engine.begin() as conn:
            result = conn.execute(
                update(WORKER_LEASE)
                .where(
                    WORKER_LEASE.c.lease_name == self.lease_name,
                    WORKER_LEASE.c.holder == self.holder,
                    WORKER_LEASE.c.fence == self.fence,
                )
                .values(expires_at=expires)
            )
            if result.rowcount == 0:
                self._held = False
                current = (
                    conn.execute(
                        select(WORKER_LEASE).where(
                            WORKER_LEASE.c.lease_name == self.lease_name
                        )
                    )
                    .mappings()
                    .first()
                )
                other = current["holder"] if current else "<deleted>"
                fence = int(current["fence"]) if current else -1
                raise LeaseLost(
                    f"lease {self.lease_name!r} is no longer ours: holder={other!r} "
                    f"fence={fence} (we hold fence={self.fence}). Stopping rather than "
                    "continuing to write as a second writer."
                )
        state = self.current()
        assert state is not None
        return state

    def release(self) -> None:
        """Give the lease back so a restart does not have to wait out the TTL."""
        try:
            with self.store.engine.begin() as conn:
                conn.execute(
                    update(WORKER_LEASE)
                    .where(
                        WORKER_LEASE.c.lease_name == self.lease_name,
                        WORKER_LEASE.c.holder == self.holder,
                    )
                    .values(expires_at=self.clock())
                )
        finally:
            self._held = False
            self._drop_advisory()

    def __enter__(self) -> LeaseState:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def _held_by_other(lease: WorkerLease, now: datetime) -> LeaseNotAcquired:
    state = lease.current()
    return LeaseNotAcquired(
        lease.lease_name,
        state.holder if state else "<unknown>",
        state.expires_at if state else now,
    )


def force_release(store: WorkerStore, lease_name: str) -> bool:
    """Expire a lease regardless of holder.  An OPERATOR action, never automatic.

    Exposed for ``fiboki worker status --release`` after a machine has been
    hard-powered-off.  It is deliberately not something a worker can call on
    itself, because "I could not get the lease so I took it" is the bug.
    """
    with store.engine.begin() as conn:
        result = conn.execute(
            update(WORKER_LEASE)
            .where(WORKER_LEASE.c.lease_name == lease_name)
            .values(expires_at=_utcnow())
        )
    return bool(result.rowcount)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class WorkerState(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    FAILING = "failing"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


class Heartbeat:
    """Upserts one row per worker.  Written on EVERY cycle, success or not."""

    def __init__(
        self,
        store: WorkerStore,
        worker: str,
        kind: str,
        *,
        lease_name: str = "",
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.store = store
        self.worker = worker
        self.kind = kind
        self.lease_name = lease_name
        self.clock = clock
        self.started_at = clock()
        self.cycle = 0
        self.cycles_ok = 0
        self.cycles_failed = 0
        self.jobs_done = 0
        self.last_error = ""
        self.last_error_at: datetime | None = None

    def write(
        self,
        status: WorkerState | str,
        *,
        error: str = "",
        fence: int = 0,
        detail: str = "",
        jobs_done: int | None = None,
    ) -> datetime:
        now = self.clock()
        if error:
            self.last_error = error[:4000]
            self.last_error_at = now
        if jobs_done is not None:
            self.jobs_done = jobs_done
        label = status.value if isinstance(status, WorkerState) else str(status)
        values = {
            "kind": self.kind,
            "lease_name": self.lease_name,
            "beat_at": now,
            "started_at": self.started_at,
            "status": label,
            "cycle": self.cycle,
            "cycles_ok": self.cycles_ok,
            "cycles_failed": self.cycles_failed,
            "jobs_done": self.jobs_done,
            # The error is SET, never cleared by a later success: the point of
            # recording it is that somebody reads it after the fact.
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "fence": fence,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "detail": detail[:4000],
        }
        with self.store.engine.begin() as conn:
            result = conn.execute(
                update(WORKER_HEARTBEAT)
                .where(WORKER_HEARTBEAT.c.worker_id == self.worker)
                .values(**values)
            )
            if result.rowcount == 0:
                conn.execute(insert(WORKER_HEARTBEAT).values(worker_id=self.worker, **values))
        _metrics.record_worker_liveness(self.worker, self.kind, age_seconds=0.0, fresh=True)
        return now

    def age_seconds(self) -> float:
        with self.store.engine.connect() as conn:
            row = (
                conn.execute(
                    select(WORKER_HEARTBEAT.c.beat_at).where(
                        WORKER_HEARTBEAT.c.worker_id == self.worker
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return float("inf")
        beat = _as_utc(row["beat_at"]) or self.clock()
        return max(0.0, (self.clock() - beat).total_seconds())


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------


class ErrorReporter(Protocol):
    """The Sentry-shaped seam V1 had no equivalent of.

    ``sentry_sdk.capture_exception`` satisfies this.  So does a lambda that
    appends to a list, which is what the tests use.  The point is that the
    worker HAS one: V1's worker had no error reporting at all, so a crash left
    no trace anywhere except a log line nobody was shipping.
    """

    def __call__(self, exc: BaseException, context: Mapping[str, Any]) -> None: ...


class LoggingErrorReporter:
    """Default reporter: full traceback into the structured log."""

    def __init__(self, logger: Any = None) -> None:
        self.log = logger or _log
        self.captured: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, exc: BaseException, context: Mapping[str, Any]) -> None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.captured.append((f"{type(exc).__name__}: {exc}", dict(context)))
        self.log.error(
            "worker cycle raised",
            extra={"error": f"{type(exc).__name__}: {exc}", "traceback": tb, **dict(context)},
        )


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


class CycleOutcome(str, Enum):
    IDLE = "idle"
    WORKED = "worked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CycleResult:
    """What one cycle did.  ``jobs`` drives the poll interval: a cycle that did
    work polls again immediately, an idle one backs off."""

    outcome: CycleOutcome = CycleOutcome.IDLE
    jobs: int = 0
    detail: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def idle(cls, detail: str = "") -> CycleResult:
        return cls(CycleOutcome.IDLE, 0, detail)

    @classmethod
    def worked(cls, jobs: int = 1, detail: str = "", **data: Any) -> CycleResult:
        return cls(CycleOutcome.WORKED, jobs, detail, data)


@dataclass
class WorkerConfig:
    kind: str
    lease_name: str = ""
    #: Seconds between cycles when the last cycle found nothing to do.
    idle_sleep_seconds: float = 2.0
    #: Seconds between cycles when the last cycle did work.
    busy_sleep_seconds: float = 0.0
    #: Lease TTL. Must comfortably exceed the longest expected cycle, or the
    #: worker will lose its own lease mid-job.
    lease_ttl_seconds: float = 60.0
    #: How long a graceful shutdown waits for the current cycle before the
    #: worker abandons it and exits anyway.
    shutdown_grace_seconds: float = 30.0
    #: Stop after this many cycles. 0 = forever. Used by tests and by
    #: `fiboki worker run --once`.
    max_cycles: int = 0
    #: Consecutive failures before the worker gives up and exits non-zero, so a
    #: supervisor restarts it cleanly rather than letting it spin on a poisoned
    #: state forever. 0 = never give up.
    max_consecutive_failures: int = 20
    #: Exponential backoff applied after a failed cycle, capped.
    failure_backoff_seconds: float = 1.0
    failure_backoff_max_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("a worker needs a kind")
        self.lease_name = self.lease_name or self.kind


_FEED_FAILURE_TYPES = frozenset(
    {
        "ProviderError",
        "ConnectError",
        "CandleHttpError",
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
        "NetworkError",
    }
)


def cycle_failure_event(exc: BaseException) -> AlertEvent:
    """A feed or broker outage is not a strategy degradation.

    ``STRATEGY_DEGRADED`` means the strategy's own score or lifecycle moved.
    A candle fetch that cannot resolve the practice host is the broker path
    being unreachable, and that is ``BROKER_UNHEALTHY``.
    """
    current: BaseException | None = exc
    for _ in range(6):
        if current is None:
            break
        if type(current).__name__ in _FEED_FAILURE_TYPES:
            return AlertEvent.BROKER_UNHEALTHY
        current = current.__cause__ or current.__context__
    return AlertEvent.STRATEGY_DEGRADED


class Worker(abc.ABC):
    """Base class.  Subclasses implement :meth:`run_cycle` and nothing else.

    The lifecycle, exactly:

    ``setup() -> acquire lease -> resume() -> [renew, run_cycle, heartbeat]* ->
    teardown() -> release lease -> final heartbeat``

    ``resume()`` is where crash-safe resumption happens: it is called once,
    after the lease is held and before the first cycle, which is the only
    moment at which it is safe to re-claim work a dead predecessor left
    half-done.
    """

    def __init__(
        self,
        config: WorkerConfig,
        store: WorkerStore,
        *,
        dispatcher: AlertDispatcher | None = None,
        error_reporter: ErrorReporter | None = None,
        worker: str | None = None,
        clock: Callable[[], datetime] = _utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.store = store
        self.dispatcher = dispatcher
        self.error_reporter: ErrorReporter = error_reporter or LoggingErrorReporter()
        self.worker_id = worker or worker_id(config.kind)
        self.clock = clock
        self.monotonic = monotonic

        self.lease = WorkerLease(
            store,
            config.lease_name,
            self.worker_id,
            ttl_seconds=config.lease_ttl_seconds,
            clock=clock,
        )
        self.heartbeat = Heartbeat(
            store, self.worker_id, config.kind, lease_name=config.lease_name, clock=clock
        )
        self._stop = threading.Event()
        self._stop_reason = ""
        self._in_cycle = threading.Event()
        self._abandon_timer: threading.Timer | None = None
        self._installed_handlers: dict[int, Any] = {}
        self.consecutive_failures = 0
        self.exit_code = EXIT_OK
        self.last_cycle: CycleResult | None = None

    # -- hooks -----------------------------------------------------------

    @abc.abstractmethod
    def run_cycle(self) -> CycleResult:
        """Do one unit of work.  MAY RAISE -- the loop records and continues."""

    def setup(self) -> None:  # noqa: B027 - an optional hook, not an abstract one
        """Called once before the lease is taken."""

    def resume(self) -> None:  # noqa: B027 - an optional hook, not an abstract one
        """Called once after the lease is taken, before the first cycle.

        This is where a worker re-claims work its predecessor abandoned.  It is
        safe here and nowhere else, because holding the lease is the only proof
        that the predecessor is not still running.
        """

    def teardown(self) -> None:  # noqa: B027 - an optional hook, not an abstract one
        """Called once on the way out, before the lease is released."""

    def on_abandon(self) -> None:  # noqa: B027 - an optional hook, not an abstract one
        """Called when a cycle overran the shutdown grace and is abandoned.

        The default does nothing.  A worker with in-flight external state
        overrides it to mark that state UNKNOWN rather than leaving it RUNNING.
        """

    # -- signals ---------------------------------------------------------

    def install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT set the stop event.  They do NOT kill the cycle.

        A signal handler that raises inside whatever line the worker happened
        to be on is how V1 left a job marked RUNNING with no process behind it.
        Setting an event means the interruption happens at a point the worker
        chose.
        """
        if threading.current_thread() is not threading.main_thread():
            _log.warning("signal handlers not installed: not on the main thread")
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._installed_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):  # pragma: no cover - restricted env
                _log.warning("could not install handler for signal %s", sig)

    def restore_signal_handlers(self) -> None:
        for sig, handler in self._installed_handlers.items():
            with contextlib.suppress(ValueError, OSError):  # pragma: no cover
                signal.signal(sig, handler)
        self._installed_handlers.clear()

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        name = signal.Signals(signum).name
        if self._stop.is_set():
            # A second signal means the operator is insisting. Honour it.
            _log.warning("second %s received; abandoning the current cycle now", name)
            self._abandon("second signal")
            raise SystemExit(EXIT_OK)
        self.request_stop(name)
        _log.info("%s received; finishing the current cycle then stopping", name)

    def request_stop(self, reason: str = "requested") -> None:
        self._stop_reason = reason
        self._stop.set()
        self._arm_abandon_timer()

    def _arm_abandon_timer(self) -> None:
        """Bound how long a graceful shutdown waits for the current cycle.

        Without this, "graceful" means "hangs forever if the cycle is stuck on
        a socket read", and the operator reaches for ``kill -9`` -- which is
        the ungraceful shutdown we were trying to avoid, now with a
        half-written job record behind it.  The bound turns an unbounded wait
        into an explicit, RECORDED abandonment.
        """
        grace = self.config.shutdown_grace_seconds
        if grace <= 0 or self._abandon_timer is not None:
            return
        timer = threading.Timer(grace, self._abandon_if_still_working, args=("grace expired",))
        timer.daemon = True
        self._abandon_timer = timer
        timer.start()

    def _abandon_if_still_working(self, reason: str) -> None:
        if not self._in_cycle.is_set():
            return
        self._abandon(reason)
        # The cycle is wedged in something we cannot interrupt from here
        # (a blocking read, a C extension). The record is written; leaving the
        # process alive to be SIGKILLed later would add nothing.
        os._exit(EXIT_OK)

    def _abandon(self, reason: str) -> None:
        _log.critical(
            "abandoning in-flight cycle", extra={"reason": reason, "worker_id": self.worker_id}
        )
        try:
            self.on_abandon()
        except Exception:  # pragma: no cover - defensive
            _log.exception("on_abandon() raised while abandoning")
        try:
            self.heartbeat.write(
                WorkerState.STOPPING,
                error=f"cycle abandoned: {reason}",
                fence=self.lease.fence,
                detail="ABANDONED mid-cycle; any in-flight external state is UNKNOWN",
            )
            self.lease.release()
        except Exception:  # pragma: no cover - the DB may be the thing that hung
            _log.exception("could not record the abandonment")

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def pulse(self, detail: str = "") -> None:
        """Renew the lease and write a WORKING heartbeat, from inside a cycle.

        The loop only renews and beats BETWEEN cycles. A cycle that runs
        several jobs, or one long job, must call this (or run under
        :class:`fiboki.workers.research_worker.HeartbeatPulse`, which calls it
        on a timer) or its lease expires mid-cycle and a second supervised
        worker can take it (audit F, P1-10). Raises :class:`LeaseLost` if the
        lease is no longer ours, which the caller must not swallow.
        """
        self.lease.renew()
        self.heartbeat.write(WorkerState.WORKING, fence=self.lease.fence, detail=detail)

    # -- the loop --------------------------------------------------------

    def run(self, *, install_signals: bool = True) -> int:
        """Run until stopped.  Returns the process exit code."""
        correlation = new_correlation_id("worker")
        with bind(correlation_id=correlation, worker_id=self.worker_id, kind=self.config.kind):
            if install_signals:
                self.install_signal_handlers()
            try:
                return self._run_guarded()
            finally:
                if self._abandon_timer is not None:
                    self._abandon_timer.cancel()
                    self._abandon_timer = None
                self.restore_signal_handlers()

    def _run_guarded(self) -> int:
        self.setup()
        self.heartbeat.write(WorkerState.STARTING)

        try:
            state = self.lease.acquire()
        except LeaseNotAcquired as exc:
            # LOUD, and a distinct exit code so a supervisor does not restart-loop.
            _log.critical(
                "refusing to start: lease held by another worker",
                extra={
                    "lease": exc.lease_name,
                    "holder": exc.holder,
                    "expires_at": exc.expires_at.isoformat(),
                },
            )
            print(f"FATAL: {exc}", file=sys.stderr, flush=True)
            if self.dispatcher is not None:
                # One incident per (lease, holder): launchd retries a refused
                # start every ThrottleInterval, and each retry has a new pid in
                # its source, so without this key every retry was a new
                # CRITICAL incident (four for one stale worker, 2026-09-29).
                self.dispatcher.fire(
                    AlertEvent.WORKER_LEASE_CONTENDED,
                    f"a second {self.config.kind} worker tried to start while "
                    f"{exc.holder} holds the {exc.lease_name} lease",
                    source=self.worker_id,
                    dedupe_key=f"lease_contended:{exc.lease_name}:{exc.holder}",
                    holder=exc.holder,
                    lease=exc.lease_name,
                )
            self.heartbeat.write(
                WorkerState.STOPPED, error=f"lease held by {exc.holder}", detail="not started"
            )
            self.exit_code = EXIT_LEASE_HELD
            return EXIT_LEASE_HELD

        _log.info(
            "lease acquired",
            extra={"lease": state.lease_name, "fence": state.fence, "holder": state.holder},
        )
        try:
            self.resume()
        except Exception as exc:
            self._report(exc, phase="resume")
            self.heartbeat.write(
                WorkerState.CRASHED,
                error=f"{type(exc).__name__}: {exc}",
                fence=self.lease.fence,
                detail="resume() failed",
            )
            self.lease.release()
            self.exit_code = EXIT_FATAL
            return EXIT_FATAL

        self.heartbeat.write(WorkerState.IDLE, fence=self.lease.fence)
        code = self._loop()

        try:
            self.teardown()
        except Exception as exc:  # teardown failing must not hide the exit code
            self._report(exc, phase="teardown")
        finally:
            self.lease.release()
            self.heartbeat.write(
                WorkerState.STOPPED,
                fence=self.lease.fence,
                detail=f"stopped: {self._stop_reason or 'loop finished'}",
            )
        self.exit_code = code
        return code

    def _loop(self) -> int:
        cycles = 0
        while not self._stop.is_set():
            if self.config.max_cycles and cycles >= self.config.max_cycles:
                self._stop_reason = "max_cycles"
                break
            cycles += 1
            self.heartbeat.cycle = cycles

            # Renew BEFORE working, so a cycle that runs long does not
            # discover mid-flight that it lost the lease an hour ago.
            try:
                self.lease.renew()
            except LeaseLost as exc:
                _log.critical("lease lost; stopping", extra={"error": str(exc)})
                self.heartbeat.write(
                    WorkerState.STOPPING, error=str(exc), fence=self.lease.fence
                )
                if self.dispatcher is not None:
                    self.dispatcher.fire(
                        AlertEvent.WORKER_LEASE_CONTENDED,
                        str(exc),
                        source=self.worker_id,
                        dedupe_key=f"lease_lost:{self.config.kind}:{self.worker_id}",
                    )
                return EXIT_FATAL

            started = self.monotonic()
            self._in_cycle.set()
            try:
                result = self.run_cycle()
                if not isinstance(result, CycleResult):
                    result = CycleResult.idle(str(result))
                self.last_cycle = result
                self.consecutive_failures = 0
                self.heartbeat.cycles_ok += 1
                self.heartbeat.jobs_done += result.jobs
                _metrics.WORKER_CYCLES.inc(worker=self.worker_id, outcome=result.outcome.value)
                # HEARTBEAT ON SUCCESS
                self.heartbeat.write(
                    WorkerState.WORKING if result.jobs else WorkerState.IDLE,
                    fence=self.lease.fence,
                    detail=result.detail,
                )
                sleep_for = (
                    self.config.busy_sleep_seconds
                    if result.outcome is CycleOutcome.WORKED
                    else self.config.idle_sleep_seconds
                )
            except Exception as exc:
                # HEARTBEAT ON FAILURE. This is the line V1 did not have.
                # Without it a failing worker and a dead worker are the same
                # observation, and the operator learns to distrust both.
                self.consecutive_failures += 1
                self.heartbeat.cycles_failed += 1
                error = f"{type(exc).__name__}: {exc}"
                _metrics.WORKER_CYCLES.inc(worker=self.worker_id, outcome="failed")
                self.last_cycle = CycleResult(CycleOutcome.FAILED, 0, error)
                self.heartbeat.write(
                    WorkerState.FAILING,
                    error=error,
                    fence=self.lease.fence,
                    detail=f"cycle {cycles} failed ({self.consecutive_failures} in a row)",
                )
                self._report(exc, phase="cycle", cycle=cycles)
                if self.dispatcher is not None and self.consecutive_failures in (1, 5, 20):
                    event = (
                        AlertEvent.JOB_DEAD_LETTERED
                        if self.consecutive_failures >= 20
                        else cycle_failure_event(exc)
                    )
                    self.dispatcher.fire(
                        event,
                        f"{self.config.kind} worker cycle failed "
                        f"{self.consecutive_failures}x consecutively: {error}",
                        source=self.worker_id,
                        # Kind, not pid. A launchd restart storm is one outage;
                        # a new worker id must not open a new incident.
                        dedupe_key=f"cycle_fail:{self.config.kind}:{event.value}",
                        cycle=cycles,
                    )
                if (
                    self.config.max_consecutive_failures
                    and self.consecutive_failures >= self.config.max_consecutive_failures
                ):
                    _log.critical(
                        "giving up after %s consecutive failures",
                        self.consecutive_failures,
                        extra={"error": error},
                    )
                    self._stop_reason = "max_consecutive_failures"
                    return EXIT_FATAL
                sleep_for = min(
                    self.config.failure_backoff_seconds * (2 ** (self.consecutive_failures - 1)),
                    self.config.failure_backoff_max_seconds,
                )
            finally:
                self._in_cycle.clear()
                _metrics.JOB_DURATION.observe(
                    self.monotonic() - started, queue=self.config.lease_name, job_type="cycle"
                )

            if self._stop.is_set():
                break
            if sleep_for > 0:
                # Interruptible: a SIGTERM during the idle sleep takes effect
                # at once rather than after the full interval.
                self._stop.wait(sleep_for)
        return EXIT_OK

    # -- helpers ---------------------------------------------------------

    def _report(self, exc: BaseException, **context: Any) -> None:
        payload = {"worker_id": self.worker_id, "kind": self.config.kind, **context}
        try:
            self.error_reporter(exc, payload)
        except Exception:  # pragma: no cover - a reporter that itself fails
            _log.exception("error reporter raised; original error was %r", exc)

    def status(self) -> dict[str, Any]:
        lease = self.lease.current()
        return {
            "worker_id": self.worker_id,
            "kind": self.config.kind,
            "lease": self.config.lease_name,
            "lease_holder": lease.holder if lease else None,
            "fence": self.lease.fence,
            "cycles": self.heartbeat.cycle,
            "cycles_ok": self.heartbeat.cycles_ok,
            "cycles_failed": self.heartbeat.cycles_failed,
            "jobs_done": self.heartbeat.jobs_done,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.heartbeat.last_error,
            "stopping": self.stopping,
        }


def watchdog_views(store: WorkerStore, *, now: datetime | None = None) -> list[HeartbeatView]:
    """Adapter so :class:`HeartbeatWatchdog` can read the heartbeat table."""
    return store.heartbeats(now=now)


def expected_workers_from_env(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """``FIBOKI_EXPECTED_WORKERS=research@host:1,live@host:2``.

    A watchdog with no expectation cannot alert on a worker that never started,
    which is the failure mode after a reboot.
    """
    environ = env if env is not None else os.environ
    raw = environ.get("FIBOKI_EXPECTED_WORKERS", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


_ONCE_LOCK = threading.Lock()
_ONCE_SEEN: dict[str, float] = {}


def log_once(key: str, ttl: float, *, clock: Callable[[], float] = time.monotonic) -> bool:
    """True the first time ``key`` is seen, then False until ``ttl`` seconds pass.

    For a LEVEL condition that is true on every cycle -- a market closed all
    weekend, an instrument whose candle keeps arriving late -- where logging it
    every cycle would bury the one line that matters. Usage::

        if log_once(f"market_closed:{symbol}", 3600):
            _log.info("market closed", extra={"instrument": symbol})

    It suppresses LOG LINES only. It is not an alert de-duplicator (the
    :class:`~fiboki.obs.alerts.AlertDispatcher` has its own time-windowed
    ``dedupe_key``) and it must never gate a check, a metric or an alert: a
    condition that is still true is still recorded, it is just not re-narrated.

    Pattern after freqtrade ``mixins/logging_mixin.py`` ``log_once`` (GPL-3.0,
    not copied). A ``ttl`` of zero or less always returns True.
    """
    if ttl <= 0:
        return True
    now = clock()
    with _ONCE_LOCK:
        last = _ONCE_SEEN.get(key)
        if last is not None and (now - last) < ttl:
            return False
        _ONCE_SEEN[key] = now
        return True


def reset_log_once(prefix: str = "") -> None:
    """Forget remembered keys (all, or those starting with ``prefix``). For tests."""
    with _ONCE_LOCK:
        for key in [k for k in _ONCE_SEEN if k.startswith(prefix)]:
            del _ONCE_SEEN[key]


def drain_sleep(stop: threading.Event, seconds: float) -> None:
    """Interruptible sleep helper for subclasses."""
    if seconds > 0:
        stop.wait(seconds)


def summarise_leases(store: WorkerStore) -> Sequence[Mapping[str, Any]]:
    now = _utcnow()
    rows = store.lease_rows()
    out = []
    for row in rows:
        expires = _as_utc(row["expires_at"]) or now
        out.append(
            {
                "lease": row["lease_name"],
                "holder": row["holder"],
                "fence": row["fence"],
                "expires_in_seconds": round((expires - now).total_seconds(), 1),
                "live": expires > now,
                "host": row["host"],
                "pid": row["pid"],
            }
        )
    return out


def iter_stale(views: Iterable[HeartbeatView], *, older_than: float) -> list[HeartbeatView]:
    return [v for v in views if v.age_seconds >= older_than]
