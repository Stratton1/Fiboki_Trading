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


def test_the_plateau_rationale_states_the_ratio_the_gate_reads() -> None:
    """The text shown beside the gate is the definition in stats.stability
    (``(s + |s|) / (m + |s|)``, neighbours excluding the point), and its 60%
    reading is the threshold: plateau_ratio(1.0, 0.6) == 1.25."""
    from fiboki.stats.stability import plateau_ratio

    gate = GATE_SET_V2.by_name("parameter_plateau")
    assert "(s + |s|) / (m + |s|)" in gate.rationale
    assert "EXCLUDING the point" in gate.rationale and "60%" in gate.rationale
    assert "divided by the mean of its neighbourhood" not in gate.rationale
    assert plateau_ratio(1.0, 0.6) == pytest.approx(gate.threshold)


class TestTheAuditedSetIsFrozen:
    #: GATE_SET_V2.fingerprint() at v2.0.0-audit (verified 2026-09-30, commit 7e487a7).
    #: A change here is a new gate set, which needs a new version, not an edit.
    PINNED = "fe7daa4c71e886b120f7ebe2e14cd5331e0c906b89e473f290f394e6de6e3ea2"

    def test_the_fingerprint_is_pinned(self) -> None:
        assert GATE_SET_V2.fingerprint() == self.PINNED

    def test_building_the_candidates_did_not_touch_it(self) -> None:
        from fiboki.validation.gates import GATE_SET_V2_1_CANDIDATES

        assert GATE_SET_V2_1_CANDIDATES  # built at import, after GATE_SET_V2
        assert GATE_SET_V2.version == "v2.0.0-audit"
        assert GATE_SET_V2.fingerprint() == self.PINNED


#: candidate -> (audited gates removed, candidate gates added, in declaration order)
_INTENDED: dict[str, tuple[set[str], list[str]]] = {
    "c_min_trl": ({"min_trades"}, ["min_track_record"]),
    "c_wfe_log": (
        {"walk_forward_efficiency"},
        ["walk_forward_min_oos_trades", "walk_forward_efficiency_log_growth"],
    ),
    "c_hit_wilson": ({"oos_window_hit_rate"}, ["oos_window_hit_rate_wilson"]),
    "c_plateau_median": (
        {"parameter_plateau"},
        ["plateau_neighbourhood_median", "plateau_neighbourhood_min"],
    ),
    "c_dsr_family": ({"deflated_sharpe"}, ["deflated_sharpe_family_n"]),
}


class TestE2Candidates:
    """Candidate sets for the E-2 calibration study: data, versioned, never promotion bars."""

    @staticmethod
    def _candidates():
        from fiboki.validation.gates import GATE_SET_V2_1_CANDIDATES

        return GATE_SET_V2_1_CANDIDATES

    def test_the_named_candidates_exist(self) -> None:
        assert set(self._candidates()) == {*_INTENDED, "c_all", "c_hit_8fold"}

    def test_the_eight_fold_candidate_moves_one_threshold_and_names_its_fold_count(self) -> None:
        from fiboki.validation.gates import CANDIDATE_LADDER_FOLDS

        eight = self._candidates()["c_hit_8fold"]
        audited = {g.name: g.to_dict() for g in GATE_SET_V2.gates}
        mine = {g.name: g.to_dict() for g in eight.gates}
        assert set(mine) == set(audited)
        assert mine["oos_window_hit_rate"]["threshold"] == 0.625  # 5 of 8
        for name in set(audited) - {"oos_window_hit_rate"}:
            assert mine[name] == audited[name]
        assert CANDIDATE_LADDER_FOLDS == {"c_hit_8fold": 8}
        assert "walk_forward_folds must be 8" in eight.description

    @pytest.mark.parametrize("name", list(_INTENDED))
    def test_each_differs_from_v2_in_exactly_the_intended_gate(self, name) -> None:
        removed, added = _INTENDED[name]
        candidate = self._candidates()[name]
        audited = {g.name: g.to_dict() for g in GATE_SET_V2.gates}
        mine = {g.name: g.to_dict() for g in candidate.gates}
        assert set(audited) - set(mine) == removed
        assert [g.name for g in candidate.gates if g.name not in audited] == added
        # Every other audited gate is carried over byte for byte, in its place.
        for gate_name in set(audited) - removed:
            assert mine[gate_name] == audited[gate_name]
        kept = [g.name for g in candidate.gates if g.name in audited]
        assert kept == [g.name for g in GATE_SET_V2.gates if g.name not in removed]

    def test_c_all_is_every_replacement_together(self) -> None:
        c_all = self._candidates()["c_all"]
        removed = set().union(*(r for r, _ in _INTENDED.values()))
        added = {g for _, a in _INTENDED.values() for g in a}
        names = [g.name for g in c_all.gates]
        assert removed.isdisjoint(names)
        assert added <= set(names)
        assert len(names) == len(GATE_SET_V2.gates) - len(removed) + len(added)

    @pytest.mark.parametrize(
        ("name", "gate", "metric", "comparison", "threshold", "rung"),
        [
            ("c_min_trl", "min_track_record", "n_trades_over_min_trl", Comparison.GTE, 1.0, 0),
            ("c_wfe_log", "walk_forward_efficiency_log_growth",
             "walk_forward_efficiency_log_growth", Comparison.GTE, 50.0, 2),
            ("c_wfe_log", "walk_forward_min_oos_trades", "walk_forward_min_oos_trades",
             Comparison.GTE, 30.0, 2),
            ("c_hit_wilson", "oos_window_hit_rate_wilson",
             "oos_profitable_fraction_wilson_lower", Comparison.GT, 0.5, 2),
            ("c_plateau_median", "plateau_neighbourhood_median",
             "plateau_neighbourhood_median_ratio", Comparison.GTE, 0.6, 4),
            ("c_plateau_median", "plateau_neighbourhood_min", "plateau_neighbourhood_min",
             Comparison.GT, 0.0, 4),
            ("c_dsr_family", "deflated_sharpe_family_n", "deflated_sharpe_ratio_family_n",
             Comparison.GT, 0.95, 5),
        ],
    )
    def test_the_candidate_thresholds(self, name, gate, metric, comparison, threshold, rung) -> None:
        g = self._candidates()[name].by_name(gate)
        assert (g.metric, g.comparison, g.threshold, g.rung) == (metric, comparison, threshold, rung)
        assert len(g.rationale) > 40

    def test_no_candidate_can_be_mistaken_for_a_calibrated_or_audited_set(self) -> None:
        from fiboki.validation.gates import CANDIDATE_VERSION_PREFIX

        fingerprints = {GATE_SET_V2.fingerprint()}
        for name, gates in self._candidates().items():
            assert gates.version == f"{CANDIDATE_VERSION_PREFIX}{name}"
            assert gates.version.startswith("v2.1.0-candidate:")
            assert "calibrated" not in gates.version
            assert "NOT FOR PROMOTION" in gates.description
            fingerprints.add(gates.fingerprint())
        assert len(fingerprints) == 1 + len(self._candidates())
