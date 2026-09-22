"""Tests for watchlist API endpoints."""


def test_default_watchlist_created(api_client, auth_headers):
    """First GET auto-creates a 'Forex Majors' watchlist."""
    resp = api_client.get("/api/v1/watchlists", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Forex Majors"
    assert "EURUSD" in data[0]["instrument_ids"]


def test_create_watchlist(api_client, auth_headers):
    body = {"name": "My Metals", "instrument_ids": ["XAUUSD", "XAGUSD"]}
    resp = api_client.post("/api/v1/watchlists", json=body, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Metals"
    assert data["instrument_ids"] == ["XAUUSD", "XAGUSD"]
    assert data["id"] > 0


def test_create_watchlist_validation(api_client, auth_headers):
    """Empty name or empty instruments should fail."""
    resp = api_client.post(
        "/api/v1/watchlists",
        json={"name": "", "instrument_ids": ["EURUSD"]},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    resp = api_client.post(
        "/api/v1/watchlists",
        json={"name": "Empty", "instrument_ids": []},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_update_watchlist(api_client, auth_headers):
    # Create
    resp = api_client.post(
        "/api/v1/watchlists",
        json={"name": "Test", "instrument_ids": ["EURUSD"]},
        headers=auth_headers,
    )
    wl_id = resp.json()["id"]

    # Update name
    resp = api_client.put(
        f"/api/v1/watchlists/{wl_id}",
        json={"name": "Renamed"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"

    # Update instruments
    resp = api_client.put(
        f"/api/v1/watchlists/{wl_id}",
        json={"instrument_ids": ["GBPUSD", "USDJPY"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["instrument_ids"] == ["GBPUSD", "USDJPY"]


def test_update_empty_body_fails(api_client, auth_headers):
    resp = api_client.post(
        "/api/v1/watchlists",
        json={"name": "Test", "instrument_ids": ["EURUSD"]},
        headers=auth_headers,
    )
    wl_id = resp.json()["id"]

    resp = api_client.put(
        f"/api/v1/watchlists/{wl_id}",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_delete_watchlist(api_client, auth_headers):
    resp = api_client.post(
        "/api/v1/watchlists",
        json={"name": "ToDelete", "instrument_ids": ["EURUSD"]},
        headers=auth_headers,
    )
    wl_id = resp.json()["id"]

    resp = api_client.delete(f"/api/v1/watchlists/{wl_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] == wl_id


def test_delete_nonexistent_watchlist(api_client, auth_headers):
    resp = api_client.delete("/api/v1/watchlists/99999", headers=auth_headers)
    assert resp.status_code == 404


def test_list_multiple_watchlists(api_client, auth_headers):
    # Trigger default creation
    api_client.get("/api/v1/watchlists", headers=auth_headers)

    # Create two more
    api_client.post(
        "/api/v1/watchlists",
        json={"name": "Metals", "instrument_ids": ["XAUUSD"]},
        headers=auth_headers,
    )
    api_client.post(
        "/api/v1/watchlists",
        json={"name": "Indices", "instrument_ids": ["US500", "UK100"]},
        headers=auth_headers,
    )

    resp = api_client.get("/api/v1/watchlists", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    names = {w["name"] for w in data}
    assert names == {"Forex Majors", "Metals", "Indices"}
