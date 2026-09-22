"""Tests for walk-forward analysis engine."""

import pandas as pd
import pytest
from pathlib import Path

from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.research.walk_forward import (
    WalkForwardResult,
    WalkForwardWindow,
    run_walk_forward,
)


FIXTURES_DIR = Path(__file__).parent.parent.parent / "data" / "fixtures"


@pytest.fixture
def eurusd_h1_df():
    """Load EURUSD H1 fixture data."""
    path = FIXTURES_DIR / "sample_eurusd_h1.csv"
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    df["instrument"] = "EURUSD"
    df["timeframe"] = "H1"
    return df


class TestWalkForwardDataclasses:
    def test_window_defaults(self):
        w = WalkForwardWindow(
            window_index=0, train_start="2020-01-01", train_end="2020-06-01",
            test_start="2020-06-01", test_end="2020-09-01",
            train_bars=1000, test_bars=500,
        )
        assert w.train_trades == 0
        assert w.test_score == 0.0

    def test_result_defaults(self):
        r = WalkForwardResult(
            strategy_id="s1", instrument="EURUSD", timeframe="H1",
            train_window_bars=2000, test_window_bars=500, step_bars=500,
            total_windows=0,
        )
        assert r.avg_test_score == 0.0
        assert r.status == "ok"


class TestRunWalkForward:
    def test_basic_run(self, eurusd_h1_df):
        """Walk-forward runs and returns structured result."""
        total_bars = len(eurusd_h1_df)
        # Use small windows to guarantee at least one window
        train = total_bars // 3
        test = total_bars // 6
        result = run_walk_forward(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            train_window_bars=train, test_window_bars=test, step_bars=test,
        )
        assert isinstance(result, WalkForwardResult)
        assert result.strategy_id == "bot01_sanyaku"
        assert result.instrument == "EURUSD"
        assert result.timeframe == "H1"
        assert result.total_windows >= 1
        assert len(result.windows) == result.total_windows

    def test_window_indices_sequential(self, eurusd_h1_df):
        total_bars = len(eurusd_h1_df)
        train = total_bars // 3
        test = total_bars // 6
        result = run_walk_forward(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            train_window_bars=train, test_window_bars=test, step_bars=test,
        )
        for i, w in enumerate(result.windows):
            assert w.window_index == i

    def test_too_little_data_gives_zero_windows(self, eurusd_h1_df):
        """If data is smaller than train + test, no windows are produced."""
        result = run_walk_forward(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            train_window_bars=len(eurusd_h1_df), test_window_bars=100,
        )
        assert result.total_windows == 0
        assert result.avg_test_score == 0.0

    def test_score_degradation_computed(self, eurusd_h1_df):
        total_bars = len(eurusd_h1_df)
        train = total_bars // 3
        test = total_bars // 6
        result = run_walk_forward(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            train_window_bars=train, test_window_bars=test, step_bars=test,
        )
        if result.total_windows > 0:
            avg_train = sum(w.train_score for w in result.windows) / len(result.windows)
            expected_deg = round(avg_train - result.avg_test_score, 4)
            assert result.score_degradation == expected_deg

    def test_deterministic(self, eurusd_h1_df):
        total_bars = len(eurusd_h1_df)
        train = total_bars // 3
        test = total_bars // 6
        kwargs = dict(
            strategy_id="bot01_sanyaku", instrument="EURUSD", timeframe=Timeframe.H1,
            train_window_bars=train, test_window_bars=test, step_bars=test,
        )
        r1 = run_walk_forward(eurusd_h1_df, **kwargs)
        r2 = run_walk_forward(eurusd_h1_df, **kwargs)
        assert r1.avg_test_score == r2.avg_test_score
        assert r1.total_test_trades == r2.total_test_trades
