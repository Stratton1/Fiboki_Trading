"""Minimum stop distance floor — regression tests.

Verifies that the ATR-based minimum stop distance floor prevents
position-size inflation when stop distances are tight relative to
ATR.  This was the root cause of bot04/EURUSD/H1 producing £111K
from £1K — tiny stops (< 5 pips) were hitting the 30x leverage
cap, creating asymmetric payoffs that compounded unrealistically.

The fix: ``calculate_position_size`` now accepts a ``min_stop_distance``
parameter.  The engine passes ATR as the floor.  When the actual
stop is tighter than ATR, the sizing formula uses ATR instead,
keeping risk-based sizing (not leverage) as the binding constraint.
"""

import math

import numpy as np
import pandas as pd
import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.backtester.sizing import calculate_position_size
from fibokei.core.models import Direction, Timeframe


# ---------------------------------------------------------------------------
# Unit: min_stop_distance parameter
# ---------------------------------------------------------------------------


class TestMinStopDistance:
    """Verify min_stop_distance clamps sizing correctly."""

    def test_no_floor_tight_stop_large_size(self):
        """Without floor, a 2-pip stop on £1K produces a large position."""
        # 2-pip stop on EURUSD
        size = calculate_position_size(
            1000, 1.0, 1.1000, 1.0998, 30.0, "EURUSD",
            min_stop_distance=0.0,
        )
        # Risk-based: 10 / 0.0002 = 50,000 units
        # Leverage cap: 1000 * 30 / 1.10 = 27,272 units
        assert size == pytest.approx(27272.7, rel=0.01)

    def test_floor_reduces_size(self):
        """With ATR floor (40 pips), a 2-pip stop is sized as 40 pips."""
        size_no_floor = calculate_position_size(
            1000, 1.0, 1.1000, 1.0998, 30.0, "EURUSD",
            min_stop_distance=0.0,
        )
        size_with_floor = calculate_position_size(
            1000, 1.0, 1.1000, 1.0998, 30.0, "EURUSD",
            min_stop_distance=0.0040,  # 40 pips ATR floor
        )
        # Floor should reduce size: 10 / 0.0040 = 2,500 units
        assert size_with_floor == pytest.approx(2500.0, rel=0.01)
        assert size_with_floor < size_no_floor

    def test_floor_doesnt_affect_wide_stops(self):
        """When actual stop > floor, sizing is unchanged."""
        size_no_floor = calculate_position_size(
            1000, 1.0, 1.1000, 1.0950, 30.0, "EURUSD",
            min_stop_distance=0.0,
        )
        size_with_floor = calculate_position_size(
            1000, 1.0, 1.1000, 1.0950, 30.0, "EURUSD",
            min_stop_distance=0.0040,  # 40 pips, less than the 50-pip stop
        )
        assert size_with_floor == pytest.approx(size_no_floor, rel=0.001)

    def test_floor_zero_disables(self):
        """min_stop_distance=0 is equivalent to no floor."""
        size_default = calculate_position_size(
            1000, 1.0, 1.1000, 1.0998, 30.0, "EURUSD",
        )
        size_zero = calculate_position_size(
            1000, 1.0, 1.1000, 1.0998, 30.0, "EURUSD",
            min_stop_distance=0.0,
        )
        assert size_default == pytest.approx(size_zero, rel=0.001)

    def test_leverage_cap_still_applies_with_floor(self):
        """Even with floor, leverage cap is the final constraint."""
        # Very wide stop + small floor: risk-based sizing dominates
        size = calculate_position_size(
            1000, 1.0, 1.1000, 1.0500, 30.0, "EURUSD",
            min_stop_distance=0.0010,
        )
        # Risk-based: 10 / 0.05 = 200 units
        # Leverage cap: 27,272 units (not hit)
        assert size == pytest.approx(200.0, rel=0.01)


# ---------------------------------------------------------------------------
# Integration: bot04/EURUSD/H1 specific
# ---------------------------------------------------------------------------


class TestBot04EURUSDRealism:
    """Full-run regression tests for the exact combo that was inflated."""

    @pytest.fixture
    def eurusd_h1_data(self):
        """Load canonical EURUSD H1 data."""
        from fibokei.data.providers.registry import load_canonical
        df = load_canonical("EURUSD", "H1")
        if df is None:
            pytest.skip("No canonical EURUSD H1 data available")
        return df

    @pytest.fixture
    def bot04_result(self, eurusd_h1_data):
        """Run bot04 on EURUSD H1 with £1K capital."""
        from fibokei.strategies.bot04_chikou_momentum import ChikouMomentum
        config = BacktestConfig(initial_capital=1000.0)
        result = Backtester(ChikouMomentum(), config).run(
            eurusd_h1_data, "EURUSD", Timeframe.H1
        )
        return result

    def test_net_pnl_below_50x(self, bot04_result):
        """Net PnL must not exceed 50x initial capital.

        The old bug produced 111x.  After fix, 15x is typical.
        We use 50x as a generous upper bound to catch regressions
        without being fragile against data changes.
        """
        metrics = compute_metrics(bot04_result)
        ratio = metrics["total_net_profit"] / 1000.0
        assert ratio < 50.0, (
            f"bot04/EURUSD/H1 net PnL £{metrics['total_net_profit']:,.0f} "
            f"is {ratio:.0f}x capital — still inflated"
        )

    def test_no_trades_at_30x_leverage(self, bot04_result):
        """No trade should be at the 30x leverage ceiling."""
        equity = 1000.0
        max_lev = 0.0
        for t in bot04_result.trades:
            lev = t.position_size * t.entry_price / equity if equity > 0 else 0
            max_lev = max(max_lev, lev)
            equity += t.pnl
        assert max_lev < 29.9, (
            f"Found trade at {max_lev:.1f}x leverage — min_stop_distance not working"
        )

    def test_mean_leverage_below_15x(self, bot04_result):
        """Mean leverage should be well below the 30x cap."""
        equity = 1000.0
        leverages = []
        for t in bot04_result.trades:
            lev = t.position_size * t.entry_price / equity if equity > 0 else 0
            leverages.append(lev)
            equity += t.pnl
        mean_lev = np.mean(leverages)
        assert mean_lev < 15.0, (
            f"Mean leverage {mean_lev:.1f}x too high — positions still oversized"
        )

    def test_max_single_trade_pnl_pct(self, bot04_result):
        """No single trade should gain or lose more than 5% of equity."""
        equity = 1000.0
        for t in bot04_result.trades:
            pct = abs(t.pnl) / equity * 100 if equity > 0 else 0
            assert pct < 5.0, (
                f"Single trade PnL {t.pnl:.2f} is {pct:.1f}% of equity £{equity:.0f} — "
                f"position too large"
            )
            equity += t.pnl

    def test_best_trade_bounded(self, bot04_result):
        """Best single trade should not exceed £2,000 from a £1K account."""
        metrics = compute_metrics(bot04_result)
        assert metrics["best_trade"] < 2000.0, (
            f"Best trade £{metrics['best_trade']:,.0f} is implausibly large"
        )


# ---------------------------------------------------------------------------
# Integration: min_stop_distance applied in engine
# ---------------------------------------------------------------------------


class _TightStopStrategy:
    """Strategy that always enters with a 1-pip stop (guaranteed to hit floor)."""

    strategy_id = "test_tight_stop"
    warmup_period = 100

    def run_preparation(self, df):
        from fibokei.indicators.atr import ATR
        return ATR().compute(df)

    def generate_signal(self, df, i, context):
        if i % 30 != 0:
            return None
        bar = df.iloc[i]
        return _Signal(Direction.LONG, bar["close"])

    def generate_exit(self, position_dict, df, i, context):
        return None

    def build_trade_plan(self, signal, context):
        entry = signal.proposed_entry
        return _TradePlan(
            stop_loss=entry - 0.0001,  # 1-pip stop — should trigger floor
            take_profit_targets=[entry + 0.0050],
            max_bars_in_trade=20,
        )


class _Signal:
    def __init__(self, direction, proposed_entry):
        self.direction = direction
        self.proposed_entry = proposed_entry


class _TradePlan:
    def __init__(self, stop_loss, take_profit_targets, max_bars_in_trade=None):
        self.stop_loss = stop_loss
        self.take_profit_targets = take_profit_targets
        self.max_bars_in_trade = max_bars_in_trade


class TestEngineMinStopIntegration:
    """Verify the engine passes ATR as min_stop_distance."""

    def test_tight_stop_strategy_uses_atr_floor(self):
        """A strategy with 1-pip stops should produce moderate leverage."""
        rng = np.random.default_rng(42)
        n = 500
        dates = pd.date_range("2024-01-01", periods=n, freq="h")
        prices = np.linspace(1.10, 1.12, n) + rng.normal(0, 0.001, n)
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices + rng.uniform(0.0003, 0.002, n),
                "low": prices - rng.uniform(0.0003, 0.002, n),
                "close": prices + rng.uniform(-0.001, 0.001, n),
                "volume": rng.integers(100, 5000, n),
            },
            index=dates,
        )

        config = BacktestConfig(initial_capital=1000.0)
        result = Backtester(_TightStopStrategy(), config).run(
            df, "EURUSD", Timeframe.H1
        )

        # Without ATR floor, every trade would be at 30x leverage
        # With ATR floor, trades should be moderate leverage
        equity = 1000.0
        for t in result.trades:
            lev = t.position_size * t.entry_price / equity if equity > 0 else 0
            assert lev < 29.9, (
                f"Trade at {lev:.1f}x leverage — ATR floor not applied in engine"
            )
            equity += t.pnl
