"""Tests for trade journal API endpoints."""

import time

import pytest


def _run_backtest_and_wait(api_client, auth_headers, timeout=30):
    """Run a quick backtest to create trades in the DB."""
    resp = api_client.post(
        "/api/v1/backtests/run",
        json={
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
        },
        params={"async": "true"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = api_client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers).json()
        if job["state"] == "completed":
            return job
        if job["state"] == "failed":
            pytest.skip(f"Backtest failed: {job.get('error')}")
        time.sleep(0.2)
    pytest.skip("Backtest did not complete in time")


def _get_first_trade_id(api_client, auth_headers) -> int:
    """Get the ID of the first trade in the system."""
    resp = api_client.get("/api/v1/trades/?size=1", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    if not items:
        pytest.skip("No trades available for journal tests")
    return items[0]["id"]


@pytest.fixture
def trade_id(api_client, auth_headers):
    """Run a backtest to populate trades, return the first trade ID."""
    _run_backtest_and_wait(api_client, auth_headers)
    return _get_first_trade_id(api_client, auth_headers)


# ── Journal CRUD ─────────────────────────────────────────────


def test_journal_empty_initially(api_client, auth_headers, trade_id):
    resp = api_client.get(f"/api/v1/trades/{trade_id}/journal", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() is None


def test_create_journal_entry(api_client, auth_headers, trade_id):
    body = {"note": "Good entry, held well", "tags": ["good entry", "textbook setup"]}
    resp = api_client.post(
        f"/api/v1/trades/{trade_id}/journal",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["trade_id"] == trade_id
    assert data["note"] == "Good entry, held well"
    assert data["tags"] == ["good entry", "textbook setup"]
    assert data["id"] > 0


def test_create_journal_duplicate_rejected(api_client, auth_headers, trade_id):
    """Only one journal entry per trade."""
    body = {"note": "First entry"}
    api_client.post(f"/api/v1/trades/{trade_id}/journal", json=body, headers=auth_headers)

    resp = api_client.post(
        f"/api/v1/trades/{trade_id}/journal",
        json={"note": "Second attempt"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


def test_update_journal_entry(api_client, auth_headers, trade_id):
    api_client.post(
        f"/api/v1/trades/{trade_id}/journal",
        json={"note": "Original", "tags": ["good entry"]},
        headers=auth_headers,
    )

    resp = api_client.patch(
        f"/api/v1/trades/{trade_id}/journal",
        json={"note": "Updated note", "tags": ["bad entry", "news event"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["note"] == "Updated note"
    assert data["tags"] == ["bad entry", "news event"]


def test_delete_journal_entry(api_client, auth_headers, trade_id):
    api_client.post(
        f"/api/v1/trades/{trade_id}/journal",
        json={"note": "To delete"},
        headers=auth_headers,
    )

    resp = api_client.delete(f"/api/v1/trades/{trade_id}/journal", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] == trade_id

    # Verify it's gone
    resp = api_client.get(f"/api/v1/trades/{trade_id}/journal", headers=auth_headers)
    assert resp.json() is None


def test_journal_nonexistent_trade(api_client, auth_headers):
    resp = api_client.post(
        "/api/v1/trades/999999/journal",
        json={"note": "No such trade"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_list_journal_entries(api_client, auth_headers, trade_id):
    api_client.post(
        f"/api/v1/trades/{trade_id}/journal",
        json={"note": "A note", "tags": ["review later"]},
        headers=auth_headers,
    )

    resp = api_client.get("/api/v1/journal", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(e["trade_id"] == trade_id for e in data["items"])


def test_list_journal_filter_by_tag(api_client, auth_headers, trade_id):
    api_client.post(
        f"/api/v1/trades/{trade_id}/journal",
        json={"note": "Tagged", "tags": ["special_tag"]},
        headers=auth_headers,
    )

    resp = api_client.get("/api/v1/journal?tag=special_tag", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1

    resp = api_client.get("/api/v1/journal?tag=nonexistent_tag", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
