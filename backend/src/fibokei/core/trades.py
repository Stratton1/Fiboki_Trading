"""Trade planning and result models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from fibokei.core.models import Direction, Timeframe


class ExitReason(str, Enum):
    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"
    PARTIAL_TAKE_PROFIT = "partial_take_profit"
    TRAILING_STOP_HIT = "trailing_stop_hit"
    BREAK_EVEN_EXIT = "break_even_exit"
    OPPOSITE_SIGNAL_EXIT = "opposite_signal_exit"
    INDICATOR_INVALIDATION_EXIT = "indicator_invalidation_exit"
    TIME_STOP_EXIT = "time_stop_exit"
    MANUAL_STOP = "manual_stop"
    SYSTEM_SHUTDOWN_EXIT = "system_shutdown_exit"


class TradePlan(BaseModel):
    """Plan for executing a trade."""

    entry_price: float
    stop_loss: float
    take_profit_targets: list[float]
    trailing_stop_rule: str | None = None
    break_even_rule: str | None = None
    max_risk_amount: float = 0.0
    risk_pct: float = 1.0
    position_size: float = 0.0
    stale_after_bars: int | None = None
    max_bars_in_trade: int | None = 50
    partial_close_pcts: list[float] | None = None
    allowed_exit_reasons: list[str] = Field(
        default_factory=lambda: [r.value for r in ExitReason]
    )


class TradeResult(BaseModel):
    """Result of a completed trade."""

    trade_id: str
    strategy_id: str
    instrument: str
    timeframe: Timeframe
    direction: Direction
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: ExitReason
    pnl: float
    pnl_pct: float
    position_size: float
    bars_in_trade: int
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
