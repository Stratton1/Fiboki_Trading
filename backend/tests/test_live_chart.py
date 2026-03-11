"""Tests for live chart mode (Phase 14.3).

Tests the live data provider, market-data mode routing,
and live status endpoint using mocks (no real IG API calls).
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# live_provider unit tests
# ---------------------------------------------------------------------------


class TestLiveProvider:
    """Unit tests for fibokei.data.live_provider."""

    def test_is_live_available_when_configured(self, monkeypatch):
        monkeypatch.setenv("FIBOKEI_IG_API_KEY", "key")
        monkeypatch.setenv("FIBOKEI_IG_USERNAME", "user")
        monkeypatch.setenv("FIBOKEI_IG_PASSWORD", "pass")

        from fibokei.data.live_provider import is_live_available

        assert is_live_available() is True

    def test_is_live_available_when_missing_creds(self, monkeypatch):
        monkeypatch.delenv("FIBOKEI_IG_API_KEY", raising=False)
        monkeypatch.delenv("FIBOKEI_IG_USERNAME", raising=False)
        monkeypatch.delenv("FIBOKEI_IG_PASSWORD", raising=False)

        from fibokei.data.live_provider import is_live_available

        assert is_live_available() is False

    def test_get_supported_ig_resolution_valid(self):
        from fibokei.data.live_provider import get_supported_ig_resolution

        assert get_supported_ig_resolution("H1") == "HOUR"
        assert get_supported_ig_resolution("M5") == "MINUTE_5"
        assert get_supported_ig_resolution("D") == "DAY"
        assert get_supported_ig_resolution("h1") == "HOUR"  # case-insensitive

    def test_get_supported_ig_resolution_invalid(self):
        from fibokei.data.live_provider import get_supported_ig_resolution

        assert get_supported_ig_resolution("H2") is None
        assert get_supported_ig_resolution("INVALID") is None

    def test_load_live_unsupported_timeframe(self):
        from fibokei.data.live_provider import load_live

        with pytest.raises(ValueError, match="not supported for live mode"):
            load_live("EURUSD", "H2")

    @patch("fibokei.data.live_provider._get_client")
    def test_load_live_success(self, mock_get_client):
        """load_live returns a DataFrame with correct structure."""
        from fibokei.data.live_provider import clear_live_cache, load_live

        clear_live_cache()

        mock_client = MagicMock()
        mock_client.get_prices.return_value = {
            "prices": [
                {
                    "snapshotTime": "2026-03-10 10:00:00",
                    "openPrice": {"bid": 1.0900, "ask": 1.0902},
                    "highPrice": {"bid": 1.0920, "ask": 1.0922},
                    "lowPrice": {"bid": 1.0880, "ask": 1.0882},
                    "closePrice": {"bid": 1.0910, "ask": 1.0912},
                    "lastTradedVolume": 1000,
                },
                {
                    "snapshotTime": "2026-03-10 11:00:00",
                    "openPrice": {"bid": 1.0910, "ask": 1.0912},
                    "highPrice": {"bid": 1.0930, "ask": 1.0932},
                    "lowPrice": {"bid": 1.0890, "ask": 1.0892},
                    "closePrice": {"bid": 1.0920, "ask": 1.0922},
                    "lastTradedVolume": 1200,
                },
            ]
        }
        mock_get_client.return_value = mock_client

        df, source = load_live("EURUSD", "H1")

        assert source == "live/ig_demo"
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df.iloc[0]["open"] == 1.0900
        assert df.iloc[0]["volume"] == 1000
        assert df.index.name == "timestamp"

        mock_client.get_prices.assert_called_once()

    @patch("fibokei.data.live_provider._get_client")
    def test_load_live_cache_hit(self, mock_get_client):
        """Second call within TTL uses cache, no extra API call."""
        from fibokei.data.live_provider import clear_live_cache, load_live

        clear_live_cache()

        mock_client = MagicMock()
        mock_client.get_prices.return_value = {
            "prices": [
                {
                    "snapshotTime": "2026-03-10 10:00:00",
                    "openPrice": {"bid": 1.09},
                    "highPrice": {"bid": 1.092},
                    "lowPrice": {"bid": 1.088},
                    "closePrice": {"bid": 1.091},
                    "lastTradedVolume": 500,
                },
            ]
        }
        mock_get_client.return_value = mock_client

        df1, _ = load_live("EURUSD", "H1")
        df2, _ = load_live("EURUSD", "H1")

        # Only one API call despite two load_live calls
        assert mock_client.get_prices.call_count == 1
        assert len(df1) == len(df2)

    @patch("fibokei.data.live_provider._get_client")
    def test_load_live_ig_error(self, mock_get_client):
        """IG API error raises RuntimeError."""
        from fibokei.data.live_provider import clear_live_cache, load_live
        from fibokei.execution.ig_client import IGClientError

        clear_live_cache()

        mock_client = MagicMock()
        mock_client.get_prices.side_effect = IGClientError("API down")
        mock_get_client.return_value = mock_client

        with pytest.raises(RuntimeError, match="IG API error"):
            load_live("EURUSD", "H1")

    @patch("fibokei.data.live_provider._get_client")
    def test_load_live_empty_prices(self, mock_get_client):
        """Empty price list raises RuntimeError."""
        from fibokei.data.live_provider import clear_live_cache, load_live

        clear_live_cache()

        mock_client = MagicMock()
        mock_client.get_prices.return_value = {"prices": []}
        mock_get_client.return_value = mock_client

        with pytest.raises(RuntimeError, match="No price data returned"):
            load_live("EURUSD", "H1")

    def test_load_live_unknown_symbol(self):
        from fibokei.data.live_provider import clear_live_cache, load_live

        clear_live_cache()

        with pytest.raises(ValueError, match="No IG epic mapping"):
            load_live("FOOBAR", "H1")


# ---------------------------------------------------------------------------
# API endpoint tests (market-data mode routing)
# ---------------------------------------------------------------------------


class TestMarketDataModeRouting:
    """Integration tests for GET /market-data/{instrument}/{timeframe}?mode=..."""

    def test_historical_mode_default(self, api_client, auth_headers):
        """Default mode=historical returns candles from canonical data."""
        response = api_client.get(
            "/api/v1/market-data/EURUSD/H1", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "historical"
        assert len(data["candles"]) > 0

    def test_historical_mode_explicit(self, api_client, auth_headers):
        """Explicit mode=historical works."""
        response = api_client.get(
            "/api/v1/market-data/EURUSD/H1?mode=historical", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "historical"

    def test_live_mode_no_creds(self, api_client, auth_headers, monkeypatch):
        """mode=live returns 503 when IG creds are missing."""
        monkeypatch.delenv("FIBOKEI_IG_API_KEY", raising=False)
        monkeypatch.delenv("FIBOKEI_IG_USERNAME", raising=False)
        monkeypatch.delenv("FIBOKEI_IG_PASSWORD", raising=False)

        response = api_client.get(
            "/api/v1/market-data/EURUSD/H1?mode=live", headers=auth_headers
        )
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_invalid_mode(self, api_client, auth_headers):
        """Invalid mode value returns 422."""
        response = api_client.get(
            "/api/v1/market-data/EURUSD/H1?mode=realtime", headers=auth_headers
        )
        assert response.status_code == 422

    def test_response_has_mode_field(self, api_client, auth_headers):
        """Response includes the mode field."""
        response = api_client.get(
            "/api/v1/market-data/EURUSD/H1", headers=auth_headers
        )
        data = response.json()
        assert "mode" in data
        assert data["mode"] in ("historical", "live")

    def test_response_has_source_field(self, api_client, auth_headers):
        """Response includes the source field."""
        response = api_client.get(
            "/api/v1/market-data/EURUSD/H1", headers=auth_headers
        )
        data = response.json()
        assert "source" in data
        assert data["source"] is not None


class TestLiveStatusEndpoint:
    """Tests for GET /market-data/live/status."""

    def test_live_status_unavailable(self, api_client, auth_headers, monkeypatch):
        """Returns available=false when IG creds missing."""
        monkeypatch.delenv("FIBOKEI_IG_API_KEY", raising=False)
        monkeypatch.delenv("FIBOKEI_IG_USERNAME", raising=False)
        monkeypatch.delenv("FIBOKEI_IG_PASSWORD", raising=False)

        response = api_client.get(
            "/api/v1/market-data/live/status", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["reason"] is not None

    def test_live_status_available(self, api_client, auth_headers, monkeypatch):
        """Returns available=true when IG creds are set."""
        monkeypatch.setenv("FIBOKEI_IG_API_KEY", "key")
        monkeypatch.setenv("FIBOKEI_IG_USERNAME", "user")
        monkeypatch.setenv("FIBOKEI_IG_PASSWORD", "pass")

        response = api_client.get(
            "/api/v1/market-data/live/status", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["reason"] is None
