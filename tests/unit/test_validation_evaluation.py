"""Windows, declared parameter domains, and what an evaluation is allowed to claim."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    ParameterGrid,
    WindowEvaluation,
    canonical_params,
    params_key,
)

START = pd.Timestamp("2020-01-01", tz="UTC")
END = pd.Timestamp("2024-01-01", tz="UTC")


class TestDateWindow:
    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        window = DateWindow("w", "2020-01-01", "2021-01-01")
        assert window.start.tzinfo is not None
        assert str(window.start.tz) == "UTC"

    def test_an_empty_or_reversed_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be after"):
            DateWindow("w", END, START)
        with pytest.raises(ValueError, match="must be after"):
            DateWindow("w", START, START)

    def test_slices_tile_the_window_exactly(self) -> None:
        slices = DateWindow("w", START, END).slices(6)
        assert len(slices) == 6
        assert slices[0].start == START
        assert slices[-1].end == END
        for a, b in itertools.pairwise(slices):
            assert a.end == b.start
            assert not a.overlaps(b)

    def test_windows_are_half_open_so_a_boundary_bar_belongs_to_one_side(self) -> None:
        a, b = DateWindow("w", START, END).slices(2)
        assert a.contains(a.start)
        assert not a.contains(a.end)
        assert b.contains(b.start)

    def test_split_puts_the_requested_fraction_in_the_tail(self) -> None:
        head, tail = DateWindow("w", START, END).split(
            0.2, head_name="research", tail_name="holdout"
        )
        assert tail.days / (head.days + tail.days) == pytest.approx(0.2, abs=1e-9)
        assert head.end == tail.start

    def test_it_round_trips_through_a_dict(self) -> None:
        window = DateWindow("w", START, END)
        assert DateWindow.from_dict(window.to_dict()) == window


class TestParameterGrid:
    def test_points_are_the_cartesian_product_in_a_stable_order(self) -> None:
        grid = ParameterGrid.from_axes({"b": (1, 2), "a": (10, 20)})
        assert grid.names == ("a", "b")  # axes sorted by name
        assert grid.points() == [
            {"a": 10, "b": 1},
            {"a": 10, "b": 2},
            {"a": 20, "b": 1},
            {"a": 20, "b": 2},
        ]
        assert ParameterGrid.from_axes({"b": (1, 2), "a": (10, 20)}).points() == grid.points()

    def test_an_empty_grid_still_yields_one_point(self) -> None:
        assert ParameterGrid().points() == [{}]

    def test_an_empty_axis_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty domain"):
            ParameterGrid((("a", ()),))

    def test_capping_keeps_the_endpoints_and_records_the_original_size(self) -> None:
        grid = ParameterGrid.from_axes({"a": tuple(range(10)), "b": tuple(range(10))})
        capped = grid.capped(30)
        assert capped.full_size() <= 30
        assert capped.truncated_from == 100
        for _, values in capped.axes:
            assert values[0] == 0 and values[-1] == 9

    def test_capping_is_deterministic(self) -> None:
        grid = ParameterGrid.from_axes({"a": tuple(range(12)), "b": tuple(range(7))})
        assert grid.capped(20).axes == grid.capped(20).axes

    def test_numeric_names_excludes_booleans_and_strings(self) -> None:
        grid = ParameterGrid.from_axes(
            {"n": (1, 2), "flag": (True, False), "mode": ("a", "b")}
        )
        assert grid.numeric_names == ("n",)


class TestGridFromADeclaredDocument:
    """The sweep domain comes from the STRATEGY, not from a sweeping script."""

    def test_it_reads_the_documents_declared_domains(self) -> None:
        from tests.validation_fixtures import seed_document

        doc = seed_document()
        grid = ParameterGrid.from_document(doc, max_values_per_axis=3, max_points=10_000)
        assert set(grid.names) == set(doc.parameters)
        rsi = dict(grid.axes)["rsi_period"]
        spec = doc.parameters["rsi_period"]
        assert min(rsi) == spec.min_value
        assert max(rsi) == spec.max_value
        assert len(rsi) <= 3

    def test_the_declared_default_survives_thinning(self) -> None:
        from tests.validation_fixtures import seed_document

        doc = seed_document()
        grid = ParameterGrid.from_document(doc, max_values_per_axis=3, max_points=10_000)
        axes = dict(grid.axes)
        assert doc.parameters["rsi_period"].default in axes["rsi_period"]

    def test_a_candidate_built_from_a_document_carries_its_content_hash(self) -> None:
        from tests.validation_fixtures import seed_document

        doc = seed_document()
        candidate = Candidate.from_document(doc, max_points=128)
        assert candidate.content_hash == doc.content_hash()
        assert candidate.strategy_id == doc.strategy_id
        assert candidate.grid.full_size() <= 128
        assert set(candidate.default_params) == set(doc.parameters)

    def test_capping_never_drops_a_declared_parameter(self) -> None:
        """Two values per axis is the floor; a 6-parameter document cannot be
        swept in fewer than 64 points without silently ignoring a parameter, and
        silently ignoring one would make the report describe a sweep that never
        happened."""
        from tests.validation_fixtures import seed_document

        doc = seed_document()
        grid = ParameterGrid.from_document(doc, max_points=8)
        assert set(grid.names) == set(doc.parameters)
        assert grid.full_size() == 2 ** len(doc.parameters)
        assert grid.truncated_from > grid.full_size()

    def test_a_candidate_without_a_content_hash_is_refused(self) -> None:
        with pytest.raises(ValueError, match="content_hash is mandatory"):
            Candidate(strategy_id="x", content_hash="")


class TestWindowEvaluation:
    @staticmethod
    def _eval(returns, **kwargs) -> WindowEvaluation:
        arr = np.asarray(returns, dtype=float)
        return WindowEvaluation(
            window=DateWindow("w", START, END),
            params={"a": 1},
            n_trades=kwargs.pop("n_trades", arr.size),
            net_profit=kwargs.pop("net_profit", float(arr.sum())),
            returns=arr,
            **kwargs,
        )

    def test_expectancy_is_profit_per_trade(self) -> None:
        assert self._eval([1.0, 2.0, 3.0]).expectancy == pytest.approx(2.0)

    def test_zero_trades_has_no_expectancy_rather_than_a_divide_by_zero(self) -> None:
        assert self._eval([], n_trades=0, net_profit=0.0).expectancy == 0.0

    def test_a_flat_return_series_has_a_sharpe_of_zero_not_an_infinity(self) -> None:
        assert self._eval([1.0] * 20).sharpe == 0.0

    def test_degeneracies_are_named(self) -> None:
        assert "no_trades" in self._eval([], n_trades=0, net_profit=0.0).degeneracies()
        assert "zero_variance_returns" in self._eval([2.0] * 10).degeneracies()
        assert "all_zero_returns" in self._eval([0.0] * 10).degeneracies()
        assert "non_finite_returns" in self._eval([1.0, np.nan]).degeneracies()

    def test_a_healthy_evaluation_has_no_degeneracies(self) -> None:
        rng = np.random.default_rng(0)
        assert self._eval(rng.standard_normal(50)).degeneracies() == ()

    def test_an_unknown_returns_basis_is_refused(self) -> None:
        with pytest.raises(ValueError, match="returns_basis"):
            self._eval([1.0, 2.0], returns_basis="vibes")

    def test_an_unknown_selection_metric_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown selection metric"):
            self._eval([1.0, 2.0]).metric("profit_obviously")


class TestParamsKey:
    def test_it_is_order_independent(self) -> None:
        assert params_key({"a": 1, "b": 2}) == params_key({"b": 2, "a": 1})

    def test_it_separates_different_values(self) -> None:
        assert params_key({"a": 1}) != params_key({"a": 2})

    def test_canonical_params_sorts_and_keeps_types(self) -> None:
        out = canonical_params({"b": 2.5, "a": 1, "c": True})
        assert list(out) == ["a", "b", "c"]
        assert out["c"] is True
