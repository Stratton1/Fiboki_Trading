"""Execution control API — kill switch, audit logs, IG demo status."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.core.feature_flags import FeatureFlags
from fibokei.db.repository import (
    activate_kill_switch,
    deactivate_kill_switch,
    get_execution_audit,
    get_kill_switch,
)

router = APIRouter(tags=["execution"])


# ---------- Schemas ----------

class KillSwitchResponse(BaseModel):
    is_active: bool
    reason: str | None = None
    activated_by: str | None = None
    activated_at: str | None = None


class KillSwitchRequest(BaseModel):
    reason: str = ""


class ExecutionModeResponse(BaseModel):
    mode: str  # "paper", "ig_demo"
    live_execution_enabled: bool
    ig_paper_mode: bool
    kill_switch_active: bool


class AuditEntryResponse(BaseModel):
    id: int
    timestamp: str
    execution_mode: str
    action: str
    instrument: str
    direction: str | None = None
    size: float | None = None
    deal_id: str | None = None
    status: str
    error_message: str | None = None
    bot_id: str | None = None


# ---------- Endpoints ----------

@router.get("/execution/mode", response_model=ExecutionModeResponse)
def get_execution_mode(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get current execution mode and safety status."""
    flags = FeatureFlags()
    ks = get_kill_switch(db)
    return ExecutionModeResponse(
        mode=flags.execution_mode,
        live_execution_enabled=flags.live_execution_enabled,
        ig_paper_mode=flags.ig_paper_mode,
        kill_switch_active=ks.is_active,
    )


@router.get("/execution/kill-switch", response_model=KillSwitchResponse)
def get_kill_switch_status(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get kill switch status."""
    ks = get_kill_switch(db)
    return KillSwitchResponse(
        is_active=ks.is_active,
        reason=ks.reason,
        activated_by=ks.activated_by,
        activated_at=ks.activated_at.isoformat() if ks.activated_at else None,
    )


@router.post("/execution/kill-switch/activate", response_model=KillSwitchResponse)
def activate_kill_switch_endpoint(
    req: KillSwitchRequest,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Activate the kill switch — stops all execution immediately."""
    ks = activate_kill_switch(db, reason=req.reason, activated_by=user.username)
    return KillSwitchResponse(
        is_active=ks.is_active,
        reason=ks.reason,
        activated_by=ks.activated_by,
        activated_at=ks.activated_at.isoformat() if ks.activated_at else None,
    )


@router.post("/execution/kill-switch/deactivate", response_model=KillSwitchResponse)
def deactivate_kill_switch_endpoint(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deactivate the kill switch — resume execution."""
    ks = deactivate_kill_switch(db)
    return KillSwitchResponse(
        is_active=ks.is_active,
        reason=ks.reason,
        activated_by=ks.activated_by,
        activated_at=ks.activated_at.isoformat() if ks.activated_at else None,
    )


@router.get("/execution/audit", response_model=list[AuditEntryResponse])
def get_audit_log(
    execution_mode: str | None = None,
    bot_id: str | None = None,
    limit: int = 100,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get execution audit log entries."""
    entries = get_execution_audit(db, execution_mode=execution_mode, bot_id=bot_id, limit=limit)
    return [
        AuditEntryResponse(
            id=e.id,
            timestamp=e.timestamp.isoformat() if e.timestamp else "",
            execution_mode=e.execution_mode,
            action=e.action,
            instrument=e.instrument,
            direction=e.direction,
            size=e.size,
            deal_id=e.deal_id,
            status=e.status,
            error_message=e.error_message,
            bot_id=e.bot_id,
        )
        for e in entries
    ]
