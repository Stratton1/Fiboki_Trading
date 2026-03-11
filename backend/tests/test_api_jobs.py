"""Tests for the jobs API endpoints."""

import time

import pytest


@pytest.fixture(autouse=True)
def _reset_engine():
    """Reset the job engine between tests."""
    from fibokei.jobs.engine import reset_job_engine
    reset_job_engine()
    yield
    reset_job_engine()


def test_list_jobs_empty(auth_client):
    resp = auth_client.get("/api/v1/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["active_count"] == 0


def test_list_jobs_unauthenticated(api_client):
    resp = api_client.get("/api/v1/jobs")
    assert resp.status_code == 401


def test_get_job_not_found(auth_client):
    resp = auth_client.get("/api/v1/jobs/nonexistent")
    assert resp.status_code == 404


def test_cancel_nonexistent_job(auth_client):
    resp = auth_client.post("/api/v1/jobs/nonexistent/cancel")
    assert resp.status_code == 404


def test_jobs_created_via_engine(auth_client):
    """Submit a job through the engine and verify it shows up in the API."""
    from fibokei.jobs.engine import get_job_engine

    engine = get_job_engine()

    def quick_job(progress_callback=None):
        if progress_callback:
            progress_callback(100)
        return {"result": "done"}

    info = engine.submit("test", "test label", quick_job)
    info._future.result(timeout=5)
    time.sleep(0.05)

    resp = auth_client.get("/api/v1/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["job_id"] == info.job_id
    assert data["items"][0]["state"] == "completed"

    # Get single job
    resp = auth_client.get(f"/api/v1/jobs/{info.job_id}")
    assert resp.status_code == 200
    job = resp.json()
    assert job["label"] == "test label"
    assert job["result"] == {"result": "done"}


def test_filter_by_type(auth_client):
    from fibokei.jobs.engine import get_job_engine

    engine = get_job_engine()

    def noop(progress_callback=None):
        return {}

    a = engine.submit("backtest", "bt", noop)
    b = engine.submit("research", "res", noop)
    a._future.result(timeout=5)
    b._future.result(timeout=5)
    time.sleep(0.05)

    resp = auth_client.get("/api/v1/jobs?job_type=backtest")
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["job_type"] == "backtest"


def test_filter_by_state(auth_client):
    from fibokei.jobs.engine import get_job_engine

    engine = get_job_engine()

    def noop(progress_callback=None):
        return {}

    info = engine.submit("test", "done", noop)
    info._future.result(timeout=5)
    time.sleep(0.05)

    resp = auth_client.get("/api/v1/jobs?state=completed")
    data = resp.json()
    assert len(data["items"]) == 1

    resp = auth_client.get("/api/v1/jobs?state=running")
    data = resp.json()
    assert len(data["items"]) == 0
