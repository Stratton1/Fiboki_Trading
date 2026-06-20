"""Regression tests for strategy registry health and tier classification.

Locks the registered count, canonical/experimental split, and registry/disk
parity so the strategy count can never silently drift again (the cause of
repeated count-assertion churn). Pure/offline — no network, no DB.
"""

from fibokei.strategies.registry import (
    CANONICAL_STRATEGY_IDS,
    EXPECTED_MIN_STRATEGIES,
    classify_strategy,
    strategy_registry,
)


def test_canonical_set_is_twelve():
    assert len(CANONICAL_STRATEGY_IDS) == 12
    assert EXPECTED_MIN_STRATEGIES == 12


def test_registry_health_counts():
    health = strategy_registry.registry_health()
    # 12 canonical + 9 extended (bot01-13, 15-22) + 25 traditional_gen1.
    assert health["canonical_count"] == 12
    assert health["registered_count"] >= EXPECTED_MIN_STRATEGIES
    # Every registered strategy falls into exactly one tier — the tier counts
    # must sum to the total (no strategy lost or double-counted).
    assert health["registered_count"] == sum(health["tier_counts"].values())


def test_registry_matches_disk():
    """Every strategy file on disk must be registered (no orphans)."""
    health = strategy_registry.registry_health()
    assert health["unregistered_files"] == []
    assert health["healthy"] is True


def test_classify_strategy_tiers():
    assert classify_strategy("bot01_sanyaku") == "canonical"
    assert classify_strategy("bot12_kumo_fib_tz") == "canonical"
    assert classify_strategy("bot22_fib_volume") == "experimental"
    assert classify_strategy("nonexistent") == "experimental"
    # Strategy Factory tiers are identified by compiled-id prefix.
    assert classify_strategy("factory_trad_ema_crossover_v1") == "traditional_gen1"
    assert classify_strategy("factory_hyb_anything_v1") == "hybrid_gen1"


def test_list_available_includes_tier():
    items = strategy_registry.list_available()
    assert items, "registry should not be empty"
    assert all("tier" in i for i in items)
    valid = {"canonical", "experimental", "traditional_gen1", "hybrid_gen1"}
    assert all(i["tier"] in valid for i in items)
