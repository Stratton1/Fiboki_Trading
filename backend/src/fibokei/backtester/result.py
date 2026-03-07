"""Backtest result model."""

from datetime import datetime

from pydantic import BaseModel

from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.core.trades import TradeResult


class BacktestResult(BaseModel):
    """Result of a completed backtest run."""

    strategy_id: str
    instrument: str
    timeframe: Timeframe
    config: BacktestConfig
    trades: list[TradeResult]
    equity_curve: list[float]
    start_date: datetime
    end_date: datetime
    total_bars: int
