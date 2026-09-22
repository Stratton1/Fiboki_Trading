"""Market regime classification indicator."""

import numpy as np
import pandas as pd

from fibokei.indicators.base import Indicator


class MarketRegime(Indicator):
    """Classifies market regime using Ichimoku and ATR values.

    Requires Ichimoku Cloud and ATR columns to already be computed on
    the DataFrame before calling compute().
    """

    def __init__(self, atr_expansion_factor: float = 1.5, atr_avg_period: int = 20):
        self.atr_expansion_factor = atr_expansion_factor
        self.atr_avg_period = atr_avg_period

    @property
    def name(self) -> str:
        return "market_regime"

    @property
    def required_columns(self) -> list[str]:
        return [
            "open", "high", "low", "close",
            "tenkan_sen", "kijun_sen",
            "senkou_span_a", "senkou_span_b",
            "atr",
        ]

    @property
    def warmup_period(self) -> int:
        return 78 + self.atr_avg_period  # Ichimoku warmup + ATR avg window

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)

        close = df["close"].values
        tenkan = df["tenkan_sen"].values
        kijun = df["kijun_sen"].values
        span_a = df["senkou_span_a"].values
        span_b = df["senkou_span_b"].values
        atr = df["atr"].values

        # Pre-compute cloud boundaries and ATR average
        cloud_top = np.maximum(span_a, span_b)
        cloud_bottom = np.minimum(span_a, span_b)
        cloud_width = cloud_top - cloud_bottom

        atr_series = pd.Series(atr)
        atr_avg = atr_series.rolling(window=self.atr_avg_period).mean().values

        n = len(df)
        regime = ["no_trade"] * n

        for i in range(self.warmup_period, n):
            c = close[i]
            tk = tenkan[i]
            kj = kijun[i]
            sa = span_a[i]
            sb = span_b[i]
            ct = cloud_top[i]
            cb = cloud_bottom[i]
            a = atr[i]
            aa = atr_avg[i]

            # Skip if essential values are NaN
            if any(np.isnan(x) for x in [c, tk, kj, sa, sb, a, aa]):
                continue

            above_cloud = c > ct
            below_cloud = c < cb
            inside_cloud = not above_cloud and not below_cloud
            tk_above_kj = tk > kj
            tk_below_kj = tk < kj
            tk_near_kj = abs(tk - kj) < a * 0.3
            expanding_cloud = cloud_width[i] > cloud_width[i - 1] if i > 0 else False
            vol_expansion = a > self.atr_expansion_factor * aa

            # Distance from kijun relative to ATR
            dist_from_kijun = abs(c - kj) / a if a > 0 else 0

            # Priority-ordered classification
            if vol_expansion:
                regime[i] = "volatility_expansion"
            elif above_cloud and tk_above_kj and expanding_cloud:
                regime[i] = "trending_bullish"
            elif below_cloud and tk_below_kj and expanding_cloud:
                regime[i] = "trending_bearish"
            elif above_cloud and not tk_above_kj:
                regime[i] = "pullback_bullish"
            elif below_cloud and not tk_below_kj:
                regime[i] = "pullback_bearish"
            elif above_cloud and dist_from_kijun > 3.0:
                regime[i] = "reversal_candidate"
            elif below_cloud and dist_from_kijun > 3.0:
                regime[i] = "reversal_candidate"
            elif inside_cloud or tk_near_kj:
                regime[i] = "consolidation"
            elif abs(c - ct) < a * 0.5 or abs(c - cb) < a * 0.5:
                regime[i] = "breakout_candidate"
            else:
                # Default: trending in direction of price relative to cloud
                if above_cloud:
                    regime[i] = "trending_bullish"
                elif below_cloud:
                    regime[i] = "trending_bearish"

        df["regime"] = regime
        return df
