"""Trend-strength / reversal indicators (centralised). Closed-candle only."""

import numpy as np
import pandas as pd

from fibokei.indicators.base import Indicator


class ADX(Indicator):
    """Average Directional Index (Wilder). Adds ``adx_{n}``, ``plus_di``, ``minus_di``."""

    def __init__(self, period: int = 14):
        if period < 2:
            raise ValueError("ADX period must be >= 2")
        self.period = period

    @property
    def name(self) -> str:
        return f"adx_{self.period}"

    @property
    def warmup_period(self) -> int:
        return 2 * self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        n = self.period
        up = df["high"].diff()
        down = -df["low"].diff()
        plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0.0)
        minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0.0)
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [df["high"] - df["low"],
             (df["high"] - prev_close).abs(),
             (df["low"] - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / n, adjust=False).mean().replace(0.0, float("nan"))
        plus_sm = plus_dm.ewm(alpha=1.0 / n, adjust=False).mean()
        minus_sm = minus_dm.ewm(alpha=1.0 / n, adjust=False).mean()
        plus_di = 100.0 * plus_sm / atr
        minus_di = 100.0 * minus_sm / atr
        di_sum = (plus_di + minus_di).replace(0.0, float("nan"))
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        df["plus_di"] = plus_di
        df["minus_di"] = minus_di
        df[self.name] = dx.ewm(alpha=1.0 / n, adjust=False).mean()
        return df


class ParabolicSAR(Indicator):
    """Parabolic SAR. Adds ``psar`` and ``psar_trend`` (1 = up, -1 = down)."""

    def __init__(self, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2):
        if not (0 < af_start <= af_max):
            raise ValueError("PSAR requires 0 < af_start <= af_max")
        self.af_start, self.af_step, self.af_max = af_start, af_step, af_max

    @property
    def name(self) -> str:
        return "psar"

    @property
    def warmup_period(self) -> int:
        return 2

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        n = len(df)
        psar = np.full(n, np.nan)
        trend = np.zeros(n, dtype=int)
        if n < 2:
            df["psar"] = psar
            df["psar_trend"] = trend
            return df

        up = True
        af = self.af_start
        ep = high[0]
        sar = low[0]
        for i in range(1, n):
            prior_sar = sar
            sar = prior_sar + af * (ep - prior_sar)
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
            trend[i] = 1 if up else -1

        df["psar"] = psar
        df["psar_trend"] = trend
        return df
