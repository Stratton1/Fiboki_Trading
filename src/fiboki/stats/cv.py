"""Purged and embargoed cross-validation for overlapping financial labels.

Plain k-fold leaks.  A trade opened on Monday and closed on Friday has a label
that spans the whole week; if Monday is in the training fold and Wednesday in the
test fold, the training label already contains the test period's outcome.  V1's
walk-forward had no purge and no embargo, so every reported out-of-sample number
was contaminated by construction at the fold boundaries.

Two estimators:

* :class:`PurgedKFold` - contiguous test folds, training samples whose label span
  overlaps the test span are PURGED, and samples immediately after the test fold
  are EMBARGOED to kill residual serial correlation.
* :class:`CombinatorialPurgedCV` - test folds are every combination of ``k`` of
  ``N`` groups, which yields ``C(N,k)*k/N`` complete backtest PATHS rather than
  the single path a walk-forward gives you.  One path is one sample of the
  performance distribution; a decision made on one path is a decision made on a
  sample of size one.

References
----------
Lopez de Prado, M. (2018). "Advances in Financial Machine Learning", ch. 7 and 12.
"""
from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

__all__ = [
    "CVSplit",
    "CombinatorialPurgedCV",
    "LabelSpans",
    "PurgedKFold",
    "n_backtest_paths",
]


def n_backtest_paths(n_groups: int, k: int) -> int:
    """Number of complete backtest paths from combinatorial purged CV.

    ``C(N, k) * k / N``, which equals ``C(N-1, k-1)``.
    ``N=6, k=2 -> 5`` paths from 15 splits; ``N=10, k=2 -> 9`` paths from 45.
    """
    if n_groups < 2:
        raise ValueError("n_groups must be >= 2")
    if not 1 <= k < n_groups:
        raise ValueError("k must satisfy 1 <= k < n_groups")
    total = math.comb(n_groups, k) * k
    if total % n_groups != 0:
        raise AssertionError("C(N,k)*k must divide N")
    return total // n_groups


def _to_time_array(values) -> np.ndarray:
    """Normalise timestamps to naive UTC ``datetime64[ns]``; leave numerics alone.

    Fiboki stores tz-aware UTC timestamps, and ``DatetimeIndex.to_numpy()`` on a
    tz-aware index returns an OBJECT array of Timestamps, which silently defeats
    every vectorised comparison below.  Converting once, here, is the fix.
    """
    if isinstance(values, pd.Series):
        values = values.to_numpy() if values.dt.tz is None else values.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    if isinstance(values, pd.DatetimeIndex):
        values = (values.tz_convert("UTC").tz_localize(None) if values.tz is not None else values).to_numpy()
    arr = np.asarray(values)
    if arr.dtype == object:
        try:
            idx = pd.DatetimeIndex(arr)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError("could not interpret label span values as times") from exc
        arr = (idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx).to_numpy()
    if np.issubdtype(arr.dtype, np.datetime64):
        return arr.astype("datetime64[ns]")
    return arr.astype(float)


@dataclass(frozen=True, slots=True)
class LabelSpans:
    """When each sample's label STARTS and when it STOPS being determined.

    For a trade, ``starts`` is entry time and ``ends`` is exit time.  For a
    horizon-labelled bar, ``ends`` is the bar at the end of the horizon.  Samples
    must be sorted by ``starts``; that ordering defines the fold geometry.

    Timestamps are normalised to naive UTC ``datetime64[ns]`` on construction.
    """

    starts: np.ndarray
    ends: np.ndarray

    def __init__(self, starts, ends) -> None:
        object.__setattr__(self, "starts", _to_time_array(starts))
        object.__setattr__(self, "ends", _to_time_array(ends))
        if self.starts.shape != self.ends.shape or self.starts.ndim != 1:
            raise ValueError("starts and ends must be 1-D arrays of equal length")
        if self.starts.size == 0:
            raise ValueError("label spans are empty")
        if self.starts.dtype != self.ends.dtype:
            raise ValueError("starts and ends must share a dtype (both times or both numeric)")
        if np.any(self.ends < self.starts):
            raise ValueError("a label cannot end before it starts")
        zero = np.timedelta64(0, "ns") if self.is_datetime else 0.0
        if self.starts.size > 1 and np.any(np.diff(self.starts) < zero):
            raise ValueError("samples must be sorted by start time")

    @property
    def is_datetime(self) -> bool:
        return bool(np.issubdtype(self.starts.dtype, np.datetime64))

    def __len__(self) -> int:
        return int(self.starts.size)

    @property
    def durations(self) -> np.ndarray:
        return self.ends - self.starts

    @staticmethod
    def from_series(spans: pd.Series) -> LabelSpans:
        """From the AFML convention: a Series indexed by start time, valued by end time."""
        s = spans.sort_index()
        return LabelSpans(starts=s.index, ends=s.to_numpy())

    @staticmethod
    def from_trades(trades: Sequence) -> LabelSpans:
        """From ``fiboki.core.contracts.Trade`` objects (entry_time -> exit_time)."""
        if len(trades) == 0:
            raise ValueError("no trades")
        order = sorted(range(len(trades)), key=lambda i: trades[i].entry_time)
        return LabelSpans(
            starts=pd.DatetimeIndex([trades[i].entry_time for i in order]),
            ends=pd.DatetimeIndex([trades[i].exit_time for i in order]),
        )

    def embargo_delta(self, percentile: float):
        """Embargo length taken from a percentile of the label durations.

        Sizing the embargo from how long labels actually last is more defensible
        than a fixed percentage of rows, which silently changes meaning whenever
        the sample length or bar size changes.
        """
        if not 0.0 <= percentile <= 100.0:
            raise ValueError("percentile must be in [0, 100]")
        d = self.durations
        if self.is_datetime:
            secs = d.astype("timedelta64[s]").astype(float)
            return np.timedelta64(int(np.percentile(secs, percentile)), "s").astype("timedelta64[ns]")
        return float(np.percentile(d, percentile))


@dataclass(frozen=True, slots=True)
class CVSplit:
    """One train/test split, carrying what it had to throw away and why."""

    train: np.ndarray = field(repr=False)
    test: np.ndarray = field(repr=False)
    test_groups: tuple[int, ...] = ()
    n_purged: int = 0
    n_embargoed: int = 0

    def __iter__(self) -> Iterator[np.ndarray]:
        """So ``for train, test in cv.split():`` works."""
        yield self.train
        yield self.test

    @property
    def n_train(self) -> int:
        return int(self.train.size)

    @property
    def n_test(self) -> int:
        return int(self.test.size)


def _resolve_embargo(spans: LabelSpans, embargo_pct: float, embargo_percentile: float | None):
    """Return an embargo expressed as a TIME delta, or None for positional embargo."""
    if embargo_percentile is not None:
        return spans.embargo_delta(embargo_percentile)
    return None


def _purge_and_embargo(
    spans: LabelSpans,
    test_idx: np.ndarray,
    embargo_pct: float,
    embargo_delta,
) -> tuple[np.ndarray, int, int]:
    """Training indices after purging overlaps and embargoing the aftermath."""
    n = len(spans)
    keep = np.ones(n, dtype=bool)
    keep[test_idx] = False
    before_purge = keep.sum()

    # Purge: drop any training sample whose label span overlaps ANY contiguous
    # run of the test set. Runs are handled separately so a combinatorial test
    # set made of scattered groups does not purge the gaps between them.
    runs = _contiguous_runs(test_idx)
    for lo, hi in runs:
        t0 = spans.starts[lo]
        t1 = spans.ends[lo : hi + 1].max()
        overlaps = (spans.starts <= t1) & (spans.ends >= t0)
        keep &= ~overlaps
    keep[test_idx] = False
    after_purge = keep.sum()
    n_purged = int(before_purge - after_purge)

    # Embargo: forward-looking only. Serial correlation means the observations
    # immediately AFTER a test fold still carry it; the ones before are already
    # handled by the purge.
    embargoed = np.zeros(n, dtype=bool)
    for lo, hi in runs:
        t1 = spans.ends[lo : hi + 1].max()
        if embargo_delta is not None:
            embargoed |= (spans.starts > t1) & (spans.starts <= t1 + embargo_delta)
        elif embargo_pct > 0.0:
            width = int(math.ceil(n * embargo_pct))
            stop = min(n, hi + 1 + width)
            embargoed[hi + 1 : stop] = True
    n_embargoed = int((keep & embargoed).sum())
    keep &= ~embargoed
    return np.flatnonzero(keep), n_purged, n_embargoed


def _contiguous_runs(idx: np.ndarray) -> list[tuple[int, int]]:
    """Split a sorted index array into ``(first, last)`` contiguous runs."""
    if idx.size == 0:
        return []
    s = np.sort(idx)
    breaks = np.flatnonzero(np.diff(s) != 1)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [s.size - 1]])
    return [(int(s[a]), int(s[b])) for a, b in zip(starts, ends, strict=True)]


class PurgedKFold:
    """K-fold with contiguous test folds, purging and a forward embargo.

    Parameters
    ----------
    n_splits
        Number of folds.
    spans
        :class:`LabelSpans` for every sample, sorted by start.
    embargo_pct
        Embargo as a fraction of the total sample count (AFML's default form).
    embargo_percentile
        If given, the embargo is instead a TIME delta taken from this percentile
        of the label durations, which keeps its meaning when bar size changes.
        Takes precedence over ``embargo_pct``.
    """

    def __init__(
        self,
        n_splits: int = 5,
        *,
        spans: LabelSpans,
        embargo_pct: float = 0.0,
        embargo_percentile: float | None = None,
    ) -> None:
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if len(spans) < n_splits:
            raise ValueError("fewer samples than folds")
        if embargo_pct < 0.0:
            raise ValueError("embargo_pct must be >= 0")
        self.n_splits = int(n_splits)
        self.spans = spans
        self.embargo_pct = float(embargo_pct)
        self.embargo_percentile = embargo_percentile
        self._embargo_delta = _resolve_embargo(spans, embargo_pct, embargo_percentile)

    def get_n_splits(self) -> int:
        return self.n_splits

    def split(self) -> Iterator[CVSplit]:
        n = len(self.spans)
        folds = np.array_split(np.arange(n), self.n_splits)
        for fold in folds:
            train, purged, embargoed = _purge_and_embargo(
                self.spans, fold, self.embargo_pct, self._embargo_delta
            )
            yield CVSplit(
                train=train,
                test=np.asarray(fold),
                test_groups=(),
                n_purged=purged,
                n_embargoed=embargoed,
            )


class CombinatorialPurgedCV:
    """Combinatorial purged cross-validation (AFML ch. 12).

    Splits the sample into ``n_groups`` contiguous groups and tests on every
    combination of ``k`` of them, purging and embargoing around each test group.
    That produces ``C(N,k)`` splits which reassemble into
    ``n_backtest_paths(N, k)`` complete, non-overlapping backtest paths.

    Use the path structure: a strategy that looks good on the mean path but fails
    on a third of the individual paths is not a strategy, it is a bet on which
    path you happen to live in.
    """

    def __init__(
        self,
        n_groups: int = 6,
        k: int = 2,
        *,
        spans: LabelSpans,
        embargo_pct: float = 0.0,
        embargo_percentile: float | None = None,
    ) -> None:
        if len(spans) < n_groups:
            raise ValueError("fewer samples than groups")
        self.n_paths = n_backtest_paths(n_groups, k)  # validates n_groups / k
        self.n_groups = int(n_groups)
        self.k = int(k)
        self.spans = spans
        self.embargo_pct = float(embargo_pct)
        self.embargo_percentile = embargo_percentile
        self._embargo_delta = _resolve_embargo(spans, embargo_pct, embargo_percentile)
        self._groups = np.array_split(np.arange(len(spans)), n_groups)
        self._combos = list(combinations(range(n_groups), k))

    def get_n_splits(self) -> int:
        return len(self._combos)

    @property
    def n_splits(self) -> int:
        return len(self._combos)

    def split(self) -> Iterator[CVSplit]:
        for combo in self._combos:
            test_idx = np.concatenate([self._groups[g] for g in combo])
            train, purged, embargoed = _purge_and_embargo(
                self.spans, test_idx, self.embargo_pct, self._embargo_delta
            )
            yield CVSplit(
                train=train,
                test=np.sort(test_idx),
                test_groups=tuple(combo),
                n_purged=purged,
                n_embargoed=embargoed,
            )

    def path_map(self) -> list[list[tuple[int, int]]]:
        """Assign splits to backtest paths.

        Returns ``n_paths`` lists; entry ``p`` holds ``(split_index, group)`` pairs
        covering every group exactly once, so concatenating those test segments
        gives one complete out-of-sample path over the whole sample.
        """
        # For each group, the splits that test it. Every group is tested by
        # exactly C(N-1, k-1) = n_paths splits, so the p-th occurrence of each
        # group forms the p-th path.
        per_group: list[list[int]] = [[] for _ in range(self.n_groups)]
        for s, combo in enumerate(self._combos):
            for g in combo:
                per_group[g].append(s)
        paths: list[list[tuple[int, int]]] = []
        for p in range(self.n_paths):
            paths.append([(per_group[g][p], g) for g in range(self.n_groups)])
        return paths

    def group_indices(self, group: int) -> np.ndarray:
        return np.asarray(self._groups[group])
