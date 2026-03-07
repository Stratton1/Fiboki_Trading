"""Average True Range (ATR) indicator implementation."""

import pandas as pd

from fibokei.indicators.base import Indicator


class ATR(Indicator):
    """Average True Range indicator.

    Measures market volatility using the exponential moving average
    of the True Range.
    """

    def __init__(self, period: int = 14):
        self.period = period

    @property
    def name(self) -> str:
        return "atr"

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)

        high = df["high"]
        low = df["low"]
        close = df["close"]

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        # ATR as EMA of True Range
        df["atr"] = tr.ewm(span=self.period, adjust=False).mean()

        return df
