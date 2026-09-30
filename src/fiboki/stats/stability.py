"""Parameter stability: is this a plateau or a spike?

Taking the argmax of a parameter search is how V1 chose parameters.  The argmax of
a noisy surface is, by construction, the point where the noise was most
favourable, and it is usually surrounded by cells that perform far worse - which
is exactly what live trading will deliver, because the parameter that is optimal
in-sample is not the parameter that will be optimal next quarter.

The alternative implemented here: score each grid point by the behaviour of its
NEIGHBOURHOOD.  A broad region where every nearby setting also works is a real
effect that happens to be parameterised; a lone spike is a fitted artefact.

The neighbourhood is a Chebyshev ball in GRID-INDEX space, not in parameter-value
space, so it is agnostic to spacing (linear, log, or irregular) but it does assume
each parameter's values are ordered meaningfully.  A categorical parameter must
not be passed as an axis.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product

import numpy as np
import pandas as pd

__all__ = [
    "PLATEAU_RATIO_DEFINITION",
    "ParameterPoint",
    "StabilityReport",
    "analyse_parameter_stability",
    "marginal_sensitivity",
    "neighbourhood_median_ratio",
    "plateau_ratio",
    "sensitivity_surface",
]

#: Recorded in reports next to the number, because the number's meaning changed.
PLATEAU_RATIO_DEFINITION = "excluding_point_additive_floor_v2"


def plateau_ratio(score: float, neighbour_mean: float) -> float:
    """Point-to-plateau ratio, scale-free: ``(s + c) / (m + c)`` with ``c = |s|``.

    ``s`` is the selected point's score and ``m`` the mean of its neighbours
    EXCLUDING the point itself. Two defects of the old ``s / mean(including
    s)`` are fixed:

    * **The point no longer dilutes its own test.** With ``k`` neighbours the old
      denominator was ``(s + k m) / (k + 1)``; a spike pulled its own
      neighbourhood mean up and so looked less like a spike.
    * **Scale.** A score of 0.12 against a neighbourhood of 0.09 gave 1.33, a
      failure, on noise, and as the mean approached zero the ratio exploded.
      The additive floor ``c = |s|`` puts the point's own magnitude on both
      sides, so the ratio is bounded in ``[0, 2]`` whenever ``m >= 0`` and
      depends only on ``m / s``.

    For ``s > 0`` the ratio is ``2 / (1 + m/s)``, so the gate's ``<= 1.25`` is
    exactly ``m >= 0.6 s``: the neighbours must keep 60% of the point's score.
    That is the scale-free condition proposed in the audit (section 3.1), with
    the mean where the audit wrote the median; the threshold did not move.

    Returns ``nan`` when ``s <= 0`` or ``m`` is not finite (nothing to test: a
    non-positive selected score has already failed rung 1), and ``inf`` when
    ``m + c <= 0`` (the neighbours lose at least as much as the point makes:
    a spike on a losing surface, which must fail rather than go unevaluated).
    """
    s = float(score)
    m = float(neighbour_mean)
    if not np.isfinite(s) or not np.isfinite(m) or s <= 0.0:
        return float("nan")
    c = abs(s)
    denominator = m + c
    if denominator <= 0.0:
        return float("inf")
    return (s + c) / denominator


def neighbourhood_median_ratio(score: float, neighbour_median: float) -> float:
    """Median neighbour score over the point's own score: ``median(neighbours) / s``.

    The audit's (section 3.1) scale-free plateau condition, measured as its own
    number so a candidate gate can read it (``>= 0.6``) beside, not instead of,
    :func:`plateau_ratio`. The MEDIAN of the neighbourhood EXCLUDING the point,
    so neither the point nor one extreme neighbour moves it. Dimensionless:
    multiplying every score by ``k > 0`` leaves it unchanged.

    Returns ``nan`` when ``s <= 0`` (a ratio to a non-positive score has no
    plateau reading; the gate is then NOT_EVALUATED, which blocks) or when the
    median is not finite (no neighbours).
    """
    s = float(score)
    m = float(neighbour_median)
    if not np.isfinite(s) or not np.isfinite(m) or s <= 0.0:
        return float("nan")
    return m / s


@dataclass(frozen=True, slots=True)
class ParameterPoint:
    """One grid point, with its own score and its neighbourhood's."""

    params: dict[str, float]
    score: float
    plateau_mean: float
    """Mean score over the neighbourhood INCLUDING this point."""
    plateau_std: float
    plateau_min: float
    plateau_mean_excluding: float
    """Mean over the neighbours only - what you get if you miss this cell."""
    point_plateau_ratio: float
    """``(score + c) / (plateau_mean_excluding + c)`` with the additive floor
    ``c = |score|``. See :func:`plateau_ratio`. 1.0 on a flat surface; above 1
    the point stands out from its neighbours; at the gate's 1.25 the neighbours
    average 60% of the point. ``inf`` when the neighbours are so negative that
    the denominator is not positive (a spike on a losing surface); ``nan`` when
    the point's own score is not positive or it has no neighbours."""
    point_plateau_ratio_inclusive: float
    """The pre-``engine_v3_realism`` definition, ``score / plateau_mean`` with the
    point INSIDE its own neighbourhood mean. Kept for comparison only: it is
    scale-dependent (it explodes as the mean approaches zero) and the point
    dilutes its own test."""
    plateau_quality: float
    """``plateau_mean - penalty * plateau_std``: the level you can expect to keep
    if the parameter drifts one grid step in any direction.  Rank on this."""
    n_neighbours: int
    coverage: float
    """Populated cells over the FULL ``(2r+1)^d`` neighbourhood, so a grid edge
    scores below 1.0 even when every cell it has is populated.  A low-coverage
    point's plateau statistics rest on fewer neighbours and deserve less trust."""
    is_isolated_peak: bool
    plateau_median_excluding: float = float("nan")
    """Median over the neighbours only (the point excluded); ``nan`` without
    neighbours. Input to :func:`neighbourhood_median_ratio`."""
    plateau_min_excluding: float = float("nan")
    """Minimum over the neighbours only (the point excluded); ``nan`` without
    neighbours. ``plateau_min`` includes the point and is kept as it was."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        p = ", ".join(f"{k}={v:g}" for k, v in self.params.items())
        return (
            f"ParameterPoint({p} | score={self.score:.4g} "
            f"plateau={self.plateau_mean:.4g} quality={self.plateau_quality:.4g}"
            f"{' ISOLATED' if self.is_isolated_peak else ''})"
        )


@dataclass(frozen=True, slots=True)
class StabilityReport:
    points: tuple[ParameterPoint, ...] = field(repr=False)
    axes: dict[str, np.ndarray] = field(repr=False, default_factory=dict)
    cube: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    """Scores arranged on the grid; ``nan`` where the grid was not evaluated."""
    radius: int = 1
    penalty: float = 1.0

    @property
    def best_by_score(self) -> ParameterPoint:
        """The naive argmax - included so the two choices can be compared directly."""
        return max(self.points, key=lambda p: (p.score, -p.plateau_std))

    @property
    def best_by_plateau(self) -> ParameterPoint:
        """The point to actually trade."""
        return max(self.points, key=lambda p: (p.plateau_quality, p.score))

    def ranked_by_plateau(self) -> tuple[ParameterPoint, ...]:
        return tuple(sorted(self.points, key=lambda p: -p.plateau_quality))

    @property
    def isolated_peaks(self) -> tuple[ParameterPoint, ...]:
        return tuple(p for p in self.points if p.is_isolated_peak)

    def as_frame(self) -> pd.DataFrame:
        rows = []
        for p in self.points:
            row = dict(p.params)
            row.update(
                score=p.score,
                plateau_mean=p.plateau_mean,
                plateau_std=p.plateau_std,
                plateau_min=p.plateau_min,
                point_plateau_ratio=p.point_plateau_ratio,
                plateau_quality=p.plateau_quality,
                coverage=p.coverage,
                is_isolated_peak=p.is_isolated_peak,
            )
            rows.append(row)
        return pd.DataFrame(rows)


def _grid_frame(
    grid: Sequence[Mapping[str, float]] | pd.DataFrame, scores: Sequence[float] | np.ndarray
) -> pd.DataFrame:
    frame = pd.DataFrame(list(grid)) if not isinstance(grid, pd.DataFrame) else grid.copy()
    if frame.empty:
        raise ValueError("parameter grid is empty")
    score_arr = np.asarray(scores, dtype=float).ravel()
    if score_arr.size != len(frame):
        raise ValueError(
            f"scores length {score_arr.size} does not match grid length {len(frame)}"
        )
    if frame.columns.duplicated().any():
        raise ValueError("duplicate parameter names in grid")
    frame = frame.reset_index(drop=True)
    frame["__score__"] = score_arr
    return frame


def analyse_parameter_stability(
    grid: Sequence[Mapping[str, float]] | pd.DataFrame,
    scores: Sequence[float] | np.ndarray,
    *,
    radius: int = 1,
    penalty: float = 1.0,
    isolation_ratio: float = 0.5,
) -> StabilityReport:
    """Score every grid point by its neighbourhood as well as by itself.

    Parameters
    ----------
    grid
        One mapping per evaluated point (or a DataFrame with one column per
        parameter).  The grid need not be complete; missing cells are ``nan`` and
        are skipped by the neighbourhood statistics, with ``coverage`` recording
        how much of the neighbourhood was actually there.
    scores
        One score per grid point.  HIGHER MUST BE BETTER - pass ``-drawdown``, not
        drawdown, or the ranking inverts silently.
    radius
        Neighbourhood radius in grid steps (Chebyshev).  ``radius=1`` is the
        immediate ring; use 2 for a fine grid where one step is a trivial change.
    penalty
        Weight on the neighbourhood standard deviation in ``plateau_quality``.
        0 ranks on the neighbourhood mean alone; 1 (default) demands the region be
        both good and even.
    isolation_ratio
        A point is flagged as an isolated peak when it is the best in its
        neighbourhood and its neighbours average less than this fraction of it.
    """
    if radius < 1:
        raise ValueError("radius must be >= 1")
    frame = _grid_frame(grid, scores)
    names = [c for c in frame.columns if c != "__score__"]
    if not names:
        raise ValueError("grid has no parameter columns")

    axes = {n: np.sort(frame[n].unique()) for n in names}
    shape = tuple(len(axes[n]) for n in names)
    cube = np.full(shape, np.nan)
    coords = np.empty((len(frame), len(names)), dtype=int)
    for j, n in enumerate(names):
        lookup = {v: i for i, v in enumerate(axes[n])}
        coords[:, j] = frame[n].map(lookup).to_numpy()
    duplicated = pd.DataFrame(coords).duplicated().any()
    if duplicated:
        raise ValueError("grid contains duplicate parameter combinations")
    cube[tuple(coords.T)] = frame["__score__"].to_numpy()

    full_neighbourhood = (2 * radius + 1) ** len(names)
    points: list[ParameterPoint] = []
    for row in range(len(frame)):
        idx = coords[row]
        slices = tuple(
            slice(max(0, idx[j] - radius), min(shape[j], idx[j] + radius + 1))
            for j in range(len(names))
        )
        block = cube[slices]
        finite = block[np.isfinite(block)]
        local_pos = tuple(idx[j] - slices[j].start for j in range(len(names)))
        score = float(cube[tuple(idx)])

        mask = np.ones(block.shape, dtype=bool)
        mask[local_pos] = False
        neighbours = block[mask & np.isfinite(block)]

        plateau_mean = float(np.mean(finite))
        plateau_std = float(np.std(finite)) if finite.size > 1 else 0.0
        plateau_min = float(np.min(finite))
        excl_mean = float(np.mean(neighbours)) if neighbours.size else float("nan")
        excl_median = float(np.median(neighbours)) if neighbours.size else float("nan")
        excl_min = float(np.min(neighbours)) if neighbours.size else float("nan")
        inclusive = score / plateau_mean if plateau_mean > 0.0 else float("nan")
        ratio = plateau_ratio(score, excl_mean)
        quality = plateau_mean - penalty * plateau_std
        # STRICTLY best, so the edge of a genuine plateau (which ties with its
        # neighbours) is never mistaken for a spike.
        strictly_best = bool(neighbours.size and score > neighbours.max())
        isolated = bool(
            strictly_best and score > 0.0 and excl_mean < isolation_ratio * score
        )
        points.append(
            ParameterPoint(
                params={n: float(frame.iloc[row][n]) for n in names},
                score=score,
                plateau_mean=plateau_mean,
                plateau_std=plateau_std,
                plateau_min=plateau_min,
                plateau_mean_excluding=excl_mean,
                point_plateau_ratio=ratio,
                point_plateau_ratio_inclusive=inclusive,
                plateau_quality=quality,
                n_neighbours=int(neighbours.size),
                coverage=float(finite.size / full_neighbourhood),
                is_isolated_peak=isolated,
                plateau_median_excluding=excl_median,
                plateau_min_excluding=excl_min,
            )
        )
    return StabilityReport(
        points=tuple(points), axes=axes, cube=cube, radius=int(radius), penalty=float(penalty)
    )


def sensitivity_surface(
    grid: Sequence[Mapping[str, float]] | pd.DataFrame,
    scores: Sequence[float] | np.ndarray,
    x: str,
    y: str,
    *,
    agg: str = "mean",
) -> pd.DataFrame:
    """Two-parameter surface, averaging over every other parameter.

    Rows are ``y`` values, columns are ``x`` values - the orientation a heatmap
    wants.  Marginalising over the other parameters is deliberate: a surface
    sliced at the argmax of everything else shows the ridge you fitted, not the
    ridge that exists.
    """
    frame = _grid_frame(grid, scores)
    for col in (x, y):
        if col not in frame.columns:
            raise ValueError(f"unknown parameter {col!r}")
    if x == y:
        raise ValueError("x and y must be different parameters")
    return frame.pivot_table(index=y, columns=x, values="__score__", aggfunc=agg).sort_index()


def marginal_sensitivity(
    grid: Sequence[Mapping[str, float]] | pd.DataFrame,
    scores: Sequence[float] | np.ndarray,
) -> pd.DataFrame:
    """Per-parameter marginal effect and how much the result hinges on it.

    Returns one row per parameter with the mean score at its best and worst
    levels, the spread between them, and the standard deviation across levels.
    A parameter with a large spread is one the result depends on - and therefore
    one that must be justified, walked forward and stress-tested, not tuned.
    """
    frame = _grid_frame(grid, scores)
    names = [c for c in frame.columns if c != "__score__"]
    rows = []
    for n in names:
        by_level = frame.groupby(n)["__score__"].mean()
        rows.append(
            {
                "parameter": n,
                "n_levels": int(by_level.size),
                "best_level": float(by_level.idxmax()),
                "best_mean": float(by_level.max()),
                "worst_level": float(by_level.idxmin()),
                "worst_mean": float(by_level.min()),
                "spread": float(by_level.max() - by_level.min()),
                "std_across_levels": float(by_level.std(ddof=0)),
            }
        )
    return pd.DataFrame(rows).set_index("parameter").sort_values("spread", ascending=False)


def full_grid(axes: Mapping[str, Sequence[float]]) -> list[dict[str, float]]:
    """Cartesian product helper: ``{'a': [1,2], 'b': [3,4]}`` -> 4 parameter dicts."""
    names = list(axes)
    return [dict(zip(names, combo, strict=True)) for combo in product(*(axes[n] for n in names))]
