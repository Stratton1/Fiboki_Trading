"""Promotion criteria: versioned data, fail-closed gates, and the LIVE seal."""
from __future__ import annotations

import itertools

import pytest

from fiboki.core.enums import StrategyLifecycle
from fiboki.lifecycle.promotion import (
    PROMOTION_RULES_V1,
    PromotionEvidence,
    PromotionRuleSet,
    UnknownPromotion,
    evaluate_promotion,
)
from fiboki.lifecycle.service import LifecycleService, PromotionRefused
from fiboki.lifecycle.state import (
    LADDER,
    LEGAL_TRANSITIONS,
    HumanAuthorisationRequired,
    LifecycleStateMachine,
    TransitionKind,
)
from fiboki.validation.gates import GATE_SET_V2, PRODUCTION_GATE_SET, GateStatus
from fiboki.validation.report import RungOutcome, RungResult, ValidationReport, Verdict
from tests.lifecycle_fixtures import (
    AUTOMATED_ACTORS,
    HASH_A,
    HUMAN,
    T0,
    authorisation,
    machine_at,
    promotion_evidence,
    promotion_metrics,
)

LADDER_EDGES = list(itertools.pairwise(LADDER))


def _passing_report(content_hash: str = HASH_A, **overrides) -> ValidationReport:
    # Every metric BOTH sets read, so a report passes under the production set
    # (v2.1.0-calibrated since 2026-10-08) and the audited one alike.
    values = {
        "n_trades": 500.0,
        "n_trades_over_min_trl": 1.6,
        "walk_forward_efficiency": 72.0,
        "walk_forward_efficiency_log_growth": 70.0,
        "walk_forward_min_oos_trades": 60.0,
        "oos_profitable_fraction": 0.71,
        "deflated_sharpe_ratio": 0.97,
        "pbo": 0.11,
        "spa_p_consistent": 0.01,
        "stepm_member": 1.0,
        "net_profit_at_2x_spread": 1200.0,
        "point_plateau_ratio": 1.08,
        "plateau_neighbourhood_median_ratio": 0.85,
        "plateau_neighbourhood_min": 0.4,
    }
    values.update(overrides)
    return ValidationReport.build(
        strategy_id="ichimoku_baseline",
        strategy_content_hash=content_hash,
        dataset_version_id="eurusd_h1_v3",
        gate_set=PRODUCTION_GATE_SET,
        gate_results=PRODUCTION_GATE_SET.evaluate(values),
        rungs=[
            RungResult(index=i, name=f"RUNG{i}", outcome=RungOutcome.PASS)
            for i in range(7)
        ],
        code_version_override="testsha",
    )


# ------------------------------------------------------------- the rule set


def test_one_rule_per_ladder_edge_and_nothing_else():
    assert {r.key for r in PROMOTION_RULES_V1.rules} == set(LADDER_EDGES)
    assert PROMOTION_RULES_V1.covers_every_promotion(LEGAL_TRANSITIONS) == ()


def test_every_promotion_edge_in_the_state_machine_has_criteria():
    """The gap this package closes, asserted directly: a transition the machine
    permits and nothing judges."""
    promotions = [t for t in LEGAL_TRANSITIONS if t.kind is TransitionKind.PROMOTION]
    for t in promotions:
        assert PROMOTION_RULES_V1.has_rule(t.from_state, t.to_state), t


def test_the_rule_set_is_fingerprinted_and_the_fingerprint_moves_with_a_threshold():
    before = PROMOTION_RULES_V1.fingerprint()
    rule = PROMOTION_RULES_V1.rule(StrategyLifecycle.PAPER, StrategyLifecycle.SHADOW)
    softer = PromotionRuleSet(
        version="lifecycle_promotion_v1_softer",
        rules=tuple(
            r
            if r.key != rule.key
            else type(r)(
                from_state=r.from_state,
                to_state=r.to_state,
                gate_set=r.gate_set.with_overrides("softer", min_days_in_paper=1.0),
                requires_validation_report=r.requires_validation_report,
                requires_holdout_consumed_once=r.requires_holdout_consumed_once,
                requires_human_authorisation=r.requires_human_authorisation,
            )
            for r in PROMOTION_RULES_V1.rules
        ),
    )
    assert softer.fingerprint() != before


def test_an_unknown_transition_raises_rather_than_passing():
    with pytest.raises(UnknownPromotion):
        evaluate_promotion(
            PROMOTION_RULES_V1,
            StrategyLifecycle.PAPER,
            StrategyLifecycle.LIVE,
            PromotionEvidence(),
        )


# ----------------------------------------------------------- fail-closed


@pytest.mark.parametrize(("lower", "upper"), LADDER_EDGES, ids=lambda s: s.value)
def test_a_missing_metric_blocks_every_promotion(lower, upper):
    decision = evaluate_promotion(
        PROMOTION_RULES_V1, lower, upper, PromotionEvidence(metrics={})
    )
    rule = PROMOTION_RULES_V1.rule(lower, upper)
    if not rule.gate_set.gates:
        pytest.skip("rule carries no numeric gates")
    assert not decision.allowed
    assert all(
        g.status is GateStatus.NOT_EVALUATED for g in decision.gate_results
    ), "a gate nobody ran is not a gate that passed"


@pytest.mark.parametrize(("lower", "upper"), LADDER_EDGES, ids=lambda s: s.value)
def test_every_edge_passes_on_complete_satisfying_evidence(lower, upper):
    decision = evaluate_promotion(
        PROMOTION_RULES_V1,
        lower,
        upper,
        PromotionEvidence(
            metrics=promotion_metrics(),
            validation_report=_passing_report(),
            holdout_consumption_count=1,
            human_authorisation=authorisation(to_state=upper),
        ),
        strategy_content_hash=HASH_A,
    )
    assert decision.allowed, decision.describe()


def test_validating_to_candidate_needs_a_report_and_one_holdout_look():
    base = dict(
        metrics=promotion_metrics(),
        validation_report=_passing_report(),
        holdout_consumption_count=1,
    )
    ok = evaluate_promotion(
        PROMOTION_RULES_V1,
        StrategyLifecycle.VALIDATING,
        StrategyLifecycle.CANDIDATE,
        PromotionEvidence(**base),
    )
    assert ok.allowed

    for count, fragment in ((0, "has not been consumed"), (2, "exactly once"), (None, "never checked")):
        decision = evaluate_promotion(
            PROMOTION_RULES_V1,
            StrategyLifecycle.VALIDATING,
            StrategyLifecycle.CANDIDATE,
            PromotionEvidence(**{**base, "holdout_consumption_count": count}),
        )
        assert not decision.allowed
        assert fragment in decision.binding_constraint


def test_a_report_under_the_superseded_audited_set_does_not_count():
    """v2.0.0-audit was the production bar until 2026-10-08. A report that cleared
    it (all stored reports did, or failed it) is not evidence under the switch."""
    report = ValidationReport.build(
        strategy_id="x",
        strategy_content_hash=HASH_A,
        dataset_version_id="d",
        gate_set=GATE_SET_V2,
        gate_results=GATE_SET_V2.evaluate(
            {
                "n_trades": 500.0, "walk_forward_efficiency": 72.0,
                "oos_profitable_fraction": 0.71, "deflated_sharpe_ratio": 0.97, "pbo": 0.11,
                "spa_p_consistent": 0.01, "stepm_member": 1.0,
                "net_profit_at_2x_spread": 1200.0, "point_plateau_ratio": 1.08,
            }
        ),
        rungs=[RungResult(index=i, name=f"RUNG{i}", outcome=RungOutcome.PASS) for i in range(7)],
        code_version_override="testsha",
    )
    assert report.verdict is Verdict.PROMOTE
    decision = evaluate_promotion(
        PROMOTION_RULES_V1,
        StrategyLifecycle.VALIDATING,
        StrategyLifecycle.CANDIDATE,
        PromotionEvidence(
            metrics=promotion_metrics(), validation_report=report, holdout_consumption_count=1,
        ),
    )
    assert not decision.allowed
    assert "not the production set v2.1.0-calibrated" in decision.binding_constraint


def test_a_report_under_a_softer_gate_set_does_not_count():
    softer = GATE_SET_V2.with_overrides("v2.0.0-softer", min_trades=50.0)
    report = ValidationReport.build(
        strategy_id="x",
        strategy_content_hash=HASH_A,
        dataset_version_id="d",
        gate_set=softer,
        gate_results=softer.evaluate(
            {
                "n_trades": 60.0,
                "walk_forward_efficiency": 72.0,
                "oos_profitable_fraction": 0.71,
                "deflated_sharpe_ratio": 0.97,
                "pbo": 0.11,
                "spa_p_consistent": 0.01,
                "stepm_member": 1.0,
                "net_profit_at_2x_spread": 1200.0,
                "point_plateau_ratio": 1.08,
            }
        ),
        rungs=[
            RungResult(index=i, name=f"RUNG{i}", outcome=RungOutcome.PASS)
            for i in range(7)
        ],
        code_version_override="testsha",
    )
    assert report.verdict is Verdict.PROMOTE
    decision = evaluate_promotion(
        PROMOTION_RULES_V1,
        StrategyLifecycle.VALIDATING,
        StrategyLifecycle.CANDIDATE,
        PromotionEvidence(
            metrics=promotion_metrics(),
            validation_report=report,
            holdout_consumption_count=1,
        ),
    )
    assert not decision.allowed
    assert "not the production set" in decision.binding_constraint


def test_a_rejected_report_blocks_with_its_own_binding_constraint():
    report = _passing_report(deflated_sharpe_ratio=0.41)
    decision = evaluate_promotion(
        PROMOTION_RULES_V1,
        StrategyLifecycle.VALIDATING,
        StrategyLifecycle.CANDIDATE,
        PromotionEvidence(
            metrics=promotion_metrics(),
            validation_report=report,
            holdout_consumption_count=1,
        ),
    )
    assert not decision.allowed
    assert "deflated_sharpe" in decision.binding_constraint


def test_the_declared_external_trial_count_must_be_non_zero():
    decision = evaluate_promotion(
        PROMOTION_RULES_V1,
        StrategyLifecycle.VALIDATING,
        StrategyLifecycle.CANDIDATE,
        PromotionEvidence(
            metrics=promotion_metrics(external_trial_count=0.0),
            validation_report=_passing_report(),
            holdout_consumption_count=1,
        ),
    )
    assert not decision.allowed
    assert "external_trial_count_declared" in decision.binding_constraint


@pytest.mark.parametrize(
    ("edge", "metric", "bad"),
    [
        ((StrategyLifecycle.PAPER, StrategyLifecycle.SHADOW), "days_in_state", 29.0),
        ((StrategyLifecycle.PAPER, StrategyLifecycle.SHADOW), "forward_trades", 39.0),
        (
            (StrategyLifecycle.PAPER, StrategyLifecycle.SHADOW),
            "forward_to_backtest_sharpe_ratio",
            0.49,
        ),
        (
            (StrategyLifecycle.PAPER, StrategyLifecycle.SHADOW),
            "divergence_dimensions_flagged",
            1.0,
        ),
        ((StrategyLifecycle.SHADOW, StrategyLifecycle.DEMO), "worker_kill_alert_proven", 0.0),
        ((StrategyLifecycle.SHADOW, StrategyLifecycle.DEMO), "reconciliation_clean_days", 6.0),
        ((StrategyLifecycle.DEMO, StrategyLifecycle.APPROVED), "days_in_state", 89.0),
        ((StrategyLifecycle.DEMO, StrategyLifecycle.APPROVED), "forward_trades", 99.0),
        ((StrategyLifecycle.DEMO, StrategyLifecycle.APPROVED), "unreconciled_fills", 1.0),
        (
            (StrategyLifecycle.DEMO, StrategyLifecycle.APPROVED),
            "realised_cost_to_modelled_ratio",
            1.6,
        ),
        (
            (StrategyLifecycle.APPROVED, StrategyLifecycle.LIVE),
            "kill_switch_drill_completed",
            0.0,
        ),
        (
            (StrategyLifecycle.APPROVED, StrategyLifecycle.LIVE),
            "stopping_rules_pre_registered",
            2.0,
        ),
        (
            (StrategyLifecycle.APPROVED, StrategyLifecycle.LIVE),
            "stopping_rules_registered_before_forward_data",
            0.0,
        ),
    ],
    ids=lambda v: str(v),
)
def test_each_forward_criterion_blocks_on_its_own(edge, metric, bad):
    lower, upper = edge
    decision = evaluate_promotion(
        PROMOTION_RULES_V1,
        lower,
        upper,
        PromotionEvidence(
            metrics=promotion_metrics(**{metric: bad}),
            validation_report=_passing_report(),
            holdout_consumption_count=1,
            human_authorisation=authorisation(to_state=upper),
        ),
        strategy_content_hash=HASH_A,
    )
    assert not decision.allowed, f"{metric}={bad} should have blocked {lower}->{upper}"


# ------------------------------------------------------------ LIVE is sealed


def test_approved_to_live_is_the_only_rule_requiring_a_human_authorisation():
    requiring = [r.key for r in PROMOTION_RULES_V1.rules if r.requires_human_authorisation]
    assert requiring == [(StrategyLifecycle.APPROVED, StrategyLifecycle.LIVE)]


def test_live_promotion_is_refused_without_an_authorisation():
    decision = evaluate_promotion(
        PROMOTION_RULES_V1,
        StrategyLifecycle.APPROVED,
        StrategyLifecycle.LIVE,
        PromotionEvidence(metrics=promotion_metrics()),
        strategy_content_hash=HASH_A,
    )
    assert not decision.allowed
    assert "human authorisation" in decision.binding_constraint


@pytest.mark.parametrize("actor", AUTOMATED_ACTORS, ids=lambda a: a.name)
def test_the_service_refuses_every_automated_route_to_live(actor):
    """Through the SERVICE, with satisfying criteria and a valid authorisation.

    The criteria are met and the paperwork is in order; the only thing wrong is
    who is asking. That is the property that must hold, because a passing
    evidence set is exactly the situation in which an automated promoter would
    look reasonable.
    """
    service = LifecycleService(machine=machine_at(StrategyLifecycle.APPROVED))
    with pytest.raises(HumanAuthorisationRequired):
        service.promote(
            HASH_A,
            StrategyLifecycle.LIVE,
            actor=actor,
            reason="criteria met",
            evidence=promotion_evidence(),
            at=T0,
        )
    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.APPROVED


@pytest.mark.parametrize("actor", AUTOMATED_ACTORS, ids=lambda a: a.name)
@pytest.mark.parametrize(
    "start",
    [StrategyLifecycle.DEMO, StrategyLifecycle.APPROVED],
    ids=lambda s: s.value,
)
def test_no_automated_actor_can_walk_the_last_stages(actor, start):
    """Not just the final hop: every promotion into a running state is human-only,
    so there is no sequence of automated steps that ends at LIVE."""
    service = LifecycleService(machine=machine_at(start))
    nxt = LADDER[LADDER.index(start) + 1]
    with pytest.raises(HumanAuthorisationRequired):
        service.promote(
            HASH_A,
            nxt,
            actor=actor,
            reason="criteria met",
            evidence=promotion_evidence(),
            at=T0,
        )


def test_a_human_with_everything_in_order_reaches_live_through_the_service():
    service = LifecycleService(machine=machine_at(StrategyLifecycle.APPROVED))
    record = service.promote(
        HASH_A,
        StrategyLifecycle.LIVE,
        actor=HUMAN,
        reason="Gate C evidence reviewed and signed off",
        evidence=promotion_evidence(),
        at=T0,
    )
    assert record.state is StrategyLifecycle.LIVE
    assert service.machine.verify().ok


def test_the_service_refuses_and_reports_the_binding_constraint():
    service = LifecycleService(machine=machine_at(StrategyLifecycle.PAPER))
    with pytest.raises(PromotionRefused) as exc:
        service.promote(
            HASH_A,
            StrategyLifecycle.SHADOW,
            actor=HUMAN,
            reason="early",
            evidence=promotion_evidence(days_in_state=3.0),
            at=T0,
        )
    assert "min_days_in_paper" in exc.value.decision.binding_constraint
    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.PAPER


def test_a_refused_promotion_writes_nothing_to_the_log():
    machine = machine_at(StrategyLifecycle.PAPER)
    before = len(machine.log)
    service = LifecycleService(machine=machine)
    with pytest.raises(PromotionRefused):
        service.promote(
            HASH_A,
            StrategyLifecycle.SHADOW,
            actor=HUMAN,
            reason="early",
            evidence=promotion_evidence(forward_trades=1.0),
            at=T0,
        )
    assert len(machine.log) == before


def test_promotion_writes_the_decision_as_evidence():
    service = LifecycleService(machine=machine_at(StrategyLifecycle.PAPER))
    record = service.promote(
        HASH_A,
        StrategyLifecycle.SHADOW,
        actor=HUMAN,
        reason="Gate B first half satisfied",
        evidence=promotion_evidence(),
        at=T0,
    )
    kinds = {e.kind.value for e in record.history[-1].evidence}
    assert "promotion_evaluation" in kinds


def test_a_fresh_machine_has_no_states_to_leak():
    assert LifecycleStateMachine().records() == ()
