"""Chart annotation endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.api.schemas.charts import (
    ChartAnnotationsResponse,
    PricePoint,
    TradeMarker,
)
from fibokei.db.models import BacktestRunModel, TradeModel

router = APIRouter(tags=["charts"])


@router.get(
    "/charts/annotations/{backtest_id}",
    response_model=ChartAnnotationsResponse,
)
def get_chart_annotations(
    backtest_id: int,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Return trade markers and strategy annotations for a backtest run."""
    run = db.query(BacktestRunModel).filter(BacktestRunModel.id == backtest_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    trades = (
        db.query(TradeModel)
        .filter(TradeModel.backtest_run_id == backtest_id)
        .order_by(TradeModel.entry_time.asc())
        .all()
    )

    trade_markers = []
    for t in trades:
        entry_point = PricePoint(
            timestamp=t.entry_time.isoformat(),
            price=t.entry_price,
        )
        exit_point = PricePoint(
            timestamp=t.exit_time.isoformat(),
            price=t.exit_price,
        )
        outcome = "win" if t.pnl > 0 else ("loss" if t.pnl < 0 else "breakeven")
        trade_markers.append(
            TradeMarker(
                trade_id=str(t.id),
                strategy_id=t.strategy_id,
                direction=t.direction,
                entry=entry_point,
                exit=exit_point,
                label=f"{t.direction} {t.exit_reason}",
                outcome=outcome,
            )
        )

    return ChartAnnotationsResponse(
        trade_markers=trade_markers,
        strategy_annotations=[],
    )
