"""Candlestick pattern detection indicator."""

import numpy as np
import pandas as pd

from fibokei.indicators.base import Indicator


class CandlestickPatterns(Indicator):
    """Detects common candlestick patterns.

    Adds boolean columns for: bullish/bearish engulfing,
    bullish/bearish pin bars, and strong bullish/bearish closes.
    """

    @property
    def name(self) -> str:
        return "candlestick_patterns"

    @property
    def warmup_period(self) -> int:
        return 2  # Need previous bar for engulfing

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)

        o = df["open"]
        h = df["high"]
        l = df["low"]  # noqa: E741
        c = df["close"]

        body = (c - o).abs()
        range_size = h - l
        upper_wick = h - np.maximum(o, c)
        lower_wick = np.minimum(o, c) - l

        prev_o = o.shift(1)
        prev_c = c.shift(1)
        prev_body = (prev_c - prev_o).abs()

        # Bullish engulfing: current bullish body engulfs prior bearish body
        df["bullish_engulfing"] = (
            (c > o)  # current is bullish
            & (prev_c < prev_o)  # prior is bearish
            & (o <= prev_c)  # current open <= prior close
            & (c >= prev_o)  # current close >= prior open
            & (body > prev_body)  # current body larger
        )

        # Bearish engulfing: current bearish body engulfs prior bullish body
        df["bearish_engulfing"] = (
            (c < o)  # current is bearish
            & (prev_c > prev_o)  # prior is bullish
            & (o >= prev_c)  # current open >= prior close
            & (c <= prev_o)  # current close <= prior open
            & (body > prev_body)  # current body larger
        )

        # Bullish pin bar: small body in upper third, long lower wick (>2x body)
        df["bullish_pin_bar"] = (
            (lower_wick > 2 * body)
            & (upper_wick < body)
            & (np.minimum(o, c) > l + range_size * 0.66)
            & (range_size > 0)
        )

        # Bearish pin bar: small body in lower third, long upper wick (>2x body)
        df["bearish_pin_bar"] = (
            (upper_wick > 2 * body)
            & (lower_wick < body)
            & (np.maximum(o, c) < l + range_size * 0.33)
            & (range_size > 0)
        )

        # Strong bullish close: close in top 25% of range, body > 60% of range
        df["strong_bullish_close"] = (
            (c > o)
            & (c >= l + range_size * 0.75)
            & (body >= range_size * 0.60)
            & (range_size > 0)
        )

        # Strong bearish close: close in bottom 25% of range, body > 60% of range
        df["strong_bearish_close"] = (
            (c < o)
            & (c <= l + range_size * 0.25)
            & (body >= range_size * 0.60)
            & (range_size > 0)
        )

        # Fill NaN booleans with False
        bool_cols = [
            "bullish_engulfing", "bearish_engulfing",
            "bullish_pin_bar", "bearish_pin_bar",
            "strong_bullish_close", "strong_bearish_close",
        ]
        for col in bool_cols:
            df[col] = df[col].fillna(False)

        return df
