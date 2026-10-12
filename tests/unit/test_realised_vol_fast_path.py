"""``realised_portfolio_vol``'s NumPy path equals the pandas definition exactly.

The engine calls the realised-vol function with the whole equity curve on
every decision bar; the pandas version made a 20-year backtest quadratic in its
length. The NumPy path is admissible only if it returns the SAME float, so the
comparison is ``==``, never a tolerance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.risk.accounting import (
    _realised_portfolio_vol_reference,
    realised_portfolio_vol,
)


def _curve(rng: np.random.Generator, n: int, kind: int) -> pd.Series:
    start = np.int64(1_136_160_000) * 10**9
    if kind % 3 == 0:  # regular H4
        steps = np.full(n - 1, 4 * 3600 * 10**9, dtype=np.int64)
    elif kind % 3 == 1:  # weekend gaps
        steps = rng.choice([4 * 3600, 52 * 3600], size=n - 1, p=[0.9, 0.1]) * 10**9
    else:  # irregular, odd-nanosecond spacing (median falls between two values)
        steps = rng.integers(1, 10**12, size=n - 1)
    ts = np.concatenate([[start], start + np.cumsum(steps)])
    values = 100_000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
    if kind % 5 == 0 and n > 4:
        values[rng.integers(0, n, size=max(1, n // 10))] = np.nan  # gaps
    if kind % 7 == 0 and n > 4:
        values[rng.integers(1, n)] = 0.0  # a zero (inf / nan returns)
    if kind % 11 == 0:
        values[:] = values[0]  # flat: sigma 0
    return pd.Series(values, index=pd.DatetimeIndex(ts, tz="UTC"))


@pytest.mark.parametrize("min_obs", [0, 1, 2, 20])
def test_numpy_path_equals_pandas_definition(min_obs: int) -> None:
    rng = np.random.default_rng(20261010 + min_obs)
    nonzero = 0
    for k in range(1500):
        n = int(rng.integers(1, 400)) if k % 4 else int(rng.integers(1, 30))
        curve = _curve(rng, n, k)
        fast = realised_portfolio_vol(curve, min_observations=min_obs)
        ref = _realised_portfolio_vol_reference(curve, min_observations=min_obs)
        assert fast == ref, (k, n, fast, ref)
        for ppy in (None, 2190.0):
            assert realised_portfolio_vol(
                curve, periods_per_year=ppy, min_observations=min_obs
            ) == _realised_portfolio_vol_reference(
                curve, periods_per_year=ppy, min_observations=min_obs
            )
        nonzero += fast > 0
    assert nonzero > 500


def test_long_engine_shaped_curve() -> None:
    rng = np.random.default_rng(7)
    n = 31_000
    curve = pd.Series(
        100_000.0 + np.cumsum(rng.normal(0.0, 50.0, n)),
        index=pd.date_range("2006-01-02", periods=n, freq="4h", tz="UTC"),
    )
    for end in (21, 22, 500, 9_999, 31_000):
        part = curve.iloc[:end]
        assert realised_portfolio_vol(part, min_observations=20) == (
            _realised_portfolio_vol_reference(part, min_observations=20)
        )


def test_non_series_inputs_still_take_the_reference_path() -> None:
    values = [100.0 + i for i in range(40)]
    index = pd.date_range("2020-01-01", periods=40, freq="D", tz="UTC")
    assert realised_portfolio_vol(values, index=index) == (
        _realised_portfolio_vol_reference(values, index=index)
    )
    assert realised_portfolio_vol(values) == 0.0  # no timestamps: refuse to annualise
