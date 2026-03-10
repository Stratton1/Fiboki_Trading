"""System diagnostics endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.core.feature_flags import FeatureFlags

router = APIRouter(tags=["system"])


class SystemHealthResponse(BaseModel):
    status: str
    version: str


class SystemStatusResponse(BaseModel):
    api_version: str
    database: str
    paper_engine: str
    strategies_loaded: int
    execution_mode: str
    kill_switch_active: bool
    data_source: str


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

    flags = FeatureFlags()
    from fibokei.db.repository import get_kill_switch
    ks = get_kill_switch(db)

    # Determine active data source
    from fibokei.data.paths import get_data_root, get_canonical_dir

    data_root = get_data_root()
    canonical = get_canonical_dir()
    if (canonical / "manifest.json").exists():
        data_source = "volume"
    elif (data_root / "starter").exists() and any((data_root / "starter").iterdir()):
        data_source = "starter"
    else:
        data_source = "fixtures"

    return SystemStatusResponse(
        api_version="1.0.0",
        database=db_status,
        paper_engine=paper_status,
        strategies_loaded=strategies_loaded,
        execution_mode=flags.execution_mode,
        kill_switch_active=ks.is_active,
        data_source=data_source,
    )
