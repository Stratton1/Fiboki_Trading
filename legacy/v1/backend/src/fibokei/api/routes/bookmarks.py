"""Bookmarks API — save/unsave research results, backtests, and trades."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.models import BookmarkModel

router = APIRouter(tags=["bookmarks"])

VALID_ENTITY_TYPES = {"research_result", "backtest", "trade"}


class BookmarkCreate(BaseModel):
    entity_type: str = Field(..., description="research_result | backtest | trade")
    entity_id: int
    note: str | None = None


class BookmarkResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    note: str | None = None
    created_at: str | None = None


@router.get("/bookmarks", response_model=list[BookmarkResponse])
def list_bookmarks(
    entity_type: str | None = Query(None),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """List bookmarks, optionally filtered by entity type."""
    query = db.query(BookmarkModel).filter(BookmarkModel.user_id == user.user_id)
    if entity_type:
        query = query.filter(BookmarkModel.entity_type == entity_type)
    bookmarks = query.order_by(BookmarkModel.created_at.desc()).all()
    return [
        {
            "id": b.id,
            "entity_type": b.entity_type,
            "entity_id": b.entity_id,
            "note": b.note,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bookmarks
    ]


@router.post("/bookmarks", response_model=BookmarkResponse, status_code=201)
def create_bookmark(
    req: BookmarkCreate,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Create a bookmark."""
    if req.entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entity_type. Must be one of: {', '.join(VALID_ENTITY_TYPES)}",
        )
    # Check for duplicate
    existing = (
        db.query(BookmarkModel)
        .filter(
            BookmarkModel.user_id == user.user_id,
            BookmarkModel.entity_type == req.entity_type,
            BookmarkModel.entity_id == req.entity_id,
        )
        .first()
    )
    if existing:
        return {
            "id": existing.id,
            "entity_type": existing.entity_type,
            "entity_id": existing.entity_id,
            "note": existing.note,
            "created_at": existing.created_at.isoformat() if existing.created_at else None,
        }

    bookmark = BookmarkModel(
        user_id=user.user_id,
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        note=req.note,
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)
    return {
        "id": bookmark.id,
        "entity_type": bookmark.entity_type,
        "entity_id": bookmark.entity_id,
        "note": bookmark.note,
        "created_at": bookmark.created_at.isoformat() if bookmark.created_at else None,
    }


@router.delete("/bookmarks/{bookmark_id}")
def delete_bookmark(
    bookmark_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete a bookmark by ID."""
    bookmark = (
        db.query(BookmarkModel)
        .filter(BookmarkModel.id == bookmark_id, BookmarkModel.user_id == user.user_id)
        .first()
    )
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    db.delete(bookmark)
    db.commit()
    return {"deleted": bookmark_id}


@router.delete("/bookmarks")
def delete_bookmark_by_entity(
    entity_type: str = Query(...),
    entity_id: int = Query(...),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete a bookmark by entity type and ID (toggle off)."""
    bookmark = (
        db.query(BookmarkModel)
        .filter(
            BookmarkModel.user_id == user.user_id,
            BookmarkModel.entity_type == entity_type,
            BookmarkModel.entity_id == entity_id,
        )
        .first()
    )
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    db.delete(bookmark)
    db.commit()
    return {"deleted": bookmark.id}
