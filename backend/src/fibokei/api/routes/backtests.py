"""Backtest endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
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
from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.loader import load_ohlcv_csv
from fibokei.db.models import BacktestRunModel, TradeModel
from fibokei.db.repository import get_backtest_results, save_backtest_result
from fibokei.strategies.registry import strategy_registry

router = APIRouter(tags=["backtests"])


@router.post("/backtests/run", response_model=BacktestSummaryResponse)
def run_backtest(
    req: BacktestRunRequest,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Run a backtest synchronously and save the results."""
    try:
        strategy = strategy_registry.get(req.strategy_id)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {req.strategy_id}")

    if req.config_overrides:
        strategy.config.update(req.config_overrides)

    config = BacktestConfig()

    default_path = (
        f"../data/fixtures/sample_{req.instrument.lower()}_{req.timeframe.lower()}.csv"
    )
    data_path = req.data_path or default_path
    try:
        tf_enum = Timeframe(req.timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {req.timeframe}")

    try:
        df = load_ohlcv_csv(data_path, req.instrument, tf_enum)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="Data file not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid data format: {e}")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to load data")

    backtester = Backtester(strategy, config)
    result = backtester.run(df, req.instrument, tf_enum)

    metrics = compute_metrics(result)
    # Inject equity curve so it can be retrieved later
    metrics["equity_curve"] = result.equity_curve

    run_model = save_backtest_result(db, result, metrics)

    return {
        "id": run_model.id,
        "strategy_id": run_model.strategy_id,
        "instrument": run_model.instrument,
        "timeframe": run_model.timeframe,
        "start_date": run_model.start_date,
        "end_date": run_model.end_date,
        "total_trades": run_model.total_trades,
        "net_profit": run_model.net_profit,
        "sharpe_ratio": run_model.sharpe_ratio,
        "max_drawdown_pct": run_model.max_drawdown_pct,
    }


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
