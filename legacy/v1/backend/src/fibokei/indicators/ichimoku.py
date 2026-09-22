"""Ichimoku Cloud indicator implementation."""

import pandas as pd

from fibokei.indicators.base import Indicator


class IchimokuCloud(Indicator):
    """Ichimoku Kinko Hyo (Ichimoku Cloud) indicator.

    Computes all five Ichimoku components:
    - Tenkan-sen (Conversion Line)
    - Kijun-sen (Base Line)
    - Senkou Span A (Leading Span A)
    - Senkou Span B (Leading Span B)
    - Chikou Span (Lagging Span)
    """

    def __init__(
        self,
        tenkan_period: int = 9,
        kijun_period: int = 26,
        senkou_b_period: int = 52,
        chikou_shift: int = 26,
    ):
        self.tenkan_period = tenkan_period
        self.kijun_period = kijun_period
        self.senkou_b_period = senkou_b_period
        self.chikou_shift = chikou_shift

    @property
    def name(self) -> str:
        return "ichimoku_cloud"

    @property
    def warmup_period(self) -> int:
        return self.senkou_b_period + self.chikou_shift

    def _donchian_midline(self, high: pd.Series, low: pd.Series, period: int) -> pd.Series:
        """Calculate (highest high + lowest low) / 2 over period."""
        return (high.rolling(window=period).max() + low.rolling(window=period).min()) / 2

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)

        high = df["high"]
        low = df["low"]
        close = df["close"]

        # Tenkan-sen: (highest high + lowest low) / 2 over tenkan_period
        df["tenkan_sen"] = self._donchian_midline(high, low, self.tenkan_period)

        # Kijun-sen: (highest high + lowest low) / 2 over kijun_period
        df["kijun_sen"] = self._donchian_midline(high, low, self.kijun_period)

        # Senkou Span A: (tenkan + kijun) / 2, shifted forward
        df["senkou_span_a"] = ((df["tenkan_sen"] + df["kijun_sen"]) / 2).shift(
            self.chikou_shift
        )

        # Senkou Span B: donchian midline over senkou_b_period, shifted forward
        df["senkou_span_b"] = self._donchian_midline(high, low, self.senkou_b_period).shift(
            self.chikou_shift
        )

        # Chikou Span: close shifted backward
        df["chikou_span"] = close.shift(-self.chikou_shift)

        return df
