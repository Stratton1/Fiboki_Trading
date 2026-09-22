"""Tests for out-of-sample testing module."""

import pandas as pd
import pytest
from pathlib import Path

from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.research.oos import OOSSplitResult, run_oos_test


FIXTURES_DIR = Path(__file__).parent.parent.parent / "data" / "fixtures"


@pytest.fixture
def eurusd_h1_df():
    path = FIXTURES_DIR / "sample_eurusd_h1.csv"
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    df["instrument"] = "EURUSD"
    df["timeframe"] = "H1"
    return df


class TestOOSSplitResult:
    def test_defaults(self):
        r = OOSSplitResult(
            strategy_id="s1", instrument="EURUSD", timeframe="H1",
            split_ratio=0.7, split_bar_index=700,
            in_sample_bars=700, out_of_sample_bars=300,
        )
        assert r.robust is False
        assert r.status == "ok"
        assert r.score_degradation == 0.0


class TestRunOOSTest:
    def test_basic_run(self, eurusd_h1_df):
        result = run_oos_test(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            split_ratio=0.7,
        )
        assert isinstance(result, OOSSplitResult)
        assert result.strategy_id == "bot01_sanyaku"
        assert result.split_ratio == 0.7
        assert result.status == "ok"

    def test_split_correctness(self, eurusd_h1_df):
        total = len(eurusd_h1_df)
        result = run_oos_test(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            split_ratio=0.7,
        )
        expected_split = int(total * 0.7)
        assert result.split_bar_index == expected_split
        assert result.in_sample_bars == expected_split
        assert result.out_of_sample_bars == total - expected_split
        assert result.in_sample_bars + result.out_of_sample_bars == total

    def test_different_split_ratios(self, eurusd_h1_df):
        total = len(eurusd_h1_df)
        r5 = run_oos_test(eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1, split_ratio=0.5)
        r8 = run_oos_test(eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1, split_ratio=0.8)
        assert r5.in_sample_bars < r8.in_sample_bars
        assert r5.out_of_sample_bars > r8.out_of_sample_bars

    def test_score_degradation_sign(self, eurusd_h1_df):
        result = run_oos_test(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
        )
        # Degradation = IS score - OOS score
        expected = round(result.is_score - result.oos_score, 4)
        assert result.score_degradation == expected

    def test_robust_flag_logic(self, eurusd_h1_df):
        result = run_oos_test(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
        )
        if result.is_score > 0:
            assert result.robust == (result.oos_score >= result.is_score * 0.5)

    def test_deterministic(self, eurusd_h1_df):
        r1 = run_oos_test(eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1)
        r2 = run_oos_test(eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1)
        assert r1.is_score == r2.is_score
        assert r1.oos_score == r2.oos_score
        assert r1.is_trades == r2.is_trades
        assert r1.oos_trades == r2.oos_trades

    def test_timestamps_populated(self, eurusd_h1_df):
        result = run_oos_test(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
        )
        assert result.in_sample_start != ""
        assert result.in_sample_end != ""
        assert result.out_of_sample_start != ""
        assert result.out_of_sample_end != ""
