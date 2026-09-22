"""Regression test: TP-hit trades can have negative PnL when spread exceeds TP distance.

This is correct behaviour — not a bug. When the spread/slippage cost pushes the
adjusted entry price beyond the take-profit target, the trade exits at TP but the
PnL is negative because the fill was worse than TP.

Example: LONG entry signal at 1.1000, TP at 1.1004 (4 pips).
With 5-pip spread → adjusted entry = 1.1000 + 0.00025 = 1.10025.
Exit at TP = 1.1004 → PnL = (1.1004 - 1.10025) * size = +0.15 pips (still positive).

But with a very wide spread (e.g. 10 pips = 0.0010):
adjusted entry = 1.1000 + 0.0005 = 1.1005.
Exit at TP = 1.1004 → PnL = (1.1004 - 1.1005) * size = -0.1 pips (negative!).

This test proves:
1. The mechanism exists and is understood
2. The PnL sign is correct given the maths
3. Zero-spread configs never produce negative-PnL TP hits
"""

import numpy as np
import pandas as pd
import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.position import Position
from fibokei.core.models import Direction, Timeframe
from fibokei.core.trades import ExitReason


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Signal:
    def __init__(self, direction, proposed_entry):
        self.direction = direction
        self.proposed_entry = proposed_entry


class _TradePlan:
    def __init__(self, stop_loss, take_profit_targets, max_bars_in_trade=None):
        self.stop_loss = stop_loss
        self.take_profit_targets = take_profit_targets
        self.max_bars_in_trade = max_bars_in_trade


class _TightTPStrategy:
    """Strategy with a very tight TP (4 pips) to trigger the spread artifact."""

    strategy_id = "test_tight_tp"
    warmup_period = 100

    def __init__(self, tp_distance: float = 0.0004):
        self.tp_distance = tp_distance

    def run_preparation(self, df):
        return df

    def generate_signal(self, df, i, context):
        if i != 100:  # Only signal once, at bar 100
            return None
        bar = df.iloc[i]
        return _Signal(direction=Direction.LONG, proposed_entry=bar["close"])

    def generate_exit(self, position_dict, df, i, context):
        return None

    def build_trade_plan(self, signal, context):
        entry = signal.proposed_entry
        return _TradePlan(
            stop_loss=entry - 0.0050,  # wide SL so it doesn't trigger
            take_profit_targets=[entry + self.tp_distance],
            max_bars_in_trade=50,
        )


def _make_data_that_hits_tp(base_price: float = 1.1000, tp_pips: float = 4) -> pd.DataFrame:
    """Generate synthetic data where TP is hit on the second bar after entry."""
    n_bars = 150
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="h")
    data = {
        "open": [base_price] * n_bars,
        "high": [base_price + 0.0001] * n_bars,
        "low": [base_price - 0.0001] * n_bars,
        "close": [base_price] * n_bars,
        "volume": [1000] * n_bars,
    }
    df = pd.DataFrame(data, index=dates)

    # Bar 101 (first bar after entry at bar 100): price spikes to hit TP
    tp_target = base_price + tp_pips * 0.0001
    df.iloc[101, df.columns.get_loc("high")] = tp_target + 0.0002
    df.iloc[101, df.columns.get_loc("close")] = tp_target

    return df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTPHitNegativePnL:
    """Verify the TP-hit negative-PnL artifact is understood and documented."""

    def test_wide_spread_causes_negative_pnl_on_tp_hit(self):
        """When spread exceeds TP distance, TP hit produces negative PnL."""
        tp_distance = 0.0004  # 4 pips
        spread = 0.0010       # 10 pips — wider than TP distance

        df = _make_data_that_hits_tp(base_price=1.1000, tp_pips=4)
        config = BacktestConfig(
            initial_capital=10_000,
            risk_per_trade_pct=1.0,
            spread_points=spread,
            slippage_points=0.0,
        )
        strategy = _TightTPStrategy(tp_distance=tp_distance)
        result = Backtester(strategy, config).run(df, "EURUSD", Timeframe.H1)

        # Should have exactly 1 trade
        assert len(result.trades) == 1, f"Expected 1 trade, got {len(result.trades)}"
        trade = result.trades[0]

        # Trade must exit via TP
        assert trade.exit_reason == ExitReason.TAKE_PROFIT_HIT

        # PnL must be negative because spread > TP distance
        # adjusted_entry = 1.1000 + 0.0005 (half spread) = 1.1005
        # exit at TP = 1.1000 + 0.0004 = 1.1004
        # PnL per unit = 1.1004 - 1.1005 = -0.0001
        assert trade.pnl < 0, (
            f"Expected negative PnL on TP hit with wide spread, got {trade.pnl:.6f}"
        )

    def test_zero_spread_never_produces_negative_tp_pnl(self):
        """With zero spread, all TP hits must have positive PnL."""
        df = _make_data_that_hits_tp(base_price=1.1000, tp_pips=4)
        config = BacktestConfig(
            initial_capital=10_000,
            risk_per_trade_pct=1.0,
            spread_points=0.0001,  # tiny spread, much less than TP distance
            slippage_points=0.0,
        )
        strategy = _TightTPStrategy(tp_distance=0.0004)
        result = Backtester(strategy, config).run(df, "EURUSD", Timeframe.H1)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == ExitReason.TAKE_PROFIT_HIT
        assert trade.pnl > 0, (
            f"TP hit with minimal spread should be profitable, got {trade.pnl:.6f}"
        )

    def test_position_unit_level_verification(self):
        """Unit test: directly verify the maths at Position level."""
        base_entry = 1.1000
        half_spread = 0.0005  # 10 pip total spread
        adjusted_entry = base_entry + half_spread  # 1.1005
        tp = base_entry + 0.0004  # 1.1004 — set relative to raw signal

        pos = Position(
            strategy_id="test",
            instrument="EURUSD",
            timeframe=Timeframe.H1,
            direction=Direction.LONG,
            entry_time=pd.Timestamp("2024-01-01"),
            entry_price=adjusted_entry,
            stop_loss=base_entry - 0.0050,
            take_profit_targets=[tp],
            position_size=10_000,
        )

        # Close at TP price
        trade = pos.close(tp, pd.Timestamp("2024-01-02"), ExitReason.TAKE_PROFIT_HIT)

        # PnL = (1.1004 - 1.1005) * 10_000 = -1.0
        expected_pnl = (tp - adjusted_entry) * 10_000
        assert abs(trade.pnl - expected_pnl) < 0.01
        assert trade.pnl < 0, "TP hit with spread > TP distance must produce negative PnL"

    def test_short_direction_same_artifact(self):
        """SHORT trades have the same artifact: spread pushes entry below TP."""
        base_entry = 1.1000
        half_spread = 0.0005
        adjusted_entry = base_entry - half_spread  # 1.0995 for SHORT
        tp = base_entry - 0.0004  # 1.0996 — closer to entry than spread

        pos = Position(
            strategy_id="test",
            instrument="EURUSD",
            timeframe=Timeframe.H1,
            direction=Direction.SHORT,
            entry_time=pd.Timestamp("2024-01-01"),
            entry_price=adjusted_entry,
            stop_loss=base_entry + 0.0050,
            take_profit_targets=[tp],
            position_size=10_000,
        )

        # SHORT PnL = (entry - exit) * size = (1.0995 - 1.0996) * 10_000 = -1.0
        trade = pos.close(tp, pd.Timestamp("2024-01-02"), ExitReason.TAKE_PROFIT_HIT)
        assert trade.pnl < 0, "SHORT TP hit with spread > TP distance must produce negative PnL"
