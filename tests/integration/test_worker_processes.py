"""Real OS processes, real signals, real contention.

The in-process tests in ``tests/unit/test_worker_base.py`` prove the logic.
These prove the thing the logic is FOR: that a worker is a process with its own
pid and its own exit code, that a second one started by mistake exits instead
of racing, and that SIGTERM produces a clean shutdown rather than a job stuck
in RUNNING behind a dead process.

V1 could not have had these tests, because there was no process to send a
signal to -- the worker was a daemon thread inside the API.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

pytestmark = pytest.mark.timeout(120)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["FIBOKI_LOG_FORMAT"] = "json"
    return env


def _worker_script(db: Path, worker: str, *, cycles: int, cycle_seconds: float = 0.05) -> str:
    return textwrap.dedent(
        f"""
        import json, sys, time
        from fiboki.workers.base import Worker, WorkerConfig, WorkerStore, CycleResult

        class Demo(Worker):
            def run_cycle(self):
                time.sleep({cycle_seconds})
                print(json.dumps({{"event": "cycle", "n": self.heartbeat.cycle}}), flush=True)
                return CycleResult.worked(1)

            def teardown(self):
                print(json.dumps({{"event": "teardown"}}), flush=True)

            def on_abandon(self):
                print(json.dumps({{"event": "abandoned"}}), flush=True)

        store = WorkerStore.sqlite_at({str(db)!r})
        config = WorkerConfig(
            kind="research",
            max_cycles={cycles},
            idle_sleep_seconds=0.05,
            busy_sleep_seconds=0.05,
            lease_ttl_seconds=30.0,
            shutdown_grace_seconds=10.0,
        )
        worker = Demo(config, store, worker={worker!r})
        print(json.dumps({{"event": "starting", "worker": worker.worker_id}}), flush=True)
        code = worker.run()
        print(json.dumps({{"event": "exited", "code": code}}), flush=True)
        sys.exit(code)
        """
    )


def _spawn(script: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Its own process group, so a signal to the child does not reach pytest.
        start_new_session=True,
    )


def _wait_for_line(proc: subprocess.Popen, predicate, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()  # type: ignore[union-attr]
        if not line:
            if proc.poll() is not None:
                raise AssertionError(f"process exited early (code {proc.returncode})")
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if predicate(payload):
            return payload
    raise AssertionError(f"timed out waiting for a line after {timeout}s")


def test_a_second_worker_process_exits_rather_than_racing(tmp_path):
    """Start TWO. The second must exit 75 and do no work.

    This is the V1 duplicate-order failure, reproduced at the process level:
    both workers shared a hardcoded id, both drained the same queue, and each
    refreshed the single heartbeat row so the duplication was invisible.
    """
    db = tmp_path / "state.db"
    first = _spawn(_worker_script(db, "a@host:1", cycles=60, cycle_seconds=0.15))
    try:
        _wait_for_line(first, lambda p: p.get("event") == "cycle")

        second = _spawn(_worker_script(db, "b@host:2", cycles=5))
        out, err = second.communicate(timeout=60)

        assert second.returncode == 75, (
            f"the second worker should exit 75 (lease held); got {second.returncode}\n"
            f"stdout={out}\nstderr={err}"
        )
        # It must not have run a single cycle.
        assert '"event": "cycle"' not in out
        # And it must say so LOUDLY on stderr, where a supervisor's log picks
        # it up, not only in a structured log somebody has to go looking for.
        assert "REFUSING TO START" in err
        assert "a@host:1" in err
    finally:
        first.send_signal(signal.SIGTERM)
        try:
            first.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover
            first.kill()


def test_sigterm_shuts_the_worker_down_gracefully(tmp_path):
    """SIGTERM: finish the cycle, tear down, release the lease, exit 0."""
    db = tmp_path / "state.db"
    proc = _spawn(_worker_script(db, "a@host:1", cycles=0, cycle_seconds=0.2))
    try:
        _wait_for_line(proc, lambda p: p.get("event") == "cycle")
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()
        raise

    assert proc.returncode == 0, f"expected a clean exit; stderr={err}"
    # teardown ran -- the shutdown was orderly, not a kill.
    assert '"event": "teardown"' in out
    # ...and the cycle it was in the middle of was FINISHED, not abandoned.
    assert '"event": "abandoned"' not in out

    from fiboki.workers.base import WorkerStore

    store = WorkerStore.sqlite_at(db)
    try:
        rows = store.heartbeat_rows()
        assert rows[0]["status"] == "stopped"
        assert rows[0]["cycles_ok"] >= 1
        # The lease is released, so a restart is immediate rather than waiting
        # out the 30s TTL.
        lease = store.lease_rows()[0]
        from datetime import UTC, datetime

        from fiboki.workers.base import _as_utc

        assert _as_utc(lease["expires_at"]) <= datetime.now(tz=UTC)
    finally:
        store.close()


def test_a_killed_worker_releases_its_lease_by_expiry(tmp_path):
    """SIGKILL cannot run a handler, so the TTL is the only recovery path."""
    db = tmp_path / "state.db"
    proc = _spawn(_worker_script(db, "a@host:1", cycles=0, cycle_seconds=0.1))
    try:
        _wait_for_line(proc, lambda p: p.get("event") == "cycle")
        proc.kill()
        proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()
        raise

    from datetime import UTC, datetime, timedelta

    from fiboki.workers.base import WorkerLease, WorkerStore, _as_utc

    store = WorkerStore.sqlite_at(db)
    try:
        lease = store.lease_rows()[0]
        expires = _as_utc(lease["expires_at"])
        assert expires is not None
        # Still held -- correctly, because we cannot distinguish "killed" from
        # "paused" and taking a live worker's lease is the worse error.
        assert expires > datetime.now(tz=UTC)
        assert expires <= datetime.now(tz=UTC) + timedelta(seconds=31)

        # An operator who KNOWS the machine is gone can force it.
        from fiboki.workers.base import force_release

        assert force_release(store, "research")
        assert WorkerLease(store, "research", "b@host:2").acquire().holder == "b@host:2"
    finally:
        store.close()
