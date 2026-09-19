"""Ichimoku Kinko Hyo.

Three V1 defects are fixed here, deliberately and visibly.

1. **Chikou is not in the strategy frame.** V1 wrote
   ``chikou_span = close.shift(-26)`` into the DataFrame handed to every
   strategy: a genuine future close, readable at bar i. V2's ``compute`` never
   emits it. The display series lives in :func:`chikou_span_display`, which is
   documented as chart-only, and the *causal* restatement of the classic chikou
   test ("is price above where it was 26 bars ago?") is published as
   ``..._chikou_above_price``.

2. **Senkou spans have their own shift.** V1 displaced the cloud with
   ``chikou_shift``, so a sensitivity sweep over the chikou parameter silently
   moved the cloud. ``senkou_shift`` is a separate parameter here.

3. **No future cloud on the current row.** A senkou value computed at bar i is
   *plotted* at bar i + senkou_shift. The value legitimately readable at bar i
   is therefore the one computed at bar i - senkou_shift, i.e. a backward shift
   of the raw series. The forward-displaced values that belong to bars after i
   are never attached to row i; the projected cloud is available only through
   :func:`projected_cloud_display`.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from fiboki.indicators.base import OHLC_COLUMNS, Indicator


def _midline(high: pd.Series, low: pd.Series, period: int) -> pd.Series:
    hh = high.rolling(period, min_periods=period).max()
    ll = low.rolling(period, min_periods=period).min()
    return (hh + ll) / 2.0


class Ichimoku(Indicator):
    """Tenkan, Kijun, Senkou A/B (readable, causal) and cloud geometry."""

    def __init__(
        self,
        tenkan_period: int = 9,
        kijun_period: int = 26,
        senkou_b_period: int = 52,
        senkou_shift: int = 26,
        chikou_shift: int = 26,
    ) -> None:
        if min(tenkan_period, kijun_period, senkou_b_period) < 1:
            raise ValueError("Ichimoku periods must be >= 1")
        if senkou_shift < 0 or chikou_shift < 0:
            raise ValueError("Ichimoku shifts must be >= 0")
        self.tenkan_period = tenkan_period
        self.kijun_period = kijun_period
        self.senkou_b_period = senkou_b_period
        self.senkou_shift = senkou_shift
        self.chikou_shift = chikou_shift

    @property
    def name(self) -> str:
        return (
            f"ichimoku_{self.tenkan_period}_{self.kijun_period}_"
            f"{self.senkou_b_period}_{self.senkou_shift}_{self.chikou_shift}"
        )

    @property
    def warmup_period(self) -> int:
        # The last line to become readable is Senkou B: senkou_b_period bars to
        # form, then senkou_shift bars before the displaced value lands.
        return max(
            self.senkou_b_period + self.senkou_shift,
            self.kijun_period + self.senkou_shift,
            self.chikou_shift + 1,
        )

    @property
    def output_columns(self) -> tuple[str, ...]:
        p = self.name
        return (
            f"{p}_tenkan",
            f"{p}_kijun",
            f"{p}_senkou_a",
            f"{p}_senkou_b",
            f"{p}_cloud_top",
            f"{p}_cloud_bottom",
            f"{p}_price_vs_cloud",
            f"{p}_chikou_above_price",
        )

    def params(self) -> dict[str, Any]:
        return {
            "tenkan_period": self.tenkan_period,
            "kijun_period": self.kijun_period,
            "senkou_b_period": self.senkou_b_period,
            "senkou_shift": self.senkou_shift,
            "chikou_shift": self.chikou_shift,
        }

    # -------------------------------------------------------------- raw

    def raw_senkou_a(self, df: pd.DataFrame) -> pd.Series:
        """Senkou A *as computed at each bar*, before forward displacement."""
        tenkan = _midline(df["high"], df["low"], self.tenkan_period)
        kijun = _midline(df["high"], df["low"], self.kijun_period)
        return (tenkan + kijun) / 2.0

    def raw_senkou_b(self, df: pd.DataFrame) -> pd.Series:
        return _midline(df["high"], df["low"], self.senkou_b_period)

    # ---------------------------------------------------------- compute

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        p = self.name
        high, low, close = df["high"], df["low"], df["close"]
        tenkan = _midline(high, low, self.tenkan_period)
        kijun = _midline(high, low, self.kijun_period)

        # shift(+k) pulls a value FORWARD IN TIME from the past: the cloud shown
        # at bar i was computed at bar i - senkou_shift. Nothing from the future.
        senkou_a = ((tenkan + kijun) / 2.0).shift(self.senkou_shift)
        senkou_b = _midline(high, low, self.senkou_b_period).shift(self.senkou_shift)

        cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
        cloud_bottom = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)

        price_vs_cloud = pd.Series(float("nan"), index=df.index, dtype=float)
        valid = cloud_top.notna() & cloud_bottom.notna()
        price_vs_cloud[valid & (close > cloud_top)] = 1.0
        price_vs_cloud[valid & (close < cloud_bottom)] = -1.0
        price_vs_cloud[valid & (close <= cloud_top) & (close >= cloud_bottom)] = 0.0

        # Causal restatement of the chikou test. The classic statement "chikou
        # (close plotted 26 bars back) is above price" is, read at bar i,
        # exactly "close[i] > close[i - chikou_shift]" — entirely historical.
        lagged = close.shift(self.chikou_shift)
        chikou_above = pd.Series(float("nan"), index=df.index, dtype=float)
        ok = lagged.notna()
        chikou_above[ok] = (close[ok] > lagged[ok]).astype(float)

        return {
            f"{p}_tenkan": tenkan,
            f"{p}_kijun": kijun,
            f"{p}_senkou_a": senkou_a,
            f"{p}_senkou_b": senkou_b,
            f"{p}_cloud_top": cloud_top,
            f"{p}_cloud_bottom": cloud_bottom,
            f"{p}_price_vs_cloud": price_vs_cloud,
            f"{p}_chikou_above_price": chikou_above,
        }


# ---------------------------------------------------------------- display


def chikou_span_display(df: pd.DataFrame, chikou_shift: int = 26) -> pd.Series:
    """Chikou span **for charting only**.

    This is ``close.shift(-chikou_shift)``: the value at row i is the close of a
    *future* bar. It exists so a chart can draw the lagging span in its
    conventional position. It must never be merged into a frame that a strategy,
    backtest or paper engine reads. :mod:`fiboki.strategy.compiler` has no code
    path that can request it.
    """
    return df["close"].shift(-chikou_shift).rename("chikou_span_display")


def projected_cloud_display(
    df: pd.DataFrame,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    senkou_shift: int = 26,
) -> pd.DataFrame:
    """The forward-projected cloud **for charting only**.

    Returns a frame indexed by *bar offset* (0 .. senkou_shift-1 beyond the last
    bar) holding the senkou values that belong to bars that have not happened.
    Deliberately returned on a different index so it cannot be joined onto the
    strategy frame by accident.
    """
    missing = [c for c in OHLC_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"projected_cloud_display requires {missing}")
    tenkan = _midline(df["high"], df["low"], tenkan_period)
    kijun = _midline(df["high"], df["low"], kijun_period)
    raw_a = ((tenkan + kijun) / 2.0).to_numpy()
    raw_b = _midline(df["high"], df["low"], senkou_b_period).to_numpy()
    if senkou_shift == 0:
        return pd.DataFrame({"bars_ahead": [], "senkou_a": [], "senkou_b": []})
    tail = slice(len(df) - senkou_shift, len(df))
    return pd.DataFrame(
        {
            "bars_ahead": range(1, senkou_shift + 1),
            "senkou_a": raw_a[tail],
            "senkou_b": raw_b[tail],
        }
    )
