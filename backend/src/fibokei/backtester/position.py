"""Position tracking for backtester."""

import uuid
from datetime import datetime

import pandas as pd

from fibokei.core.models import Direction, Timeframe
from fibokei.core.trades import ExitReason, TradeResult


class Position:
    """Tracks an open position during backtesting."""

    def __init__(
        self,
        strategy_id: str,
        instrument: str,
        timeframe: Timeframe,
        direction: Direction,
        entry_time: datetime,
        entry_price: float,
        stop_loss: float,
        take_profit_targets: list[float],
        position_size: float,
        max_bars_in_trade: int = 100,
    ):
        self.trade_id = str(uuid.uuid4())
        self.strategy_id = strategy_id
        self.instrument = instrument
        self.timeframe = timeframe
        self.direction = direction
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit_targets = take_profit_targets
        self.position_size = position_size
        self.max_bars_in_trade = max_bars_in_trade
        self.bars_in_trade = 0
        self.max_favorable_excursion = 0.0
        self.max_adverse_excursion = 0.0
        self.is_open = True

    def update(self, bar: pd.Series) -> ExitReason | None:
        """Update position with new bar data.

        Checks stop loss first (conservative), then take profit.
        Returns ExitReason if position should close, None otherwise.
        """
        self.bars_in_trade += 1
        high = bar["high"]
        low = bar["low"]

        # Calculate excursions
        if self.direction == Direction.LONG:
            favorable = high - self.entry_price
            adverse = self.entry_price - low
        else:
            favorable = self.entry_price - low
            adverse = high - self.entry_price

        self.max_favorable_excursion = max(self.max_favorable_excursion, favorable)
        self.max_adverse_excursion = max(self.max_adverse_excursion, adverse)

        # Check stop loss first (conservative assumption)
        if self.direction == Direction.LONG:
            if low <= self.stop_loss:
                return ExitReason.STOP_LOSS_HIT
        else:
            if high >= self.stop_loss:
                return ExitReason.STOP_LOSS_HIT

        # Check take profit
        if self.take_profit_targets:
            tp = self.take_profit_targets[0]
            if self.direction == Direction.LONG:
                if high >= tp:
                    return ExitReason.TAKE_PROFIT_HIT
            else:
                if low <= tp:
                    return ExitReason.TAKE_PROFIT_HIT

        # Check time stop
        if self.bars_in_trade >= self.max_bars_in_trade:
            return ExitReason.TIME_STOP_EXIT

        return None

    def close(
        self, exit_price: float, exit_time: datetime, reason: ExitReason
    ) -> TradeResult:
        """Close the position and produce a TradeResult."""
        self.is_open = False

        if self.direction == Direction.LONG:
            pnl = (exit_price - self.entry_price) * self.position_size
            pnl_pct = (exit_price - self.entry_price) / self.entry_price * 100
        else:
            pnl = (self.entry_price - exit_price) * self.position_size
            pnl_pct = (self.entry_price - exit_price) / self.entry_price * 100

        return TradeResult(
            trade_id=self.trade_id,
            strategy_id=self.strategy_id,
            instrument=self.instrument,
            timeframe=self.timeframe,
            direction=self.direction,
            entry_time=self.entry_time,
            entry_price=self.entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            position_size=self.position_size,
            bars_in_trade=self.bars_in_trade,
            max_favorable_excursion=self.max_favorable_excursion,
            max_adverse_excursion=self.max_adverse_excursion,
        )

    def to_dict(self) -> dict:
        """Convert to dict for passing to strategy methods."""
        return {
            "trade_id": self.trade_id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit_targets": self.take_profit_targets,
            "position_size": self.position_size,
            "bars_in_trade": self.bars_in_trade,
            "max_bars_in_trade": self.max_bars_in_trade,
            "is_open": self.is_open,
        }


def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    max_leverage: float = 30.0,
    instrument: str = "",
) -> float:
    """Calculate position size with leverage cap.

    Delegates to fibokei.backtester.sizing for the full implementation.
    Kept here for backward compatibility with external callers.
    """
    from fibokei.backtester.sizing import calculate_position_size as _calc

    return _calc(capital, risk_pct, entry, stop, max_leverage, instrument)
