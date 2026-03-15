"""DEV-ONLY routes for seeding test data.

Gated behind FIBOKEI_DEV_SEED=1 environment variable.
These endpoints MUST NOT be available in production.
"""

import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from fibokei.api.deps import get_db

router = APIRouter(tags=["dev"])


def _require_dev_mode():
    """Fail if dev seed mode is not enabled."""
    if not os.environ.get("FIBOKEI_DEV_SEED"):
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/dev/seed/backtest")
def seed_backtest(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_require_dev_mode),
):
    """Create a minimal backtest + trades for UI testing.

    Returns the created backtest_run_id. Idempotent: if a seed backtest
    already exists (strategy_id='seed_ichimoku_pullback'), returns the existing one.

    DEV ONLY — gated behind FIBOKEI_DEV_SEED=1.
    """
    from fibokei.db.models import BacktestRunModel, TradeModel

    # Check if seed backtest already exists
    existing = db.query(BacktestRunModel).filter(
        BacktestRunModel.strategy_id == "seed_ichimoku_pullback",
        BacktestRunModel.instrument == "EURUSD",
        BacktestRunModel.timeframe == "H1",
    ).first()

    if existing:
        return {
            "backtest_run_id": existing.id,
            "seeded": False,
            "message": "Seed backtest already exists",
        }

    # Create backtest run
    base_time = datetime(2024, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
    run = BacktestRunModel(
        strategy_id="seed_ichimoku_pullback",
        instrument="EURUSD",
        timeframe="H1",
        start_date=base_time,
        end_date=base_time + timedelta(days=30),
        total_trades=5,
        net_profit=42.50,
        sharpe_ratio=1.35,
        max_drawdown_pct=3.2,
        metrics_json={
            "win_rate": 0.60,
            "profit_factor": 1.85,
            "expectancy": 8.50,
            "initial_capital": 10000,
            "risk_per_trade_pct": 1.0,
            "spread_points": 0.00016,
            "slippage_points": 0,
            "max_leverage": 30,
        },
    )
    db.add(run)
    db.flush()

    # Create sample trades (mix of wins/losses, different exit reasons)
    trades_data = [
        {"dir": "LONG", "entry": 1.0850, "exit": 1.0885, "reason": "take_profit_hit", "pnl": 17.50, "bars": 8, "offset_h": 24},
        {"dir": "SHORT", "entry": 1.0890, "exit": 1.0870, "reason": "take_profit_hit", "pnl": 10.00, "bars": 5, "offset_h": 72},
        {"dir": "LONG", "entry": 1.0860, "exit": 1.0840, "reason": "stop_loss_hit", "pnl": -10.00, "bars": 3, "offset_h": 120},
        {"dir": "LONG", "entry": 1.0855, "exit": 1.0880, "reason": "take_profit_hit", "pnl": 12.50, "bars": 12, "offset_h": 200},
        {"dir": "SHORT", "entry": 1.0895, "exit": 1.0882, "reason": "take_profit_hit", "pnl": -0.50, "bars": 6, "offset_h": 350},
    ]

    for td in trades_data:
        entry_time = base_time + timedelta(hours=td["offset_h"])
        exit_time = entry_time + timedelta(hours=td["bars"])
        trade = TradeModel(
            backtest_run_id=run.id,
            strategy_id="seed_ichimoku_pullback",
            instrument="EURUSD",
            direction=td["dir"],
            entry_time=entry_time,
            entry_price=td["entry"],
            exit_time=exit_time,
            exit_price=td["exit"],
            exit_reason=td["reason"],
            pnl=td["pnl"],
            bars_in_trade=td["bars"],
        )
        db.add(trade)

    db.commit()

    return {
        "backtest_run_id": run.id,
        "seeded": True,
        "message": "Seed backtest created with 5 trades",
    }
