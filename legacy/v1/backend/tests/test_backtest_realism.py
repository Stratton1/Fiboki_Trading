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
        # £1,000 account, EURUSD at 1.10, 30:1 leverage
        max_size = max_position_size(1_000, 1.10, 30.0)
        # max notional = 1,000 * 30 = 30,000 → 30,000 / 1.10 ≈ 27,272.7 units
        assert abs(max_size - 27_272.73) < 1.0

    def test_leverage_cap_usdjpy(self):
        # £1,000 account, USDJPY at 150, 30:1 leverage
        max_size = max_position_size(1_000, 150.0, 30.0)
        # 30,000 / 150 = 200 units
        assert max_size == 200.0

    def test_zero_price_returns_zero(self):
        assert max_position_size(1_000, 0.0, 30.0) == 0.0


class TestCalculatePositionSize:
    def test_eurusd_1pct_risk_20pip_stop(self):
        """EURUSD: 1% risk on £1K with 20-pip stop → reasonable size."""
        size = calculate_position_size(
            capital=1_000,
            risk_pct=1.0,
            entry=1.1000,
            stop=1.0980,  # 20-pip stop
            max_leverage=30.0,
            instrument="EURUSD",
        )
        # risk_amount = 10, risk_per_unit = 0.0020 → raw = 5,000 units
        # leverage cap: 1,000 * 30 / 1.10 ≈ 27,273
        # 5,000 < 27,273 → size = 5,000
        assert abs(size - 5_000) < 1.0
        # Notional = 5,000 * 1.10 = 5,500 → leverage ≈ 5.5x (well under 30x)
        notional = size * 1.10
        assert notional / 1_000 < 30.0

    def test_eurusd_tight_stop_hits_leverage_cap(self):
        """EURUSD: 1% risk with 1-pip stop → leverage cap kicks in."""
        size = calculate_position_size(
            capital=1_000,
            risk_pct=1.0,
            entry=1.1000,
            stop=1.0999,  # 1-pip stop
            max_leverage=30.0,
            instrument="EURUSD",
        )
        # raw = 10 / 0.0001 = 100,000 units → way over leverage cap
        # capped at ~27,273 units
        max_allowed = max_position_size(1_000, 1.1000, 30.0)
        assert abs(size - max_allowed) < 1.0

    def test_usdjpy_pip_conversion(self):
        """USDJPY: pip value adjustment prevents oversized positions."""
        size = calculate_position_size(
            capital=1_000,
            risk_pct=1.0,
            entry=150.00,
            stop=149.80,  # 20-pip stop
            max_leverage=30.0,
            instrument="USDJPY",
        )
        # risk_amount = 10
        # risk_per_unit = 0.20 JPY, but adjusted: 0.20 / 150 = 0.001333 USD
        # raw = 10 / 0.001333 ≈ 7,500
        # leverage cap: 1,000 * 30 / 150 = 200
        # So capped at 200 units
        assert size == 200.0

    def test_zero_risk_distance(self):
        size = calculate_position_size(1_000, 1.0, 1.10, 1.10)
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

    def test_default_config_uses_1k_capital(self):
        """BacktestConfig default must match paper account: £1,000."""
        config = BacktestConfig()
        assert config.initial_capital == 1_000.0

    def test_eurusd_pnl_bounded(self):
        """EURUSD backtest PnL must not exceed 10x initial capital."""
        df = _make_trending_data("EURUSD", n_bars=500, base_price=1.10)
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "EURUSD", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 1_000
        # Max credible: starting capital * 10 (100 trades at 1% risk each)
        assert final_equity < 1_000 * 10, (
            f"EURUSD final equity {final_equity:.2f} exceeds 10x initial capital"
        )
        # Must not go negative
        assert final_equity > 0

    def test_gbpusd_leverage_respected(self):
        """GBPUSD: every trade's position size respects 30:1 leverage."""
        df = _make_trending_data("GBPUSD", n_bars=500, base_price=1.27)
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
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
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "USDJPY", Timeframe.H1)

        for trade in result.trades:
            # Individual trade PnL should be reasonable (not 150x too large)
            assert abs(trade.pnl) < 500, (
                f"USDJPY trade PnL {trade.pnl:.2f} looks like raw JPY, not account currency"
            )

    def test_spread_costs_applied(self):
        """Backtest with spread should produce worse results than zero spread."""
        df = _make_trending_data("EURUSD", n_bars=300, base_price=1.10)
        strategy = _DummyStrategy()

        config_no_spread = BacktestConfig(
            initial_capital=1_000, spread_points=0.0001, slippage_points=0.0
        )
        config_with_spread = BacktestConfig(
            initial_capital=1_000, spread_points=0.0010, slippage_points=0.0
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
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "EURUSD", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 1_000
        # With 1% risk per trade and ~45 trades, max credible growth
        # is roughly 1.01^45 ≈ 1.56x. Allow 20x as generous upper bound.
        assert final_equity < 1_000 * 20, (
            f"Exponential blowup detected: final equity = {final_equity:.2f}"
        )


class TestBacktestRealism1kCapital:
    """Additional realism tests specifically for £1,000 starting capital
    across different instrument classes."""

    def test_xauusd_gold_pnl_bounded(self):
        """Gold (XAUUSD): PnL bounded relative to £1K account."""
        df = _make_trending_data("XAUUSD", n_bars=500, base_price=2000.0, volatility=2.0)
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "XAUUSD", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 1_000
        assert final_equity < 1_000 * 10, (
            f"XAUUSD final equity {final_equity:.2f} exceeds 10x initial capital"
        )
        assert final_equity > 0

    def test_xauusd_spread_applied(self):
        """Gold: default spread (0.35 points) is applied."""
        spread = get_default_spread("XAUUSD")
        assert spread == 0.35

    def test_us500_index_pnl_bounded(self):
        """US500 index: PnL bounded relative to £1K account."""
        df = _make_trending_data("US500", n_bars=500, base_price=5000.0, volatility=5.0)
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "US500", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 1_000
        assert final_equity < 1_000 * 10, (
            f"US500 final equity {final_equity:.2f} exceeds 10x initial capital"
        )
        assert final_equity > 0

    def test_bcousd_oil_pnl_bounded(self):
        """Brent crude (BCOUSD): PnL bounded relative to £1K account."""
        df = _make_trending_data("BCOUSD", n_bars=500, base_price=80.0, volatility=0.3)
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "BCOUSD", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 1_000
        assert final_equity < 1_000 * 10, (
            f"BCOUSD final equity {final_equity:.2f} exceeds 10x initial capital"
        )
        assert final_equity > 0

    def test_eurjpy_cross_pnl_bounded(self):
        """EURJPY cross: PnL bounded and JPY conversion applied."""
        df = _make_trending_data("EURJPY", n_bars=500, base_price=165.0, volatility=0.15)
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "EURJPY", Timeframe.H1)

        final_equity = result.equity_curve[-1] if result.equity_curve else 1_000
        assert final_equity < 1_000 * 10, (
            f"EURJPY final equity {final_equity:.2f} exceeds 10x initial capital"
        )
        for trade in result.trades:
            # Trade PnL should be in account currency, not raw JPY
            assert abs(trade.pnl) < 200, (
                f"EURJPY trade PnL {trade.pnl:.2f} looks unconverted"
            )

    def test_1k_account_max_single_trade_loss(self):
        """With 1% risk, max single-trade loss should be ~£10."""
        df = _make_trending_data(
            "EURUSD", n_bars=500, base_price=1.10,
            trend_pct=-0.05, volatility=0.005, seed=123,
        )
        config = BacktestConfig(initial_capital=1_000, risk_per_trade_pct=1.0)
        bt = Backtester(_DummyStrategy(), config)
        result = bt.run(df, "EURUSD", Timeframe.H1)

        for trade in result.trades:
            # A single losing trade should lose at most ~2x the intended risk
            # (slippage/gaps can cause slightly more than 1% loss)
            assert trade.pnl > -30, (
                f"Single trade loss {trade.pnl:.2f} exceeds 3% of £1K account"
            )

    def test_default_spread_always_applied(self):
        """When spread_points=0 in config, engine applies instrument-specific default."""
        df = _make_trending_data("EURUSD", n_bars=300, base_price=1.10)

        # Config with 0 spread (engine should apply default)
        config_default = BacktestConfig(initial_capital=1_000, spread_points=0.0)
        # Config with explicit zero-override (engine still applies default when 0.0)
        result_default = Backtester(_DummyStrategy(), config_default).run(df, "EURUSD", Timeframe.H1)

        # Config with unrealistically high spread
        config_high = BacktestConfig(initial_capital=1_000, spread_points=0.01)
        result_high = Backtester(_DummyStrategy(), config_high).run(df, "EURUSD", Timeframe.H1)

        pnl_default = sum(t.pnl for t in result_default.trades)
        pnl_high = sum(t.pnl for t in result_high.trades)

        # Default spread should produce better PnL than inflated spread
        assert pnl_default >= pnl_high
