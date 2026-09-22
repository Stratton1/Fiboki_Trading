"""Wave 3 — Agent / bot-lifecycle / strategy-lineage ledger API.

Read endpoints power the per-bot/strategy "Lineage / Audit" tab. Create
endpoints let agent lanes and operators append records out-of-process. There
are intentionally **no update or delete endpoints** — the ledger is write-once.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db import ledger_repository as ledger

router = APIRouter(tags=["agent-ledger"])


# ── Schemas ──────────────────────────────────────────────────────────────


class AgentRunCreate(BaseModel):
    lane: str
    agent_type: str | None = None
    actor: str = "agent"
    status: str = "started"
    prompt_hash: str | None = None
    code_diff_hash: str | None = None
    dataset_version: str | None = None
    summary: str | None = None
    detail_json: dict | None = None


class AgentRunResponse(BaseModel):
    id: int
    run_id: str
    lane: str
    agent_type: str | None
    actor: str
    status: str
    prompt_hash: str | None
    code_diff_hash: str | None
    dataset_version: str | None
    summary: str | None
    created_at: str

    @classmethod
    def of(cls, r) -> "AgentRunResponse":
        return cls(
            id=r.id, run_id=r.run_id, lane=r.lane, agent_type=r.agent_type,
            actor=r.actor, status=r.status, prompt_hash=r.prompt_hash,
            code_diff_hash=r.code_diff_hash, dataset_version=r.dataset_version,
            summary=r.summary, created_at=r.created_at.isoformat(),
        )


class LifecycleEventCreate(BaseModel):
    event_type: str
    actor: str = "agent"
    bot_id: str | None = None
    strategy_id: str | None = None
    variant_id: str | None = None
    source_strategy_id: str | None = None
    instrument: str | None = None
    timeframe: str | None = None
    agent_run_id: str | None = None
    backtest_result_id: str | None = None
    research_run_id: str | None = None
    oos_result_id: str | None = None
    monte_carlo_result_id: str | None = None
    dataset_version: str | None = None
    risk_decision: str | None = None
    approval_status: str | None = None
    reason: str | None = None
    stats_json: dict | None = None


class LifecycleEventResponse(BaseModel):
    id: int
    event_id: str
    event_type: str
    actor: str
    bot_id: str | None
    strategy_id: str | None
    variant_id: str | None
    agent_run_id: str | None
    approval_status: str | None
    risk_decision: str | None
    reason: str | None
    created_at: str

    @classmethod
    def of(cls, r) -> "LifecycleEventResponse":
        return cls(
            id=r.id, event_id=r.event_id, event_type=r.event_type, actor=r.actor,
            bot_id=r.bot_id, strategy_id=r.strategy_id, variant_id=r.variant_id,
            agent_run_id=r.agent_run_id, approval_status=r.approval_status,
            risk_decision=r.risk_decision, reason=r.reason,
            created_at=r.created_at.isoformat(),
        )


class LineageCreate(BaseModel):
    strategy_id: str
    parent_strategy_id: str | None = None
    variant_id: str | None = None
    origin: str = "hand_coded"
    actor: str = "system"
    agent_run_id: str | None = None
    code_diff_hash: str | None = None
    params_json: dict | None = None
    reason: str | None = None


class LineageResponse(BaseModel):
    id: int
    lineage_id: str
    strategy_id: str
    parent_strategy_id: str | None
    variant_id: str | None
    origin: str
    actor: str
    agent_run_id: str | None
    created_at: str

    @classmethod
    def of(cls, r) -> "LineageResponse":
        return cls(
            id=r.id, lineage_id=r.lineage_id, strategy_id=r.strategy_id,
            parent_strategy_id=r.parent_strategy_id, variant_id=r.variant_id,
            origin=r.origin, actor=r.actor, agent_run_id=r.agent_run_id,
            created_at=r.created_at.isoformat(),
        )


# ── Agent runs ───────────────────────────────────────────────────────────


@router.get("/agent-runs", response_model=list[AgentRunResponse])
def list_agent_runs(
    lane: str | None = None,
    limit: int = Query(100, le=500),
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return [AgentRunResponse.of(r) for r in ledger.list_agent_runs(db, lane=lane, limit=limit)]


@router.post("/agent-runs", response_model=AgentRunResponse, status_code=201)
def create_agent_run(
    body: AgentRunCreate,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AgentRunResponse.of(ledger.create_agent_run(db, body.model_dump()))


# ── Bot lifecycle ────────────────────────────────────────────────────────


@router.get("/bot-lifecycle", response_model=list[LifecycleEventResponse])
def list_bot_lifecycle(
    bot_id: str | None = None,
    strategy_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(200, le=1000),
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = ledger.list_lifecycle_events(
        db, bot_id=bot_id, strategy_id=strategy_id, event_type=event_type, limit=limit
    )
    return [LifecycleEventResponse.of(r) for r in rows]


@router.post("/bot-lifecycle", response_model=LifecycleEventResponse, status_code=201)
def create_bot_lifecycle(
    body: LifecycleEventCreate,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return LifecycleEventResponse.of(ledger.create_lifecycle_event(db, body.model_dump()))


# ── Strategy lineage ─────────────────────────────────────────────────────


@router.get("/strategy-lineage", response_model=list[LineageResponse])
def list_strategy_lineage(
    strategy_id: str | None = None,
    limit: int = Query(200, le=1000),
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = ledger.list_strategy_lineage(db, strategy_id=strategy_id, limit=limit)
    return [LineageResponse.of(r) for r in rows]


@router.post("/strategy-lineage", response_model=LineageResponse, status_code=201)
def create_strategy_lineage(
    body: LineageCreate,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return LineageResponse.of(ledger.create_strategy_lineage(db, body.model_dump()))
