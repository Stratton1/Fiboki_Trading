"""Worker PROCESSES.

The one architectural fact that matters here: a worker is a process, not a
thread inside the API.  V1 ran its worker as a daemon thread in the FastAPI
process; the thread died, the API stayed green, and nothing in the system was
capable of noticing.  Everything in this package -- the lease, the heartbeat,
the signal handling, the exit codes -- exists because the worker now has its
own pid and can therefore be supervised, observed and reported dead.

Run one with::

    python -m fiboki.workers.research_worker      # or via `fiboki worker run`
"""
from __future__ import annotations

from fiboki.workers.base import (
    EXIT_FATAL,
    EXIT_LEASE_HELD,
    EXIT_OK,
    CycleOutcome,
    CycleResult,
    ErrorReporter,
    Heartbeat,
    LeaseLost,
    LeaseNotAcquired,
    LoggingErrorReporter,
    Worker,
    WorkerConfig,
    WorkerLease,
    WorkerState,
    WorkerStore,
    force_release,
    summarise_leases,
    watchdog_views,
    worker_id,
)

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
    "force_release",
    "summarise_leases",
    "watchdog_views",
    "worker_id",
]
