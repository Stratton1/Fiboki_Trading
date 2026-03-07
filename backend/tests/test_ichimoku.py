"""Tests for Ichimoku Cloud indicator."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.ichimoku import IchimokuCloud


def _make_price_df(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Create a simple price DataFrame for testing."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.10 + np.cumsum(rng.normal(0, 0.001, n))
    high = close + abs(rng.normal(0.001, 0.0005, n))
    low = close - abs(rng.normal(0.001, 0.0005, n))
    open_price = close + rng.normal(0, 0.0003, n)
    # Ensure OHLC consistency
    high = np.maximum(high, np.maximum(open_price, close))
    low = np.minimum(low, np.minimum(open_price, close))

    return pd.DataFrame(
        {"open": open_price, "high": high, "low": low, "close": close, "volume": 1000},
        index=timestamps,
    )


class TestIchimokuCloud:
    def test_name(self):
        ich = IchimokuCloud()
        assert ich.name == "ichimoku_cloud"

    def test_warmup_period_default(self):
        ich = IchimokuCloud()
        assert ich.warmup_period == 78  # 52 + 26

    def test_output_columns(self):
        df = _make_price_df()
        ich = IchimokuCloud()
        result = ich.compute(df)
        assert "tenkan_sen" in result.columns
        assert "kijun_sen" in result.columns
        assert "senkou_span_a" in result.columns
        assert "senkou_span_b" in result.columns
        assert "chikou_span" in result.columns

    def test_output_same_length(self):
        df = _make_price_df()
        ich = IchimokuCloud()
        result = ich.compute(df)
        assert len(result) == len(df)

    def test_warmup_produces_nan(self):
        df = _make_price_df()
        ich = IchimokuCloud()
        result = ich.compute(df)
        # Tenkan needs 9 bars, first 8 should be NaN
        assert result["tenkan_sen"].iloc[:8].isna().all()
        # Kijun needs 26 bars, first 25 should be NaN
        assert result["kijun_sen"].iloc[:24].isna().all()

    def test_valid_values_after_warmup(self):
        df = _make_price_df()
        ich = IchimokuCloud()
        result = ich.compute(df)
        # After full warmup, values should be valid
        valid = result.iloc[ich.warmup_period:]
        assert not valid["tenkan_sen"].isna().any()
        assert not valid["kijun_sen"].isna().any()

    def test_tenkan_manually(self):
        """Verify Tenkan calculation against manual computation."""
        df = _make_price_df()
        ich = IchimokuCloud(tenkan_period=9)
        result = ich.compute(df)

        # At bar 8 (index 8, 9th bar): tenkan = (max high[0:9] + min low[0:9]) / 2
        h9 = df["high"].iloc[:9].max()
        l9 = df["low"].iloc[:9].min()
        expected_tenkan = (h9 + l9) / 2
        assert abs(result["tenkan_sen"].iloc[8] - expected_tenkan) < 1e-10

    def test_kijun_manually(self):
        """Verify Kijun calculation against manual computation."""
        df = _make_price_df()
        ich = IchimokuCloud(kijun_period=26)
        result = ich.compute(df)

        # At bar 25 (26th bar)
        h26 = df["high"].iloc[:26].max()
        l26 = df["low"].iloc[:26].min()
        expected_kijun = (h26 + l26) / 2
        assert abs(result["kijun_sen"].iloc[25] - expected_kijun) < 1e-10

    def test_chikou_is_close_shifted_back(self):
        """Chikou Span should be close shifted backward by chikou_shift periods."""
        df = _make_price_df()
        ich = IchimokuCloud(chikou_shift=26)
        result = ich.compute(df)

        # Chikou at position i = close at position i+26
        for i in range(10, 50):
            if i + 26 < len(df):
                assert result["chikou_span"].iloc[i] == df["close"].iloc[i + 26]

    def test_custom_parameters(self):
        df = _make_price_df()
        ich = IchimokuCloud(tenkan_period=7, kijun_period=22, senkou_b_period=44, chikou_shift=22)
        result = ich.compute(df)
        assert ich.warmup_period == 66  # 44 + 22
        # Should still produce valid columns
        assert "tenkan_sen" in result.columns
        valid = result.iloc[ich.warmup_period:]
        assert not valid["tenkan_sen"].isna().any()

    def test_senkou_span_a_is_shifted_forward(self):
        """Senkou Span A at bar i should use tenkan/kijun from bar i-26."""
        df = _make_price_df()
        ich = IchimokuCloud()
        result = ich.compute(df)

        # Senkou A at index 60 should equal (tenkan[34] + kijun[34]) / 2
        idx = 60
        src = idx - ich.chikou_shift  # 34
        if not pd.isna(result["tenkan_sen"].iloc[src]) and not pd.isna(result["kijun_sen"].iloc[src]):
            expected = (result["tenkan_sen"].iloc[src] + result["kijun_sen"].iloc[src]) / 2
            assert abs(result["senkou_span_a"].iloc[idx] - expected) < 1e-10
