"""Oscillator indicators (centralised per architecture rules).

MACD, Stochastic, CCI and Rate-of-Change. All deterministic and computed on
closed candles only (rolling / ewm / backward-shift — never read future bars).
"""

import pandas as pd

from fibokei.indicators.base import Indicator


class MACD(Indicator):
    """Moving Average Convergence Divergence.

    Adds: ``macd_line``, ``macd_signal``, ``macd_hist``.
    """

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        if not (0 < fast < slow):
            raise ValueError("MACD requires 0 < fast < slow")
        if signal < 1:
            raise ValueError("MACD signal period must be >= 1")
        self.fast, self.slow, self.signal = fast, slow, signal

    @property
    def name(self) -> str:
        return f"macd_{self.fast}_{self.slow}_{self.signal}"

    @property
    def warmup_period(self) -> int:
        return self.slow + self.signal

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        fast_ema = df["close"].ewm(span=self.fast, adjust=False).mean()
        slow_ema = df["close"].ewm(span=self.slow, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=self.signal, adjust=False).mean()
        df["macd_line"] = macd_line
        df["macd_signal"] = signal_line
        df["macd_hist"] = macd_line - signal_line
        return df


class Stochastic(Indicator):
    """Stochastic Oscillator (slow). Adds ``stoch_k``, ``stoch_d``."""

    def __init__(self, k_period: int = 14, smooth: int = 3, d_period: int = 3):
        if min(k_period, smooth, d_period) < 1:
            raise ValueError("Stochastic periods must be >= 1")
        self.k_period, self.smooth, self.d_period = k_period, smooth, d_period

    @property
    def name(self) -> str:
        return f"stoch_{self.k_period}_{self.smooth}_{self.d_period}"

    @property
    def warmup_period(self) -> int:
        return self.k_period + self.smooth + self.d_period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        low_n = df["low"].rolling(self.k_period).min()
        high_n = df["high"].rolling(self.k_period).max()
        rng = (high_n - low_n).replace(0.0, float("nan"))
        raw_k = 100.0 * (df["close"] - low_n) / rng
        k = raw_k.rolling(self.smooth).mean()
        df["stoch_k"] = k
        df["stoch_d"] = k.rolling(self.d_period).mean()
        return df


class CCI(Indicator):
    """Commodity Channel Index. Adds ``cci_{period}``."""

    def __init__(self, period: int = 20):
        if period < 2:
            raise ValueError("CCI period must be >= 2")
        self.period = period

    @property
    def name(self) -> str:
        return f"cci_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        sma = tp.rolling(self.period).mean()
        mad = tp.rolling(self.period).apply(
            lambda x: (abs(x - x.mean())).mean(), raw=True
        )
        df[self.name] = (tp - sma) / (0.015 * mad.replace(0.0, float("nan")))
        return df


class ROC(Indicator):
    """Rate of Change / momentum (%). Adds ``roc_{period}``."""

    def __init__(self, period: int = 10):
        if period < 1:
            raise ValueError("ROC period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"roc_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        prev = df["close"].shift(self.period)
        df[self.name] = 100.0 * (df["close"] - prev) / prev.replace(0.0, float("nan"))
        return df
