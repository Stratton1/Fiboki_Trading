"""A ValidationReport must round-trip deterministically and derive its own verdict."""
from __future__ import annotations

import json
import math

import pytest

from fiboki.core.enums import Provenance, StrategyLifecycle
from fiboki.validation.gates import GATE_SET_V2, GateStatus
from fiboki.validation.report import (
    BindingConstraint,
    RungOutcome,
    RungResult,
    ValidationReport,
    Verdict,
    canonicalise,
    code_version,
    decanonicalise,
)

PASSING_VALUES = {
    "n_trades": 612.0,
    "walk_forward_efficiency": 91.4,
    "oos_profitable_fraction": 0.8,
    "deflated_sharpe_ratio": 0.991,
    "pbo": 0.06,
    "spa_p_consistent": 0.002,
    "stepm_member": 1.0,
    "net_profit_at_2x_spread": 411.2,
    "point_plateau_ratio": 1.08,
}


def _rungs(n: int = 7, failing: int | None = None) -> list[RungResult]:
    out = []
    for i in range(n):
        if failing is not None and i == failing:
            out.append(RungResult(i, f"R{i}", RungOutcome.FAIL, reason=f"rung {i} said no"))
        elif failing is not None and i > failing:
            out.append(RungResult(i, f"R{i}", RungOutcome.NOT_REACHED))
        else:
            out.append(RungResult(i, f"R{i}", RungOutcome.PASS, metrics={"x": float(i)}))
    return out


def _report(values=None, rungs=None, **kwargs) -> ValidationReport:
    values = PASSING_VALUES if values is None else values
    return ValidationReport.build(
        strategy_id="n_wave_fib",
        strategy_content_hash="c" * 64,
        dataset_version_id="eurusd_h1_v7",
        gate_set=GATE_SET_V2,
        gate_results=GATE_SET_V2.evaluate(values),
        rungs=rungs if rungs is not None else _rungs(),
        engine_config={"initial_balance": 10_000.0, "account_ccy": "GBP"},
        broker_profile={"name": "ig_realistic"},
        trial_count_n=12,
        raw_trial_count=25,
        cross_trial_sharpe_variance=0.0412,
        dsr_variance_source="cross_trial_sharpes",
        provenance_labels={"pbo": Provenance.OUT_OF_SAMPLE.value},
        code_version_override="deadbeefcafe",
        **kwargs,
    )


class TestRoundTrip:
    def test_json_is_byte_stable_across_repeated_serialisation(self) -> None:
        report = _report()
        assert report.to_json() == report.to_json()
        assert report.content_hash() == report.content_hash()

    def test_reloading_reproduces_the_same_json(self) -> None:
        report = _report()
        again = ValidationReport.from_json(report.to_json())
        assert again.to_json() == report.to_json()
        assert again.content_hash() == report.content_hash()

    def test_every_recorded_fact_survives_the_round_trip(self) -> None:
        report = _report()
        again = ValidationReport.from_json(report.to_json())
        assert again.strategy_content_hash == report.strategy_content_hash
        assert again.dataset_version_id == report.dataset_version_id
        assert again.code_version == "deadbeefcafe"
        assert again.engine_config == report.engine_config
        assert again.broker_profile == report.broker_profile
        assert again.trial_count_n == 12
        assert again.raw_trial_count == 25
        assert again.cross_trial_sharpe_variance == pytest.approx(0.0412)
        assert again.gate_set_version == GATE_SET_V2.version
        assert again.gate_set_fingerprint == GATE_SET_V2.fingerprint()
        assert again.provenance_labels == report.provenance_labels
        assert [r.outcome for r in again.rungs] == [r.outcome for r in report.rungs]
        assert again.gate_values() == report.gate_values()

    def test_the_output_is_strict_json_even_with_non_finite_numbers(self) -> None:
        """A NaN literal is not JSON. A frontend or another language must be able
        to read a stored report, so non-finite values are tagged instead."""
        values = {**PASSING_VALUES, "walk_forward_efficiency": math.nan}
        report = _report(values=values)
        blob = report.to_json()
        assert "NaN" not in blob
        assert "Infinity" not in blob
        json.loads(blob)  # strict by default: would raise on a bare NaN

    def test_a_non_finite_value_round_trips_exactly(self) -> None:
        payload = {"a": math.nan, "b": math.inf, "c": -math.inf, "d": 1.5}
        restored = decanonicalise(json.loads(json.dumps(canonicalise(payload))))
        assert math.isnan(restored["a"])
        assert restored["b"] == math.inf
        assert restored["c"] == -math.inf
        assert restored["d"] == 1.5

    def test_it_saves_and_loads_from_disk(self, tmp_path) -> None:
        report = _report()
        path = report.save(tmp_path / "reports" / "r.json")
        assert ValidationReport.load(path).content_hash() == report.content_hash()


class TestVerdictIsDerived:
    """The verdict is a function of the evidence, so no caller can contradict it."""

    def test_all_gates_and_rungs_passing_promotes(self) -> None:
        report = _report()
        assert report.verdict is Verdict.PROMOTE
        assert report.passed
        assert report.binding_constraint.kind == "none"
        assert report.lifecycle_recommendation is StrategyLifecycle.CANDIDATE

    def test_a_failing_gate_rejects_and_names_the_shortfall(self) -> None:
        report = _report(values={**PASSING_VALUES, "pbo": 0.34})
        assert report.verdict is Verdict.REJECT
        assert report.binding_constraint.name == "pbo"
        assert report.binding_constraint.observed == pytest.approx(0.34)
        assert report.binding_constraint.required == pytest.approx(0.20)
        assert report.binding_constraint.shortfall == pytest.approx(0.14)
        assert "short by 0.14" in report.binding_constraint.describe()

    def test_the_binding_constraint_is_the_earliest_rung_not_the_worst_gap(self) -> None:
        """Fail-fast means the researcher must fix the EARLIEST obstacle."""
        report = _report(
            values={
                **PASSING_VALUES,
                "oos_profitable_fraction": 0.55,  # rung 2, small shortfall
                "deflated_sharpe_ratio": 0.10,  # rung 5, enormous shortfall
            }
        )
        assert report.binding_constraint.name == "oos_window_hit_rate"
        assert report.binding_constraint.rung_index == 2

    def test_a_failing_rung_beats_a_failing_gate_and_carries_its_reason(self) -> None:
        report = _report(
            values={**PASSING_VALUES, "deflated_sharpe_ratio": 0.1}, rungs=_rungs(failing=0)
        )
        assert report.verdict is Verdict.REJECT
        assert report.binding_constraint.rung_index == 0
        assert "rung 0 said no" in report.binding_constraint.reason

    def test_a_missing_gate_input_is_incomplete_not_a_pass(self) -> None:
        """Fail-closed: a statistic nobody computed is not a statistic that passed."""
        values = dict(PASSING_VALUES)
        del values["pbo"]
        report = _report(values=values)
        assert report.gate("pbo").status is GateStatus.NOT_EVALUATED
        assert report.verdict is Verdict.INCOMPLETE
        assert not report.passed
        assert report.lifecycle_recommendation is StrategyLifecycle.RESEARCH

    def test_unreached_rungs_make_the_verdict_incomplete_when_nothing_failed(self) -> None:
        rungs = [
            RungResult(0, "R0", RungOutcome.PASS),
            RungResult(1, "R1", RungOutcome.NOT_REACHED),
        ]
        report = _report(rungs=rungs)
        assert report.verdict is not Verdict.PROMOTE


class TestRungResultDiscipline:
    def test_a_rejection_without_a_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="rejected without a reason"):
            RungResult(3, "PURGED_CV", RungOutcome.FAIL)
        with pytest.raises(ValueError, match="rejected without a reason"):
            RungResult(3, "PURGED_CV", RungOutcome.ERROR)

    def test_a_pass_needs_no_reason(self) -> None:
        assert RungResult(0, "SANITY", RungOutcome.PASS).passed

    def test_not_reached_does_not_count_as_a_pass(self) -> None:
        rung = RungResult(4, "ROBUSTNESS", RungOutcome.NOT_REACHED)
        assert not rung.passed
        assert rung.outcome.blocks_promotion

    def test_the_screen_rung_can_be_marked_as_not_evidence(self) -> None:
        rung = RungResult(1, "IN_SAMPLE_SCREEN", RungOutcome.PASS, is_evidence=False)
        assert rung.to_dict()["is_evidence"] is False
        assert RungResult.from_dict(rung.to_dict()).is_evidence is False


class TestCodeVersion:
    def test_an_explicit_environment_version_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("FIBOKI_CODE_VERSION", "image-2026.09.19")
        code_version.cache_clear()
        assert code_version() == "image-2026.09.19"
        code_version.cache_clear()

    def test_it_never_raises_and_never_returns_an_empty_string(self) -> None:
        code_version.cache_clear()
        value = code_version()
        assert isinstance(value, str) and value


class TestSummary:
    def test_a_rejection_summary_names_the_binding_constraint(self) -> None:
        report = _report(values={**PASSING_VALUES, "pbo": 0.34})
        summary = report.summary()
        assert "REJECT" in summary
        assert "pbo" in summary
        assert report.strategy_content_hash[:12] in summary

    def test_binding_constraint_none_is_serialisable(self) -> None:
        none = BindingConstraint.none()
        assert BindingConstraint.from_dict(none.to_dict()) == none
