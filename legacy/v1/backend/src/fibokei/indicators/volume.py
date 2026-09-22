"""Volume-based indicators (centralised). Closed-candle only.

Volume is unreliable on FX (often tick-count or zero), so these degrade
gracefully and strategies built on them are marked ``research_limited``.
"""

import numpy as np
import pandas as pd

from fibokei.indicators.base import Indicator

_VOL_COLS = ["open", "high", "low", "close", "volume"]


class VWAP(Indicator):
    """Rolling Volume-Weighted Average Price. Adds ``vwap``.

    Falls back to a rolling mean of typical price when volume is absent/zero.
    """

    def __init__(self, period: int = 20):
        if period < 1:
            raise ValueError("VWAP period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"vwap_{self.period}"

    @property
    def required_columns(self) -> list[str]:
        return _VOL_COLS

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"].fillna(0.0)
        pv = (tp * vol).rolling(self.period).sum()
        vsum = vol.rolling(self.period).sum()
        vwap = pv / vsum.replace(0.0, float("nan"))
        # Graceful fallback where volume is zero/absent (e.g. FX feeds).
        df[self.name] = vwap.fillna(tp.rolling(self.period).mean())
        return df


class VolumeMA(Indicator):
    """Volume moving average. Adds ``vol_ma_{period}``."""

    def __init__(self, period: int = 20):
        if period < 1:
            raise ValueError("VolumeMA period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"vol_ma_{self.period}"

    @property
    def required_columns(self) -> list[str]:
        return _VOL_COLS

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        df[self.name] = df["volume"].fillna(0.0).rolling(self.period).mean()
        return df


class OBV(Indicator):
    """On-Balance Volume. Adds ``obv``."""

    @property
    def name(self) -> str:
        return "obv"

    @property
    def required_columns(self) -> list[str]:
        return _VOL_COLS

    @property
    def warmup_period(self) -> int:
        return 2

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        direction = np.sign(df["close"].diff().fillna(0.0))
        df["obv"] = (direction * df["volume"].fillna(0.0)).cumsum()
        return df
