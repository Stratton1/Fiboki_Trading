"""System diagnostics endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db

router = APIRouter(tags=["system"])


class SystemHealthResponse(BaseModel):
    status: str
    version: str


class SystemStatusResponse(BaseModel):
    api_version: str
    database: str
    paper_engine: str
    strategies_loaded: int


@router.get("/system/health", response_model=SystemHealthResponse)
def system_health() -> SystemHealthResponse:
    """Public health check endpoint."""
    return SystemHealthResponse(status="ok", version="1.0.0")


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(
    db=Depends(get_db),
    _user: TokenData = Depends(get_current_user),
) -> SystemStatusResponse:
    """Protected system status endpoint."""
    # Check database connectivity
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "error"

    # Paper engine status
    paper_status = "standby"

    # Count loaded strategies
    from fibokei.strategies.registry import strategy_registry

    strategies_loaded = len(strategy_registry.list_available())

    return SystemStatusResponse(
        api_version="1.0.0",
        database=db_status,
        paper_engine=paper_status,
        strategies_loaded=strategies_loaded,
    )
