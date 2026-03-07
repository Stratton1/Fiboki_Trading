"""Tests for CandlestickPatterns indicator."""

import numpy as np
import pandas as pd
import pytest

from fibokei.indicators.candles import CandlestickPatterns


def _make_candle_df(bars: list[tuple]) -> pd.DataFrame:
    """Create DataFrame from (open, high, low, close) tuples."""
    timestamps = pd.date_range("2024-01-01", periods=len(bars), freq="h", tz="UTC")
    data = {
        "open": [b[0] for b in bars],
        "high": [b[1] for b in bars],
        "low": [b[2] for b in bars],
        "close": [b[3] for b in bars],
    }
    return pd.DataFrame(data, index=timestamps)


class TestCandlestickPatterns:
    def test_name(self):
        cp = CandlestickPatterns()
        assert cp.name == "candlestick_patterns"

    def test_output_columns(self):
        df = _make_candle_df([(1.10, 1.11, 1.09, 1.10)] * 5)
        cp = CandlestickPatterns()
        result = cp.compute(df)
        expected_cols = [
            "bullish_engulfing", "bearish_engulfing",
            "bullish_pin_bar", "bearish_pin_bar",
            "strong_bullish_close", "strong_bearish_close",
        ]
        for col in expected_cols:
            assert col in result.columns

    def test_bullish_engulfing_detected(self):
        # Bar 0: bearish (open > close)
        # Bar 1: bullish, body engulfs bar 0's body
        bars = [
            (1.1050, 1.1060, 1.0990, 1.1000),  # bearish: open=1.105, close=1.100
            (1.0990, 1.1070, 1.0980, 1.1060),  # bullish: open=1.099, close=1.106, engulfs
        ]
        df = _make_candle_df(bars)
        result = CandlestickPatterns().compute(df)
        assert result["bullish_engulfing"].iloc[1] is True or result["bullish_engulfing"].iloc[1]

    def test_bearish_engulfing_detected(self):
        # Bar 0: bullish (close > open)
        # Bar 1: bearish, body engulfs bar 0's body
        bars = [
            (1.1000, 1.1060, 1.0990, 1.1050),  # bullish: open=1.100, close=1.105
            (1.1060, 1.1070, 1.0980, 1.0990),  # bearish: open=1.106, close=1.099, engulfs
        ]
        df = _make_candle_df(bars)
        result = CandlestickPatterns().compute(df)
        assert result["bearish_engulfing"].iloc[1]

    def test_bullish_pin_bar_detected(self):
        # Long lower wick, small body in upper part
        # open=1.1043, high=1.1050, low=1.1000, close=1.1047
        # body=0.0004, lower_wick=0.0043, upper_wick=0.0003
        bars = [
            (1.1043, 1.1050, 1.1000, 1.1047),
        ]
        df = _make_candle_df(bars)
        result = CandlestickPatterns().compute(df)
        assert result["bullish_pin_bar"].iloc[0]

    def test_bearish_pin_bar_detected(self):
        # Long upper wick, small body in lower part
        # open=1.1007, high=1.1050, low=1.1000, close=1.1003
        # body=0.0004, upper_wick=0.0043, lower_wick=0.0003
        bars = [
            (1.1007, 1.1050, 1.1000, 1.1003),
        ]
        df = _make_candle_df(bars)
        result = CandlestickPatterns().compute(df)
        assert result["bearish_pin_bar"].iloc[0]

    def test_strong_bullish_close_detected(self):
        # Close in top 25%, body > 60% of range
        # open=1.1005, high=1.1050, low=1.1000, close=1.1045
        # range=0.005, body=0.004 (80%), close at 90% of range
        bars = [
            (1.1005, 1.1050, 1.1000, 1.1045),
        ]
        df = _make_candle_df(bars)
        result = CandlestickPatterns().compute(df)
        assert result["strong_bullish_close"].iloc[0]

    def test_strong_bearish_close_detected(self):
        # Close in bottom 25%, body > 60% of range
        # open=1.1045, high=1.1050, low=1.1000, close=1.1005
        bars = [
            (1.1045, 1.1050, 1.1000, 1.1005),
        ]
        df = _make_candle_df(bars)
        result = CandlestickPatterns().compute(df)
        assert result["strong_bearish_close"].iloc[0]

    def test_no_false_positives_on_doji(self):
        # Doji: open ≈ close, roughly equal wicks
        bars = [
            (1.1025, 1.1050, 1.1000, 1.1025),
            (1.1025, 1.1050, 1.1000, 1.1025),
        ]
        df = _make_candle_df(bars)
        result = CandlestickPatterns().compute(df)
        # Doji shouldn't trigger engulfing or strong close
        assert not result["bullish_engulfing"].iloc[1]
        assert not result["bearish_engulfing"].iloc[1]
        assert not result["strong_bullish_close"].iloc[1]
        assert not result["strong_bearish_close"].iloc[1]
