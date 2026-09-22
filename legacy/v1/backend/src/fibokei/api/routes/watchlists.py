"""Watchlists API — create and manage instrument watchlists."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.repository import (
    create_watchlist,
    delete_watchlist,
    list_watchlists,
    update_watchlist,
)

router = APIRouter(tags=["watchlists"])

FOREX_MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]


class WatchlistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    instrument_ids: list[str] = Field(..., min_length=1)


class WatchlistUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    instrument_ids: list[str] | None = None


class WatchlistResponse(BaseModel):
    id: int
    name: str
    instrument_ids: list[str]


def _ensure_default_watchlist(db: Session, user_id: int) -> None:
    """Auto-create a 'Forex Majors' watchlist if the user has none."""
    existing = list_watchlists(db, user_id)
    if not existing:
        create_watchlist(db, user_id, "Forex Majors", FOREX_MAJORS)


@router.get("/watchlists", response_model=list[WatchlistResponse])
def get_watchlists(
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """List user's watchlists (auto-creates a default if none exist)."""
    _ensure_default_watchlist(db, user.user_id)
    watchlists = list_watchlists(db, user.user_id)
    return [
        WatchlistResponse(
            id=wl.id,
            name=wl.name,
            instrument_ids=wl.instrument_ids,
        )
        for wl in watchlists
    ]


@router.post("/watchlists", response_model=WatchlistResponse, status_code=201)
def create_watchlist_endpoint(
    req: WatchlistCreate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Create a new watchlist."""
    wl = create_watchlist(db, user.user_id, req.name, req.instrument_ids)
    return WatchlistResponse(
        id=wl.id,
        name=wl.name,
        instrument_ids=wl.instrument_ids,
    )


@router.put("/watchlists/{watchlist_id}", response_model=WatchlistResponse)
def update_watchlist_endpoint(
    watchlist_id: int,
    req: WatchlistUpdate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Update a watchlist's name or instruments."""
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    wl = update_watchlist(db, watchlist_id, user.user_id, updates)
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return WatchlistResponse(
        id=wl.id,
        name=wl.name,
        instrument_ids=wl.instrument_ids,
    )


@router.delete("/watchlists/{watchlist_id}")
def delete_watchlist_endpoint(
    watchlist_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete a watchlist."""
    deleted = delete_watchlist(db, watchlist_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return {"deleted": watchlist_id}
