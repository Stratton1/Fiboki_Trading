"""Research matrix API endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from fibokei.api.auth import TokenData, get_current_user
from fibokei.api.deps import get_db
from fibokei.api.schemas.research import (
    AdvancedResearchRequest,
    AdvancedResearchResponse,
    MonteCarloResponse,
    OOSResponse,
    ResearchCompareRequest,
    ResearchResultResponse,
    ResearchRunRequest,
    ResearchRunSummary,
    ScoringWeights,
    SensitivityPointResponse,
    SensitivityResponse,
    ValidationBatchResponse,
    ValidationResultResponse,
    ValidationRunRequest,
    WalkForwardResponse,
    WalkForwardWindowResponse,
)
from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.db.models import ResearchResultModel
from fibokei.db.repository import get_research_rankings, save_research_results
from fibokei.research.matrix import ResearchMatrix
from fibokei.research.scorer import ScoringConfig

router = APIRouter(tags=["research"])


def _scoring_config_from_weights(weights: ScoringWeights | None) -> ScoringConfig:
    """Convert API scoring weights to ScoringConfig."""
    if weights is None:
        return ScoringConfig()
    return ScoringConfig(
        weight_risk_adjusted=weights.weight_risk_adjusted,
        weight_profit_factor=weights.weight_profit_factor,
        weight_return=weights.weight_return,
        weight_drawdown=weights.weight_drawdown,
        weight_sample=weights.weight_sample,
        weight_stability=weights.weight_stability,
    )


@router.post("/research/run", response_model=ResearchRunSummary)
def run_research(
    req: ResearchRunRequest,
    db: Session = Depends(get_db),
    user: TokenData = Depends(get_current_user),
):
    """Run research matrix across strategy x instrument x timeframe combinations."""
    run_id = str(uuid.uuid4())[:8]
    scoring_config = _scoring_config_from_weights(req.scoring_weights)
    scoring_config.min_trades_full = req.min_trades

    config = BacktestConfig(
        initial_capital=req.initial_capital,
        risk_per_trade_pct=req.risk_per_trade_pct,
    )

    timeframes = []
    for tf_str in req.timeframes:
        try:
            timeframes.append(Timeframe(tf_str.upper()))
        except ValueError:
            continue

    matrix = ResearchMatrix(
        strategies=req.strategy_ids,
        instruments=req.instruments,
        timeframes=timeframes,
        config=config,
        scoring_config=scoring_config,
        provider=req.provider,
    )

    from pathlib import Path
    data_dir = req.data_dir or str(
        Path(__file__).resolve().parent.parent.parent.parent / "data" / "fixtures"
    )
    results = matrix.run(data_dir)

    # Apply min trades filter
    from fibokei.research.filter import apply_minimum_trade_filter
    qualified, _ = apply_minimum_trade_filter(results, req.min_trades)

    # Persist all results
    result_dicts = []
    for r in results:
        result_dicts.append({
            "run_id": run_id,
            "strategy_id": r.strategy_id,
            "instrument": r.instrument,
            "timeframe": r.timeframe,
            "composite_score": r.composite_score,
            "rank": r.rank,
            "metrics": r.metrics,
        })

    saved = save_research_results(db, result_dicts) if result_dicts else []

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
        qualified=len(qualified),
        min_trades=req.min_trades,
        scoring_weights=req.scoring_weights,
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


@router.post("/research/advanced", response_model=AdvancedResearchResponse)
def run_advanced_research(
    req: AdvancedResearchRequest,
    user: TokenData = Depends(get_current_user),
):
    """Run advanced research analysis: walk-forward, OOS, Monte Carlo, sensitivity."""
    scoring_config = _scoring_config_from_weights(req.scoring_weights)
    bt_config = BacktestConfig(
        initial_capital=req.initial_capital,
        risk_per_trade_pct=req.risk_per_trade_pct,
    )

    try:
        tf_enum = Timeframe(req.timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {req.timeframe}")

    # Load data
    df = load_canonical(req.instrument, req.timeframe.upper(), provider=req.provider)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {req.instrument} {req.timeframe}")

    df["instrument"] = req.instrument
    df["timeframe"] = req.timeframe.upper()

    response = AdvancedResearchResponse()

    # Walk-forward
    from fibokei.research.walk_forward import run_walk_forward
    wf = run_walk_forward(
        df, req.strategy_id, req.instrument, tf_enum,
        train_window_bars=req.wf_train_bars,
        test_window_bars=req.wf_test_bars,
        step_bars=req.wf_step_bars,
        config=bt_config,
        scoring_config=scoring_config,
    )
    response.walk_forward = WalkForwardResponse(
        strategy_id=wf.strategy_id,
        instrument=wf.instrument,
        timeframe=wf.timeframe,
        total_windows=wf.total_windows,
        avg_test_score=round(wf.avg_test_score, 4),
        avg_test_sharpe=round(wf.avg_test_sharpe, 4),
        total_test_trades=wf.total_test_trades,
        score_degradation=wf.score_degradation,
        windows=[
            WalkForwardWindowResponse(
                window_index=w.window_index,
                train_start=w.train_start,
                train_end=w.train_end,
                test_start=w.test_start,
                test_end=w.test_end,
                train_bars=w.train_bars,
                test_bars=w.test_bars,
                train_trades=w.train_trades,
                test_trades=w.test_trades,
                train_score=round(w.train_score, 4),
                test_score=round(w.test_score, 4),
                train_net_profit=round(w.train_net_profit, 2),
                test_net_profit=round(w.test_net_profit, 2),
            )
            for w in wf.windows
        ],
        status=wf.status,
    )

    # Out-of-sample
    from fibokei.research.oos import run_oos_test
    oos = run_oos_test(
        df, req.strategy_id, req.instrument, tf_enum,
        split_ratio=req.oos_split_ratio,
        config=bt_config,
        scoring_config=scoring_config,
    )
    response.oos = OOSResponse(
        strategy_id=oos.strategy_id,
        instrument=oos.instrument,
        timeframe=oos.timeframe,
        split_ratio=oos.split_ratio,
        in_sample_bars=oos.in_sample_bars,
        out_of_sample_bars=oos.out_of_sample_bars,
        is_trades=oos.is_trades,
        is_score=round(oos.is_score, 4),
        is_sharpe=round(oos.is_sharpe, 4),
        is_net_profit=round(oos.is_net_profit, 2),
        oos_trades=oos.oos_trades,
        oos_score=round(oos.oos_score, 4),
        oos_sharpe=round(oos.oos_sharpe, 4),
        oos_net_profit=round(oos.oos_net_profit, 2),
        score_degradation=oos.score_degradation,
        robust=oos.robust,
        status=oos.status,
    )

    # Monte Carlo — needs trade PnLs
    from fibokei.backtester.engine import Backtester
    from fibokei.research.monte_carlo import run_monte_carlo
    from fibokei.strategies.registry import strategy_registry

    strategy = strategy_registry.get(req.strategy_id)
    bt = Backtester(strategy, bt_config)
    bt_result = bt.run(df, req.instrument, tf_enum)

    trade_pnls = [t.pnl for t in bt_result.trades if hasattr(t, "pnl")]
    if not trade_pnls:
        trade_pnls = [
            (t.exit_price - t.entry_price) * (1 if t.direction.value == "LONG" else -1)
            for t in bt_result.trades
            if t.exit_price is not None
        ]

    mc = run_monte_carlo(
        trade_pnls, req.strategy_id, req.instrument, req.timeframe.upper(),
        initial_capital=req.initial_capital,
        num_simulations=req.mc_simulations,
        seed=req.mc_seed,
    )
    response.monte_carlo = MonteCarloResponse(
        strategy_id=mc.strategy_id,
        instrument=mc.instrument,
        timeframe=mc.timeframe,
        num_simulations=mc.num_simulations,
        num_trades=mc.num_trades,
        original_net_profit=mc.original_net_profit,
        mean_net_profit=mc.mean_net_profit,
        median_net_profit=mc.median_net_profit,
        p5_net_profit=mc.p5_net_profit,
        p95_net_profit=mc.p95_net_profit,
        mean_max_drawdown=mc.mean_max_drawdown,
        p95_max_drawdown=mc.p95_max_drawdown,
        profit_probability=mc.profit_probability,
        ruin_probability=mc.ruin_probability,
        robust=mc.robust,
        status=mc.status,
    )

    # Sensitivity — run for default params of this strategy family
    from fibokei.research.sensitivity import get_default_params, run_sensitivity

    default_params = get_default_params(req.strategy_id)
    sensitivity_results = []
    for param_name, param_values in default_params.items():
        sens = run_sensitivity(
            df, req.strategy_id, req.instrument, tf_enum,
            param_name=param_name,
            param_values=param_values,
            config=bt_config,
            scoring_config=scoring_config,
        )
        sensitivity_results.append(SensitivityResponse(
            strategy_id=sens.strategy_id,
            instrument=sens.instrument,
            timeframe=sens.timeframe,
            param_name=sens.param_name,
            baseline_value=sens.baseline_value,
            score_range=sens.score_range,
            score_std=sens.score_std,
            robust=sens.robust,
            variations=[
                SensitivityPointResponse(
                    param_value=v.param_value,
                    total_trades=v.total_trades,
                    net_profit=round(v.net_profit, 2),
                    sharpe_ratio=round(v.sharpe_ratio, 4),
                    composite_score=round(v.composite_score, 4),
                )
                for v in sens.variations
            ],
            status=sens.status,
        ))

    response.sensitivity = sensitivity_results if sensitivity_results else None

    return response


@router.post("/research/validate", response_model=ValidationBatchResponse)
def run_validation(
    req: ValidationRunRequest,
    user: TokenData = Depends(get_current_user),
):
    """Validate shortlisted strategy-instrument-timeframe combinations."""
    from fibokei.research.validation import run_validation_rerun

    bt_config = BacktestConfig(initial_capital=req.initial_capital)
    shortlist = [item.model_dump() for item in req.shortlist]

    batch = run_validation_rerun(
        shortlist,
        config=bt_config,
        validation_provider=req.validation_provider,
    )

    return ValidationBatchResponse(
        total_validated=batch.total_validated,
        total_passed=batch.total_passed,
        total_failed=batch.total_failed,
        total_skipped=batch.total_skipped,
        pass_rate=batch.pass_rate,
        results=[
            ValidationResultResponse(
                strategy_id=r.strategy_id,
                instrument=r.instrument,
                timeframe=r.timeframe,
                original_score=r.original_score,
                validation_score=r.validation_score,
                score_divergence=r.score_divergence,
                passed=r.passed,
                validation_status=r.validation_status,
                validation_provider=r.validation_provider,
            )
            for r in batch.results
        ],
    )
