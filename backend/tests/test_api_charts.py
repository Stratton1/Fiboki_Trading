"""Tests for chart annotation API endpoints."""


def test_backtest_annotations_not_found(api_client, auth_headers):
    """GET /charts/annotations/9999 returns 404."""
    response = api_client.get(
        "/api/v1/charts/annotations/9999", headers=auth_headers
    )
    assert response.status_code == 404


def test_backtest_annotations_after_run(api_client, auth_headers):
    """Run a backtest first, then get annotations -> 200 with trade_markers."""
    # Run a backtest to create data
    req_data = {
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "data_path": "../data/fixtures/sample_eurusd_h1.csv",
    }
    post_resp = api_client.post(
        "/api/v1/backtests/run", json=req_data, headers=auth_headers
    )
    assert post_resp.status_code == 200, post_resp.text
    run_id = post_resp.json()["id"]

    # Get annotations
    response = api_client.get(
        f"/api/v1/charts/annotations/{run_id}", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "trade_markers" in data
    assert "strategy_annotations" in data
    assert len(data["trade_markers"]) > 0

    # Verify trade marker structure
    marker = data["trade_markers"][0]
    assert "trade_id" in marker
    assert "strategy_id" in marker
    assert "direction" in marker
    assert "entry" in marker
    assert "exit" in marker
    assert "outcome" in marker
    assert marker["outcome"] in ("win", "loss", "breakeven")
