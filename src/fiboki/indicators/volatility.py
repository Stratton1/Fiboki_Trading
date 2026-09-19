"""Volatility, channel and band indicators. All strictly causal."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fiboki.indicators.base import Indicator, safe_divide, true_range, wilder_smooth


class ATR(Indicator):
    """Wilder's average true range (Wilder seeding, not an EMA approximation)."""

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"atr_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {"period": self.period}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        tr = true_range(df).to_numpy(dtype=float)
        return {self.name: pd.Series(wilder_smooth(tr, self.period), index=df.index)}


class Bollinger(Indicator):
    """Bollinger bands using the *population* standard deviation (ddof=0).

    ddof matters: ddof=1 on a 20-bar window inflates the band width by ~2.6%,
    which silently changes every mean-reversion entry. Pinned in the goldens.
    """

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        if period < 2:
            raise ValueError("Bollinger period must be >= 2")
        if num_std <= 0:
            raise ValueError("Bollinger num_std must be > 0")
        self.period = period
        self.num_std = float(num_std)

    @property
    def name(self) -> str:
        return f"bb_{self.period}_{self.num_std:g}"

    @property
    def warmup_period(self) -> int:
        return self.period

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (
            f"{self.name}_mid",
            f"{self.name}_upper",
            f"{self.name}_lower",
            f"{self.name}_pctb",
            f"{self.name}_width",
        )

    def params(self) -> dict[str, Any]:
        return {"period": self.period, "num_std": self.num_std}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        mid = df["close"].rolling(self.period, min_periods=self.period).mean()
        std = df["close"].rolling(self.period, min_periods=self.period).std(ddof=0)
        upper = mid + self.num_std * std
        lower = mid - self.num_std * std
        return {
            f"{self.name}_mid": mid,
            f"{self.name}_upper": upper,
            f"{self.name}_lower": lower,
            f"{self.name}_pctb": safe_divide(df["close"] - lower, upper - lower),
            f"{self.name}_width": safe_divide(upper - lower, mid),
        }


class Donchian(Indicator):
    """Donchian channel.

    Publishes both the window *including* the current bar and the window
    *excluding* it (``_prior``). Breakout rules must use ``_prior``: a channel
    that contains the current bar's own high can never be broken by that bar.
    Both are causal; only one of them is economically meaningful for entries.
    """

    def __init__(self, period: int = 20) -> None:
        if period < 2:
            raise ValueError("Donchian period must be >= 2")
        self.period = period

    @property
    def name(self) -> str:
        return f"donchian_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (
            f"{self.name}_upper",
            f"{self.name}_lower",
            f"{self.name}_mid",
            f"{self.name}_upper_prior",
            f"{self.name}_lower_prior",
        )

    def params(self) -> dict[str, Any]:
        return {"period": self.period}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        upper = df["high"].rolling(self.period, min_periods=self.period).max()
        lower = df["low"].rolling(self.period, min_periods=self.period).min()
        return {
            f"{self.name}_upper": upper,
            f"{self.name}_lower": lower,
            f"{self.name}_mid": (upper + lower) / 2.0,
            f"{self.name}_upper_prior": upper.shift(1),
            f"{self.name}_lower_prior": lower.shift(1),
        }


class Keltner(Indicator):
    """Keltner channel: EMA midline with Wilder-ATR bands."""

    def __init__(
        self, ema_period: int = 20, atr_period: int = 10, multiple: float = 2.0
    ) -> None:
        if min(ema_period, atr_period) < 1:
            raise ValueError("Keltner periods must be >= 1")
        if multiple <= 0:
            raise ValueError("Keltner multiple must be > 0")
        self.ema_period, self.atr_period, self.multiple = (
            ema_period,
            atr_period,
            float(multiple),
        )

    @property
    def name(self) -> str:
        return f"kc_{self.ema_period}_{self.atr_period}_{self.multiple:g}"

    @property
    def warmup_period(self) -> int:
        return max(3 * self.ema_period, self.atr_period + 1)

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (f"{self.name}_mid", f"{self.name}_upper", f"{self.name}_lower")

    def params(self) -> dict[str, Any]:
        return {
            "ema_period": self.ema_period,
            "atr_period": self.atr_period,
            "multiple": self.multiple,
        }

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        mid = df["close"].ewm(span=self.ema_period, adjust=False).mean()
        atr = pd.Series(
            wilder_smooth(true_range(df).to_numpy(dtype=float), self.atr_period),
            index=df.index,
        )
        return {
            f"{self.name}_mid": mid,
            f"{self.name}_upper": mid + self.multiple * atr,
            f"{self.name}_lower": mid - self.multiple * atr,
        }


class RealisedVolatility(Indicator):
    """Rolling standard deviation of log returns, optionally annualised.

    ``bars_per_year`` comes from ``Timeframe.bars_per_year``; leaving it None
    publishes the raw per-bar standard deviation so nothing is annualised by
    accident with the wrong horizon.
    """

    def __init__(
        self, period: int = 20, bars_per_year: float | None = None, ddof: int = 1
    ) -> None:
        if period < 2:
            raise ValueError("RealisedVolatility period must be >= 2")
        if bars_per_year is not None and bars_per_year <= 0:
            raise ValueError("bars_per_year must be > 0 when supplied")
        if ddof not in (0, 1):
            raise ValueError("ddof must be 0 or 1")
        self.period = period
        self.bars_per_year = bars_per_year
        self.ddof = ddof

    @property
    def name(self) -> str:
        suffix = "ann" if self.bars_per_year else "raw"
        return f"realised_vol_{self.period}_{suffix}"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "bars_per_year": self.bars_per_year,
            "ddof": self.ddof,
        }

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        close = df["close"].astype(float)
        ratio = close / close.shift(1)
        log_ret = np.log(ratio.where(ratio > 0))
        vol = log_ret.rolling(self.period, min_periods=self.period).std(ddof=self.ddof)
        if self.bars_per_year:
            vol = vol * np.sqrt(self.bars_per_year)
        return {self.name: vol}
