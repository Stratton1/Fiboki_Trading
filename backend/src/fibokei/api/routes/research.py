"""Research matrix API endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.api.schemas.research import (
    ResearchCompareRequest,
    ResearchResultResponse,
    ResearchRunRequest,
    ResearchRunSummary,
)
from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.loader import load_ohlcv_csv
from fibokei.db.models import ResearchResultModel
from fibokei.db.repository import get_research_rankings, save_research_results
from fibokei.research.scorer import compute_composite_score
from fibokei.strategies.registry import strategy_registry

router = APIRouter(tags=["research"])


def _resolve_data_dir(data_dir: str | None) -> str:
    """Resolve data directory, defaulting to project fixtures."""
    if data_dir:
        return data_dir
    from pathlib import Path

    return str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "fixtures")


@router.post("/research/run", response_model=ResearchRunSummary)
def run_research(
    req: ResearchRunRequest,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Run research matrix across strategy x instrument x timeframe combinations."""
    run_id = str(uuid.uuid4())[:8]
    data_dir = _resolve_data_dir(req.data_dir)
    config = BacktestConfig(
        initial_capital=req.initial_capital,
        risk_per_trade_pct=req.risk_per_trade_pct,
    )

    results = []
    for sid in req.strategy_ids:
        try:
            strategy = strategy_registry.get(sid)
        except KeyError:
            continue

        for inst in req.instruments:
            for tf_str in req.timeframes:
                try:
                    tf_enum = Timeframe(tf_str.upper())
                except ValueError:
                    continue

                # Try to load data
                from pathlib import Path

                data_path = Path(data_dir) / f"sample_{inst.lower()}_{tf_str.lower()}.csv"
                if not data_path.exists():
                    continue

                try:
                    df = load_ohlcv_csv(str(data_path), inst, tf_enum)
                except Exception:
                    continue

                backtester = Backtester(strategy, config)
                result = backtester.run(df, inst, tf_enum)
                metrics = compute_metrics(result)
                metrics["equity_curve"] = result.equity_curve
                score = compute_composite_score(metrics)

                results.append({
                    "run_id": run_id,
                    "strategy_id": sid,
                    "instrument": inst,
                    "timeframe": tf_str.upper(),
                    "composite_score": score,
                    "metrics": metrics,
                })

    # Rank by composite score
    results.sort(key=lambda r: r["composite_score"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # Persist
    saved = save_research_results(db, results) if results else []

    top = None
    if saved:
        s = saved[0]
        top = ResearchResultResponse(
            id=s.id,
            run_id=s.run_id,
            strategy_id=s.strategy_id,
            instrument=s.instrument,
            timeframe=s.timeframe,
            composite_score=s.composite_score,
            rank=s.rank,
            metrics_json=s.metrics_json,
            created_at=s.created_at,
        )

    return ResearchRunSummary(
        run_id=run_id,
        total_combinations=len(req.strategy_ids) * len(req.instruments) * len(req.timeframes),
        completed=len(results),
        top_result=top,
    )


@router.get("/research/rankings", response_model=list[ResearchResultResponse])
def get_rankings(
    sort_by: str = Query("composite_score", pattern="^(composite_score|rank)$"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Get ranked research results."""
    results = get_research_rankings(db, sort_by=sort_by, limit=limit)
    return [
        {
            "id": r.id,
            "run_id": r.run_id,
            "strategy_id": r.strategy_id,
            "instrument": r.instrument,
            "timeframe": r.timeframe,
            "composite_score": r.composite_score,
            "rank": r.rank,
            "metrics_json": r.metrics_json,
            "created_at": r.created_at,
        }
        for r in results
    ]


@router.post("/research/compare", response_model=list[ResearchResultResponse])
def compare_combinations(
    req: ResearchCompareRequest,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Compare specific strategy-instrument-timeframe combinations."""
    from sqlalchemy import select

    results = []
    for combo in req.combos:
        parts = combo.split(":")
        if len(parts) != 3:
            continue
        sid, inst, tf = parts

        stmt = (
            select(ResearchResultModel)
            .where(ResearchResultModel.strategy_id == sid)
            .where(ResearchResultModel.instrument == inst)
            .where(ResearchResultModel.timeframe == tf.upper())
            .order_by(ResearchResultModel.created_at.desc())
            .limit(1)
        )
        row = db.scalar(stmt)
        if row:
            results.append({
                "id": row.id,
                "run_id": row.run_id,
                "strategy_id": row.strategy_id,
                "instrument": row.instrument,
                "timeframe": row.timeframe,
                "composite_score": row.composite_score,
                "rank": row.rank,
                "metrics_json": row.metrics_json,
                "created_at": row.created_at,
            })

    if not results:
        raise HTTPException(status_code=404, detail="No matching research results found")

    return results
