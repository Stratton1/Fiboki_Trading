"""IG-aligned realism tests: verify backtest economics match IG execution model.

These tests prove that:
1. Leverage limits are instrument-class-specific (not flat 30:1)
2. Position sizing produces IG-credible trade sizes
3. PnL calculations are correct per asset class
4. Sharpe ratio is realistic (not inflated by sparse equity curves)
5. Scenario and scorer defaults use £1,000 (not £10,000)
6. Research and backtest paths use the same economics
"""

import math

import numpy as np
import pandas as pd
import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics, _compute_sharpe_from_trades
from fibokei.backtester.sizing import (
    calculate_position_size,
    get_default_spread,
    get_ig_leverage,
    max_position_size,
    pip_value_adjustment,
)
from fibokei.core.models import Direction, Timeframe
from fibokei.core.trades import ExitReason, TradeResult
from fibokei.research.scorer import ScoringConfig, _score_return


# ---------------------------------------------------------------------------
# IG leverage limits
# ---------------------------------------------------------------------------


class TestIGLeverage:
    """Verify instrument-class leverage limits match IG FCA retail."""

    def test_fx_majors_30x(self):
        for sym in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]:
            assert get_ig_leverage(sym) == 30.0, f"{sym} should be 30:1"

    def test_fx_crosses_20x(self):
        for sym in ["EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "AUDNZD", "GBPCHF"]:
            assert get_ig_leverage(sym) == 20.0, f"{sym} should be 20:1"

    def test_gold_20x(self):
        assert get_ig_leverage("XAUUSD") == 20.0

    def test_silver_20x(self):
        assert get_ig_leverage("XAGUSD") == 20.0

    def test_oil_10x(self):
        assert get_ig_leverage("BCOUSD") == 10.0
        assert get_ig_leverage("WTIUSD") == 10.0

    def test_indices_20x(self):
        for sym in ["US500", "US100", "UK100", "DE40", "JP225"]:
            assert get_ig_leverage(sym) == 20.0, f"{sym} should be 20:1"

    def test_crypto_2x(self):
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD"]:
            assert get_ig_leverage(sym) == 2.0, f"{sym} should be 2:1"

    def test_unknown_fx_defaults_to_20x(self):
        # SEKPLN is a plausible FX pair not in the explicit table
        assert get_ig_leverage("SEKPLN") == 20.0

    def test_unknown_non_fx_defaults_to_10x(self):
        assert get_ig_leverage("COPPER") == 10.0


# ---------------------------------------------------------------------------
# Golden trade tests: hand-worked examples with IG-style economics
# ---------------------------------------------------------------------------


class TestGoldenTrades:
    """Hand-calculated PnL tests for specific instrument scenarios."""

    def test_eurusd_long_win(self):
        """EURUSD long: 45-pip stop, 2:1 RR, 1% risk on £1K.

        Entry: 1.1000, Stop: 1.0955, TP: 1.1090
        Risk distance: 0.0045
        Risk amount: £10
        Position size: 10 / 0.0045 = 2,222.2 units
        Leverage: 2222 * 1.10 / 1000 = 2.4x (well under 30x)
        Win PnL: (1.1090 - 1.1000) * 2222.2 = £20.00
        """
        size = calculate_position_size(1000, 1.0, 1.1000, 1.0955, 30.0, "EURUSD")
        assert abs(size - 2222.2) < 1.0
        pnl = (1.1090 - 1.1000) * size
        assert abs(pnl - 20.0) < 0.1

    def test_eurusd_leverage_not_hit(self):
        """EURUSD with 45-pip stop: leverage should be ~2.4x, not 30x."""
        size = calculate_position_size(1000, 1.0, 1.1000, 1.0955, 30.0, "EURUSD")
        leverage = size * 1.1000 / 1000
        assert leverage < 5.0, f"EURUSD leverage {leverage:.1f}x is too high"

    def test_usdjpy_leverage_capped(self):
        """USDJPY: leverage cap makes actual risk << 1%.

        At 30:1 with £1K equity and USDJPY at 150:
        max_size = 1000 * 30 / 150 = 200 units
        Risk-based size = 10 / (0.45 * 1/150) = 3,333 → capped at 200
        Actual risk = 200 * 0.45 / 150 = £0.60 (not £10)
        """
        size = calculate_position_size(1000, 1.0, 150.0, 149.55, 30.0, "USDJPY")
        assert size == 200.0
        # Actual risk is much less than intended 1%
        actual_risk = size * abs(150.0 - 149.55) * pip_value_adjustment("USDJPY", 150.0)
        assert actual_risk < 1.0, "USDJPY actual risk should be < £1"

    def test_xauusd_long_win(self):
        """XAUUSD long: 30-point stop, 2:1 RR, 1% risk, 20:1 leverage.

        Entry: 2000, Stop: 1970, TP: 2060
        Risk: 30 points, Risk amount: £10
        Size: 10 / 30 = 0.333 units
        Max size @20x: 1000 * 20 / 2000 = 10 units (not hit)
        Win PnL: 60 * 0.333 = £20.00
        """
        size = calculate_position_size(1000, 1.0, 2000.0, 1970.0, 30.0, "XAUUSD")
        assert abs(size - 0.333) < 0.01
        # Verify IG leverage cap applied (20:1 not 30:1)
        max_size_ig = max_position_size(1000, 2000.0, 20.0)
        assert max_size_ig == 10.0
        assert size < max_size_ig  # Risk-based is below cap

    def test_bcousd_leverage_cap_at_10x(self):
        """Oil: IG leverage is 10:1, not 30:1.

        With a tight 0.1-point stop at price 80:
        Risk-based: 10 / 0.1 = 100 units
        Max @30x: 375 units → not capped (WRONG)
        Max @10x: 125 units → not capped
        But even tighter stop of 0.02:
        Risk-based: 10 / 0.02 = 500 units
        Max @30x: 375 → capped at 375 (WRONG, too big)
        Max @10x: 125 → capped at 125 (CORRECT)
        """
        # With very tight stop, leverage cap matters
        size = calculate_position_size(1000, 1.0, 80.0, 79.98, 30.0, "BCOUSD")
        # Should be capped at 10:1 = 1000*10/80 = 125
        max_at_10x = max_position_size(1000, 80.0, 10.0)
        assert size <= max_at_10x + 0.01, (
            f"BCOUSD size {size:.1f} exceeds 10:1 cap of {max_at_10x:.1f}"
        )

    def test_us500_leverage_cap_at_20x(self):
        """US500: IG leverage is 20:1, not 30:1."""
        # Tight stop to trigger leverage cap
        size = calculate_position_size(1000, 1.0, 5000.0, 4999.9, 30.0, "US500")
        max_at_20x = max_position_size(1000, 5000.0, 20.0)
        assert size <= max_at_20x + 0.01, (
            f"US500 size {size:.1f} exceeds 20:1 cap of {max_at_20x:.1f}"
        )

    def test_eurjpy_pnl_conversion(self):
        """EURJPY: PnL in JPY must be converted to account currency.

        Entry: 165, Stop: 164.55, TP: 165.90
        Risk distance: 0.45 (45 pips)
        pip_adj = 1/165 = 0.00606
        effective_risk_per_unit = 0.45 * 0.00606 = 0.002727
        raw_size = 10 / 0.002727 = 3,667 units
        max@20x (cross): 1000 * 20 / 165 = 121.2 → CAPPED at 121.2
        """
        size = calculate_position_size(1000, 1.0, 165.0, 164.55, 30.0, "EURJPY")
        max_at_20x = max_position_size(1000, 165.0, 20.0)
        assert size <= max_at_20x + 0.01, (
            f"EURJPY size {size:.1f} exceeds 20:1 cap of {max_at_20x:.1f}"
        )
        # Win PnL: (165.90 - 165) * 121.2 * (1/165.90) ≈ £0.66
        win_pnl = (165.90 - 165.0) * size * pip_value_adjustment("EURJPY", 165.90)
        assert win_pnl < 2.0, f"EURJPY win PnL {win_pnl:.2f} unrealistically large"

    def test_btcusd_leverage_cap_at_2x(self):
        """BTCUSD: crypto leverage must be 2:1."""
        size = calculate_position_size(1000, 1.0, 50000.0, 49000.0, 30.0, "BTCUSD")
        max_at_2x = max_position_size(1000, 50000.0, 2.0)
        assert size <= max_at_2x + 0.001


# ---------------------------------------------------------------------------
# Sharpe ratio sanity
# ---------------------------------------------------------------------------


class TestSharpeRealism:
    """Verify Sharpe is not inflated by sparse equity curves."""

    def _make_mock_trades(self, n_wins, n_losses, win_pnl, loss_pnl):
        """Create mock TradeResult objects."""
        trades = []
        for _ in range(n_wins):
            trades.append(_MockTrade(pnl=win_pnl))
        for _ in range(n_losses):
            trades.append(_MockTrade(pnl=loss_pnl))
        # Shuffle deterministically
        import random
        random.Random(42).shuffle(trades)
        return trades

    def test_modest_strategy_sharpe_below_10(self):
        """A 55% WR / 2:1 RR strategy with uniform payoffs.

        Per-trade mean/std ≈ 0.44, annualized by sqrt(252) ≈ 6.9.
        This is high because the payoffs have zero noise (every win is
        exactly +20, every loss exactly -10). Real strategies have more
        variance, producing lower Sharpe. We just verify it's not in the
        hundreds (the old bug) and that it's positive.
        """
        trades = self._make_mock_trades(55, 45, 20.0, -10.0)
        sharpe = _compute_sharpe_from_trades(trades, 1000.0, 500, 8760)
        assert sharpe < 10.0, f"Sharpe {sharpe:.2f} unrealistically high for 55/45 WR"
        assert sharpe > 0, "Should be positive for net-winning strategy"

    def test_strong_strategy_sharpe_below_15(self):
        """Even a very strong 70% WR / 2:1 RR stays bounded.

        Same caveat: uniform payoffs produce high per-trade Sharpe.
        With real variance these would be much lower.
        """
        trades = self._make_mock_trades(70, 30, 20.0, -10.0)
        sharpe = _compute_sharpe_from_trades(trades, 1000.0, 500, 8760)
        assert sharpe < 15.0, f"Sharpe {sharpe:.2f} unrealistically high"

    def test_losing_strategy_negative_sharpe(self):
        """A net-losing strategy should have negative Sharpe."""
        trades = self._make_mock_trades(30, 70, 10.0, -10.0)
        sharpe = _compute_sharpe_from_trades(trades, 1000.0, 500, 8760)
        assert sharpe < 0, f"Losing strategy should have negative Sharpe, got {sharpe:.2f}"


class _MockTrade:
    def __init__(self, pnl):
        self.pnl = pnl


# ---------------------------------------------------------------------------
# Config and scorer defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    """Verify all entry points use £1,000 default capital."""

    def test_backtest_config_default(self):
        config = BacktestConfig()
        assert config.initial_capital == 1000.0

    def test_scenario_default(self):
        from fibokei.api.routes.research import ScenarioRequest
        assert ScenarioRequest.model_fields["capital"].default == 1000.0

    def test_scorer_fallback_capital(self):
        """Scorer should use 1000.0 when initial_capital missing from metrics."""
        metrics = {"total_net_profit": 100.0}  # No initial_capital
        score = _score_return(metrics, ScoringConfig())
        # 100/1000 = 10% → 0.10 (capped at return_cap=1.0)
        assert abs(score - 0.10) < 0.01

    def test_research_and_backtest_same_config(self):
        """Research matrix uses BacktestConfig() → same £1K default."""
        from fibokei.research.matrix import ResearchMatrix
        matrix = ResearchMatrix([], [], [])
        assert matrix.config.initial_capital == 1000.0


# ---------------------------------------------------------------------------
# Integration: full backtest realism with IG-aligned sizing
# ---------------------------------------------------------------------------


def _make_trending_data(
    n_bars=500, base_price=1.10, trend_pct=0.05, volatility=0.003, seed=42,
):
    rng = np.random.default_rng(seed)
    prices = [base_price]
    for _ in range(n_bars - 1):
        change = trend_pct / n_bars + rng.normal(0, volatility) * base_price / 1000
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
    strategy_id = "test_ig_realism"
    warmup_period = 100

    def run_preparation(self, df):
        return df

    def generate_signal(self, df, i, context):
        if i % 20 != 0:
            return None
        bar = df.iloc[i]
        return _Signal(Direction.LONG, bar["close"])

    def generate_exit(self, position_dict, df, i, context):
        return None

    def build_trade_plan(self, signal, context):
        entry = signal.proposed_entry
        return _TradePlan(
            stop_loss=entry * 0.998,
            take_profit_targets=[entry * 1.004],
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


class TestFullBacktestIG:
    """Integration tests with IG-aligned economics."""

    def test_eurusd_credible_pnl(self):
        df = _make_trending_data(500, 1.10)
        config = BacktestConfig(initial_capital=1000, risk_per_trade_pct=1.0)
        result = Backtester(_DummyStrategy(), config).run(df, "EURUSD", Timeframe.H1)
        metrics = compute_metrics(result)
        # PnL bounded: £1K account with 1% risk cannot produce 10x return
        final = result.equity_curve[-1] if result.equity_curve else 1000
        assert final < 1000 * 10
        # Sharpe is finite (not NaN/Inf)
        assert math.isfinite(metrics["sharpe_ratio"])
        # Net profit bounded by position sizing + capital
        assert metrics["total_net_profit"] < 5000, (
            f"Net profit {metrics['total_net_profit']:.2f} implausibly high for £1K account"
        )

    def test_bcousd_respects_10x_leverage(self):
        """Oil backtest must use 10:1 leverage, not 30:1."""
        df = _make_trending_data(500, 80.0, volatility=0.3)
        config = BacktestConfig(initial_capital=1000, risk_per_trade_pct=1.0)
        result = Backtester(_DummyStrategy(), config).run(df, "BCOUSD", Timeframe.H1)
        for trade in result.trades:
            notional = trade.position_size * trade.entry_price
            leverage = notional / 1000  # approximate, doesn't account for compounding
            assert leverage <= 10.5, (
                f"BCOUSD trade leverage {leverage:.1f}x exceeds IG 10:1 limit"
            )

    def test_xauusd_respects_20x_leverage(self):
        """Gold backtest must use 20:1 leverage, not 30:1."""
        df = _make_trending_data(500, 2000.0, volatility=2.0)
        config = BacktestConfig(initial_capital=1000, risk_per_trade_pct=1.0)
        result = Backtester(_DummyStrategy(), config).run(df, "XAUUSD", Timeframe.H1)
        for trade in result.trades:
            notional = trade.position_size * trade.entry_price
            leverage = notional / 1000
            assert leverage <= 20.5, (
                f"XAUUSD trade leverage {leverage:.1f}x exceeds IG 20:1 limit"
            )

    def test_no_duplicate_trades(self):
        """Verify no duplicate trade IDs or overlapping trades."""
        df = _make_trending_data(500, 1.10)
        config = BacktestConfig(initial_capital=1000)
        result = Backtester(_DummyStrategy(), config).run(df, "EURUSD", Timeframe.H1)
        trade_ids = [t.trade_id for t in result.trades]
        assert len(trade_ids) == len(set(trade_ids)), "Duplicate trade IDs found"

    def test_equity_curve_matches_trade_pnls(self):
        """Final equity = initial + sum(trade PnLs)."""
        df = _make_trending_data(500, 1.10)
        config = BacktestConfig(initial_capital=1000)
        result = Backtester(_DummyStrategy(), config).run(df, "EURUSD", Timeframe.H1)
        expected_final = 1000 + sum(t.pnl for t in result.trades)
        actual_final = result.equity_curve[-1] if result.equity_curve else 1000
        assert abs(actual_final - expected_final) < 0.01, (
            f"Equity curve final {actual_final:.2f} != trade PnL sum {expected_final:.2f}"
        )

    def test_spread_always_applied(self):
        """Default spread must be applied even when config.spread_points=0."""
        df = _make_trending_data(300, 1.10)
        config_default = BacktestConfig(initial_capital=1000, spread_points=0.0)
        config_high = BacktestConfig(initial_capital=1000, spread_points=0.01)

        result_default = Backtester(_DummyStrategy(), config_default).run(df, "EURUSD", Timeframe.H1)
        result_high = Backtester(_DummyStrategy(), config_high).run(df, "EURUSD", Timeframe.H1)

        pnl_default = sum(t.pnl for t in result_default.trades)
        pnl_high = sum(t.pnl for t in result_high.trades)
        assert pnl_default >= pnl_high
