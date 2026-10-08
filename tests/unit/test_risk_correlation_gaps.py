"""Correlation over instruments with different trading calendars.

An index closes when FX does not, so the aligned close frame has gaps. The
estimate has always forward-filled them (pandas' old ``pct_change`` default).
That default is deprecated; the fill is now explicit so a pandas upgrade cannot
silently change the correlation the risk gateway and portfolio construction
read. Pinned here: same numbers as the implicit fill, and no FutureWarning.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from fiboki.risk.accounting import correlation_from_frames


def _frames() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2026-01-05", periods=120, freq="h", tz="UTC")
    fx = 1.1 * np.exp(np.cumsum(rng.normal(0, 1e-3, len(idx))))
    index = 5000 * np.exp(np.cumsum(rng.normal(0, 1e-3, len(idx))))
    index_series = pd.Series(index, index=idx)
    index_series.iloc[30:40] = np.nan  # market closed: a gap in the aligned frame
    return {
        "EURUSD": pd.DataFrame({"close": fx}, index=idx),
        "US500": pd.DataFrame({"close": index_series.dropna()}),
    }


def _reference(frames: dict[str, pd.DataFrame]) -> float:
    closes = pd.DataFrame({k: v["close"] for k, v in frames.items()}).sort_index()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rets = closes.pct_change().dropna(how="all")  # the implicit pad fill
    both = rets[["EURUSD", "US500"]].dropna()
    return float(both["EURUSD"].corr(both["US500"]))


def test_gapped_calendars_give_the_same_correlation_as_before_without_a_warning() -> None:
    frames = _frames()
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        matrix = correlation_from_frames(frames, min_observations=30)
    i, j = matrix.labels.index("EURUSD"), matrix.labels.index("US500")
    assert matrix.matrix[i][j] == _reference(frames)
