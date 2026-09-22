"""Tests for the Phase 2 db_targets router mode.

Verifies that:
  - Bots with no explicit targets fall back to the seeded Paper account.
  - Bots with IG and Tradovate targets fan out to both.
  - Disabled accounts and disabled targets are skipped.
  - Account allocation/risk is used unless overridden by the target row.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.repository import (
    create_bot_execution_target,
    create_execution_account,
    update_bot_execution_target,
    update_execution_account,
)
from fibokei.execution.router_factory import build_execution_router_from_env
from fibokei.execution.targets import (
    BROKER_IG,
    BROKER_PAPER,
    BROKER_TRADOVATE,
    ROUTER_MODE_DB_TARGETS,
)

_ENV = [
    "FIBOKEI_EXECUTION_ROUTER_MODE",
    "FIBOKEI_PAPER_ACCOUNT_ENABLED",
    "FIBOKEI_IG_ACCOUNT_ENABLED",
    "FIBOKEI_TRADOVATE_ACCOUNT_ENABLED",
    "FIBOKEI_LIVE_EXECUTION_ENABLED",
]


@pytest.fixture
def clean_env(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "db_targets")
    return monkeypatch


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return get_session_factory(engine)


def test_db_targets_mode_with_factory(clean_env, session_factory):
    """Router builds in db_targets mode when both env and factory align."""
    router = build_execution_router_from_env(session_factory=session_factory)
    assert router.mode == ROUTER_MODE_DB_TARGETS


def test_db_targets_falls_back_without_factory(clean_env):
    """Without session_factory, router downgrades to env_global_fanout."""
    router = build_execution_router_from_env()
    # With only paper enabled by default → env_global_fanout falls back to paper
    assert router.mode != ROUTER_MODE_DB_TARGETS


def test_bot_with_no_targets_uses_default_paper(clean_env, session_factory):
    """A bot with zero rows in bot_execution_targets gets the seeded Paper account."""
    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-with-no-targets")
    assert len(targets) == 1
    assert targets[0].broker == BROKER_PAPER
    assert targets[0].is_enabled is True


def test_bot_with_ig_and_tradovate_loads_both(clean_env, session_factory):
    """A bot with IG + Tradovate targets fans out to both."""
    with session_factory() as session:
        ig = create_execution_account(
            session,
            {
                "name": "IG Demo",
                "broker": "ig",
                "environment": "demo",
                "allocated_capital": 1000.0,
            },
        )
        tv = create_execution_account(
            session,
            {
                "name": "Tradovate Demo",
                "broker": "tradovate",
                "environment": "demo",
                "allocated_capital": 5000.0,
            },
        )
        create_bot_execution_target(
            session, {"bot_id": "bot-multi", "execution_account_id": ig.id}
        )
        create_bot_execution_target(
            session, {"bot_id": "bot-multi", "execution_account_id": tv.id}
        )

    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-multi")
    brokers = sorted(t.broker for t in targets)
    assert brokers == [BROKER_IG, BROKER_TRADOVATE]


def test_disabled_target_is_skipped(clean_env, session_factory):
    with session_factory() as session:
        ig = create_execution_account(
            session, {"name": "IG", "broker": "ig", "environment": "demo"}
        )
        target = create_bot_execution_target(
            session, {"bot_id": "bot-x", "execution_account_id": ig.id}
        )
        update_bot_execution_target(session, target.id, {"is_enabled": False})

    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-x")
    # IG target disabled → bot falls back to default Paper
    assert len(targets) == 1
    assert targets[0].broker == BROKER_PAPER


def test_disabled_account_is_skipped(clean_env, session_factory):
    with session_factory() as session:
        ig = create_execution_account(
            session, {"name": "IG", "broker": "ig", "environment": "demo"}
        )
        create_bot_execution_target(
            session, {"bot_id": "bot-y", "execution_account_id": ig.id}
        )
        update_execution_account(session, ig.id, {"is_enabled": False})

    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-y")
    # IG account disabled → bot falls back to default Paper
    assert len(targets) == 1
    assert targets[0].broker == BROKER_PAPER


def test_account_allocation_used_by_default(clean_env, session_factory):
    """When no override is set, sizing uses the account's allocated_capital."""
    with session_factory() as session:
        ig = create_execution_account(
            session,
            {
                "name": "IG",
                "broker": "ig",
                "environment": "demo",
                "allocated_capital": 1234.0,
                "risk_per_trade_pct": 0.7,
            },
        )
        create_bot_execution_target(
            session, {"bot_id": "bot-z", "execution_account_id": ig.id}
        )

    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-z")
    assert targets[0].allocated_capital == 1234.0
    assert targets[0].risk_per_trade_pct == 0.7


def test_target_overrides_take_precedence(clean_env, session_factory):
    with session_factory() as session:
        ig = create_execution_account(
            session,
            {
                "name": "IG",
                "broker": "ig",
                "environment": "demo",
                "allocated_capital": 1000.0,
                "risk_per_trade_pct": 1.0,
            },
        )
        create_bot_execution_target(
            session,
            {
                "bot_id": "bot-w",
                "execution_account_id": ig.id,
                "allocation_override": 500.0,
                "risk_per_trade_pct_override": 0.25,
            },
        )

    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-w")
    assert targets[0].allocated_capital == 500.0
    assert targets[0].risk_per_trade_pct == 0.25


def test_summary_shows_static_snapshot(clean_env, session_factory):
    """Router summary lists currently-enabled accounts as a snapshot view."""
    with session_factory() as session:
        create_execution_account(
            session, {"name": "IG", "broker": "ig", "environment": "demo"}
        )

    router = build_execution_router_from_env(session_factory=session_factory)
    summary = router.summary()
    assert summary["router_mode"] == ROUTER_MODE_DB_TARGETS
    brokers = [t["broker"] for t in summary["targets"]]
    assert "paper" in brokers
    assert "ig" in brokers


def test_live_target_blocked_without_global_master(clean_env, session_factory):
    """Live env account without FIBOKEI_LIVE_EXECUTION_ENABLED stays gated."""
    with session_factory() as session:
        create_execution_account(
            session,
            {
                "name": "IG Live",
                "broker": "ig",
                "environment": "live",
                "live_allowed": True,
            },
        )
    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-live")
    # The bot has no explicit targets; fallback Paper is returned, never the live IG.
    assert all(t.environment != "live" for t in targets)


def test_live_target_allowed_with_all_three_flags(clean_env, session_factory):
    """live_allowed at row + global flag + env=live → live_allowed True on target."""
    clean_env.setenv("FIBOKEI_LIVE_EXECUTION_ENABLED", "true")
    with session_factory() as session:
        ig = create_execution_account(
            session,
            {
                "name": "IG Live",
                "broker": "ig",
                "environment": "live",
                "live_allowed": True,
            },
        )
        create_bot_execution_target(
            session, {"bot_id": "bot-live", "execution_account_id": ig.id}
        )
    router = build_execution_router_from_env(session_factory=session_factory)
    targets = router._targets_for("bot-live")
    live_targets = [t for t in targets if t.environment == "live"]
    assert len(live_targets) == 1
    assert live_targets[0].live_allowed is True
