"""Data access functions for FIBOKEI persistence."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fibokei.backtester.result import BacktestResult
from fibokei.db.models import (
    BacktestRunModel,
    DatasetModel,
    ResearchResultModel,
    TradeModel,
)


def save_backtest_result(
    session: Session, result: BacktestResult, metrics: dict
) -> BacktestRunModel:
    """Save a backtest result and its trades to the database."""
    run = BacktestRunModel(
        strategy_id=result.strategy_id,
        instrument=result.instrument,
        timeframe=result.timeframe.value,
        config_json=result.config.model_dump() if result.config else None,
        start_date=result.start_date,
        end_date=result.end_date,
        total_trades=len(result.trades),
        net_profit=metrics.get("total_net_profit", 0.0),
        sharpe_ratio=metrics.get("sharpe_ratio"),
        max_drawdown_pct=metrics.get("max_drawdown_pct"),
        metrics_json=metrics,
    )
    session.add(run)
    session.flush()  # Get the run.id

    for trade in result.trades:
        trade_row = TradeModel(
            backtest_run_id=run.id,
            strategy_id=trade.strategy_id,
            instrument=trade.instrument,
            direction=trade.direction.value,
            entry_time=trade.entry_time,
            entry_price=trade.entry_price,
            exit_time=trade.exit_time,
            exit_price=trade.exit_price,
            exit_reason=trade.exit_reason.value,
            pnl=trade.pnl,
            bars_in_trade=trade.bars_in_trade,
        )
        session.add(trade_row)

    session.commit()
    return run


def get_backtest_results(
    session: Session,
    strategy_id: str | None = None,
    instrument: str | None = None,
    limit: int = 100,
) -> list[BacktestRunModel]:
    """Retrieve backtest results with optional filters."""
    stmt = select(BacktestRunModel)
    if strategy_id:
        stmt = stmt.where(BacktestRunModel.strategy_id == strategy_id)
    if instrument:
        stmt = stmt.where(BacktestRunModel.instrument == instrument)
    stmt = stmt.order_by(BacktestRunModel.created_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def save_research_results(
    session: Session, results: list[dict]
) -> list[ResearchResultModel]:
    """Save research matrix results."""
    models = []
    for r in results:
        model = ResearchResultModel(
            run_id=r["run_id"],
            strategy_id=r["strategy_id"],
            instrument=r["instrument"],
            timeframe=r["timeframe"],
            composite_score=r.get("composite_score", 0.0),
            rank=r.get("rank", 0),
            metrics_json=r.get("metrics"),
        )
        session.add(model)
        models.append(model)
    session.commit()
    return models


def get_research_rankings(
    session: Session,
    sort_by: str = "composite_score",
    limit: int = 50,
) -> list[ResearchResultModel]:
    """Get research results ranked by score."""
    stmt = select(ResearchResultModel)
    if sort_by == "composite_score":
        stmt = stmt.order_by(ResearchResultModel.composite_score.desc())
    elif sort_by == "rank":
        stmt = stmt.order_by(ResearchResultModel.rank.asc())
    stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def save_dataset_meta(session: Session, meta: dict) -> DatasetModel:
    """Save dataset metadata."""
    model = DatasetModel(
        instrument=meta["instrument"],
        timeframe=meta["timeframe"],
        source_id=meta.get("source_id"),
        bar_count=meta.get("bar_count", 0),
        start_date=meta.get("start_date"),
        end_date=meta.get("end_date"),
        status=meta.get("status", "active"),
        file_path=meta.get("file_path"),
    )
    session.add(model)
    session.commit()
    return model
