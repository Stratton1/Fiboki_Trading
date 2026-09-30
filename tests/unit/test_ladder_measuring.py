"""``ValidationLadder.run_measuring`` says what ``run`` says, for every gate set.

The calibration study judges one measurement under the audited set and under
every E-2 candidate. That is only evidence about the ladder as shipped if the
measurement-mode verdict is the fail-fast verdict, candidate by candidate, so
these tests run both on the same synthetic candidates, across the outcomes a
ladder can reach (rejected at rung 0, at a middle rung, at deflation, promoted),
and pin verdict and binding constraint equal.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from fiboki.validation.gates import GATE_SET_V2, GATE_SET_V2_1_CANDIDATES, GateSet
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder, sanity_trade_floor
from fiboki.validation.report import RungOutcome, ValidationReport, Verdict
from tests.validation_fixtures import (
    DATA_END,
    DATA_START,
    DATASET_VERSION,
    SyntheticEvaluator,
    candidate,
    plateau_mu,
    pure_noise_evaluator,
)

BASE = LadderConfig(stress_samples=10, spa_bootstraps=60, cscv_splits=6)
#: The floor every judged set admits: c_min_trl's 150 (GATE_SET_V2's is 400).
MEASURING = dataclasses.replace(BASE, min_trades=150)

#: (label, evaluator factory): edges from none to large, at trade counts from
#: below 150 (rung 0 rejects for every set) through the 150..400 band (the
#: audited floor rejects, MinTRL may admit) to well above 400.
CASES = {
    "noise": lambda: pure_noise_evaluator(),
    "noise_few_trades": lambda: pure_noise_evaluator(periods_per_day=0.20),
    "noise_under_150": lambda: pure_noise_evaluator(periods_per_day=0.06),
    "weak_edge": lambda: SyntheticEvaluator(mu=plateau_mu(peak=0.05)),
    "weak_edge_few_trades": lambda: SyntheticEvaluator(mu=plateau_mu(peak=0.08), periods_per_day=0.20),
    "modest_edge": lambda: SyntheticEvaluator(mu=plateau_mu(peak=0.12)),
    "modest_edge_other_seed": lambda: SyntheticEvaluator(mu=plateau_mu(peak=0.12), seed=77),
    "strong_edge": lambda: SyntheticEvaluator(mu=plateau_mu(peak=0.30)),
    "strong_edge_few_trades": lambda: SyntheticEvaluator(mu=plateau_mu(peak=0.30), periods_per_day=0.20),
    "strong_edge_under_150": lambda: SyntheticEvaluator(mu=plateau_mu(peak=0.30), periods_per_day=0.06),
}


def _registry() -> HoldoutRegistry:
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    return registry


def _fail_fast(gate_set: GateSet, label: str) -> ValidationReport:
    config = dataclasses.replace(BASE, min_trades=sanity_trade_floor(gate_set))
    return ValidationLadder(config=config, gate_set=gate_set).run(
        candidate(label, "a" * 64), CASES[label](),
        registry=_registry(), dataset_version_id=DATASET_VERSION,
    )


def _measured(label: str) -> dict[str, ValidationReport]:
    ladder = ValidationLadder(config=MEASURING, gate_set=GATE_SET_V2_1_CANDIDATES["c_all"])
    return ladder.run_measuring(
        candidate(label, "a" * 64), CASES[label](),
        registry=_registry(), dataset_version_id=DATASET_VERSION,
        gate_sets={GATE_SET_V2.version: GATE_SET_V2, **GATE_SET_V2_1_CANDIDATES},
    )


def _same_verdict(fail_fast: ValidationReport, measured: ValidationReport) -> None:
    assert measured.verdict == fail_fast.verdict
    assert (measured.binding_constraint.kind, measured.binding_constraint.name) == (
        fail_fast.binding_constraint.kind, fail_fast.binding_constraint.name
    )
    assert measured.gate_set_fingerprint == fail_fast.gate_set_fingerprint
    # Every gate the fail-fast ladder reached reads the same under measurement.
    reached = {r.index for r in fail_fast.rungs if r.outcome is not RungOutcome.NOT_REACHED}
    ff = {g.gate.name: (g.status, g.value) for g in fail_fast.gate_results if g.gate.rung in reached}
    mm = {g.gate.name: (g.status, g.value) for g in measured.gate_results if g.gate.rung in reached}
    assert mm == ff


@pytest.mark.parametrize("label", sorted(CASES))
def test_the_audited_set_reads_the_same_under_measurement(label: str) -> None:
    _same_verdict(_fail_fast(GATE_SET_V2, label), _measured(label)[GATE_SET_V2.version])


@pytest.mark.parametrize("name", sorted(GATE_SET_V2_1_CANDIDATES))
@pytest.mark.parametrize("label", ["noise", "weak_edge_few_trades", "strong_edge", "strong_edge_few_trades"])
def test_each_candidate_set_reads_the_same_under_measurement(name: str, label: str) -> None:
    _same_verdict(_fail_fast(GATE_SET_V2_1_CANDIDATES[name], label), _measured(label)[name])


def test_the_cases_span_the_outcomes_the_ladder_can_reach() -> None:
    """Equivalence on ten promotions would pin nothing; the cases must reject at
    rung 0, at a middle rung, and promote."""
    verdicts = {label: _fail_fast(GATE_SET_V2, label) for label in CASES}
    binding_rungs = {
        r.binding_constraint.rung_index for r in verdicts.values() if r.verdict is Verdict.REJECT
    }
    assert 0 in binding_rungs
    assert any(0 < r < 5 for r in binding_rungs), binding_rungs
    assert any(r.verdict is Verdict.PROMOTE for r in verdicts.values())


def test_measurement_reports_the_rungs_above_a_blocked_one() -> None:
    """The point of the mode: a candidate the audited floor rejects at rung 0 still
    has its walk-forward, plateau and deflation measured and every gate read."""
    report = _measured("strong_edge_few_trades")[GATE_SET_V2.version]
    assert report.verdict is Verdict.REJECT
    assert report.binding_constraint.name == "min_trades"
    assert report.rungs[0].outcome is RungOutcome.FAIL
    assert not any(r.outcome is RungOutcome.NOT_REACHED for r in report.rungs[1:6])
    fail_fast = _fail_fast(GATE_SET_V2, "strong_edge_few_trades")
    assert all(r.outcome is RungOutcome.NOT_REACHED for r in fail_fast.rungs[1:])


def test_measurement_refuses_a_persistent_registry(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "holdout.sqlite")
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    ladder = ValidationLadder(config=MEASURING, gate_set=GATE_SET_V2_1_CANDIDATES["c_all"])
    with pytest.raises(ValueError, match="in_memory"):
        ladder.run_measuring(
            candidate("x", "a" * 64), pure_noise_evaluator(),
            registry=registry, dataset_version_id=DATASET_VERSION, gate_sets={},
        )


def test_measurement_refuses_a_floor_above_a_judged_sets_floor() -> None:
    """Judging c_min_trl (floor 150) from a ladder whose rung 0 rejects below 400
    would reject at rung 0 what that set admits."""
    ladder = ValidationLadder(config=BASE, gate_set=GATE_SET_V2)  # floor 400
    with pytest.raises(ValueError, match="above the 150-trade floor"):
        ladder.run_measuring(
            candidate("x", "a" * 64), pure_noise_evaluator(),
            registry=_registry(), dataset_version_id=DATASET_VERSION,
            gate_sets={"c_min_trl": GATE_SET_V2_1_CANDIDATES["c_min_trl"]},
        )
