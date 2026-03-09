"""Pydantic schemas for research API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ResearchRunRequest(BaseModel):
    strategy_ids: list[str] = Field(..., min_length=1)
    instruments: list[str] = Field(..., min_length=1)
    timeframes: list[str] = Field(..., min_length=1)
    data_dir: str | None = None
    provider: str | None = Field(None, description="Data provider: histdata, dukascopy, or None for auto")
    initial_capital: float = 10000.0
    risk_per_trade_pct: float = 1.0


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
    top_result: ResearchResultResponse | None = None


class ResearchCompareRequest(BaseModel):
    combos: list[str] = Field(
        ...,
        min_length=1,
        description="List of 'strategy_id:instrument:timeframe' strings",
    )
