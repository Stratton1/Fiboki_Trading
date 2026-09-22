"""Tests for SwingDetector indicator."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.swing import SwingDetector


def _make_swing_df():
    """Create price data with obvious swing highs and lows."""
    # Pattern: up-peak-down-trough-up-peak-down
    prices = [
        # Trough at ~index 5, Peak at ~index 10, Trough at ~index 15, Peak at ~index 20
        1.00, 1.01, 1.02, 1.03, 1.04,  # 0-4 rising
        1.00, 0.99, 0.98, 0.97, 0.96,  # 5-9 falling (trough near 5 would need lookback context)
        1.05, 1.06, 1.08, 1.10, 1.12,  # 10-14 rising
        1.13, 1.14, 1.15, 1.14, 1.13,  # 15-19 peak at 17
        1.10, 1.08, 1.06, 1.04, 1.02,  # 20-24 falling
        1.00, 0.98, 0.97, 0.98, 1.00,  # 25-29 trough at 27
        1.02, 1.04, 1.06, 1.08, 1.10,  # 30-34 rising
    ]
    n = len(prices)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "open": prices,
        "high": [p + 0.005 for p in prices],
        "low": [p - 0.005 for p in prices],
        "close": prices,
    }, index=timestamps)


class TestSwingDetector:
    def test_name(self):
        sd = SwingDetector()
        assert sd.name == "swing_detector"

    def test_warmup_period(self):
        sd = SwingDetector(lookback=5)
        assert sd.warmup_period == 10

    def test_output_columns(self):
        df = _make_swing_df()
        sd = SwingDetector(lookback=3)
        result = sd.compute(df)
        assert "swing_high" in result.columns
        assert "swing_low" in result.columns
        assert "last_swing_high" in result.columns
        assert "last_swing_low" in result.columns

    def test_detects_swing_high(self):
        df = _make_swing_df()
        sd = SwingDetector(lookback=3)
        result = sd.compute(df)
        # Should detect a swing high around index 17 (peak at 1.15+0.005)
        swing_highs = result["swing_high"].dropna()
        assert len(swing_highs) > 0

    def test_detects_swing_low(self):
        df = _make_swing_df()
        sd = SwingDetector(lookback=3)
        result = sd.compute(df)
        swing_lows = result["swing_low"].dropna()
        assert len(swing_lows) > 0

    def test_last_swing_high_forward_filled(self):
        df = _make_swing_df()
        sd = SwingDetector(lookback=3)
        result = sd.compute(df)
        # After first swing high is detected, last_swing_high should remain filled
        first_high_idx = result["swing_high"].first_valid_index()
        if first_high_idx is not None:
            pos = result.index.get_loc(first_high_idx)
            # All values after should be non-NaN
            after = result["last_swing_high"].iloc[pos:]
            assert not after.isna().any()

    def test_last_swing_low_forward_filled(self):
        df = _make_swing_df()
        sd = SwingDetector(lookback=3)
        result = sd.compute(df)
        first_low_idx = result["swing_low"].first_valid_index()
        if first_low_idx is not None:
            pos = result.index.get_loc(first_low_idx)
            after = result["last_swing_low"].iloc[pos:]
            assert not after.isna().any()

    def test_different_lookback(self):
        df = _make_swing_df()
        sd3 = SwingDetector(lookback=3)
        sd2 = SwingDetector(lookback=2)
        r3 = sd3.compute(df.copy())
        r2 = sd2.compute(df.copy())
        # Smaller lookback should detect more swings (less strict)
        n3 = r3["swing_high"].count() + r3["swing_low"].count()
        n2 = r2["swing_high"].count() + r2["swing_low"].count()
        assert n2 >= n3
