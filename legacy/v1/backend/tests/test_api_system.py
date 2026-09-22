"""Tests for system API endpoints."""

import os
from unittest import mock


class TestSystemHealth:
    def test_system_health(self, api_client):
        response = api_client.get("/api/v1/system/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"


class TestSystemStatus:
    def test_system_status(self, api_client, auth_headers):
        response = api_client.get("/api/v1/system/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "api_version" in data
        assert "database" in data
        assert "strategies_loaded" in data
        assert data["database"] == "connected"
        assert data["strategies_loaded"] >= 1

    def test_system_status_requires_auth(self, api_client):
        response = api_client.get("/api/v1/system/status")
        assert response.status_code == 401

    def test_strategies_loaded_matches_registry_size(self, api_client, auth_headers):
        """Regression: /system/status must report the full registered count,
        not the operator-visibility-filtered subset. Setting
        FIBOKEI_VISIBLE_STRATEGIES previously truncated this value to 2."""
        from fibokei.strategies.registry import strategy_registry

        expected = strategy_registry.loaded_count
        # Sanity: the architectural baseline is at least 12 strategies.
        assert expected >= 12, (
            f"Strategy registry under-populated ({expected}) — investigate "
            "registry imports before trusting this test"
        )

        # The visibility filter env var must NOT shrink the loaded count.
        with mock.patch.dict(
            os.environ, {"FIBOKEI_VISIBLE_STRATEGIES": "bot01_sanyaku,bot02_kijun_pullback"}
        ):
            response = api_client.get("/api/v1/system/status", headers=auth_headers)
            assert response.status_code == 200
            assert response.json()["strategies_loaded"] == expected

    def test_strategies_expected_min_exposed(self, api_client, auth_headers):
        """Dashboard reads strategies_expected_min instead of hardcoding 12.
        The field must be present and match the registry's published constant."""
        from fibokei.strategies.registry import EXPECTED_MIN_STRATEGIES

        response = api_client.get("/api/v1/system/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "strategies_expected_min" in data
        assert data["strategies_expected_min"] == EXPECTED_MIN_STRATEGIES
        assert data["strategies_loaded"] >= data["strategies_expected_min"], (
            "Production registry is below the architectural minimum — fix "
            "strategy imports before shipping"
        )
