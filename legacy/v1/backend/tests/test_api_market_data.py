"""Tests for market data API endpoints."""


def test_get_market_data(api_client, auth_headers):
    """GET /market-data/EURUSD/H1 returns 200 with candles and ichimoku."""
    response = api_client.get(
        "/api/v1/market-data/EURUSD/H1", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["instrument"] == "EURUSD"
    assert data["timeframe"] == "H1"
    assert len(data["candles"]) > 0
    assert len(data["ichimoku"]) > 0

    # Verify candle structure
    candle = data["candles"][0]
    assert "timestamp" in candle
    assert "open" in candle
    assert "high" in candle
    assert "low" in candle
    assert "close" in candle

    # Verify ichimoku structure
    ich = data["ichimoku"][0]
    assert "timestamp" in ich
    assert "tenkan" in ich
    assert "kijun" in ich
    assert "senkou_a" in ich
    assert "senkou_b" in ich
    assert "chikou" in ich

    # Verify NaN values are serialized as None (early values have no Ichimoku)
    assert data["ichimoku"][0]["tenkan"] is None or isinstance(
        data["ichimoku"][0]["tenkan"], float
    )


def test_get_market_data_invalid_instrument(api_client, auth_headers):
    """GET /market-data/INVALID/H1 returns 404."""
    response = api_client.get(
        "/api/v1/market-data/INVALID/H1", headers=auth_headers
    )
    assert response.status_code == 404


def test_get_market_data_invalid_timeframe(api_client, auth_headers):
    """GET /market-data/EURUSD/INVALID returns 400."""
    response = api_client.get(
        "/api/v1/market-data/EURUSD/INVALID", headers=auth_headers
    )
    assert response.status_code == 400
