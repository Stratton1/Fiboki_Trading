"""Trend and moving-average indicators. All strictly causal."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fiboki.indicators.base import Indicator, safe_divide, true_range, wilder_smooth


class SMA(Indicator):
    """Simple moving average of the close."""

    def __init__(self, period: int = 20, source: str = "close") -> None:
        if period < 1:
            raise ValueError("SMA period must be >= 1")
        self.period = period
        self.source = source

    @property
    def name(self) -> str:
        return f"sma_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {"period": self.period, "source": self.source}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        return {self.name: df[self.source].rolling(self.period, min_periods=self.period).mean()}


class EMA(Indicator):
    """Exponential moving average, ``alpha = 2/(period+1)``, seeded at bar 0.

    ``adjust=False`` matches the recursive definition used by every charting
    package and by MACD below.
    """

    def __init__(self, period: int = 20, source: str = "close") -> None:
        if period < 1:
            raise ValueError("EMA period must be >= 1")
        self.period = period
        self.source = source

    @property
    def name(self) -> str:
        return f"ema_{self.period}"

    @property
    def warmup_period(self) -> int:
        # An EMA seeded at bar 0 is biased towards the seed; 3 time-constants
        # reduces the seed weight to ~5%.
        return 3 * self.period

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {"period": self.period, "source": self.source}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        return {self.name: df[self.source].ewm(span=self.period, adjust=False).mean()}


class WMA(Indicator):
    """Linearly weighted moving average; weight ``j`` on the j-th oldest bar."""

    def __init__(self, period: int = 20, source: str = "close") -> None:
        if period < 1:
            raise ValueError("WMA period must be >= 1")
        self.period = period
        self.source = source

    @property
    def name(self) -> str:
        return f"wma_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name,)

    def params(self) -> dict[str, Any]:
        return {"period": self.period, "source": self.source}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        weights = np.arange(1, self.period + 1, dtype=float)
        denom = weights.sum()
        series = df[self.source].rolling(self.period, min_periods=self.period).apply(
            lambda window: float(np.dot(window, weights) / denom), raw=True
        )
        return {self.name: series}


class MACD(Indicator):
    """Moving average convergence/divergence."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        if not 0 < fast < slow:
            raise ValueError("MACD requires 0 < fast < slow")
        if signal < 1:
            raise ValueError("MACD signal period must be >= 1")
        self.fast, self.slow, self.signal = fast, slow, signal

    @property
    def name(self) -> str:
        return f"macd_{self.fast}_{self.slow}_{self.signal}"

    @property
    def warmup_period(self) -> int:
        return 3 * self.slow + self.signal

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (f"{self.name}_line", f"{self.name}_signal", f"{self.name}_hist")

    def params(self) -> dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow, "signal": self.signal}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        fast = df["close"].ewm(span=self.fast, adjust=False).mean()
        slow = df["close"].ewm(span=self.slow, adjust=False).mean()
        line = fast - slow
        sig = line.ewm(span=self.signal, adjust=False).mean()
        return {
            f"{self.name}_line": line,
            f"{self.name}_signal": sig,
            f"{self.name}_hist": line - sig,
        }


class ADX(Indicator):
    """Wilder's average directional index with +DI / -DI.

    Uses Wilder's own seeding (simple mean of the first ``period`` values) so
    the published numbers match the textbook rather than an EMA approximation.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("ADX period must be >= 2")
        self.period = period

    @property
    def name(self) -> str:
        return f"adx_{self.period}"

    @property
    def warmup_period(self) -> int:
        # period bars to seed DI, another period to seed the ADX of DX.
        return 2 * self.period + 1

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name, f"{self.name}_plus_di", f"{self.name}_minus_di")

    def params(self) -> dict[str, Any]:
        return {"period": self.period}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        up = df["high"].diff()
        down = -df["low"].diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        # Bar 0 has no directional movement at all.
        plus_dm[0] = np.nan
        minus_dm[0] = np.nan

        tr = true_range(df).to_numpy(dtype=float)
        tr[0] = np.nan  # TR at bar 0 has no prior close; exclude from seeding.

        atr = wilder_smooth(tr, self.period)
        plus_sm = wilder_smooth(plus_dm, self.period)
        minus_sm = wilder_smooth(minus_dm, self.period)

        idx = df.index
        atr_s = pd.Series(atr, index=idx)
        plus_di = 100.0 * safe_divide(pd.Series(plus_sm, index=idx), atr_s)
        minus_di = 100.0 * safe_divide(pd.Series(minus_sm, index=idx), atr_s)
        dx = 100.0 * safe_divide((plus_di - minus_di).abs(), plus_di + minus_di)
        adx = wilder_smooth(dx.to_numpy(dtype=float), self.period)
        return {
            self.name: pd.Series(adx, index=idx),
            f"{self.name}_plus_di": plus_di,
            f"{self.name}_minus_di": minus_di,
        }


class PSAR(Indicator):
    """Parabolic SAR. ``psar_trend`` is +1 for an up trend, -1 for a down trend.

    The recursion is forward-only: bar ``i``'s SAR uses the state after bar
    ``i-1`` plus bar ``i``'s own high/low, and the standard "SAR may not
    penetrate the prior two lows/highs" clamp looks strictly backwards.
    """

    def __init__(
        self, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2
    ) -> None:
        if not 0 < af_start <= af_max:
            raise ValueError("PSAR requires 0 < af_start <= af_max")
        if af_step <= 0:
            raise ValueError("PSAR af_step must be > 0")
        self.af_start, self.af_step, self.af_max = af_start, af_step, af_max

    @property
    def name(self) -> str:
        return "psar"

    @property
    def warmup_period(self) -> int:
        return 5

    @property
    def output_columns(self) -> tuple[str, ...]:
        return ("psar", "psar_trend")

    def params(self) -> dict[str, Any]:
        return {
            "af_start": self.af_start,
            "af_step": self.af_step,
            "af_max": self.af_max,
        }

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        n = len(df)
        psar = np.full(n, np.nan)
        trend = np.zeros(n, dtype=float)
        if n < 2:
            return {"psar": psar, "psar_trend": trend}

        up = high[1] >= high[0]
        af = self.af_start
        ep = high[0] if up else low[0]
        sar = low[0] if up else high[0]
        for i in range(1, n):
            sar = sar + af * (ep - sar)
            if up:
                sar = min(sar, low[i - 1], low[max(0, i - 2)])
                if low[i] < sar:
                    up = False
                    sar = ep
                    ep = low[i]
                    af = self.af_start
                elif high[i] > ep:
                    ep = high[i]
                    af = min(af + self.af_step, self.af_max)
            else:
                sar = max(sar, high[i - 1], high[max(0, i - 2)])
                if high[i] > sar:
                    up = True
                    sar = ep
                    ep = high[i]
                    af = self.af_start
                elif low[i] < ep:
                    ep = low[i]
                    af = min(af + self.af_step, self.af_max)
            psar[i] = sar
            trend[i] = 1.0 if up else -1.0
        return {"psar": psar, "psar_trend": trend}
