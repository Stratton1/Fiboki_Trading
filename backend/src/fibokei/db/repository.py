"""Data access functions for Fiboki persistence."""

import math
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from fibokei.backtester.result import BacktestResult
from fibokei.db.models import (
    AlertModel,
    BacktestRunModel,
    ChartDrawingModel,
    DatasetModel,
    DrawingTemplateModel,
    EvaluationPhaseModel,
    ExecutionAuditModel,
    KillSwitchModel,
    PaperAccountModel,
    PaperBotModel,
    PaperTradeModel,
    ResearchResultModel,
    TradeJournalModel,
    TradeModel,
    WatchlistModel,
)

if TYPE_CHECKING:
    # Type-only import for the quoted "datetime | None" parameter annotations
    # used throughout this module; runtime call sites already import datetime
    # locally where needed.
    from datetime import datetime  # noqa: F401


def _sanitize_for_json(obj):
    """Convert numpy types to native Python types for JSON/SQL serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [_sanitize_for_json(v) for v in obj.tolist()]
    return obj


# ---------- Chart drawings ----------


def save_drawing(session: Session, drawing_data: dict) -> ChartDrawingModel:
    """Save a chart drawing."""
    model = ChartDrawingModel(**drawing_data)
    session.add(model)
    session.commit()
    return model


def get_drawings(
    session: Session, user_id: int, instrument: str, timeframe: str
) -> list[ChartDrawingModel]:
    """Get all drawings for a user/instrument/timeframe combo."""
    stmt = (
        select(ChartDrawingModel)
        .where(ChartDrawingModel.user_id == user_id)
        .where(ChartDrawingModel.instrument == instrument)
        .where(ChartDrawingModel.timeframe == timeframe)
        .order_by(ChartDrawingModel.created_at.asc())
    )
    return list(session.scalars(stmt).all())


def update_drawing(
    session: Session, drawing_id: int, user_id: int, updates: dict
) -> ChartDrawingModel | None:
    """Update a drawing. Returns None if not found or wrong user."""
    drawing = session.scalars(
        select(ChartDrawingModel)
        .where(ChartDrawingModel.id == drawing_id)
        .where(ChartDrawingModel.user_id == user_id)
    ).first()
    if not drawing:
        return None
    for key, value in updates.items():
        if hasattr(drawing, key):
            setattr(drawing, key, value)
    session.commit()
    return drawing


def delete_drawing(session: Session, drawing_id: int, user_id: int) -> bool:
    """Delete a single drawing. Returns True if deleted."""
    drawing = session.scalars(
        select(ChartDrawingModel)
        .where(ChartDrawingModel.id == drawing_id)
        .where(ChartDrawingModel.user_id == user_id)
    ).first()
    if not drawing:
        return False
    session.delete(drawing)
    session.commit()
    return True


def delete_all_drawings(
    session: Session, user_id: int, instrument: str, timeframe: str
) -> int:
    """Delete all drawings for a user/instrument/timeframe. Returns count deleted."""
    drawings = get_drawings(session, user_id, instrument, timeframe)
    count = len(drawings)
    for d in drawings:
        session.delete(d)
    if count:
        session.commit()
    return count


# ---------- Drawing templates ----------


def save_drawing_template(session: Session, data: dict) -> DrawingTemplateModel:
    """Create a drawing template."""
    model = DrawingTemplateModel(**data)
    session.add(model)
    session.commit()
    return model


def get_drawing_templates(session: Session, user_id: int) -> list[DrawingTemplateModel]:
    """Get all drawing templates for a user."""
    stmt = (
        select(DrawingTemplateModel)
        .where(DrawingTemplateModel.user_id == user_id)
        .order_by(DrawingTemplateModel.updated_at.desc())
    )
    return list(session.scalars(stmt).all())


def update_drawing_template(
    session: Session, template_id: int, user_id: int, updates: dict
) -> DrawingTemplateModel | None:
    """Update a template. Returns None if not found or wrong user."""
    tmpl = session.scalars(
        select(DrawingTemplateModel)
        .where(DrawingTemplateModel.id == template_id)
        .where(DrawingTemplateModel.user_id == user_id)
    ).first()
    if not tmpl:
        return None
    for key, value in updates.items():
        if hasattr(tmpl, key):
            setattr(tmpl, key, value)
    session.commit()
    return tmpl


def delete_drawing_template(session: Session, template_id: int, user_id: int) -> bool:
    """Delete a drawing template. Returns True if deleted."""
    tmpl = session.scalars(
        select(DrawingTemplateModel)
        .where(DrawingTemplateModel.id == template_id)
        .where(DrawingTemplateModel.user_id == user_id)
    ).first()
    if not tmpl:
        return False
    session.delete(tmpl)
    session.commit()
    return True


# ---------- Backtest results ----------


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
        net_profit=float(metrics.get("total_net_profit", 0.0)),
        sharpe_ratio=(
            float(metrics["sharpe_ratio"])
            if metrics.get("sharpe_ratio") is not None else None
        ),
        max_drawdown_pct=(
            float(metrics["max_drawdown_pct"])
            if metrics.get("max_drawdown_pct") is not None else None
        ),
        metrics_json=_sanitize_for_json(metrics),
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
            entry_price=float(trade.entry_price),
            exit_time=trade.exit_time,
            exit_price=float(trade.exit_price),
            exit_reason=trade.exit_reason.value,
            pnl=float(trade.pnl),
            bars_in_trade=int(trade.bars_in_trade),
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
            composite_score=float(r.get("composite_score", 0.0)),
            rank=int(r.get("rank", 0)),
            metrics_json=_sanitize_for_json(r.get("metrics")),
        )
        session.add(model)
        models.append(model)
    session.commit()
    return models


def _get_visible_strategy_ids() -> set[str] | None:
    """Return visible strategy IDs from env, or None if unset (show all)."""
    import os
    visible = os.environ.get("FIBOKEI_VISIBLE_STRATEGIES", "")
    if not visible:
        return None
    return {s.strip() for s in visible.split(",") if s.strip()}


def get_research_rankings(
    session: Session,
    sort_by: str = "composite_score",
    limit: int = 50,
    run_id: str | None = None,
    deduplicate: bool = False,
    strategy_id: str | None = None,
) -> list[ResearchResultModel]:
    """Get research results ranked by score.

    If run_id is provided, results are scoped to that run only.
    If deduplicate is True and run_id is None (all-runs view), returns only the
    best-scoring row per (strategy_id, instrument, timeframe) combo.
    Respects FIBOKEI_VISIBLE_STRATEGIES filter unless strategy_id is specified.
    """
    # If a specific strategy_id is requested, bypass visible filter
    if strategy_id:
        visible = {strategy_id}
    else:
        visible = _get_visible_strategy_ids()

    if deduplicate and not run_id:
        stmt = select(ResearchResultModel).order_by(ResearchResultModel.composite_score.desc())
        if visible:
            stmt = stmt.where(ResearchResultModel.strategy_id.in_(visible))
        all_results = list(session.scalars(stmt).all())
        seen: set[tuple[str, str, str]] = set()
        deduped: list[ResearchResultModel] = []
        for r in all_results:
            key = (r.strategy_id, r.instrument, r.timeframe)
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        if sort_by == "rank":
            deduped.sort(key=lambda r: r.rank)
        return deduped[:limit]

    stmt = select(ResearchResultModel)
    if run_id:
        stmt = stmt.where(ResearchResultModel.run_id == run_id)
    if visible:
        stmt = stmt.where(ResearchResultModel.strategy_id.in_(visible))
    if sort_by == "composite_score":
        stmt = stmt.order_by(ResearchResultModel.composite_score.desc())
    elif sort_by == "rank":
        stmt = stmt.order_by(ResearchResultModel.rank.asc())
    stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def delete_research_run(session: Session, run_id: str) -> int:
    """Delete all research results for a given run_id. Returns count deleted."""
    results = list(
        session.scalars(
            select(ResearchResultModel).where(ResearchResultModel.run_id == run_id)
        ).all()
    )
    count = len(results)
    for r in results:
        session.delete(r)
    if count:
        session.commit()
    return count


def delete_single_research_result(session: Session, result_id: int) -> bool:
    """Delete a single research result row. Returns True if deleted."""
    result = session.get(ResearchResultModel, result_id)
    if not result:
        return False
    session.delete(result)
    session.commit()
    return True


def delete_all_research_results(
    session: Session,
    run_id: str | None = None,
    keep_combos: list[tuple[str, str, str]] | None = None,
) -> int:
    """Bulk delete research results.

    If run_id is given, only delete results from that run.
    If keep_combos is given, skip rows matching those (strategy_id, instrument, timeframe) tuples.
    """
    stmt = select(ResearchResultModel)
    if run_id:
        stmt = stmt.where(ResearchResultModel.run_id == run_id)
    results = list(session.scalars(stmt).all())

    keep_set = set(keep_combos) if keep_combos else set()
    count = 0
    for r in results:
        if keep_set and (r.strategy_id, r.instrument, r.timeframe) in keep_set:
            continue
        session.delete(r)
        count += 1
    if count:
        session.commit()
    return count


def list_research_runs(session: Session) -> list[dict]:
    """List distinct research runs with metadata including top score and strategies."""
    from sqlalchemy import func

    stmt = (
        select(
            ResearchResultModel.run_id,
            func.min(ResearchResultModel.created_at).label("created_at"),
            func.count(ResearchResultModel.id).label("result_count"),
            func.max(ResearchResultModel.composite_score).label("top_score"),
        )
        .group_by(ResearchResultModel.run_id)
        .order_by(func.min(ResearchResultModel.created_at).desc())
    )
    rows = session.execute(stmt).all()
    return [
        {
            "run_id": row.run_id,
            "created_at": row.created_at,
            "result_count": row.result_count,
            "top_score": round(row.top_score, 4) if row.top_score else 0.0,
        }
        for row in rows
    ]


def delete_non_saved_results(session: Session, user_id: int, run_id: str | None = None) -> int:
    """Delete research results whose combos are NOT in the user's saved shortlist.

    If run_id is given, only targets that run. Returns count deleted.
    """
    from fibokei.db.models import SavedShortlistModel

    # Get all shortlisted combos for this user
    shortlisted = session.execute(
        select(
            SavedShortlistModel.strategy_id,
            SavedShortlistModel.instrument,
            SavedShortlistModel.timeframe,
        ).where(SavedShortlistModel.user_id == user_id)
    ).all()
    saved_set = {(s.strategy_id, s.instrument, s.timeframe) for s in shortlisted}

    stmt = select(ResearchResultModel)
    if run_id:
        stmt = stmt.where(ResearchResultModel.run_id == run_id)
    results = list(session.scalars(stmt).all())

    count = 0
    for r in results:
        if (r.strategy_id, r.instrument, r.timeframe) not in saved_set:
            session.delete(r)
            count += 1
    if count:
        session.commit()
    return count


# ── Saved Shortlist ──────────────────────────────────────────


def list_saved_shortlist(session: Session, user_id: int) -> list:
    """List all saved shortlist entries for a user."""
    from fibokei.db.models import SavedShortlistModel

    stmt = (
        select(SavedShortlistModel)
        .where(SavedShortlistModel.user_id == user_id)
        .order_by(SavedShortlistModel.score.desc())
    )
    return list(session.scalars(stmt).all())


def upsert_shortlist_entry(session: Session, user_id: int, data: dict):
    """Save or update a shortlist entry (upsert by user+strategy+instrument+tf)."""
    from fibokei.db.models import SavedShortlistModel

    existing = session.scalar(
        select(SavedShortlistModel).where(
            SavedShortlistModel.user_id == user_id,
            SavedShortlistModel.strategy_id == data["strategy_id"],
            SavedShortlistModel.instrument == data["instrument"],
            SavedShortlistModel.timeframe == data["timeframe"],
        )
    )
    if existing:
        existing.score = data["score"]
        existing.source_run_id = data.get("source_run_id")
        existing.metrics_snapshot = data.get("metrics_snapshot")
        if data.get("note") is not None:
            existing.note = data["note"]
        session.commit()
        session.refresh(existing)
        return existing

    entry = SavedShortlistModel(
        user_id=user_id,
        strategy_id=data["strategy_id"],
        instrument=data["instrument"],
        timeframe=data["timeframe"],
        score=data["score"],
        source_run_id=data.get("source_run_id"),
        metrics_snapshot=data.get("metrics_snapshot"),
        note=data.get("note"),
        status=data.get("status", "active"),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def update_shortlist_entry(session: Session, entry_id: int, user_id: int, updates: dict):
    """Update note or status on a shortlist entry. Returns updated entry or None."""
    from fibokei.db.models import SavedShortlistModel

    entry = session.scalar(
        select(SavedShortlistModel).where(
            SavedShortlistModel.id == entry_id,
            SavedShortlistModel.user_id == user_id,
        )
    )
    if not entry:
        return None
    if "note" in updates:
        entry.note = updates["note"]
    if "status" in updates:
        entry.status = updates["status"]
    session.commit()
    session.refresh(entry)
    return entry


def delete_shortlist_entry(session: Session, entry_id: int, user_id: int) -> bool:
    """Remove a shortlist entry. Returns True if deleted."""
    from fibokei.db.models import SavedShortlistModel

    entry = session.scalar(
        select(SavedShortlistModel).where(
            SavedShortlistModel.id == entry_id,
            SavedShortlistModel.user_id == user_id,
        )
    )
    if not entry:
        return False
    session.delete(entry)
    session.commit()
    return True


def migrate_bookmarks_to_shortlist(session: Session) -> int:
    """Migrate existing research_result bookmarks to saved_shortlist entries.

    Idempotent — skips bookmarks that already have a matching shortlist entry.
    Returns count of entries created.
    """
    from fibokei.db.models import BookmarkModel, SavedShortlistModel

    bookmarks = list(
        session.scalars(
            select(BookmarkModel).where(BookmarkModel.entity_type == "research_result")
        ).all()
    )
    created = 0
    for bm in bookmarks:
        result = session.get(ResearchResultModel, bm.entity_id)
        if not result:
            continue  # orphaned bookmark — skip

        # Check if shortlist entry already exists for this combo
        existing = session.scalar(
            select(SavedShortlistModel).where(
                SavedShortlistModel.user_id == bm.user_id,
                SavedShortlistModel.strategy_id == result.strategy_id,
                SavedShortlistModel.instrument == result.instrument,
                SavedShortlistModel.timeframe == result.timeframe,
            )
        )
        if existing:
            # Update if new score is higher
            if result.composite_score > existing.score:
                existing.score = result.composite_score
                existing.source_run_id = result.run_id
                existing.metrics_snapshot = result.metrics_json
            continue

        entry = SavedShortlistModel(
            user_id=bm.user_id,
            strategy_id=result.strategy_id,
            instrument=result.instrument,
            timeframe=result.timeframe,
            score=result.composite_score,
            source_run_id=result.run_id,
            metrics_snapshot=result.metrics_json,
            note=bm.note,
            status="active",
        )
        session.add(entry)
        created += 1

    if created:
        session.commit()
    return created


def delete_backtest_run(session: Session, run_id: int) -> bool:
    """Delete a backtest run and its trades (via cascade). Returns True if deleted."""
    run = session.get(BacktestRunModel, run_id)
    if not run:
        return False
    session.delete(run)
    session.commit()
    return True


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


# ---------- Paper trading persistence ----------


def save_paper_bot(session: Session, bot_data: dict) -> PaperBotModel:
    """Create or update a paper bot record."""
    existing = session.scalars(
        select(PaperBotModel).where(PaperBotModel.bot_id == bot_data["bot_id"])
    ).first()
    if existing:
        for key, value in bot_data.items():
            if key != "bot_id" and hasattr(existing, key):
                setattr(existing, key, value)
        session.commit()
        return existing
    model = PaperBotModel(**bot_data)
    session.add(model)
    session.commit()
    return model


def get_paper_bot(session: Session, bot_id: str) -> PaperBotModel | None:
    """Get a paper bot by its bot_id."""
    return session.scalars(
        select(PaperBotModel).where(PaperBotModel.bot_id == bot_id)
    ).first()


def get_paper_bots(
    session: Session, state: str | None = None
) -> list[PaperBotModel]:
    """Get all paper bots, optionally filtered by state."""
    stmt = select(PaperBotModel)
    if state:
        stmt = stmt.where(PaperBotModel.state == state)
    stmt = stmt.order_by(PaperBotModel.created_at.desc())
    return list(session.scalars(stmt).all())


def get_active_paper_bots(session: Session) -> list[PaperBotModel]:
    """Get bots in monitoring or position_open state (for worker recovery)."""
    stmt = select(PaperBotModel).where(
        PaperBotModel.state.in_(["monitoring", "position_open"])
    )
    return list(session.scalars(stmt).all())


def delete_paper_bot(session: Session, bot_id: str) -> bool:
    """Delete a paper bot and its trades. Returns True if deleted."""
    bot = get_paper_bot(session, bot_id)
    if not bot:
        return False
    # Delete associated trades first
    from fibokei.db.models import PaperTradeModel
    session.query(PaperTradeModel).where(PaperTradeModel.bot_id == bot_id).delete()
    session.delete(bot)
    session.commit()
    return True


def delete_all_paper_bots(session: Session) -> int:
    """Delete all paper bots and their trades. Returns count deleted."""
    from fibokei.db.models import PaperTradeModel
    bots = get_paper_bots(session)
    count = len(bots)
    session.query(PaperTradeModel).delete()
    for bot in bots:
        session.delete(bot)
    session.commit()
    return count


def update_paper_bot_state(
    session: Session,
    bot_id: str,
    state: str,
    last_evaluated_bar: "datetime | None" = None,
    bars_seen: int | None = None,
    position_json: dict | None = None,
    error_message: str | None = None,
) -> PaperBotModel | None:
    """Update bot state fields. Returns None if bot not found."""

    bot = get_paper_bot(session, bot_id)
    if not bot:
        return None
    bot.state = state
    if last_evaluated_bar is not None:
        bot.last_evaluated_bar = last_evaluated_bar
    if bars_seen is not None:
        bot.bars_seen = bars_seen
    if position_json is not None:
        bot.position_json = position_json
    if error_message is not None:
        bot.error_message = error_message
    session.commit()
    return bot


def save_paper_trade(session: Session, trade_data: dict) -> PaperTradeModel:
    """Record a closed paper trade."""
    model = PaperTradeModel(**trade_data)
    session.add(model)
    session.commit()
    return model


def get_paper_trades(
    session: Session,
    bot_id: str | None = None,
    limit: int = 100,
    is_live: bool | None = None,
    since: "datetime | None" = None,
) -> list[PaperTradeModel]:
    """Get paper trades, optionally filtered by bot_id, is_live, and/or since.

    is_live=True  → only forward-monitoring trades (entry_time >= bot.created_at)
    is_live=False → only historical-replay trades
    is_live=None  → all trades (default)
    since         → only trades with entry_time >= since (used for phase-scoped stats)
    """
    stmt = select(PaperTradeModel)
    if bot_id:
        stmt = stmt.where(PaperTradeModel.bot_id == bot_id)
    if is_live is True:
        stmt = stmt.where(PaperTradeModel.is_live == True)  # noqa: E712
    elif is_live is False:
        stmt = stmt.where(PaperTradeModel.is_live == False)  # noqa: E712
    if since is not None:
        stmt = stmt.where(PaperTradeModel.entry_time >= since)
    stmt = stmt.order_by(PaperTradeModel.created_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def get_or_create_paper_account(
    session: Session, initial_balance: float = 1000.0
) -> PaperAccountModel:
    """Get the single paper account record, creating if needed."""
    from fibokei.paper.account import DEFAULT_CURRENCY, DEFAULT_INITIAL_BALANCE

    balance = initial_balance if initial_balance != 1000.0 else DEFAULT_INITIAL_BALANCE
    account = session.scalars(select(PaperAccountModel)).first()
    if not account:
        account = PaperAccountModel(
            initial_balance=balance,
            balance=balance,
            equity=balance,
            currency=DEFAULT_CURRENCY,
        )
        session.add(account)
        session.commit()
    return account


def update_paper_account(
    session: Session,
    balance: float,
    equity: float,
    daily_pnl: float,
    weekly_pnl: float,
) -> PaperAccountModel:
    """Update the paper account snapshot."""
    account = get_or_create_paper_account(session)
    account.balance = balance
    account.equity = equity
    account.daily_pnl = daily_pnl
    account.weekly_pnl = weekly_pnl
    session.commit()
    return account


def reset_paper_account(session: Session) -> PaperAccountModel:
    """Reset paper account to initial balance, clear all PnL counters."""
    account = get_or_create_paper_account(session)
    account.balance = account.initial_balance
    account.equity = account.initial_balance
    account.daily_pnl = 0.0
    account.weekly_pnl = 0.0
    session.commit()
    return account


def reset_paper_counters_and_recalculate(session: Session) -> PaperAccountModel:
    """Reset daily/weekly counters and recalculate balance from closed trades.

    Corrects balance corruption caused by warmup-phase PnL bleeding into the
    account before the warmup fix was deployed. Balance is rebuilt as:
        initial_balance + sum(all closed trade PnL)
    Daily and weekly PnL counters are zeroed — the worker will accumulate
    them correctly from this point forward.
    """
    from sqlalchemy import func as _func
    account = get_or_create_paper_account(session)
    total_pnl = session.scalar(
        select(_func.coalesce(_func.sum(PaperTradeModel.pnl), 0.0))
    ) or 0.0
    account.balance = account.initial_balance + total_pnl
    account.equity = account.balance
    account.daily_pnl = 0.0
    account.weekly_pnl = 0.0
    session.commit()
    return account


def get_best_research_score(
    session: Session, strategy_id: str, instrument: str, timeframe: str
) -> float | None:
    """Get the best composite score for a strategy/instrument/timeframe combo."""
    result = session.scalars(
        select(ResearchResultModel.composite_score)
        .where(ResearchResultModel.strategy_id == strategy_id)
        .where(ResearchResultModel.instrument == instrument)
        .where(ResearchResultModel.timeframe == timeframe)
        .order_by(ResearchResultModel.composite_score.desc())
        .limit(1)
    ).first()
    return result


# ---------- Execution audit ----------


def save_execution_audit(session: Session, audit_data: dict) -> ExecutionAuditModel:
    """Record an execution audit log entry."""
    model = ExecutionAuditModel(**audit_data)
    session.add(model)
    session.commit()
    return model


def get_execution_audit(
    session: Session,
    execution_mode: str | None = None,
    bot_id: str | None = None,
    limit: int = 100,
) -> list[ExecutionAuditModel]:
    """Retrieve execution audit entries with optional filters."""
    stmt = select(ExecutionAuditModel)
    if execution_mode:
        stmt = stmt.where(ExecutionAuditModel.execution_mode == execution_mode)
    if bot_id:
        stmt = stmt.where(ExecutionAuditModel.bot_id == bot_id)
    stmt = stmt.order_by(ExecutionAuditModel.timestamp.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def get_slippage_summary(
    session: Session,
    instrument: str | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate slippage statistics from execution audit entries.

    Returns per-instrument stats and an overall summary.
    """
    from sqlalchemy import func

    stmt = select(
        ExecutionAuditModel.instrument,
        func.count().label("fills"),
        func.avg(ExecutionAuditModel.slippage_pips).label("avg_slippage"),
        func.max(ExecutionAuditModel.slippage_pips).label("max_slippage"),
        func.min(ExecutionAuditModel.slippage_pips).label("min_slippage"),
        func.avg(ExecutionAuditModel.fill_latency_ms).label("avg_latency_ms"),
    ).where(
        ExecutionAuditModel.filled_price.is_not(None),
    ).where(
        ExecutionAuditModel.status == "success",
    )

    if instrument:
        stmt = stmt.where(ExecutionAuditModel.instrument == instrument)

    stmt = stmt.group_by(ExecutionAuditModel.instrument)
    rows = session.execute(stmt).all()

    instruments = []
    total_fills = 0
    total_slippage_sum = 0.0
    for row in rows:
        avg_slip = round(float(row.avg_slippage or 0), 2)
        fills = int(row.fills)
        total_fills += fills
        total_slippage_sum += avg_slip * fills
        instruments.append({
            "instrument": row.instrument,
            "fills": fills,
            "avg_slippage_pips": avg_slip,
            "max_slippage_pips": round(float(row.max_slippage or 0), 2),
            "min_slippage_pips": round(float(row.min_slippage or 0), 2),
            "avg_latency_ms": round(float(row.avg_latency_ms or 0), 1),
        })

    return {
        "total_fills": total_fills,
        "avg_slippage_pips": round(total_slippage_sum / total_fills, 2) if total_fills else 0.0,
        "instruments": instruments,
    }


# ---------- Kill switch ----------


def get_kill_switch(session: Session) -> KillSwitchModel:
    """Get or create the kill switch record (single-row table)."""
    ks = session.scalars(select(KillSwitchModel)).first()
    if not ks:
        ks = KillSwitchModel(is_active=False)
        session.add(ks)
        session.commit()
    return ks


def activate_kill_switch(
    session: Session, reason: str = "", activated_by: str = ""
) -> KillSwitchModel:
    """Activate the kill switch — blocks all execution."""
    from datetime import datetime, timezone

    ks = get_kill_switch(session)
    ks.is_active = True
    ks.reason = reason
    ks.activated_by = activated_by
    ks.activated_at = datetime.now(timezone.utc)
    ks.deactivated_at = None
    session.commit()
    return ks


def deactivate_kill_switch(session: Session) -> KillSwitchModel:
    """Deactivate the kill switch — resume execution."""
    from datetime import datetime, timezone

    ks = get_kill_switch(session)
    ks.is_active = False
    ks.deactivated_at = datetime.now(timezone.utc)
    session.commit()
    return ks


# ---------- Alerts ----------


def save_alert(
    session: Session,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    metadata_json: dict | None = None,
) -> AlertModel:
    """Create an in-app alert."""
    model = AlertModel(
        alert_type=alert_type,
        severity=severity,
        title=title,
        message=message,
        metadata_json=metadata_json,
    )
    session.add(model)
    session.commit()
    return model


def list_alerts(
    session: Session,
    alert_type: str | None = None,
    is_read: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AlertModel]:
    """List alerts with optional filters, newest first."""
    stmt = select(AlertModel)
    if alert_type:
        stmt = stmt.where(AlertModel.alert_type == alert_type)
    if is_read is not None:
        stmt = stmt.where(AlertModel.is_read == is_read)
    stmt = stmt.order_by(AlertModel.created_at.desc()).offset(offset).limit(limit)
    return list(session.scalars(stmt).all())


def count_unread_alerts(session: Session) -> int:
    """Count unread alerts."""
    from sqlalchemy import func

    result = session.execute(
        select(func.count()).select_from(AlertModel).where(AlertModel.is_read == False)  # noqa: E712
    )
    return result.scalar() or 0


def mark_alert_read(session: Session, alert_id: int) -> AlertModel | None:
    """Mark a single alert as read."""
    alert = session.get(AlertModel, alert_id)
    if not alert:
        return None
    alert.is_read = True
    session.commit()
    return alert


def mark_all_alerts_read(session: Session) -> int:
    """Mark all unread alerts as read. Returns count updated."""
    from sqlalchemy import update

    result = session.execute(
        update(AlertModel).where(AlertModel.is_read == False).values(is_read=True)  # noqa: E712
    )
    session.commit()
    return result.rowcount


# ---------- Watchlists ----------


def create_watchlist(
    session: Session, user_id: int, name: str, instrument_ids: list[str]
) -> WatchlistModel:
    """Create a new watchlist."""
    model = WatchlistModel(user_id=user_id, name=name, instrument_ids=instrument_ids)
    session.add(model)
    session.commit()
    return model


def list_watchlists(session: Session, user_id: int) -> list[WatchlistModel]:
    """Get all watchlists for a user."""
    stmt = (
        select(WatchlistModel)
        .where(WatchlistModel.user_id == user_id)
        .order_by(WatchlistModel.created_at.asc())
    )
    return list(session.scalars(stmt).all())


def get_watchlist(session: Session, watchlist_id: int, user_id: int) -> WatchlistModel | None:
    """Get a single watchlist by ID and user."""
    return session.scalars(
        select(WatchlistModel)
        .where(WatchlistModel.id == watchlist_id)
        .where(WatchlistModel.user_id == user_id)
    ).first()


def update_watchlist(
    session: Session, watchlist_id: int, user_id: int, updates: dict
) -> WatchlistModel | None:
    """Update a watchlist. Returns None if not found or wrong user."""
    wl = session.scalars(
        select(WatchlistModel)
        .where(WatchlistModel.id == watchlist_id)
        .where(WatchlistModel.user_id == user_id)
    ).first()
    if not wl:
        return None
    for key, value in updates.items():
        if hasattr(wl, key):
            setattr(wl, key, value)
    session.commit()
    return wl


def delete_watchlist(session: Session, watchlist_id: int, user_id: int) -> bool:
    """Delete a watchlist. Returns True if deleted."""
    wl = session.scalars(
        select(WatchlistModel)
        .where(WatchlistModel.id == watchlist_id)
        .where(WatchlistModel.user_id == user_id)
    ).first()
    if not wl:
        return False
    session.delete(wl)
    session.commit()
    return True


# ---------- Trade journal ----------


def create_journal_entry(
    session: Session,
    user_id: int,
    trade_id: int,
    note: str | None,
    tags: list[str] | None,
) -> TradeJournalModel:
    """Create a journal entry for a trade."""
    model = TradeJournalModel(
        user_id=user_id, trade_id=trade_id, note=note, tags=tags or []
    )
    session.add(model)
    session.commit()
    return model


def get_journal_entry(
    session: Session, trade_id: int, user_id: int
) -> TradeJournalModel | None:
    """Get a journal entry for a trade."""
    return session.scalars(
        select(TradeJournalModel)
        .where(TradeJournalModel.trade_id == trade_id)
        .where(TradeJournalModel.user_id == user_id)
    ).first()


def update_journal_entry(
    session: Session, trade_id: int, user_id: int, updates: dict
) -> TradeJournalModel | None:
    """Update a journal entry. Returns None if not found or wrong user."""
    entry = get_journal_entry(session, trade_id, user_id)
    if not entry:
        return None
    for key, value in updates.items():
        if hasattr(entry, key) and key not in ("id", "user_id", "trade_id", "created_at"):
            setattr(entry, key, value)
    session.commit()
    return entry


def delete_journal_entry(session: Session, trade_id: int, user_id: int) -> bool:
    """Delete a journal entry. Returns True if deleted."""
    entry = get_journal_entry(session, trade_id, user_id)
    if not entry:
        return False
    session.delete(entry)
    session.commit()
    return True


def list_journal_entries(
    session: Session,
    user_id: int,
    tag: str | None = None,
    limit: int = 50,
) -> list[TradeJournalModel]:
    """List journal entries for a user, optionally filtered by tag, newest first."""
    stmt = (
        select(TradeJournalModel)
        .where(TradeJournalModel.user_id == user_id)
    )
    if tag:
        stmt = stmt.where(TradeJournalModel.tags.contains(tag))
    stmt = stmt.order_by(TradeJournalModel.created_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


# ── Execution Accounts (Phase 2) ───────────────────────────

from fibokei.db.models import (  # noqa: E402
    BotExecutionTargetModel,
    ExecutionAccountModel,
)


def list_execution_accounts(
    session: Session, *, enabled_only: bool = False
) -> list[ExecutionAccountModel]:
    """Return all execution accounts ordered by creation time."""
    stmt = select(ExecutionAccountModel)
    if enabled_only:
        stmt = stmt.where(ExecutionAccountModel.is_enabled == True)  # noqa: E712
    stmt = stmt.order_by(ExecutionAccountModel.created_at.asc())
    return list(session.scalars(stmt).all())


def get_execution_account(
    session: Session, account_id: int
) -> ExecutionAccountModel | None:
    return session.get(ExecutionAccountModel, account_id)


def get_execution_account_by_name(
    session: Session, name: str
) -> ExecutionAccountModel | None:
    return session.scalar(
        select(ExecutionAccountModel).where(ExecutionAccountModel.name == name)
    )


def get_default_execution_account(
    session: Session,
) -> ExecutionAccountModel | None:
    """Return the default execution account (typically the seeded Paper)."""
    return session.scalar(
        select(ExecutionAccountModel).where(
            ExecutionAccountModel.is_default == True  # noqa: E712
        )
    )


def create_execution_account(
    session: Session, data: dict
) -> ExecutionAccountModel:
    """Create a new execution account.

    ``data`` must include ``name``, ``broker``, ``environment``. Other
    fields fall back to the SQLAlchemy column defaults.
    """
    model = ExecutionAccountModel(**data)
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def update_execution_account(
    session: Session, account_id: int, updates: dict
) -> ExecutionAccountModel | None:
    acct = session.get(ExecutionAccountModel, account_id)
    if acct is None:
        return None
    for field, value in updates.items():
        if field in ("id", "created_at"):
            continue
        if hasattr(acct, field):
            setattr(acct, field, value)
    session.commit()
    session.refresh(acct)
    return acct


# ── Bot Execution Targets (Phase 2) ────────────────────────


def list_bot_execution_targets(
    session: Session,
    *,
    bot_id: str | None = None,
    enabled_only: bool = False,
) -> list[BotExecutionTargetModel]:
    """List execution targets, optionally filtered by bot or enabled state."""
    stmt = select(BotExecutionTargetModel)
    if bot_id is not None:
        stmt = stmt.where(BotExecutionTargetModel.bot_id == bot_id)
    if enabled_only:
        stmt = stmt.where(BotExecutionTargetModel.is_enabled == True)  # noqa: E712
    stmt = stmt.order_by(BotExecutionTargetModel.created_at.asc())
    return list(session.scalars(stmt).all())


def get_bot_execution_target(
    session: Session, target_id: int
) -> BotExecutionTargetModel | None:
    return session.get(BotExecutionTargetModel, target_id)


def create_bot_execution_target(
    session: Session, data: dict
) -> BotExecutionTargetModel:
    """Create a per-bot execution target.

    The (bot_id, execution_account_id) unique constraint will raise on
    duplicate inserts — operators must update an existing row instead.
    """
    model = BotExecutionTargetModel(**data)
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def update_bot_execution_target(
    session: Session, target_id: int, updates: dict
) -> BotExecutionTargetModel | None:
    target = session.get(BotExecutionTargetModel, target_id)
    if target is None:
        return None
    for field, value in updates.items():
        if field in ("id", "bot_id", "execution_account_id", "created_at"):
            continue
        if hasattr(target, field):
            setattr(target, field, value)
    session.commit()
    session.refresh(target)
    return target


def delete_bot_execution_target(session: Session, target_id: int) -> bool:
    target = session.get(BotExecutionTargetModel, target_id)
    if target is None:
        return False
    session.delete(target)
    session.commit()
    return True


def list_targets_with_accounts(
    session: Session, *, bot_id: str
) -> list[tuple[BotExecutionTargetModel, ExecutionAccountModel]]:
    """Return ``(target, account)`` pairs for a bot, enabled-only on both sides.

    Used by the router-factory ``db_targets`` mode to construct
    :class:`ResolvedTarget` objects per signal. Disabled targets and
    disabled accounts are filtered out.
    """
    stmt = (
        select(BotExecutionTargetModel, ExecutionAccountModel)
        .join(
            ExecutionAccountModel,
            BotExecutionTargetModel.execution_account_id == ExecutionAccountModel.id,
        )
        .where(BotExecutionTargetModel.bot_id == bot_id)
        .where(BotExecutionTargetModel.is_enabled == True)  # noqa: E712
        .where(ExecutionAccountModel.is_enabled == True)  # noqa: E712
        .order_by(ExecutionAccountModel.created_at.asc())
    )
    return [(t, a) for t, a in session.execute(stmt).all()]


# ── Bot Signals + Execution Attempts (Phase 3) ─────────────

from fibokei.db.models import (  # noqa: E402
    BotSignalModel,
    ExecutionAttemptModel,
)

# Status vocabulary for ExecutionAttemptModel.status. Constants are exported
# so the router and API layer never use stringly-typed values directly.
ATTEMPT_STATUS_PENDING = "pending"
ATTEMPT_STATUS_SKIPPED = "skipped"
ATTEMPT_STATUS_REJECTED = "rejected"
ATTEMPT_STATUS_SUBMITTED = "submitted"
ATTEMPT_STATUS_FILLED = "filled"
ATTEMPT_STATUS_PARTIALLY_FILLED = "partially_filled"
ATTEMPT_STATUS_CLOSED = "closed"
ATTEMPT_STATUS_FAILED = "failed"

VALID_ATTEMPT_STATUSES = frozenset({
    ATTEMPT_STATUS_PENDING,
    ATTEMPT_STATUS_SKIPPED,
    ATTEMPT_STATUS_REJECTED,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_FILLED,
    ATTEMPT_STATUS_PARTIALLY_FILLED,
    ATTEMPT_STATUS_CLOSED,
    ATTEMPT_STATUS_FAILED,
})


def create_bot_signal(session: Session, data: dict) -> BotSignalModel:
    """Create a parent ``bot_signals`` row for a fan-out dispatch."""
    model = BotSignalModel(**data)
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def get_bot_signal(session: Session, signal_id: int) -> BotSignalModel | None:
    return session.get(BotSignalModel, signal_id)


def list_bot_signals(
    session: Session,
    *,
    bot_id: str | None = None,
    instrument: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[BotSignalModel]:
    """List bot signals, newest first."""
    stmt = select(BotSignalModel)
    if bot_id is not None:
        stmt = stmt.where(BotSignalModel.bot_id == bot_id)
    if instrument is not None:
        stmt = stmt.where(BotSignalModel.instrument == instrument)
    stmt = stmt.order_by(BotSignalModel.signal_timestamp.desc()).offset(offset).limit(limit)
    return list(session.scalars(stmt).all())


def create_execution_attempt(
    session: Session, data: dict
) -> ExecutionAttemptModel:
    """Create a child ``execution_attempts`` row.

    ``data`` must include ``bot_signal_id``, ``broker``, ``environment``,
    ``instrument``, ``status``. Other fields fall back to column defaults.
    """
    model = ExecutionAttemptModel(**data)
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def update_execution_attempt(
    session: Session, attempt_id: int, updates: dict
) -> ExecutionAttemptModel | None:
    attempt = session.get(ExecutionAttemptModel, attempt_id)
    if attempt is None:
        return None
    for field, value in updates.items():
        if field in ("id", "bot_signal_id", "created_at"):
            continue
        if hasattr(attempt, field):
            setattr(attempt, field, value)
    session.commit()
    session.refresh(attempt)
    return attempt


def list_execution_attempts(
    session: Session,
    *,
    bot_signal_id: int | None = None,
    bot_id: str | None = None,
    broker: str | None = None,
    execution_account_id: int | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[ExecutionAttemptModel]:
    """List execution attempts with common filters, newest first."""
    stmt = select(ExecutionAttemptModel)
    if bot_signal_id is not None:
        stmt = stmt.where(ExecutionAttemptModel.bot_signal_id == bot_signal_id)
    if broker is not None:
        stmt = stmt.where(ExecutionAttemptModel.broker == broker)
    if execution_account_id is not None:
        stmt = stmt.where(
            ExecutionAttemptModel.execution_account_id == execution_account_id
        )
    if status is not None:
        stmt = stmt.where(ExecutionAttemptModel.status == status)
    if bot_id is not None:
        stmt = stmt.join(
            BotSignalModel, ExecutionAttemptModel.bot_signal_id == BotSignalModel.id
        ).where(BotSignalModel.bot_id == bot_id)
    stmt = stmt.order_by(ExecutionAttemptModel.created_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def derive_parent_signal_status(attempts: list[ExecutionAttemptModel]) -> str:
    """Roll attempt statuses up to a single parent-signal status label.

    Vocabulary returned:
      - ``empty`` — no attempts at all
      - ``all_filled`` — every attempt filled or closed
      - ``all_skipped`` — every attempt skipped (kill switch / disabled / unsupported)
      - ``all_rejected`` — every attempt rejected by gate or broker
      - ``failed`` — every attempt failed (adapter exception etc.)
      - ``partial_success`` — at least one filled and at least one not-filled
      - ``mixed`` — anything else (e.g. mix of skipped + rejected)
    """
    if not attempts:
        return "empty"
    statuses = [a.status for a in attempts]
    distinct = set(statuses)
    filled_set = {ATTEMPT_STATUS_FILLED, ATTEMPT_STATUS_CLOSED, ATTEMPT_STATUS_PARTIALLY_FILLED}
    if distinct <= filled_set:
        return "all_filled"
    if distinct == {ATTEMPT_STATUS_SKIPPED}:
        return "all_skipped"
    if distinct == {ATTEMPT_STATUS_REJECTED}:
        return "all_rejected"
    if distinct == {ATTEMPT_STATUS_FAILED}:
        return "failed"
    has_filled = any(s in filled_set for s in statuses)
    has_not_filled = any(s not in filled_set for s in statuses)
    if has_filled and has_not_filled:
        return "partial_success"
    return "mixed"


# ── Strategy Variants ──────────────────────────────────────

from fibokei.db.models import StrategyVariantModel  # noqa: E402


def create_variant(
    session: Session,
    strategy_id: str,
    name: str,
    params: dict,
    backtest_run_id: int | None = None,
    trade_overlap: float | None = None,
) -> StrategyVariantModel:
    """Create a new strategy variant."""
    variant = StrategyVariantModel(
        strategy_id=strategy_id,
        name=name,
        params=params,
        backtest_run_id=backtest_run_id,
        trade_overlap=trade_overlap,
    )
    session.add(variant)
    session.commit()
    session.refresh(variant)
    return variant


def list_variants(
    session: Session,
    strategy_id: str | None = None,
    active_only: bool = False,
) -> list[StrategyVariantModel]:
    """List strategy variants, optionally filtered by strategy_id."""
    stmt = select(StrategyVariantModel)
    if strategy_id:
        stmt = stmt.where(StrategyVariantModel.strategy_id == strategy_id)
    if active_only:
        stmt = stmt.where(StrategyVariantModel.is_active == True)  # noqa: E712
    stmt = stmt.order_by(StrategyVariantModel.created_at.desc())
    return list(session.scalars(stmt).all())


def get_variant(session: Session, variant_id: int) -> StrategyVariantModel | None:
    """Get a variant by ID."""
    return session.get(StrategyVariantModel, variant_id)


def update_variant(
    session: Session,
    variant_id: int,
    updates: dict,
) -> StrategyVariantModel | None:
    """Update a variant's fields."""
    variant = session.get(StrategyVariantModel, variant_id)
    if not variant:
        return None
    for key, value in updates.items():
        if hasattr(variant, key):
            setattr(variant, key, value)
    session.commit()
    session.refresh(variant)
    return variant


def delete_variant(session: Session, variant_id: int) -> bool:
    """Delete a variant by ID."""
    variant = session.get(StrategyVariantModel, variant_id)
    if not variant:
        return False
    session.delete(variant)
    session.commit()
    return True


# ── Evaluation Phases ─────────────────────────────────────────


def get_active_phase(session: Session) -> EvaluationPhaseModel | None:
    """Return the current active evaluation phase, or None if none exists."""
    return session.scalars(
        select(EvaluationPhaseModel).where(EvaluationPhaseModel.is_active == True)  # noqa: E712
    ).first()


def get_phase(session: Session, phase_id: int) -> EvaluationPhaseModel | None:
    """Return a phase by ID."""
    return session.get(EvaluationPhaseModel, phase_id)


def list_phases(session: Session) -> list[EvaluationPhaseModel]:
    """List all evaluation phases, newest first."""
    return list(
        session.scalars(
            select(EvaluationPhaseModel).order_by(EvaluationPhaseModel.started_at.desc())
        ).all()
    )


def archive_current_phase(
    session: Session,
    phase_name: str = "Phase A — Initial Testing",
    phase_label: str = "phase_a",
    description: str | None = None,
    final_balance: float | None = None,
    initial_balance: float = 1000.0,
    started_at: "datetime | None" = None,
) -> EvaluationPhaseModel:
    """Archive all existing bots and trades into a named phase record.

    This is idempotent — if an active phase already exists, bots/trades
    without a phase_id are assigned to it. If no active phase exists, one
    is created first.

    After archiving:
      - All existing bots are assigned to the phase and marked archived_at=now
      - All existing trades are assigned to the phase
      - phase.is_active = False, phase.archived_at = now
      - phase.total_trades and phase.net_pnl are computed from trades
    """
    from datetime import datetime, timezone

    from sqlalchemy import func as _func

    now = datetime.now(timezone.utc)

    # Find or create an active phase to receive the archived bots/trades
    phase = get_active_phase(session)
    if not phase:
        phase_started = started_at or now
        phase = EvaluationPhaseModel(
            name=phase_name,
            phase_label=phase_label,
            is_active=True,
            started_at=phase_started,
            initial_balance=initial_balance,
            normalized_baseline=initial_balance,
            currency="GBP",
            description=description,
        )
        session.add(phase)
        session.flush()  # get phase.id

    # Record which bots were running during this phase (for reporting).
    # Bots are NOT stopped and NOT marked archived — they are cross-phase entities
    # that continue running into the next phase.  Only their phase_id is set so
    # the phase record knows which bots existed during the period.
    unassigned_bots = list(
        session.scalars(
            select(PaperBotModel).where(PaperBotModel.phase_id.is_(None))
        ).all()
    )
    for bot in unassigned_bots:
        bot.phase_id = phase.id
        # Do NOT set archived_at — bots are not archived, only their trades are

    # Assign all unassigned trades to this phase
    # Use bulk update for efficiency
    from sqlalchemy import update as _update
    session.execute(
        _update(PaperTradeModel)
        .where(PaperTradeModel.phase_id.is_(None))
        .values(phase_id=phase.id)
    )

    # Compute totals from trades assigned to this phase
    totals = session.execute(
        select(
            _func.count(PaperTradeModel.id).label("total"),
            _func.coalesce(_func.sum(PaperTradeModel.pnl), 0.0).label("net_pnl"),
        ).where(PaperTradeModel.phase_id == phase.id)
    ).one()
    phase.total_trades = int(totals.total)
    phase.net_pnl = float(totals.net_pnl)

    if final_balance is not None:
        phase.final_balance = final_balance
    elif phase.total_trades > 0:
        phase.final_balance = phase.initial_balance + float(totals.net_pnl)

    phase.is_active = False
    phase.archived_at = now
    if description:
        phase.description = description

    session.commit()
    return phase


def create_new_phase(
    session: Session,
    name: str,
    phase_label: str,
    initial_balance: float = 1000.0,
    normalized_baseline: float = 1000.0,
    broker_balance_at_start: float | None = None,
    description: str | None = None,
) -> EvaluationPhaseModel:
    """Create a new active evaluation phase.

    Any existing active phase must have been archived first — this function
    will raise if an active phase already exists.
    """
    existing = get_active_phase(session)
    if existing:
        raise ValueError(
            f"Active phase '{existing.phase_label}' already exists (id={existing.id}). "
            "Archive it first via archive_current_phase()."
        )

    from datetime import datetime, timezone

    phase = EvaluationPhaseModel(
        name=name,
        phase_label=phase_label,
        is_active=True,
        started_at=datetime.now(timezone.utc),
        initial_balance=initial_balance,
        normalized_baseline=normalized_baseline,
        broker_balance_at_start=broker_balance_at_start,
        currency="GBP",
        description=description,
    )
    session.add(phase)
    session.commit()
    return phase


def transition_to_new_phase(
    session: Session,
    new_phase_name: str,
    new_phase_label: str,
    archive_name: str = "Phase A — Initial Testing",
    archive_label: str = "phase_a",
    archive_description: str | None = None,
    archive_final_balance: float | None = None,
    archive_initial_balance: float = 1000.0,
    new_initial_balance: float = 1000.0,
    new_normalized_baseline: float = 1000.0,
    new_broker_balance: float | None = None,
    new_description: str | None = None,
) -> tuple[EvaluationPhaseModel, EvaluationPhaseModel]:
    """Archive the current phase and create a new one atomically.

    Returns (archived_phase, new_phase).
    All existing bots and trades are assigned to the archived phase.
    """
    archived = archive_current_phase(
        session,
        phase_name=archive_name,
        phase_label=archive_label,
        description=archive_description,
        final_balance=archive_final_balance,
        initial_balance=archive_initial_balance,
    )
    new_phase = create_new_phase(
        session,
        name=new_phase_name,
        phase_label=new_phase_label,
        initial_balance=new_initial_balance,
        normalized_baseline=new_normalized_baseline,
        broker_balance_at_start=new_broker_balance,
        description=new_description,
    )
    return archived, new_phase


def beat_worker_heartbeat(
    session: Session,
    worker_id: str,
    hostname: str = "",
    poll_interval_s: int = 60,
    bots_active: int = 0,
    loops_completed: int = 0,
    last_error: str | None = None,
) -> None:
    """Upsert the heartbeat row for ``worker_id`` (called every poll loop)."""
    from datetime import datetime, timezone

    from fibokei.db.models import WorkerHeartbeatModel

    row = session.scalars(
        select(WorkerHeartbeatModel).where(WorkerHeartbeatModel.worker_id == worker_id)
    ).first()
    now = datetime.now(timezone.utc)
    if row is None:
        row = WorkerHeartbeatModel(
            worker_id=worker_id, hostname=hostname, started_at=now,
            poll_interval_s=poll_interval_s,
        )
        session.add(row)
    row.last_beat_at = now
    row.poll_interval_s = poll_interval_s
    row.bots_active = bots_active
    row.loops_completed = loops_completed
    row.last_error = last_error
    session.commit()


def get_worker_heartbeats(session: Session) -> list:
    """All worker heartbeat rows, freshest first."""
    from fibokei.db.models import WorkerHeartbeatModel

    return list(
        session.scalars(
            select(WorkerHeartbeatModel).order_by(WorkerHeartbeatModel.last_beat_at.desc())
        )
    )


# ── Wave 1: broker trade ledger ─────────────────────────────────────────────


def upsert_broker_trade(session: Session, data: dict) -> tuple:
    """Insert or update a broker trade, keyed on (source, reference).

    Returns (row, created: bool). Idempotent — re-importing the same IG
    transaction updates the existing row instead of duplicating it.
    """
    from fibokei.db.models import BrokerTradeModel

    existing = session.scalars(
        select(BrokerTradeModel).where(
            BrokerTradeModel.source == data["source"],
            BrokerTradeModel.reference == data["reference"],
        )
    ).first()

    mutable = (
        "deal_id", "broker_transaction_id", "instrument_name", "instrument",
        "epic", "direction", "size", "open_level", "close_level", "pnl",
        "currency", "transaction_type", "opened_at", "closed_at",
        "bot_id", "strategy_id", "research_run_id", "raw_json",
    )
    if existing is not None:
        for k in mutable:
            if k in data and data[k] is not None:
                setattr(existing, k, data[k])
        session.commit()
        return existing, False

    row = BrokerTradeModel(**{k: v for k, v in data.items()})
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, True


def list_broker_trades(
    session: Session,
    source: str | None = None,
    bot_id: str | None = None,
    strategy_id: str | None = None,
    instrument: str | None = None,
    limit: int = 200,
) -> list:
    """List broker trades, newest first, with optional filters."""
    from fibokei.db.models import BrokerTradeModel

    stmt = select(BrokerTradeModel)
    if source:
        stmt = stmt.where(BrokerTradeModel.source == source)
    if bot_id:
        stmt = stmt.where(BrokerTradeModel.bot_id == bot_id)
    if strategy_id:
        stmt = stmt.where(BrokerTradeModel.strategy_id == strategy_id)
    if instrument:
        stmt = stmt.where(BrokerTradeModel.instrument == instrument)
    stmt = stmt.order_by(BrokerTradeModel.closed_at.desc().nullslast()).limit(limit)
    return list(session.scalars(stmt))


# ── Bulk bot reset (clean-slate for a new phase) ────────────────────────────


def reset_all_bots(session: Session) -> int:
    """Reset every bot to a clean monitoring state: no open position, fresh
    warmup, no error. Used when starting a clean phase so the fleet lines up
    with a freshly reset paper account (and an empty broker). Returns count.
    """
    from fibokei.db.models import PaperBotModel

    bots = list(session.scalars(select(PaperBotModel)))
    for bot in bots:
        bot.state = "monitoring"
        bot.position_json = None
        bot.last_evaluated_bar = None
        bot.bars_seen = 0
        bot.error_message = ""
    session.commit()
    return len(bots)
