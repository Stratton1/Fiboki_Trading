"""System diagnostics endpoints."""

import os

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
    worker_bots: int
    strategies_loaded: int
    execution_mode: str
    kill_switch_active: bool
    data_source: str


class RiskConfigResponse(BaseModel):
    max_risk_per_trade_pct: float
    max_portfolio_risk_pct: float
    max_open_trades: int
    max_per_instrument: int
    daily_soft_stop_pct: float
    daily_hard_stop_pct: float
    weekly_soft_stop_pct: float
    weekly_hard_stop_pct: float
    fleet_max_bots_per_instrument: int
    fleet_max_total_positions: int
    fleet_max_exposure_per_instrument: int
    fleet_correlation_threshold: float
    fleet_cull_sigma: float
    fleet_cull_min_trades: int


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

    # Paper engine status — check if background worker thread is alive
    import threading

    worker_thread = next(
        (t for t in threading.enumerate() if t.name == "paper-worker"), None
    )
    paper_status = "running" if worker_thread and worker_thread.is_alive() else "stopped"
    worker_bots = 0

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
        worker_bots=worker_bots,
        strategies_loaded=strategies_loaded,
        execution_mode=flags.execution_mode,
        kill_switch_active=ks.is_active,
        data_source=data_source,
    )


@router.get("/system/risk-config", response_model=RiskConfigResponse)
def get_risk_config(
    _user: TokenData = Depends(get_current_user),
) -> RiskConfigResponse:
    """Return live risk parameters as configured via environment variables."""
    return RiskConfigResponse(
        max_risk_per_trade_pct=float(os.environ.get("FIBOKEI_MAX_RISK_PER_TRADE_PCT", "1.0")),
        max_portfolio_risk_pct=float(os.environ.get("FIBOKEI_MAX_PORTFOLIO_RISK_PCT", "5.0")),
        max_open_trades=int(os.environ.get("FIBOKEI_MAX_OPEN_TRADES", "8")),
        max_per_instrument=int(os.environ.get("FIBOKEI_MAX_PER_INSTRUMENT", "2")),
        daily_soft_stop_pct=float(os.environ.get("FIBOKEI_DAILY_SOFT_STOP_PCT", "3.0")),
        daily_hard_stop_pct=float(os.environ.get("FIBOKEI_DAILY_HARD_STOP_PCT", "4.0")),
        weekly_soft_stop_pct=float(os.environ.get("FIBOKEI_WEEKLY_SOFT_STOP_PCT", "6.0")),
        weekly_hard_stop_pct=float(os.environ.get("FIBOKEI_WEEKLY_HARD_STOP_PCT", "8.0")),
        fleet_max_bots_per_instrument=int(os.environ.get("FIBOKEI_FLEET_MAX_BOTS_PER_INSTRUMENT", "5")),
        fleet_max_total_positions=int(os.environ.get("FIBOKEI_FLEET_MAX_TOTAL_POSITIONS", "20")),
        fleet_max_exposure_per_instrument=int(os.environ.get("FIBOKEI_FLEET_MAX_EXPOSURE_PER_INSTRUMENT", "6")),
        fleet_correlation_threshold=float(os.environ.get("FIBOKEI_FLEET_CORRELATION_THRESHOLD", "0.85")),
        fleet_cull_sigma=float(os.environ.get("FIBOKEI_FLEET_CULL_SIGMA", "2.0")),
        fleet_cull_min_trades=int(os.environ.get("FIBOKEI_FLEET_CULL_MIN_TRADES", "50")),
    )
