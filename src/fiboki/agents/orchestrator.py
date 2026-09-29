"""A STATELESS orchestrator: named queues, jobs, idempotency, retries.

Why stateless
-------------
The obvious design for an autonomous research system is a long-running
conductor that holds the plan, remembers what it has already tried, and decides
what to do next.  That design is the single largest source of audit drift in
these systems: the thing that decided is a process that no longer exists, its
reasoning lived in a context window that was never written down, and a re-run
produces something different because the accumulated state differed.

So there is no conductor here.  There are named work queues.  A job is a **pure
function of its recorded inputs**: its type, its payload and the deterministic
services injected into its handler.  Nothing a job needs is carried in the
orchestrator's head, which means any job can be replayed from its record alone
and must produce the same answer.

What the orchestrator does hold is a durable job LEDGER -- submitted specs and
their outcomes.  That is records, not state: deleting the process and
reconstructing it from the ledger changes nothing.

The ledger is SQLite (audit F, P1-8). It used to be two Python dicts, so a
restart -- including a launchd restart after a crash -- lost every queued job
and every idempotency key, and a re-fired schedule re-submitted work that had
already run. ``Orchestrator(path=<state_dir>/jobs.sqlite)`` (or
:meth:`Orchestrator.durable`) keeps specs, status, attempts, a result record
and the idempotency key (``UNIQUE``) in a WAL-mode file with a busy timeout;
``Orchestrator()`` keeps the same tables in memory. Claims are atomic and
fenced by the worker lease: see :class:`Orchestrator`.

Guarantees
----------
* **Idempotent job keys.**  Submitting the same key twice returns the first
  record.  A re-triggered schedule, a retried agent call and a duplicated event
  all collapse to one execution.
* **Retries with backoff.**  Exponential, bounded by ``max_attempts``, then the
  job is dead-lettered rather than retried forever.
* **Scheduled and event-triggered jobs**, both of which route through the same
  idempotent submit path.
* **Narrow permissions.**  A job declares the capabilities its submitter needed;
  :class:`JobType` contains no execution job, so there is no job an agent can
  submit that reaches an order.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fiboki.agents.capabilities import Capability


class JobType(str, Enum):
    """Every job a research agent may cause to happen.

    Note the absence, again: there is no ORDER, no EXECUTION, no PROMOTION,
    no LIMIT_CHANGE.  A queue that cannot express an execution job cannot be
    talked into running one.
    """

    BACKTEST = "backtest"
    VALIDATION = "validation"
    WALKFORWARD = "walkforward"
    ABLATION = "ablation"
    SENSITIVITY = "sensitivity"
    DATA_QUALITY_SCAN = "data_quality_scan"
    REGIME_SCAN = "regime_scan"
    LIBRARIAN_FILING = "librarian_filing"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class OrchestratorError(RuntimeError):
    pass


class UnknownJobType(OrchestratorError):
    pass


# ---------------------------------------------------------------------------
# Clocks: injected so a test never waits and a replay is reproducible
# ---------------------------------------------------------------------------


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class ManualClock:
    """A clock the caller advances.  Backoff is then testable in microseconds."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 5, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def canonical_key(job_type: JobType, payload: Mapping[str, Any]) -> str:
    """Derive an idempotency key from the job's recorded inputs."""
    blob = json.dumps(
        {"job_type": job_type.value, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{job_type.value}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class JobSpec:
    """The complete, recorded input to one job.  Nothing else is consulted."""

    job_type: JobType
    queue: str
    payload: dict[str, Any]
    idempotency_key: str = ""
    submitted_by: str = ""
    role: str = ""
    required_capabilities: frozenset[Capability] = frozenset()
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    backoff_factor: float = 2.0
    max_backoff_seconds: float = 300.0
    scheduled_for: datetime | None = None
    trigger: str = "manual"
    audit_action_id: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.queue:
            raise ValueError("a job needs a named queue")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0 or self.backoff_factor < 1.0:
            raise ValueError("backoff must be non-negative with a factor >= 1")
        object.__setattr__(
            self,
            "idempotency_key",
            self.idempotency_key or canonical_key(self.job_type, self.payload),
        )

    def backoff_for(self, attempt: int) -> float:
        """Delay before attempt ``attempt`` (1-based), capped."""
        delay = self.backoff_seconds * (self.backoff_factor ** max(0, attempt - 1))
        return min(delay, self.max_backoff_seconds)

    def fingerprint(self) -> dict[str, Any]:
        return {
            "job_type": self.job_type.value,
            "queue": self.queue,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "submitted_by": self.submitted_by,
            "role": self.role,
            "required_capabilities": sorted(c.value for c in self.required_capabilities),
            "trigger": self.trigger,
        }


@dataclass(frozen=True, slots=True)
class Attempt:
    number: int
    started_at: datetime
    finished_at: datetime
    status: JobStatus
    error: str = ""


@dataclass(frozen=True, slots=True)
class JobRecord:
    """The durable record of a job.  Replaced wholesale on each transition."""

    job_id: str
    spec: JobSpec
    status: JobStatus = JobStatus.PENDING
    attempts: tuple[Attempt, ...] = ()
    result: dict[str, Any] | None = None
    error: str = ""
    submitted_at: datetime | None = None
    next_attempt_at: datetime | None = None
    deduplicated: bool = False

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def terminal(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.DEAD_LETTER)


@dataclass(frozen=True, slots=True)
class JobContext:
    """Everything a handler is given.  There is no back-reference to the orchestrator.

    A handler cannot enqueue follow-up work, read another job's state, or reach
    the agent that submitted it.  It sees its own recorded inputs and the
    deterministic services that were injected when the handler was registered.
    """

    job_id: str
    job_type: JobType
    payload: Mapping[str, Any]
    attempt: int
    now: datetime
    services: Mapping[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any:
        if name not in self.services:
            raise OrchestratorError(
                f"job {self.job_type.value} needs the {name!r} service, which was not "
                "injected when its handler was registered"
            )
        return self.services[name]

    def require(self, *keys: str) -> tuple[Any, ...]:
        missing = [k for k in keys if k not in self.payload]
        if missing:
            raise ValueError(f"{self.job_type.value}: payload is missing {missing}")
        return tuple(self.payload[k] for k in keys)


JobHandler = Callable[[JobContext], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class _Registration:
    handler: JobHandler
    services: Mapping[str, Any]
    required_capabilities: frozenset[Capability]


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """A recurring submission.  Each bucket submits once, idempotently."""

    name: str
    job_type: JobType
    queue: str
    payload: dict[str, Any]
    interval_seconds: float
    submitted_by: str = "scheduler"
    role: str = "scheduler"

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

    def bucket(self, now: datetime) -> int:
        return int(now.timestamp() // self.interval_seconds)

    def key_for(self, now: datetime) -> str:
        return f"sched:{self.name}:{self.bucket(now)}"


@dataclass(frozen=True, slots=True)
class EventTrigger:
    """An event-to-job mapping.  ``build`` is a pure function of the event."""

    event: str
    job_type: JobType
    queue: str
    build: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    name: str = ""
    submitted_by: str = "event"
    role: str = "event"


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------


class FencedOut(OrchestratorError):
    """A claim or a completion carried a lease fence older than the ledger has seen.

    The fencing token is the worker lease's ``fence`` (``workers/base.py``),
    which increments on every acquisition. The ledger keeps the highest fence
    it has been shown; a process presenting a LOWER one is a zombie that lost
    its lease while paused (a laptop lid, a debugger, a stop-the-world GC) and
    must not claim work or overwrite the outcome of work a newer holder owns.
    """


#: Schema of the durable ledger. One row per job; ``idempotency_key`` is
#: UNIQUE so two processes submitting the same key cannot both insert.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
    job_id          TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    queue           TEXT NOT NULL,
    job_type        TEXT NOT NULL,
    spec_json       TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempts_json   TEXT NOT NULL DEFAULT '[]',
    result_json     TEXT,
    error           TEXT NOT NULL DEFAULT '',
    submitted_at    TEXT NOT NULL,
    next_attempt_at TEXT,
    queue_seq       INTEGER NOT NULL,
    claimed_by      TEXT NOT NULL DEFAULT '',
    claim_fence     INTEGER,
    claimed_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_job_queue ON job (queue, status, queue_seq);
CREATE TABLE IF NOT EXISTS ledger_meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

_RUNNABLE = (JobStatus.PENDING.value, JobStatus.FAILED.value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _spec_to_json(spec: JobSpec) -> str:
    """The spec, exactly as recorded. A payload that is not JSON is refused.

    Refused rather than stringified: a job is a pure function of its RECORDED
    inputs, so the payload a handler sees must be the one that survives a
    restart. Stringifying ``datetime(2026, 1, 5)`` into ``"2026-01-05 00:00"``
    would hand the handler one type before a restart and another after it.
    """
    body = {
        "job_type": spec.job_type.value,
        "queue": spec.queue,
        "payload": spec.payload,
        "idempotency_key": spec.idempotency_key,
        "submitted_by": spec.submitted_by,
        "role": spec.role,
        "required_capabilities": sorted(c.value for c in spec.required_capabilities),
        "max_attempts": spec.max_attempts,
        "backoff_seconds": spec.backoff_seconds,
        "backoff_factor": spec.backoff_factor,
        "max_backoff_seconds": spec.max_backoff_seconds,
        "scheduled_for": _iso(spec.scheduled_for),
        "trigger": spec.trigger,
        "audit_action_id": spec.audit_action_id,
        "created_at": _iso(spec.created_at),
    }
    try:
        return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OrchestratorError(
            f"job payload for {spec.job_type.value} is not JSON-serialisable ({exc}). "
            "The ledger records the exact inputs a job runs on; convert the value to "
            "a JSON type at submission rather than letting it change type on restart."
        ) from exc


def _spec_from_json(blob: str) -> JobSpec:
    body = json.loads(blob)
    return JobSpec(
        job_type=JobType(body["job_type"]),
        queue=body["queue"],
        payload=dict(body["payload"]),
        idempotency_key=body["idempotency_key"],
        submitted_by=body.get("submitted_by", ""),
        role=body.get("role", ""),
        required_capabilities=frozenset(
            Capability(c) for c in body.get("required_capabilities", ())
        ),
        max_attempts=int(body["max_attempts"]),
        backoff_seconds=float(body["backoff_seconds"]),
        backoff_factor=float(body["backoff_factor"]),
        max_backoff_seconds=float(body["max_backoff_seconds"]),
        scheduled_for=_from_iso(body.get("scheduled_for")),
        trigger=body.get("trigger", "manual"),
        audit_action_id=body.get("audit_action_id"),
        created_at=_from_iso(body.get("created_at")),
    )


def _attempts_to_json(attempts: Sequence[Attempt]) -> str:
    return json.dumps(
        [
            {
                "number": a.number,
                "started_at": _iso(a.started_at),
                "finished_at": _iso(a.finished_at),
                "status": a.status.value,
                "error": a.error,
            }
            for a in attempts
        ],
        separators=(",", ":"),
    )


def _attempts_from_json(blob: str) -> tuple[Attempt, ...]:
    return tuple(
        Attempt(
            number=int(a["number"]),
            started_at=_from_iso(a["started_at"]) or datetime.min.replace(tzinfo=UTC),
            finished_at=_from_iso(a["finished_at"]) or datetime.min.replace(tzinfo=UTC),
            status=JobStatus(a["status"]),
            error=a.get("error", ""),
        )
        for a in json.loads(blob or "[]")
    )


def _result_to_json(output: Mapping[str, Any]) -> str:
    """A handler's result, as recorded. Non-JSON values are recorded as text.

    Unlike a payload, a result is an OUTPUT: nothing re-runs from it, and the
    handlers return identifiers (``backtest_id``, ``report_id``) that the
    durable stores behind them resolve. Recording an exotic value by its
    ``str`` is therefore a reference, not a change of meaning.
    """
    return json.dumps(dict(output), sort_keys=True, default=str, separators=(",", ":"))


class Orchestrator:
    """Named queues plus a DURABLE job ledger.  Holds no reasoning state.

    The ledger is SQLite. ``Orchestrator(path=...)`` opens (or creates) a file,
    in WAL mode with a busy timeout, so jobs, their attempts, their results
    and every idempotency key survive a process restart and are visible to
    another process opening the same file. ``Orchestrator()`` with no path
    keeps the same ledger in an in-memory database: identical semantics,
    nothing persisted, which is what unit tests and the agent workflows'
    private synchronous queue want.

    What is NOT in the ledger: handlers, schedules and event triggers. Those
    are code, registered by the platform at wiring time, and a restarted
    process registers them again. A job whose type has no handler in THIS
    process stays queued rather than being failed by a process that cannot
    run it.

    Claiming is atomic. :meth:`run_next` selects and marks a job RUNNING
    inside one ``BEGIN IMMEDIATE`` transaction, so two workers on one file
    can never both run it. When :meth:`bind_lease` has been called the claim
    also carries the worker lease's fencing token: the ledger remembers the
    highest fence it has seen and refuses a claim, or a completion, from a
    holder presenting a lower one (:class:`FencedOut`).
    """

    def __init__(
        self,
        clock: Clock | None = None,
        *,
        path: str | Path | None = None,
    ) -> None:
        self.clock: Clock = clock or SystemClock()
        self.path: Path | None = None if path is None else Path(path)
        self._handlers: dict[JobType, _Registration] = {}
        self._scheduled: dict[str, ScheduledJob] = {}
        self._triggers: list[EventTrigger] = []
        self._holder: str = ""
        self._fence: Callable[[], int] | None = None
        self._lock = threading.RLock()
        if self.path is None:
            target = ":memory:"
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            target = str(self.path)
        # isolation_level=None: autocommit, so every transaction below is an
        # explicit BEGIN IMMEDIATE ... COMMIT and nothing is left implicitly open.
        self._conn = sqlite3.connect(
            target, timeout=30.0, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        cursor = self._conn.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000")
            if self.path is not None:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=FULL")
            cursor.executescript(_SCHEMA)
        finally:
            cursor.close()

    # -- lifecycle --------------------------------------------------------

    @classmethod
    def durable(cls, state_dir: str | Path, *, clock: Clock | None = None) -> Orchestrator:
        """The production ledger: ``<state_dir>/jobs.sqlite``."""
        return cls(clock, path=Path(state_dir) / "jobs.sqlite")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def bind_lease(self, holder: str, fence: Callable[[], int]) -> None:
        """Fence every claim and completion with ``fence()``, the worker lease token.

        Called by the worker once it holds its lease (``ResearchWorker.resume``).
        ``holder`` is recorded on every claim so an abandoned RUNNING job can be
        attributed to the process that took it.
        """
        self._holder = holder
        self._fence = fence

    @contextlib.contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One ``BEGIN IMMEDIATE`` transaction: the write lock is taken up front."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _read(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def _next_seq(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM ledger_meta WHERE key='queue_seq'").fetchone()
        value = (int(row["value"]) if row else 0) + 1
        conn.execute(
            "INSERT INTO ledger_meta (key, value) VALUES ('queue_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (value,),
        )
        return value

    def _check_fence(self, conn: sqlite3.Connection, *, advance: bool) -> int | None:
        """Refuse a fence below the high-water mark; optionally raise the mark."""
        if self._fence is None:
            return None
        fence = int(self._fence())
        row = conn.execute("SELECT value FROM ledger_meta WHERE key='fence_hwm'").fetchone()
        hwm = int(row["value"]) if row else 0
        if fence < hwm:
            raise FencedOut(
                f"lease fence {fence} held by {self._holder!r} is older than the "
                f"ledger's high-water mark {hwm}: another worker has taken the lease "
                "since. This process must not claim or record jobs."
            )
        if advance and fence > hwm:
            conn.execute(
                "INSERT INTO ledger_meta (key, value) VALUES ('fence_hwm', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (fence,),
            )
        return fence

    @staticmethod
    def _record_from_row(row: sqlite3.Row, *, deduplicated: bool = False) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            spec=_spec_from_json(row["spec_json"]),
            status=JobStatus(row["status"]),
            attempts=_attempts_from_json(row["attempts_json"]),
            result=None if row["result_json"] is None else json.loads(row["result_json"]),
            error=row["error"] or "",
            submitted_at=_from_iso(row["submitted_at"]),
            next_attempt_at=_from_iso(row["next_attempt_at"]),
            deduplicated=deduplicated,
        )

    # -- registration -----------------------------------------------------

    def register_handler(
        self,
        job_type: JobType,
        handler: JobHandler,
        *,
        services: Mapping[str, Any] | None = None,
        required_capabilities: Iterable[Capability] = (),
    ) -> None:
        """Register the ONE deterministic function that executes this job type.

        Handlers are registered by the platform at wiring time, never by an
        agent.  The signature is checked so a handler cannot quietly take a
        second argument that would let something else be threaded in.
        """
        signature = inspect.signature(handler)
        positional = [
            p
            for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            and p.default is inspect.Parameter.empty
        ]
        if len(positional) != 1:
            raise OrchestratorError(
                f"a job handler takes exactly one argument (JobContext); "
                f"{getattr(handler, '__name__', handler)!r} takes {len(positional)}"
            )
        self._handlers[job_type] = _Registration(
            handler=handler,
            services=dict(services or {}),
            required_capabilities=frozenset(required_capabilities),
        )

    def handled_types(self) -> tuple[JobType, ...]:
        return tuple(sorted(self._handlers, key=lambda t: t.value))

    # -- submission -------------------------------------------------------

    def submit(self, spec: JobSpec) -> JobRecord:
        """Enqueue a job.  Idempotent on ``spec.idempotency_key``, across processes."""
        existing = self.by_key(spec.idempotency_key)
        if existing is not None:
            return replace(existing, deduplicated=True)
        if spec.job_type not in self._handlers:
            raise UnknownJobType(
                f"no handler registered for {spec.job_type.value}; a job nobody can "
                "execute must not be accepted into a queue"
            )
        now = self.clock.now()
        recorded = replace(spec, created_at=spec.created_at or now)
        spec_json = _spec_to_json(recorded)
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        try:
            with self._tx() as conn:
                conn.execute(
                    "INSERT INTO job (job_id, idempotency_key, queue, job_type, spec_json, "
                    "status, submitted_at, next_attempt_at, queue_seq) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        recorded.idempotency_key,
                        recorded.queue,
                        recorded.job_type.value,
                        spec_json,
                        JobStatus.PENDING.value,
                        _iso(now),
                        _iso(recorded.scheduled_for or now),
                        self._next_seq(conn),
                    ),
                )
        except sqlite3.IntegrityError:
            # Another process inserted the same key between our read and our
            # write. The UNIQUE constraint is what makes that a dedup and not
            # a second job.
            winner = self.by_key(spec.idempotency_key)
            if winner is None:  # pragma: no cover - the constraint says otherwise
                raise
            return replace(winner, deduplicated=True)
        return self.get(job_id)

    # -- execution --------------------------------------------------------

    def run_next(self, queue: str) -> JobRecord | None:
        """Claim and execute the next runnable job on ``queue``.  None if idle.

        The claim is one transaction: select the oldest runnable row whose
        backoff has elapsed and whose type this process can handle, check the
        lease fence, mark it RUNNING. The handler then runs OUTSIDE any
        transaction, and the outcome is written only if the claim is still
        ours.
        """
        now = self.clock.now()
        claimed: sqlite3.Row | None = None
        with self._tx() as conn:
            fence = self._check_fence(conn, advance=True)
            rows = conn.execute(
                "SELECT * FROM job WHERE queue=? AND status IN (?,?) ORDER BY queue_seq",
                (queue, *_RUNNABLE),
            ).fetchall()
            for row in rows:
                due = _from_iso(row["next_attempt_at"])
                if due is not None and due > now:
                    continue
                if JobType(row["job_type"]) not in self._handlers:
                    continue
                conn.execute(
                    "UPDATE job SET status=?, claimed_by=?, claim_fence=?, claimed_at=? "
                    "WHERE job_id=?",
                    (JobStatus.RUNNING.value, self._holder, fence, _iso(now), row["job_id"]),
                )
                claimed = row
                break
        if claimed is None:
            return None
        return self._execute(self._record_from_row(claimed), fence=fence)

    def drain(self, queue: str, *, max_jobs: int = 100) -> tuple[JobRecord, ...]:
        """Run until the queue has nothing runnable, or ``max_jobs`` is hit."""
        done: list[JobRecord] = []
        for _ in range(max_jobs):
            record = self.run_next(queue)
            if record is None:
                break
            done.append(record)
            if record.status == JobStatus.FAILED:
                # Re-queued with backoff; stop rather than spin on the clock.
                break
        return tuple(done)

    def _execute(self, record: JobRecord, *, fence: int | None) -> JobRecord:
        registration = self._handlers[record.spec.job_type]
        attempt_number = record.attempt_count + 1
        started = self.clock.now()
        ctx = JobContext(
            job_id=record.job_id,
            job_type=record.spec.job_type,
            payload=dict(record.spec.payload),
            attempt=attempt_number,
            now=started,
            services=registration.services,
        )
        try:
            output = registration.handler(ctx)
        except Exception as exc:
            finished = self.clock.now()
            error = f"{type(exc).__name__}: {exc}"
            attempts = (
                *record.attempts,
                Attempt(attempt_number, started, finished, JobStatus.FAILED, error),
            )
            if attempt_number >= record.spec.max_attempts:
                updated = replace(
                    record,
                    status=JobStatus.DEAD_LETTER,
                    attempts=attempts,
                    error=error,
                    next_attempt_at=None,
                )
            else:
                delay = record.spec.backoff_for(attempt_number)
                updated = replace(
                    record,
                    status=JobStatus.FAILED,
                    attempts=attempts,
                    error=error,
                    next_attempt_at=finished + timedelta(seconds=delay),
                )
            return self._finish(updated, fence=fence, result_json=None)

        finished = self.clock.now()
        attempts = (
            *record.attempts,
            Attempt(attempt_number, started, finished, JobStatus.SUCCEEDED),
        )
        result_json = _result_to_json(output)
        updated = replace(
            record,
            status=JobStatus.SUCCEEDED,
            attempts=attempts,
            result=json.loads(result_json),
            error="",
            next_attempt_at=None,
        )
        return self._finish(updated, fence=fence, result_json=result_json)

    def _finish(
        self, updated: JobRecord, *, fence: int | None, result_json: str | None
    ) -> JobRecord:
        """Write an outcome, but only over OUR claim.

        ``WHERE status='running' AND claimed_by=? AND claim_fence IS ?``: if the
        row was reclaimed (a newer holder recovered it as abandoned) this
        process's outcome is discarded and :class:`FencedOut` is raised, rather
        than overwriting the record of whoever owns it now.
        """
        with self._tx() as conn:
            self._check_fence(conn, advance=False)
            requeue = updated.status is JobStatus.FAILED
            cursor = conn.execute(
                "UPDATE job SET status=?, attempts_json=?, result_json=?, error=?, "
                "next_attempt_at=?, claimed_by='', claim_fence=NULL, claimed_at=NULL, "
                "queue_seq=CASE WHEN ? THEN ? ELSE queue_seq END "
                "WHERE job_id=? AND status=? AND claimed_by=? AND claim_fence IS ?",
                (
                    updated.status.value,
                    _attempts_to_json(updated.attempts),
                    result_json,
                    updated.error,
                    _iso(updated.next_attempt_at),
                    1 if requeue else 0,
                    self._next_seq(conn) if requeue else 0,
                    updated.job_id,
                    JobStatus.RUNNING.value,
                    self._holder,
                    fence,
                ),
            )
            if cursor.rowcount != 1:
                raise FencedOut(
                    f"job {updated.job_id} is no longer claimed by {self._holder!r} "
                    f"(fence {fence}); its outcome ({updated.status.value}) was NOT "
                    "recorded. The job was reclaimed by a newer worker."
                )
        return updated

    def recover_abandoned(self, *, reason: str = "worker died mid-job") -> tuple[JobRecord, ...]:
        """Return every job left RUNNING by a dead predecessor to its retry policy.

        Safe to call only while holding the worker lease (``ResearchWorker.resume``
        does), because the lease is the proof that no other live process is
        still running those jobs. Each abandoned run is counted as a FAILED
        attempt -- it did not complete -- and the job is re-queued with its own
        backoff, or dead-lettered when that exhausts ``max_attempts``. A crash
        loop on one poisoned job therefore ends in the dead-letter queue rather
        than running forever.
        """
        now = self.clock.now()
        recovered: list[JobRecord] = []
        with self._tx() as conn:
            fence = self._check_fence(conn, advance=True)
            rows = conn.execute(
                "SELECT * FROM job WHERE status=? ORDER BY queue_seq",
                (JobStatus.RUNNING.value,),
            ).fetchall()
            for row in rows:
                if (
                    row["claimed_by"] == self._holder
                    and fence is not None
                    and row["claim_fence"] == fence
                ):
                    continue  # our own live claim, not an abandoned one
                record = self._record_from_row(row)
                started = _from_iso(row["claimed_at"]) or now
                number = record.attempt_count + 1
                error = (
                    f"abandoned: {reason} (claimed by {row['claimed_by'] or '?'} "
                    f"with fence {row['claim_fence']})"
                )
                attempts = (
                    *record.attempts,
                    Attempt(number, started, now, JobStatus.FAILED, error),
                )
                if number >= record.spec.max_attempts:
                    status, next_at = JobStatus.DEAD_LETTER, None
                else:
                    status = JobStatus.FAILED
                    next_at = now + timedelta(seconds=record.spec.backoff_for(number))
                conn.execute(
                    "UPDATE job SET status=?, attempts_json=?, error=?, next_attempt_at=?, "
                    "claimed_by='', claim_fence=NULL, claimed_at=NULL, queue_seq=? "
                    "WHERE job_id=?",
                    (
                        status.value,
                        _attempts_to_json(attempts),
                        error,
                        _iso(next_at),
                        self._next_seq(conn),
                        record.job_id,
                    ),
                )
                recovered.append(
                    replace(
                        record,
                        status=status,
                        attempts=attempts,
                        error=error,
                        next_attempt_at=next_at,
                    )
                )
        return tuple(recovered)

    # -- scheduling and events -------------------------------------------

    def schedule(self, job: ScheduledJob) -> ScheduledJob:
        if job.name in self._scheduled:
            raise OrchestratorError(f"a scheduled job named {job.name!r} already exists")
        self._scheduled[job.name] = job
        return job

    def tick(self, now: datetime | None = None) -> tuple[JobRecord, ...]:
        """Submit every scheduled job whose interval bucket has not been filled.

        Because the idempotency key contains the bucket, calling ``tick`` ten
        times inside one interval submits once -- and, with a file-backed
        ledger, so does a restarted process calling it again.
        """
        when = now or self.clock.now()
        submitted: list[JobRecord] = []
        for name in sorted(self._scheduled):
            job = self._scheduled[name]
            key = job.key_for(when)
            if self.by_key(key) is not None:
                continue
            submitted.append(
                self.submit(
                    JobSpec(
                        job_type=job.job_type,
                        queue=job.queue,
                        payload=dict(job.payload),
                        idempotency_key=key,
                        submitted_by=job.submitted_by,
                        role=job.role,
                        trigger=f"schedule:{job.name}",
                    )
                )
            )
        return tuple(submitted)

    def on_event(self, trigger: EventTrigger) -> EventTrigger:
        self._triggers.append(trigger)
        return trigger

    def emit(self, event: str, payload: Mapping[str, Any]) -> tuple[JobRecord, ...]:
        """Fire an event.  Each matching trigger builds one job, idempotently."""
        submitted: list[JobRecord] = []
        for trigger in self._triggers:
            if trigger.event != event:
                continue
            built = dict(trigger.build(payload))
            submitted.append(
                self.submit(
                    JobSpec(
                        job_type=trigger.job_type,
                        queue=trigger.queue,
                        payload=built,
                        submitted_by=trigger.submitted_by,
                        role=trigger.role,
                        trigger=f"event:{event}",
                    )
                )
            )
        return tuple(submitted)

    # -- reading ----------------------------------------------------------

    def get(self, job_id: str) -> JobRecord:
        rows = self._read("SELECT * FROM job WHERE job_id=?", (job_id,))
        if not rows:
            raise KeyError(f"unknown job {job_id!r}")
        return self._record_from_row(rows[0])

    def by_key(self, idempotency_key: str) -> JobRecord | None:
        rows = self._read("SELECT * FROM job WHERE idempotency_key=?", (idempotency_key,))
        return self._record_from_row(rows[0]) if rows else None

    def records(self) -> tuple[JobRecord, ...]:
        rows = self._read("SELECT * FROM job")
        return tuple(
            sorted(
                (self._record_from_row(r) for r in rows),
                key=lambda r: (r.submitted_at or datetime.min.replace(tzinfo=UTC), r.job_id),
            )
        )

    def queue_names(self) -> tuple[str, ...]:
        return tuple(r["queue"] for r in self._read("SELECT DISTINCT queue FROM job ORDER BY queue"))

    def pending(self, queue: str) -> tuple[JobRecord, ...]:
        """Jobs waiting on ``queue`` (PENDING, or FAILED and re-queued), in queue order."""
        rows = self._read(
            "SELECT * FROM job WHERE queue=? AND status IN (?,?) ORDER BY queue_seq",
            (queue, *_RUNNABLE),
        )
        return tuple(self._record_from_row(r) for r in rows)

    def running(self) -> tuple[JobRecord, ...]:
        rows = self._read(
            "SELECT * FROM job WHERE status=? ORDER BY queue_seq", (JobStatus.RUNNING.value,)
        )
        return tuple(self._record_from_row(r) for r in rows)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in JobStatus}
        for row in self._read("SELECT status, COUNT(*) AS n FROM job GROUP BY status"):
            out[row["status"]] = int(row["n"])
        return out


# ---------------------------------------------------------------------------
# The submission interface tools use
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobTicket:
    """What a tool hands back to an agent after queueing work."""

    job_id: str
    queue: str
    job_type: str
    status: str
    idempotency_key: str
    deduplicated: bool

    @classmethod
    def from_record(cls, record: JobRecord) -> JobTicket:
        return cls(
            job_id=record.job_id,
            queue=record.spec.queue,
            job_type=record.spec.job_type.value,
            status=record.status.value,
            idempotency_key=record.spec.idempotency_key,
            deduplicated=record.deduplicated,
        )


@runtime_checkable
class JobSubmitter(Protocol):
    """The narrow slice of the orchestrator that tools are given."""

    def __call__(self, spec: JobSpec) -> JobTicket: ...


def submitter_for(orchestrator: Orchestrator) -> JobSubmitter:
    def _submit(spec: JobSpec) -> JobTicket:
        return JobTicket.from_record(orchestrator.submit(spec))

    return _submit


def queue_capabilities(specs: Sequence[JobSpec]) -> frozenset[Capability]:
    """Union of the capabilities a batch of jobs required.  Audit convenience."""
    out: set[Capability] = set()
    for spec in specs:
        out |= spec.required_capabilities
    return frozenset(out)


__all__ = [
    "Attempt",
    "Clock",
    "EventTrigger",
    "FencedOut",
    "JobContext",
    "JobHandler",
    "JobRecord",
    "JobSpec",
    "JobStatus",
    "JobSubmitter",
    "JobTicket",
    "JobType",
    "ManualClock",
    "Orchestrator",
    "OrchestratorError",
    "ScheduledJob",
    "SystemClock",
    "UnknownJobType",
    "canonical_key",
    "queue_capabilities",
    "submitter_for",
]
