"""Append-only repository for the Wave 3 agent / lifecycle / lineage ledger.

This module is the ONLY sanctioned way to write to the ledger tables, and it
deliberately exposes **no update or delete functions** — the audit trail is
write-once. Enforcing immutability in code (rather than relying on DB triggers
that differ between SQLite dev and Postgres prod) keeps the guarantee portable
and testable. The accompanying test asserts no mutation function exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from fibokei.db.models import (
    AgentRunModel,
    BotLifecycleEventModel,
    StrategyLineageModel,
)

# Canonical lifecycle event vocabulary. Recording an unknown event_type is
# rejected so the ledger stays queryable and analytics don't silently miss
# transitions.
LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset({
    "created", "generated", "cloned", "mutated", "backtested", "validated",
    "rejected", "shortlisted", "promoted_to_paper", "demoted_from_paper",
    "promoted_to_demo", "demoted_from_demo", "proposed_for_live",
    "approved_for_live", "disabled", "archived", "manual_override",
    "agent_decision", "safety_veto",
})

AGENT_LANES: frozenset[str] = frozenset(
    {"builder", "quant_auditor", "operator", "safety_governor"}
)

ACTORS: frozenset[str] = frozenset({"human", "agent", "system"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


# ── Create (append-only) ─────────────────────────────────────────────────


def create_agent_run(session: Session, data: dict) -> AgentRunModel:
    """Append an agent-run record. ``run_id`` is generated if not supplied."""
    lane = data.get("lane")
    if lane not in AGENT_LANES:
        raise ValueError(f"Unknown agent lane: {lane!r}. Must be one of {sorted(AGENT_LANES)}")
    actor = data.get("actor", "agent")
    if actor not in ACTORS:
        raise ValueError(f"Unknown actor: {actor!r}")
    row = AgentRunModel(
        run_id=data.get("run_id") or _new_id(),
        lane=lane,
        agent_type=data.get("agent_type"),
        actor=actor,
        status=data.get("status", "started"),
        prompt_hash=data.get("prompt_hash"),
        code_diff_hash=data.get("code_diff_hash"),
        dataset_version=data.get("dataset_version"),
        summary=data.get("summary"),
        detail_json=data.get("detail_json"),
        created_at=_now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_lifecycle_event(session: Session, data: dict) -> BotLifecycleEventModel:
    """Append a bot/strategy lifecycle event with full provenance."""
    event_type = data.get("event_type")
    if event_type not in LIFECYCLE_EVENT_TYPES:
        raise ValueError(
            f"Unknown event_type: {event_type!r}. "
            f"Must be one of {sorted(LIFECYCLE_EVENT_TYPES)}"
        )
    actor = data.get("actor", "agent")
    if actor not in ACTORS:
        raise ValueError(f"Unknown actor: {actor!r}")
    row = BotLifecycleEventModel(
        event_id=data.get("event_id") or _new_id(),
        event_type=event_type,
        actor=actor,
        bot_id=data.get("bot_id"),
        strategy_id=data.get("strategy_id"),
        variant_id=data.get("variant_id"),
        source_strategy_id=data.get("source_strategy_id"),
        instrument=data.get("instrument"),
        timeframe=data.get("timeframe"),
        agent_run_id=data.get("agent_run_id"),
        backtest_result_id=data.get("backtest_result_id"),
        research_run_id=data.get("research_run_id"),
        oos_result_id=data.get("oos_result_id"),
        monte_carlo_result_id=data.get("monte_carlo_result_id"),
        dataset_version=data.get("dataset_version"),
        risk_decision=data.get("risk_decision"),
        approval_status=data.get("approval_status"),
        reason=data.get("reason"),
        stats_json=data.get("stats_json"),
        created_at=_now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_strategy_lineage(session: Session, data: dict) -> StrategyLineageModel:
    """Append a strategy lineage (parent→child provenance) record."""
    row = StrategyLineageModel(
        lineage_id=data.get("lineage_id") or _new_id(),
        strategy_id=data["strategy_id"],
        parent_strategy_id=data.get("parent_strategy_id"),
        variant_id=data.get("variant_id"),
        origin=data.get("origin", "hand_coded"),
        actor=data.get("actor", "system"),
        agent_run_id=data.get("agent_run_id"),
        code_diff_hash=data.get("code_diff_hash"),
        params_json=data.get("params_json"),
        reason=data.get("reason"),
        created_at=_now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── Read ─────────────────────────────────────────────────────────────────


def list_agent_runs(session: Session, lane: str | None = None, limit: int = 100):
    stmt = select(AgentRunModel)
    if lane:
        stmt = stmt.where(AgentRunModel.lane == lane)
    stmt = stmt.order_by(AgentRunModel.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def list_lifecycle_events(
    session: Session,
    bot_id: str | None = None,
    strategy_id: str | None = None,
    event_type: str | None = None,
    limit: int = 200,
):
    stmt = select(BotLifecycleEventModel)
    if bot_id:
        stmt = stmt.where(BotLifecycleEventModel.bot_id == bot_id)
    if strategy_id:
        stmt = stmt.where(BotLifecycleEventModel.strategy_id == strategy_id)
    if event_type:
        stmt = stmt.where(BotLifecycleEventModel.event_type == event_type)
    stmt = stmt.order_by(BotLifecycleEventModel.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def list_strategy_lineage(
    session: Session, strategy_id: str | None = None, limit: int = 200
):
    stmt = select(StrategyLineageModel)
    if strategy_id:
        stmt = stmt.where(StrategyLineageModel.strategy_id == strategy_id)
    stmt = stmt.order_by(StrategyLineageModel.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())
