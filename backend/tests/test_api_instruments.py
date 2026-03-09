"""Tests for instruments API endpoints."""

import pytest


class TestInstrumentsList:
    def test_list_instruments(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 67
        symbols = [i["symbol"] for i in data]
        assert "EURUSD" in symbols
        assert "XAUUSD" in symbols
        assert "BTCUSD" in symbols

    def test_instrument_has_fields(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments", headers=auth_headers)
        data = response.json()
        inst = data[0]
        assert "symbol" in inst
        assert "name" in inst
        assert "asset_class" in inst
        assert "has_canonical_data" in inst

    def test_has_canonical_data_values(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments", headers=auth_headers)
        data = response.json()
        by_symbol = {i["symbol"]: i for i in data}
        assert by_symbol["EURUSD"]["has_canonical_data"] is True
        assert by_symbol["BTCUSD"]["has_canonical_data"] is False

    def test_filter_by_asset_class_forex_major(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/instruments?asset_class=forex_major", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 7
        assert all(i["asset_class"] == "forex_major" for i in data)

    def test_filter_by_asset_class_crypto(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/instruments?asset_class=crypto", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 5
        assert all(i["asset_class"] == "crypto" for i in data)

    def test_filter_by_invalid_asset_class(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/instruments?asset_class=nonexistent", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []


class TestInstrumentDetail:
    def test_get_instrument(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments/EURUSD", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "EURUSD"
        assert data["asset_class"] == "forex_major"
        assert data["has_canonical_data"] is True

    def test_get_instrument_case_insensitive(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments/eurusd", headers=auth_headers)
        assert response.status_code == 200

    def test_get_unknown_instrument(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments/ZZZZZ", headers=auth_headers)
        assert response.status_code == 404
