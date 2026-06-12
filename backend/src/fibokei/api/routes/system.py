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
    # Phase 1 multi-broker fan-out: router state + enabled-target summary.
    # ``execution_mode`` is retained for back-compat (legacy single-broker
    # vocabulary). For multi-broker visibility the frontend should rely on
    # ``router_mode`` + ``execution_targets``.
    router_mode: str = "legacy_single"
    execution_targets: list[dict] = []
    # Dedicated-worker heartbeats (worker_heartbeats table). ``paper_engine``
    # is derived from these when no in-process thread exists.
    worker_heartbeats: list[dict] = []


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

    # Dedicated-worker heartbeats: when FIBOKEI_WORKER_EXTERNAL=true the
    # in-process thread is intentionally absent, so liveness comes from the
    # worker_heartbeats table. Fresh = beat within 3 poll intervals.
    heartbeats: list[dict] = []
    try:
        from datetime import datetime, timezone

        from fibokei.db.repository import get_worker_heartbeats

        now = datetime.now(timezone.utc)
        for hb in get_worker_heartbeats(db):
            beat_at = hb.last_beat_at
            if beat_at.tzinfo is None:
                beat_at = beat_at.replace(tzinfo=timezone.utc)
            age_s = (now - beat_at).total_seconds()
            fresh = age_s < max(hb.poll_interval_s, 60) * 3
            heartbeats.append({
                "worker_id": hb.worker_id,
                "hostname": hb.hostname,
                "last_beat_age_s": round(age_s, 1),
                "fresh": fresh,
                "bots_active": hb.bots_active,
                "loops_completed": hb.loops_completed,
                "poll_interval_s": hb.poll_interval_s,
                "last_error": hb.last_error,
            })
        if paper_status == "stopped" and any(h["fresh"] for h in heartbeats):
            paper_status = "running"
    except Exception:
        pass  # table may not exist yet on first deploy

    # Count monitoring/position_open bots from DB (not hardcoded)
    try:
        from fibokei.db.repository import get_active_paper_bots
        active_bots = get_active_paper_bots(db)
        worker_bots = len(active_bots)
    except Exception:
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

    # Multi-broker router snapshot — built from env vars, mirrors what the
    # worker would see on a fresh restart. The API process never executes
    # orders, so this is purely informational.
    try:
        from fibokei.execution.router_factory import build_execution_router_from_env

        exec_router = build_execution_router_from_env(kill_switch_check=lambda: ks.is_active)
        router_summary = exec_router.summary()
        router_mode = router_summary["router_mode"]
        execution_targets = router_summary["targets"]
    except Exception:  # pragma: no cover — degrade gracefully
        router_mode = flags.execution_router_mode
        execution_targets = []

    return SystemStatusResponse(
        api_version="1.0.0",
        database=db_status,
        paper_engine=paper_status,
        worker_bots=worker_bots,
        strategies_loaded=strategies_loaded,
        execution_mode=flags.execution_mode,
        kill_switch_active=ks.is_active,
        data_source=data_source,
        router_mode=router_mode,
        execution_targets=execution_targets,
        worker_heartbeats=heartbeats,
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
