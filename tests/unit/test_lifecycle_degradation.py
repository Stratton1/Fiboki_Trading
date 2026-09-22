"""The degradation score and its hysteresis.

The flapping tests are the point. A single threshold on a noisy statistic
produces a strategy that oscillates between PAPER and WATCH every evaluation,
and after the third oscillation nobody reads the signal.
"""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from fiboki.core.enums import StrategyLifecycle
from fiboki.lifecycle.degradation import (
    DEGRADATION_CONFIG_V1,
    DegradationBand,
    DegradationConfig,
    DegradationScore,
    DegradationTracker,
    score_degradation,
)
from fiboki.lifecycle.monitor import ForwardMonitor
from fiboki.lifecycle.stopping_rules import (
    InMemoryPreRegistrationStore,
    PsrFloorParameters,
    RuleObservation,
    StoppingRuleKind,
    build_rule,
)
from tests.lifecycle_fixtures import HASH_A, T0, expectation, observation


def _score(value: float, confidence: float = 1.0) -> DegradationScore:
    return DegradationScore(
        score=value, confidence=confidence, components={"return": value}, config_version="t"
    )


def _fired_psr_evaluation():
    store = InMemoryPreRegistrationStore()
    reg = store.register(
        strategy_content_hash=HASH_A,
        kind=StoppingRuleKind.PSR_FLOOR,
        parameters=PsrFloorParameters(backtest_sharpe=0.40),
        registered_by="joe",
        reason="pre-registration",
        at=T0,
    )
    bad = np.random.default_rng(1).normal(-0.004, 0.010, 200)
    evaluation = build_rule(reg).evaluate(
        RuleObservation(strategy_content_hash=HASH_A, returns=bad, at=T0)
    )
    assert evaluation.fired
    return evaluation


# ==========================================================================
# Config
# ==========================================================================


def test_the_hysteresis_gap_is_mandatory():
    with pytest.raises(ValueError, match="the gap between them is the hysteresis"):
        DegradationConfig(watch_enter=0.35, watch_exit=0.35)


def test_the_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        DegradationConfig(weights={"return": 0.5, "cost": 0.2})


def test_entry_thresholds_increase_with_depth():
    with pytest.raises(ValueError, match="increase with depth"):
        DegradationConfig(degraded_enter=0.20, degraded_exit=0.10)


def test_one_adverse_component_can_reach_watch_on_its_own():
    """The reachability check nobody does, and which quietly disarms most
    scoring systems: if a single component's weight sits below the first band's
    entry threshold, no single failure can ever raise a flag however total it is.

    Checked against the realistic case rather than the perfect one: spread,
    slippage and latency all badly wrong while rejections stay nominal is three
    of the four cost dimensions, so the component scores 0.75, not 1.0.
    """
    config = DEGRADATION_CONFIG_V1
    for component in ("return", "cost", "risk"):
        assert config.weights[component] >= config.watch_enter, (
            f"{component} fully adverse cannot reach WATCH on its own"
        )
    # And the realistic partial case: spread, slippage and latency all badly
    # wrong while rejections stay nominal, with no regime data, which is how the
    # weight is renormalised over what was actually observed.
    observed_weight = sum(
        float(v) for k, v in config.weights.items() if k != "regime"
    )
    assert config.weights["cost"] * 0.75 / observed_weight >= config.watch_enter


def test_the_config_serialises_with_every_band():
    payload = DEGRADATION_CONFIG_V1.to_dict()
    assert set(payload["bands"]) == {"watch", "degraded", "quarantined"}
    for band in payload["bands"].values():
        assert band["enter"] > band["exit"]


# ==========================================================================
# Scoring
# ==========================================================================


def test_a_healthy_report_scores_near_zero():
    rng = np.random.default_rng(101)
    report = ForwardMonitor().compare(
        expectation(), observation(returns=rng.normal(0.004, 0.010, 220))
    )
    score = score_degradation(report)
    assert score.score == 0.0
    assert score.confidence > 0.5


def test_a_cost_blowout_scores_and_says_stored_expectancies_are_overstated():
    report = ForwardMonitor().compare(
        expectation(), observation(spread=2.5, slippage=0.8, latency=400.0)
    )
    score = score_degradation(report)
    assert score.score > 0.0
    assert score.components["cost"] > 0.5
    assert any("overstated" in n for n in score.notes)


def test_nothing_evaluated_scores_zero_and_says_why():
    """The dishonest alternative is a clean bill of health."""
    score = score_degradation(None, [])
    assert score.score == 0.0
    assert score.confidence == 0.0
    assert any("NOT because the strategy is healthy" in n for n in score.notes)


def test_an_unevaluated_component_is_excluded_rather_than_scored_zero():
    """A monitor with no regime data must not dilute a bad return score."""
    rng = np.random.default_rng(77)
    report = ForwardMonitor().compare(
        expectation(), observation(returns=rng.normal(-0.004, 0.010, 220))
    )
    score = score_degradation(report)
    assert score.components["regime"] is None
    assert score.confidence < 1.0
    assert score.score > 0.3, "the return collapse must not be diluted by ignorance"


def test_a_fired_rule_forces_quarantine_and_says_it_bypasses_dwell():
    score = score_degradation(None, [_fired_psr_evaluation()])
    assert score.forced_band is DegradationBand.QUARANTINED
    assert score.score == 1.0
    assert any("does not wait for dwell time" in n for n in score.notes)


def test_a_latched_halt_keeps_forcing_quarantine_after_the_rule_clears():
    score = score_degradation(None, [], latched_halts=1)
    assert score.forced_band is DegradationBand.QUARANTINED


def test_low_confidence_is_flagged_on_the_score():
    rng = np.random.default_rng(3)
    report = ForwardMonitor().compare(
        expectation(),
        observation(returns=rng.normal(0.004, 0.01, 200), n_cost=2, orders_submitted=3),
    )
    score = score_degradation(report, [])
    assert score.confidence < 1.0
    assert score.components["cost"] is None


# ==========================================================================
# Hysteresis
# ==========================================================================


def test_a_single_bad_reading_does_not_change_the_band():
    tracker = DegradationTracker()
    verdict = tracker.observe(_score(0.90))
    assert verdict.band is DegradationBand.HEALTHY
    assert not verdict.changed
    assert verdict.pending_band is DegradationBand.QUARANTINED
    assert verdict.consecutive == 1


WATCH_LEVEL = DEGRADATION_CONFIG_V1.watch_enter + 0.02
BELOW_WATCH = DEGRADATION_CONFIG_V1.watch_enter - 0.02


def test_a_sustained_bad_reading_changes_the_band():
    tracker = DegradationTracker()
    tracker.observe(_score(WATCH_LEVEL))
    verdict = tracker.observe(_score(WATCH_LEVEL))
    assert verdict.changed
    assert verdict.band is DegradationBand.WATCH
    assert verdict.target_state is StrategyLifecycle.WATCH


def test_strict_alternation_across_a_threshold_never_moves_the_band():
    """0.36 / 0.34 straddles the WATCH entry at 0.35. Under the dwell rule no
    two consecutive readings ever clear it, so nothing moves at all -- which is
    the correct answer to a statistic sitting on its own threshold."""
    tracker = DegradationTracker(DegradationConfig(allow_automatic_recovery=True))
    bands = [
        tracker.observe(_score(WATCH_LEVEL if i % 2 == 0 else BELOW_WATCH)).band
        for i in range(40)
    ]
    assert set(bands) == {DegradationBand.HEALTHY}


def test_a_band_entered_on_sustained_evidence_does_not_then_flap_back():
    """Two consecutive readings above the entry threshold move it to WATCH; the
    same oscillation afterwards must not move it back, because 0.34 is above the
    WATCH EXIT of 0.20. That gap is the hysteresis."""
    tracker = DegradationTracker(DegradationConfig(allow_automatic_recovery=True))
    tracker.observe(_score(WATCH_LEVEL))
    tracker.observe(_score(WATCH_LEVEL))
    assert tracker.band is DegradationBand.WATCH

    bands = [
        tracker.observe(_score(WATCH_LEVEL if i % 2 == 0 else BELOW_WATCH)).band
        for i in range(40)
    ]
    assert set(bands) == {DegradationBand.WATCH}, bands


def test_noise_around_the_deeper_thresholds_does_not_flap_either():
    tracker = DegradationTracker(DegradationConfig(allow_automatic_recovery=True))
    rng = np.random.default_rng(19)
    centre = DEGRADATION_CONFIG_V1.degraded_enter
    bands = []
    for _ in range(80):
        bands.append(
            tracker.observe(_score(float(np.clip(centre + rng.normal(0, 0.04), 0, 1)))).band
        )
    changes = sum(1 for a, b in itertools.pairwise(bands) if a is not b)
    assert changes <= 2, f"{changes} band changes on pure noise: {bands}"


def test_a_clean_recovery_takes_more_evidence_than_a_demotion():
    config = DegradationConfig(allow_automatic_recovery=True)
    tracker = DegradationTracker(config)
    for _ in range(config.consecutive_to_worsen):
        tracker.observe(_score(WATCH_LEVEL))
    assert tracker.band is DegradationBand.WATCH

    for i in range(config.consecutive_to_recover - 1):
        verdict = tracker.observe(_score(0.05))
        assert not verdict.changed, f"recovered after only {i + 1} readings"
    assert tracker.observe(_score(0.05)).changed
    assert tracker.band is DegradationBand.HEALTHY


def test_recovery_is_only_a_recommendation_by_default():
    """The default config never puts risk back on by itself: that requires a
    named human at the state machine."""
    tracker = DegradationTracker()  # allow_automatic_recovery=False
    for _ in range(2):
        tracker.observe(_score(WATCH_LEVEL))
    assert tracker.band is DegradationBand.WATCH
    verdict = None
    for _ in range(5):
        verdict = tracker.observe(_score(0.02))
    assert tracker.band is DegradationBand.WATCH
    assert verdict.recommended_band is DegradationBand.HEALTHY
    assert "explicit operator action" in verdict.reason


def test_worsening_may_skip_a_band_but_recovery_may_not():
    config = DegradationConfig(allow_automatic_recovery=True)
    tracker = DegradationTracker(config)
    for _ in range(config.consecutive_to_worsen):
        tracker.observe(_score(0.95))
    assert tracker.band is DegradationBand.QUARANTINED

    for _ in range(config.consecutive_to_recover):
        tracker.observe(_score(0.0))
    assert tracker.band is DegradationBand.DEGRADED, "recovery moves one band at a time"


def test_a_fired_rule_bypasses_dwell_entirely():
    tracker = DegradationTracker()
    score = score_degradation(None, [_fired_psr_evaluation()])
    verdict = tracker.observe(score)
    assert verdict.changed
    assert verdict.band is DegradationBand.QUARANTINED
    assert verdict.consecutive == 0


def test_a_low_confidence_score_moves_nothing_in_either_direction():
    tracker = DegradationTracker()
    for _ in range(10):
        verdict = tracker.observe(_score(0.95, confidence=0.05))
    assert tracker.band is DegradationBand.HEALTHY
    assert "below the" in verdict.reason


def test_the_tracker_starts_from_the_lifecycle_state_it_is_given():
    tracker = DegradationTracker(band=DegradationBand.DEGRADED)
    assert DegradationBand.from_lifecycle(StrategyLifecycle.DEGRADED) is DegradationBand.DEGRADED
    assert DegradationBand.from_lifecycle(StrategyLifecycle.PAPER) is DegradationBand.HEALTHY
    assert tracker.observe(_score(0.0)).band is DegradationBand.DEGRADED


def test_band_to_lifecycle_mapping_is_total_and_healthy_maps_to_nothing():
    assert DegradationBand.HEALTHY.lifecycle_state is None
    for band in (DegradationBand.WATCH, DegradationBand.DEGRADED, DegradationBand.QUARANTINED):
        assert band.lifecycle_state is not None
