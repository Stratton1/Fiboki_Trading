"""Paper trading bot — wraps a strategy with an account."""

from enum import Enum

import pandas as pd

from fibokei.backtester.position import Position, calculate_position_size
from fibokei.core.models import Timeframe
from fibokei.core.trades import ExitReason
from fibokei.paper.account import PaperAccount
from fibokei.strategies.base import Strategy


class BotState(str, Enum):
    IDLE = "idle"
    MONITORING = "monitoring"
    POSITION_OPEN = "position_open"
    STOPPED = "stopped"
    PAUSED = "paused"


class PaperBot:
    """Runs a strategy on live/simulated bar data with a paper account."""

    def __init__(
        self,
        bot_id: str,
        strategy: Strategy,
        instrument: str,
        timeframe: Timeframe,
        account: PaperAccount,
        risk_pct: float = 1.0,
    ):
        self.bot_id = bot_id
        self.strategy = strategy
        self.instrument = instrument
        self.timeframe = timeframe
        self.account = account
        self.risk_pct = risk_pct

        self.state = BotState.IDLE
        self.position: Position | None = None
        self.bars_seen = 0
        self._df: pd.DataFrame | None = None
        self._prepared = False
        self._last_evaluated_bar = None

    def start(self) -> None:
        """Start monitoring for signals."""
        if self.state in (BotState.IDLE, BotState.STOPPED, BotState.PAUSED):
            self.state = BotState.MONITORING

    def stop(self) -> None:
        """Stop the bot. Close any open position at market."""
        self.state = BotState.STOPPED

    def pause(self) -> None:
        """Pause the bot — no new trades but manage open positions."""
        if self.state == BotState.MONITORING:
            self.state = BotState.PAUSED

    def on_candle_close(self, bar: pd.Series, bar_time) -> dict | None:
        """Process a new closed candle. Returns event dict if something happened."""
        if self.state in (BotState.STOPPED, BotState.IDLE):
            return None

        self.bars_seen += 1

        # Append bar to internal DataFrame
        if self._df is None:
            self._df = pd.DataFrame([bar], index=[bar_time])
        else:
            new_row = pd.DataFrame([bar], index=[bar_time])
            self._df = pd.concat([self._df, new_row])

        # Need enough bars for indicators
        if self.bars_seen < 100:
            return None

        # Re-compute indicators
        try:
            prepared = self.strategy.run_preparation(self._df.copy())
        except Exception:
            return None

        idx = len(prepared) - 1
        context = {
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "risk_pct": self.risk_pct,
            "capital": self.account.equity,
        }

        # Manage existing position
        if self.position is not None:
            current_bar = prepared.iloc[idx]
            mechanical_exit = self.position.update(current_bar)
            exit_reason = self.strategy.generate_exit(
                self.position.to_dict(), prepared, idx, context
            )
            if mechanical_exit is not None:
                exit_reason = mechanical_exit

            if exit_reason is not None:
                exit_price = self._get_exit_price(current_bar, exit_reason)
                trade = self.position.close(exit_price, bar_time, exit_reason)
                self.account.record_trade(trade)
                # Remove from open positions
                self.account.open_positions = [
                    p for p in self.account.open_positions
                    if p.get("trade_id") != self.position.trade_id
                ]
                self.position = None
                self.state = BotState.MONITORING
                return {"event": "trade_closed", "trade": trade}

        # Look for new entry (not if paused)
        if self.position is None and self.state == BotState.MONITORING:
            signal = self.strategy.generate_signal(prepared, idx, context.copy())
            if signal is not None:
                plan = self.strategy.build_trade_plan(signal, context)
                entry_price = signal.proposed_entry
                pos_size = calculate_position_size(
                    self.account.equity, self.risk_pct, entry_price, plan.stop_loss
                )
                if pos_size > 0:
                    self.position = Position(
                        strategy_id=self.strategy.strategy_id,
                        instrument=self.instrument,
                        timeframe=self.timeframe,
                        direction=signal.direction,
                        entry_time=bar_time,
                        entry_price=entry_price,
                        stop_loss=plan.stop_loss,
                        take_profit_targets=plan.take_profit_targets,
                        position_size=pos_size,
                        max_bars_in_trade=plan.max_bars_in_trade or 100,
                    )
                    self.state = BotState.POSITION_OPEN
                    self.account.open_positions.append(self.position.to_dict())
                    return {"event": "trade_opened", "signal": signal}

        return None

    def _get_exit_price(self, bar: pd.Series, reason: ExitReason) -> float:
        """Determine exit price based on exit reason."""
        if reason == ExitReason.STOP_LOSS_HIT and self.position:
            return self.position.stop_loss
        if reason == ExitReason.TAKE_PROFIT_HIT and self.position:
            if self.position.take_profit_targets:
                return self.position.take_profit_targets[0]
        return bar["close"]

    def get_status(self) -> dict:
        """Return bot status summary."""
        return {
            "bot_id": self.bot_id,
            "strategy_id": self.strategy.strategy_id,
            "instrument": self.instrument,
            "timeframe": self.timeframe.value,
            "state": self.state.value,
            "bars_seen": self.bars_seen,
            "has_position": self.position is not None,
            "position": self.position.to_dict() if self.position else None,
            "last_evaluated_bar": (
                self._last_evaluated_bar.isoformat()
                if self._last_evaluated_bar
                else None
            ),
        }
