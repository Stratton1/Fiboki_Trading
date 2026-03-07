"""Tests for paper trading API endpoints."""

import fibokei.api.routes.paper as paper_mod


def _reset_orchestrator():
    """Reset module-level orchestrator between tests."""
    paper_mod._orchestrator = None


def test_create_bot(api_client, auth_headers):
    _reset_orchestrator()
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
    }
    response = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["bot_id"]
    assert data["strategy_id"] == "bot01_sanyaku"
    assert data["state"] == "monitoring"


def test_list_bots(api_client, auth_headers):
    _reset_orchestrator()
    # Create a bot first
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
    }
    api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)

    response = api_client.get("/api/v1/paper/bots", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_get_bot(api_client, auth_headers):
    _reset_orchestrator()
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
    }
    create_resp = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    bot_id = create_resp.json()["bot_id"]

    response = api_client.get(f"/api/v1/paper/bots/{bot_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["bot_id"] == bot_id
    assert data["state"] == "monitoring"


def test_stop_bot(api_client, auth_headers):
    _reset_orchestrator()
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
    }
    create_resp = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    bot_id = create_resp.json()["bot_id"]

    response = api_client.post(f"/api/v1/paper/bots/{bot_id}/stop", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "stopped"


def test_pause_bot(api_client, auth_headers):
    _reset_orchestrator()
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
    }
    create_resp = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    bot_id = create_resp.json()["bot_id"]

    response = api_client.post(f"/api/v1/paper/bots/{bot_id}/pause", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "paused"


def test_get_account(api_client, auth_headers):
    _reset_orchestrator()
    response = api_client.get("/api/v1/paper/account", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["balance"] == 10000.0
    assert data["equity"] == 10000.0
    assert data["total_pnl"] == 0.0


def test_bot_not_found(api_client, auth_headers):
    _reset_orchestrator()
    response = api_client.get("/api/v1/paper/bots/nonexistent", headers=auth_headers)
    assert response.status_code == 404
