"""Tests for strategies API endpoints."""

import pytest


class TestStrategiesList:
    def test_list_strategies(self, api_client, auth_headers):
        # Derive the expected count from the registry rather than hardcoding,
        # which was the same defect class as the /system/status bug: the
        # 12-strategy architectural minimum has since grown but tests with
        # hardcoded counts drift silently.
        from fibokei.strategies.registry import strategy_registry

        response = api_client.get("/api/v1/strategies", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        expected_visible = len(strategy_registry.list_available())
        assert len(data) == expected_visible
        assert expected_visible >= 12, "registry under-populated"
        ids = [s["id"] for s in data]
        assert "bot01_sanyaku" in ids
        assert "bot12_kumo_fib_tz" in ids

    def test_strategy_has_fields(self, api_client, auth_headers):
        response = api_client.get("/api/v1/strategies", headers=auth_headers)
        data = response.json()
        s = data[0]
        assert "id" in s
        assert "name" in s
        assert "family" in s
        assert "complexity" in s
        assert "supports_long" in s
        assert "supports_short" in s


class TestStrategyDetail:
    def test_get_strategy(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/strategies/bot01_sanyaku", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "bot01_sanyaku"
        assert data["family"] == "ichimoku"
        assert "valid_market_regimes" in data
        assert "required_indicators" in data

    def test_get_unknown_strategy(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/strategies/bot99_fake", headers=auth_headers
        )
        assert response.status_code == 404
