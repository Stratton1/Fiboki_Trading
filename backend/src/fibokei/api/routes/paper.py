"""Paper trading API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fibokei.api.auth import TokenData, get_current_user
from fibokei.paper.account import PaperAccount
from fibokei.paper.orchestrator import BotOrchestrator
from fibokei.risk.engine import RiskEngine

router = APIRouter(tags=["paper"])

# Module-level orchestrator (shared across requests in the same process)
_orchestrator: BotOrchestrator | None = None


def _get_orchestrator() -> BotOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = BotOrchestrator(
            account=PaperAccount(initial_balance=10000.0),
            risk_engine=RiskEngine(),
        )
    return _orchestrator


class CreateBotRequest(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    risk_pct: float = 1.0


class CreateBotResponse(BaseModel):
    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    state: str


class BotStatusResponse(BaseModel):
    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    state: str
    bars_seen: int
    has_position: bool
    position: dict | None = None


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


@router.post("/paper/bots", response_model=CreateBotResponse)
def create_bot(
    req: CreateBotRequest,
    user: TokenData = Depends(get_current_user),
):
    """Create and start a paper trading bot."""
    orch = _get_orchestrator()
    try:
        bot_id = orch.add_bot(req.strategy_id, req.instrument, req.timeframe, req.risk_pct)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    bot = orch.get_bot(bot_id)
    bot.start()

    return CreateBotResponse(
        bot_id=bot_id,
        strategy_id=req.strategy_id,
        instrument=req.instrument,
        timeframe=req.timeframe.upper(),
        state=bot.state.value,
    )


@router.get("/paper/bots", response_model=list[BotStatusResponse])
def list_bots(user: TokenData = Depends(get_current_user)):
    """List all paper trading bots."""
    orch = _get_orchestrator()
    return orch.get_all_status()


@router.get("/paper/bots/{bot_id}", response_model=BotStatusResponse)
def get_bot(bot_id: str, user: TokenData = Depends(get_current_user)):
    """Get bot detail."""
    orch = _get_orchestrator()
    bot = orch.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot.get_status()


@router.post("/paper/bots/{bot_id}/stop")
def stop_bot(bot_id: str, user: TokenData = Depends(get_current_user)):
    """Stop a paper trading bot."""
    orch = _get_orchestrator()
    bot = orch.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    bot.stop()
    return {"bot_id": bot_id, "state": bot.state.value}


@router.post("/paper/bots/{bot_id}/pause")
def pause_bot(bot_id: str, user: TokenData = Depends(get_current_user)):
    """Pause a paper trading bot."""
    orch = _get_orchestrator()
    bot = orch.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    bot.pause()
    return {"bot_id": bot_id, "state": bot.state.value}


@router.get("/paper/account", response_model=AccountResponse)
def get_account(user: TokenData = Depends(get_current_user)):
    """Get paper trading account overview."""
    orch = _get_orchestrator()
    return orch.account.get_status()
