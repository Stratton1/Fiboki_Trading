"""Fiboki V2 validation: the promotion pipeline, and the record it leaves behind.

``fiboki.stats`` decides whether a NUMBER can be believed. This package decides
whether a STRATEGY can be promoted, by running the statistics in a fixed order
against data that has been partitioned honestly, and by writing down everything
a later reader needs to check the conclusion.

    evaluation  the surface the ladder is defined against: windows, declared
                parameter domains, and "run this parameterisation over that
                window"
    gates       the promotion thresholds, as versioned data
    holdout     the registry that owns the final 20% and refuses a second look
    ladder      seven ordered, fail-fast rungs, each able to reject
    report      one ValidationReport per candidate, promoted or not

Typical use::

    registry = HoldoutRegistry("var/holdout.sqlite")
    registry.define("eurusd_h1_v3", data_start=..., data_end=...)

    candidate = Candidate.from_document(doc)
    report = ValidationLadder().run(
        candidate, evaluator,
        registry=registry, dataset_version_id="eurusd_h1_v3",
        engine_config=config.fingerprint(),
        broker_profile=config.profile.fingerprint(),
        actor="agent:research-loop",
    )
    print(report.summary())

The report is produced whether the candidate passed or failed, and a failed one
is worth keeping: it is the only record of where the search has already been.
"""
from __future__ import annotations

from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluationCache,
    EvaluatorConfig,
)
from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    Evaluator,
    ParameterGrid,
    WindowEvaluation,
)
from fiboki.validation.gates import (
    GATE_SET_V2,
    Comparison,
    Gate,
    GateResult,
    GateSet,
    GateStatus,
)
from fiboki.validation.holdout import (
    DEFAULT_HOLDOUT_FRACTION,
    HoldoutAlreadyConsumed,
    HoldoutConsumption,
    HoldoutError,
    HoldoutLeak,
    HoldoutRegistry,
    HoldoutSegment,
    HoldoutToken,
    UnknownHoldout,
)
from fiboki.validation.ladder import (
    DeflationRung,
    HoldoutRung,
    InSampleScreenRung,
    LadderConfig,
    LadderContext,
    PurgedCVRung,
    RobustnessRung,
    Rung,
    SanityRung,
    ValidationLadder,
    WalkForwardRung,
    default_rungs,
)
from fiboki.validation.report import (
    REPORT_VERSION,
    BindingConstraint,
    RungOutcome,
    RungResult,
    ValidationReport,
    Verdict,
    code_version,
)
from fiboki.validation.run import ValidationRun, run_validation

__all__ = [
    "DEFAULT_HOLDOUT_FRACTION",
    "GATE_SET_V2",
    "REPORT_VERSION",
    "BindingConstraint",
    "Candidate",
    "Comparison",
    "DateWindow",
    "DeflationRung",
    "EngineEvaluator",
    "EvaluationCache",
    "Evaluator",
    "EvaluatorConfig",
    "Gate",
    "GateResult",
    "GateSet",
    "GateStatus",
    "HoldoutAlreadyConsumed",
    "HoldoutConsumption",
    "HoldoutError",
    "HoldoutLeak",
    "HoldoutRegistry",
    "HoldoutRung",
    "HoldoutSegment",
    "HoldoutToken",
    "InSampleScreenRung",
    "LadderConfig",
    "LadderContext",
    "ParameterGrid",
    "PurgedCVRung",
    "RobustnessRung",
    "Rung",
    "RungOutcome",
    "RungResult",
    "SanityRung",
    "UnknownHoldout",
    "ValidationLadder",
    "ValidationReport",
    "ValidationRun",
    "Verdict",
    "WalkForwardRung",
    "WindowEvaluation",
    "code_version",
    "default_rungs",
    "run_validation",
]
