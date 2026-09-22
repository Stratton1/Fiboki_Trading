"""Volume-dependent indicators.

Fiboki's universe is mostly spot FX and CFDs, where the "volume" a broker
publishes is a tick count or is identically zero. V1 pretended otherwise: OBV
was computed on zero volume (so its strategy could never trade) and VWAP
silently fell back to a rolling mean of the typical price and was still called
VWAP. Here, absent or zero volume is a first-class outcome:

* ``on_unavailable="raise"`` (default) raises :class:`VolumeUnavailableError`.
* ``on_unavailable="mark"`` emits NaN values plus an explicit
  ``<name>_unavailable`` flag column, so a research report can say "this
  indicator had no data" instead of quietly reporting a different quantity.

Nothing ever degrades to a different indicator.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from fiboki.indicators.base import Indicator, VolumeUnavailableError

Unavailable = Literal["raise", "mark"]


class _VolumeIndicator(Indicator):
    requires_volume = True

    def __init__(self, on_unavailable: Unavailable = "raise") -> None:
        if on_unavailable not in ("raise", "mark"):
            raise ValueError("on_unavailable must be 'raise' or 'mark'")
        self.on_unavailable: Unavailable = on_unavailable

    def _volume(self, df: pd.DataFrame) -> pd.Series:
        if "volume" not in df.columns:
            raise VolumeUnavailableError(
                f"{self.name}: no 'volume' column. This instrument/feed has no "
                "volume; do not use volume indicators on it."
            )
        return df["volume"].astype(float)

    def _reject_if_empty(self, vol: pd.Series) -> None:
        total = float(np.nansum(vol.to_numpy()))
        if not np.isfinite(total) or total <= 0.0:
            raise VolumeUnavailableError(
                f"{self.name}: volume is absent or identically zero over "
                f"{len(vol)} bars. Refusing to publish a number computed from "
                "nothing."
            )


class OBV(_VolumeIndicator):
    """On-balance volume.

    OBV is a cumulative sum, so a single bar of real volume anywhere in the
    frame makes the whole series meaningful; conversely an all-zero frame makes
    it identically zero, which is the V1 trap. That is checked up front.
    """

    @property
    def name(self) -> str:
        return "obv"

    @property
    def warmup_period(self) -> int:
        return 2

    @property
    def output_columns(self) -> tuple[str, ...]:
        return ("obv", "obv_unavailable")

    def params(self) -> dict[str, Any]:
        return {"on_unavailable": self.on_unavailable}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        n = len(df)
        try:
            vol = self._volume(df)
            self._reject_if_empty(vol)
        except VolumeUnavailableError:
            if self.on_unavailable == "raise":
                raise
            return {
                "obv": np.full(n, np.nan),
                "obv_unavailable": np.ones(n, dtype=float),
            }
        direction = np.sign(df["close"].diff().fillna(0.0))
        obv = (direction * vol.fillna(0.0)).cumsum()
        obv.iloc[0] = np.nan  # no prior close, so no direction at bar 0
        return {"obv": obv, "obv_unavailable": np.zeros(n, dtype=float)}


class VWAP(_VolumeIndicator):
    """Rolling volume-weighted average price over ``period`` bars.

    Per-bar honesty: a window whose traded volume is zero yields NaN and sets
    the ``_unavailable`` flag for that bar. It never becomes a price mean.
    """

    def __init__(self, period: int = 20, on_unavailable: Unavailable = "raise") -> None:
        super().__init__(on_unavailable)
        if period < 1:
            raise ValueError("VWAP period must be >= 1")
        self.period = period

    @property
    def name(self) -> str:
        return f"vwap_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    @property
    def output_columns(self) -> tuple[str, ...]:
        return (self.name, f"{self.name}_unavailable")

    def params(self) -> dict[str, Any]:
        return {"period": self.period, "on_unavailable": self.on_unavailable}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        n = len(df)
        try:
            vol = self._volume(df)
            self._reject_if_empty(vol)
        except VolumeUnavailableError:
            if self.on_unavailable == "raise":
                raise
            return {
                self.name: np.full(n, np.nan),
                f"{self.name}_unavailable": np.ones(n, dtype=float),
            }
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = vol.fillna(0.0)
        pv = (tp * vol).rolling(self.period, min_periods=self.period).sum()
        vsum = vol.rolling(self.period, min_periods=self.period).sum()
        vwap = pv / vsum.where(vsum > 0.0)
        flag = np.where(vsum.isna(), np.nan, (vsum.fillna(0.0) <= 0.0).astype(float))
        return {
            self.name: vwap,
            f"{self.name}_unavailable": pd.Series(flag, index=df.index),
        }
