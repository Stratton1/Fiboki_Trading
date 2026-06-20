"""Pivot point levels (centralised). Closed-candle only.

Classic floor pivots derived from the PRIOR completed bar's H/L/C (shifted
back one bar), so no value depends on the current/forming bar — safe for any
timeframe and free of look-ahead.
"""

import pandas as pd

from fibokei.indicators.base import Indicator


class PivotPoints(Indicator):
    """Adds ``pivot``, ``pivot_r1``, ``pivot_s1``, ``pivot_r2``, ``pivot_s2``."""

    @property
    def name(self) -> str:
        return "pivot"

    @property
    def warmup_period(self) -> int:
        return 2

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        h = df["high"].shift(1)
        low = df["low"].shift(1)
        c = df["close"].shift(1)
        p = (h + low + c) / 3.0
        df["pivot"] = p
        df["pivot_r1"] = 2.0 * p - low
        df["pivot_s1"] = 2.0 * p - h
        df["pivot_r2"] = p + (h - low)
        df["pivot_s2"] = p - (h - low)
        return df
