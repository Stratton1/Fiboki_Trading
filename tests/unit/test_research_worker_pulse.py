"""Long deterministic jobs run INSIDE the heartbeat pulse (audit F, P1-10).

Before this, only agent work ran under :class:`HeartbeatPulse`. A ladder run or
a sweep cell took minutes against a 60 s lease, so the lease expired mid-job,
a second supervised worker could take it, and the heartbeat aged past the
"down" threshold while the worker was busy. The test the brief asks for: a
1 s lease and a 3 s job. Real time, real SQLite, no mocks of the lease.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fiboki.agents.orchestrator import JobContext, JobSpec, JobStatus, JobType, Orchestrator
from fiboki.workers.base import LeaseNotAcquired, WorkerLease, WorkerStore
from fiboki.workers.research_worker import ResearchWorker, ResearchWorkerConfig


def _slow(seconds: float, started: threading.Event | None = None):
    def handler(ctx: JobContext) -> Mapping[str, Any]:
        if started is not None:
            started.set()
        time.sleep(seconds)
        return {"slept": seconds, "cell": ctx.payload.get("cell")}

    return handler


def test_a_three_second_job_holds_a_one_second_lease(tmp_path: Path) -> None:
    started = threading.Event()
    orch = Orchestrator(path=tmp_path / "jobs.sqlite")
    orch.register_handler(JobType.BACKTEST, _slow(3.0, started))
    orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", payload={"cell": 1}))

    with WorkerStore.sqlite_at(tmp_path / "state.db") as store:
        worker = ResearchWorker(
            orch,
            store,
            ResearchWorkerConfig(
                max_cycles=1,
                idle_sleep_seconds=0,
                lease_ttl_seconds=1.0,  # a third of the job
                pulse_seconds=15.0,  # the production default; capped to ttl/3
            ),
            worker="research@test:1",
        )
        assert worker.pulse_interval() == pytest.approx(1.0 / 3.0)
        thread = threading.Thread(target=lambda: worker.run(install_signals=False))
        thread.start()
        assert started.wait(5.0)

        # Mid-job, twice the TTL after the claim: a second worker must STILL be
        # refused, which is only true if the lease was renewed during the job.
        time.sleep(2.0)
        intruder = WorkerLease(store, "research", "research@test:2", ttl_seconds=1.0)
        with pytest.raises(LeaseNotAcquired):
            intruder.acquire()
        row = next(r for r in store.heartbeat_rows() if r["worker_id"] == worker.worker_id)
        assert row["status"] == "working"
        assert row["detail"] == "running a research job"

        thread.join(15.0)
        assert not thread.is_alive()

    assert worker.exit_code == 0, worker.heartbeat.last_error
    assert worker.last_job_pulse is not None
    assert worker.last_job_pulse.lease_lost == ""
    assert worker.last_job_pulse.beats >= 6, worker.last_job_pulse.beats
    assert orch.counts()["succeeded"] == 1


def test_the_lease_is_renewed_between_jobs_in_one_cycle(tmp_path: Path) -> None:
    orch = Orchestrator(path=tmp_path / "jobs.sqlite")
    orch.register_handler(JobType.BACKTEST, _slow(0.05))
    for cell in range(3):
        orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", payload={"cell": cell}))
    beats: list[str] = []
    with WorkerStore.sqlite_at(tmp_path / "state.db") as store:
        worker = ResearchWorker(
            orch,
            store,
            ResearchWorkerConfig(max_cycles=1, idle_sleep_seconds=0, jobs_per_cycle=3),
            worker="research@test:1",
        )
        real = worker.pulse

        def spy(detail: str = "") -> None:
            beats.append(detail)
            real(detail)

        worker.pulse = spy  # type: ignore[method-assign]
        assert worker.run(install_signals=False) == 0
    assert [b for b in beats if b.startswith("between jobs")] == [
        "between jobs (1 done this cycle)",
        "between jobs (2 done this cycle)",
    ]
    assert orch.counts()["succeeded"] == 3


def test_resume_binds_the_fence_and_recovers_an_abandoned_job(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    first = Orchestrator(path=path)
    first.register_handler(JobType.BACKTEST, _slow(0.0))
    first.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", payload={"cell": 7},
                         backoff_seconds=0.0))
    with first._tx() as conn:
        conn.execute("UPDATE job SET status='running', claimed_by='research@dead:9', claim_fence=1")
    first.close()

    orch = Orchestrator(path=path)
    orch.register_handler(JobType.BACKTEST, _slow(0.0))
    with WorkerStore.sqlite_at(tmp_path / "state.db") as store:
        worker = ResearchWorker(
            orch, store, ResearchWorkerConfig(max_cycles=1, idle_sleep_seconds=0),
            worker="research@test:1",
        )
        assert worker.run(install_signals=False) == 0
    assert [r.job_id for r in worker.recovered] == [orch.records()[0].job_id]
    (record,) = orch.records()
    assert record.status is JobStatus.SUCCEEDED
    assert record.attempt_count == 2, "the abandoned run is counted as an attempt"
    assert "abandoned" in record.attempts[0].error
