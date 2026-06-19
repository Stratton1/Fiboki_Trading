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
    # 12 canonical + 9 extended currently registered (bot01-13, 15-22).
    assert health["canonical_count"] == 12
    assert health["registered_count"] >= EXPECTED_MIN_STRATEGIES
    assert health["registered_count"] == (
        health["canonical_count"] + health["experimental_count"]
    )


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


def test_list_available_includes_tier():
    items = strategy_registry.list_available()
    assert items, "registry should not be empty"
    assert all("tier" in i for i in items)
    assert all(i["tier"] in ("canonical", "experimental") for i in items)
