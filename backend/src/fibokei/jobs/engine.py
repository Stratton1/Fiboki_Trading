"""Background job engine using threading + concurrent.futures.

Jobs are submitted to a thread pool, tracked in-memory and persisted to the DB.
Each job has a UUID, state lifecycle (pending → running → completed/failed),
progress tracking (0–100), and optional result references.
"""

import logging
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_WORKERS = 4


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobInfo:
    """In-memory representation of a running/completed job."""

    job_id: str
    job_type: str  # "backtest" | "research"
    label: str  # Human-readable label, e.g. "bot01 EURUSD H1"
    state: JobState = JobState.PENDING
    progress: int = 0  # 0-100
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    _future: Future | None = field(default=None, repr=False)


class JobEngine:
    """Singleton-style job engine managing background tasks."""

    def __init__(self, max_workers: int = MAX_WORKERS):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._jobs: dict[str, JobInfo] = {}
        self._lock = Lock()

    def submit(
        self,
        job_type: str,
        label: str,
        fn: Callable[..., dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> JobInfo:
        """Submit a job for background execution.

        ``fn`` must accept a ``progress_callback(pct: int)`` keyword argument
        and return a dict that will be stored as the job result.
        """
        job_id = str(uuid.uuid4())
        info = JobInfo(job_id=job_id, job_type=job_type, label=label)

        def _progress_callback(pct: int) -> None:
            with self._lock:
                if info.state == JobState.CANCELLED:
                    raise _CancelledError(job_id)
                info.progress = max(0, min(100, pct))

        def _wrapper() -> None:
            with self._lock:
                if info.state == JobState.CANCELLED:
                    return
                info.state = JobState.RUNNING
                info.started_at = datetime.now(timezone.utc)

            try:
                result = fn(*args, progress_callback=_progress_callback, **kwargs)
                with self._lock:
                    if info.state == JobState.CANCELLED:
                        return
                    info.state = JobState.COMPLETED
                    info.progress = 100
                    info.result = result
                    info.completed_at = datetime.now(timezone.utc)
                logger.info("Job %s (%s) completed", job_id, label)
            except _CancelledError:
                with self._lock:
                    info.state = JobState.CANCELLED
                    info.completed_at = datetime.now(timezone.utc)
                logger.info("Job %s (%s) cancelled", job_id, label)
            except Exception as exc:
                with self._lock:
                    info.state = JobState.FAILED
                    info.error = str(exc)
                    info.completed_at = datetime.now(timezone.utc)
                logger.exception("Job %s (%s) failed", job_id, label)

        future = self._executor.submit(_wrapper)
        info._future = future

        with self._lock:
            self._jobs[job_id] = info

        logger.info("Job %s (%s) submitted [type=%s]", job_id, label, job_type)
        return info

    def get(self, job_id: str) -> JobInfo | None:
        """Get job info by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(
        self,
        job_type: str | None = None,
        state: JobState | None = None,
        limit: int = 50,
    ) -> list[JobInfo]:
        """List jobs, newest first, with optional filters."""
        with self._lock:
            jobs = list(self._jobs.values())

        if job_type:
            jobs = [j for j in jobs if j.job_type == job_type]
        if state:
            jobs = [j for j in jobs if j.state == state]

        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a job.

        Returns True if the job was found and cancellation was requested.
        The job will transition to CANCELLED on its next progress_callback.
        """
        with self._lock:
            info = self._jobs.get(job_id)
            if not info:
                return False
            if info.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                return False
            info.state = JobState.CANCELLED
            info.completed_at = datetime.now(timezone.utc)
            return True

    def active_count(self) -> int:
        """Number of pending + running jobs."""
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.state in (JobState.PENDING, JobState.RUNNING)
            )

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the thread pool."""
        self._executor.shutdown(wait=wait)


class _CancelledError(Exception):
    """Internal signal for job cancellation."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Job {job_id} cancelled")


# Module-level singleton, initialized during app startup
_engine: JobEngine | None = None


def get_job_engine() -> JobEngine:
    """Get the global job engine instance."""
    global _engine
    if _engine is None:
        _engine = JobEngine()
    return _engine


def reset_job_engine() -> None:
    """Shut down and replace the engine (for testing)."""
    global _engine
    if _engine is not None:
        _engine.shutdown(wait=False)
    _engine = JobEngine()
