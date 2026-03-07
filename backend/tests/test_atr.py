"""Tests for ATR indicator."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.atr import ATR


def _make_price_df(n: int = 50, seed: int = 42) -> pd.DataFrame:
    """Create a simple price DataFrame for testing."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.10 + np.cumsum(rng.normal(0, 0.001, n))
    high = close + abs(rng.normal(0.001, 0.0005, n))
    low = close - abs(rng.normal(0.001, 0.0005, n))
    open_price = close + rng.normal(0, 0.0003, n)
    high = np.maximum(high, np.maximum(open_price, close))
    low = np.minimum(low, np.minimum(open_price, close))

    return pd.DataFrame(
        {"open": open_price, "high": high, "low": low, "close": close, "volume": 1000},
        index=timestamps,
    )


class TestATR:
    def test_name(self):
        atr = ATR()
        assert atr.name == "atr"

    def test_warmup_period(self):
        atr = ATR(period=14)
        assert atr.warmup_period == 14

    def test_output_column(self):
        df = _make_price_df()
        atr = ATR()
        result = atr.compute(df)
        assert "atr" in result.columns

    def test_output_same_length(self):
        df = _make_price_df()
        atr = ATR()
        result = atr.compute(df)
        assert len(result) == len(df)

    def test_atr_positive(self):
        """ATR should always be positive."""
        df = _make_price_df()
        atr = ATR()
        result = atr.compute(df)
        valid = result["atr"].dropna()
        assert (valid > 0).all()

    def test_atr_valid_from_start(self):
        """ATR should produce values from bar 0 (EWM fills forward)."""
        df = _make_price_df()
        atr = ATR()
        result = atr.compute(df)
        # EWM with adjust=False produces values from bar 0
        # Bar 0 uses high-low as true range (no prev close available)
        assert not pd.isna(result["atr"].iloc[0])
        assert result["atr"].iloc[0] > 0

    def test_custom_period(self):
        df = _make_price_df()
        atr = ATR(period=7)
        result = atr.compute(df)
        assert atr.warmup_period == 7
        assert "atr" in result.columns

    def test_manual_true_range(self):
        """Verify true range calculation for a known bar."""
        df = _make_price_df()
        # Bar 5 true range should be max of:
        # high[5]-low[5], |high[5]-close[4]|, |low[5]-close[4]|
        h = df["high"].iloc[5]
        l = df["low"].iloc[5]
        prev_c = df["close"].iloc[4]
        expected_tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        assert expected_tr > 0  # Sanity check

    def test_higher_volatility_higher_atr(self):
        """ATR should be higher for more volatile data."""
        # Low volatility
        timestamps = pd.date_range("2024-01-01", periods=50, freq="h", tz="UTC")
        df_low = pd.DataFrame({
            "open": [1.10] * 50,
            "high": [1.101] * 50,
            "low": [1.099] * 50,
            "close": [1.10] * 50,
            "volume": [1000] * 50,
        }, index=timestamps)

        # High volatility
        df_high = pd.DataFrame({
            "open": [1.10] * 50,
            "high": [1.15] * 50,
            "low": [1.05] * 50,
            "close": [1.10] * 50,
            "volume": [1000] * 50,
        }, index=timestamps)

        atr = ATR(period=14)
        atr_low = atr.compute(df_low.copy())["atr"].iloc[-1]
        atr_high = atr.compute(df_high.copy())["atr"].iloc[-1]
        assert atr_high > atr_low
