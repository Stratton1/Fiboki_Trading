"""Tests for strategies API endpoints."""


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


class TestStrategiesGrouped:
    def test_grouped_returns_tiers_in_order(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/strategies/grouped", headers=auth_headers
        )
        assert response.status_code == 200
        groups = response.json()
        assert groups, "expected at least one tier group"
        # Canonical tier first; experimental last if present.
        tiers = [g["tier"] for g in groups]
        assert tiers[0] == "canonical"
        # Each group carries display metadata + a non-empty strategy list.
        for g in groups:
            assert g["label"] and g["badge"] and g["description"]
            assert g["count"] == len(g["strategies"]) > 0
            assert all(s["tier"] == g["tier"] for s in g["strategies"])

    def test_grouped_total_matches_flat_list(self, api_client, auth_headers):
        flat = api_client.get("/api/v1/strategies", headers=auth_headers).json()
        groups = api_client.get(
            "/api/v1/strategies/grouped", headers=auth_headers
        ).json()
        grouped_total = sum(g["count"] for g in groups)
        assert grouped_total == len(flat)

    def test_grouped_includes_factory_tiers(self, api_client, auth_headers):
        groups = api_client.get(
            "/api/v1/strategies/grouped", headers=auth_headers
        ).json()
        by_tier = {g["tier"]: g for g in groups}
        assert by_tier["traditional_gen1"]["count"] == 25
        assert by_tier["hybrid_gen1"]["count"] == 10


class TestRegistryHealthEndpoint:
    def test_health_exposes_tier_counts(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/strategies/registry-health", headers=auth_headers
        )
        assert response.status_code == 200
        h = response.json()
        assert h["canonical_count"] == 12
        assert h["traditional_gen1_count"] == 25
        assert h["hybrid_gen1_count"] == 10
        assert h["registered_count"] == sum(h["tier_counts"].values())


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
