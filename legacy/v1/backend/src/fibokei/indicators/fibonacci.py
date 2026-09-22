"""Fibonacci retracement, extension, and time zone indicators."""

import numpy as np
import pandas as pd

from fibokei.indicators.base import Indicator

RETRACEMENT_RATIOS = {
    "0.0": 0.0,
    "0.236": 0.236,
    "0.382": 0.382,
    "0.5": 0.5,
    "0.618": 0.618,
    "0.786": 0.786,
    "1.0": 1.0,
}

EXTENSION_RATIOS = {
    "1.0": 1.0,
    "1.272": 1.272,
    "1.618": 1.618,
    "2.0": 2.0,
    "2.618": 2.618,
}

FIB_TIME_NUMBERS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]


class FibonacciRetracement(Indicator):
    """Fibonacci retracement levels based on swing highs/lows.

    Requires swing_high and swing_low columns from SwingDetector.
    """

    @property
    def name(self) -> str:
        return "fibonacci_retracement"

    @property
    def warmup_period(self) -> int:
        return 20  # Need swings to be established

    @property
    def required_columns(self) -> list[str]:
        return ["high", "low", "close", "last_swing_high", "last_swing_low"]

    def compute_levels(
        self, swing_high: float, swing_low: float
    ) -> dict[str, float]:
        """Compute retracement price levels between swing_high and swing_low.

        Level 0.0 = swing_high (top), level 1.0 = swing_low (bottom).
        Retracement levels are measured from the top downward.
        """
        diff = swing_high - swing_low
        return {k: swing_high - v * diff for k, v in RETRACEMENT_RATIOS.items()}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)

        n = len(df)
        cols = {
            f"fib_{k.replace('.', '')}": np.full(n, np.nan)
            for k in RETRACEMENT_RATIOS
        }

        for i in range(n):
            sh = df["last_swing_high"].iloc[i]
            sl = df["last_swing_low"].iloc[i]
            if pd.isna(sh) or pd.isna(sl) or sh <= sl:
                continue
            levels = self.compute_levels(sh, sl)
            for k, v in levels.items():
                col_name = f"fib_{k.replace('.', '')}"
                cols[col_name][i] = v

        for col_name, values in cols.items():
            df[col_name] = values

        return df


class FibonacciExtension(Indicator):
    """Fibonacci extension levels for profit targets."""

    @property
    def name(self) -> str:
        return "fibonacci_extension"

    @property
    def warmup_period(self) -> int:
        return 30

    def compute_extensions(
        self, swing_a: float, swing_b: float, swing_c: float
    ) -> dict[str, float]:
        """Compute extension levels from an A-B-C wave.

        For bullish (A < B > C): extensions project upward from C.
        For bearish (A > B < C): extensions project downward from C.
        """
        wave_size = abs(swing_b - swing_a)
        bullish = swing_b > swing_a

        result = {}
        for k, ratio in EXTENSION_RATIOS.items():
            if bullish:
                result[k] = swing_c + ratio * wave_size
            else:
                result[k] = swing_c - ratio * wave_size
        return result

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        # Extension levels require A-B-C wave identification
        # This is computed on-demand by strategies rather than as columns
        return df


class FibonacciTimeZones(Indicator):
    """Fibonacci time zone projections."""

    @property
    def name(self) -> str:
        return "fibonacci_time_zones"

    @property
    def warmup_period(self) -> int:
        return 10

    @property
    def required_columns(self) -> list[str]:
        return ["high", "low", "close", "swing_high", "swing_low"]

    def compute_time_zones(
        self, anchor_bar_idx: int, total_bars: int
    ) -> list[int]:
        """Return bar indices at Fibonacci intervals from anchor."""
        zones = []
        for fib_num in FIB_TIME_NUMBERS:
            idx = anchor_bar_idx + fib_num
            if idx < total_bars:
                zones.append(idx)
        return zones

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)

        n = len(df)
        fib_tz = np.zeros(n, dtype=bool)

        # Project time zones from each confirmed swing point
        for i in range(n):
            is_swing = not pd.isna(df["swing_high"].iloc[i]) or not pd.isna(
                df["swing_low"].iloc[i]
            )
            if is_swing:
                zones = self.compute_time_zones(i, n)
                for z in zones:
                    fib_tz[z] = True

        df["fib_time_zone"] = fib_tz
        return df
