"""Causal swing (fractal) detection.

V1's ``SwingDetector`` used a centred window: it wrote a swing onto bar ``i``
after inspecting bars ``i+1 .. i+lookback``. Two consequences followed, and both
were real bugs:

* **Look-ahead.** A backtest could enter on a swing that, in real time, had not
  yet been confirmed.
* **Backtest/paper divergence.** The last ``lookback`` bars can never carry a
  swing, so the live bot -- which is always standing on the last bar -- saw a
  different world from the backtest.

V2 keeps the same fractal *definition* but publishes it at the bar where it
becomes knowable. A swing high at bar ``j`` is confirmed at bar ``j + lookback``;
``swing_high_confirmed`` carries its price on that confirmation bar and NaN
everywhere else. Backtest and paper therefore see the identical series.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fiboki.indicators.base import Indicator

SWING_COLUMNS: tuple[str, ...] = (
    "swing_high_confirmed",
    "swing_low_confirmed",
    "last_swing_high",
    "last_swing_low",
    "last_swing_high_bar",
    "last_swing_low_bar",
    "swing_leg_direction",
)


def _raw_fractals(
    high: np.ndarray, low: np.ndarray, lookback: int
) -> tuple[np.ndarray, np.ndarray]:
    """Centred fractal detection. NOT causal on its own -- callers must shift."""
    n = high.size
    raw_high = np.full(n, np.nan)
    raw_low = np.full(n, np.nan)
    for j in range(lookback, n - lookback):
        left_h = high[j - lookback : j]
        right_h = high[j + 1 : j + lookback + 1]
        if high[j] > left_h.max() and high[j] > right_h.max():
            raw_high[j] = high[j]
        left_l = low[j - lookback : j]
        right_l = low[j + 1 : j + lookback + 1]
        if low[j] < left_l.min() and low[j] < right_l.min():
            raw_low[j] = low[j]
    return raw_high, raw_low


class SwingDetector(Indicator):
    """Confirmed swing highs/lows plus the running "most recent confirmed" pair.

    Columns:

    ``swing_high_confirmed`` / ``swing_low_confirmed``
        Price of a swing, published on its confirmation bar (``j + lookback``).
    ``last_swing_high`` / ``last_swing_low``
        Forward fill of the above: the newest swing *knowable now*.
    ``last_swing_high_bar`` / ``last_swing_low_bar``
        Integer bar position ``j`` of that swing, so downstream indicators can
        order the two legs in time.
    ``swing_leg_direction``
        +1 when the newer confirmed extreme is the high (an up leg), -1 when it
        is the low, NaN before both exist.
    """

    def __init__(self, lookback: int = 5) -> None:
        if lookback < 1:
            raise ValueError("SwingDetector lookback must be >= 1")
        self.lookback = lookback

    @property
    def name(self) -> str:
        return f"swing_{self.lookback}"

    @property
    def warmup_period(self) -> int:
        # lookback bars either side of the swing, plus the swing bar itself.
        return 2 * self.lookback + 1

    @property
    def output_columns(self) -> tuple[str, ...]:
        return SWING_COLUMNS

    def params(self) -> dict[str, Any]:
        return {"lookback": self.lookback}

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        lb = self.lookback
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        n = high.size
        raw_high, raw_low = _raw_fractals(high, low, lb)

        # Publish each raw swing lb bars later: swing at j is knowable at j+lb.
        conf_high = np.full(n, np.nan)
        conf_low = np.full(n, np.nan)
        bar_high = np.full(n, np.nan)
        bar_low = np.full(n, np.nan)
        if n > lb:
            conf_high[lb:] = raw_high[: n - lb]
            conf_low[lb:] = raw_low[: n - lb]
            swing_bars = np.arange(n, dtype=float) - lb
            bar_high[lb:] = np.where(np.isnan(raw_high[: n - lb]), np.nan, swing_bars[lb:])
            bar_low[lb:] = np.where(np.isnan(raw_low[: n - lb]), np.nan, swing_bars[lb:])

        idx = df.index
        last_high = pd.Series(conf_high, index=idx).ffill()
        last_low = pd.Series(conf_low, index=idx).ffill()
        last_high_bar = pd.Series(bar_high, index=idx).ffill()
        last_low_bar = pd.Series(bar_low, index=idx).ffill()

        direction = pd.Series(np.nan, index=idx, dtype=float)
        both = last_high_bar.notna() & last_low_bar.notna()
        direction[both & (last_high_bar > last_low_bar)] = 1.0
        direction[both & (last_high_bar < last_low_bar)] = -1.0
        # Equal bar indices are impossible (one bar cannot be both extremes of
        # a strict fractal), but be explicit rather than leaving it undefined.
        direction[both & (last_high_bar == last_low_bar)] = 0.0

        return {
            "swing_high_confirmed": pd.Series(conf_high, index=idx),
            "swing_low_confirmed": pd.Series(conf_low, index=idx),
            "last_swing_high": last_high,
            "last_swing_low": last_low,
            "last_swing_high_bar": last_high_bar,
            "last_swing_low_bar": last_low_bar,
            "swing_leg_direction": direction,
        }
