"""Helpers for the market-data platform tests.

A plain module rather than a conftest, so the data tests keep working no matter
what else lands in ``tests/conftest.py``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.data.schema import (
    BarDatasetMetadata,
    PriceBasis,
    canonical_frame,
    describe_frame,
)

V1_HISTDATA_ROOT = Path("/home/claude/fiboki/data/canonical/histdata")


def make_bars(
    *,
    instrument: str = "EURUSD",
    timeframe: Timeframe = Timeframe.H1,
    periods: int = 400,
    start: str = "2026-01-05 00:00",
    price_basis: PriceBasis = PriceBasis.MID,
    seed: int = 11,
    with_volume: bool = True,
    freq: str | None = None,
) -> pd.DataFrame:
    """A clean synthetic bar frame: monotonic, in-session, geometrically valid.

    Starts on a Monday so it sits inside the FX week, which keeps the
    session-calendar checks quiet unless a test deliberately breaks them.
    """
    rule = freq or f"{timeframe.minutes}min"
    idx = pd.date_range(start=start, periods=periods, freq=rule, tz="UTC", name="timestamp")
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.0004, periods)
    close = 1.10 + np.cumsum(steps)
    open_ = np.empty(periods)
    open_[0] = close[0] - steps[0]
    open_[1:] = close[:-1]
    wick = np.abs(rng.normal(0.0, 0.0003, periods)) + 0.0001
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    data: dict[str, object] = {"open": open_, "high": high, "low": low, "close": close}
    if with_volume:
        data["volume"] = rng.integers(50, 5000, periods)
    frame = pd.DataFrame(data, index=idx)
    return canonical_frame(
        frame, instrument=instrument, timeframe=timeframe, price_basis=price_basis
    )


def make_metadata(frame: pd.DataFrame, **kwargs: object) -> BarDatasetMetadata:
    defaults: dict[str, object] = {
        "source": "test",
        "source_identifier": "synthetic",
        "timezone_of_origin": "UTC",
    }
    defaults.update(kwargs)
    return describe_frame(frame, **defaults)  # type: ignore[arg-type]


def has_v1_data() -> bool:
    return V1_HISTDATA_ROOT.exists() and any(V1_HISTDATA_ROOT.glob("*/*.parquet"))


requires_v1_data = pytest.mark.skipif(
    not has_v1_data(), reason=f"V1 HistData store not present at {V1_HISTDATA_ROOT}"
)
