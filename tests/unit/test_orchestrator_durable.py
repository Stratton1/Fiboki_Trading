"""The orchestrator's job ledger is DURABLE (audit F, P1-8).

It used to be two Python dicts. A restart -- a crash and a launchd restart, a
reboot, an operator's ``bootout`` -- lost every queued job and every
idempotency key, so a re-fired schedule re-ran finished work and a re-run
validation hit ``HoldoutAlreadyConsumed``. These tests pin the replacement:

* jobs submitted by one PROCESS are run by the next one (a real subprocess, not
  a second object in the same interpreter);
* an idempotency key submitted before a restart still deduplicates after it;
* the key is UNIQUE in the database, so two processes cannot both insert it;
* a claim is atomic: two orchestrators on one file never run the same job;
* the lease fence: a zombie with an older fence can neither claim nor record;
* a job left RUNNING by a dead worker returns to its own retry policy.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fiboki.agents.orchestrator import (
    FencedOut,
    JobContext,
    JobSpec,
    JobStatus,
    JobType,
    ManualClock,
    Orchestrator,
    OrchestratorError,
)

REPO = Path(__file__).resolve().parents[2]


def _ok(ctx: JobContext) -> Mapping[str, Any]:
    return {"echo": dict(ctx.payload), "attempt": ctx.attempt}


def _spec(**overrides: Any) -> JobSpec:
    base: dict[str, Any] = {
        "job_type": JobType.BACKTEST,
        "queue": "research",
        "payload": {"strategy_id": "s1", "instrument": "EURUSD"},
    }
    base.update(overrides)
    return JobSpec(**base)


def _run_in_subprocess(code: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_jobs_submitted_by_one_process_survive_into_the_next(tmp_path: Path) -> None:
    """The restart test. Process A submits and exits; process B runs the jobs."""
    ledger = tmp_path / "state" / "jobs.sqlite"
    _run_in_subprocess(
        f"""
        from fiboki.agents.orchestrator import JobSpec, JobType, Orchestrator
        orch = Orchestrator(path={str(ledger)!r})
        orch.register_handler(JobType.BACKTEST, lambda ctx: {{"never": "run here"}})
        for i in range(3):
            orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research",
                                payload={{"cell": i}}, idempotency_key=f"cell-{{i}}"))
        orch.close()
        """
    )
    out = _run_in_subprocess(
        f"""
        import json
        from fiboki.agents.orchestrator import JobSpec, JobType, Orchestrator
        orch = Orchestrator(path={str(ledger)!r})
        orch.register_handler(JobType.BACKTEST, lambda ctx: {{"ran": ctx.payload["cell"]}})
        before = len(orch.pending("research"))
        again = orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research",
                                    payload={{"cell": 0}}, idempotency_key="cell-0"))
        done = orch.drain("research")
        print(json.dumps({{
            "before": before,
            "dedup": again.deduplicated,
            "results": [r.result for r in done],
            "counts": orch.counts(),
        }}))
        """
    )
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["before"] == 3
    assert payload["dedup"] is True, "an idempotency key must survive a restart"
    assert payload["results"] == [{"ran": 0}, {"ran": 1}, {"ran": 2}]
    assert payload["counts"]["succeeded"] == 3 and payload["counts"]["pending"] == 0


def test_the_ledger_file_is_wal_with_a_unique_idempotency_key(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    orch = Orchestrator(path=path)
    orch.register_handler(JobType.BACKTEST, _ok)
    orch.submit(_spec(idempotency_key="k"))
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO job (job_id, idempotency_key, queue, job_type, spec_json, "
                "status, submitted_at, queue_seq) VALUES ('x','k','q','backtest','{}',"
                "'pending','2026-01-01',99)"
            )
    orch.close()


def test_two_processes_racing_one_key_insert_exactly_once(tmp_path: Path) -> None:
    """Both read "not present" and both insert: the UNIQUE index decides."""
    path = tmp_path / "jobs.sqlite"
    first = Orchestrator(path=path)
    second = Orchestrator(path=path)
    for orch in (first, second):
        orch.register_handler(JobType.BACKTEST, _ok)
    real_by_key = second.by_key
    calls = {"n": 0}

    def stale_by_key(key: str):
        calls["n"] += 1
        if calls["n"] == 1:
            first.submit(_spec(idempotency_key=key))  # the other process wins
            return None  # ...but this one already looked
        return real_by_key(key)

    second.by_key = stale_by_key  # type: ignore[method-assign]
    record = second.submit(_spec(idempotency_key="race"))
    assert record.deduplicated is True
    assert len(first.records()) == 1


def test_a_claim_is_atomic_across_two_orchestrators_on_one_file(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    ran: list[int] = []
    lock = threading.Lock()

    def handler(ctx: JobContext) -> Mapping[str, Any]:
        with lock:
            ran.append(int(ctx.payload["cell"]))
        return {"cell": ctx.payload["cell"]}

    seed = Orchestrator(path=path)
    seed.register_handler(JobType.BACKTEST, handler)
    for i in range(40):
        seed.submit(_spec(payload={"cell": i}))

    workers = [Orchestrator(path=path) for _ in range(4)]
    for orch in workers:
        orch.register_handler(JobType.BACKTEST, handler)

    def drain(orch: Orchestrator) -> None:
        while orch.run_next("research") is not None:
            pass

    threads = [threading.Thread(target=drain, args=(o,)) for o in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(ran) == list(range(40)), "every job exactly once"
    assert seed.counts()["succeeded"] == 40


def test_a_zombie_with_an_older_fence_can_neither_claim_nor_record(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    fences = {"old": 3, "new": 4}
    zombie = Orchestrator(path=path)
    zombie.register_handler(JobType.BACKTEST, _ok)
    zombie.bind_lease("research@mac:100", lambda: fences["old"])
    zombie.submit(_spec(idempotency_key="a"))
    zombie.submit(_spec(idempotency_key="b", payload={"strategy_id": "s2"}))

    successor = Orchestrator(path=path)
    successor.register_handler(JobType.BACKTEST, _ok)
    successor.bind_lease("research@mac:200", lambda: fences["new"])
    assert successor.run_next("research") is not None  # raises the high-water mark to 4

    with pytest.raises(FencedOut, match="older than the ledger's high-water mark"):
        zombie.run_next("research")
    assert len(successor.pending("research")) == 1, "the zombie claimed nothing"


def test_a_completion_over_a_reclaimed_job_is_refused(tmp_path: Path) -> None:
    """A worker pauses mid-job; its successor recovers the job; the old one wakes."""
    path = tmp_path / "jobs.sqlite"
    successor = Orchestrator(path=path)
    successor.register_handler(JobType.BACKTEST, _ok)
    successor.bind_lease("research@mac:200", lambda: 5)
    gate = threading.Event()
    release = threading.Event()

    def paused(ctx: JobContext) -> Mapping[str, Any]:
        gate.set()
        release.wait(10)
        return {"late": True}

    zombie = Orchestrator(path=path)
    zombie.register_handler(JobType.BACKTEST, paused)
    zombie.bind_lease("research@mac:100", lambda: 4)
    zombie.submit(_spec(idempotency_key="only"))

    outcome: dict[str, Any] = {}

    def run_zombie() -> None:
        try:
            zombie.run_next("research")
        except FencedOut as exc:
            outcome["error"] = str(exc)

    thread = threading.Thread(target=run_zombie)
    thread.start()
    assert gate.wait(10)
    recovered = successor.recover_abandoned(reason="lease taken over")
    assert [r.status for r in recovered] == [JobStatus.FAILED]
    release.set()
    thread.join(10)
    assert "older than the ledger" in outcome["error"] or "no longer claimed" in outcome["error"]
    record = successor.by_key("only")
    assert record is not None and record.status is JobStatus.FAILED
    assert record.result is None, "the zombie's late result was not written"


def test_a_job_left_running_by_a_dead_worker_returns_to_its_retry_policy(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    clock = ManualClock()
    dead = Orchestrator(clock, path=path)
    dead.register_handler(JobType.BACKTEST, _ok)
    dead.bind_lease("research@mac:100", lambda: 1)
    dead.submit(_spec(idempotency_key="k", max_attempts=2, backoff_seconds=10.0))
    # Simulate the crash: the claim is written, the process dies before the result.
    with dead._tx() as conn:
        conn.execute(
            "UPDATE job SET status='running', claimed_by='research@mac:100', claim_fence=1"
        )
    dead.close()

    restarted = Orchestrator(clock, path=path)
    restarted.register_handler(JobType.BACKTEST, _ok)
    restarted.bind_lease("research@mac:200", lambda: 2)
    (first,) = restarted.recover_abandoned()
    assert first.status is JobStatus.FAILED and first.attempt_count == 1
    assert "abandoned" in first.error and "research@mac:100" in first.error
    assert restarted.run_next("research") is None, "backoff honoured"
    clock.advance(10.0)
    assert restarted.run_next("research").status is JobStatus.SUCCEEDED

    # The second abandonment exhausts max_attempts=1 -> dead letter.
    restarted.submit(_spec(idempotency_key="k2", max_attempts=1))
    with restarted._tx() as conn:
        conn.execute(
            "UPDATE job SET status='running', claimed_by='x', claim_fence=1 "
            "WHERE idempotency_key='k2'"
        )
    (second,) = restarted.recover_abandoned()
    assert second.status is JobStatus.DEAD_LETTER


def test_a_non_json_payload_is_refused_rather_than_changed(tmp_path: Path) -> None:
    orch = Orchestrator(path=tmp_path / "jobs.sqlite")
    orch.register_handler(JobType.BACKTEST, _ok)
    with pytest.raises(OrchestratorError, match="not JSON-serialisable"):
        orch.submit(_spec(payload={"when": object()}, idempotency_key="x"))


def test_the_handler_sees_the_recorded_payload_before_and_after_a_restart(tmp_path: Path) -> None:
    """A tuple is recorded as a JSON list; the handler gets the list both times."""
    seen: list[Any] = []

    def handler(ctx: JobContext) -> Mapping[str, Any]:
        seen.append(ctx.payload["legs"])
        return {}

    path = tmp_path / "jobs.sqlite"
    orch = Orchestrator(path=path)
    orch.register_handler(JobType.BACKTEST, handler)
    orch.submit(_spec(payload={"legs": (1, 2)}, idempotency_key="a"))
    orch.drain("research")
    orch.close()
    again = Orchestrator(path=path)
    again.register_handler(JobType.BACKTEST, handler)
    again.submit(_spec(payload={"legs": (3, 4)}, idempotency_key="b"))
    again.drain("research")
    assert seen == [[1, 2], [3, 4]]


def test_a_job_whose_type_has_no_handler_here_stays_queued(tmp_path: Path) -> None:
    """A process that cannot run a job type must not fail it for the one that can."""
    path = tmp_path / "jobs.sqlite"
    full = Orchestrator(path=path)
    full.register_handler(JobType.BACKTEST, _ok)
    full.register_handler(JobType.VALIDATION, _ok)
    full.submit(_spec(job_type=JobType.VALIDATION, idempotency_key="v"))
    partial = Orchestrator(path=path)
    partial.register_handler(JobType.BACKTEST, _ok)
    assert partial.run_next("research") is None
    assert full.run_next("research").status is JobStatus.SUCCEEDED
