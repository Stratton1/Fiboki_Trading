"""Tests for paper trading API endpoints."""

from fibokei.db.repository import save_research_results


def _seed_research(api_client, strategy_id="bot01_sanyaku",
                   instrument="EURUSD", timeframe="H1"):
    """Seed a qualifying research score so the promotion gate passes."""
    session_factory = api_client.app.state.session_factory
    with session_factory() as session:
        save_research_results(session, [{
            "run_id": "seed_api_test",
            "strategy_id": strategy_id,
            "instrument": instrument,
            "timeframe": timeframe,
            "composite_score": 0.80,
            "rank": 1,
        }])


def test_create_bot(api_client, auth_headers):
    _seed_research(api_client)
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
    _seed_research(api_client)
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
    _seed_research(api_client)
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
    _seed_research(api_client)
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
    _seed_research(api_client)
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
    response = api_client.get("/api/v1/paper/account", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["balance"] == 10000.0
    assert data["equity"] == 10000.0
    assert data["total_pnl"] == 0.0


def test_bot_not_found(api_client, auth_headers):
    response = api_client.get("/api/v1/paper/bots/nonexistent", headers=auth_headers)
    assert response.status_code == 404


def test_create_bot_with_source_tracking(api_client, auth_headers):
    """Bots created with source_type and source_id are persisted and returned."""
    _seed_research(api_client)
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "source_type": "research",
        "source_id": "seed_api_test",
    }
    response = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["source_type"] == "research"
    assert data["source_id"] == "seed_api_test"

    # Verify source fields are returned in detail endpoint
    detail = api_client.get(f"/api/v1/paper/bots/{data['bot_id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["source_type"] == "research"
    assert detail.json()["source_id"] == "seed_api_test"


def test_create_bot_default_source_type(api_client, auth_headers):
    """When no source_type is given, defaults to 'manual'."""
    _seed_research(api_client)
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
    }
    response = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["source_type"] == "manual"
    assert response.json()["source_id"] is None


def test_promotion_gate_rejects_low_score(api_client, auth_headers):
    """Combo with score below threshold is rejected."""
    # Seed a low score
    session_factory = api_client.app.state.session_factory
    with session_factory() as session:
        save_research_results(session, [{
            "run_id": "low_score",
            "strategy_id": "bot01_sanyaku",
            "instrument": "GBPUSD",
            "timeframe": "H1",
            "composite_score": 0.30,
            "rank": 1,
        }])

    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "GBPUSD",
        "timeframe": "H1",
    }
    response = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    assert response.status_code == 422
    assert "Promotion gate failed" in response.json()["detail"]


def test_promotion_gate_rejects_no_research(api_client, auth_headers):
    """Combo with no research results is rejected."""
    req = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "NZDUSD",
        "timeframe": "D",
    }
    response = api_client.post("/api/v1/paper/bots", json=req, headers=auth_headers)
    assert response.status_code == 422
    assert "composite_score=none" in response.json()["detail"]
