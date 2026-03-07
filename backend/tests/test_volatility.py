"""Tests for RollingVolatility indicator."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.volatility import RollingVolatility


def _make_df(n: int = 100, volatility: float = 0.001) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(42)
    close = 1.10 + np.cumsum(rng.normal(0, volatility, n))
    high = close + 0.001
    low = close - 0.001
    return pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close, "volume": 1000,
    }, index=ts)


class TestRollingVolatility:
    def test_name(self):
        rv = RollingVolatility()
        assert rv.name == "rolling_volatility"

    def test_warmup_period(self):
        rv = RollingVolatility(period=20)
        assert rv.warmup_period == 21

    def test_output_column(self):
        df = _make_df()
        rv = RollingVolatility()
        result = rv.compute(df)
        assert "rolling_vol" in result.columns

    def test_values_positive_after_warmup(self):
        df = _make_df()
        rv = RollingVolatility(period=20)
        result = rv.compute(df)
        valid = result["rolling_vol"].dropna()
        assert (valid > 0).all()

    def test_higher_volatility_data(self):
        df_low = _make_df(volatility=0.0005)
        df_high = _make_df(volatility=0.005)
        rv = RollingVolatility(period=20)
        r_low = rv.compute(df_low)
        r_high = rv.compute(df_high)
        # Higher vol data should have higher rolling_vol
        mean_low = r_low["rolling_vol"].dropna().mean()
        mean_high = r_high["rolling_vol"].dropna().mean()
        assert mean_high > mean_low

    def test_custom_period(self):
        df = _make_df()
        rv10 = RollingVolatility(period=10)
        rv30 = RollingVolatility(period=30)
        r10 = rv10.compute(df.copy())
        r30 = rv30.compute(df.copy())
        # Period 10 should have first valid value earlier than period 30
        first_valid_10 = r10["rolling_vol"].first_valid_index()
        first_valid_30 = r30["rolling_vol"].first_valid_index()
        assert first_valid_10 < first_valid_30
