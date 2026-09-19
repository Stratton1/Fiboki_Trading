"""Direction-aware Fibonacci retracements and extensions.

V1 anchored blind: it took ``last_swing_high`` and ``last_swing_low``, required
``high > low``, and where that failed emitted nothing at all -- silently. Whole
instruments produced zero Fibonacci signals and nobody could tell whether that
was the market or the code.

V2 anchors by *time order*, which is what "direction-aware" actually means:

* ``start`` = the older of the two most recently confirmed swings,
* ``end``   = the newer one.

An up leg is low -> high (``end > start``), a down leg is high -> low. A
retracement ratio ``r`` is then ``end - r * (end - start)`` and an extension is
``start + r * (end - start)``, both of which give the textbook levels for either
direction with no branching and no silent empty case. The swings used are the
*confirmed* ones from :class:`~fiboki.indicators.swing.SwingDetector`, computed
internally so the two can never be run out of order.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fiboki.indicators.base import Indicator
from fiboki.indicators.swing import SwingDetector

DEFAULT_RETRACEMENTS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)
DEFAULT_EXTENSIONS: tuple[float, ...] = (1.272, 1.618, 2.0, 2.618)


def _tag(ratio: float) -> str:
    return f"{ratio:.3f}".replace(".", "")


class Fibonacci(Indicator):
    """Retracement and extension levels from the newest confirmed swing leg."""

    def __init__(
        self,
        swing_lookback: int = 5,
        retracements: tuple[float, ...] = DEFAULT_RETRACEMENTS,
        extensions: tuple[float, ...] = DEFAULT_EXTENSIONS,
    ) -> None:
        if swing_lookback < 1:
            raise ValueError("Fibonacci swing_lookback must be >= 1")
        if not retracements and not extensions:
            raise ValueError("Fibonacci needs at least one ratio")
        for r in (*retracements, *extensions):
            if r <= 0:
                raise ValueError("Fibonacci ratios must be > 0")
        self.swing_lookback = swing_lookback
        self.retracements = tuple(retracements)
        self.extensions = tuple(extensions)
        self._swing = SwingDetector(lookback=swing_lookback)

    @property
    def name(self) -> str:
        return f"fib_{self.swing_lookback}"

    @property
    def warmup_period(self) -> int:
        # Two confirmed swings of opposite sign are needed before any level
        # exists: one full fractal window for each, at worst back to back.
        return 2 * self._swing.warmup_period

    @property
    def output_columns(self) -> tuple[str, ...]:
        p = self.name
        cols = [f"{p}_dir", f"{p}_start", f"{p}_end", f"{p}_range"]
        cols += [f"{p}_ret_{_tag(r)}" for r in self.retracements]
        cols += [f"{p}_ext_{_tag(r)}" for r in self.extensions]
        return tuple(cols)

    def params(self) -> dict[str, Any]:
        return {
            "swing_lookback": self.swing_lookback,
            "retracements": list(self.retracements),
            "extensions": list(self.extensions),
        }

    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        p = self.name
        swung = self._swing.compute(df)
        high = swung["last_swing_high"]
        low = swung["last_swing_low"]
        high_bar = swung["last_swing_high_bar"]
        low_bar = swung["last_swing_low_bar"]

        both = high.notna() & low.notna() & high_bar.notna() & low_bar.notna()
        up_leg = both & (high_bar > low_bar)  # newer extreme is the high
        down_leg = both & (low_bar > high_bar)

        idx = df.index
        start = pd.Series(np.nan, index=idx, dtype=float)
        end = pd.Series(np.nan, index=idx, dtype=float)
        start[up_leg] = low[up_leg]
        end[up_leg] = high[up_leg]
        start[down_leg] = high[down_leg]
        end[down_leg] = low[down_leg]

        span = end - start
        direction = pd.Series(np.nan, index=idx, dtype=float)
        direction[both] = np.sign(span[both])

        out: dict[str, Any] = {
            f"{p}_dir": direction,
            f"{p}_start": start,
            f"{p}_end": end,
            f"{p}_range": span.abs(),
        }
        for r in self.retracements:
            out[f"{p}_ret_{_tag(r)}"] = end - r * span
        for r in self.extensions:
            out[f"{p}_ext_{_tag(r)}"] = start + r * span
        return out
