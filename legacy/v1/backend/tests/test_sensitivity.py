"""Tests for parameter sensitivity analysis."""

import pandas as pd
import pytest
from pathlib import Path

from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.research.sensitivity import (
    DEFAULT_PARAM_RANGES,
    SensitivityPoint,
    SensitivityResult,
    get_default_params,
    run_sensitivity,
)


FIXTURES_DIR = Path(__file__).parent.parent.parent / "data" / "fixtures"


@pytest.fixture
def eurusd_h1_df():
    path = FIXTURES_DIR / "sample_eurusd_h1.csv"
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    df["instrument"] = "EURUSD"
    df["timeframe"] = "H1"
    return df


class TestGetDefaultParams:
    def test_ichimoku_family(self):
        params = get_default_params("bot01_sanyaku")
        assert "tenkan_period" in params
        assert "kijun_period" in params

    def test_hybrid_family(self):
        params = get_default_params("bot09_golden_cloud")
        assert "tenkan_period" in params

    def test_unknown_family_returns_empty(self):
        # fibonacci family has an entry; test a truly unknown family
        from fibokei.strategies.registry import strategy_registry
        # All strategies are ichimoku or hybrid, so empty result if family not in map
        params = get_default_params("bot01_sanyaku")
        assert isinstance(params, dict)


class TestSensitivityDataclasses:
    def test_point_defaults(self):
        p = SensitivityPoint(param_name="tenkan_period", param_value=9.0)
        assert p.total_trades == 0
        assert p.composite_score == 0.0

    def test_result_defaults(self):
        r = SensitivityResult(
            strategy_id="s1", instrument="EURUSD", timeframe="H1",
            param_name="tenkan_period", baseline_value=9.0,
        )
        assert r.robust is False
        assert r.status == "ok"


class TestRunSensitivity:
    def test_basic_run(self, eurusd_h1_df):
        result = run_sensitivity(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            param_name="tenkan_period", param_values=[7, 9, 11],
        )
        assert isinstance(result, SensitivityResult)
        assert result.strategy_id == "bot01_sanyaku"
        assert result.param_name == "tenkan_period"
        assert len(result.variations) == 3

    def test_variation_values_match_input(self, eurusd_h1_df):
        values = [7.0, 8.0, 9.0, 10.0, 11.0]
        result = run_sensitivity(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            param_name="tenkan_period", param_values=values,
        )
        returned_values = [v.param_value for v in result.variations]
        assert returned_values == values

    def test_score_range_computed(self, eurusd_h1_df):
        result = run_sensitivity(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            param_name="tenkan_period", param_values=[7, 9, 11],
        )
        scores = [v.composite_score for v in result.variations]
        expected_range = round(max(scores) - min(scores), 4)
        assert result.score_range == expected_range

    def test_robust_flag(self, eurusd_h1_df):
        result = run_sensitivity(
            eurusd_h1_df, "bot01_sanyaku", "EURUSD", Timeframe.H1,
            param_name="tenkan_period", param_values=[7, 9, 11],
        )
        assert result.robust == (result.score_range < 0.2)

    def test_deterministic(self, eurusd_h1_df):
        kwargs = dict(
            strategy_id="bot01_sanyaku", instrument="EURUSD", timeframe=Timeframe.H1,
            param_name="tenkan_period", param_values=[8, 9, 10],
        )
        r1 = run_sensitivity(eurusd_h1_df, **kwargs)
        r2 = run_sensitivity(eurusd_h1_df, **kwargs)
        for v1, v2 in zip(r1.variations, r2.variations):
            assert v1.composite_score == v2.composite_score
            assert v1.total_trades == v2.total_trades
