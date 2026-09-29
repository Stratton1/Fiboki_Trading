"""/api/health edge cases: the stale threshold, an unreadable store, a broken journal."""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import insert

from fiboki.api.app import create_app
from fiboki.api.health import build_health, heartbeat_check
from fiboki.api.platform import read_worker_heartbeat
from fiboki.api.settings import load_settings
from fiboki.workers.base import WORKER_HEARTBEAT, WorkerStore
from tests.api.conftest import ORIGIN

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"


def _beat(db: Path, age_seconds: float) -> None:
    with WorkerStore.sqlite_at(db) as store, store.engine.begin() as conn:
        at = NOW - timedelta(seconds=age_seconds)
        conn.execute(
            insert(WORKER_HEARTBEAT).values(
                worker_id="live@mac:1", kind="live", beat_at=at,
                started_at=at - timedelta(hours=1), status="idle",
            )
        )


def _checks(report) -> dict:
    return {c.name: c for c in report.checks}


def test_a_beat_exactly_at_the_threshold_is_stale_in_health_too(tmp_path, api_env, monkeypatch):
    """platform.py says age >= threshold is stale; health used to say age > threshold."""
    db = tmp_path / "worker.db"
    _beat(db, 120.0)
    env = {**api_env, "FIBOKI_WORKER_HEARTBEAT": str(db)}
    app = create_app(load_settings(env), configure_logs=False)
    platform, settings = app.state.platform, app.state.settings
    reading = read_worker_heartbeat(db, stale_after_seconds=120.0, now=NOW)
    assert reading.state == "stale" and reading.age_seconds == 120.0
    monkeypatch.setattr(type(platform), "worker_heartbeat", lambda self: reading)
    check = _checks(build_health(platform, settings))["worker_heartbeat"]
    assert check.status == "down"
    assert "120s old (stale at 120s)" in check.detail


def test_just_under_the_threshold_is_ok(tmp_path):
    db = tmp_path / "worker.db"
    _beat(db, 119.0)
    reading = read_worker_heartbeat(db, stale_after_seconds=120.0, now=NOW)
    assert heartbeat_check(reading, 120.0)[0] == "ok"


def test_an_unreadable_store_is_described_as_unreadable(tmp_path, api_env):
    db = tmp_path / "worker.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)
    env = {**api_env, "FIBOKI_WORKER_HEARTBEAT": str(db)}
    with TestClient(create_app(load_settings(env), configure_logs=False), base_url=ORIGIN) as c:
        body = c.get("/api/health").json()
    check = {x["name"]: x for x in body["checks"]}["worker_heartbeat"]
    assert check["status"] == "down"
    assert "unreadable" in check["detail"]
    assert "UNKNOWN" in check["detail"]
    assert "has ever been written" not in check["detail"]
    assert body["worker_heartbeat_age_seconds"] is None


def test_a_missing_store_is_described_as_never_written(client):
    body = client.get("/api/health").json()
    check = {x["name"]: x for x in body["checks"]}["worker_heartbeat"]
    assert "No worker heartbeat has ever been written" in check["detail"]
    assert "[file_missing]" in check["detail"]


def test_a_broken_journal_degrades_health(tmp_path, api_env):
    root = tmp_path / "paper"
    shutil.copytree(FIXTURE, root)
    broken = root / "zz_broken_session"
    broken.mkdir()
    (broken / "summary.json").write_text(json.dumps({"account_ccy": "USD"}))  # no provenance
    env = {**api_env, "FIBOKI_PAPER_ROOT": str(root)}
    with TestClient(create_app(load_settings(env), configure_logs=False), base_url=ORIGIN) as c:
        body = c.get("/api/health").json()
    check = {x["name"]: x for x in body["checks"]}["paper_journal"]
    assert check["status"] == "degraded"
    assert "zz_broken_session" in check["detail"]
    assert body["status"] != "ok"


def test_a_journal_with_no_readable_session_says_unavailable_not_empty(tmp_path, api_env):
    root = tmp_path / "paper"
    broken = root / "only_session"
    broken.mkdir(parents=True)
    (broken / "summary.json").write_text("{not json")
    env = {**api_env, "FIBOKI_PAPER_ROOT": str(root)}
    app = create_app(load_settings(env), configure_logs=False)
    check = _checks(build_health(app.state.platform, app.state.settings))["paper_journal"]
    assert check.status == "degraded"
    assert "UNAVAILABLE, not empty" in check.detail


def test_a_clean_journal_is_ok_and_no_journal_adds_no_check(tmp_path, api_env):
    root = tmp_path / "paper"
    shutil.copytree(FIXTURE, root)
    app = create_app(load_settings({**api_env, "FIBOKI_PAPER_ROOT": str(root)}), configure_logs=False)
    assert _checks(build_health(app.state.platform, app.state.settings))["paper_journal"].status == "ok"
    bare = create_app(load_settings(api_env), configure_logs=False)
    assert "paper_journal" not in _checks(build_health(bare.state.platform, bare.state.settings))
