"""Alert Centre API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.db.repository import (
    count_unread_alerts,
    list_alerts,
    mark_alert_read,
    mark_all_alerts_read,
    save_alert,
)

router = APIRouter(tags=["alerts"])


class AlertResponse(BaseModel):
    id: int
    alert_type: str
    severity: str
    title: str
    message: str
    metadata_json: dict | None = None
    is_read: bool
    created_at: str


class AlertListResponse(BaseModel):
    items: list[AlertResponse]
    unread_count: int
    total: int


class CreateAlertRequest(BaseModel):
    alert_type: str
    severity: str = "info"
    title: str
    message: str
    metadata_json: dict | None = None


@router.get("/alerts", response_model=AlertListResponse)
def get_alerts(
    alert_type: str | None = Query(None),
    is_read: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List alerts with optional filters."""
    alerts = list_alerts(db, alert_type=alert_type, is_read=is_read, limit=limit, offset=offset)
    unread = count_unread_alerts(db)
    return AlertListResponse(
        items=[
            AlertResponse(
                id=a.id,
                alert_type=a.alert_type,
                severity=a.severity,
                title=a.title,
                message=a.message,
                metadata_json=a.metadata_json,
                is_read=a.is_read,
                created_at=a.created_at.isoformat() if a.created_at else "",
            )
            for a in alerts
        ],
        unread_count=unread,
        total=len(alerts),
    )


@router.get("/alerts/unread-count")
def get_unread_count(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get unread alert count (lightweight endpoint for sidebar badge)."""
    return {"unread_count": count_unread_alerts(db)}


@router.post("/alerts", response_model=AlertResponse)
def create_alert(
    req: CreateAlertRequest,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a manual alert (for testing or operator use)."""
    alert = save_alert(
        db,
        alert_type=req.alert_type,
        severity=req.severity,
        title=req.title,
        message=req.message,
        metadata_json=req.metadata_json,
    )
    return AlertResponse(
        id=alert.id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        title=alert.title,
        message=alert.message,
        metadata_json=alert.metadata_json,
        is_read=alert.is_read,
        created_at=alert.created_at.isoformat() if alert.created_at else "",
    )


@router.post("/alerts/{alert_id}/read")
def mark_read(
    alert_id: int,
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a single alert as read."""
    alert = mark_alert_read(db, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"id": alert_id, "is_read": True}


@router.post("/alerts/read-all")
def mark_all_read(
    user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark all alerts as read."""
    count = mark_all_alerts_read(db)
    return {"marked_read": count}
