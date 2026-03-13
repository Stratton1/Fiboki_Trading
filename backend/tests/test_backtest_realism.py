"""Tests proving backtest economic realism after the sizing fix.

These tests verify that position sizing, leverage caps, pip value
conversion, and spread costs produce economically credible results.
"""

import numpy as np
import pandas as pd
import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.sizing import (
    calculate_position_size,
    get_default_spread,
    max_position_size,
    pip_value_adjustment,
)
from fibokei.core.models import Direction, Timeframe


# ---------------------------------------------------------------------------
# Unit tests: sizing module
# ---------------------------------------------------------------------------


class TestPipValueAdjustment:
    def test_eurusd_no_adjustment(self):
        assert pip_value_adjustment("EURUSD", 1.10) == 1.0

    def test_gbpusd_no_adjustment(self):
        assert pip_value_adjustment("GBPUSD", 1.27) == 1.0

    def test_usdjpy_divides_by_price(self):
        adj = pip_value_adjustment("USDJPY", 150.0)
        assert abs(adj - 1.0 / 150.0) < 1e-10

    def test_eurjpy_divides_by_price(self):
        adj = pip_value_adjustment("EURJPY", 165.0)
        assert abs(adj - 1.0 / 165.0) < 1e-10

    def test_xauusd_no_adjustment(self):
        assert pip_value_adjustment("XAUUSD", 2000.0) == 1.0

    def test_case_insensitive(self):
        assert pip_value_adjustment("usdjpy", 150.0) == pip_value_adjustment("USDJPY", 150.0)


class TestMaxPositionSize:
    def test_leverage_cap_eurusd(self):
        # £10,000 account, EURUSD at 1.10, 30:1 leverage
        max_size = max_position_size(10_000, 1.10, 30.0)
        # max notional = 10,000 * 30 = 300,000 → 300,000 / 1.10 ≈ 272,727 units
        assert abs(max_size - 272_727.27) < 1.0

    def test_leverage_cap_usdjpy(self):
        # £10,000 account, USDJPY at 150, 30:1 leverage
        max_size = max_position_size(10_000, 150.0, 30.0)
        # 300,000 / 150 = 2,000 units
        assert max_size == 2_000.0

    def test_zero_price_returns_zero(self):
        assert max_position_size(10_000, 0.0, 30.0) == 0.0


class TestCalculatePositionSize:
    def test_eurusd_1pct_risk_20pip_stop(self):
        """EURUSD: 1% risk on £10K with 20-pip stop → reasonable size."""
        size = calculate_position_size(
            capital=10_000,
            risk_pct=1.0,
            entry=1.1000,
            stop=1.0980,  # 20-pip stop
            max_leverage=30.0,
            instrument="EURUSD",
        )
        # risk_amount = 100, risk_per_unit = 0.0020 → raw = 50,000 units
        # leverage cap: 10,000 * 30 / 1.10 ≈ 272,727
        # 50,000 < 272,727 → size = 50,000
        assert abs(size - 50_000) < 1.0
        # Notional = 50,000 * 1.10 = 55,000 → leverage ≈ 5.5x (well under 30x)
        notional = size * 1.10
        assert notional / 10_000 < 30.0

    def test_eurusd_tight_stop_hits_leverage_cap(self):
        """EURUSD: 1% risk with 1-pip stop → leverage cap kicks in."""
        size = calculate_position_size(
            capital=10_000,
            risk_pct=1.0,
            entry=1.1000,
            stop=1.0999,  # 1-pip stop
            max_leverage=30.0,
            instrument="EURUSD",
        )
        # raw = 100 / 0.0001 = 1,000,000 units → way over leverage cap
        # capped at 272,727 units
        max_allowed = max_position_size(10_000, 1.1000, 30.0)
        assert abs(size - max_allowed) < 1.0

    def test_usdjpy_pip_conversion(self):
        """USDJPY: pip value adjustment prevents oversized positions."""
        size = calculate_position_size(
            capital=10_000,
            risk_pct=1.0,
            entry=150.00,
            stop=149.80,  # 20-pip stop
            max_leverage=30.0,
            instrument="USDJPY",
        )
        # risk_amount = 100
        # risk_per_unit = 0.20 JPY, but adjusted: 0.20 / 150 = 0.001333 USD
        # raw = 100 / 0.001333 ≈ 75,000
        # leverage cap: 10,000 * 30 / 150 = 2,000
        # So capped at 2,000 units
        assert size == 2_000.0

    def test_zero_risk_distance(self):
        size = calculate_position_size(10_000, 1.0, 1.10, 1.10)
        assert size == 0.0


class TestDefaultSpreads:
    def test_eurusd_has_spread(self):
        assert get_default_spread("EURUSD") > 0

    def test_usdjpy_has_spread(self):
        assert get_default_spread("USDJPY") > 0

    def test_unknown_forex_gets_default(self):
        assert get_default_spread("ABCDEF") > 0

    def test_non_forex_returns_zero(self):
        assert get_default_spread("BTC") == 0.0


# ---------------------------------------------------------------------------
# Integration tests: full backtest produces credible PnL
# ---------------------------------------------------------------------------


def _make_trending_data(
    instrument: str,
    n_bars: int = 500,
    base_price: float = 1.10,
    trend_pct: float = 0.05,
    volatility: float = 0.003,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLC data with a mild uptrend."""
    rng = np.random.default_rng(seed)
    prices = [base_price]
    trend_per_bar = trend_pct / n_bars

    for _ in range(n_bars - 1):
        change = trend_per_bar + rng.normal(0, volatility) * base_price / 1000
        prices.append(prices[-1] + change)

    dates = pd.date_range("2024-01-01", periods=n_bars, freq="h")
    df = pd.DataFrame(index=dates)

    for i in range(n_bars):
        mid = prices[i]
        noise = rng.uniform(0.0002, 0.001) * mid
        df.loc[dates[i], "open"] = mid
        df.loc[dates[i], "high"] = mid + noise
        df.loc[dates[i], "low"] = mid - noise
        df.loc[dates[i], "close"] = mid + rng.uniform(-noise / 2, noise / 2)
        df.loc[dates[i], "volume"] = rng.integers(100, 10000)

    return df


class _DummyStrategy:
    """Minimal strategy that generates alternating long signals."""

    strategy_id = "test_realism"
    warmup_period = 100

    def run_preparation(self, df):
        return df

    def generate_signal(self, df, i, context):
        # Signal every 20 bars to avoid overtrading
        if i % 20 != 0:
            return None
        bar = df.iloc[i]
        return _Signal(
            direction=Direction.LONG,
            proposed_entry=bar["close"],
        )

    def generate_exit(self, position_dict, df, i, context):
        return None

    def build_trade_plan(self, signal, context):
        entry = signal.proposed_entry
        stop = entry * 0.998  # 20-pip-ish stop for EURUSD
        tp = entry * 1.004  # 40-pip-ish TP
        return _TradePlan(
            stop_loss=stop,
            take_profit_targets=[tp],
            max_bars_in_trade=50,
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


class TestBacktestRealism:
    """Integration tests: full backtests produce economically credible results."""

    def test_eurusd_pnl_bounded(self):
        """EURUSD backtest PnL must not exceed 10x initial capital."""
        df = _make_trending_data("EURUSD", n_bars=500, base_price=1.10)
        config = BacktestConfig(initial_capital=10_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "EURUSD", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 10_000
        # Max credible: starting capital * 10 (100 trades at 1% risk each)
        assert final_equity < 10_000 * 10, (
            f"EURUSD final equity {final_equity:.2f} exceeds 10x initial capital"
        )
        # Must not go negative
        assert final_equity > 0

    def test_gbpusd_leverage_respected(self):
        """GBPUSD: every trade's position size respects 30:1 leverage."""
        df = _make_trending_data("GBPUSD", n_bars=500, base_price=1.27)
        config = BacktestConfig(initial_capital=10_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "GBPUSD", Timeframe.H1)

        for trade in result.trades:
            notional = trade.position_size * trade.entry_price
            # Allow small floating-point tolerance
            leverage = notional / config.initial_capital
            assert leverage <= 30.1, (
                f"Trade leverage {leverage:.1f}x exceeds 30x cap"
            )

    def test_usdjpy_pnl_in_account_currency(self):
        """USDJPY: PnL must be in account currency (not raw JPY)."""
        df = _make_trending_data("USDJPY", n_bars=500, base_price=150.0, volatility=0.1)
        config = BacktestConfig(initial_capital=10_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "USDJPY", Timeframe.H1)

        for trade in result.trades:
            # Individual trade PnL should be reasonable (not 150x too large)
            assert abs(trade.pnl) < 5_000, (
                f"USDJPY trade PnL {trade.pnl:.2f} looks like raw JPY, not account currency"
            )

    def test_spread_costs_applied(self):
        """Backtest with spread should produce worse results than zero spread."""
        df = _make_trending_data("EURUSD", n_bars=300, base_price=1.10)
        strategy = _DummyStrategy()

        config_no_spread = BacktestConfig(
            initial_capital=10_000, spread_points=0.0001, slippage_points=0.0
        )
        config_with_spread = BacktestConfig(
            initial_capital=10_000, spread_points=0.0010, slippage_points=0.0
        )

        result_low = Backtester(strategy, config_no_spread).run(df, "EURUSD", Timeframe.H1)
        result_high = Backtester(strategy, config_with_spread).run(df, "EURUSD", Timeframe.H1)

        pnl_low = sum(t.pnl for t in result_low.trades)
        pnl_high = sum(t.pnl for t in result_high.trades)

        # Higher spread should produce lower (or equal) PnL
        assert pnl_low >= pnl_high, (
            f"Higher spread produced better PnL: {pnl_high:.2f} vs {pnl_low:.2f}"
        )

    def test_bankruptcy_guard(self):
        """Equity should never go below zero."""
        # Use high volatility data that causes losses
        df = _make_trending_data(
            "EURUSD", n_bars=500, base_price=1.10,
            trend_pct=-0.10, volatility=0.01, seed=99,
        )
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=2.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "EURUSD", Timeframe.H1)

        for eq in result.equity_curve:
            assert eq >= 0, f"Equity went negative: {eq}"

    def test_no_exponential_blowup(self):
        """Even with many winning trades, equity growth must be bounded."""
        df = _make_trending_data(
            "EURUSD", n_bars=1000, base_price=1.10,
            trend_pct=0.20, volatility=0.001, seed=7,
        )
        config = BacktestConfig(initial_capital=10_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "EURUSD", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 10_000
        # With 1% risk per trade and ~45 trades, max credible growth
        # is roughly 1.01^45 ≈ 1.56x. Allow 20x as generous upper bound.
        assert final_equity < 10_000 * 20, (
            f"Exponential blowup detected: final equity = {final_equity:.2f}"
        )
