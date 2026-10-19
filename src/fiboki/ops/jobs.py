"""The ops job queue: requests the API records and a separate process runs.

"Never start a worker inside the API." A reconciliation re-runs the backtest
engine over a session's bars, which is seconds of CPU and reads the data
store; the API therefore only RECORDS the request (who, why, when) and a
separate process, ``fiboki ops jobs``, claims and runs it and writes the
result where the API reads it. Until that process runs, the job says
``queued``, which is the truth.

The queue is one append-only file (``<ops_dir>/jobs.jsonl``, durable, torn
tails quarantined): a job is its LAST row by ``job_id``. Results are written
atomically to ``<ops_dir>/reconciliation/<session>/<job_id>.json`` plus
``latest.json``.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fiboki.core.durable import durable_append, read_payloads, read_record, write_record_atomic

__all__ = [
    "JOB_KINDS",
    "OpsJob",
    "OpsJobQueue",
    "reconciliation_dir",
]

JOB_KINDS = ("reconcile",)
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True, slots=True)
class OpsJob:
    job_id: str
    kind: str
    session_id: str
    status: str  # queued | running | succeeded | failed
    requested_by: str
    reason: str
    requested_at: str
    updated_at: str
    result_path: str = ""
    error: str = ""
    runner: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def reconciliation_dir(ops_dir: str | Path, session_id: str) -> Path:
    return Path(ops_dir) / "reconciliation" / _SAFE.sub("_", session_id)


class OpsJobQueue:
    def __init__(self, ops_dir: str | Path) -> None:
        self.ops_dir = Path(ops_dir)
        self.path = self.ops_dir / "jobs.jsonl"

    # -- reads -----------------------------------------------------------------

    def jobs(self) -> list[OpsJob]:
        if not self.path.exists():
            return []
        latest: dict[str, OpsJob] = {}
        order: list[str] = []
        for payload in read_payloads(self.path):
            row = json.loads(payload)
            job = OpsJob(**{k: row[k] for k in OpsJob.__dataclass_fields__ if k in row})
            if job.job_id not in latest:
                order.append(job.job_id)
            latest[job.job_id] = job
        return [latest[j] for j in order]

    def for_session(self, session_id: str, kind: str = "reconcile") -> list[OpsJob]:
        return [j for j in self.jobs() if j.session_id == session_id and j.kind == kind]

    def pending(self) -> list[OpsJob]:
        return [j for j in self.jobs() if j.status == "queued"]

    # -- writes ----------------------------------------------------------------

    def _write(self, job: OpsJob) -> OpsJob:
        self.ops_dir.mkdir(parents=True, exist_ok=True)
        durable_append(self.path, json.dumps(asdict(job), sort_keys=True, default=str))
        return job

    def request(self, kind: str, session_id: str, *, requested_by: str, reason: str) -> OpsJob:
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown ops job kind {kind!r}; known {JOB_KINDS}")
        open_ = [j for j in self.for_session(session_id, kind) if j.status in ("queued", "running")]
        if open_:
            return open_[-1]  # idempotent: one outstanding request per session and kind
        stamp = _now()
        return self._write(
            OpsJob(
                job_id=f"ops_{uuid.uuid4().hex[:12]}", kind=kind, session_id=session_id,
                status="queued", requested_by=requested_by, reason=reason,
                requested_at=stamp, updated_at=stamp,
            )
        )

    def transition(self, job: OpsJob, status: str, **changes: Any) -> OpsJob:
        data = {**asdict(job), **changes, "status": status, "updated_at": _now()}
        return self._write(OpsJob(**data))

    # -- results ---------------------------------------------------------------

    def write_result(self, job: OpsJob, body: dict[str, Any]) -> Path:
        directory = reconciliation_dir(self.ops_dir, job.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        text = json.dumps({**body, "job_id": job.job_id}, sort_keys=True, default=str)
        path = directory / f"{job.job_id}.json"
        write_record_atomic(path, text)
        write_record_atomic(directory / "latest.json", text)
        return path

    def latest_result(self, session_id: str) -> dict[str, Any] | None:
        path = reconciliation_dir(self.ops_dir, session_id) / "latest.json"
        if not path.exists():
            return None
        return json.loads(read_record(path))
