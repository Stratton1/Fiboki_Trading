"""Tests for research API endpoints."""

import time


def _wait_for_job(api_client, auth_headers, job_id, timeout=30):
    """Poll a job until it completes or fails."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = api_client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        data = resp.json()
        if data["state"] in ("completed", "failed"):
            return data
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


def _run_research_and_wait(api_client, auth_headers):
    """Submit research and wait for the async job to finish."""
    req_data = {
        "strategy_ids": ["bot01_sanyaku"],
        "instruments": ["EURUSD"],
        "timeframes": ["H1"],
        "data_dir": "../data/fixtures",
    }
    response = api_client.post("/api/v1/research/run", json=req_data, headers=auth_headers)
    assert response.status_code == 200
    job = response.json()
    assert job["job_id"]
    assert job["job_type"] == "research"
    return _wait_for_job(api_client, auth_headers, job["job_id"])


def test_run_research(api_client, auth_headers):
    job = _run_research_and_wait(api_client, auth_headers)
    assert job["state"] == "completed"
    result = job["result"]
    assert result["run_id"]
    assert result["total_combinations"] == 1
    assert result["completed"] == 1


def test_get_rankings(api_client, auth_headers):
    _run_research_and_wait(api_client, auth_headers)

    response = api_client.get("/api/v1/research/rankings", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["composite_score"] > 0
    assert data[0]["rank"] == 1


def test_compare_combinations(api_client, auth_headers):
    _run_research_and_wait(api_client, auth_headers)

    compare_data = {"combos": ["bot01_sanyaku:EURUSD:H1"]}
    response = api_client.post(
        "/api/v1/research/compare", json=compare_data, headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["strategy_id"] == "bot01_sanyaku"


def test_compare_not_found(api_client, auth_headers):
    compare_data = {"combos": ["nonexistent:EURUSD:H1"]}
    response = api_client.post(
        "/api/v1/research/compare", json=compare_data, headers=auth_headers
    )
    assert response.status_code == 404
