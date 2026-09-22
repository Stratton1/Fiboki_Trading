"""Swing high/low detection indicator."""

import numpy as np
import pandas as pd

from fibokei.indicators.base import Indicator


class SwingDetector(Indicator):
    """Detects swing highs and lows using fractal logic.

    A swing high at bar i requires high[i] > all highs in
    [i-lookback, i+lookback] excluding i. Similarly for swing lows.
    """

    def __init__(self, lookback: int = 5):
        self.lookback = lookback

    @property
    def name(self) -> str:
        return "swing_detector"

    @property
    def warmup_period(self) -> int:
        return self.lookback * 2

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)

        high = df["high"].values
        low = df["low"].values
        n = len(df)
        lb = self.lookback

        swing_high = np.full(n, np.nan)
        swing_low = np.full(n, np.nan)

        for i in range(lb, n - lb):
            # Check swing high: high[i] must be strictly greater than
            # all highs in the window on both sides
            left_highs = high[i - lb : i]
            right_highs = high[i + 1 : i + lb + 1]
            if high[i] > left_highs.max() and high[i] > right_highs.max():
                swing_high[i] = high[i]

            # Check swing low: low[i] must be strictly less than
            # all lows in the window on both sides
            left_lows = low[i - lb : i]
            right_lows = low[i + 1 : i + lb + 1]
            if low[i] < left_lows.min() and low[i] < right_lows.min():
                swing_low[i] = low[i]

        df["swing_high"] = swing_high
        df["swing_low"] = swing_low

        # Forward-fill last known swing values
        df["last_swing_high"] = df["swing_high"].ffill()
        df["last_swing_low"] = df["swing_low"].ffill()

        return df
