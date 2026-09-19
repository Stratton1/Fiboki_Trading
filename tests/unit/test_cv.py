"""Purged / embargoed / combinatorial cross-validation."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from stats_trades import make_trade

from fiboki.stats.cv import (
    CombinatorialPurgedCV,
    LabelSpans,
    PurgedKFold,
    n_backtest_paths,
)


def hourly_spans(n: int = 240, hold: str = "5h") -> LabelSpans:
    idx = pd.date_range("2023-01-02", periods=n, freq="h", tz="UTC")
    return LabelSpans(starts=idx, ends=idx + pd.Timedelta(hold))


class TestPathCounts:
    @pytest.mark.golden
    @pytest.mark.parametrize(
        ("n_groups", "k", "splits", "paths"),
        [(6, 2, 15, 5), (10, 2, 45, 9)],
    )
    def test_published_worked_examples(
        self, n_groups: int, k: int, splits: int, paths: int
    ) -> None:
        """AFML ch. 12: N=6,k=2 -> 15 splits and 5 paths; N=10,k=2 -> 45 and 9."""
        assert math.comb(n_groups, k) == splits
        assert n_backtest_paths(n_groups, k) == paths
        cv = CombinatorialPurgedCV(n_groups, k, spans=hourly_spans())
        assert cv.n_splits == splits
        assert cv.n_paths == paths
        assert len(list(cv.split())) == splits

    @pytest.mark.parametrize(
        ("n", "k"), [(6, 2), (10, 2), (10, 3), (12, 4), (8, 1), (20, 5), (7, 3)]
    )
    def test_equals_binomial_identity(self, n: int, k: int) -> None:
        """C(N,k)*k/N == C(N-1,k-1)."""
        assert n_backtest_paths(n, k) == math.comb(n - 1, k - 1)

    @pytest.mark.parametrize(("n", "k"), [(6, 0), (6, 6), (6, 7), (1, 1)])
    def test_rejects_degenerate_arguments(self, n: int, k: int) -> None:
        with pytest.raises(ValueError):
            n_backtest_paths(n, k)


class TestPurgedKFold:
    def test_folds_partition_the_sample(self) -> None:
        spans = hourly_spans()
        folds = [s.test for s in PurgedKFold(5, spans=spans).split()]
        assert sorted(np.concatenate(folds).tolist()) == list(range(len(spans)))

    def test_train_and_test_are_disjoint(self) -> None:
        for s in PurgedKFold(5, spans=hourly_spans()).split():
            assert not set(s.train.tolist()) & set(s.test.tolist())

    def test_no_training_label_overlaps_the_test_window(self) -> None:
        """The purge is the whole point: a training label that is still open during
        the test window has already seen the test outcome."""
        spans = hourly_spans(hold="9h")
        for s in PurgedKFold(5, spans=spans).split():
            t0 = spans.starts[s.test].min()
            t1 = spans.ends[s.test].max()
            assert not np.any((spans.starts[s.train] <= t1) & (spans.ends[s.train] >= t0))

    def test_purge_removes_samples_a_naive_kfold_would_keep(self) -> None:
        spans = hourly_spans(hold="9h")
        splits = list(PurgedKFold(5, spans=spans).split())
        assert all(s.n_purged > 0 for s in splits)
        assert all(s.n_train < len(spans) - s.n_test for s in splits)

    def test_longer_labels_purge_more(self) -> None:
        short = sum(s.n_purged for s in PurgedKFold(5, spans=hourly_spans(hold="2h")).split())
        long = sum(s.n_purged for s in PurgedKFold(5, spans=hourly_spans(hold="20h")).split())
        assert long > short

    def test_percentage_embargo_removes_extra_samples(self) -> None:
        spans = hourly_spans(hold="1h")
        plain = list(PurgedKFold(5, spans=spans).split())
        embargoed = list(PurgedKFold(5, spans=spans, embargo_pct=0.05).split())
        assert sum(s.n_embargoed for s in embargoed) > 0
        assert sum(s.n_train for s in embargoed) < sum(s.n_train for s in plain)

    def test_embargo_is_forward_only(self) -> None:
        """Samples before the fold are handled by the purge; the embargo protects
        against serial correlation leaking FORWARD out of the test window."""
        spans = hourly_spans(hold="1h")
        split = next(iter(PurgedKFold(5, spans=spans, embargo_pct=0.05).split()))
        first_fold_end = split.test.max()
        gap = split.train.min() - first_fold_end
        assert gap > 1

    def test_duration_percentile_embargo(self) -> None:
        idx = pd.date_range("2023-01-02", periods=200, freq="h", tz="UTC")
        holds = pd.to_timedelta(np.random.default_rng(0).integers(1, 12, 200), unit="h")
        spans = LabelSpans(starts=idx, ends=idx + holds)
        p50 = list(PurgedKFold(4, spans=spans, embargo_percentile=50).split())
        p99 = list(PurgedKFold(4, spans=spans, embargo_percentile=99).split())
        assert sum(s.n_train for s in p99) <= sum(s.n_train for s in p50)

    def test_split_unpacks_as_a_tuple(self) -> None:
        for train, test in PurgedKFold(3, spans=hourly_spans()).split():
            assert isinstance(train, np.ndarray) and isinstance(test, np.ndarray)

    def test_from_trades(self) -> None:
        trades = [make_trade(10.0, index=i, bars_held=6) for i in range(60)]
        spans = LabelSpans.from_trades(trades)
        assert len(spans) == 60
        assert all(s.n_purged > 0 for s in PurgedKFold(4, spans=spans).split())

    def test_from_series(self) -> None:
        idx = pd.date_range("2023-01-02", periods=50, freq="h", tz="UTC")
        spans = LabelSpans.from_series(pd.Series(idx + pd.Timedelta("3h"), index=idx))
        assert len(spans) == 50
        assert spans.is_datetime

    def test_numeric_spans_supported(self) -> None:
        spans = LabelSpans(starts=np.arange(100.0), ends=np.arange(100.0) + 4.0)
        assert not spans.is_datetime
        assert all(s.n_purged > 0 for s in PurgedKFold(4, spans=spans, embargo_pct=0.02).split())

    def test_rejects_unsorted_or_inverted_spans(self) -> None:
        with pytest.raises(ValueError, match="sorted"):
            LabelSpans(starts=np.array([3.0, 1.0, 2.0]), ends=np.array([4.0, 5.0, 6.0]))
        with pytest.raises(ValueError, match="cannot end before"):
            LabelSpans(starts=np.array([1.0, 2.0]), ends=np.array([0.0, 3.0]))

    def test_rejects_too_many_folds(self) -> None:
        with pytest.raises(ValueError):
            PurgedKFold(1, spans=hourly_spans())


class TestCombinatorialPurgedCV:
    def test_test_sets_cover_every_group_the_right_number_of_times(self) -> None:
        cv = CombinatorialPurgedCV(6, 2, spans=hourly_spans())
        seen = [g for s in cv.split() for g in s.test_groups]
        counts = np.bincount(seen, minlength=6)
        assert np.all(counts == cv.n_paths)

    def test_paths_cover_the_sample_exactly_once(self) -> None:
        """Each path must be a complete, non-overlapping out-of-sample run."""
        cv = CombinatorialPurgedCV(6, 2, spans=hourly_spans())
        splits = list(cv.split())
        for path in cv.path_map():
            covered = np.concatenate([cv.group_indices(g) for _, g in path])
            assert sorted(covered.tolist()) == list(range(len(cv.spans)))
            assert len(path) == cv.n_groups
            for split_idx, group in path:
                assert group in splits[split_idx].test_groups

    def test_path_map_has_the_right_shape(self) -> None:
        cv = CombinatorialPurgedCV(10, 2, spans=hourly_spans())
        paths = cv.path_map()
        assert len(paths) == 9
        assert all(len(p) == 10 for p in paths)

    def test_purging_applies_around_each_scattered_test_group(self) -> None:
        spans = hourly_spans(hold="9h")
        cv = CombinatorialPurgedCV(6, 2, spans=spans)
        for s in cv.split():
            for g in s.test_groups:
                idx = cv.group_indices(g)
                t0, t1 = spans.starts[idx].min(), spans.ends[idx].max()
                assert not np.any((spans.starts[s.train] <= t1) & (spans.ends[s.train] >= t0))

    def test_gap_between_scattered_groups_is_not_purged_wholesale(self) -> None:
        """Non-adjacent test groups must not purge everything between them - that
        would throw away most of the training set for no reason."""
        cv = CombinatorialPurgedCV(6, 2, spans=hourly_spans(hold="1h"))
        adjacent = next(s for s in cv.split() if s.test_groups == (0, 1))
        scattered = next(s for s in cv.split() if s.test_groups == (0, 5))
        assert scattered.n_train >= adjacent.n_train - 2

    def test_train_and_test_disjoint(self) -> None:
        for s in CombinatorialPurgedCV(6, 2, spans=hourly_spans()).split():
            assert not set(s.train.tolist()) & set(s.test.tolist())
