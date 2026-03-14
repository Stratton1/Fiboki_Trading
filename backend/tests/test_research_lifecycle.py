"""Tests for research results lifecycle: deduplication, clear modes, run listing."""

import time


def _wait_for_job(api_client, auth_headers, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = api_client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        data = resp.json()
        if data["state"] in ("completed", "failed"):
            return data
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


def _run_research(api_client, auth_headers):
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


# ── Deduplication ────────────────────────────────────────────


def test_rankings_deduplicate_across_runs(api_client, auth_headers):
    """Run research twice → all-runs with deduplicate=true shows one row per combo."""
    job1 = _run_research(api_client, auth_headers)
    assert job1["state"] == "completed"
    job2 = _run_research(api_client, auth_headers)
    assert job2["state"] == "completed"

    # Without dedup: should have 2 rows for same combo
    r1 = api_client.get("/api/v1/research/rankings?limit=50", headers=auth_headers)
    assert r1.status_code == 200
    all_rows = r1.json()
    assert len(all_rows) >= 2  # same combo from two runs

    # With dedup: should have 1 row per unique combo
    r2 = api_client.get("/api/v1/research/rankings?limit=50&deduplicate=true", headers=auth_headers)
    assert r2.status_code == 200
    deduped = r2.json()
    combos = {(r["strategy_id"], r["instrument"], r["timeframe"]) for r in deduped}
    assert len(deduped) == len(combos)  # no duplicates


def test_rankings_single_run_no_duplicates(api_client, auth_headers):
    """Within a single run, results should not duplicate."""
    job = _run_research(api_client, auth_headers)
    run_id = job["result"]["run_id"]

    r = api_client.get(f"/api/v1/research/rankings?run_id={run_id}", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    combos = [(r["strategy_id"], r["instrument"], r["timeframe"]) for r in data]
    assert len(combos) == len(set(combos))  # no duplicates within run


# ── Run Listing ──────────────────────────────────────────────


def test_run_listing_includes_top_score(api_client, auth_headers):
    _run_research(api_client, auth_headers)

    r = api_client.get("/api/v1/research/runs", headers=auth_headers)
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) >= 1
    assert "top_score" in runs[0]
    assert runs[0]["top_score"] > 0


def test_run_listing_ordered_newest_first(api_client, auth_headers):
    _run_research(api_client, auth_headers)
    _run_research(api_client, auth_headers)

    r = api_client.get("/api/v1/research/runs", headers=auth_headers)
    runs = r.json()
    assert len(runs) >= 2
    # Newest first
    if runs[0]["created_at"] and runs[1]["created_at"]:
        assert runs[0]["created_at"] >= runs[1]["created_at"]


# ── Clear Non-Saved ──────────────────────────────────────────


def test_clear_non_saved_preserves_shortlisted(api_client, auth_headers):
    """Clear non-saved should keep results whose combos are in the shortlist."""
    job = _run_research(api_client, auth_headers)
    run_id = job["result"]["run_id"]

    # Save the combo to shortlist
    rankings = api_client.get(f"/api/v1/research/rankings?run_id={run_id}", headers=auth_headers).json()
    assert len(rankings) >= 1
    combo = rankings[0]
    api_client.post("/api/v1/research/shortlist", json={
        "strategy_id": combo["strategy_id"],
        "instrument": combo["instrument"],
        "timeframe": combo["timeframe"],
        "score": combo["composite_score"],
    }, headers=auth_headers)

    # Clear non-saved
    r = api_client.delete(f"/api/v1/research/results/non-saved?run_id={run_id}", headers=auth_headers)
    assert r.status_code == 200
    # Since the only combo IS saved, deleted_count should be 0
    assert r.json()["deleted_count"] == 0

    # Results should still exist
    after = api_client.get(f"/api/v1/research/rankings?run_id={run_id}", headers=auth_headers).json()
    assert len(after) == len(rankings)


def test_clear_non_saved_deletes_unsaved(api_client, auth_headers):
    """Clear non-saved should delete results not in the shortlist."""
    job = _run_research(api_client, auth_headers)
    run_id = job["result"]["run_id"]

    # Do NOT save to shortlist
    rankings_before = api_client.get(f"/api/v1/research/rankings?run_id={run_id}", headers=auth_headers).json()
    assert len(rankings_before) >= 1

    # Clear non-saved
    r = api_client.delete(f"/api/v1/research/results/non-saved?run_id={run_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["deleted_count"] == len(rankings_before)

    # Results should be gone
    after = api_client.get(f"/api/v1/research/rankings?run_id={run_id}", headers=auth_headers).json()
    assert len(after) == 0


# ── Shortlist survives all clears ─────────────────────────────


def test_shortlist_survives_clear_all(api_client, auth_headers):
    """Deleting all results must not delete shortlist entries."""
    job = _run_research(api_client, auth_headers)
    rankings = api_client.get("/api/v1/research/rankings", headers=auth_headers).json()
    combo = rankings[0]

    # Save to shortlist
    api_client.post("/api/v1/research/shortlist", json={
        "strategy_id": combo["strategy_id"],
        "instrument": combo["instrument"],
        "timeframe": combo["timeframe"],
        "score": combo["composite_score"],
    }, headers=auth_headers)

    # Clear ALL results
    api_client.delete("/api/v1/research/results", headers=auth_headers)

    # Shortlist must survive
    sl = api_client.get("/api/v1/research/shortlist", headers=auth_headers).json()
    assert len(sl) >= 1
    assert sl[0]["strategy_id"] == combo["strategy_id"]
