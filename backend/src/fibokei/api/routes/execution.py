"""Execution control API — kill switch, audit logs, IG demo status."""

from typing import TYPE_CHECKING

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
    get_slippage_summary,
)

if TYPE_CHECKING:
    # Imported only for type-annotation resolution. The runtime path
    # delays the IGClient import so this module doesn't depend on the IG
    # adapter being importable before its lazy-init helper runs.
    from fibokei.execution.ig_client import IGClient

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
    requested_price: float | None = None
    filled_price: float | None = None
    slippage_pips: float | None = None
    fill_latency_ms: int | None = None
    detail_json: dict | None = None


class SlippageInstrumentResponse(BaseModel):
    instrument: str
    fills: int
    avg_slippage_pips: float
    max_slippage_pips: float
    min_slippage_pips: float
    avg_latency_ms: float


class SlippageSummaryResponse(BaseModel):
    total_fills: int
    avg_slippage_pips: float
    instruments: list[SlippageInstrumentResponse]


class IGHealthResponse(BaseModel):
    configured: bool       # Credentials present in env
    reachable: bool        # Demo API responded to auth attempt
    account_id: str | None = None
    account_name: str | None = None
    balance: float | None = None
    error: str | None = None


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
            requested_price=e.requested_price,
            filled_price=e.filled_price,
            slippage_pips=e.slippage_pips,
            fill_latency_ms=e.fill_latency_ms,
        )
        for e in entries
    ]


# Module-level IG client singleton — avoids re-authenticating (and
# triggering IG's account-switch endpoint) on every health-check poll.
# The client's ensure_session() handles token refresh internally.
_ig_health_client: "IGClient | None" = None


def _get_ig_health_client() -> "IGClient":
    """Return the singleton IGClient, creating it on first use."""
    global _ig_health_client
    from fibokei.execution.ig_client import IGClient
    if _ig_health_client is None:
        _ig_health_client = IGClient()
    return _ig_health_client


@router.get("/execution/ig-health", response_model=IGHealthResponse)
def get_ig_health(
    user: TokenData = Depends(get_current_user),
):
    """Check IG demo connectivity without placing any orders.

    Attempts to authenticate with the IG demo API and fetch account info.
    Returns credential presence, reachability, and account balance.
    Uses a cached IGClient so it does not re-authenticate on every poll —
    IG rate-limits rapid repeated logins and account switches.
    """
    import os

    from fibokei.execution.ig_client import IGClientError

    api_key = os.environ.get("FIBOKEI_IG_API_KEY", "")
    username = os.environ.get("FIBOKEI_IG_USERNAME", "")
    password = os.environ.get("FIBOKEI_IG_PASSWORD", "")
    configured = bool(api_key and username and password)

    if not configured:
        return IGHealthResponse(
            configured=False, reachable=False, error="IG credentials not configured"
        )

    try:
        client = _get_ig_health_client()
        client.ensure_session()  # re-authenticates only if session expired
        acct = client.get_account_info()
        balance_info = acct.get("balance", {})
        return IGHealthResponse(
            configured=True,
            reachable=True,
            account_id=acct.get("accountId"),
            account_name=acct.get("accountName"),
            balance=balance_info.get("balance"),
        )
    except IGClientError as e:
        # Reset the cached client on auth errors so the next poll tries fresh
        global _ig_health_client
        _ig_health_client = None
        return IGHealthResponse(configured=True, reachable=False, error=str(e))
    except Exception as e:
        return IGHealthResponse(configured=True, reachable=False, error=f"Unexpected error: {e}")


@router.get("/execution/slippage", response_model=SlippageSummaryResponse)
def get_slippage(
    instrument: str | None = None,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get slippage analytics summary, optionally filtered by instrument."""
    summary = get_slippage_summary(db, instrument=instrument)
    return summary


# ── Multi-broker (Phase 1 fan-out) endpoints ─────────────────────────────


class TradovateHealthResponse(BaseModel):
    configured: bool
    reachable: bool
    env: str
    account_id: str | None = None
    account_name: str | None = None
    supported_symbols_count: int = 0
    error: str | None = None


class RouterTargetView(BaseModel):
    target_id: str
    name: str
    broker: str
    environment: str
    is_enabled: bool
    live_allowed: bool
    allocated_capital: float
    risk_per_trade_pct: float


class RouterStateResponse(BaseModel):
    router_mode: str
    kill_switch_active: bool
    targets: list[RouterTargetView]
    warning: str | None = None


@router.get("/execution/tradovate-health", response_model=TradovateHealthResponse)
def get_tradovate_health(
    user: TokenData = Depends(get_current_user),
):
    """Check Tradovate readiness without placing any orders.

    Probe builds a fresh adapter+client, runs healthcheck, and returns
    a small summary. Demo by default. Live is hard-blocked unless the
    operator has explicitly opted in.
    """
    from fibokei.execution.tradovate_adapter import TradovateExecutionAdapter

    adapter = TradovateExecutionAdapter()
    info = adapter.healthcheck()
    return TradovateHealthResponse(
        configured=bool(info.get("configured", False)),
        reachable=bool(info.get("reachable", False)),
        env=str(info.get("env", "demo")),
        account_id=info.get("account_id"),
        account_name=info.get("account_name"),
        supported_symbols_count=int(info.get("supported_symbols_count", 0) or 0),
        error=info.get("error"),
    )


class ExecutionAccountResponse(BaseModel):
    id: int
    name: str
    broker: str
    environment: str
    broker_account_id: str | None = None
    base_currency: str = "GBP"
    starting_balance: float = 1000.0
    allocated_capital: float = 1000.0
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 4.0
    max_weekly_loss_pct: float = 8.0
    max_open_positions: int = 20
    is_enabled: bool = True
    is_default: bool = False
    live_allowed: bool = False
    config_json: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CreateExecutionAccountRequest(BaseModel):
    name: str
    broker: str  # paper|ig|tradovate
    environment: str = "paper"  # paper|demo|live
    broker_account_id: str | None = None
    base_currency: str = "GBP"
    starting_balance: float = 1000.0
    allocated_capital: float = 1000.0
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 4.0
    max_weekly_loss_pct: float = 8.0
    max_open_positions: int = 20
    is_enabled: bool = True
    is_default: bool = False
    live_allowed: bool = False
    config_json: dict | None = None


class UpdateExecutionAccountRequest(BaseModel):
    name: str | None = None
    broker_account_id: str | None = None
    base_currency: str | None = None
    starting_balance: float | None = None
    allocated_capital: float | None = None
    risk_per_trade_pct: float | None = None
    max_daily_loss_pct: float | None = None
    max_weekly_loss_pct: float | None = None
    max_open_positions: int | None = None
    is_enabled: bool | None = None
    is_default: bool | None = None
    live_allowed: bool | None = None
    config_json: dict | None = None


class BotExecutionTargetResponse(BaseModel):
    id: int
    bot_id: str
    execution_account_id: int
    is_enabled: bool = True
    allocation_override: float | None = None
    risk_per_trade_pct_override: float | None = None
    sizing_mode: str = "static_allocation"
    config_json: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Nested account view for convenient frontend rendering.
    account: ExecutionAccountResponse | None = None


class CreateBotExecutionTargetRequest(BaseModel):
    execution_account_id: int
    is_enabled: bool = True
    allocation_override: float | None = None
    risk_per_trade_pct_override: float | None = None
    sizing_mode: str = "static_allocation"
    config_json: dict | None = None


class UpdateBotExecutionTargetRequest(BaseModel):
    is_enabled: bool | None = None
    allocation_override: float | None = None
    risk_per_trade_pct_override: float | None = None
    sizing_mode: str | None = None
    config_json: dict | None = None


def _account_to_response(acct) -> ExecutionAccountResponse:
    return ExecutionAccountResponse(
        id=acct.id,
        name=acct.name,
        broker=acct.broker,
        environment=acct.environment,
        broker_account_id=acct.broker_account_id,
        base_currency=acct.base_currency,
        starting_balance=acct.starting_balance,
        allocated_capital=acct.allocated_capital,
        risk_per_trade_pct=acct.risk_per_trade_pct,
        max_daily_loss_pct=acct.max_daily_loss_pct,
        max_weekly_loss_pct=acct.max_weekly_loss_pct,
        max_open_positions=acct.max_open_positions,
        is_enabled=acct.is_enabled,
        is_default=acct.is_default,
        live_allowed=acct.live_allowed,
        config_json=acct.config_json,
        created_at=acct.created_at.isoformat() if acct.created_at else None,
        updated_at=acct.updated_at.isoformat() if acct.updated_at else None,
    )


def _target_to_response(target, account=None) -> BotExecutionTargetResponse:
    return BotExecutionTargetResponse(
        id=target.id,
        bot_id=target.bot_id,
        execution_account_id=target.execution_account_id,
        is_enabled=target.is_enabled,
        allocation_override=target.allocation_override,
        risk_per_trade_pct_override=target.risk_per_trade_pct_override,
        sizing_mode=target.sizing_mode,
        config_json=target.config_json,
        created_at=target.created_at.isoformat() if target.created_at else None,
        updated_at=target.updated_at.isoformat() if target.updated_at else None,
        account=_account_to_response(account) if account else None,
    )


@router.get("/execution/accounts", response_model=list[ExecutionAccountResponse])
def list_execution_accounts_endpoint(
    enabled_only: bool = False,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all execution accounts, oldest first."""
    from fibokei.db.repository import list_execution_accounts

    accounts = list_execution_accounts(db, enabled_only=enabled_only)
    return [_account_to_response(a) for a in accounts]


@router.get("/execution/accounts/{account_id}", response_model=ExecutionAccountResponse)
def get_execution_account_endpoint(
    account_id: int,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fibokei.db.repository import get_execution_account

    acct = get_execution_account(db, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Execution account not found")
    return _account_to_response(acct)


@router.post("/execution/accounts", response_model=ExecutionAccountResponse)
def create_execution_account_endpoint(
    req: CreateExecutionAccountRequest,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new execution account.

    Names must be unique. ``broker`` ∈ ``{paper, ig, tradovate}``;
    ``environment`` ∈ ``{paper, demo, live}``. Live setup is gated by the
    router separately — creating a live-environment account does not by
    itself enable real-money execution.
    """
    from fibokei.db.repository import (
        create_execution_account,
        get_execution_account_by_name,
    )

    if req.broker not in ("paper", "ig", "tradovate"):
        raise HTTPException(status_code=400, detail=f"Unknown broker: {req.broker}")
    if req.environment not in ("paper", "demo", "live"):
        raise HTTPException(
            status_code=400, detail=f"Unknown environment: {req.environment}"
        )
    existing = get_execution_account_by_name(db, req.name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Execution account with name '{req.name}' already exists",
        )

    acct = create_execution_account(db, req.model_dump())
    return _account_to_response(acct)


@router.patch("/execution/accounts/{account_id}", response_model=ExecutionAccountResponse)
def update_execution_account_endpoint(
    account_id: int,
    req: UpdateExecutionAccountRequest,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Patch fields on an execution account. Identity fields cannot be changed."""
    from fibokei.db.repository import update_execution_account

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    acct = update_execution_account(db, account_id, updates)
    if acct is None:
        raise HTTPException(status_code=404, detail="Execution account not found")
    return _account_to_response(acct)


@router.get("/execution/accounts/{account_id}/status")
def get_execution_account_status_endpoint(
    account_id: int,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Operator readiness summary for a single account.

    Reports broker, environment, enabled state, allocated capital, and a
    broker-specific ``configured`` flag indicating whether credentials are
    set in env. Does NOT call the broker — safe to poll.
    """
    import os as _os

    from fibokei.db.repository import get_execution_account

    acct = get_execution_account(db, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Execution account not found")

    if acct.broker == "ig":
        configured = bool(
            _os.environ.get("FIBOKEI_IG_API_KEY")
            and _os.environ.get("FIBOKEI_IG_USERNAME")
            and _os.environ.get("FIBOKEI_IG_PASSWORD")
        )
    elif acct.broker == "tradovate":
        configured = bool(
            _os.environ.get("FIBOKEI_TRADOVATE_USERNAME")
            and _os.environ.get("FIBOKEI_TRADOVATE_PASSWORD")
            and _os.environ.get("FIBOKEI_TRADOVATE_CID")
            and _os.environ.get("FIBOKEI_TRADOVATE_SECRET")
        )
    else:  # paper
        configured = True

    # Phase 4: surface live risk state when available. We read directly via
    # AccountRiskEngine using the API process's session_factory so the
    # response is current without a worker round-trip.
    risk_state: dict | None = None
    try:
        from fibokei.execution.account_risk import AccountRiskEngine

        engine = AccountRiskEngine(lambda: db.__class__(bind=db.get_bind()))
        snap = engine.state_for(account_id)
        if snap is not None:
            risk_state = {
                "open_positions": snap.open_positions,
                "max_open_positions": snap.max_open_positions,
                "daily_realised_pnl": snap.daily_realised_pnl,
                "weekly_realised_pnl": snap.weekly_realised_pnl,
                "daily_dd_pct": round(snap.daily_dd_pct, 2),
                "weekly_dd_pct": round(snap.weekly_dd_pct, 2),
                "max_daily_loss_pct": snap.max_daily_loss_pct,
                "max_weekly_loss_pct": snap.max_weekly_loss_pct,
                "blocked": snap.blocked,
                "block_reason": snap.block_reason or None,
                "block_code": snap.block_code or None,
            }
    except Exception:  # pragma: no cover — degrade gracefully
        risk_state = None

    return {
        "id": acct.id,
        "name": acct.name,
        "broker": acct.broker,
        "environment": acct.environment,
        "is_enabled": acct.is_enabled,
        "live_allowed": acct.live_allowed,
        "allocated_capital": acct.allocated_capital,
        "risk_per_trade_pct": acct.risk_per_trade_pct,
        "configured": configured,
        "risk_state": risk_state,
    }


@router.get("/execution/accounts/{account_id}/reconcile")
def reconcile_execution_account_endpoint(
    account_id: int,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Phase 5: per-account reconciliation status.

    Returns a typed ``status`` (clean / mismatch / unavailable /
    credentials_missing / unsupported) plus mismatch details. Never raises
    — broker networking errors are surfaced as ``unavailable`` so the
    System page can render a clean status pill.
    """
    from fibokei.db.repository import (
        get_execution_account,
        get_paper_bots,
    )
    from fibokei.execution.reconciliation import reconcile_account

    acct = get_execution_account(db, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Execution account not found")

    # Phase 5 stub: pull tracked positions from in-memory paper bots' DB
    # rows. A future Phase will pull these from execution_attempts joined
    # to bot_signals so reconciliation reflects the broker side per-target.
    fiboki_positions: list[dict] = []
    for bot in get_paper_bots(db):
        if bot.position_json:
            fiboki_positions.append({
                "deal_id": bot.position_json.get("trade_id"),
                "instrument": bot.instrument,
                "direction": bot.position_json.get("direction"),
                "size": bot.position_json.get("position_size"),
            })

    status = reconcile_account(acct, fiboki_positions)
    return {
        "account_id": status.account_id,
        "account_name": status.account_name,
        "broker": status.broker,
        "environment": status.environment,
        "status": status.status,
        "fiboki_position_count": status.fiboki_position_count,
        "broker_position_count": status.broker_position_count,
        "matched": status.matched,
        "mismatch_count": status.mismatch_count,
        "detail": status.detail,
        "mismatches": [
            {
                "type": m.type,
                "instrument": m.instrument,
                "fiboki_deal_id": m.fiboki_deal_id,
                "broker_deal_id": m.broker_deal_id,
                "detail": m.detail,
            }
            for m in status.mismatches
        ],
    }


@router.get(
    "/paper/bots/{bot_id}/targets",
    response_model=list[BotExecutionTargetResponse],
)
def list_bot_targets_endpoint(
    bot_id: str,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List execution targets attached to a bot."""
    from fibokei.db.repository import (
        get_execution_account,
        list_bot_execution_targets,
    )

    targets = list_bot_execution_targets(db, bot_id=bot_id)
    out: list[BotExecutionTargetResponse] = []
    for t in targets:
        acct = get_execution_account(db, t.execution_account_id)
        out.append(_target_to_response(t, acct))
    return out


@router.post(
    "/paper/bots/{bot_id}/targets",
    response_model=BotExecutionTargetResponse,
)
def create_bot_target_endpoint(
    bot_id: str,
    req: CreateBotExecutionTargetRequest,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Attach an execution target to a bot.

    The (bot_id, execution_account_id) pair must be unique. To re-enable a
    previously-disabled target, ``PATCH`` the existing row instead.
    """
    from sqlalchemy.exc import IntegrityError

    from fibokei.db.repository import (
        create_bot_execution_target,
        get_execution_account,
    )

    acct = get_execution_account(db, req.execution_account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Execution account not found")

    payload = {"bot_id": bot_id, **req.model_dump()}
    try:
        target = create_bot_execution_target(db, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Bot '{bot_id}' already has a target on account {req.execution_account_id}; "
                "PATCH the existing row instead."
            ),
        ) from None
    return _target_to_response(target, acct)


@router.patch(
    "/paper/bots/{bot_id}/targets/{target_id}",
    response_model=BotExecutionTargetResponse,
)
def update_bot_target_endpoint(
    bot_id: str,
    target_id: int,
    req: UpdateBotExecutionTargetRequest,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fibokei.db.repository import (
        get_bot_execution_target,
        get_execution_account,
        update_bot_execution_target,
    )

    target = get_bot_execution_target(db, target_id)
    if target is None or target.bot_id != bot_id:
        raise HTTPException(status_code=404, detail="Bot execution target not found")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    target = update_bot_execution_target(db, target_id, updates)
    acct = get_execution_account(db, target.execution_account_id)
    return _target_to_response(target, acct)


@router.delete("/paper/bots/{bot_id}/targets/{target_id}")
def delete_bot_target_endpoint(
    bot_id: str,
    target_id: int,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fibokei.db.repository import (
        delete_bot_execution_target,
        get_bot_execution_target,
    )

    target = get_bot_execution_target(db, target_id)
    if target is None or target.bot_id != bot_id:
        raise HTTPException(status_code=404, detail="Bot execution target not found")
    delete_bot_execution_target(db, target_id)
    return {"deleted": target_id}


class ExecutionAttemptResponse(BaseModel):
    id: int
    bot_signal_id: int
    execution_target_id: int | None = None
    execution_account_id: int | None = None
    broker: str
    environment: str
    instrument: str
    broker_symbol: str | None = None
    direction: str | None = None
    requested_size: float | None = None
    adjusted_size: float | None = None
    filled_size: float | None = None
    requested_price: float | None = None
    filled_price: float | None = None
    status: str
    broker_order_id: str | None = None
    broker_deal_id: str | None = None
    broker_fill_id: str | None = None
    rejection_reason: str | None = None
    error_code: str | None = None
    latency_ms: int | None = None
    slippage_pips: float | None = None
    detail_json: dict | None = None
    created_at: str | None = None


class BotSignalResponse(BaseModel):
    id: int
    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    direction: str
    kind: str
    signal_timestamp: str | None = None
    bar_time: str | None = None
    plan_json: dict | None = None
    created_at: str | None = None
    parent_status: str
    attempt_count: int
    attempts: list[ExecutionAttemptResponse] | None = None


def _attempt_to_response(a) -> ExecutionAttemptResponse:
    return ExecutionAttemptResponse(
        id=a.id,
        bot_signal_id=a.bot_signal_id,
        execution_target_id=a.execution_target_id,
        execution_account_id=a.execution_account_id,
        broker=a.broker,
        environment=a.environment,
        instrument=a.instrument,
        broker_symbol=a.broker_symbol,
        direction=a.direction,
        requested_size=a.requested_size,
        adjusted_size=a.adjusted_size,
        filled_size=a.filled_size,
        requested_price=a.requested_price,
        filled_price=a.filled_price,
        status=a.status,
        broker_order_id=a.broker_order_id,
        broker_deal_id=a.broker_deal_id,
        broker_fill_id=a.broker_fill_id,
        rejection_reason=a.rejection_reason,
        error_code=a.error_code,
        latency_ms=a.latency_ms,
        slippage_pips=a.slippage_pips,
        detail_json=a.detail_json,
        created_at=a.created_at.isoformat() if a.created_at else None,
    )


def _signal_to_response(
    s, *, attempts: list | None = None, include_attempts: bool = True
) -> BotSignalResponse:
    from fibokei.db.repository import derive_parent_signal_status

    atts = attempts if attempts is not None else list(s.attempts)
    return BotSignalResponse(
        id=s.id,
        bot_id=s.bot_id,
        strategy_id=s.strategy_id,
        instrument=s.instrument,
        timeframe=s.timeframe,
        direction=s.direction,
        kind=s.kind,
        signal_timestamp=s.signal_timestamp.isoformat() if s.signal_timestamp else None,
        bar_time=s.bar_time.isoformat() if s.bar_time else None,
        plan_json=s.plan_json,
        created_at=s.created_at.isoformat() if s.created_at else None,
        parent_status=derive_parent_signal_status(atts),
        attempt_count=len(atts),
        attempts=[_attempt_to_response(a) for a in atts] if include_attempts else None,
    )


@router.get("/execution/signals", response_model=list[BotSignalResponse])
def list_execution_signals_endpoint(
    bot_id: str | None = None,
    instrument: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List parent signals with embedded child attempt summaries.

    Newest first. Each signal is returned with its full attempt list and
    a derived ``parent_status`` so the frontend can render partial-success
    badges without a second round trip.
    """
    from fibokei.db.repository import list_bot_signals

    signals = list_bot_signals(
        db, bot_id=bot_id, instrument=instrument, limit=limit, offset=offset
    )
    return [_signal_to_response(s) for s in signals]


@router.get("/execution/signals/{signal_id}", response_model=BotSignalResponse)
def get_execution_signal_endpoint(
    signal_id: int,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fibokei.db.repository import get_bot_signal

    signal = get_bot_signal(db, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return _signal_to_response(signal)


@router.get(
    "/execution/signals/{signal_id}/attempts",
    response_model=list[ExecutionAttemptResponse],
)
def list_signal_attempts_endpoint(
    signal_id: int,
    status: str | None = None,
    broker: str | None = None,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from fibokei.db.repository import (
        get_bot_signal,
        list_execution_attempts,
    )

    signal = get_bot_signal(db, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    atts = list_execution_attempts(
        db,
        bot_signal_id=signal_id,
        broker=broker,
        status=status,
    )
    return [_attempt_to_response(a) for a in atts]


@router.get("/execution/router", response_model=RouterStateResponse)
def get_router_state(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current execution-router mode and configured targets.

    The API process builds its own router snapshot from the same env-var
    factory the worker uses, so the response reflects the configuration
    that *would* be in effect for a freshly-started worker. Useful for
    verifying env vars before restarting Render/Railway.
    """
    from fibokei.execution.router_factory import build_execution_router_from_env
    from fibokei.execution.targets import ROUTER_MODE_ENV_GLOBAL_FANOUT

    def _ks_check() -> bool:
        ks = get_kill_switch(db)
        return bool(ks.is_active)

    router = build_execution_router_from_env(kill_switch_check=_ks_check)
    summary = router.summary()
    warning: str | None = None
    if (
        summary["router_mode"] == ROUTER_MODE_ENV_GLOBAL_FANOUT
        and len([t for t in summary["targets"] if t["is_enabled"]]) > 1
    ):
        warning = (
            "env_global_fanout active: every running bot will fan out to every "
            "enabled execution account. Per-bot target control arrives in Phase 2."
        )
    return RouterStateResponse(
        router_mode=summary["router_mode"],
        kill_switch_active=summary["kill_switch_active"],
        targets=[RouterTargetView(**t) for t in summary["targets"]],
        warning=warning,
    )


# ── Wave 1: broker trade ledger (IG transaction reconciliation) ─────────────


class BrokerTradeView(BaseModel):
    id: int
    source: str
    reference: str
    deal_id: str | None = None
    instrument_name: str | None = None
    instrument: str | None = None
    direction: str | None = None
    size: float | None = None
    open_level: float | None = None
    close_level: float | None = None
    pnl: float | None = None
    currency: str | None = None
    closed_at: str | None = None
    bot_id: str | None = None
    strategy_id: str | None = None


@router.get("/execution/broker-trades", response_model=list[BrokerTradeView])
def list_broker_trades_endpoint(
    source: str | None = None,
    bot_id: str | None = None,
    strategy_id: str | None = None,
    instrument: str | None = None,
    limit: int = 200,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Broker-executed trades imported from IG (separate from paper trades).

    Filter by source (ig_demo/ig_live/paper/backtest), bot, strategy, instrument.
    """
    from fibokei.db.repository import list_broker_trades

    rows = list_broker_trades(
        db, source=source, bot_id=bot_id, strategy_id=strategy_id,
        instrument=instrument, limit=limit,
    )
    return [
        BrokerTradeView(
            id=r.id, source=r.source, reference=r.reference, deal_id=r.deal_id,
            instrument_name=r.instrument_name, instrument=r.instrument,
            direction=r.direction, size=r.size, open_level=r.open_level,
            close_level=r.close_level, pnl=r.pnl, currency=r.currency,
            closed_at=r.closed_at.isoformat() if r.closed_at else None,
            bot_id=r.bot_id, strategy_id=r.strategy_id,
        )
        for r in rows
    ]


class ReconcileTradesResponse(BaseModel):
    configured: bool
    reachable: bool
    total: int = 0
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    error: str | None = None


@router.post("/execution/reconcile-trades", response_model=ReconcileTradesResponse)
def reconcile_broker_trades(
    from_date: str | None = None,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pull IG demo transaction history and upsert it into the broker ledger.

    Idempotent. Requires IG credentials (worker/prod env). Never raises —
    credential/network problems return a typed status so the operator sees
    exactly what to fix. ``from_date`` defaults to 30 days ago (YYYY-MM-DD).
    """
    import os
    from datetime import datetime, timedelta, timezone

    from fibokei.execution.broker_ledger import import_ig_transactions
    from fibokei.execution.ig_client import IGClientError

    configured = bool(
        os.environ.get("FIBOKEI_IG_API_KEY")
        and os.environ.get("FIBOKEI_IG_USERNAME")
        and os.environ.get("FIBOKEI_IG_PASSWORD")
    )
    if not configured:
        return ReconcileTradesResponse(
            configured=False, reachable=False,
            error="IG credentials not configured (expected on the worker service).",
        )

    if not from_date:
        from_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    try:
        client = _get_ig_health_client()
        client.ensure_session()
        counts = import_ig_transactions(db, client, from_date, source="ig_demo")
        return ReconcileTradesResponse(
            configured=True, reachable=True, **counts
        )
    except IGClientError as e:
        global _ig_health_client
        _ig_health_client = None
        return ReconcileTradesResponse(configured=True, reachable=False, error=str(e))
    except Exception as e:
        return ReconcileTradesResponse(
            configured=True, reachable=False, error=f"Unexpected error: {e}"
        )
