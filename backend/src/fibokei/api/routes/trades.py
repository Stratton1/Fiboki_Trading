"""Trade history API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.models import TradeModel

router = APIRouter(tags=["trades"])


class TradeResponse(BaseModel):
    id: int
    strategy_id: str
    instrument: str
    direction: str
    entry_time: str | None
    entry_price: float
    exit_time: str | None
    exit_price: float
    exit_reason: str
    pnl: float
    bars_in_trade: int
    backtest_run_id: int


class TradeListResponse(BaseModel):
    items: list[TradeResponse]
    total: int
    page: int
    size: int


def _trade_to_response(trade: TradeModel) -> TradeResponse:
    """Convert a TradeModel to a TradeResponse."""
    return TradeResponse(
        id=trade.id,
        strategy_id=trade.strategy_id,
        instrument=trade.instrument,
        direction=trade.direction,
        entry_time=trade.entry_time.isoformat() if trade.entry_time else None,
        entry_price=trade.entry_price,
        exit_time=trade.exit_time.isoformat() if trade.exit_time else None,
        exit_price=trade.exit_price,
        exit_reason=trade.exit_reason,
        pnl=trade.pnl,
        bars_in_trade=trade.bars_in_trade,
        backtest_run_id=trade.backtest_run_id,
    )


@router.get("/trades/", response_model=TradeListResponse)
def list_trades(
    strategy_id: str | None = Query(None),
    instrument: str | None = Query(None),
    direction: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
) -> TradeListResponse:
    """List trades with optional filters and pagination."""
    query = db.query(TradeModel)

    if strategy_id is not None:
        query = query.filter(TradeModel.strategy_id == strategy_id)
    if instrument is not None:
        query = query.filter(TradeModel.instrument == instrument)
    if direction is not None:
        query = query.filter(TradeModel.direction == direction)

    total = query.count()
    offset = (page - 1) * size
    trades = query.offset(offset).limit(size).all()

    return TradeListResponse(
        items=[_trade_to_response(t) for t in trades],
        total=total,
        page=page,
        size=size,
    )


@router.get("/trades/{trade_id}", response_model=TradeResponse)
def get_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    _user: TokenData = Depends(get_current_user),
) -> TradeResponse:
    """Get a single trade by ID."""
    trade = db.query(TradeModel).filter(TradeModel.id == trade_id).first()
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return _trade_to_response(trade)
