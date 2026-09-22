"""Moving-average indicators (centralised per architecture rules)."""

import pandas as pd

from fibokei.indicators.base import Indicator


class SMA(Indicator):
    """Simple Moving Average of the close. Adds column ``sma_{period}``."""

    def __init__(self, period: int = 20):
        if period < 1:
            raise ValueError("SMA period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"sma_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        df[self.name] = df["close"].rolling(self.period).mean()
        return df


class EMA(Indicator):
    """Exponential Moving Average of the close. Adds column ``ema_{period}``."""

    def __init__(self, period: int = 20):
        if period < 1:
            raise ValueError("EMA period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"ema_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        df[self.name] = df["close"].ewm(span=self.period, adjust=False).mean()
        return df


class RSI(Indicator):
    """Relative Strength Index (Wilder smoothing). Adds ``rsi_{period}``."""

    def __init__(self, period: int = 14):
        if period < 2:
            raise ValueError("RSI period must be >= 2")
        self.period = period

    @property
    def name(self) -> str:
        return f"rsi_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        delta = df["close"].diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.ewm(alpha=1.0 / self.period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / self.period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, float("nan"))
        df[self.name] = (100.0 - 100.0 / (1.0 + rs)).fillna(100.0)
        return df
