"""The research worker: executes queued jobs, and checkpoints HONESTLY.

The V1 failure this module exists to close
------------------------------------------
V1 ran a 10,000-cell parameter sweep.  It checkpointed each cell as it went so
a restart would not recompute finished work -- correct, and the right idea.
What it did was::

    result = run_cell(cell)
    checkpoint.mark_done(cell)      # unconditionally
    if result is None:              # no data for this instrument/period
        continue

A cell that returned no data was therefore recorded as *complete*.  On the
restart that inevitably followed, those cells were skipped, permanently.  At
the end, the sweep wrote ``phase1_complete.json``.  That file was false: the
sweep had never computed roughly 8% of its cells and never would, and every
downstream ranking was computed over a silently truncated universe.  Nobody
found out for months, because the artefact said ``complete: true``.

Three rules follow, and they are enforced structurally here:

**Rule 1 -- a no-data cell is NOT done.**  :class:`CellStatus` separates
``DONE`` from ``NO_DATA``.  :meth:`CheckpointStore.pending` returns everything
that is not ``DONE``, so a ``NO_DATA`` cell is retried on the next run.  The
record is kept (so the reason is visible) without being treated as success.

**Rule 2 -- too much missing data is a FAILURE, not a footnote.**  If more
than ``max_no_data_fraction`` of a batch returns nothing, the sweep raises
:class:`NoDataFractionExceeded`.  A sweep quietly producing 8% fewer results
than it claims is worse than one that stops, because the first one gets
believed.

**Rule 3 -- the completion marker cannot lie.**  :meth:`SweepReport.complete`
is computed from the checkpoint store, and :func:`write_completion_marker`
refuses to write a marker for an incomplete sweep.  You cannot get a
``complete: true`` artefact out of this module for a sweep that is not.

Alignment with the orchestrator
-------------------------------
This worker does not reimplement queues, retries or idempotency; those live in
:mod:`fiboki.agents.orchestrator` and are the orchestrator's job.  The worker
is the PROCESS that calls :meth:`Orchestrator.run_next` on a set of queues.
The division is: the orchestrator decides what a job is and when it may run;
the worker decides which machine runs it, when it stops, and what it records
about itself while doing so.

Agent research cycles (``FIBOKI_AGENT_CYCLES``)
-----------------------------------------------
With the flag on, :meth:`ResearchWorker.setup` composes
:class:`fiboki.workers.research_runtime.ResearchRuntime` onto THIS worker's
orchestrator (so the deterministic handlers are registered where the loop
drains) and each cycle first asks it for due work: the nightly research cycle
and any queued failure investigation. With the flag off nothing is composed
and the worker behaves exactly as before.

An agent cycle with a real model runs for minutes, far longer than the lease
TTL, and the base loop only beats and renews BETWEEN cycles. So the agent work
runs inside :class:`HeartbeatPulse`, a thread that renews the lease and writes
a ``working`` heartbeat every ``pulse_seconds`` until the work returns. Without
it a long cycle would read as a dead worker and, worse, let a second worker
take the lease mid-cycle.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fiboki.agents.orchestrator import JobRecord, JobStatus, JobType, Orchestrator
from fiboki.obs import metrics as _metrics
from fiboki.obs.alerts import AlertDispatcher, AlertEvent
from fiboki.obs.logging import bind, get_logger, new_correlation_id
from fiboki.workers.base import (
    CycleResult,
    LeaseLost,
    Worker,
    WorkerConfig,
    WorkerState,
    WorkerStore,
)

__all__ = [
    "CellOutcome",
    "CellStatus",
    "CheckpointStore",
    "HeartbeatPulse",
    "NoDataFractionExceeded",
    "ResearchWorker",
    "ResearchWorkerConfig",
    "SweepCell",
    "SweepReport",
    "SweepRunner",
    "write_completion_marker",
]

_log = get_logger("fiboki.workers.research")

#: Job types this worker will execute. Sourced from the orchestrator's enum so
#: adding a job type there is a compile-time-ish decision here, not a silent
#: omission.
RESEARCH_JOB_TYPES: tuple[JobType, ...] = (
    JobType.BACKTEST,
    JobType.VALIDATION,
    JobType.WALKFORWARD,
    JobType.ABLATION,
    JobType.SENSITIVITY,
    JobType.DATA_QUALITY_SCAN,
    JobType.REGIME_SCAN,
    JobType.LIBRARIAN_FILING,
)


# ---------------------------------------------------------------------------
# Cells and their status
# ---------------------------------------------------------------------------


class CellStatus(str, Enum):
    """The states a sweep cell can be in.

    ``DONE`` is the ONLY one that means "never compute this again".  That is
    the whole fix: V1 had a boolean, so everything that stopped being in
    progress became done.
    """

    PENDING = "pending"
    DONE = "done"
    #: Computed, produced no usable data. NOT done. Retried on the next run.
    NO_DATA = "no_data"
    #: Raised. NOT done. Retried, and counted separately from no-data.
    FAILED = "failed"
    #: A human decided this cell is legitimately impossible (e.g. the
    #: instrument did not exist in that period). Excluded from the denominator
    #: by an explicit act, which leaves a record.
    EXCLUDED = "excluded"

    @property
    def terminal(self) -> bool:
        """Never recomputed."""
        return self in (CellStatus.DONE, CellStatus.EXCLUDED)


@dataclass(frozen=True, slots=True)
class SweepCell:
    """One unit of a sweep.  ``key`` must be stable across runs -- it is the
    checkpoint identity, so deriving it from anything non-deterministic (a
    ``uuid4``, a dict iteration order) breaks resumption silently."""

    key: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("a sweep cell needs a stable key")


@dataclass(frozen=True, slots=True)
class CellOutcome:
    """What computing a cell produced.  ``status`` is chosen by the compute
    function, and the runner refuses to guess: returning ``None`` is treated as
    ``NO_DATA``, never as success."""

    status: CellStatus
    result: Mapping[str, Any] | None = None
    reason: str = ""

    @classmethod
    def done(cls, result: Mapping[str, Any] | None = None) -> CellOutcome:
        return cls(CellStatus.DONE, result or {})

    @classmethod
    def no_data(cls, reason: str = "") -> CellOutcome:
        return cls(CellStatus.NO_DATA, None, reason or "no bars for this cell")

    @classmethod
    def failed(cls, reason: str) -> CellOutcome:
        return cls(CellStatus.FAILED, None, reason)


class NoDataFractionExceeded(RuntimeError):
    """Too much of the batch had no data.  The sweep stops.

    The number in the message is deliberately the raw count as well as the
    fraction, because "6% missing" reads as noise and "612 of 10,000 cells
    produced nothing" does not.
    """

    def __init__(self, no_data: int, total: int, threshold: float) -> None:
        fraction = no_data / total if total else 1.0
        super().__init__(
            f"{no_data} of {total} cells ({fraction:.1%}) returned NO DATA, above the "
            f"{threshold:.1%} threshold. Stopping. These cells are NOT checkpointed as "
            "complete and will be retried. Do not write a completion marker and do not "
            "rank on this sweep: V1 shipped a phase1_complete.json for exactly this "
            "situation and the ranking underneath it was computed over a truncated "
            "universe for months."
        )
        self.no_data = no_data
        self.total = total
        self.fraction = fraction
        self.threshold = threshold


# ---------------------------------------------------------------------------
# Checkpoint store
# ---------------------------------------------------------------------------


class CheckpointStore:
    """Durable per-cell progress in SQLite.

    SQLite rather than a JSON file because a JSON file rewritten after every
    cell is a torn-write waiting to happen: V1's checkpoint file was truncated
    by a power loss once and the sweep restarted from zero, which is the mild
    version of this failure.  A row per cell, committed, is not.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS sweep_cell (
        sweep_id   TEXT NOT NULL,
        cell_key   TEXT NOT NULL,
        status     TEXT NOT NULL,
        reason     TEXT NOT NULL DEFAULT '',
        result     TEXT,
        attempts   INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (sweep_id, cell_key)
    );
    CREATE INDEX IF NOT EXISTS ix_sweep_cell_status ON sweep_cell (sweep_id, status);
    CREATE TABLE IF NOT EXISTS sweep_run (
        sweep_id   TEXT PRIMARY KEY,
        total      INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        note       TEXT NOT NULL DEFAULT ''
    );
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(self._SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> CheckpointStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writes ----------------------------------------------------------

    def begin_sweep(self, sweep_id: str, total: int, note: str = "") -> None:
        now = datetime.now(tz=UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sweep_run (sweep_id, total, started_at, updated_at, note) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(sweep_id) DO UPDATE SET total=excluded.total, "
                "updated_at=excluded.updated_at",
                (sweep_id, total, now, now, note),
            )
            self._conn.commit()

    def record(self, sweep_id: str, cell_key: str, outcome: CellOutcome) -> None:
        """Write the cell's outcome.  ``DONE`` is stored as ``DONE``; NOTHING
        ELSE IS.  This method is the single place that decision is made, which
        is why it is a method and not an inline ``mark_done`` at the call site
        the way V1 had it."""
        now = datetime.now(tz=UTC).isoformat()
        payload = json.dumps(outcome.result, default=str) if outcome.result is not None else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO sweep_cell (sweep_id, cell_key, status, reason, result, "
                "attempts, updated_at) VALUES (?,?,?,?,?,1,?) "
                "ON CONFLICT(sweep_id, cell_key) DO UPDATE SET "
                "status=excluded.status, reason=excluded.reason, result=excluded.result, "
                "attempts=sweep_cell.attempts+1, updated_at=excluded.updated_at",
                (sweep_id, cell_key, outcome.status.value, outcome.reason, payload, now),
            )
            self._conn.commit()

    def exclude(self, sweep_id: str, cell_key: str, reason: str) -> None:
        """An OPERATOR marks a cell legitimately impossible.

        This is the only way a non-computed cell stops being retried, and it
        requires a reason, so the record says who decided and why rather than
        a boolean silently flipping.
        """
        if not reason.strip():
            raise ValueError("excluding a cell requires a reason; that is the point")
        self.record(sweep_id, cell_key, CellOutcome(CellStatus.EXCLUDED, None, reason))

    # -- reads -----------------------------------------------------------

    def status_of(self, sweep_id: str, cell_key: str) -> CellStatus:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM sweep_cell WHERE sweep_id=? AND cell_key=?",
                (sweep_id, cell_key),
            ).fetchone()
        return CellStatus(row[0]) if row else CellStatus.PENDING

    def completed_keys(self, sweep_id: str) -> set[str]:
        """Keys that must NEVER be recomputed.  ``DONE`` and ``EXCLUDED`` only."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT cell_key FROM sweep_cell WHERE sweep_id=? AND status IN (?,?)",
                (sweep_id, CellStatus.DONE.value, CellStatus.EXCLUDED.value),
            ).fetchall()
        return {r[0] for r in rows}

    def pending(self, sweep_id: str, cells: Sequence[SweepCell]) -> list[SweepCell]:
        """The cells still to compute.

        A ``NO_DATA`` cell is in here.  That single fact is the difference
        between this module and V1's.
        """
        done = self.completed_keys(sweep_id)
        return [c for c in cells if c.key not in done]

    def counts(self, sweep_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM sweep_cell WHERE sweep_id=? GROUP BY status",
                (sweep_id,),
            ).fetchall()
        out = {s.value: 0 for s in CellStatus}
        for status, count in rows:
            out[status] = count
        return out

    def results(self, sweep_id: str) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT cell_key, result FROM sweep_cell WHERE sweep_id=? AND status=?",
                (sweep_id, CellStatus.DONE.value),
            ).fetchall()
        return {key: json.loads(blob) if blob else {} for key, blob in rows}

    def total(self, sweep_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT total FROM sweep_run WHERE sweep_id=?", (sweep_id,)
            ).fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Sweep report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SweepReport:
    """The truth about a sweep.  ``complete`` is DERIVED, never asserted."""

    sweep_id: str
    total: int
    done: int
    no_data: int
    failed: int
    excluded: int
    computed_this_run: int
    skipped_this_run: int
    elapsed_seconds: float
    stopped_early: bool = False
    stop_reason: str = ""

    @property
    def outstanding(self) -> int:
        return max(0, self.total - self.done - self.excluded)

    @property
    def complete(self) -> bool:
        """True only when every cell is DONE or explicitly EXCLUDED.

        A no-data cell makes this False.  A failed cell makes this False.
        Being stopped early makes this False.  There is no code path in this
        module that sets it any other way.
        """
        return (
            not self.stopped_early
            and self.total > 0
            and self.outstanding == 0
            and self.no_data == 0
            and self.failed == 0
        )

    @property
    def no_data_fraction(self) -> float:
        return self.no_data / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sweep_id": self.sweep_id,
            "total": self.total,
            "done": self.done,
            "no_data": self.no_data,
            "failed": self.failed,
            "excluded": self.excluded,
            "outstanding": self.outstanding,
            "computed_this_run": self.computed_this_run,
            "skipped_this_run": self.skipped_this_run,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "no_data_fraction": round(self.no_data_fraction, 6),
            "complete": self.complete,
            "stopped_early": self.stopped_early,
            "stop_reason": self.stop_reason,
        }

    def summary(self) -> str:
        return (
            f"sweep {self.sweep_id}: {self.done}/{self.total} done, "
            f"{self.no_data} no-data, {self.failed} failed, {self.excluded} excluded, "
            f"{self.computed_this_run} computed / {self.skipped_this_run} skipped this run, "
            f"complete={self.complete}"
        )


class IncompleteSweep(RuntimeError):
    pass


def write_completion_marker(path: str | os.PathLike[str], report: SweepReport) -> Path:
    """Write a completion marker, REFUSING to lie about it.

    V1 wrote ``phase1_complete.json`` unconditionally at the end of the loop.
    The loop finishing and the sweep being complete are different facts, and
    the artefact recorded the first while claiming the second.
    """
    if not report.complete:
        raise IncompleteSweep(
            f"refusing to write a completion marker for an INCOMPLETE sweep: "
            f"{report.summary()}. Resolve the {report.outstanding} outstanding cell(s) "
            f"({report.no_data} no-data, {report.failed} failed) or exclude them "
            "explicitly with a reason."
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {**report.to_dict(), "written_at": datetime.now(tz=UTC).isoformat()}
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Sweep runner
# ---------------------------------------------------------------------------

#: ``compute(cell) -> CellOutcome``. Returning ``None`` is read as NO_DATA.
ComputeFn = Callable[[SweepCell], "CellOutcome | Mapping[str, Any] | None"]


class SweepRunner:
    """Runs a cell list, resumably, with an honest no-data policy."""

    def __init__(
        self,
        checkpoints: CheckpointStore,
        *,
        max_no_data_fraction: float = 0.02,
        min_cells_for_guard: int = 20,
        dispatcher: AlertDispatcher | None = None,
        should_stop: Callable[[], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 <= max_no_data_fraction <= 1.0:
            raise ValueError("max_no_data_fraction must be between 0 and 1")
        self.checkpoints = checkpoints
        self.max_no_data_fraction = max_no_data_fraction
        self.min_cells_for_guard = min_cells_for_guard
        self.dispatcher = dispatcher
        self.should_stop = should_stop or (lambda: False)
        self.monotonic = monotonic

    def run(
        self,
        sweep_id: str,
        cells: Sequence[SweepCell],
        compute: ComputeFn,
        *,
        strategy: str = "",
        on_cell: Callable[[SweepCell, CellOutcome], None] | None = None,
    ) -> SweepReport:
        started = self.monotonic()
        total = len(cells)
        self.checkpoints.begin_sweep(sweep_id, total)

        pending = self.checkpoints.pending(sweep_id, cells)
        skipped = total - len(pending)
        _log.info(
            "sweep resuming",
            extra={
                "sweep_id": sweep_id,
                "total": total,
                "pending": len(pending),
                "skipped": skipped,
            },
        )

        computed = 0
        no_data_this_run = 0
        stopped_early = False
        stop_reason = ""

        for cell in pending:
            if self.should_stop():
                stopped_early = True
                stop_reason = "worker stopping"
                break

            cell_started = self.monotonic()
            try:
                raw = compute(cell)
            except Exception as exc:
                outcome = CellOutcome.failed(f"{type(exc).__name__}: {exc}")
            else:
                outcome = _coerce_outcome(raw)

            elapsed = self.monotonic() - cell_started
            if strategy:
                _metrics.observe_backtest_wall_time(
                    elapsed, strategy=strategy, instrument=str(cell.params.get("instrument", ""))
                )

            # THE CHECKPOINT. Status is whatever the outcome says, and only a
            # DONE outcome removes the cell from future runs.
            self.checkpoints.record(sweep_id, cell.key, outcome)
            computed += 1
            if outcome.status is CellStatus.NO_DATA:
                no_data_this_run += 1
            if on_cell is not None:
                on_cell(cell, outcome)

            # The guard is evaluated as we go, not at the end, so a sweep that
            # is going to fail the guard does not burn nine hours first.
            if computed >= self.min_cells_for_guard:
                fraction = no_data_this_run / computed
                if fraction > self.max_no_data_fraction:
                    self._alert_no_data(sweep_id, no_data_this_run, computed, fraction)
                    raise NoDataFractionExceeded(
                        no_data_this_run, computed, self.max_no_data_fraction
                    )

        counts = self.checkpoints.counts(sweep_id)
        report = SweepReport(
            sweep_id=sweep_id,
            total=total,
            done=counts[CellStatus.DONE.value],
            no_data=counts[CellStatus.NO_DATA.value],
            failed=counts[CellStatus.FAILED.value],
            excluded=counts[CellStatus.EXCLUDED.value],
            computed_this_run=computed,
            skipped_this_run=skipped,
            elapsed_seconds=self.monotonic() - started,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
        )
        # A final guard over the WHOLE sweep, not just this run: a sweep that
        # accumulated no-data cells across three restarts is just as truncated.
        if (
            report.total >= self.min_cells_for_guard
            and report.no_data_fraction > self.max_no_data_fraction
        ):
            self._alert_no_data(sweep_id, report.no_data, report.total, report.no_data_fraction)
            raise NoDataFractionExceeded(
                report.no_data, report.total, self.max_no_data_fraction
            )
        _log.info("sweep pass finished", extra={"sweep_id": sweep_id, **report.to_dict()})
        return report

    def _alert_no_data(self, sweep_id: str, no_data: int, total: int, fraction: float) -> None:
        if self.dispatcher is None:
            return
        self.dispatcher.fire(
            AlertEvent.SWEEP_NO_DATA_EXCEEDED,
            f"sweep {sweep_id}: {no_data}/{total} cells ({fraction:.1%}) returned no data, "
            f"above the {self.max_no_data_fraction:.1%} threshold. The sweep stopped and "
            "those cells are NOT marked complete.",
            source=sweep_id,
            dedupe_key=f"no_data:{sweep_id}",
            no_data=no_data,
            total=total,
            fraction=round(fraction, 4),
        )


def _coerce_outcome(raw: Any) -> CellOutcome:
    """Normalise whatever the compute function returned.

    ``None`` is NO_DATA.  Not done.  This is the coercion V1 got wrong by
    marking done first and inspecting the result afterwards.
    """
    if raw is None:
        return CellOutcome.no_data("compute returned None")
    if isinstance(raw, CellOutcome):
        return raw
    if isinstance(raw, Mapping):
        if not raw:
            return CellOutcome.no_data("compute returned an empty mapping")
        status = raw.get("status")
        if isinstance(status, CellStatus):
            return CellOutcome(status, raw.get("result"), str(raw.get("reason", "")))
        if isinstance(status, str):
            try:
                return CellOutcome(CellStatus(status), raw.get("result"), str(raw.get("reason", "")))
            except ValueError:
                pass
        return CellOutcome.done(raw)
    raise TypeError(
        f"a sweep compute function must return CellOutcome, a Mapping or None; got "
        f"{type(raw).__name__}. Guessing whether an arbitrary object counts as success is "
        "exactly the ambiguity that produced V1's false completion marker."
    )


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


@dataclass
class ResearchWorkerConfig(WorkerConfig):
    kind: str = "research"
    #: Orchestrator queues this worker drains, in priority order.
    queues: tuple[str, ...] = ("research",)
    #: Jobs to run per cycle before writing a heartbeat and re-checking stop.
    #: Small, so SIGTERM is honoured promptly even under a full queue.
    jobs_per_cycle: int = 1
    idle_sleep_seconds: float = 2.0
    #: Seconds between heartbeats (and lease renewals) while a long agent
    #: cycle runs. Must be well under ``lease_ttl_seconds``.
    pulse_seconds: float = 15.0


class HeartbeatPulse:
    """Keeps a worker's heartbeat and lease alive while one long step runs.

    Used as a context manager around work that cannot return to the loop for
    minutes. The pulse thread only renews the lease and writes the heartbeat;
    it never touches the work. If the lease is lost the pulse records it and
    asks the worker to stop: the in-flight step cannot be interrupted from
    here, and the next loop iteration's own renewal then exits the worker.
    """

    def __init__(self, worker: Worker, *, interval: float, detail: str) -> None:
        if interval <= 0:
            raise ValueError("a heartbeat pulse needs a positive interval")
        self.worker = worker
        self.interval = interval
        self.detail = detail
        self.beats = 0
        self.lease_lost = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.worker.lease.renew()
                self.worker.heartbeat.write(
                    WorkerState.WORKING, fence=self.worker.lease.fence, detail=self.detail
                )
                self.beats += 1
            except LeaseLost as exc:
                self.lease_lost = str(exc)
                _log.critical("lease lost during a long step", extra={"error": str(exc)})
                self.worker.request_stop("lease lost during agent work")
                return
            except Exception as exc:  # the pulse must never kill the process
                _log.error(
                    "heartbeat pulse failed", extra={"error": f"{type(exc).__name__}: {exc}"}
                )

    def __enter__(self) -> HeartbeatPulse:
        self._thread = threading.Thread(
            target=self._run, name=f"pulse-{self.worker.worker_id}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.interval * 2))


class ResearchWorker(Worker):
    """Drains orchestrator queues in its own process.

    It adds nothing to the orchestrator's semantics: ``run_next`` already
    handles retries, backoff, dead-lettering and idempotency, and duplicating
    any of that here would give two sources of truth for whether a job ran.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        store: WorkerStore,
        config: ResearchWorkerConfig | None = None,
        *,
        agent_runtime: Any = None,
        environ: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config or ResearchWorkerConfig(), store, **kwargs)
        self.orchestrator = orchestrator
        self.processed: list[JobRecord] = []
        #: A composed :class:`~fiboki.workers.research_runtime.ResearchRuntime`,
        #: or ``None``. Left ``None`` here, :meth:`setup` composes one from the
        #: environment when ``FIBOKI_AGENT_CYCLES`` is on.
        self.agent_runtime = agent_runtime
        self._environ = environ
        self.agent_results: list[Any] = []
        self.last_pulse: HeartbeatPulse | None = None

    @property
    def rconfig(self) -> ResearchWorkerConfig:
        return self.config  # type: ignore[return-value]

    def setup(self) -> None:
        """Compose the agent research runtime when the flag asks for it.

        Imported here, not at module top, so a default research worker never
        imports the agent workflows at all. A misconfiguration raises: a flag
        that is on but cannot be honoured is a startup error, not an idle
        worker that looks healthy.
        """
        if self.agent_runtime is not None:
            return
        from fiboki.workers.research_runtime import research_runtime_from_env

        self.agent_runtime = research_runtime_from_env(
            self.orchestrator, environ=self._environ, dispatcher=self.dispatcher
        )
        if self.agent_runtime is not None:
            _log.info(
                "agent research runtime composed",
                extra={
                    "handlers": [t.value for t in self.orchestrator.handled_types()],
                    "schedule": [s.name for s in self.agent_runtime.schedule],
                },
            )

    def resume(self) -> None:
        """Nothing to reclaim: the orchestrator's ledger already holds every
        job's state, and a job left RUNNING by a crashed predecessor is
        re-queued by its own retry policy.  Recorded here explicitly so the
        absence is a decision rather than an oversight."""
        depths = {q: len(self.orchestrator.pending(q)) for q in self.rconfig.queues}
        _log.info("research worker resuming", extra={"queue_depths": depths})
        for queue, depth in depths.items():
            _metrics.record_queue_depth(queue, depth)

    def run_cycle(self) -> CycleResult:
        agent = self._run_agent_work()
        if agent is not None:
            return agent
        done = 0
        detail = ""
        for queue in self.rconfig.queues:
            _metrics.record_queue_depth(queue, len(self.orchestrator.pending(queue)))
            while done < self.rconfig.jobs_per_cycle:
                if self.stopping:
                    break
                record = self._run_one(queue)
                if record is None:
                    break
                done += 1
                detail = f"{record.spec.job_type.value}:{record.status.value}"
            if done >= self.rconfig.jobs_per_cycle:
                break
        if done == 0:
            return CycleResult.idle("no runnable jobs")
        return CycleResult.worked(done, detail)

    def _run_agent_work(self) -> CycleResult | None:
        """Due agent work, under a heartbeat pulse. ``None`` when there is none."""
        runtime = self.agent_runtime
        if runtime is None or self.stopping or not runtime.has_work():
            return None
        pulse = HeartbeatPulse(
            self, interval=self.rconfig.pulse_seconds, detail="agent research work running"
        )
        self.last_pulse = pulse
        with pulse:
            results = runtime.tick(should_stop=lambda: self.stopping)
        self.agent_results.extend(results)
        if not results:
            return None
        return CycleResult.worked(
            len(results), "; ".join(r.summary() for r in results)[:4000]
        )

    def _run_one(self, queue: str) -> JobRecord | None:
        correlation = new_correlation_id("job")
        with bind(correlation_id=correlation, queue=queue):
            started = time.monotonic()
            record = self.orchestrator.run_next(queue)
            if record is None:
                return None
            job_type = record.spec.job_type.value
            _metrics.record_job_started(queue, job_type)
            _metrics.record_job_finished(
                queue, job_type, record.status.value, time.monotonic() - started
            )
            self.processed.append(record)
            if record.status is JobStatus.DEAD_LETTER:
                _log.error(
                    "job dead-lettered",
                    extra={"job_id": record.job_id, "job_type": job_type, "error": record.error},
                )
                if self.dispatcher is not None:
                    self.dispatcher.fire(
                        AlertEvent.JOB_DEAD_LETTERED,
                        f"job {record.job_id} ({job_type}) dead-lettered after "
                        f"{record.attempt_count} attempts: {record.error}",
                        source=self.worker_id,
                        job_id=record.job_id,
                        job_type=job_type,
                    )
            elif record.status is JobStatus.FAILED:
                _log.warning(
                    "job failed; re-queued with backoff",
                    extra={"job_id": record.job_id, "job_type": job_type, "error": record.error},
                )
            else:
                _log.info(
                    "job finished",
                    extra={
                        "job_id": record.job_id,
                        "job_type": job_type,
                        "status": record.status.value,
                    },
                )
            return record


def queue_depths(orchestrator: Orchestrator, queues: Iterable[str]) -> dict[str, int]:
    return {q: len(orchestrator.pending(q)) for q in queues}
