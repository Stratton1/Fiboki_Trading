"""Tests for saved shortlist and result deletion API endpoints."""

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
    return _wait_for_job(api_client, auth_headers, job["job_id"])


# ── Shortlist CRUD ───────────────────────────────────────────


def test_shortlist_empty_initially(api_client, auth_headers):
    response = api_client.get("/api/v1/research/shortlist", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_save_to_shortlist(api_client, auth_headers):
    entry = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "score": 0.72,
        "source_run_id": "abc123",
        "note": "Looks promising",
    }
    response = api_client.post("/api/v1/research/shortlist", json=entry, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["strategy_id"] == "bot01_sanyaku"
    assert data["instrument"] == "EURUSD"
    assert data["score"] == 0.72
    assert data["note"] == "Looks promising"
    assert data["status"] == "active"
    assert data["id"] > 0


def test_shortlist_upsert_updates_existing(api_client, auth_headers):
    """Saving the same combo again should update, not duplicate."""
    entry = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "score": 0.72,
    }
    r1 = api_client.post("/api/v1/research/shortlist", json=entry, headers=auth_headers)
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    entry["score"] = 0.85
    r2 = api_client.post("/api/v1/research/shortlist", json=entry, headers=auth_headers)
    assert r2.status_code == 201
    assert r2.json()["id"] == first_id
    assert r2.json()["score"] == 0.85

    # Only one entry in the list
    listing = api_client.get("/api/v1/research/shortlist", headers=auth_headers)
    assert len(listing.json()) == 1


def test_patch_shortlist_entry(api_client, auth_headers):
    entry = {
        "strategy_id": "bot02_kumo",
        "instrument": "GBPUSD",
        "timeframe": "H4",
        "score": 0.60,
    }
    r = api_client.post("/api/v1/research/shortlist", json=entry, headers=auth_headers)
    entry_id = r.json()["id"]

    patch = {"note": "Updated note", "status": "archived"}
    r2 = api_client.patch(f"/api/v1/research/shortlist/{entry_id}", json=patch, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["note"] == "Updated note"
    assert r2.json()["status"] == "archived"


def test_patch_shortlist_not_found(api_client, auth_headers):
    r = api_client.patch("/api/v1/research/shortlist/99999", json={"note": "x"}, headers=auth_headers)
    assert r.status_code == 404


def test_delete_shortlist_entry(api_client, auth_headers):
    entry = {
        "strategy_id": "bot03_tenkan",
        "instrument": "USDJPY",
        "timeframe": "D1",
        "score": 0.55,
    }
    r = api_client.post("/api/v1/research/shortlist", json=entry, headers=auth_headers)
    entry_id = r.json()["id"]

    r2 = api_client.delete(f"/api/v1/research/shortlist/{entry_id}", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["deleted"] == entry_id

    # Verify gone
    listing = api_client.get("/api/v1/research/shortlist", headers=auth_headers)
    ids = [e["id"] for e in listing.json()]
    assert entry_id not in ids


def test_delete_shortlist_not_found(api_client, auth_headers):
    r = api_client.delete("/api/v1/research/shortlist/99999", headers=auth_headers)
    assert r.status_code == 404


# ── Research Runs + Result Deletion ──────────────────────────


def test_list_research_runs(api_client, auth_headers):
    job = _run_research_and_wait(api_client, auth_headers)
    assert job["state"] == "completed"

    response = api_client.get("/api/v1/research/runs", headers=auth_headers)
    assert response.status_code == 200
    runs = response.json()
    assert len(runs) >= 1
    assert "run_id" in runs[0]
    assert "result_count" in runs[0]
    assert runs[0]["result_count"] >= 1


def test_delete_single_research_result(api_client, auth_headers):
    _run_research_and_wait(api_client, auth_headers)

    # Get a result ID
    rankings = api_client.get("/api/v1/research/rankings", headers=auth_headers).json()
    assert len(rankings) >= 1
    result_id = rankings[0]["id"]

    r = api_client.delete(f"/api/v1/research/results/{result_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["deleted"] == result_id


def test_delete_single_result_not_found(api_client, auth_headers):
    r = api_client.delete("/api/v1/research/results/99999", headers=auth_headers)
    assert r.status_code == 404


def test_delete_results_bulk(api_client, auth_headers):
    job = _run_research_and_wait(api_client, auth_headers)
    run_id = job["result"]["run_id"]

    r = api_client.delete(f"/api/v1/research/results?run_id={run_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["deleted_count"] >= 1
    assert r.json()["run_id"] == run_id

    # Verify rankings for that run are gone
    rankings = api_client.get(
        f"/api/v1/research/rankings?run_id={run_id}", headers=auth_headers
    ).json()
    assert len(rankings) == 0
