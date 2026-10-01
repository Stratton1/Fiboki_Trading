"""A trial that does not move in a block must not crash the ladder.

Found by the E-1 ``perturbed_price_paths`` run (2026-10-01): three of four shards
died in rung 3 with ``returns have zero dispersion; Sharpe ratio undefined``,
raised while RANKING the trials of a purged-CV train block in which one grid
point had not traded. The defaults had traded (rung 0 checks that); a neighbour
had not. The ranking must treat an undefined score as never-best, a path whose
selected trials never moved must count as not positive, and deflation must
refuse honestly rather than raise.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from fiboki.validation.evaluation import DateWindow
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder, _select_column
from fiboki.validation.report import RungOutcome, Verdict
from tests.validation_fixtures import (
    DATA_END,
    DATA_START,
    DATASET_VERSION,
    SyntheticEvaluator,
    candidate,
    plateau_mu,
)

CONFIG = LadderConfig(stress_samples=10, spa_bootstraps=60, cscv_splits=6)
DEAD = {"fast": 25, "slow": 60}  # the grid's far corner: never trades


def _silent_corner(params: Mapping[str, Any], _w: DateWindow) -> float:
    """Zero volatility at the dead corner: with zero drift its returns are all 0."""
    return 0.0 if (int(params["fast"]), int(params["slow"])) == (25, 60) else 1.0


def _dead_corner_mu(peak: float):
    base = plateau_mu(peak=peak)

    def _mu(params: Mapping[str, Any], window: DateWindow, times: np.ndarray) -> float:
        if (int(params["fast"]), int(params["slow"])) == (25, 60):
            return 0.0
        return base(params, window, times)

    return _mu


def _evaluator(peak: float) -> SyntheticEvaluator:
    return SyntheticEvaluator(mu=_dead_corner_mu(peak), sigma=_silent_corner)


def _run(peak: float):
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    return ValidationLadder(config=dataclasses.replace(CONFIG, min_trades=400), gate_set=GATE_SET_V2).run(
        candidate("dead_corner", "d" * 64), _evaluator(peak),
        registry=registry, dataset_version_id=DATASET_VERSION,
    )


def test_an_undefined_score_is_never_the_best_column() -> None:
    rng = np.random.default_rng(1)
    block = rng.standard_normal((50, 4)) - 5.0  # every real trial loses
    block[:, 2] = 0.0  # a trial that never traded: zero dispersion, zero profit
    assert _select_column(block, "sharpe") != 2  # not "best" for being undefined
    # net_profit ranking is unchanged: 0 beats a loss, which is a different question
    assert _select_column(block, "net_profit") == 2


def test_the_ladder_runs_through_a_dead_grid_point() -> None:
    """With a real edge elsewhere the dead corner is just a worse neighbour."""
    report = _run(peak=0.30)
    assert all(r.outcome is not RungOutcome.ERROR for r in report.rungs)
    cv = next(r for r in report.rungs if r.index == 3)
    assert cv.outcome is RungOutcome.PASS
    assert cv.metrics["n_undefined_path_sharpes"] == 0
    assert report.verdict in (Verdict.PROMOTE, Verdict.REJECT)


def test_a_dead_grid_point_cannot_crash_a_losing_candidate() -> None:
    """Every live trial loses; the dead corner is the only non-negative column by
    net profit and must not be ranked best by an undefined Sharpe either."""
    report = _run(peak=-0.20)
    assert all(r.outcome is not RungOutcome.ERROR for r in report.rungs), [
        (r.index, r.outcome, r.reason) for r in report.rungs
    ]
    assert report.verdict is Verdict.REJECT


@pytest.mark.parametrize("peak", [0.30, -0.20])
def test_measuring_mode_runs_through_it_too(peak: float) -> None:
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    from fiboki.validation.gates import GATE_SET_V2_1_CANDIDATES

    ladder = ValidationLadder(config=dataclasses.replace(CONFIG, min_trades=150),
                              gate_set=GATE_SET_V2_1_CANDIDATES["c_all"])
    reports = ladder.run_measuring(
        candidate("dead_corner", "d" * 64), _evaluator(peak),
        registry=registry, dataset_version_id=DATASET_VERSION,
        gate_sets={GATE_SET_V2.version: GATE_SET_V2},
    )
    for report in reports.values():
        assert all(r.outcome is not RungOutcome.ERROR for r in report.rungs)
