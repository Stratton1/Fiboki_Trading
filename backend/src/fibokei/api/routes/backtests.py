"""Backtest endpoints."""

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.api.schemas.backtests import (
    BacktestDetailResponse,
    BacktestRunRequest,
    BacktestSummaryResponse,
    EquityCurveResponse,
    TradeListResponse,
)
from fibokei.api.schemas.jobs import JobSubmittedResponse
from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.db.models import BacktestRunModel, TradeModel
from fibokei.db.repository import delete_backtest_run, get_backtest_results, save_backtest_result
from fibokei.jobs.engine import get_job_engine
from fibokei.strategies.registry import strategy_registry

router = APIRouter(tags=["backtests"])


def _run_backtest_sync(
    strategy_id: str,
    instrument: str,
    timeframe: str,
    config_overrides: dict | None,
    session_factory,
    progress_callback=None,
):
    """Execute a backtest — used both sync and as a job target."""
    strategy = strategy_registry.get(strategy_id)
    if config_overrides:
        strategy.config.update(config_overrides)

    config = BacktestConfig()
    tf_enum = Timeframe(timeframe.upper())

    df = load_canonical(instrument, tf_enum.value)
    if df is None:
        raise ValueError(f"No data file for {instrument}/{tf_enum.value}")

    if progress_callback:
        progress_callback(10)

    backtester = Backtester(strategy, config)
    result = backtester.run(df, instrument, tf_enum)

    if progress_callback:
        progress_callback(80)

    metrics = compute_metrics(result)
    metrics["equity_curve"] = result.equity_curve

    with session_factory() as db:
        run_model = save_backtest_result(db, result, metrics)
        result_dict = {
            "backtest_run_id": run_model.id,
            "strategy_id": run_model.strategy_id,
            "instrument": run_model.instrument,
            "timeframe": run_model.timeframe,
            "total_trades": run_model.total_trades,
            "net_profit": run_model.net_profit,
            "sharpe_ratio": run_model.sharpe_ratio,
            "max_drawdown_pct": run_model.max_drawdown_pct,
        }

    if progress_callback:
        progress_callback(100)

    return result_dict


@router.post("/backtests/run")
def run_backtest(
    req: BacktestRunRequest,
    request: Request,
    async_mode: bool = Query(False, alias="async"),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Run a backtest. Use ?async=true to run in background and get a job ID."""
    # Validate inputs before submitting
    try:
        strategy_registry.get(req.strategy_id)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {req.strategy_id}")

    try:
        tf_enum = Timeframe(req.timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {req.timeframe}")

    if not async_mode:
        # Synchronous path (existing behaviour)
        try:
            result = _run_backtest_sync(
                req.strategy_id, req.instrument, req.timeframe,
                req.config_overrides, request.app.state.session_factory,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        # Re-query the saved model for full response
        run_id = result["backtest_run_id"]
        run = db.query(BacktestRunModel).filter(BacktestRunModel.id == run_id).first()
        return {
            "id": run.id,
            "strategy_id": run.strategy_id,
            "instrument": run.instrument,
            "timeframe": run.timeframe,
            "start_date": run.start_date,
            "end_date": run.end_date,
            "total_trades": run.total_trades,
            "net_profit": run.net_profit,
            "sharpe_ratio": run.sharpe_ratio,
            "max_drawdown_pct": run.max_drawdown_pct,
        }

    # Async path — submit to job engine
    label = f"{req.strategy_id} {req.instrument} {req.timeframe.upper()}"
    engine = get_job_engine()
    info = engine.submit(
        job_type="backtest",
        label=label,
        fn=_run_backtest_sync,
        strategy_id=req.strategy_id,
        instrument=req.instrument,
        timeframe=req.timeframe,
        config_overrides=req.config_overrides,
        session_factory=request.app.state.session_factory,
    )
    return JobSubmittedResponse(
        job_id=info.job_id,
        job_type=info.job_type,
        label=info.label,
        state=info.state.value,
    )


@router.get("/backtests", response_model=list[BacktestSummaryResponse])
def list_backtests(
    strategy_id: str | None = None,
    instrument: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """List past backtest runs with optional filtering."""
    runs = get_backtest_results(
        db, strategy_id=strategy_id, instrument=instrument, limit=limit
    )
    if timeframe:
        runs = [r for r in runs if r.timeframe == timeframe.upper()]

    return [
        {
            "id": r.id,
            "strategy_id": r.strategy_id,
            "instrument": r.instrument,
            "timeframe": r.timeframe,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "total_trades": r.total_trades,
            "net_profit": r.net_profit,
            "sharpe_ratio": r.sharpe_ratio,
            "max_drawdown_pct": r.max_drawdown_pct,
            "created_at": r.created_at,
        }
        for r in runs
    ]


@router.get("/backtests/{run_id}", response_model=BacktestDetailResponse)
def get_backtest(
    run_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Get full backtest result with all metrics."""
    run = db.query(BacktestRunModel).filter(BacktestRunModel.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    return {
        "id": run.id,
        "strategy_id": run.strategy_id,
        "instrument": run.instrument,
        "timeframe": run.timeframe,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "total_trades": run.total_trades,
        "net_profit": run.net_profit,
        "sharpe_ratio": run.sharpe_ratio,
        "max_drawdown_pct": run.max_drawdown_pct,
        "config_json": run.config_json,
        "metrics_json": run.metrics_json,
    }


@router.delete("/backtests/bulk")
def bulk_delete_backtests(
    ids: list[int] = Body(..., embed=True),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete multiple backtest runs by IDs."""
    deleted = 0
    for run_id in ids:
        if delete_backtest_run(db, run_id):
            deleted += 1
    return {"deleted_count": deleted, "requested": len(ids)}


@router.delete("/backtests/{run_id}")
def delete_backtest(
    run_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Delete a backtest run and all its trades."""
    deleted = delete_backtest_run(db, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return {"deleted": run_id}


@router.get("/backtests/{run_id}/trades", response_model=TradeListResponse)
def get_backtest_trades(
    run_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Get paginated trade list for a backtest."""
    run = db.query(BacktestRunModel).filter(BacktestRunModel.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    offset = (page - 1) * size
    trades_query = db.query(TradeModel).filter(TradeModel.backtest_run_id == run_id)
    total = trades_query.count()
    trades = trades_query.order_by(TradeModel.entry_time.asc()).offset(offset).limit(size).all()

    return {
        "items": [
            {
                "id": t.id,
                "strategy_id": t.strategy_id,
                "instrument": t.instrument,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl": t.pnl,
                "bars_in_trade": t.bars_in_trade,
            }
            for t in trades
        ],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/backtests/{run_id}/equity-curve", response_model=EquityCurveResponse)
def get_backtest_equity_curve(
    run_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Get equity curve data points for charting."""
    run = db.query(BacktestRunModel).filter(BacktestRunModel.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    metrics = run.metrics_json or {}
    equity_curve = metrics.get("equity_curve", [])

    return {"equity_curve": equity_curve}
