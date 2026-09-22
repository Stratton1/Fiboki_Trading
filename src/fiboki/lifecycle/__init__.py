"""Fiboki V2 strategy lifecycle: transitions, forward validation, demotion.

The gap this package closes is stated in ``docs/v2/ROADMAP.md`` §2 and
``VALIDATION_STANDARD.md`` §7: ``StrategyLifecycle`` was consumed by the risk
gateway and by portfolio construction, both tested, and **owned by nothing**. No
module made a transition, no module validated one, nothing computed degradation,
and none of the audit's three pre-registered stopping rules existed.

    state           the 13 states, the legal edges as data, and a hash-chained
                    append-only transition log
    promotion       objective criteria per edge, versioned, built on
                    ``validation/gates.py``
    monitor         eleven-dimension forward validation with block-bootstrap
                    confidence
    stopping_rules  PSR floor, bootstrap drawdown limit, CUSUM on excess return
                    -- pre-registered, latched, released only by an operator
    degradation     one score, with hysteresis, driving WATCH -> DEGRADED ->
                    QUARANTINED
    service         what the live worker calls and the API reads

Typical use::

    service = LifecycleService(machine=LifecycleStateMachine(FileTransitionLog(p)))
    service.register_strategy(
        strategy_id="ichimoku_baseline",
        strategy_content_hash=doc.content_hash(),
        actor=Actor.human("joe"),
        reason="V2 seed document",
    )
    ...
    evaluation = service.evaluate(h, expectation=expected, observation=observed)
    print(evaluation.summary())

The asymmetry to keep in mind: this package demotes automatically and promotes
only for a named human, and nothing in it can reach ``LIVE`` without a written
human authorisation bound to that exact strategy content hash.
"""
from __future__ import annotations

from fiboki.lifecycle.degradation import (
    DEGRADATION_CONFIG_V1,
    DegradationBand,
    DegradationConfig,
    DegradationScore,
    DegradationTracker,
    DegradationVerdict,
    score_degradation,
)
from fiboki.lifecycle.monitor import (
    Divergence,
    DivergenceDimension,
    DivergenceReport,
    DivergenceStatus,
    Expectation,
    ForwardMonitor,
    MonitorConfig,
    Observation,
    max_drawdown,
    two_sample_block_ks,
)
from fiboki.lifecycle.promotion import (
    PROMOTION_RULES_V1,
    PromotionDecision,
    PromotionEvidence,
    PromotionRule,
    PromotionRuleSet,
    UnknownPromotion,
    evaluate_promotion,
)
from fiboki.lifecycle.service import (
    LifecycleEvaluation,
    LifecycleService,
    PromotionRefused,
    StrategyStatus,
)
from fiboki.lifecycle.state import (
    HEALTH_STATES,
    LADDER,
    LEGAL_TRANSITIONS,
    RUNNING_STATES,
    TERMINAL_STATES,
    Actor,
    ActorKind,
    ChainVerification,
    Evidence,
    EvidenceKind,
    FileTransitionLog,
    HumanAuthorisation,
    HumanAuthorisationRequired,
    IllegalTransition,
    InMemoryTransitionLog,
    LifecycleError,
    LifecycleStateMachine,
    LifecycleTransition,
    StrategyRecord,
    TransitionKind,
    TransitionLog,
    TransitionRecord,
    UnknownStrategy,
    transition_table,
)
from fiboki.lifecycle.stopping_rules import (
    ALL_RULE_KINDS,
    BootstrapDrawdownParameters,
    BootstrapDrawdownRule,
    CusumCalibration,
    CusumExcessReturnRule,
    CusumParameters,
    DrawdownLimitCalibration,
    FileHaltJournal,
    FilePreRegistrationStore,
    HaltEvent,
    HaltNotReleasable,
    HaltRegistry,
    InMemoryHaltJournal,
    InMemoryPreRegistrationStore,
    PreRegistrationStore,
    PsrFloorParameters,
    PsrFloorRule,
    RuleEvaluation,
    RuleObservation,
    RuleRegistration,
    RuleStatus,
    StoppingRule,
    StoppingRuleError,
    StoppingRuleKind,
    build_rule,
    calibrate_cusum_threshold,
    calibrate_drawdown_limit,
    cusum_parameters_for,
    cusum_path,
)

__all__ = [
    "ALL_RULE_KINDS",
    "DEGRADATION_CONFIG_V1",
    "HEALTH_STATES",
    "LADDER",
    "LEGAL_TRANSITIONS",
    "PROMOTION_RULES_V1",
    "RUNNING_STATES",
    "TERMINAL_STATES",
    "Actor",
    "ActorKind",
    "BootstrapDrawdownParameters",
    "BootstrapDrawdownRule",
    "ChainVerification",
    "CusumCalibration",
    "CusumExcessReturnRule",
    "CusumParameters",
    "DegradationBand",
    "DegradationConfig",
    "DegradationScore",
    "DegradationTracker",
    "DegradationVerdict",
    "Divergence",
    "DivergenceDimension",
    "DivergenceReport",
    "DivergenceStatus",
    "DrawdownLimitCalibration",
    "Evidence",
    "EvidenceKind",
    "Expectation",
    "FileHaltJournal",
    "FilePreRegistrationStore",
    "FileTransitionLog",
    "ForwardMonitor",
    "HaltEvent",
    "HaltNotReleasable",
    "HaltRegistry",
    "HumanAuthorisation",
    "HumanAuthorisationRequired",
    "IllegalTransition",
    "InMemoryHaltJournal",
    "InMemoryPreRegistrationStore",
    "InMemoryTransitionLog",
    "LifecycleError",
    "LifecycleEvaluation",
    "LifecycleService",
    "LifecycleStateMachine",
    "LifecycleTransition",
    "MonitorConfig",
    "Observation",
    "PreRegistrationStore",
    "PromotionDecision",
    "PromotionEvidence",
    "PromotionRefused",
    "PromotionRule",
    "PromotionRuleSet",
    "PsrFloorParameters",
    "PsrFloorRule",
    "RuleEvaluation",
    "RuleObservation",
    "RuleRegistration",
    "RuleStatus",
    "StoppingRule",
    "StoppingRuleError",
    "StoppingRuleKind",
    "StrategyRecord",
    "StrategyStatus",
    "TransitionKind",
    "TransitionLog",
    "TransitionRecord",
    "UnknownPromotion",
    "UnknownStrategy",
    "build_rule",
    "calibrate_cusum_threshold",
    "calibrate_drawdown_limit",
    "cusum_parameters_for",
    "cusum_path",
    "evaluate_promotion",
    "max_drawdown",
    "score_degradation",
    "transition_table",
    "two_sample_block_ks",
]
