"""Pydantic schemas for backtest API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class BacktestRunRequest(BaseModel):
    strategy_id: str
    instrument: str
    timeframe: str
    config_overrides: dict[str, Any] | None = None


class BacktestSummaryResponse(BaseModel):
    id: int
    strategy_id: str
    instrument: str
    timeframe: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    total_trades: int
    net_profit: float
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    created_at: datetime | None = None


class BacktestDetailResponse(BacktestSummaryResponse):
    config_json: dict[str, Any] | None = None
    metrics_json: dict[str, Any] | None = None


class TradeResponse(BaseModel):
    id: int
    strategy_id: str
    instrument: str
    direction: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    pnl: float
    bars_in_trade: int


class TradeListResponse(BaseModel):
    items: list[TradeResponse]
    total: int
    page: int
    size: int


class EquityCurveResponse(BaseModel):
    equity_curve: list[float]
