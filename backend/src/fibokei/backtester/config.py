"""Backtest configuration model."""

from pydantic import BaseModel


class BacktestConfig(BaseModel):
    """Configuration for a backtest run."""

    initial_capital: float = 10000.0
    risk_per_trade_pct: float = 1.0
    spread_points: float = 0.0
    slippage_points: float = 0.0
    commission_per_trade: float = 0.0
    allow_long: bool = True
    allow_short: bool = True
    max_open_trades: int = 1
    max_bars_in_trade: int = 100
