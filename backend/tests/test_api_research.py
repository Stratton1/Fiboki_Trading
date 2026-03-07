"""Tests for research API endpoints."""


def test_run_research(api_client, auth_headers):
    req_data = {
        "strategy_ids": ["bot01_sanyaku"],
        "instruments": ["EURUSD"],
        "timeframes": ["H1"],
        "data_dir": "../data/fixtures",
    }
    response = api_client.post("/api/v1/research/run", json=req_data, headers=auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["run_id"]
    assert data["total_combinations"] == 1
    assert data["completed"] == 1
    assert data["top_result"] is not None
    assert data["top_result"]["composite_score"] > 0


def test_get_rankings(api_client, auth_headers):
    # Run research first
    req_data = {
        "strategy_ids": ["bot01_sanyaku"],
        "instruments": ["EURUSD"],
        "timeframes": ["H1"],
        "data_dir": "../data/fixtures",
    }
    api_client.post("/api/v1/research/run", json=req_data, headers=auth_headers)

    response = api_client.get("/api/v1/research/rankings", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["composite_score"] > 0
    assert data[0]["rank"] == 1


def test_compare_combinations(api_client, auth_headers):
    # Run research first
    req_data = {
        "strategy_ids": ["bot01_sanyaku"],
        "instruments": ["EURUSD"],
        "timeframes": ["H1"],
        "data_dir": "../data/fixtures",
    }
    api_client.post("/api/v1/research/run", json=req_data, headers=auth_headers)

    # Compare
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
