"""Deterministic synthetic OHLCV bars for indicator and strategy tests.

Kept out of ``conftest.py`` on purpose: several workstreams share this repo and
a root conftest is a contended file. Import this module directly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def synthetic_ohlcv(
    n: int = 400, seed: int = 20240719, with_volume: bool = True
) -> pd.DataFrame:
    """Trend + mean reversion + noise, generated from a pinned seed.

    Every test in this package therefore sees identical bars on every machine.
    """
    rng = np.random.default_rng(seed)
    drift = np.linspace(0.0, 0.08, n)
    noise = rng.normal(0.0, 0.004, n).cumsum()
    cycle = 0.02 * np.sin(np.linspace(0.0, 14.0, n))
    close = 1.25 * np.exp(drift + noise + cycle)
    wick = np.abs(rng.normal(0.0, 0.0018, n)) + 0.0005
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum.reduce([close + wick, open_, close])
    low = np.minimum.reduce([close - wick, open_, close])
    index = pd.date_range("2021-01-04", periods=n, freq="4h", tz="UTC")
    data = {"open": open_, "high": high, "low": low, "close": close}
    if with_volume:
        data["volume"] = rng.uniform(500.0, 5000.0, n)
    return pd.DataFrame(data, index=index)


def zero_volume_ohlcv(n: int = 400) -> pd.DataFrame:
    """FX-style frame whose volume column exists but is identically zero."""
    df = synthetic_ohlcv(n).copy()
    df["volume"] = 0.0
    return df
