"""Tests for fleet-aware risk controls."""

from fibokei.risk.engine import RiskEngine


def test_fleet_max_total_positions():
    engine = RiskEngine(fleet_max_total_positions=3)
    positions = [
        {"instrument": "EURUSD", "direction": "LONG", "bot_id": "a"},
        {"instrument": "GBPUSD", "direction": "SHORT", "bot_id": "b"},
        {"instrument": "USDJPY", "direction": "LONG", "bot_id": "c"},
    ]
    allowed, reason = engine.check_fleet_trade_allowed("AUDUSD", positions)
    assert not allowed
    assert "total positions" in reason


def test_fleet_max_total_positions_under_limit():
    engine = RiskEngine(fleet_max_total_positions=5)
    positions = [
        {"instrument": "EURUSD", "direction": "LONG", "bot_id": "a"},
        {"instrument": "GBPUSD", "direction": "SHORT", "bot_id": "b"},
    ]
    allowed, reason = engine.check_fleet_trade_allowed("AUDUSD", positions)
    assert allowed
    assert reason == ""


def test_fleet_max_bots_per_instrument():
    engine = RiskEngine(fleet_max_bots_per_instrument=3)
    positions = [
        {"instrument": "EURUSD", "direction": "LONG", "bot_id": "a"},
        {"instrument": "EURUSD", "direction": "SHORT", "bot_id": "b"},
        {"instrument": "EURUSD", "direction": "LONG", "bot_id": "c"},
    ]
    allowed, reason = engine.check_fleet_trade_allowed("EURUSD", positions)
    assert not allowed
    assert "bots per instrument" in reason


def test_fleet_max_bots_per_instrument_different():
    """Adding a bot for a different instrument should be fine."""
    engine = RiskEngine(fleet_max_bots_per_instrument=2)
    positions = [
        {"instrument": "EURUSD", "direction": "LONG", "bot_id": "a"},
        {"instrument": "EURUSD", "direction": "SHORT", "bot_id": "b"},
    ]
    allowed, reason = engine.check_fleet_trade_allowed("GBPUSD", positions)
    assert allowed


def test_fleet_max_exposure_per_instrument():
    engine = RiskEngine(
        fleet_max_bots_per_instrument=10,
        fleet_max_exposure_per_instrument=4,
    )
    positions = [
        {"instrument": "EURUSD", "direction": "LONG", "bot_id": f"b{i}"}
        for i in range(4)
    ]
    allowed, reason = engine.check_fleet_trade_allowed("EURUSD", positions)
    assert not allowed
    assert "exposure per instrument" in reason


def test_compute_trade_overlap_identical():
    trades = [("2024-01-01T00:00", "2024-01-01T01:00")]
    overlap = RiskEngine.compute_trade_overlap(trades, trades)
    assert overlap == 1.0


def test_compute_trade_overlap_no_overlap():
    trades_a = [("2024-01-01T00:00", "2024-01-01T01:00")]
    trades_b = [("2024-01-02T00:00", "2024-01-02T01:00")]
    overlap = RiskEngine.compute_trade_overlap(trades_a, trades_b)
    assert overlap == 0.0


def test_compute_trade_overlap_partial():
    trades_a = [
        ("2024-01-01T00:00", "2024-01-01T01:00"),
        ("2024-01-02T00:00", "2024-01-02T01:00"),
    ]
    trades_b = [
        ("2024-01-01T00:00", "2024-01-01T01:00"),
        ("2024-01-03T00:00", "2024-01-03T01:00"),
    ]
    overlap = RiskEngine.compute_trade_overlap(trades_a, trades_b)
    # 1 shared entry out of 3 unique entries = 1/3
    assert abs(overlap - 1 / 3) < 0.01


def test_compute_trade_overlap_empty():
    assert RiskEngine.compute_trade_overlap([], [("a", "b")]) == 0.0
    assert RiskEngine.compute_trade_overlap([("a", "b")], []) == 0.0


def test_find_correlated_bots():
    engine = RiskEngine(fleet_correlation_threshold=0.5)
    bot_trades = {
        "bot1": [("2024-01-01", ""), ("2024-01-02", "")],
        "bot2": [("2024-01-01", ""), ("2024-01-02", "")],
        "bot3": [("2024-01-05", ""), ("2024-01-06", "")],
    }
    alerts = engine.find_correlated_bots(bot_trades)
    assert len(alerts) == 1
    assert alerts[0]["bot_a"] == "bot1"
    assert alerts[0]["bot_b"] == "bot2"
    assert alerts[0]["overlap"] == 1.0


def test_find_correlated_bots_none():
    engine = RiskEngine(fleet_correlation_threshold=0.85)
    bot_trades = {
        "bot1": [("2024-01-01", "")],
        "bot2": [("2024-01-05", "")],
    }
    alerts = engine.find_correlated_bots(bot_trades)
    assert len(alerts) == 0


def test_find_underperformers():
    engine = RiskEngine(fleet_cull_sigma=2.0, fleet_cull_min_trades=3)
    bot_pnls = {
        "good1": [1.0] * 5,      # avg = 1.0
        "good2": [0.8] * 5,      # avg = 0.8
        "good3": [0.9] * 5,      # avg = 0.9
        "bad": [-5.0] * 5,       # avg = -5.0 (severely underperforming)
    }
    result = engine.find_underperformers(bot_pnls)
    assert len(result) == 1
    assert result[0]["bot_id"] == "bad"
    assert result[0]["sigma_below"] >= 2.0


def test_find_underperformers_not_enough_trades():
    engine = RiskEngine(fleet_cull_sigma=2.0, fleet_cull_min_trades=50)
    bot_pnls = {
        "bot1": [1.0] * 10,
        "bot2": [-5.0] * 10,
    }
    result = engine.find_underperformers(bot_pnls)
    assert len(result) == 0  # Neither has 50 trades


def test_find_underperformers_too_few_bots():
    engine = RiskEngine(fleet_cull_sigma=2.0, fleet_cull_min_trades=3)
    bot_pnls = {"solo": [1.0] * 5}
    result = engine.find_underperformers(bot_pnls)
    assert len(result) == 0  # Need at least 2 eligible bots


def test_fleet_risk_api_endpoint(api_client, auth_headers):
    resp = api_client.get("/api/v1/paper/fleet/risk", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "fleet_limits" in data
    assert "fleet_status" in data
    assert "instrument_alerts" in data
    assert "correlation_alerts" in data
    assert "underperformers" in data
    assert data["fleet_limits"]["max_bots_per_instrument"] == 5
    assert data["fleet_limits"]["max_total_positions"] == 20
