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

import hashlib
import inspect
import json
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
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


class Orchestrator:
    """Named queues plus a durable job ledger.  Holds no reasoning state."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock: Clock = clock or SystemClock()
        self._handlers: dict[JobType, _Registration] = {}
        self._records: dict[str, JobRecord] = {}
        self._by_key: dict[str, str] = {}
        self._queues: dict[str, list[str]] = {}
        self._scheduled: dict[str, ScheduledJob] = {}
        self._triggers: list[EventTrigger] = []

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
        """Enqueue a job.  Idempotent on ``spec.idempotency_key``."""
        existing_id = self._by_key.get(spec.idempotency_key)
        if existing_id is not None:
            record = self._records[existing_id]
            return replace(record, deduplicated=True)
        if spec.job_type not in self._handlers:
            raise UnknownJobType(
                f"no handler registered for {spec.job_type.value}; a job nobody can "
                "execute must not be accepted into a queue"
            )
        now = self.clock.now()
        record = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:16]}",
            spec=replace(spec, created_at=spec.created_at or now),
            status=JobStatus.PENDING,
            submitted_at=now,
            next_attempt_at=spec.scheduled_for or now,
        )
        self._records[record.job_id] = record
        self._by_key[spec.idempotency_key] = record.job_id
        self._queues.setdefault(spec.queue, []).append(record.job_id)
        return record

    # -- execution --------------------------------------------------------

    def run_next(self, queue: str) -> JobRecord | None:
        """Execute the next runnable job on ``queue``.  Returns None if idle."""
        now = self.clock.now()
        pending = self._queues.get(queue, [])
        for i, job_id in enumerate(pending):
            record = self._records[job_id]
            if record.status not in (JobStatus.PENDING, JobStatus.FAILED):
                continue
            if record.next_attempt_at is not None and record.next_attempt_at > now:
                continue
            del pending[i]
            return self._execute(record)
        return None

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

    def _execute(self, record: JobRecord) -> JobRecord:
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
        self._records[record.job_id] = replace(record, status=JobStatus.RUNNING)
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
                self._queues.setdefault(record.spec.queue, []).append(record.job_id)
            self._records[record.job_id] = updated
            return updated

        finished = self.clock.now()
        attempts = (
            *record.attempts,
            Attempt(attempt_number, started, finished, JobStatus.SUCCEEDED),
        )
        updated = replace(
            record,
            status=JobStatus.SUCCEEDED,
            attempts=attempts,
            result=dict(output),
            error="",
            next_attempt_at=None,
        )
        self._records[record.job_id] = updated
        return updated

    # -- scheduling and events -------------------------------------------

    def schedule(self, job: ScheduledJob) -> ScheduledJob:
        if job.name in self._scheduled:
            raise OrchestratorError(f"a scheduled job named {job.name!r} already exists")
        self._scheduled[job.name] = job
        return job

    def tick(self, now: datetime | None = None) -> tuple[JobRecord, ...]:
        """Submit every scheduled job whose interval bucket has not been filled.

        Because the idempotency key contains the bucket, calling ``tick`` ten
        times inside one interval submits once.
        """
        when = now or self.clock.now()
        submitted: list[JobRecord] = []
        for name in sorted(self._scheduled):
            job = self._scheduled[name]
            key = job.key_for(when)
            if key in self._by_key:
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
        if job_id not in self._records:
            raise KeyError(f"unknown job {job_id!r}")
        return self._records[job_id]

    def by_key(self, idempotency_key: str) -> JobRecord | None:
        job_id = self._by_key.get(idempotency_key)
        return self._records.get(job_id) if job_id else None

    def records(self) -> tuple[JobRecord, ...]:
        return tuple(
            sorted(self._records.values(), key=lambda r: (r.submitted_at or datetime.min, r.job_id))
        )

    def queue_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._queues))

    def pending(self, queue: str) -> tuple[JobRecord, ...]:
        return tuple(self._records[j] for j in self._queues.get(queue, []))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in JobStatus}
        for record in self._records.values():
            out[record.status.value] += 1
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
