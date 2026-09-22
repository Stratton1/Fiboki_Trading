"""The incremental expanding percentile, held to *exact* equality.

:func:`fiboki.marketstate.features.expanding_rank_pct` coordinate-compresses the
whole array before its sweep, so it cannot be advanced one observation at a
time. :class:`ExpandingRankTracker` is the same statistic over an
order-statistic structure that only ever sees the past.

"Close" is not the bar here. A percentile that drifts in the last bits is a
percentile that crosses a regime threshold on a different bar, and a regime that
changes on a different bar is a different research result. Every assertion in
this module is bit-equality: identical NaN masks and identical float64 values,
never ``approx``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.marketstate.features import (
    ExpandingRankTracker,
    FeatureEngine,
    FeatureError,
    expanding_rank_pct,
    naive_expanding_rank_pct,
)

XAUUSD_H4 = Path("data/canonical/histdata/XAUUSD/xauusd_h4.parquet")
requires_xauusd = pytest.mark.skipif(
    not XAUUSD_H4.exists(), reason=f"XAUUSD H4 not present at {XAUUSD_H4}"
)


def assert_bit_identical(left: np.ndarray, right: np.ndarray, label: str) -> None:
    """Identical NaN masks and identical float64 values. No tolerance."""
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    assert a.shape == b.shape, f"{label}: shapes differ"
    mask_a, mask_b = np.isnan(a), np.isnan(b)
    bad_mask = np.flatnonzero(mask_a != mask_b)
    assert bad_mask.size == 0, (
        f"{label}: NaN placement differs, first at index {bad_mask[:5].tolist()}"
    )
    finite = ~mask_a
    bad = np.flatnonzero(a[finite] != b[finite])
    if bad.size:
        i = int(np.flatnonzero(finite)[bad[0]])
        raise AssertionError(
            f"{label}: values differ at index {i}: {a[i]!r} != {b[i]!r} "
            f"({bad.size} of {int(finite.sum())} finite values differ)"
        )


# =====================================================================
# The primitive
# =====================================================================


def test_tracker_matches_the_naive_reference_including_ties() -> None:
    """The mid-rank tie convention is the thing that is easy to get wrong."""
    values = [1.0, 1.0, 2.0, 1.0, 3.0, 2.0, 2.0, 0.0, 1.0, 3.0, 3.0, 3.0]
    tracker = ExpandingRankTracker(min_periods=1)
    assert_bit_identical(
        tracker.extend(values),
        naive_expanding_rank_pct(values, min_periods=1),
        "ties vs naive",
    )


def test_tracker_matches_the_batch_path_on_a_random_series() -> None:
    rng = np.random.default_rng(20260920)
    # Heavy tails and a deliberate blob of repeated values, because ranks are
    # only interesting where the distribution is not smooth.
    values = np.concatenate(
        [rng.standard_t(3, size=600), np.full(120, 0.25), rng.normal(size=280)]
    )
    rng.shuffle(values)
    tracker = ExpandingRankTracker(min_periods=1)
    assert_bit_identical(
        tracker.extend(values), expanding_rank_pct(values), "random vs batch"
    )


@pytest.mark.parametrize("min_periods", [1, 2, 20, 100])
def test_min_periods_suppresses_exactly_what_the_batch_path_suppresses(
    min_periods: int,
) -> None:
    rng = np.random.default_rng(7)
    values = rng.normal(size=400)
    tracker = ExpandingRankTracker(min_periods=min_periods)
    assert_bit_identical(
        tracker.extend(values),
        expanding_rank_pct(values, min_periods=min_periods),
        f"min_periods={min_periods}",
    )


def test_non_finite_observations_are_skipped_not_inserted() -> None:
    """NaN and infinities are excluded from the denominator, as in the batch."""
    values = [1.0, np.nan, 2.0, np.inf, 3.0, -np.inf, 4.0, np.nan, 0.5]
    tracker = ExpandingRankTracker(min_periods=1)
    got = tracker.extend(values)
    assert_bit_identical(got, expanding_rank_pct(values), "non-finite")
    assert tracker.seen == 5, "only the five finite observations count"
    assert np.isnan(got[[1, 3, 5, 7]]).all()


def test_push_returns_the_same_value_extend_records() -> None:
    rng = np.random.default_rng(3)
    values = rng.normal(size=250)
    bulk = ExpandingRankTracker(min_periods=10).extend(values)
    one_at_a_time = ExpandingRankTracker(min_periods=10)
    streamed = np.array([one_at_a_time.push(float(v)) for v in values])
    assert_bit_identical(streamed, bulk, "push vs extend")


def test_a_percentile_is_strictly_inside_the_unit_interval() -> None:
    rng = np.random.default_rng(11)
    got = ExpandingRankTracker().extend(rng.normal(size=500))
    assert np.isfinite(got).all()
    assert (got > 0.0).all() and (got < 1.0).all()


def test_min_periods_below_one_is_refused() -> None:
    with pytest.raises(FeatureError, match="min_periods must be >= 1"):
        ExpandingRankTracker(min_periods=0)


def test_the_tracker_never_sees_the_future() -> None:
    """A tracker fed a prefix must agree with one fed the whole series.

    This is the property the Fenwick path gets for free from its sweep and the
    tracker has to earn: corrupting the tail must not move a single published
    rank in the head.
    """
    rng = np.random.default_rng(99)
    values = rng.normal(size=400)
    corrupted = values.copy()
    corrupted[200:] = rng.uniform(1e6, 1e9, size=200)
    clean = ExpandingRankTracker(min_periods=20).extend(values)
    dirty = ExpandingRankTracker(min_periods=20).extend(corrupted)
    assert_bit_identical(clean[:200], dirty[:200], "prefix under corruption")


# =====================================================================
# On real data, over every series the feature engine actually ranks
# =====================================================================

#: The feature-engine columns that feed an expanding percentile. Ranking a
#: synthetic random walk proves nothing about tie density, repeated zeros or
#: the long flat stretches a real gap-frequency series contains.
RANKED_SOURCES = (
    "trend_tstat_20",
    "trend_tstat_50",
    "trend_tstat_100",
    "adx_14",
    "rv_20",
    "rv_100",
    "vol_of_vol",
    "atr_rel",
    "range_rel",
    "variance_ratio_2",
    "variance_ratio_5",
    "ret_skew",
    "cs_spread_rel",
    "liquidity_proxy",
    "gap_frequency",
    "abnormal_frequency",
)


@pytest.fixture(scope="module")
def xauusd_ranked_series() -> dict[str, np.ndarray]:
    frame = pd.read_parquet(XAUUSD_H4)
    engine = FeatureEngine(
        timeframe=Timeframe.H4, instrument="XAUUSD", on_invalid_bars="drop"
    )
    # Reaching past the public API on purpose: this tests the ranked SERIES,
    # not the published percentile columns, so it needs the inputs to pct().
    columns = engine._compute_columns(frame, [])
    out: dict[str, np.ndarray] = {}
    for name in RANKED_SOURCES:
        arr = np.asarray(columns[name], dtype=float)
        # trend_tstat_* is ranked by magnitude, everything else as published.
        out[name] = np.abs(arr) if name.startswith("trend_tstat_") else arr
    return out


@requires_xauusd
@pytest.mark.parametrize("name", RANKED_SOURCES)
def test_tracker_equals_batch_on_every_ranked_xauusd_series(
    xauusd_ranked_series: dict[str, np.ndarray], name: str
) -> None:
    """26,837 real bars, bit for bit, on every series the engine ranks."""
    values = xauusd_ranked_series[name]
    assert values.size == 26837, "the XAUUSD H4 file is expected to be whole"
    assert np.isfinite(values).sum() > 26000, f"{name} is mostly NaN; nothing proven"
    assert_bit_identical(
        ExpandingRankTracker(min_periods=100).extend(values),
        expanding_rank_pct(values, min_periods=100),
        f"XAUUSD {name}",
    )


@requires_xauusd
@pytest.mark.parametrize("name", ["atr_rel", "variance_ratio_2", "gap_frequency"])
def test_tracker_equals_the_naive_quadratic_reference_on_real_data(
    xauusd_ranked_series: dict[str, np.ndarray], name: str
) -> None:
    """Against the O(n^2) definition itself, not just the fast batch path.

    Capped at 4,000 bars because the reference is quadratic; that is still 8
    million comparisons per series against a definition with no shared code.
    """
    values = xauusd_ranked_series[name][:4000]
    assert_bit_identical(
        ExpandingRankTracker(min_periods=100).extend(values),
        naive_expanding_rank_pct(values, min_periods=100),
        f"XAUUSD {name} vs naive",
    )
