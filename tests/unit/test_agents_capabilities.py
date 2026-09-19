"""The cardinal rule, tested as a property of the code rather than a promise.

If any test in this file fails, an agent can reach something it must never
reach. None of them should ever be weakened to make a feature work.
"""
from __future__ import annotations

import pytest

from fiboki.agents.capabilities import (
    ALL_CAPABILITIES,
    Capability,
    CapabilityResolver,
    ExecutionCapabilityError,
    Grant,
    PermissionDenied,
    _reads_as_execution_authority,
    assert_no_execution_capability,
    bundle,
)

# ---------------------------------------------------------------- the absence


def test_no_execution_capability_exists() -> None:
    """The guarantee is an absence, and the absence is checked mechanically."""
    assert_no_execution_capability()


@pytest.mark.parametrize(
    "forbidden",
    [
        "PLACE_ORDER",
        "SUBMIT_ORDER",
        "CANCEL_ORDER",
        "EXECUTE_TRADE",
        "SIZE_POSITION",
        "SET_RISK_LIMIT",
        "MODIFY_RISK_LIMITS",
        "DISABLE_KILL_SWITCH",
        "ENABLE_LIVE_EXECUTION",
        "ROUTE_BROKER",
        "WRITE_MARKET_DATA",
        "APPROVE_PROMOTION",
        "SET_LEVERAGE",
        "GO_LIVE",
    ],
)
def test_forbidden_names_are_recognised(forbidden: str) -> None:
    """The guard recognises the names someone would plausibly add."""
    assert _reads_as_execution_authority(forbidden) is not None


@pytest.mark.parametrize(
    "allowed",
    [
        "READ_MARKET_DATA",
        "READ_EXECUTION_TELEMETRY",
        "READ_TRADE_LEDGER",
        "READ_PORTFOLIO",
        "WRITE_HYPOTHESIS",
        "WRITE_STRATEGY_PROPOSAL",
        "SUBMIT_JOB",
    ],
)
def test_research_names_are_not_false_positives(allowed: str) -> None:
    """Reading about execution is not authority over it."""
    assert _reads_as_execution_authority(allowed) is None


def test_adding_an_execution_capability_raises() -> None:
    """Simulate the future mistake: the guard must reject it, loudly."""

    class Rogue(str):
        name = "PLACE_ORDER"
        value = "place:order"

    with pytest.raises(ExecutionCapabilityError, match="cardinal rule"):
        assert_no_execution_capability([Rogue()])  # type: ignore[list-item]


def test_every_capability_is_read_write_or_submit() -> None:
    """No capability escapes the three understood shapes."""
    for cap in Capability:
        assert cap.is_read or cap.is_write or cap is Capability.SUBMIT_JOB, cap


def test_no_capability_mentions_execution_verbs_in_its_value() -> None:
    for cap in Capability:
        assert "order" not in cap.value
        assert "broker" not in cap.value
        assert "kill" not in cap.value


# ------------------------------------------------------- deny by default


def test_unknown_principal_holds_nothing() -> None:
    resolver = CapabilityResolver()
    assert resolver.capabilities_for("nobody") == frozenset()
    assert resolver.role_of("nobody") is None
    assert not resolver.allows("nobody", Capability.READ_MARKET_DATA)


def test_ungranted_agent_can_do_nothing_at_all() -> None:
    """Deny-by-default: every capability, without exception."""
    resolver = CapabilityResolver()
    for cap in ALL_CAPABILITIES:
        assert not resolver.allows("fresh_agent", cap)
        with pytest.raises(PermissionDenied):
            resolver.require("fresh_agent", cap)


def test_grant_is_exact_not_inherited() -> None:
    resolver = CapabilityResolver()
    resolver.grant("a1", "reader", bundle(Capability.READ_MARKET_DATA))
    resolver.require("a1", Capability.READ_MARKET_DATA)
    with pytest.raises(PermissionDenied, match="read:regime"):
        resolver.require("a1", Capability.READ_REGIME)


def test_regrant_replaces_rather_than_accumulates() -> None:
    resolver = CapabilityResolver()
    resolver.grant("a1", "r1", bundle(Capability.READ_MARKET_DATA))
    resolver.grant("a1", "r2", bundle(Capability.READ_REGIME))
    assert resolver.capabilities_for("a1") == bundle(Capability.READ_REGIME)
    assert resolver.role_of("a1") == "r2"


def test_revoke_returns_to_denying_everything() -> None:
    resolver = CapabilityResolver()
    resolver.grant("a1", "r", ALL_CAPABILITIES)
    resolver.revoke("a1")
    assert resolver.capabilities_for("a1") == frozenset()


def test_cannot_grant_a_non_capability() -> None:
    resolver = CapabilityResolver()
    with pytest.raises(ValueError, match="unknown capabilities"):
        resolver.grant("a1", "r", ["place:order"])  # type: ignore[list-item]


def test_grant_rejects_non_capability_members() -> None:
    with pytest.raises(ValueError, match="non-Capability"):
        Grant(principal="a", role="r", capabilities=frozenset({"nope"}))  # type: ignore[arg-type]


def test_permission_denied_names_what_was_missing() -> None:
    resolver = CapabilityResolver()
    resolver.grant("a1", "r", bundle(Capability.READ_STRATEGY))
    with pytest.raises(PermissionDenied) as excinfo:
        resolver.require("a1", Capability.SUBMIT_JOB)
    assert "submit:job" in str(excinfo.value)
    assert "read:strategy" in str(excinfo.value)
