"""Tests for Fibonacci indicators."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.fibonacci import (
    FibonacciExtension,
    FibonacciRetracement,
    FibonacciTimeZones,
)
from fibokei.indicators.swing import SwingDetector


def _make_swing_df(n: int = 100) -> pd.DataFrame:
    """Create price data with clear swings for Fibonacci testing."""
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(42)
    # Zigzag: rise → peak at 25 → trough at 50 → peak at 75 → decline
    close = np.ones(n) * 1.10
    for i in range(1, 25):
        close[i] = close[i - 1] + 0.002 + rng.normal(0, 0.0003)
    for i in range(25, 50):
        close[i] = close[i - 1] - 0.002 + rng.normal(0, 0.0003)
    for i in range(50, 75):
        close[i] = close[i - 1] + 0.002 + rng.normal(0, 0.0003)
    for i in range(75, n):
        close[i] = close[i - 1] - 0.002 + rng.normal(0, 0.0003)

    high = close + abs(rng.normal(0.0015, 0.0003, n))
    low = close - abs(rng.normal(0.0015, 0.0003, n))
    high = np.maximum(high, close)
    low = np.minimum(low, close)

    df = pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": 1000,
    }, index=ts)
    # Add swing columns
    df = SwingDetector(lookback=3).compute(df)
    return df


class TestFibonacciRetracement:
    def test_name(self):
        fib = FibonacciRetracement()
        assert fib.name == "fibonacci_retracement"

    def test_compute_levels_known_values(self):
        fib = FibonacciRetracement()
        levels = fib.compute_levels(1.1500, 1.1000)
        # 0.0 = swing_high (top), 1.0 = swing_low (bottom)
        assert levels["0.0"] == pytest.approx(1.1500)
        assert levels["1.0"] == pytest.approx(1.1000)
        assert levels["0.5"] == pytest.approx(1.1250)
        assert levels["0.382"] == pytest.approx(1.1500 - 0.382 * 0.05)
        assert levels["0.618"] == pytest.approx(1.1500 - 0.618 * 0.05)
        assert levels["0.236"] == pytest.approx(1.1500 - 0.236 * 0.05)

    def test_compute_levels_all_keys(self):
        fib = FibonacciRetracement()
        levels = fib.compute_levels(1.20, 1.10)
        assert set(levels.keys()) == {"0.0", "0.236", "0.382", "0.5", "0.618", "0.786", "1.0"}

    def test_compute_adds_columns(self):
        df = _make_swing_df()
        fib = FibonacciRetracement()
        result = fib.compute(df)
        expected_cols = ["fib_00", "fib_0236", "fib_0382", "fib_05", "fib_0618", "fib_0786", "fib_10"]
        for col in expected_cols:
            assert col in result.columns

    def test_levels_between_swings(self):
        df = _make_swing_df()
        fib = FibonacciRetracement()
        result = fib.compute(df)
        # Where we have valid levels, they should be between last_swing_low and last_swing_high
        valid = result.dropna(subset=["fib_05"])
        if len(valid) > 0:
            row = valid.iloc[-1]
            assert row["fib_00"] >= row["fib_10"]  # 0.0 (high) >= 1.0 (low)
            assert row["fib_05"] >= row["fib_10"]
            assert row["fib_05"] <= row["fib_00"]


class TestFibonacciExtension:
    def test_name(self):
        ext = FibonacciExtension()
        assert ext.name == "fibonacci_extension"

    def test_bullish_extensions(self):
        ext = FibonacciExtension()
        # A=1.10, B=1.15, C=1.12 (bullish: B > A, pullback to C)
        levels = ext.compute_extensions(1.10, 1.15, 1.12)
        wave = 0.05  # B - A
        assert levels["1.0"] == pytest.approx(1.12 + 1.0 * wave)  # 1.17
        assert levels["1.618"] == pytest.approx(1.12 + 1.618 * wave)  # 1.2009
        assert levels["2.618"] == pytest.approx(1.12 + 2.618 * wave)  # 1.2509

    def test_bearish_extensions(self):
        ext = FibonacciExtension()
        # A=1.15, B=1.10, C=1.13 (bearish: B < A, pullback to C)
        levels = ext.compute_extensions(1.15, 1.10, 1.13)
        wave = 0.05
        assert levels["1.0"] == pytest.approx(1.13 - 1.0 * wave)  # 1.08
        assert levels["1.618"] == pytest.approx(1.13 - 1.618 * wave)


class TestFibonacciTimeZones:
    def test_name(self):
        tz = FibonacciTimeZones()
        assert tz.name == "fibonacci_time_zones"

    def test_compute_time_zones(self):
        tz = FibonacciTimeZones()
        zones = tz.compute_time_zones(10, 100)
        # anchor=10, should produce 10+1=11, 10+2=12, ..., 10+89=99
        assert 11 in zones
        assert 12 in zones
        assert 13 in zones
        assert 15 in zones  # 10+5
        assert 18 in zones  # 10+8
        # 10+144=154 > 100, should be excluded
        assert 154 not in zones

    def test_compute_adds_column(self):
        df = _make_swing_df()
        tz = FibonacciTimeZones()
        result = tz.compute(df)
        assert "fib_time_zone" in result.columns
        assert result["fib_time_zone"].dtype == bool

    def test_some_bars_marked(self):
        df = _make_swing_df()
        tz = FibonacciTimeZones()
        result = tz.compute(df)
        # With swings present, some bars should be marked as time zones
        assert result["fib_time_zone"].sum() > 0
