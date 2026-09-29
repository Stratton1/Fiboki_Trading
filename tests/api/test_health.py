"""The health endpoint must be able to say 'down'.

V1's returned a literal and stayed green with the database stopped. These tests
fail if anyone reintroduces that.
"""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings


def test_health_is_not_ok_when_nothing_is_provisioned(client):
    body = client.get("/api/health").json()
    assert body["status"] in {"degraded", "down"}
    assert body["status"] != "ok"
    names = {c["name"] for c in body["checks"]}
    assert {"database", "worker_heartbeat", "migration_revision", "audit_chain"} <= names


def test_health_reports_database_down_not_absent_key(client):
    checks = {c["name"]: c for c in client.get("/api/health").json()["checks"]}
    assert checks["database"]["status"] == "down"
    assert "No FIBOKI_EXPERIMENT_DB" in checks["database"]["detail"]


def test_health_reaches_a_real_database_when_one_exists(tmp_path, api_env, monkeypatch):
    db = tmp_path / "ledger.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('abc123')")
    env = dict(api_env)
    env["FIBOKI_EXPERIMENT_DB"] = str(db)
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url="https://workstation.fiboki.test") as c:
        body = c.get("/api/health").json()
    checks = {x["name"]: x for x in body["checks"]}
    assert checks["database"]["status"] == "ok"
    assert body["migration_revision"] == "abc123"
    # A V1 alembic stamp is known but is not this code's schema: degraded, said plainly.
    assert checks["migration_revision"]["status"] == "degraded"
    assert "V1 migration abc123" in checks["migration_revision"]["detail"]


def test_health_is_ok_on_a_ledger_this_code_opened(tmp_path, api_env):
    """The real deployment case: the worker opened the ledger, which stamped the
    schema revision; the API compares it with its own and reports ok."""
    from fiboki.research.experiment import ExperimentLedger, schema_revision

    db = tmp_path / "ledger.sqlite"
    ExperimentLedger(db).close()
    env = dict(api_env)
    env["FIBOKI_EXPERIMENT_DB"] = str(db)
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url="https://workstation.fiboki.test") as c:
        body = c.get("/api/health").json()
    checks = {x["name"]: x for x in body["checks"]}
    assert body["migration_revision"] == schema_revision()
    assert checks["migration_revision"]["status"] == "ok"
    assert checks["migration_revision"]["detail"] == f"at revision {schema_revision()}"


def test_health_says_which_build_last_opened_a_mismatched_ledger(tmp_path, api_env):
    db = tmp_path / "ledger.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE schema_revision (revision TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_revision VALUES ('ledger_deadbeef0000', '2026-01-01T00:00:00+00:00')")
    env = dict(api_env)
    env["FIBOKI_EXPERIMENT_DB"] = str(db)
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url="https://workstation.fiboki.test") as c:
        body = c.get("/api/health").json()
    check = {x["name"]: x for x in body["checks"]}["migration_revision"]
    assert check["status"] == "degraded"
    assert "database at ledger_deadbeef0000, this code expects ledger_" in check["detail"]


def test_health_goes_down_when_the_database_disappears(tmp_path, api_env):
    db = tmp_path / "ledger.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x INT)")
    env = dict(api_env)
    env["FIBOKI_EXPERIMENT_DB"] = str(db)
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url="https://workstation.fiboki.test") as c:
        before = {x["name"]: x for x in c.get("/api/health").json()["checks"]}
        assert before["database"]["status"] == "ok"
        db.unlink()
        after = {x["name"]: x for x in c.get("/api/health").json()["checks"]}
    # The probe re-runs per request; it does not cache the first green answer.
    assert after["database"]["status"] == "down"


def test_worker_heartbeat_null_is_not_zero(client):
    body = client.get("/api/health").json()
    assert body["worker_heartbeat_age_seconds"] is None


def test_build_sha_is_reported(client):
    assert client.get("/api/version").json()["build_sha"] == "deadbeef"


def test_correlation_id_is_returned_on_every_response(client):
    response = client.get("/api/health")
    assert response.headers["X-Correlation-Id"]
