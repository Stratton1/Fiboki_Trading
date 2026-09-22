"""Trade history API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.models import TradeModel
from fibokei.db.repository import (
    create_journal_entry,
    delete_journal_entry,
    get_journal_entry,
    list_journal_entries,
    update_journal_entry,
)

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


# ── Trade Journal ────────────────────────────────────────────


class JournalEntryResponse(BaseModel):
    id: int
    trade_id: int
    note: str | None
    tags: list[str]
    created_at: str | None
    updated_at: str | None


class JournalCreate(BaseModel):
    note: str | None = None
    tags: list[str] = Field(default_factory=list)


class JournalUpdate(BaseModel):
    note: str | None = None
    tags: list[str] | None = None


class JournalListResponse(BaseModel):
    items: list[JournalEntryResponse]
    total: int


def _journal_to_response(entry) -> JournalEntryResponse:
    return JournalEntryResponse(
        id=entry.id,
        trade_id=entry.trade_id,
        note=entry.note,
        tags=entry.tags or [],
        created_at=entry.created_at.isoformat() if entry.created_at else None,
        updated_at=entry.updated_at.isoformat() if entry.updated_at else None,
    )


@router.get("/trades/{trade_id}/journal", response_model=JournalEntryResponse | None)
def get_trade_journal(
    trade_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Get journal entry for a trade."""
    entry = get_journal_entry(db, trade_id, user.user_id)
    if not entry:
        return None
    return _journal_to_response(entry)


@router.post("/trades/{trade_id}/journal", response_model=JournalEntryResponse, status_code=201)
def create_trade_journal(
    trade_id: int,
    body: JournalCreate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Create a journal entry for a trade (one per trade)."""
    # Verify trade exists
    trade = db.query(TradeModel).filter(TradeModel.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    # Check if journal entry already exists
    existing = get_journal_entry(db, trade_id, user.user_id)
    if existing:
        raise HTTPException(status_code=409, detail="Journal entry already exists for this trade")

    entry = create_journal_entry(db, user.user_id, trade_id, body.note, body.tags)
    return _journal_to_response(entry)


@router.patch("/trades/{trade_id}/journal", response_model=JournalEntryResponse)
def update_trade_journal(
    trade_id: int,
    body: JournalUpdate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Update a trade's journal entry."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    entry = update_journal_entry(db, trade_id, user.user_id, updates)
    if not entry:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return _journal_to_response(entry)


@router.delete("/trades/{trade_id}/journal")
def delete_trade_journal(
    trade_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete a trade's journal entry."""
    deleted = delete_journal_entry(db, trade_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return {"deleted": trade_id}


@router.get("/journal", response_model=JournalListResponse)
def list_journal(
    tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """List all journal entries, optionally filtered by tag."""
    entries = list_journal_entries(db, user.user_id, tag=tag, limit=limit)
    return JournalListResponse(
        items=[_journal_to_response(e) for e in entries],
        total=len(entries),
    )
