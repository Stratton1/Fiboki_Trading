"""rolling_ols_stats agrees with np.polyfit at XAUUSD price scale (audit P3-1).

The previous implementation formed each window's total sum of squares as
``sum(y^2) - (sum y)^2 / w`` from running cumulative sums. On a long,
high-priced series that subtracts two numbers of order ``N * price^2`` to
recover one of order ``w * sigma^2``: catastrophic cancellation. On XAUUSD M1
over a few years the r2 and t-statistic were noise. The windows are now
demeaned before squaring, so the error is in the window's own scale.
"""
from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from fiboki.marketstate.features import rolling_ols_stats


def _reference(y: np.ndarray, t: int, w: int) -> tuple[float, float, float]:
    seg = y[t - w + 1 : t + 1]
    x = np.arange(w, dtype=float)
    slope, intercept = np.polyfit(x, seg, 1)
    fit = intercept + slope * x
    ss_res = float(((seg - fit) ** 2).sum())
    ss_tot = float(((seg - seg.mean()) ** 2).sum())
    xc = x - x.mean()
    se = np.sqrt(ss_res / (w - 2) / float((xc * xc).sum()))
    return float(slope), 1.0 - ss_res / ss_tot, float(slope / se)


@settings(max_examples=40, deadline=None, derandomize=True)
@given(
    seed=st.integers(0, 10_000),
    level=st.sampled_from([1_800.0, 2_400.0, 3_000.0, 38_000.0]),  # XAUUSD, and JP225 scale
    sigma=st.floats(0.05, 2.0),
    window=st.integers(5, 120),
    prefix=st.integers(2_000, 60_000),
)
def test_matches_polyfit_on_long_high_priced_series(seed, level, sigma, window, prefix) -> None:
    rng = np.random.default_rng(seed)
    y = level + np.cumsum(rng.normal(0.0, sigma, prefix + window + 5))
    slope, r2, tstat = rolling_ols_stats(y, window)
    for t in (window - 1, prefix // 2, y.size - 1):
        ref_slope, ref_r2, ref_t = _reference(y, t, window)
        assert abs(slope[t] - ref_slope) <= 1e-8 * max(1.0, abs(ref_slope)) + 1e-9 * sigma
        assert abs(r2[t] - ref_r2) <= 1e-7
        assert abs(tstat[t] - ref_t) <= 1e-6 * max(1.0, abs(ref_t))


def test_a_million_bar_xauusd_series_keeps_its_r2() -> None:
    """Deterministic version of the audit case: 1,000,000 M1-sized steps at
    $2,000, window 20. The cumulative sums reach ~4e12, where one ULP is ~5e-4 --
    comparable to the window's own sum of squares. The demeaned form is exact to
    about 1e-9 here."""
    rng = np.random.default_rng(20260929)
    y = 2_000.0 + np.cumsum(rng.normal(0.0, 0.05, 1_000_000))
    slope, r2, tstat = rolling_ols_stats(y, 20)
    for t in (19, 500_000, 999_999):
        ref_slope, ref_r2, ref_t = _reference(y, t, 20)
        assert abs(r2[t] - ref_r2) < 1e-8
        assert abs(slope[t] - ref_slope) < 1e-9
        assert abs(tstat[t] - ref_t) < 1e-6 * max(1.0, abs(ref_t))
