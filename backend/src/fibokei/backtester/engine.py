"""Core backtesting engine."""

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.position import Position
from fibokei.backtester.result import BacktestResult
from fibokei.backtester.sizing import (
    calculate_position_size,
    get_default_spread,
    pip_value_adjustment,
)
from fibokei.core.models import Direction, Timeframe
from fibokei.core.trades import ExitReason, TradeResult
from fibokei.strategies.base import Strategy


class Backtester:
    """Simulates strategy execution on historical data."""

    def __init__(self, strategy: Strategy, config: BacktestConfig | None = None):
        self.strategy = strategy
        self.config = config or BacktestConfig()

    def run(
        self, df: pd.DataFrame, instrument: str, timeframe: Timeframe
    ) -> BacktestResult:
        """Run backtest on historical data.

        Returns a BacktestResult with trades, equity curve, and metadata.
        """
        # Prepare data with indicators
        df = self.strategy.run_preparation(df)

        equity = self.config.initial_capital
        position: Position | None = None
        trades: list[TradeResult] = []
        equity_curve: list[float] = []

        # Apply default spread if none configured
        effective_spread = self.config.spread_points
        if effective_spread == 0.0:
            effective_spread = get_default_spread(instrument)

        # Determine warmup (skip indicator warmup bars)
        warmup = max(
            getattr(self.strategy, "warmup_period", 78),
            98,  # Minimum: Ichimoku warmup (78) + regime ATR avg (20)
        )
        warmup = min(warmup, len(df) - 1)

        context = {
            "instrument": instrument,
            "timeframe": timeframe,
            "risk_pct": self.config.risk_per_trade_pct,
            "capital": equity,
        }

        for i in range(warmup, len(df)):
            bar = df.iloc[i]
            bar_time = df.index[i]

            # --- Manage existing position ---
            if position is not None:
                # Always update mechanical state first (MFE/MAE, bars, SL/TP)
                mechanical_exit = position.update(bar)

                # Check strategy exit conditions
                exit_reason = self.strategy.generate_exit(
                    position.to_dict(), df, i, context
                )

                # Mechanical stops (SL/TP/time) take priority over strategy exits
                if mechanical_exit is not None:
                    exit_reason = mechanical_exit

                if exit_reason is not None:
                    exit_price = self._get_exit_price(
                        position, bar, exit_reason
                    )
                    trade = position.close(exit_price, bar_time, exit_reason)
                    # Convert PnL to account currency
                    adj = pip_value_adjustment(instrument, exit_price)
                    adjusted_pnl = trade.pnl * adj
                    trade = trade.model_copy(update={"pnl": adjusted_pnl})
                    trades.append(trade)
                    equity += trade.pnl
                    # Bankruptcy guard
                    if equity <= 0:
                        equity = 0.0
                        context["capital"] = equity
                        position = None
                        equity_curve.append(equity)
                        break
                    context["capital"] = equity
                    position = None

            # --- Look for new entry ---
            if position is None and equity > 0:
                signal = self.strategy.generate_signal(df, i, context.copy())

                if signal is not None:
                    # Check direction filter
                    if signal.direction == Direction.LONG and not self.config.allow_long:
                        signal = None
                    elif signal.direction == Direction.SHORT and not self.config.allow_short:
                        signal = None

                if signal is not None:
                    plan = self.strategy.build_trade_plan(signal, context)
                    entry_price = self._apply_costs(
                        signal.proposed_entry, signal.direction, effective_spread
                    )

                    # Use 2×ATR as minimum stop distance floor to prevent
                    # tiny stops from inflating position sizes via leverage.
                    # 1×ATR was insufficient on lower timeframes (M5/M15/M30)
                    # where ATR is only 1-5 pips and the leverage cap still binds.
                    atr_val = bar.get("atr", 0.0)
                    if pd.isna(atr_val):
                        atr_val = 0.0
                    min_stop = float(atr_val) * 2.0

                    pos_size = calculate_position_size(
                        equity,
                        self.config.risk_per_trade_pct,
                        entry_price,
                        plan.stop_loss,
                        max_leverage=self.config.max_leverage,
                        instrument=instrument,
                        min_stop_distance=min_stop,
                    )

                    if pos_size > 0:
                        position = Position(
                            strategy_id=self.strategy.strategy_id,
                            instrument=instrument,
                            timeframe=timeframe,
                            direction=signal.direction,
                            entry_time=bar_time,
                            entry_price=entry_price,
                            stop_loss=plan.stop_loss,
                            take_profit_targets=plan.take_profit_targets,
                            position_size=pos_size,
                            max_bars_in_trade=(
                                plan.max_bars_in_trade
                                or self.config.max_bars_in_trade
                            ),
                        )

            equity_curve.append(equity)

        # Close any remaining position at last bar close
        if position is not None:
            last_bar = df.iloc[-1]
            last_time = df.index[-1]
            trade = position.close(
                last_bar["close"], last_time, ExitReason.SYSTEM_SHUTDOWN_EXIT
            )
            adj = pip_value_adjustment(instrument, last_bar["close"])
            adjusted_pnl = trade.pnl * adj
            trade = trade.model_copy(update={"pnl": adjusted_pnl})
            trades.append(trade)
            equity += trade.pnl
            if equity_curve:
                equity_curve[-1] = equity

        return BacktestResult(
            strategy_id=self.strategy.strategy_id,
            instrument=instrument,
            timeframe=timeframe,
            config=self.config,
            trades=trades,
            equity_curve=equity_curve,
            start_date=df.index[warmup],
            end_date=df.index[-1],
            total_bars=len(df) - warmup,
        )

    def _apply_costs(self, price: float, direction: Direction, spread: float = 0.0) -> float:
        """Apply spread and slippage to entry price."""
        half_spread = spread / 2
        slippage = self.config.slippage_points

        if direction == Direction.LONG:
            return price + half_spread + slippage
        else:
            return price - half_spread - slippage

    def _get_exit_price(
        self, position: Position, bar: pd.Series, reason: ExitReason
    ) -> float:
        """Determine exit price based on exit reason."""
        if reason == ExitReason.STOP_LOSS_HIT:
            return position.stop_loss
        elif reason == ExitReason.TAKE_PROFIT_HIT:
            if position.take_profit_targets:
                return position.take_profit_targets[0]
            return bar["close"]
        else:
            return bar["close"]
