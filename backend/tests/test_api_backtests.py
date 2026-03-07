"""Tests for backtest API endpoints."""

def test_run_backtest(api_client, auth_headers):
    # Test POST run
    req_data = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "data_path": "../data/fixtures/sample_eurusd_h1.csv"
    }
    response = api_client.post("/api/v1/backtests/run", json=req_data, headers=auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "id" in data
    assert data["strategy_id"] == "bot01_sanyaku"
    assert data["total_trades"] > 0


def test_get_backtests(api_client, auth_headers):
    # Run a backtest first to get an ID
    req_data = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "data_path": "../data/fixtures/sample_eurusd_h1.csv"
    }
    post_resp = api_client.post("/api/v1/backtests/run", json=req_data, headers=auth_headers)
    run_id = post_resp.json()["id"]

    # Test GET list
    response = api_client.get("/api/v1/backtests", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["id"] == run_id

    # Test GET detail
    response = api_client.get(f"/api/v1/backtests/{run_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == run_id
    assert "metrics_json" in data
    assert "config_json" in data

    # Test GET trades
    response = api_client.get(f"/api/v1/backtests/{run_id}/trades", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert len(data["items"]) > 0

    # Test GET equity-curve
    response = api_client.get(f"/api/v1/backtests/{run_id}/equity-curve", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "equity_curve" in data
    assert isinstance(data["equity_curve"], list)
    assert len(data["equity_curve"]) > 0
