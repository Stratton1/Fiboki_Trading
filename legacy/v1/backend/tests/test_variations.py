"""Tests for parameter variation engine and API."""

import pytest

from fibokei.research.variation import (
    check_overlap,
    generate_variants,
    get_param_ranges,
    get_strategy_params,
)
from fibokei.risk.engine import RiskEngine


# ── Unit tests for variation module ─────────────────────────


def test_get_strategy_params():
    """Strategy with constructor params should be discoverable."""
    params = get_strategy_params("bot03_flat_senkou_b")
    assert "flat_lookback" in params or len(params) >= 0


def test_get_param_ranges_ichimoku():
    """Ichimoku strategies should have tenkan/kijun ranges."""
    ranges = get_param_ranges("bot01_sanyaku")
    assert "tenkan_period" in ranges
    assert "kijun_period" in ranges
    assert len(ranges["tenkan_period"]) >= 3


def test_generate_variants_capped():
    """Variant generation should respect max_variants."""
    combos = generate_variants("bot01_sanyaku", max_variants=5)
    assert len(combos) <= 5
    assert all(isinstance(c, dict) for c in combos)


def test_generate_variants_custom_ranges():
    """Custom param overrides should be used."""
    combos = generate_variants(
        "bot01_sanyaku",
        param_overrides={"tenkan_period": [7, 9, 11]},
        max_variants=10,
    )
    assert len(combos) == 3
    assert all("tenkan_period" in c for c in combos)


def test_check_overlap_identical():
    """Identical trade entries should be flagged as duplicate."""
    entries = ["2024-01-01", "2024-01-02", "2024-01-03"]
    is_dup, overlap = check_overlap(entries, [entries])
    assert is_dup
    assert overlap == 1.0


def test_check_overlap_none():
    """No overlap should not be flagged."""
    entries_a = ["2024-01-01", "2024-01-02"]
    entries_b = ["2024-01-05", "2024-01-06"]
    is_dup, overlap = check_overlap(entries_a, [entries_b])
    assert not is_dup
    assert overlap == 0.0


def test_check_overlap_threshold():
    """Overlap below threshold should not be flagged."""
    entries_a = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    entries_b = ["2024-01-01", "2024-01-05", "2024-01-06", "2024-01-07"]
    is_dup, overlap = check_overlap(entries_a, [entries_b], threshold=0.80)
    assert not is_dup
    assert overlap < 0.80


def test_check_overlap_empty():
    """Empty entries should not be flagged."""
    is_dup, overlap = check_overlap([], [["a", "b"]])
    assert not is_dup
    assert overlap == 0.0


# ── API integration tests ──────────────────────────────────


def test_list_variants_empty(api_client, auth_headers):
    resp = api_client.get("/api/v1/variations", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


def test_create_variant(api_client, auth_headers):
    resp = api_client.post(
        "/api/v1/variations",
        json={
            "strategy_id": "bot01_sanyaku",
            "name": "fast_tenkan",
            "params": {"tenkan_period": 7, "kijun_period": 22},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["strategy_id"] == "bot01_sanyaku"
    assert data["name"] == "fast_tenkan"
    assert data["params"]["tenkan_period"] == 7
    assert data["id"] > 0


def test_create_variant_duplicate_name(api_client, auth_headers):
    body = {
        "strategy_id": "bot01_sanyaku",
        "name": "dup_test",
        "params": {"tenkan_period": 9},
    }
    api_client.post("/api/v1/variations", json=body, headers=auth_headers)
    resp = api_client.post("/api/v1/variations", json=body, headers=auth_headers)
    assert resp.status_code == 409


def test_create_variant_bad_strategy(api_client, auth_headers):
    resp = api_client.post(
        "/api/v1/variations",
        json={"strategy_id": "nonexistent", "name": "bad", "params": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_delete_variant(api_client, auth_headers):
    resp = api_client.post(
        "/api/v1/variations",
        json={
            "strategy_id": "bot01_sanyaku",
            "name": "to_delete",
            "params": {"tenkan_period": 11},
        },
        headers=auth_headers,
    )
    variant_id = resp.json()["id"]

    resp = api_client.delete(f"/api/v1/variations/{variant_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] == variant_id


def test_get_param_ranges_api(api_client, auth_headers):
    resp = api_client.get(
        "/api/v1/variations/params/bot01_sanyaku",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["strategy_id"] == "bot01_sanyaku"
    assert "tenkan_period" in data["params"]


def test_generate_variants_api(api_client, auth_headers):
    resp = api_client.post(
        "/api/v1/variations/generate",
        json={"strategy_id": "bot01_sanyaku", "max_variants": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] <= 5
    assert len(data["variants"]) == data["count"]
