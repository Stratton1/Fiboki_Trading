"""Promotion gates are data: versioned, fingerprinted, and fail-closed."""
from __future__ import annotations

import math

import pytest

from fiboki.validation.gates import GATE_SET_V2, Comparison, Gate, GateSet, GateStatus


class TestTheAuditThresholds:
    """The numbers the V2 audit specified. Changing one needs a new version."""

    @pytest.mark.parametrize(
        ("name", "comparison", "threshold"),
        [
            ("deflated_sharpe", Comparison.GT, 0.95),
            ("pbo", Comparison.LT, 0.20),
            ("spa_consistent_p", Comparison.LT, 0.05),
            ("walk_forward_efficiency", Comparison.GTE, 50.0),
            ("oos_window_hit_rate", Comparison.GTE, 0.60),
            ("min_trades", Comparison.GTE, 400.0),
            ("survives_2x_spread", Comparison.GT, 0.0),
            ("parameter_plateau", Comparison.LTE, 1.25),
            ("stepm_survivor", Comparison.GTE, 1.0),
        ],
    )
    def test_threshold(self, name, comparison, threshold) -> None:
        gate = GATE_SET_V2.by_name(name)
        assert gate.comparison is comparison
        assert gate.threshold == pytest.approx(threshold)

    def test_the_min_trades_gate_is_five_times_v1s(self) -> None:
        """V1 ranked on 80 trades. That is the number this package exists to raise."""
        assert GATE_SET_V2.by_name("min_trades").threshold == 400.0

    def test_every_gate_explains_itself(self) -> None:
        for gate in GATE_SET_V2.gates:
            assert len(gate.rationale) > 40, f"{gate.name} has no stated reason"

    def test_every_gate_names_the_rung_that_produces_it(self) -> None:
        for gate in GATE_SET_V2.gates:
            assert 0 <= gate.rung <= 6


class TestVersioning:
    def test_the_fingerprint_is_stable(self) -> None:
        assert GATE_SET_V2.fingerprint() == GATE_SET_V2.fingerprint()

    def test_changing_a_threshold_changes_the_fingerprint(self) -> None:
        looser = GATE_SET_V2.with_overrides("v2.0.0-intraday", min_trades=150.0)
        assert looser.fingerprint() != GATE_SET_V2.fingerprint()
        assert looser.by_name("min_trades").threshold == 150.0
        assert GATE_SET_V2.by_name("min_trades").threshold == 400.0  # unchanged

    def test_a_gate_set_round_trips(self) -> None:
        restored = GateSet.from_dict(GATE_SET_V2.to_dict())
        assert restored.fingerprint() == GATE_SET_V2.fingerprint()

    def test_overriding_an_unknown_gate_raises(self) -> None:
        with pytest.raises(KeyError):
            GATE_SET_V2.with_overrides("v9", not_a_gate=1.0)

    def test_duplicate_gate_names_are_refused(self) -> None:
        gate = Gate("dup", "m", Comparison.GT, 0.0, rung=0)
        with pytest.raises(ValueError, match="unique"):
            GateSet("v1", (gate, gate))


class TestFailClosed:
    def test_a_missing_value_is_not_evaluated_and_blocks(self) -> None:
        result = GATE_SET_V2.by_name("pbo").evaluate(None)
        assert result.status is GateStatus.NOT_EVALUATED
        assert result.status.blocks_promotion
        assert not result.passed

    def test_a_nan_value_is_not_evaluated_and_blocks(self) -> None:
        result = GATE_SET_V2.by_name("parameter_plateau").evaluate(math.nan)
        assert result.status is GateStatus.NOT_EVALUATED
        assert result.status.blocks_promotion

    def test_not_applicable_does_not_block(self) -> None:
        result = GATE_SET_V2.by_name("parameter_plateau").evaluate(None, applicable=False)
        assert result.status is GateStatus.NOT_APPLICABLE
        assert not result.status.blocks_promotion

    def test_a_value_exactly_on_a_strict_boundary_fails(self) -> None:
        """``DSR > 0.95`` means 0.95 is not enough, and the shortfall is zero."""
        result = GATE_SET_V2.by_name("deflated_sharpe").evaluate(0.95)
        assert result.status is GateStatus.FAIL
        assert result.shortfall == pytest.approx(0.0)

    def test_a_value_exactly_on_an_inclusive_boundary_passes(self) -> None:
        assert GATE_SET_V2.by_name("min_trades").evaluate(400.0).passed


class TestShortfall:
    @pytest.mark.parametrize(
        ("name", "value", "expected"),
        [
            ("deflated_sharpe", 0.71, 0.24),
            ("pbo", 0.34, 0.14),
            ("walk_forward_efficiency", 12.0, 38.0),
            ("parameter_plateau", 2.0, 0.75),
        ],
    )
    def test_shortfall_is_in_the_metrics_own_units(self, name, value, expected) -> None:
        assert GATE_SET_V2.by_name(name).evaluate(value).shortfall == pytest.approx(
            expected
        )

    def test_a_passing_gate_has_zero_shortfall(self) -> None:
        assert GATE_SET_V2.by_name("pbo").evaluate(0.05).shortfall == 0.0

    def test_a_boolean_gate_describes_itself_in_words(self) -> None:
        described = GATE_SET_V2.by_name("stepm_survivor").evaluate(0.0).describe()
        assert "no (required yes)" in described


class TestBindingConstraint:
    def test_it_is_the_lowest_rung_among_the_blockers(self) -> None:
        results = GATE_SET_V2.evaluate(
            {
                "n_trades": 900.0,
                "walk_forward_efficiency": 10.0,  # rung 2
                "oos_profitable_fraction": 0.9,
                "deflated_sharpe_ratio": 0.1,  # rung 5, far worse
                "pbo": 0.9,
                "spa_p_consistent": 0.9,
                "stepm_member": 0.0,
                "net_profit_at_2x_spread": 1.0,
                "point_plateau_ratio": 1.0,
            }
        )
        binding = GateSet.binding_constraint(results)
        assert binding.gate.name == "walk_forward_efficiency"

    def test_it_is_none_when_everything_passes(self) -> None:
        results = GATE_SET_V2.evaluate(
            {
                "n_trades": 900.0,
                "walk_forward_efficiency": 80.0,
                "oos_profitable_fraction": 0.9,
                "deflated_sharpe_ratio": 0.99,
                "pbo": 0.01,
                "spa_p_consistent": 0.001,
                "stepm_member": 1.0,
                "net_profit_at_2x_spread": 50.0,
                "point_plateau_ratio": 1.0,
            }
        )
        assert GateSet.binding_constraint(results) is None
