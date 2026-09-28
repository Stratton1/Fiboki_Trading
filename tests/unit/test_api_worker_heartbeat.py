"""The API reads worker liveness from the worker's heartbeat TABLE, not a file mtime.

The defect: ``Platform.worker_heartbeat_age_seconds`` returned
``time.time() - st_mtime`` of the worker's SQLite store. The store runs in WAL
mode, where a committed write lands in ``state.db-wal`` and the main file's
mtime can stay hours old, so the API reported a beating worker as DOWN.

The schema is not restated here: every database is built by
``fiboki.workers.base.WorkerStore``, whose ``create_all`` uses
``WORKER_HEARTBEAT``, and rows are inserted through that same ``Table``.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from fiboki.api.platform import Platform, read_worker_heartbeat
from fiboki.api.settings import load_settings
from fiboki.workers.base import WORKER_HEARTBEAT, Heartbeat, WorkerStore

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _store(path: Path, beats: dict[str, datetime], status: str = "idle") -> None:
    with WorkerStore.sqlite_at(path) as store, store.engine.begin() as conn:
        for worker, beat_at in beats.items():
            conn.execute(
                insert(WORKER_HEARTBEAT).values(
                    worker_id=worker,
                    kind=worker.split("@", 1)[0],
                    beat_at=beat_at,
                    started_at=beat_at - timedelta(hours=1),
                    status=status,
                )
            )


def test_age_is_computed_from_beat_at_against_the_api_clock(tmp_path):
    db = tmp_path / "state.db"
    _store(db, {"research@host:1": NOW - timedelta(seconds=42)})
    reading = read_worker_heartbeat(db, stale_after_seconds=120, now=NOW)
    assert reading.state == "ok"
    assert reading.reason == "sqlite"
    assert reading.age_seconds == pytest.approx(42.0, abs=0.01)
    assert reading.newest_beat_at == NOW - timedelta(seconds=42)


def test_wal_store_with_stale_mtime_but_fresh_beat_is_alive(tmp_path):
    """The reported defect, reproduced with a real worker Heartbeat write."""
    db = tmp_path / "state.db"
    store = WorkerStore.sqlite_at(db)
    try:
        with sqlite3.connect(db) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        beat = Heartbeat(store, "research@host:7", "research")
        beat.write("idle")
        hour_ago = time.time() - 3600
        os.utime(db, (hour_ago, hour_ago))
        beat.write("working")  # lands in the WAL; the main file's mtime stays old
        assert time.time() - db.stat().st_mtime > 3000, "precondition: mtime is stale"

        reading = read_worker_heartbeat(db, stale_after_seconds=120)
        assert reading.state == "ok", reading.detail
        assert reading.reason == "sqlite"
        assert reading.age_seconds is not None and reading.age_seconds < 60
        assert [w.worker_id for w in reading.workers] == ["research@host:7"]
    finally:
        store.close()


def test_missing_file_is_none_not_zero(tmp_path):
    reading = read_worker_heartbeat(tmp_path / "nope.db", stale_after_seconds=120)
    assert reading.state == "absent"
    assert reading.age_seconds is None
    assert reading.reason == "file_missing"
    assert read_worker_heartbeat(None, stale_after_seconds=120).age_seconds is None


def test_store_without_the_table_is_absent(tmp_path):
    db = tmp_path / "other.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (x INT)")
    reading = read_worker_heartbeat(db, stale_after_seconds=120)
    assert (reading.state, reading.reason, reading.age_seconds) == (
        "absent",
        "no_heartbeat_table",
        None,
    )


def test_empty_table_is_absent(tmp_path):
    db = tmp_path / "state.db"
    WorkerStore.sqlite_at(db).close()
    reading = read_worker_heartbeat(db, stale_after_seconds=120)
    assert (reading.state, reading.reason, reading.age_seconds) == ("absent", "no_rows", None)


def test_unreadable_store_is_absent_with_the_reason(tmp_path):
    db = tmp_path / "state.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)
    reading = read_worker_heartbeat(db, stale_after_seconds=120)
    assert reading.state == "absent"
    assert reading.reason == "unreadable"
    assert reading.age_seconds is None


def test_stale_at_the_threshold_itself(tmp_path):
    db = tmp_path / "state.db"
    _store(db, {"research@host:1": NOW - timedelta(seconds=120)})
    reading = read_worker_heartbeat(db, stale_after_seconds=120, now=NOW)
    assert reading.state == "stale"
    assert reading.age_seconds == pytest.approx(120.0)


def test_every_worker_is_named_with_its_own_age(tmp_path):
    db = tmp_path / "state.db"
    _store(
        db,
        {
            "research@host:1": NOW - timedelta(seconds=5),
            "live@host:2": NOW - timedelta(seconds=900),
        },
    )
    reading = read_worker_heartbeat(db, stale_after_seconds=120, now=NOW)
    assert reading.state == "ok"  # the newest beat decides the overall age
    assert reading.age_seconds == pytest.approx(5.0)
    ages = {w.worker_id: w.age_seconds for w in reading.workers}
    assert ages == {"research@host:1": pytest.approx(5.0), "live@host:2": pytest.approx(900.0)}
    assert {w.kind for w in reading.workers} == {"research", "live"}


def test_non_sqlite_file_uses_a_labelled_mtime_fallback(tmp_path):
    touch = tmp_path / "worker.heartbeat"
    touch.write_text("")
    old = time.time() - 600
    os.utime(touch, (old, old))
    reading = read_worker_heartbeat(touch, stale_after_seconds=120)
    assert reading.reason == "mtime_fallback"
    assert reading.state == "stale"
    assert "last resort" in reading.detail


def test_platform_and_api_report_the_table_age(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from fiboki.api.app import create_app

    monkeypatch.delenv("FIBOKI_PAPER_ROOT", raising=False)
    db = tmp_path / "state.db"
    store = WorkerStore.sqlite_at(db)
    Heartbeat(store, "research@host:3", "research").write("idle")
    store.close()
    hour_ago = time.time() - 3600
    os.utime(db, (hour_ago, hour_ago))

    env = {
        "FIBOKI_STATE_DIR": str(tmp_path / "state"),
        "FIBOKI_WORKER_HEARTBEAT": str(db),
        "FIBOKI_SESSION_SECRET": "test-secret-not-for-production",
    }
    settings = load_settings(env)
    platform = Platform(settings)
    age = platform.worker_heartbeat_age_seconds()
    assert age is not None and age < 60
    worker = next(s for s in platform.data_sources() if s.name == "paper_worker")
    assert worker.healthy and worker.detail.startswith("[ok/sqlite]")

    app = create_app(settings, configure_logs=False)
    with TestClient(app, base_url="https://workstation.fiboki.test") as client:
        health = client.get("/api/health").json()
        rows = client.get("/api/system/workers").json()
    checks = {c["name"]: c for c in health["checks"]}
    assert checks["worker_heartbeat"]["status"] == "ok"
    assert health["worker_heartbeat_age_seconds"] < 60
    assert [r["name"] for r in rows["items"]] == ["research@host:3"]
    assert rows["items"][0]["state"] == "running"


def test_api_reports_never_started_with_a_null_age(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from fiboki.api.app import create_app

    monkeypatch.delenv("FIBOKI_PAPER_ROOT", raising=False)
    settings = load_settings(
        {
            "FIBOKI_STATE_DIR": str(tmp_path / "state"),
            "FIBOKI_WORKER_HEARTBEAT": str(tmp_path / "missing.db"),
        }
    )
    app = create_app(settings, configure_logs=False)
    with TestClient(app, base_url="https://workstation.fiboki.test") as client:
        health = client.get("/api/health").json()
        rows = client.get("/api/system/workers").json()
    assert health["worker_heartbeat_age_seconds"] is None
    (row,) = rows["items"]
    assert row["state"] == "never_started"
    assert row["heartbeat_age"]["value"] is None
    assert "file_missing" in row["detail"]
