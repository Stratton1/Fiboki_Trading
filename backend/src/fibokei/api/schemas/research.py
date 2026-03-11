"""Pydantic schemas for research API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ScoringWeights(BaseModel):
    """User-configurable composite score weights."""

    weight_risk_adjusted: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_profit_factor: float = Field(default=0.20, ge=0.0, le=1.0)
    weight_return: float = Field(default=0.20, ge=0.0, le=1.0)
    weight_drawdown: float = Field(default=0.15, ge=0.0, le=1.0)
    weight_sample: float = Field(default=0.10, ge=0.0, le=1.0)
    weight_stability: float = Field(default=0.10, ge=0.0, le=1.0)


class ResearchRunRequest(BaseModel):
    strategy_ids: list[str] = Field(..., min_length=1)
    instruments: list[str] = Field(..., min_length=1)
    timeframes: list[str] = Field(..., min_length=1)
    data_dir: str | None = None
    provider: str | None = Field(None, description="Data provider: histdata, dukascopy, or None for auto")
    initial_capital: float = 10000.0
    risk_per_trade_pct: float = 1.0
    min_trades: int = Field(default=80, ge=1, le=1000, description="Minimum trades for qualification")
    scoring_weights: ScoringWeights | None = Field(None, description="Custom scoring weights")


class AdvancedResearchRequest(BaseModel):
    """Request for advanced research analysis (walk-forward, OOS, Monte Carlo)."""

    strategy_id: str
    instrument: str
    timeframe: str
    provider: str | None = None
    initial_capital: float = 10000.0
    risk_per_trade_pct: float = 1.0
    scoring_weights: ScoringWeights | None = None
    # Walk-forward params
    wf_train_bars: int = Field(default=2000, ge=200)
    wf_test_bars: int = Field(default=500, ge=50)
    wf_step_bars: int = Field(default=500, ge=50)
    # OOS params
    oos_split_ratio: float = Field(default=0.7, ge=0.3, le=0.9)
    # Monte Carlo params
    mc_simulations: int = Field(default=1000, ge=100, le=10000)
    mc_seed: int | None = 42


class ResearchResultResponse(BaseModel):
    id: int
    run_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    composite_score: float
    rank: int
    metrics_json: dict[str, Any] | None = None
    created_at: datetime | None = None


class ResearchRunSummary(BaseModel):
    run_id: str
    total_combinations: int
    completed: int
    qualified: int = 0
    min_trades: int = 80
    scoring_weights: ScoringWeights | None = None
    top_result: ResearchResultResponse | None = None


class ResearchCompareRequest(BaseModel):
    combos: list[str] = Field(
        ...,
        min_length=1,
        description="List of 'strategy_id:instrument:timeframe' strings",
    )


# Advanced analysis response schemas

class WalkForwardWindowResponse(BaseModel):
    window_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_bars: int
    test_bars: int
    train_trades: int
    test_trades: int
    train_score: float
    test_score: float
    train_net_profit: float
    test_net_profit: float


class WalkForwardResponse(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    total_windows: int
    avg_test_score: float
    avg_test_sharpe: float
    total_test_trades: int
    score_degradation: float
    windows: list[WalkForwardWindowResponse]
    status: str


class OOSResponse(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    split_ratio: float
    in_sample_bars: int
    out_of_sample_bars: int
    is_trades: int
    is_score: float
    is_sharpe: float
    is_net_profit: float
    oos_trades: int
    oos_score: float
    oos_sharpe: float
    oos_net_profit: float
    score_degradation: float
    robust: bool
    status: str


class MonteCarloResponse(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    num_simulations: int
    num_trades: int
    original_net_profit: float
    mean_net_profit: float
    median_net_profit: float
    p5_net_profit: float
    p95_net_profit: float
    mean_max_drawdown: float
    p95_max_drawdown: float
    profit_probability: float
    ruin_probability: float
    robust: bool
    status: str


class SensitivityPointResponse(BaseModel):
    param_value: float
    total_trades: int
    net_profit: float
    sharpe_ratio: float
    composite_score: float


class SensitivityResponse(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    param_name: str
    baseline_value: float
    score_range: float
    score_std: float
    robust: bool
    variations: list[SensitivityPointResponse]
    status: str


class AdvancedResearchResponse(BaseModel):
    """Combined response for all advanced research analyses."""

    walk_forward: WalkForwardResponse | None = None
    oos: OOSResponse | None = None
    monte_carlo: MonteCarloResponse | None = None
    sensitivity: list[SensitivityResponse] | None = None


class ValidationItemRequest(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    original_score: float = 0.0
    original_trades: int = 0
    original_net_profit: float = 0.0
    original_sharpe: float = 0.0


class ValidationRunRequest(BaseModel):
    shortlist: list[ValidationItemRequest] = Field(..., min_length=1)
    validation_provider: str | None = None
    initial_capital: float = 10000.0


class ValidationResultResponse(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    original_score: float
    validation_score: float
    score_divergence: float
    passed: bool
    validation_status: str
    validation_provider: str | None = None


class ValidationBatchResponse(BaseModel):
    total_validated: int
    total_passed: int
    total_failed: int
    total_skipped: int
    pass_rate: float
    results: list[ValidationResultResponse]


# Research preset schemas


class ResearchPresetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    config: ResearchRunRequest


class ResearchPresetUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    config: ResearchRunRequest | None = None


class ResearchPresetResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    config: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None
