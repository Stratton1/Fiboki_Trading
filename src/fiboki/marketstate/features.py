"""Continuous per-instrument market-state features.

Every feature published here is **strictly causal**: the value on row ``i`` is a
function of rows ``0..i`` only. That is the same contract the indicator package
enforces (:mod:`fiboki.indicators.base`), and it is enforced the same way — by
corrupting the future and demanding a bit-identical past
(:func:`assert_features_causal`, exercised across every feature in
``tests/unit/test_marketstate_features.py``).

Three design rules, each of which exists because breaking it is how a research
platform lies to itself:

**1. No full-sample statistic, ever.**
A "volatility percentile" computed against the whole file tells bar 300 where it
sits in a distribution that includes 2024. That is look-ahead of the most
seductive kind, because the resulting regime labels look beautifully balanced.
Every percentile here is *expanding*: the rank of :math:`x_t` among
:math:`x_0..x_t` and nothing else. :func:`expanding_rank_pct` is the only
percentile primitive in this module, and
``tests/unit/test_marketstate_features.py`` proves that swapping it for a
full-sample rank changes the answer.

**2. Warmup is declared, not discovered.**
Each feature carries a :class:`FeatureSpec` with the number of bars that must
elapse before its value means anything. :attr:`FeatureSet.warmup` is the maximum
over the features actually requested, and :meth:`FeatureSet.valid` is the only
honest slice of the output.

**3. Absence is a value.**
HistData FX has no volume (the column is identically zero, stored as ``-1``) and
no bid/ask, so volume- and spread-derived features are published as ``NaN`` with
an explicit ``*_available`` flag rather than as a plausible-looking zero. V1's
OBV on zero volume produced a flat line that no one noticed for months.

Indicator maths is *not* re-implemented here. ATR, ADX, RSI, Donchian and
realised volatility come from :mod:`fiboki.indicators`, which is the single
place indicator calculations are allowed to live.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.indicators.trend import ADX
from fiboki.indicators.volatility import ATR, Donchian, RealisedVolatility

#: Bumped whenever a change here could alter a published feature value. Part of
#: every :class:`FeatureSet`'s fingerprint, so a stored regime label can always
#: be traced to the code that produced it.
FEATURE_ENGINE_VERSION = "1.0.0"

OHLC = ("open", "high", "low", "close")


# =====================================================================
# Errors
# =====================================================================


class FeatureError(ValueError):
    """The input frame cannot be turned into features. Raised, never patched."""


class InvalidBarError(FeatureError):
    """The frame contains bars that are not prices (non-positive or non-finite).

    The EURUSD H1 HistData file carries exactly one of these: a sentinel bar at
    2001-09-11 20:00 UTC whose OHLC are all ``-0.0001``. A single such bar
    poisons every log return, every rolling variance and every expanding
    percentile downstream of it. The engine refuses to guess what to do about
    it — pass ``on_invalid_bars="drop"`` and the drop is recorded on the
    resulting :class:`FeatureSet`.
    """


# =====================================================================
# Input hygiene
# =====================================================================


@dataclass(frozen=True, slots=True)
class DroppedBars:
    """Bars removed before feature computation, and why. Never silent."""

    timestamps: tuple[pd.Timestamp, ...]
    reason: str

    @property
    def count(self) -> int:
        return len(self.timestamps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "reason": self.reason,
            "timestamps": [str(t) for t in self.timestamps[:50]],
            "truncated": self.count > 50,
        }


def invalid_bar_mask(frame: pd.DataFrame) -> np.ndarray:
    """``True`` where a bar is not a usable price bar.

    A bar is invalid when any OHLC value is non-finite or non-positive, or when
    the high/low bracket is inconsistent with the open/close. Prices are strictly
    positive quantities; a zero or negative one is a sentinel, not a market.
    """
    arr = frame[list(OHLC)].to_numpy(dtype=float)
    bad = ~np.isfinite(arr).all(axis=1)
    bad |= (arr <= 0.0).any(axis=1)
    hi = arr[:, 1]
    lo = arr[:, 2]
    with np.errstate(invalid="ignore"):
        bad |= hi < lo
        bad |= hi < np.maximum(arr[:, 0], arr[:, 3])
        bad |= lo > np.minimum(arr[:, 0], arr[:, 3])
    return bad


def drop_invalid_bars(frame: pd.DataFrame) -> tuple[pd.DataFrame, DroppedBars]:
    """Remove non-price bars, returning the clean frame and the audit record.

    Dropping rather than masking is deliberate. A masked (NaN) bar propagates
    through every rolling window that touches it, so one sentinel bar would blank
    100+ bars of features; dropping it costs one bar and leaves a one-bar
    discontinuity in the return series, which is the smaller and more honest
    distortion. The drop is recorded and travels with the :class:`FeatureSet`.
    """
    bad = invalid_bar_mask(frame)
    if not bad.any():
        return frame, DroppedBars((), "none")
    stamps = tuple(pd.DatetimeIndex(frame.index[bad]))
    return frame.loc[~bad], DroppedBars(
        stamps,
        "non-finite, non-positive or geometrically impossible OHLC "
        "(sentinel bars are not prices)",
    )


# =====================================================================
# Causal numeric primitives
# =====================================================================


class _Fenwick:
    """Binary indexed tree over ``size`` compressed value codes."""

    __slots__ = ("_n", "_t")

    def __init__(self, size: int) -> None:
        self._n = size
        self._t = [0] * (size + 1)

    def add(self, i: int) -> None:
        n, t = self._n, self._t
        i += 1
        while i <= n:
            t[i] += 1
            i += i & (-i)

    def prefix(self, i: int) -> int:
        """Count of inserted codes in ``[0, i]``. ``i < 0`` returns 0."""
        t = self._t
        i += 1
        total = 0
        while i > 0:
            total += t[i]
            i -= i & (-i)
        return total


def expanding_rank_pct(
    values: Sequence[float] | np.ndarray | pd.Series,
    *,
    min_periods: int = 1,
) -> np.ndarray:
    """Percentile rank of each value within the expanding window ending at it.

    ``out[t] = (#{x_i < x_t} + 0.5 * #{x_i == x_t}) / n`` over ``i <= t``,
    counting only non-NaN observations. NaN inputs yield NaN and are not
    inserted. The result is in ``(0, 1)``.

    This is the *only* percentile in the market-state package. A full-sample
    ``scipy.stats.rankdata`` over the same series would use bars the strategy
    had not seen yet, and would systematically make early-sample volatility look
    calmer than it was, because the 2008 and 2020 tails are already in the
    denominator. ``tests/unit/test_marketstate_features.py`` asserts the two
    disagree on real-shaped data, so the difference cannot be dismissed as
    academic.

    Implementation note: values are coordinate-compressed against the whole
    array before the sweep. That is *not* look-ahead — compression only assigns
    order-preserving integer codes, and the counted set is still strictly the
    already-inserted prefix. The test suite pins this against a naive
    :math:`O(n^2)` reference.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    out = np.full(n, np.nan)
    if n == 0:
        return out
    valid = np.isfinite(arr)
    if not valid.any():
        return out
    uniq, codes_valid = np.unique(arr[valid], return_inverse=True)
    codes = np.full(n, -1, dtype=np.int64)
    codes[valid] = codes_valid
    tree = _Fenwick(uniq.size)
    seen = 0
    codes_list = codes.tolist()
    valid_list = valid.tolist()
    res = out
    for t in range(n):
        if not valid_list[t]:
            continue
        c = codes_list[t]
        tree.add(c)
        seen += 1
        if seen < min_periods:
            continue
        upto = tree.prefix(c)
        below = tree.prefix(c - 1)
        equal = upto - below
        res[t] = (below + 0.5 * equal) / seen
    return res


class ExpandingRankTracker:
    """Incremental, *exactly* equivalent form of :func:`expanding_rank_pct`.

    :func:`expanding_rank_pct` coordinate-compresses the whole array before its
    sweep, so it cannot be run one observation at a time: a streaming consumer
    does not have the future values the compression needs. That is why
    ``MarketStateEngine.ingest_bar`` re-ranked the entire retained history on
    every bar, and why capping the history turned an expanding percentile into a
    rolling one.

    This is the same statistic maintained over an order-statistic structure that
    only ever sees the past — a sorted list of the observations inserted so far,
    queried with :mod:`bisect`. Insertion is an ``O(n)`` ``memmove`` and a
    ``O(log n)`` search, which on the sizes this platform runs (tens of
    thousands of bars) is roughly two orders of magnitude cheaper than the
    ``O(n)`` Python sweep it replaces, and unlike a Fenwick tree it needs no
    knowledge of the value universe.

    **Exactness, not closeness.** The published value is
    ``(below + 0.5 * equal) / seen`` with ``below``, ``equal`` and ``seen``
    integers, computed from the same set of already-inserted observations the
    batch path counts. Identical integers through identical arithmetic give
    identical floats — there is no accumulation and therefore nothing to drift.
    ``tests/unit/test_marketstate_incremental.py`` pins this against both the
    batch path and the naive ``O(n^2)`` reference over every percentile-bearing
    feature series of the full 26,837-bar XAUUSD H4 file.

    Non-finite observations (NaN *and* infinities, matching ``np.isfinite`` in
    the batch path) are not inserted and yield NaN. ``min_periods`` suppresses
    the output until that many finite observations have been seen, exactly as
    the batch path does.
    """

    __slots__ = ("_min_periods", "_seen", "_sorted")

    def __init__(self, *, min_periods: int = 1) -> None:
        if min_periods < 1:
            raise FeatureError("min_periods must be >= 1")
        self._min_periods = int(min_periods)
        self._sorted: list[float] = []
        self._seen = 0

    @property
    def seen(self) -> int:
        """Finite observations inserted so far. The percentile's denominator."""
        return self._seen

    @property
    def min_periods(self) -> int:
        return self._min_periods

    def push(self, value: float) -> float:
        """Insert one observation and return its expanding percentile rank."""
        x = float(value)
        if not math.isfinite(x):
            return math.nan
        data = self._sorted
        below = bisect.bisect_left(data, x)
        # ``equal`` counts the observation being inserted, because the batch
        # path inserts into its Fenwick tree BEFORE it reads the counts. Getting
        # this wrong is an off-by-one that looks like a rounding difference.
        equal = bisect.bisect_right(data, x, below) - below + 1
        data.insert(below, x)
        self._seen += 1
        if self._seen < self._min_periods:
            return math.nan
        # Deliberately the same expression, in the same order, as the batch
        # path: integer counts, one float multiply, one float divide.
        return (below + 0.5 * equal) / self._seen

    def extend(
        self, values: Sequence[float] | np.ndarray | pd.Series
    ) -> np.ndarray:
        """Push a whole series, returning the ranks. Seeds a tracker from bulk."""
        arr = np.asarray(values, dtype=float)
        out = np.empty(arr.size, dtype=float)
        push = self.push
        for i, v in enumerate(arr.tolist()):
            out[i] = push(v)
        return out


def naive_expanding_rank_pct(
    values: Sequence[float] | np.ndarray, *, min_periods: int = 1
) -> np.ndarray:
    """O(n^2) reference implementation of :func:`expanding_rank_pct`.

    Kept in the library (not the tests) so the fast path can be re-verified from
    a REPL whenever it is touched.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    out = np.full(n, np.nan)
    seen: list[float] = []
    for t in range(n):
        x = arr[t]
        if not np.isfinite(x):
            continue
        seen.append(float(x))
        if len(seen) < min_periods:
            continue
        a = np.asarray(seen)
        below = float((a < x).sum())
        equal = float((a == x).sum())
        out[t] = (below + 0.5 * equal) / len(seen)
    return out


def rolling_ols_stats(
    y: np.ndarray, window: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Trailing-window OLS of ``y`` on bar index.

    Returns ``(slope, r2, tstat)`` aligned to the *last* bar of each window, so
    position ``t`` uses ``y[t-window+1 .. t]`` only.

    * ``slope``  — change in ``y`` per bar.
    * ``r2``     — how much of the window's variance the straight line explains,
      i.e. how *clean* the trend is, independent of its steepness.
    * ``tstat``  — ``slope / se(slope)``. The signed trend-strength measure the
      regime classifier uses: it combines steepness and cleanliness, and is
      scale-free, so it is comparable across instruments and timeframes.
    """
    arr = np.asarray(y, dtype=float)
    n = arr.size
    slope = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    tstat = np.full(n, np.nan)
    w = int(window)
    if w < 3 or n < w:
        return slope, r2, tstat
    if not np.isfinite(arr).all():
        # The convolution below would smear a single NaN across the whole
        # output. Rather than emit a half-defined column, say so: the caller's
        # input hygiene is what should have caught this.
        raise FeatureError(
            "rolling_ols_stats requires a finite series; non-finite prices must "
            "be removed explicitly (see drop_invalid_bars)"
        )

    x = np.arange(w, dtype=float)
    xc = x - x.mean()
    sxx = float((xc * xc).sum())
    # Sliding dot product with a fixed weight vector == valid convolution with
    # the reversed weights. O(n*w) flops, O(n) memory.
    dot = np.convolve(arr, xc[::-1], mode="valid")
    csum = np.concatenate([[0.0], np.cumsum(arr)])
    csum2 = np.concatenate([[0.0], np.cumsum(arr * arr)])
    sy = csum[w:] - csum[:-w]
    sy2 = csum2[w:] - csum2[:-w]
    sl = dot / sxx
    ss_tot = sy2 - (sy * sy) / w
    ss_reg = sl * sl * sxx
    ss_res = np.maximum(ss_tot - ss_reg, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        r2_v = np.where(ss_tot > 0.0, ss_reg / ss_tot, np.nan)
        se = np.sqrt(ss_res / (w - 2) / sxx)
        t_v = np.where(se > 0.0, sl / se, np.nan)
    slope[w - 1 :] = sl
    r2[w - 1 :] = r2_v
    tstat[w - 1 :] = t_v
    return slope, r2, tstat


def variance_ratio(
    log_price: pd.Series, *, lag: int, window: int, ddof: int = 1
) -> pd.Series:
    """Lo-MacKinlay variance ratio over a trailing window.

    ``VR(q) = Var(q-bar returns) / (q * Var(1-bar returns))``, using overlapping
    q-bar returns. ``VR ≈ 1`` is a random walk, ``> 1`` means returns persist
    (trending), ``< 1`` means they reverse (mean-reverting). Every input return
    at bar ``t`` is built from closes at or before ``t``, so the statistic is
    causal.
    """
    if lag < 2:
        raise ValueError("variance_ratio lag must be >= 2")
    if window < lag + 2:
        raise ValueError("variance_ratio window must exceed the lag")
    r1 = log_price.diff()
    rq = log_price.diff(lag)
    v1 = r1.rolling(window, min_periods=window).var(ddof=ddof)
    vq = rq.rolling(window, min_periods=window).var(ddof=ddof)
    denom = (v1 * float(lag)).replace(0.0, np.nan)
    return vq / denom


def hurst_from_variance_ratio(vr: pd.Series, lag: int) -> pd.Series:
    """Hurst exponent implied by a variance ratio.

    For a self-similar process ``VR(q) = q**(2H-1)``, hence
    ``H = 0.5 * (1 + log(VR)/log(q))``. ``H = 0.5`` is a random walk, ``> 0.5``
    persistent, ``< 0.5`` anti-persistent.

    Known approximation: this is a single-lag estimator, far noisier than a
    proper multi-lag R/S or DFA fit, and it inherits the variance ratio's small
    sample bias. It is published as an *indicator of tendency*, not a
    measurement, and the persistence regime axis is driven by the variance ratio
    itself rather than by this transform.
    """
    safe = vr.where(vr > 0.0)
    return 0.5 * (1.0 + np.log(safe) / math.log(lag))


def corwin_schultz_spread(
    high: pd.Series, low: pd.Series, *, smooth_window: int = 20
) -> tuple[pd.Series, pd.Series]:
    """Corwin-Schultz high/low effective-spread estimator.

    Returns ``(raw, smoothed)`` as a *fraction of price*. The estimator infers
    the bid-ask spread from the fact that a two-bar high/low range contains two
    bars' worth of spread while a one-bar range contains one. It needs only OHLC,
    which matters here because the HistData files carry no quotes at all.

    Negative raw estimates (common in quiet bars, an artefact of the estimator's
    small-sample noise) are floored at zero, as in the original paper.

    Known approximations: the estimator assumes continuous trading between the
    two bars, so overnight and weekend gaps inflate it; and on bid-only data it
    estimates the spread of the *bid* series, which is not quite the quoted
    spread. It is used here as a relative liquidity signal, never as a cost.
    """
    h = high.astype(float)
    ln_hl = np.log(h / low.astype(float))
    beta = (ln_hl**2) + (ln_hl**2).shift(1)
    h2 = pd.concat([h, h.shift(1)], axis=1).max(axis=1)
    l2 = pd.concat([low.astype(float), low.astype(float).shift(1)], axis=1).min(axis=1)
    gamma = np.log(h2 / l2) ** 2
    k = 3.0 - 2.0 * math.sqrt(2.0)
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    raw = spread.clip(lower=0.0)
    return raw, raw.rolling(smooth_window, min_periods=smooth_window).mean()


def rolling_autocorr(series: pd.Series, *, lag: int, window: int) -> pd.Series:
    """Trailing-window autocorrelation at ``lag``. Causal by construction."""
    return series.rolling(window, min_periods=window).corr(series.shift(lag))


# =====================================================================
# Sessions and clock features
# =====================================================================

#: Session windows in *venue-local* time, resolved through the tz database so
#: daylight saving is handled by construction rather than by an offset constant.
#: (start_hour, end_hour) half-open, local wall clock.
SESSION_WINDOWS: dict[str, tuple[str, float, float]] = {
    "tokyo": ("Asia/Tokyo", 9.0, 15.0),
    "london": ("Europe/London", 8.0, 16.5),
    "new_york": ("America/New_York", 8.0, 17.0),
}

#: Stable integer codes. Persisted in regime history, so never renumber.
SESSION_CODES: dict[str, int] = {
    "off_hours": 0,
    "tokyo": 1,
    "london": 2,
    "new_york": 3,
    "tokyo_london": 4,
    "london_new_york": 5,
}
SESSION_NAMES: dict[int, str] = {v: k for k, v in SESSION_CODES.items()}

#: Heuristic relative depth per session. Not measured — an ordering, used only
#: as one input to the liquidity axis, and documented as such.
SESSION_LIQUIDITY_WEIGHT: dict[int, float] = {
    0: 0.15,
    1: 0.45,
    2: 0.85,
    3: 0.80,
    4: 0.60,
    5: 1.00,
}


def session_flags(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Per-bar session membership. A pure function of the timestamp."""
    if index.tz is None:
        raise FeatureError("session flags require a tz-aware UTC index")
    out = pd.DataFrame(index=index)
    for name, (tz, start, end) in SESSION_WINDOWS.items():
        local = index.tz_convert(tz)
        hour = local.hour + local.minute / 60.0
        weekday = local.weekday
        open_ = (hour >= start) & (hour < end) & (weekday < 5)
        out[name] = np.asarray(open_, dtype=bool)
    # Annotated because each np.where below widens the shape type; the dtype is
    # pinned by the int64 seed and by the int codes in SESSION_CODES.
    code: np.ndarray = np.zeros(len(index), dtype=np.int64)
    tk = out["tokyo"].to_numpy()
    ld = out["london"].to_numpy()
    ny = out["new_york"].to_numpy()
    code = np.where(tk, SESSION_CODES["tokyo"], code)
    code = np.where(ld, SESSION_CODES["london"], code)
    code = np.where(ny, SESSION_CODES["new_york"], code)
    code = np.where(tk & ld, SESSION_CODES["tokyo_london"], code)
    code = np.where(ld & ny, SESSION_CODES["london_new_york"], code)
    out["session_code"] = code
    return out


# =====================================================================
# Configuration
# =====================================================================


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    """Every window and threshold the engine uses. Hashed into the fingerprint.

    Defaults are tuned for H4 bars. On H1 the windows describe roughly a quarter
    of the calendar span they do on H4; that is a deliberate choice to keep
    *bar-count* semantics stable across timeframes, because a regime defined in
    bars is what a bar-driven strategy actually experiences.
    """

    trend_horizons: tuple[int, ...] = (20, 50, 100)
    momentum_horizons: tuple[int, ...] = (5, 20, 50)
    vol_periods: tuple[int, ...] = (20, 100)
    atr_period: int = 14
    adx_period: int = 14
    parkinson_window: int = 20
    vol_of_vol_window: int = 20
    range_window: int = 20
    breakout_window: int = 20
    variance_ratio_window: int = 100
    variance_ratio_lags: tuple[int, ...] = (2, 5)
    autocorr_window: int = 100
    autocorr_lags: tuple[int, ...] = (1, 2)
    moment_window: int = 100
    gap_window: int = 100
    gap_atr_threshold: float = 0.5
    abnormal_window: int = 100
    abnormal_z_threshold: float = 4.0
    spread_window: int = 20
    volume_window: int = 50
    #: Observations required before an expanding percentile is published. Below
    #: this the rank is a statement about a handful of bars and means nothing.
    percentile_min_periods: int = 100
    #: Reference volatility period used by the volatility regime axis.
    primary_vol_period: int = 20
    #: Reference trend horizon used by the direction regime axis.
    primary_trend_horizon: int = 50

    def __post_init__(self) -> None:
        if self.primary_vol_period not in self.vol_periods:
            raise FeatureError(
                f"primary_vol_period {self.primary_vol_period} must be one of "
                f"vol_periods {self.vol_periods}"
            )
        if self.primary_trend_horizon not in self.trend_horizons:
            raise FeatureError(
                f"primary_trend_horizon {self.primary_trend_horizon} must be one "
                f"of trend_horizons {self.trend_horizons}"
            )
        if self.percentile_min_periods < 20:
            raise FeatureError("percentile_min_periods below 20 is not a percentile")
        for name in ("atr_period", "adx_period", "range_window", "breakout_window"):
            if getattr(self, name) < 2:
                raise FeatureError(f"{name} must be >= 2")

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}


# =====================================================================
# Feature descriptions
# =====================================================================


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """One published feature: what it is, and when it starts being true."""

    name: str
    group: str
    warmup: int
    description: str
    #: ``True`` when the feature can be legitimately absent (no volume, no
    #: quotes) rather than merely un-warmed.
    may_be_unavailable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "warmup": self.warmup,
            "description": self.description,
            "may_be_unavailable": self.may_be_unavailable,
        }


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """Computed features plus everything needed to trust or reject them."""

    instrument: str
    timeframe: str
    frame: pd.DataFrame
    specs: tuple[FeatureSpec, ...]
    fingerprint: str
    config: FeatureConfig
    dropped_bars: DroppedBars = field(default_factory=lambda: DroppedBars((), "none"))
    notes: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.specs)

    @property
    def warmup(self) -> int:
        """Bars before *every* feature is trustworthy."""
        return max((s.warmup for s in self.specs), default=0)

    def warmups(self) -> dict[str, int]:
        return {s.name: s.warmup for s in self.specs}

    def spec(self, name: str) -> FeatureSpec:
        for s in self.specs:
            if s.name == name:
                return s
        raise KeyError(f"unknown feature {name!r}")

    @property
    def ready_from(self) -> pd.Timestamp | None:
        """First timestamp at which the whole set is warm, or ``None``."""
        if len(self.frame) <= self.warmup:
            return None
        return self.frame.index[self.warmup]

    def valid(self) -> pd.DataFrame:
        """The warm slice. The only slice that should ever be ranked or fitted."""
        return self.frame.iloc[self.warmup :]

    def latest(self) -> dict[str, float]:
        if self.frame.empty:
            return {}
        return {k: float(v) for k, v in self.frame.iloc[-1].items()}

    def to_summary(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "rows": int(len(self.frame)),
            "features": len(self.specs),
            "warmup": self.warmup,
            "fingerprint": self.fingerprint,
            "dropped_bars": self.dropped_bars.to_dict(),
            "notes": list(self.notes),
        }


# =====================================================================
# The engine
# =====================================================================


class FeatureEngine:
    """Computes the full causal feature frame for one instrument's bars.

    The engine is stateless with respect to data: calling :meth:`compute` twice
    on the same frame returns identical values, and computing on ``df[:k+1]``
    returns exactly the first ``k+1`` rows of computing on ``df``. That
    *truncation equivalence* is what lets a backtest compute features once over
    history and a paper bot compute them bar by bar, and get the same answer.
    """

    def __init__(
        self,
        *,
        timeframe: Timeframe | str = Timeframe.H4,
        config: FeatureConfig | None = None,
        instrument: str = "UNKNOWN",
        on_invalid_bars: str = "raise",
    ) -> None:
        self.timeframe = (
            timeframe if isinstance(timeframe, Timeframe) else Timeframe(timeframe)
        )
        self.config = config or FeatureConfig()
        self.instrument = instrument.upper()
        if on_invalid_bars not in ("raise", "drop"):
            raise FeatureError("on_invalid_bars must be 'raise' or 'drop'")
        self.on_invalid_bars = on_invalid_bars
        self._specs: tuple[FeatureSpec, ...] = tuple(self._build_specs())
        self._fingerprint = self._compute_fingerprint()

    # ------------------------------------------------------------- meta

    @property
    def specs(self) -> tuple[FeatureSpec, ...]:
        return self._specs

    def feature_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self._specs)

    def warmups(self) -> dict[str, int]:
        return {s.name: s.warmup for s in self._specs}

    def spec_for(self, name: str) -> FeatureSpec:
        for s in self._specs:
            if s.name == name:
                return s
        raise KeyError(f"unknown feature {name!r}")

    @property
    def warmup(self) -> int:
        return max((s.warmup for s in self._specs), default=0)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def _compute_fingerprint(self) -> str:
        payload = {
            "engine_version": FEATURE_ENGINE_VERSION,
            "timeframe": self.timeframe.value,
            "config": self.config.to_dict(),
            "features": [s.to_dict() for s in self._specs],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:32]

    # ------------------------------------------------------- spec table

    def _pct_warmup(self, base: int) -> int:
        return base + self.config.percentile_min_periods

    def _build_specs(self) -> Iterable[FeatureSpec]:
        c = self.config
        s: list[FeatureSpec] = []
        add = s.append

        # -- trend ---------------------------------------------------
        for h in c.trend_horizons:
            add(FeatureSpec(f"trend_slope_{h}", "trend", h,
                            f"OLS slope of log close over {h} bars (log price per bar)"))
            add(FeatureSpec(f"trend_r2_{h}", "trend", h,
                            f"R^2 of that fit: how clean the {h}-bar trend is, 0..1"))
            add(FeatureSpec(f"trend_tstat_{h}", "trend", h,
                            f"slope/se(slope) over {h} bars: signed, scale-free "
                            "trend strength"))
            add(FeatureSpec(f"trend_direction_{h}", "trend", h,
                            f"sign of the {h}-bar slope, in {{-1,0,1}}"))
            add(FeatureSpec(f"trend_tstat_abs_pct_{h}", "trend", self._pct_warmup(h),
                            f"expanding percentile of |trend_tstat_{h}|"))
        add(FeatureSpec(f"adx_{c.adx_period}", "trend", c.adx_period * 2 + 1,
                        "Wilder ADX: undirected trend strength"))
        add(FeatureSpec("adx_pct", "trend", self._pct_warmup(c.adx_period * 2 + 1),
                        "expanding percentile of ADX"))
        add(FeatureSpec("di_spread", "trend", c.adx_period * 2 + 1,
                        "+DI minus -DI: directional balance"))

        # -- momentum ------------------------------------------------
        for h in c.momentum_horizons:
            add(FeatureSpec(f"mom_roc_{h}", "momentum", h + 1,
                            f"{h}-bar rate of change of close"))
            add(FeatureSpec(f"mom_z_{h}", "momentum", h,
                            f"(close - mean_{h}) / std_{h}: momentum in sigma units"))

        # -- volatility ----------------------------------------------
        for p in c.vol_periods:
            add(FeatureSpec(f"rv_{p}", "volatility", p + 1,
                            f"realised volatility: std of log returns over {p} bars"))
            add(FeatureSpec(f"rv_pct_{p}", "volatility", self._pct_warmup(p + 1),
                            f"expanding percentile of rv_{p} — never full-sample"))
        add(FeatureSpec(f"rv_ann_{c.primary_vol_period}", "volatility",
                        c.primary_vol_period + 1,
                        "annualised realised volatility at the primary period"))
        add(FeatureSpec("vol_of_vol", "volatility",
                        c.primary_vol_period + 1 + c.vol_of_vol_window,
                        "std of the realised-volatility series: volatility of volatility"))
        add(FeatureSpec("vol_of_vol_pct", "volatility",
                        self._pct_warmup(c.primary_vol_period + 1 + c.vol_of_vol_window),
                        "expanding percentile of vol_of_vol"))
        add(FeatureSpec(f"atr_{c.atr_period}", "volatility", c.atr_period + 1,
                        "Wilder ATR in price units"))
        add(FeatureSpec("atr_rel", "volatility", c.atr_period + 1,
                        "ATR as a fraction of close"))
        add(FeatureSpec("atr_pct", "volatility", self._pct_warmup(c.atr_period + 1),
                        "expanding percentile of atr_rel"))
        add(FeatureSpec("parkinson_vol", "volatility", c.parkinson_window,
                        "Parkinson high/low volatility estimator (per bar)"))

        # -- range and breakout --------------------------------------
        add(FeatureSpec("range_rel", "range", c.range_window,
                        f"{c.range_window}-bar high-low range as a fraction of close"))
        add(FeatureSpec("range_pct", "range", self._pct_warmup(c.range_window),
                        "expanding percentile of range_rel"))
        add(FeatureSpec("range_position", "range", c.range_window,
                        "where close sits inside the recent range, 0=low 1=high"))
        add(FeatureSpec("breakout_state", "range", c.breakout_window + 1,
                        "+1 close above the prior Donchian high, -1 below the prior low"))
        add(FeatureSpec("bars_since_breakout", "range", c.breakout_window + 1,
                        "bars elapsed since the last breakout in either direction"))
        add(FeatureSpec("breakout_frequency", "range",
                        c.breakout_window + 1 + c.gap_window,
                        "fraction of the recent window spent in a breakout state"))

        # -- persistence ---------------------------------------------
        for q in c.variance_ratio_lags:
            add(FeatureSpec(f"variance_ratio_{q}", "persistence",
                            c.variance_ratio_window + q + 1,
                            f"Lo-MacKinlay VR({q}); >1 trending, <1 mean-reverting"))
            add(FeatureSpec(f"variance_ratio_{q}_pct", "persistence",
                            self._pct_warmup(c.variance_ratio_window + q + 1),
                            f"expanding percentile of variance_ratio_{q}"))
        primary_q = c.variance_ratio_lags[0]
        add(FeatureSpec("hurst", "persistence",
                        c.variance_ratio_window + primary_q + 1,
                        f"Hurst exponent implied by VR({primary_q}); 0.5 = random walk"))
        for lag in c.autocorr_lags:
            add(FeatureSpec(f"autocorr_{lag}", "persistence",
                            c.autocorr_window + lag + 1,
                            f"trailing autocorrelation of log returns at lag {lag}"))

        # -- return distribution -------------------------------------
        add(FeatureSpec("ret_skew", "distribution", c.moment_window + 1,
                        f"skew of the last {c.moment_window} log returns"))
        add(FeatureSpec("ret_kurtosis", "distribution", c.moment_window + 1,
                        f"excess kurtosis of the last {c.moment_window} log returns"))
        add(FeatureSpec("ret_skew_pct", "distribution",
                        self._pct_warmup(c.moment_window + 1),
                        "expanding percentile of ret_skew"))

        # -- gaps and shocks -----------------------------------------
        add(FeatureSpec("gap_atr", "shock", c.atr_period + 2,
                        "(open - previous close) / ATR"))
        add(FeatureSpec("gap_frequency", "shock", c.atr_period + 2 + c.gap_window,
                        f"fraction of the last {c.gap_window} bars with "
                        f"|gap| > {c.gap_atr_threshold} ATR"))
        add(FeatureSpec("is_calendar_gap", "shock", 2,
                        "1 when the bar follows a clock gap wider than one bar "
                        "(weekend, holiday or missing data)"))
        add(FeatureSpec("ret_z", "shock", c.abnormal_window + 1,
                        "log return in units of its own trailing standard deviation"))
        add(FeatureSpec("abnormal_move", "shock", c.abnormal_window + 1,
                        f"1 when |ret_z| exceeds {c.abnormal_z_threshold}"))
        add(FeatureSpec("abnormal_frequency", "shock", c.abnormal_window * 2 + 1,
                        "fraction of the recent window flagged abnormal"))

        # -- spread / liquidity --------------------------------------
        add(FeatureSpec("spread_rel", "liquidity", 1,
                        "quoted spread as a fraction of mid, when the data carries "
                        "bid and ask", may_be_unavailable=True))
        add(FeatureSpec("spread_pct", "liquidity", self._pct_warmup(1),
                        "expanding percentile of spread_rel", may_be_unavailable=True))
        add(FeatureSpec("spread_available", "liquidity", 1,
                        "1 when the dataset actually carries bid and ask columns"))
        add(FeatureSpec("cs_spread_rel", "liquidity", c.spread_window + 1,
                        "Corwin-Schultz effective spread estimated from high/low"))
        add(FeatureSpec("cs_spread_pct", "liquidity",
                        self._pct_warmup(c.spread_window + 1),
                        "expanding percentile of cs_spread_rel"))
        add(FeatureSpec("volume_z", "liquidity", c.volume_window,
                        "volume relative to its trailing mean, in sigma units",
                        may_be_unavailable=True))
        add(FeatureSpec("volume_available", "liquidity", 1,
                        "1 when the dataset carries real volume (not the -1 absent "
                        "marker and not identically zero)"))
        add(FeatureSpec("liquidity_proxy", "liquidity",
                        self._pct_warmup(c.spread_window + 1),
                        "session depth weight x (1 - cs_spread_pct): a heuristic "
                        "0..1 liquidity score, not a measurement"))

        # -- clock ---------------------------------------------------
        add(FeatureSpec("session_code", "clock", 1,
                        "0 off-hours, 1 Tokyo, 2 London, 3 New York, "
                        "4 Tokyo/London overlap, 5 London/New York overlap"))
        add(FeatureSpec("is_london_ny_overlap", "clock", 1,
                        "1 during the London/New York overlap"))
        add(FeatureSpec("session_weight", "clock", 1,
                        "heuristic relative depth of the current session"))
        add(FeatureSpec("hour_utc", "clock", 1, "UTC hour of the bar start"))
        add(FeatureSpec("day_of_week", "clock", 1, "Monday=0 .. Sunday=6, UTC"))
        add(FeatureSpec("minute_of_week", "clock", 1,
                        "minutes since Monday 00:00 UTC; a continuous clock coordinate"))
        return s

    # ---------------------------------------------------------- compute

    def compute(self, frame: pd.DataFrame) -> FeatureSet:
        """Compute every feature over ``frame``. Never mutates the input."""
        self._validate(frame)
        notes: list[str] = []
        bad = invalid_bar_mask(frame)
        if bad.any():
            if self.on_invalid_bars == "raise":
                stamps = list(pd.DatetimeIndex(frame.index[bad])[:5])
                raise InvalidBarError(
                    f"{int(bad.sum())} bar(s) are not prices (first: {stamps}). "
                    "Pass on_invalid_bars='drop' to remove them and have the drop "
                    "recorded, or clean the dataset upstream. The engine will not "
                    "quietly compute statistics over a sentinel."
                )
            df, dropped = drop_invalid_bars(frame)
            notes.append(
                f"dropped {dropped.count} invalid bar(s) before computing features"
            )
        else:
            df, dropped = frame, DroppedBars((), "none")

        out = self._compute_columns(df, notes)
        ordered = [s.name for s in self._specs]
        missing = [n for n in ordered if n not in out]
        if missing:  # pragma: no cover - guards a spec/impl drift
            raise FeatureError(f"declared features not produced: {missing}")
        extra = [n for n in out if n not in set(ordered)]
        if extra:  # pragma: no cover
            raise FeatureError(f"undeclared features produced: {extra}")
        result = pd.DataFrame({n: out[n] for n in ordered}, index=df.index)
        result.index.name = "timestamp"
        instrument = self.instrument
        if "instrument" in df.columns and len(df):
            instrument = str(df["instrument"].iloc[0]).upper()
        return FeatureSet(
            instrument=instrument,
            timeframe=self.timeframe.value,
            frame=result,
            specs=self._specs,
            fingerprint=self._fingerprint,
            config=self.config,
            dropped_bars=dropped,
            notes=tuple(notes),
        )

    # -- validation --------------------------------------------------

    @staticmethod
    def _validate(frame: pd.DataFrame) -> None:
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise FeatureError("feature input needs a DatetimeIndex")
        if frame.index.tz is None:
            raise FeatureError(
                "feature input index is tz-naive. V2 never guesses a timezone — "
                "this is the HistData EST-without-DST bug."
            )
        if not frame.index.is_monotonic_increasing:
            raise FeatureError("feature input index must be monotonically increasing")
        if frame.index.has_duplicates:
            raise FeatureError("feature input index has duplicate timestamps")
        missing = [c for c in OHLC if c not in frame.columns]
        if missing:
            raise FeatureError(f"feature input missing columns {missing}")
        if len(frame) == 0:
            raise FeatureError("feature input is empty")

    # -- the actual maths --------------------------------------------

    def _compute_columns(
        self, df: pd.DataFrame, notes: list[str]
    ) -> dict[str, np.ndarray | pd.Series]:
        c = self.config
        idx = df.index
        n = len(df)
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        open_ = df["open"].astype(float)
        log_close = np.log(close)
        log_ret = log_close.diff()
        pmin = c.percentile_min_periods
        out: dict[str, np.ndarray | pd.Series] = {}

        def pct(series: pd.Series | np.ndarray) -> np.ndarray:
            return expanding_rank_pct(np.asarray(series, dtype=float), min_periods=pmin)

        # -- trend ---------------------------------------------------
        lc = log_close.to_numpy()
        for h in c.trend_horizons:
            slope, r2, tstat = rolling_ols_stats(lc, h)
            out[f"trend_slope_{h}"] = slope
            out[f"trend_r2_{h}"] = r2
            out[f"trend_tstat_{h}"] = tstat
            with np.errstate(invalid="ignore"):
                out[f"trend_direction_{h}"] = np.sign(slope)
            out[f"trend_tstat_abs_pct_{h}"] = pct(np.abs(tstat))

        adx_ind = ADX(period=c.adx_period)
        adx_frame = adx_ind.compute(df[list(OHLC)])
        adx_col = adx_frame[adx_ind.name]
        out[f"adx_{c.adx_period}"] = adx_col
        out["adx_pct"] = pct(adx_col)
        out["di_spread"] = (
            adx_frame[f"{adx_ind.name}_plus_di"] - adx_frame[f"{adx_ind.name}_minus_di"]
        )

        # -- momentum ------------------------------------------------
        for h in c.momentum_horizons:
            out[f"mom_roc_{h}"] = close / close.shift(h) - 1.0
            mean_h = close.rolling(h, min_periods=h).mean()
            std_h = close.rolling(h, min_periods=h).std(ddof=1).replace(0.0, np.nan)
            out[f"mom_z_{h}"] = (close - mean_h) / std_h

        # -- volatility ----------------------------------------------
        rv_series: dict[int, pd.Series] = {}
        for p in c.vol_periods:
            ind = RealisedVolatility(period=p)
            rv = ind.compute(df[list(OHLC)])[ind.name]
            rv_series[p] = rv
            out[f"rv_{p}"] = rv
            out[f"rv_pct_{p}"] = pct(rv)
        primary_rv = rv_series[c.primary_vol_period]
        out[f"rv_ann_{c.primary_vol_period}"] = primary_rv * math.sqrt(
            self.timeframe.bars_per_year
        )
        vov = primary_rv.rolling(
            c.vol_of_vol_window, min_periods=c.vol_of_vol_window
        ).std(ddof=1)
        out["vol_of_vol"] = vov
        out["vol_of_vol_pct"] = pct(vov)

        atr_ind = ATR(period=c.atr_period)
        atr = atr_ind.compute(df[list(OHLC)])[atr_ind.name]
        out[f"atr_{c.atr_period}"] = atr
        atr_rel = atr / close
        out["atr_rel"] = atr_rel
        out["atr_pct"] = pct(atr_rel)

        # Parkinson: sigma^2 = mean( ln(H/L)^2 ) / (4 ln 2), per bar.
        hl2 = np.log(high / low) ** 2
        out["parkinson_vol"] = np.sqrt(
            hl2.rolling(c.parkinson_window, min_periods=c.parkinson_window).mean()
            / (4.0 * math.log(2.0))
        )

        # -- range and breakout --------------------------------------
        w = c.range_window
        roll_hi = high.rolling(w, min_periods=w).max()
        roll_lo = low.rolling(w, min_periods=w).min()
        span = (roll_hi - roll_lo).replace(0.0, np.nan)
        range_rel = span / close
        out["range_rel"] = range_rel
        out["range_pct"] = pct(range_rel)
        out["range_position"] = ((close - roll_lo) / span).clip(0.0, 1.0)

        don = Donchian(period=c.breakout_window)
        don_frame = don.compute(df[list(OHLC)])
        upper_prior = don_frame[f"{don.name}_upper_prior"]
        lower_prior = don_frame[f"{don.name}_lower_prior"]
        state = np.zeros(n)
        up = (close > upper_prior).to_numpy()
        dn = (close < lower_prior).to_numpy()
        state[up] = 1.0
        state[dn] = -1.0
        undefined = upper_prior.isna().to_numpy()
        state[undefined] = np.nan
        out["breakout_state"] = state
        positions = np.arange(n, dtype=float)
        marker = np.where(np.abs(np.nan_to_num(state)) > 0.0, positions, np.nan)
        last_break = pd.Series(marker, index=idx).ffill().to_numpy()
        bars_since = positions - last_break
        bars_since[undefined] = np.nan
        out["bars_since_breakout"] = bars_since
        in_breakout = pd.Series(
            np.where(np.isnan(state), np.nan, np.abs(state)), index=idx
        )
        out["breakout_frequency"] = in_breakout.rolling(
            c.gap_window, min_periods=c.gap_window
        ).mean()

        # -- persistence ---------------------------------------------
        for q in c.variance_ratio_lags:
            vr = variance_ratio(log_close, lag=q, window=c.variance_ratio_window)
            out[f"variance_ratio_{q}"] = vr
            out[f"variance_ratio_{q}_pct"] = pct(vr)
        primary_q = c.variance_ratio_lags[0]
        out["hurst"] = hurst_from_variance_ratio(
            variance_ratio(log_close, lag=primary_q, window=c.variance_ratio_window),
            primary_q,
        )
        for lag in c.autocorr_lags:
            out[f"autocorr_{lag}"] = rolling_autocorr(
                log_ret, lag=lag, window=c.autocorr_window
            )

        # -- return distribution -------------------------------------
        mw = c.moment_window
        skew = log_ret.rolling(mw, min_periods=mw).skew()
        out["ret_skew"] = skew
        out["ret_kurtosis"] = log_ret.rolling(mw, min_periods=mw).kurt()
        out["ret_skew_pct"] = pct(skew)

        # -- gaps and shocks -----------------------------------------
        gap = open_ - close.shift(1)
        gap_atr = gap / atr.replace(0.0, np.nan)
        out["gap_atr"] = gap_atr
        big_gap = (gap_atr.abs() > c.gap_atr_threshold).astype(float)
        big_gap[gap_atr.isna()] = np.nan
        out["gap_frequency"] = big_gap.rolling(
            c.gap_window, min_periods=c.gap_window
        ).mean()

        step = pd.Timedelta(minutes=self.timeframe.minutes)
        delta = pd.Series(idx, index=idx).diff()
        calendar_gap = (delta > step * 1.5).astype(float)
        calendar_gap.iloc[0] = np.nan
        out["is_calendar_gap"] = calendar_gap

        aw = c.abnormal_window
        ret_std = log_ret.rolling(aw, min_periods=aw).std(ddof=1).replace(0.0, np.nan)
        ret_z = log_ret / ret_std
        out["ret_z"] = ret_z
        abnormal = (ret_z.abs() > c.abnormal_z_threshold).astype(float)
        abnormal[ret_z.isna()] = np.nan
        out["abnormal_move"] = abnormal
        out["abnormal_frequency"] = abnormal.rolling(aw, min_periods=aw).mean()

        # -- spread / liquidity --------------------------------------
        has_quotes = {"bid_close", "ask_close"}.issubset(df.columns)
        if has_quotes:
            bid = df["bid_close"].astype(float)
            ask = df["ask_close"].astype(float)
            mid = (bid + ask) / 2.0
            spread_rel = (ask - bid) / mid.replace(0.0, np.nan)
            usable = spread_rel.notna().any()
        else:
            spread_rel = pd.Series(np.nan, index=idx)
            usable = False
        if not usable:
            notes.append(
                "no bid/ask columns: quoted-spread features are published as NaN "
                "with spread_available=0 rather than as a modelled constant"
            )
        out["spread_rel"] = spread_rel
        out["spread_pct"] = pct(spread_rel) if usable else np.full(n, np.nan)
        out["spread_available"] = np.full(n, 1.0 if usable else 0.0)

        _cs_raw, cs_smooth = corwin_schultz_spread(
            high, low, smooth_window=c.spread_window
        )
        out["cs_spread_rel"] = cs_smooth
        cs_pct = pct(cs_smooth)
        out["cs_spread_pct"] = cs_pct

        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
            # -1 is the canonical "absent" marker; 0 is FX's fake volume.
            vol = vol.where(vol > 0.0)
            vol_ok = bool(vol.notna().any())
        else:
            vol = pd.Series(np.nan, index=idx)
            vol_ok = False
        if not vol_ok:
            notes.append(
                "volume is absent or identically zero: volume features are NaN "
                "with volume_available=0. Volume-dependent logic must be blocked "
                "on this dataset, not fed zeros."
            )
            out["volume_z"] = np.full(n, np.nan)
        else:
            vm = vol.rolling(c.volume_window, min_periods=c.volume_window).mean()
            vs = (
                vol.rolling(c.volume_window, min_periods=c.volume_window)
                .std(ddof=1)
                .replace(0.0, np.nan)
            )
            out["volume_z"] = (vol - vm) / vs
        out["volume_available"] = np.full(n, 1.0 if vol_ok else 0.0)

        # -- clock ---------------------------------------------------
        sessions = session_flags(idx)
        code = sessions["session_code"].to_numpy()
        out["session_code"] = code.astype(float)
        out["is_london_ny_overlap"] = (
            code == SESSION_CODES["london_new_york"]
        ).astype(float)
        weight_table = np.array(
            [SESSION_LIQUIDITY_WEIGHT[i] for i in range(len(SESSION_CODES))],
            dtype=float,
        )
        weight = weight_table[code]
        out["session_weight"] = weight
        out["liquidity_proxy"] = weight * (1.0 - cs_pct)

        utc = idx
        out["hour_utc"] = utc.hour.to_numpy().astype(float)
        dow = utc.weekday.to_numpy().astype(float)
        out["day_of_week"] = dow
        out["minute_of_week"] = (
            dow * 1440.0 + utc.hour.to_numpy() * 60.0 + utc.minute.to_numpy()
        )
        return out


# =====================================================================
# Causality proof
# =====================================================================


def corrupt_future(df: pd.DataFrame, k: int, seed: int = 20240719) -> pd.DataFrame:
    """Replace every bar strictly after ``k`` with violently different prices.

    Same idea as :func:`fiboki.indicators.base._corrupt_future`, restated here so
    the market-state package does not depend on a private symbol. The corruption
    is multiplicative and large, so any leak shows up as a visible divergence
    rather than a rounding difference.
    """
    rng = np.random.default_rng(seed)
    out = df.copy()
    tail = len(df) - k - 1
    if tail <= 0:
        raise ValueError("cannot corrupt the future of the last bar")
    base = float(df["close"].iloc[k])
    shocked = base * (2.0 + rng.uniform(0.0, 40.0, size=tail))
    out.iloc[k + 1 :, out.columns.get_loc("open")] = shocked
    out.iloc[k + 1 :, out.columns.get_loc("high")] = shocked * 1.05
    out.iloc[k + 1 :, out.columns.get_loc("low")] = shocked * 0.95
    out.iloc[k + 1 :, out.columns.get_loc("close")] = shocked
    for col in ("bid_close", "ask_close"):
        if col in out.columns:
            out.iloc[k + 1 :, out.columns.get_loc(col)] = shocked
    if "volume" in out.columns:
        vals = rng.uniform(1.0, 1e6, size=tail)
        if out["volume"].dtype.kind in "iu":
            vals = vals.astype(out["volume"].dtype)
        out.iloc[k + 1 :, out.columns.get_loc("volume")] = vals
    return out


def _prefix_equal(a: np.ndarray, b: np.ndarray, k: int) -> tuple[bool, int]:
    x = np.asarray(a, dtype=float)[: k + 1]
    y = np.asarray(b, dtype=float)[: k + 1]
    nan_diff = np.isnan(x) != np.isnan(y)
    val_diff = (~np.isnan(x)) & (~np.isnan(y)) & (x != y)
    bad = nan_diff | val_diff
    if not bad.any():
        return True, -1
    return False, int(np.argmax(bad))


def assert_features_causal(
    engine: FeatureEngine,
    frame: pd.DataFrame,
    k: int,
    columns: Iterable[str] | None = None,
) -> None:
    """Prove that features at bars ``<= k`` ignore bars ``> k``.

    Raises :class:`CausalityError` on the first violating column.
    """
    from fiboki.indicators.base import CausalityError

    clean = engine.compute(frame).frame
    dirty = engine.compute(corrupt_future(frame, k)).frame
    cols = tuple(columns) if columns is not None else tuple(clean.columns)
    for col in cols:
        ok, first = _prefix_equal(clean[col].to_numpy(), dirty[col].to_numpy(), k)
        if not ok:
            raise CausalityError(
                f"feature {col!r} changed at or before bar {k} (first divergence "
                f"at bar {first}) when only bars after {k} were altered. "
                "The feature reads the future."
            )
