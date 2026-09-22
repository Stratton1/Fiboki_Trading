"""Momentum / oscillator indicators. All strictly causal."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fiboki.indicators.base import Indicator, safe_divide, wilder_smooth


class RSI(Indicator):
    """Wilder's relative strength index.

    Seeded with the simple mean of the first ``period`` gains and losses, then
    Wilder-smoothed. A zero average loss yields RSI = 100 by definition, which
    we set explicitly instead of letting a division produce ``inf``.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("RSI period must be >= 2")
        self.period = period

    @property
    def name(self) -> str:
        return f"rsi_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {"period": self.period}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        delta = df["close"].diff().to_numpy(dtype=float)
        gain = np.where(np.isnan(delta), np.nan, np.clip(delta, 0.0, None))
        loss = np.where(np.isnan(delta), np.nan, np.clip(-delta, 0.0, None))
        avg_gain = wilder_smooth(gain, self.period)
        avg_loss = wilder_smooth(loss, self.period)
        with np.errstate(divide="ignore", invalid="ignore"):
            rsi = np.where(
                avg_loss == 0.0,
                np.where(np.isnan(avg_gain), np.nan, 100.0),
                100.0 - 100.0 / (1.0 + avg_gain / avg_loss),
            )
        return {self.name: pd.Series(rsi, index=df.index)}


class Stochastic(Indicator):
    """Slow stochastic oscillator (%K smoothed, %D = SMA of %K)."""

    def __init__(self, k_period: int = 14, smooth: int = 3, d_period: int = 3) -> None:
        if min(k_period, smooth, d_period) < 1:
            raise ValueError("Stochastic periods must be >= 1")
        self.k_period, self.smooth, self.d_period = k_period, smooth, d_period

    @property
    def name(self) -> str:
        return f"stoch_{self.k_period}_{self.smooth}_{self.d_period}"

    @property
    def warmup_period(self) -> int:
        return self.k_period + self.smooth + self.d_period

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (f"{self.name}_k", f"{self.name}_d")

    def params(self) -> dict[str, Any]:
        return {
            "k_period": self.k_period,
            "smooth": self.smooth,
            "d_period": self.d_period,
        }

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        lowest = df["low"].rolling(self.k_period, min_periods=self.k_period).min()
        highest = df["high"].rolling(self.k_period, min_periods=self.k_period).max()
        raw_k = 100.0 * safe_divide(df["close"] - lowest, highest - lowest)
        k = raw_k.rolling(self.smooth, min_periods=self.smooth).mean()
        d = k.rolling(self.d_period, min_periods=self.d_period).mean()
        return {f"{self.name}_k": k, f"{self.name}_d": d}


class CCI(Indicator):
    """Commodity channel index over the typical price."""

    def __init__(self, period: int = 20, constant: float = 0.015) -> None:
        if period < 2:
            raise ValueError("CCI period must be >= 2")
        if constant <= 0:
            raise ValueError("CCI constant must be > 0")
        self.period = period
        self.constant = constant

    @property
    def name(self) -> str:
        return f"cci_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {"period": self.period, "constant": self.constant}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        sma = tp.rolling(self.period, min_periods=self.period).mean()
        mad = tp.rolling(self.period, min_periods=self.period).apply(
            lambda w: float(np.mean(np.abs(w - w.mean()))), raw=True
        )
        return {self.name: safe_divide(tp - sma, self.constant * mad)}


class ROC(Indicator):
    """Rate of change, in percent."""

    def __init__(self, period: int = 10) -> None:
        if period < 1:
            raise ValueError("ROC period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"roc_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {"period": self.period}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        prev = df["close"].shift(self.period)
        return {self.name: 100.0 * safe_divide(df["close"] - prev, prev)}
