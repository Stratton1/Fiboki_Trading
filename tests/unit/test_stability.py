"""Parameter stability: plateaus beat peaks."""
from __future__ import annotations

import numpy as np
import pytest

from fiboki.stats.stability import (
    analyse_parameter_stability,
    full_grid,
    marginal_sensitivity,
    neighbourhood_median_ratio,
    sensitivity_surface,
)


def plateau_and_spike() -> tuple[list[dict[str, float]], list[float]]:
    """A 7x7 surface: a broad plateau at 1.0 around (3,3), a lone 1.6 at (7,7)."""
    grid = full_grid({"tenkan": [1, 2, 3, 4, 5, 6, 7], "kijun": [1, 2, 3, 4, 5, 6, 7]})
    scores = []
    for g in grid:
        a, b = g["tenkan"], g["kijun"]
        if (a, b) == (7, 7):
            scores.append(1.6)
        elif 2 <= a <= 4 and 2 <= b <= 4:
            scores.append(1.0)
        else:
            scores.append(0.0)
    return grid, scores


class TestPlateauVersusPeak:
    def test_argmax_and_plateau_choice_disagree(self) -> None:
        """The headline result: the best single cell is NOT the cell to trade."""
        grid, scores = plateau_and_spike()
        report = analyse_parameter_stability(grid, scores)
        assert report.best_by_score.params == {"tenkan": 7.0, "kijun": 7.0}
        assert report.best_by_score.score == 1.6
        assert report.best_by_plateau.params == {"tenkan": 3.0, "kijun": 3.0}
        assert report.best_by_plateau.score == 1.0

    def test_spike_is_flagged_as_isolated(self) -> None:
        grid, scores = plateau_and_spike()
        report = analyse_parameter_stability(grid, scores)
        assert [p.params for p in report.isolated_peaks] == [{"tenkan": 7.0, "kijun": 7.0}]

    def test_plateau_edges_are_not_mistaken_for_spikes(self) -> None:
        """A genuine plateau's corner ties with its neighbours, so it must not be
        flagged - a naive 'best in neighbourhood' rule gets this wrong."""
        grid, scores = plateau_and_spike()
        report = analyse_parameter_stability(grid, scores)
        flagged = {tuple(p.params.values()) for p in report.isolated_peaks}
        assert (2.0, 2.0) not in flagged
        assert (4.0, 4.0) not in flagged

    def test_point_plateau_ratio_separates_them(self) -> None:
        grid, scores = plateau_and_spike()
        report = analyse_parameter_stability(grid, scores)
        by_params = {tuple(p.params.values()): p for p in report.points}
        spike = by_params[(7.0, 7.0)]
        centre = by_params[(3.0, 3.0)]
        assert centre.point_plateau_ratio == pytest.approx(1.0)
        # Since engine_v3_realism the ratio excludes the point and carries the
        # additive floor c = |score| (stats.stability.plateau_ratio), so it is
        # bounded at 2 for a non-negative neighbourhood. A spike whose
        # neighbours score zero sits exactly at that bound, (s + s) / (0 + s):
        assert spike.point_plateau_ratio == pytest.approx(2.0)
        assert spike.point_plateau_ratio > 1.25  # fails the unchanged gate
        assert spike.plateau_mean_excluding == 0.0
        # The pre-v3 figure is still reported for comparison, and was > 3 here.
        assert spike.point_plateau_ratio_inclusive > 3.0

    def test_plateau_quality_ranking(self) -> None:
        grid, scores = plateau_and_spike()
        report = analyse_parameter_stability(grid, scores)
        top = report.ranked_by_plateau()[0]
        assert top.params == {"tenkan": 3.0, "kijun": 3.0}
        assert top.plateau_quality == pytest.approx(1.0)
        assert report.ranked_by_plateau()[-1].plateau_quality <= top.plateau_quality


class TestKnownAnswers:
    def test_flat_surface_has_ratio_one_everywhere(self) -> None:
        grid = full_grid({"a": [1, 2, 3], "b": [1, 2, 3]})
        report = analyse_parameter_stability(grid, [2.0] * 9)
        for p in report.points:
            assert p.plateau_mean == pytest.approx(2.0)
            assert p.plateau_std == pytest.approx(0.0)
            assert p.point_plateau_ratio == pytest.approx(1.0)
            assert p.plateau_quality == pytest.approx(2.0)
            assert not p.is_isolated_peak

    def test_plateau_mean_is_a_hand_computable_neighbourhood_mean(self) -> None:
        grid = full_grid({"a": [1, 2, 3], "b": [1, 2, 3]})
        scores = [float(i) for i in range(9)]  # row-major over (a, b)
        report = analyse_parameter_stability(grid, scores)
        centre = next(p for p in report.points if p.params == {"a": 2.0, "b": 2.0})
        assert centre.plateau_mean == pytest.approx(np.mean(scores))
        assert centre.n_neighbours == 8
        assert centre.coverage == pytest.approx(1.0)

    def test_edges_report_lower_coverage(self) -> None:
        grid = full_grid({"a": [1, 2, 3], "b": [1, 2, 3]})
        report = analyse_parameter_stability(grid, [1.0] * 9)
        corner = next(p for p in report.points if p.params == {"a": 1.0, "b": 1.0})
        assert corner.n_neighbours == 3
        assert corner.coverage == pytest.approx(4 / 9)

    def test_radius_two_widens_the_neighbourhood(self) -> None:
        grid, scores = plateau_and_spike()
        narrow = analyse_parameter_stability(grid, scores, radius=1)
        wide = analyse_parameter_stability(grid, scores, radius=2)
        n_narrow = next(p for p in narrow.points if p.params == {"tenkan": 3.0, "kijun": 3.0})
        n_wide = next(p for p in wide.points if p.params == {"tenkan": 3.0, "kijun": 3.0})
        assert n_wide.n_neighbours > n_narrow.n_neighbours
        assert n_wide.plateau_mean < n_narrow.plateau_mean  # reaches the zero ring

    def test_penalty_zero_ranks_on_the_mean_alone(self) -> None:
        grid, scores = plateau_and_spike()
        report = analyse_parameter_stability(grid, scores, penalty=0.0)
        for p in report.points:
            assert p.plateau_quality == pytest.approx(p.plateau_mean)

    def test_one_dimensional_grid(self) -> None:
        grid = [{"period": float(p)} for p in range(1, 11)]
        scores = [0, 0, 1, 1, 1, 1, 1, 0, 0, 3.0]
        report = analyse_parameter_stability(grid, scores)
        assert report.best_by_score.params == {"period": 10.0}
        assert report.best_by_plateau.params["period"] in (4.0, 5.0, 6.0)

    def test_incomplete_grid_is_handled_with_nan(self) -> None:
        grid = full_grid({"a": [1, 2, 3], "b": [1, 2, 3]})
        scores = [1.0] * 9
        grid.pop(4)  # drop the centre
        scores.pop(4)
        report = analyse_parameter_stability(grid, scores)
        assert np.isnan(report.cube).sum() == 1
        assert all(p.coverage < 1.0 for p in report.points)


class TestSensitivity:
    def test_surface_orientation_and_values(self) -> None:
        grid, scores = plateau_and_spike()
        surface = sensitivity_surface(grid, scores, x="tenkan", y="kijun")
        assert surface.shape == (7, 7)
        assert surface.loc[3.0, 3.0] == pytest.approx(1.0)
        assert surface.loc[7.0, 7.0] == pytest.approx(1.6)

    def test_surface_marginalises_over_other_parameters(self) -> None:
        grid = full_grid({"a": [1, 2], "b": [1, 2], "c": [1, 2]})
        scores = [float(i) for i in range(8)]
        surface = sensitivity_surface(grid, scores, x="a", y="b")
        assert surface.loc[1.0, 1.0] == pytest.approx(np.mean([0.0, 1.0]))

    def test_marginal_sensitivity_orders_by_spread(self) -> None:
        grid = full_grid({"important": [1, 2, 3], "irrelevant": [1, 2, 3]})
        scores = [g["important"] * 10.0 for g in grid]
        table = marginal_sensitivity(grid, scores)
        assert table.index[0] == "important"
        assert table.loc["irrelevant", "spread"] == pytest.approx(0.0)
        assert table.loc["important", "spread"] == pytest.approx(20.0)
        assert table.loc["important", "best_level"] == 3.0

    def test_surface_rejects_bad_columns(self) -> None:
        grid, scores = plateau_and_spike()
        with pytest.raises(ValueError, match="unknown parameter"):
            sensitivity_surface(grid, scores, x="tenkan", y="nope")
        with pytest.raises(ValueError, match="must be different"):
            sensitivity_surface(grid, scores, x="tenkan", y="tenkan")


class TestValidation:
    def test_length_mismatch(self) -> None:
        with pytest.raises(ValueError, match="does not match"):
            analyse_parameter_stability([{"a": 1.0}, {"a": 2.0}], [1.0])

    def test_duplicate_points(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            analyse_parameter_stability([{"a": 1.0}, {"a": 1.0}], [1.0, 2.0])

    def test_empty_grid(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            analyse_parameter_stability([], [])

    def test_radius_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="radius"):
            analyse_parameter_stability([{"a": 1.0}], [1.0], radius=0)

    def test_frame_export(self) -> None:
        grid, scores = plateau_and_spike()
        frame = analyse_parameter_stability(grid, scores).as_frame()
        assert len(frame) == 49
        assert {"tenkan", "kijun", "score", "plateau_quality", "is_isolated_peak"} <= set(
            frame.columns
        )


class TestNeighbourhoodMedianAndMinimum:
    """The E-2 candidate plateau inputs: median and minimum of the neighbours, point excluded."""

    @staticmethod
    def _line(scores: list[float]):
        grid = [{"p": float(i)} for i in range(len(scores))]
        return analyse_parameter_stability(grid, scores, radius=1)

    def test_hand_computed_neighbourhood(self) -> None:
        """Scores 1, 2, 10, 3, 4 on a line; the point 10 has neighbours 2 and 3:
        median 2.5, minimum 2, so the median ratio is 2.5 / 10 = 0.25."""
        point = self._line([1.0, 2.0, 10.0, 3.0, 4.0]).points[2]
        assert point.plateau_median_excluding == pytest.approx(2.5)
        assert point.plateau_min_excluding == pytest.approx(2.0)
        assert point.plateau_min == pytest.approx(2.0)  # inclusive, unchanged
        assert neighbourhood_median_ratio(point.score, point.plateau_median_excluding) == (
            pytest.approx(0.25)
        )

    def test_the_point_is_excluded_from_the_minimum(self) -> None:
        """A dip AT the point: inclusive minimum is the point, exclusive is its neighbour."""
        point = self._line([5.0, 1.0, 6.0]).points[1]
        assert point.plateau_min == pytest.approx(1.0)
        assert point.plateau_min_excluding == pytest.approx(5.0)

    def test_scale_free(self) -> None:
        base = self._line([1.0, 2.0, 10.0, 3.0, 4.0]).points[2]
        scaled = self._line([7.0, 14.0, 70.0, 21.0, 28.0]).points[2]
        assert neighbourhood_median_ratio(
            scaled.score, scaled.plateau_median_excluding
        ) == pytest.approx(neighbourhood_median_ratio(base.score, base.plateau_median_excluding))

    @pytest.mark.parametrize(
        ("score", "median"), [(0.0, 1.0), (-1.0, 1.0), (1.0, float("nan")), (float("nan"), 1.0)]
    )
    def test_undefined_is_nan(self, score: float, median: float) -> None:
        assert np.isnan(neighbourhood_median_ratio(score, median))

    def test_a_lone_point_has_no_neighbourhood(self) -> None:
        point = analyse_parameter_stability([{"p": 1.0}], [3.0]).points[0]
        assert np.isnan(point.plateau_median_excluding)
        assert np.isnan(point.plateau_min_excluding)
