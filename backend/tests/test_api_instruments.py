"""Tests for instruments API endpoints."""

import pytest


class TestInstrumentsList:
    def test_list_instruments(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 30
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


class TestInstrumentDetail:
    def test_get_instrument(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments/EURUSD", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "EURUSD"
        assert data["asset_class"] == "forex_major"

    def test_get_instrument_case_insensitive(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments/eurusd", headers=auth_headers)
        assert response.status_code == 200

    def test_get_unknown_instrument(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments/ZZZZZ", headers=auth_headers)
        assert response.status_code == 404
