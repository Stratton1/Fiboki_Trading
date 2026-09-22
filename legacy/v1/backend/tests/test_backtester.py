"""Tests for the backtesting engine."""

import numpy as np
import pandas as pd
import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.position import Position, calculate_position_size
from fibokei.core.models import Direction, Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.strategies.base import Strategy
from fibokei.strategies.bot01_sanyaku import PureSanyakuConfluence


class AlwaysLongAt100(Strategy):
    """Mock strategy that enters LONG at bar index 100 for testing."""

    @property
    def strategy_id(self):
        return "mock_long"

    @property
    def strategy_name(self):
        return "Mock Long"

    @property
    def strategy_family(self):
        return "test"

    def prepare_data(self, df):
        return df.copy()

    def compute_indicators(self, df):
        df["atr"] = 0.002
        return df

    def detect_market_regime(self, df, idx):
        return "trending_bullish"

    def detect_setup(self, df, idx, context):
        return idx == 100

    def generate_signal(self, df, idx, context):
        if idx != 100:
            return None
        close = df["close"].iloc[idx]
        return Signal(
            timestamp=df.index[idx],
            instrument=context.get("instrument", "TEST"),
            timeframe=context.get("timeframe", Timeframe.H1),
            strategy_id=self.strategy_id,
            direction=Direction.LONG,
            setup_type="test",
            proposed_entry=close,
            stop_loss=close - 0.01,
            take_profit_primary=close + 0.02,
            confidence_score=0.8,
            regime_label="trending_bullish",
        )

    def validate_signal(self, signal, context):
        return signal

    def build_trade_plan(self, signal, context):
        return TradePlan(
            entry_price=signal.proposed_entry,
            stop_loss=signal.stop_loss,
            take_profit_targets=[signal.take_profit_primary],
            max_bars_in_trade=50,
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context):
        return None

    def score_confidence(self, signal, context):
        return 0.8

    def explain_decision(self, context):
        return "Test"


def _make_test_df(n: int = 200) -> pd.DataFrame:
    """Create simple uptrending test data."""
    rng = np.random.default_rng(42)
    close = 1.10 + np.linspace(0, 0.05, n) + rng.normal(0, 0.001, n)
    high = close + 0.002
    low = close - 0.002
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": 1000,
    }, index=ts)


class TestPositionSizing:
    def test_basic_position_size(self):
        # 10000 capital, 1% risk = $100 risk
        # entry=1.10, stop=1.09, risk_per_unit=0.01
        # size = 100 / 0.01 = 10000 units
        size = calculate_position_size(10000, 1.0, 1.10, 1.09)
        assert abs(size - 10000.0) < 0.01

    def test_zero_risk_distance(self):
        size = calculate_position_size(10000, 1.0, 1.10, 1.10)
        assert size == 0.0

    def test_larger_risk_pct(self):
        size_1pct = calculate_position_size(10000, 1.0, 1.10, 1.09)
        size_2pct = calculate_position_size(10000, 2.0, 1.10, 1.09)
        assert abs(size_2pct - 2 * size_1pct) < 0.01


class TestPosition:
    def test_stop_loss_hit_long(self):
        pos = Position(
            strategy_id="test", instrument="EURUSD", timeframe=Timeframe.H1,
            direction=Direction.LONG, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=1.10, stop_loss=1.09, take_profit_targets=[1.12],
            position_size=10000,
        )
        bar = pd.Series({"high": 1.105, "low": 1.088, "close": 1.09})
        reason = pos.update(bar)
        assert reason == ExitReason.STOP_LOSS_HIT

    def test_take_profit_hit_long(self):
        pos = Position(
            strategy_id="test", instrument="EURUSD", timeframe=Timeframe.H1,
            direction=Direction.LONG, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=1.10, stop_loss=1.09, take_profit_targets=[1.12],
            position_size=10000,
        )
        bar = pd.Series({"high": 1.125, "low": 1.10, "close": 1.12})
        reason = pos.update(bar)
        assert reason == ExitReason.TAKE_PROFIT_HIT

    def test_stop_checked_before_tp(self):
        """If both SL and TP hit in same bar, SL wins (conservative)."""
        pos = Position(
            strategy_id="test", instrument="EURUSD", timeframe=Timeframe.H1,
            direction=Direction.LONG, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=1.10, stop_loss=1.09, take_profit_targets=[1.12],
            position_size=10000,
        )
        # Wide bar hitting both
        bar = pd.Series({"high": 1.13, "low": 1.08, "close": 1.10})
        reason = pos.update(bar)
        assert reason == ExitReason.STOP_LOSS_HIT

    def test_time_stop(self):
        pos = Position(
            strategy_id="test", instrument="EURUSD", timeframe=Timeframe.H1,
            direction=Direction.LONG, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=1.10, stop_loss=1.05, take_profit_targets=[1.20],
            position_size=10000, max_bars_in_trade=3,
        )
        bar = pd.Series({"high": 1.105, "low": 1.095, "close": 1.10})
        assert pos.update(bar) is None
        assert pos.update(bar) is None
        assert pos.update(bar) == ExitReason.TIME_STOP_EXIT

    def test_close_produces_trade_result(self):
        pos = Position(
            strategy_id="test", instrument="EURUSD", timeframe=Timeframe.H1,
            direction=Direction.LONG, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=1.10, stop_loss=1.09, take_profit_targets=[1.12],
            position_size=10000,
        )
        result = pos.close(1.12, pd.Timestamp("2024-01-02", tz="UTC"), ExitReason.TAKE_PROFIT_HIT)
        assert result.pnl == pytest.approx(200.0)  # (1.12 - 1.10) * 10000
        assert result.exit_reason == ExitReason.TAKE_PROFIT_HIT
        assert not pos.is_open

    def test_mfe_mae_tracking(self):
        pos = Position(
            strategy_id="test", instrument="EURUSD", timeframe=Timeframe.H1,
            direction=Direction.LONG, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
            entry_price=1.10, stop_loss=1.05, take_profit_targets=[1.20],
            position_size=10000,
        )
        pos.update(pd.Series({"high": 1.115, "low": 1.095, "close": 1.11}))
        assert pos.max_favorable_excursion == pytest.approx(0.015)
        assert pos.max_adverse_excursion == pytest.approx(0.005)


class TestBacktester:
    def test_mock_strategy_produces_trade(self):
        df = _make_test_df()
        bt = Backtester(AlwaysLongAt100())
        result = bt.run(df, "TEST", Timeframe.H1)
        assert len(result.trades) >= 1
        assert result.trades[0].direction == Direction.LONG

    def test_equity_curve_length(self):
        df = _make_test_df()
        bt = Backtester(AlwaysLongAt100())
        result = bt.run(df, "TEST", Timeframe.H1)
        assert len(result.equity_curve) == result.total_bars

    def test_spread_adjustment(self):
        df = _make_test_df()
        config = BacktestConfig(spread_points=0.002)
        bt = Backtester(AlwaysLongAt100(), config)
        result = bt.run(df, "TEST", Timeframe.H1)
        if result.trades:
            # Entry should be higher than bar close (LONG + spread)
            bar_close = df["close"].iloc[100]
            assert result.trades[0].entry_price > bar_close

    def test_bot01_on_sample_data(self, sample_eurusd_h1_path):
        from fibokei.data.loader import load_ohlcv_csv

        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        bt = Backtester(PureSanyakuConfluence())
        result = bt.run(df, "EURUSD", Timeframe.H1)
        assert result.strategy_id == "bot01_sanyaku"
        assert result.instrument == "EURUSD"
        assert len(result.equity_curve) > 0
        assert result.total_bars > 0
