"""Paper trading API endpoints — DB-backed bot state."""

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.repository import (
    get_active_paper_bots,
    get_best_research_score,
    get_or_create_paper_account,
    get_paper_bot,
    get_paper_bots,
    get_paper_trades,
    save_paper_bot,
    update_paper_bot_state,
)
from fibokei.strategies.registry import strategy_registry

router = APIRouter(tags=["paper"])

# Minimum composite score to promote a combo from research to paper
PROMOTION_THRESHOLD = float(os.environ.get("FIBOKEI_PROMOTION_THRESHOLD", "0.55"))

# Stale-data thresholds: max seconds since last evaluation per timeframe
STALE_THRESHOLDS = {
    "M1": 180,
    "M5": 900,
    "M15": 2700,
    "M30": 5400,
    "H1": 7200,
    "H4": 21600,
    "D": 172800,
}


# ---------- Request / Response schemas ----------

class CreateBotRequest(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    risk_pct: float = 1.0
    source_type: str | None = None  # "research" | "backtest" | "manual"
    source_id: str | None = None  # research run_id or backtest id


class CreateBotResponse(BaseModel):
    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    state: str
    source_type: str | None = None
    source_id: str | None = None


class BotStatusResponse(BaseModel):
    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    state: str
    bars_seen: int
    has_position: bool
    position: dict | None = None
    last_evaluated_bar: str | None = None
    error_message: str | None = None
    source_type: str | None = None
    source_id: str | None = None


class AccountResponse(BaseModel):
    balance: float
    equity: float
    initial_balance: float
    total_pnl: float
    total_pnl_pct: float
    daily_pnl: float
    weekly_pnl: float
    open_positions: int
    total_trades: int


class BotHealthItem(BaseModel):
    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    state: str
    last_evaluated_bar: str | None
    seconds_since_eval: float | None
    is_stale: bool


class HealthResponse(BaseModel):
    total_bots: int
    active_bots: int
    stale_bots: int
    bots: list[BotHealthItem]


# ---------- Endpoints ----------

@router.post("/paper/bots", response_model=CreateBotResponse)
def create_bot(
    req: CreateBotRequest,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create and start a paper trading bot (with promotion gate)."""
    try:
        strategy_registry.get(req.strategy_id)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {req.strategy_id}")

    # Promotion gate
    best_score = get_best_research_score(
        db, req.strategy_id, req.instrument, req.timeframe.upper()
    )
    if best_score is None or best_score < PROMOTION_THRESHOLD:
        score_str = f"{best_score:.3f}" if best_score is not None else "none"
        raise HTTPException(
            status_code=422,
            detail=(
                f"Promotion gate failed: composite_score={score_str}, "
                f"required>={PROMOTION_THRESHOLD:.3f}. Run research first."
            ),
        )

    bot_id = str(uuid.uuid4())[:8]
    source_type = req.source_type or "manual"
    bot_model = save_paper_bot(db, {
        "bot_id": bot_id,
        "strategy_id": req.strategy_id,
        "instrument": req.instrument,
        "timeframe": req.timeframe.upper(),
        "risk_pct": req.risk_pct,
        "source_type": source_type,
        "source_id": req.source_id,
        "state": "monitoring",
    })

    return CreateBotResponse(
        bot_id=bot_id,
        strategy_id=req.strategy_id,
        instrument=req.instrument,
        timeframe=req.timeframe.upper(),
        state=bot_model.state,
        source_type=source_type,
        source_id=req.source_id,
    )


@router.get("/paper/bots", response_model=list[BotStatusResponse])
def list_bots(
    state: str | None = Query(None),
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all paper trading bots."""
    bots = get_paper_bots(db, state=state)
    return [_bot_to_response(b) for b in bots]


@router.get("/paper/bots/{bot_id}", response_model=BotStatusResponse)
def get_bot_detail(
    bot_id: str,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get bot detail."""
    bot = get_paper_bot(db, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return _bot_to_response(bot)


@router.post("/paper/bots/{bot_id}/stop")
def stop_bot(
    bot_id: str,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stop a paper trading bot."""
    bot = update_paper_bot_state(db, bot_id, "stopped")
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return {"bot_id": bot_id, "state": bot.state}


@router.post("/paper/bots/{bot_id}/pause")
def pause_bot(
    bot_id: str,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pause a paper trading bot."""
    bot = get_paper_bot(db, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    if bot.state != "monitoring":
        raise HTTPException(status_code=400, detail="Can only pause a monitoring bot")
    updated = update_paper_bot_state(db, bot_id, "paused")
    return {"bot_id": bot_id, "state": updated.state}


@router.get("/paper/account", response_model=AccountResponse)
def get_account(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get paper trading account overview."""
    acct = get_or_create_paper_account(db)
    trades = get_paper_trades(db, limit=10000)
    active_bots = get_active_paper_bots(db)
    open_count = sum(1 for b in active_bots if b.state == "position_open")
    total_pnl = acct.balance - acct.initial_balance
    total_pnl_pct = (total_pnl / acct.initial_balance * 100) if acct.initial_balance > 0 else 0.0
    return AccountResponse(
        balance=acct.balance,
        equity=acct.equity,
        initial_balance=acct.initial_balance,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        daily_pnl=acct.daily_pnl,
        weekly_pnl=acct.weekly_pnl,
        open_positions=open_count,
        total_trades=len(trades),
    )


@router.get("/paper/health", response_model=HealthResponse)
def get_health(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bot health monitoring — stale data detection per bot."""
    all_bots = get_paper_bots(db)
    now = datetime.now(timezone.utc)
    items = []
    stale_count = 0

    for bot in all_bots:
        is_active = bot.state in ("monitoring", "position_open")
        seconds_since = None
        is_stale = False

        if bot.last_evaluated_bar and is_active:
            last_eval = bot.last_evaluated_bar
            if last_eval.tzinfo is None:
                last_eval = last_eval.replace(tzinfo=timezone.utc)
            seconds_since = (now - last_eval).total_seconds()
            threshold = STALE_THRESHOLDS.get(bot.timeframe.upper(), 7200)
            is_stale = seconds_since > threshold

        if is_stale:
            stale_count += 1

        items.append(BotHealthItem(
            bot_id=bot.bot_id,
            strategy_id=bot.strategy_id,
            instrument=bot.instrument,
            timeframe=bot.timeframe,
            state=bot.state,
            last_evaluated_bar=(
                bot.last_evaluated_bar.isoformat() if bot.last_evaluated_bar else None
            ),
            seconds_since_eval=seconds_since,
            is_stale=is_stale,
        ))

    active_count = sum(1 for b in all_bots if b.state in ("monitoring", "position_open"))

    return HealthResponse(
        total_bots=len(all_bots),
        active_bots=active_count,
        stale_bots=stale_count,
        bots=items,
    )


class BotFleetItem(BaseModel):
    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    state: str
    bars_seen: int
    total_trades: int
    total_pnl: float
    has_position: bool
    source_type: str | None = None
    last_evaluated_bar: str | None = None
    is_stale: bool = False


class FleetOverviewResponse(BaseModel):
    total_bots: int
    running: int
    paused: int
    stopped: int
    stale: int
    aggregate_pnl: float
    aggregate_trades: int
    open_positions: int
    bots: list[BotFleetItem]
    strategy_groups: dict[str, dict]


@router.get("/paper/fleet")
def get_fleet_overview(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fleet-level dashboard: aggregate metrics + per-bot stats."""
    all_bots = get_paper_bots(db)
    now = datetime.now(timezone.utc)
    items: list[BotFleetItem] = []
    strategy_groups: dict[str, dict] = {}
    total_pnl = 0.0
    total_trades_count = 0
    open_count = 0

    for bot in all_bots:
        trades = get_paper_trades(db, bot_id=bot.bot_id, limit=10000)
        bot_pnl = sum(t.pnl for t in trades)
        bot_trades = len(trades)
        total_pnl += bot_pnl
        total_trades_count += bot_trades
        has_position = bot.position_json is not None
        if has_position:
            open_count += 1

        # Stale detection
        is_stale = False
        if bot.last_evaluated_bar and bot.state in ("monitoring", "position_open"):
            last_eval = bot.last_evaluated_bar
            if last_eval.tzinfo is None:
                last_eval = last_eval.replace(tzinfo=timezone.utc)
            seconds_since = (now - last_eval).total_seconds()
            threshold = STALE_THRESHOLDS.get(bot.timeframe.upper(), 7200)
            is_stale = seconds_since > threshold

        items.append(BotFleetItem(
            bot_id=bot.bot_id,
            strategy_id=bot.strategy_id,
            instrument=bot.instrument,
            timeframe=bot.timeframe,
            state=bot.state,
            bars_seen=bot.bars_seen,
            total_trades=bot_trades,
            total_pnl=bot_pnl,
            has_position=has_position,
            source_type=getattr(bot, "source_type", None),
            last_evaluated_bar=(
                bot.last_evaluated_bar.isoformat() if bot.last_evaluated_bar else None
            ),
            is_stale=is_stale,
        ))

        # Strategy grouping
        sid = bot.strategy_id
        if sid not in strategy_groups:
            strategy_groups[sid] = {"count": 0, "running": 0, "pnl": 0.0, "trades": 0}
        strategy_groups[sid]["count"] += 1
        if bot.state in ("monitoring", "position_open"):
            strategy_groups[sid]["running"] += 1
        strategy_groups[sid]["pnl"] += bot_pnl
        strategy_groups[sid]["trades"] += bot_trades

    running = sum(1 for b in all_bots if b.state in ("monitoring", "position_open"))
    paused_count = sum(1 for b in all_bots if b.state == "paused")
    stopped_count = sum(1 for b in all_bots if b.state == "stopped")
    stale_count = sum(1 for i in items if i.is_stale)

    return FleetOverviewResponse(
        total_bots=len(all_bots),
        running=running,
        paused=paused_count,
        stopped=stopped_count,
        stale=stale_count,
        aggregate_pnl=total_pnl,
        aggregate_trades=total_trades_count,
        open_positions=open_count,
        bots=items,
        strategy_groups=strategy_groups,
    )


@router.get("/paper/bots/{bot_id}/trades")
def get_bot_trades(
    bot_id: str,
    limit: int = Query(100, ge=1, le=1000),
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get trades for a specific bot with equity curve."""
    bot = get_paper_bot(db, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    trades = get_paper_trades(db, bot_id=bot_id, limit=limit)
    # Build equity curve (chronological)
    sorted_trades = sorted(trades, key=lambda t: t.entry_time)
    cumulative = 0.0
    equity_curve = []
    for t in sorted_trades:
        cumulative += t.pnl
        equity_curve.append(round(cumulative, 2))
    return {
        "bot_id": bot_id,
        "total": len(trades),
        "trades": [
            {
                "id": t.id,
                "strategy_id": t.strategy_id,
                "instrument": t.instrument,
                "direction": t.direction,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl": t.pnl,
                "bars_in_trade": t.bars_in_trade,
            }
            for t in trades
        ],
        "equity_curve": equity_curve,
    }


@router.get("/paper/exposure")
def get_exposure(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Portfolio exposure breakdown: per-instrument, per-asset-class, risk utilization."""
    from fibokei.core.instruments import get_instrument
    from fibokei.risk.limits import get_risk_limits

    all_bots = get_paper_bots(db)
    acct = get_or_create_paper_account(db)
    limits = get_risk_limits()
    trades = get_paper_trades(db, limit=10000)

    # Per-instrument exposure (based on open positions)
    instrument_exposure: dict[str, dict] = {}
    active_positions = 0
    for bot in all_bots:
        if bot.position_json is None:
            continue
        active_positions += 1
        inst = bot.instrument
        direction = bot.position_json.get("direction", "LONG")
        if inst not in instrument_exposure:
            instrument_exposure[inst] = {"long": 0, "short": 0, "bot_count": 0}
        instrument_exposure[inst]["bot_count"] += 1
        if direction in ("LONG", "long"):
            instrument_exposure[inst]["long"] += 1
        else:
            instrument_exposure[inst]["short"] += 1

    # Derive net per instrument
    for inst, exp in instrument_exposure.items():
        exp["net"] = exp["long"] - exp["short"]

    # Per-asset-class aggregation
    asset_class_exposure: dict[str, dict] = {}
    for inst, exp in instrument_exposure.items():
        try:
            instrument_obj = get_instrument(inst)
            ac = instrument_obj.asset_class.value
        except KeyError:
            ac = "unknown"
        if ac not in asset_class_exposure:
            asset_class_exposure[ac] = {"long": 0, "short": 0, "instruments": 0}
        asset_class_exposure[ac]["long"] += exp["long"]
        asset_class_exposure[ac]["short"] += exp["short"]
        asset_class_exposure[ac]["instruments"] += 1

    # Concentration warnings: instruments with >= 3 bots
    concentration_warnings = [
        {"instrument": inst, "bot_count": exp["bot_count"]}
        for inst, exp in instrument_exposure.items()
        if exp["bot_count"] >= 3
    ]

    # Risk utilization
    total_long = sum(e["long"] for e in instrument_exposure.values())
    total_short = sum(e["short"] for e in instrument_exposure.values())

    daily_dd_pct = abs(min(acct.daily_pnl, 0.0)) / acct.initial_balance * 100 if acct.initial_balance > 0 else 0.0
    weekly_dd_pct = abs(min(acct.weekly_pnl, 0.0)) / acct.initial_balance * 100 if acct.initial_balance > 0 else 0.0

    return {
        "instrument_exposure": instrument_exposure,
        "asset_class_exposure": asset_class_exposure,
        "direction_balance": {"long": total_long, "short": total_short},
        "active_positions": active_positions,
        "concentration_warnings": concentration_warnings,
        "risk_utilization": {
            "open_trades": active_positions,
            "max_open_trades": limits["max_open_trades"],
            "open_trades_pct": round(active_positions / limits["max_open_trades"] * 100, 1) if limits["max_open_trades"] > 0 else 0,
            "daily_dd_pct": round(daily_dd_pct, 2),
            "daily_soft_stop_pct": limits["daily_soft_stop_pct"],
            "daily_hard_stop_pct": limits["daily_hard_stop_pct"],
            "weekly_dd_pct": round(weekly_dd_pct, 2),
            "weekly_soft_stop_pct": limits["weekly_soft_stop_pct"],
            "weekly_hard_stop_pct": limits["weekly_hard_stop_pct"],
        },
        "total_bots": len(all_bots),
        "total_trades": len(trades),
    }


def _bot_to_response(bot) -> BotStatusResponse:
    """Convert a PaperBotModel to response schema."""
    return BotStatusResponse(
        bot_id=bot.bot_id,
        strategy_id=bot.strategy_id,
        instrument=bot.instrument,
        timeframe=bot.timeframe,
        state=bot.state,
        bars_seen=bot.bars_seen,
        has_position=bot.position_json is not None,
        position=bot.position_json,
        last_evaluated_bar=(
            bot.last_evaluated_bar.isoformat() if bot.last_evaluated_bar else None
        ),
        error_message=bot.error_message,
        source_type=getattr(bot, "source_type", None),
        source_id=getattr(bot, "source_id", None),
    )
