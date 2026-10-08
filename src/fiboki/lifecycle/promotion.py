"""Promotion criteria per transition, as versioned data.

:mod:`fiboki.lifecycle.state` decides whether a move is *legal*. This module
decides whether it is *deserved*, and it does so the way
:mod:`fiboki.validation.gates` does: a criterion is a row -- a name, a metric, a
comparison, a number and the reason the number is what it is -- not an ``if``
statement somewhere in a promotion script. The whole rule set carries a version
and a fingerprint, so a reader two years from now can tell whether a strategy
cleared today's bar or a softer one.

The criteria are :class:`~fiboki.validation.gates.Gate` objects, deliberately.
Two threshold vocabularies would drift apart, and the failure mode of drift is
always in the same direction: whichever set is easier becomes the one that gets
quoted. Reusing ``Gate`` also inherits the property that matters most --
:class:`~fiboki.validation.gates.GateStatus.NOT_EVALUATED` **blocks**, so a
criterion whose input nobody computed is not a criterion that passed.

The ``rung`` field on each gate is ``-1``: these gates are not produced by a
ladder rung, and claiming a rung number they do not have would make the report
lie about where the evidence came from.

Where the numbers come from
---------------------------
The forward stages restate ``docs/v2/DEPLOYMENT.md`` §10, which is itself carried
forward from the V1 audit:

* **Gate A, research to paper** -- every gate in ``PRODUCTION_GATE_SET`` (``v2.1.0-calibrated``
  since 2026-10-08), plus an
  automated check that live execution is disabled. Here: ``VALIDATING ->
  CANDIDATE`` (the report) and ``CANDIDATE -> PAPER`` (the config check).
* **Gate B, paper to broker demo** -- 30 days of continuous paper with no
  unexplained heartbeat gaps, a proven worker-kill alert, and a week of clean
  reconciliation. Here: ``PAPER -> SHADOW`` and ``SHADOW -> DEMO``.
* **Gate C, demo to small live** -- 3 months and 100 trades, paper Sharpe inside
  the 90% block-bootstrap CI of the backtest Sharpe, realised costs within 1.5x
  of modelled, zero unreconciled fills. Here: ``DEMO -> APPROVED``.
* **The stopping rules** -- pre-registered *before* any live capital. Here:
  ``APPROVED -> LIVE`` requires all three registrations to already exist, so the
  ordering is enforced rather than remembered.

Trade counts at the forward stages are lower than the 400 the research gate set
demands, and that is not a relaxation: 400 is the count at which a small effect
is separable from noise *across a 23,040-cell search*, whereas a forward stage is
a single pre-specified strategy being checked for agreement with an expectation
that was fixed beforehand. The forward counts are chosen for the elapsed-time
requirement they accompany, and they are the weakest link in this file --
40 paper trades cannot separate a Sharpe of 1.0 from one of 0.5 and nothing here
pretends otherwise. See ``VALIDATION_STANDARD.md`` §6 on minimum track record
length: the honest statement is that the forward stages detect *gross* divergence
and cost surprises, not subtle decay. Subtle decay is what
:mod:`fiboki.lifecycle.stopping_rules` is for.

Reaching LIVE
-------------
``APPROVED -> LIVE`` sets ``requires_human_authorisation``. That is the second of
two independent controls; the first is in
:meth:`fiboki.lifecycle.state.LifecycleStateMachine.transition`, which refuses any
transition into LIVE by a non-human actor without consulting this file at all.
Neither control can be satisfied by an automated rule, and
``tests/unit/test_lifecycle_promotion.py`` parametrises over every automated path
to show it.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fiboki.core.enums import StrategyLifecycle
from fiboki.lifecycle.state import (
    LADDER,
    Evidence,
    EvidenceKind,
    HumanAuthorisation,
    LifecycleTransition,
)
from fiboki.validation.gates import (
    PRODUCTION_GATE_SET,
    Comparison,
    Gate,
    GateResult,
    GateSet,
    GateStatus,
)
from fiboki.validation.report import ValidationReport

__all__ = [
    "PROMOTION_RULES_V1",
    "PromotionDecision",
    "PromotionEvidence",
    "PromotionRule",
    "PromotionRuleSet",
    "UnknownPromotion",
    "evaluate_promotion",
]

_NOT_A_RUNG = -1
"""``Gate.rung`` for a criterion no ladder rung produced."""


class UnknownPromotion(LookupError):
    """No rule exists for this (from, to) pair."""


def _g(
    name: str,
    metric: str,
    comparison: Comparison,
    threshold: float,
    rationale: str,
    *,
    boolean: bool = False,
    units: str = "",
) -> Gate:
    return Gate(
        name=name,
        metric=metric,
        comparison=comparison,
        threshold=threshold,
        rung=_NOT_A_RUNG,
        rationale=rationale,
        boolean=boolean,
        units=units,
    )


# ==========================================================================
# Inputs
# ==========================================================================


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    """What a caller supplies so a promotion can be judged.

    ``metrics`` is a flat mapping of metric name to value. A metric that is
    absent, ``None`` or non-finite makes its gate ``NOT_EVALUATED``, which blocks
    -- deliberately, because the alternative is a promotion granted because
    nobody measured the thing that would have refused it.
    """

    metrics: Mapping[str, float | None] = field(default_factory=dict)
    validation_report: ValidationReport | None = field(default=None, repr=False)
    holdout_consumption_count: int | None = None
    """How many times this strategy content hash has consumed the holdout.
    ``None`` means nobody asked the registry, which blocks. Exactly ``1`` passes."""
    human_authorisation: HumanAuthorisation | None = None
    not_applicable: frozenset[str] = frozenset()
    """Gate NAMES that genuinely cannot apply. Never a way to hide a missing
    number -- that is what ``NOT_EVALUATED`` is for."""

    def metric(self, name: str) -> float | None:
        value = self.metrics.get(name)
        return None if value is None else float(value)


# ==========================================================================
# Rules
# ==========================================================================


@dataclass(frozen=True, slots=True)
class PromotionRule:
    """Everything one transition demands, numeric and structural."""

    from_state: StrategyLifecycle
    to_state: StrategyLifecycle
    gate_set: GateSet
    requires_validation_report: bool = False
    """A promotable :class:`ValidationReport` produced under the PRODUCTION gate
    set. A report produced under an overridden, softer gate set does not count,
    and the fingerprint is what catches that."""
    requires_holdout_consumed_once: bool = False
    requires_human_authorisation: bool = False
    rationale: str = ""

    @property
    def key(self) -> tuple[StrategyLifecycle, StrategyLifecycle]:
        return (self.from_state, self.to_state)

    @property
    def label(self) -> str:
        return f"{self.from_state.value}->{self.to_state.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_state.value,
            "to": self.to_state.value,
            "rationale": self.rationale,
            "requires_validation_report": self.requires_validation_report,
            "requires_holdout_consumed_once": self.requires_holdout_consumed_once,
            "requires_human_authorisation": self.requires_human_authorisation,
            "gate_set": self.gate_set.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PromotionRuleSet:
    """A versioned collection of promotion rules, fingerprinted as one."""

    version: str
    rules: tuple[PromotionRule, ...]
    description: str = ""

    def __post_init__(self) -> None:
        keys = [r.key for r in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("a PromotionRuleSet may hold at most one rule per transition")

    def rule(
        self, from_state: StrategyLifecycle, to_state: StrategyLifecycle
    ) -> PromotionRule:
        for rule in self.rules:
            if rule.key == (from_state, to_state):
                return rule
        raise UnknownPromotion(
            f"no promotion rule for {from_state.value} -> {to_state.value}"
        )

    def has_rule(
        self, from_state: StrategyLifecycle, to_state: StrategyLifecycle
    ) -> bool:
        return any(r.key == (from_state, to_state) for r in self.rules)

    def fingerprint(self) -> str:
        blob = json.dumps(
            {"version": self.version, "rules": [r.to_dict() for r in self.rules]},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "fingerprint": self.fingerprint(),
            "rules": [r.to_dict() for r in self.rules],
        }

    def covers_every_promotion(
        self, transitions: Sequence[LifecycleTransition]
    ) -> tuple[str, ...]:
        """Promotion edges with no rule. A promotion nobody wrote criteria for."""
        missing = []
        for t in transitions:
            if t.kind.value != "promotion":
                continue
            if not self.has_rule(t.from_state, t.to_state):
                missing.append(f"{t.from_state.value}->{t.to_state.value}")
        return tuple(sorted(missing))


# ==========================================================================
# Decision
# ==========================================================================


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Whether the criteria were met, and the ONE thing that stopped it if not."""

    rule: PromotionRule
    ruleset_version: str
    ruleset_fingerprint: str
    gate_results: tuple[GateResult, ...]
    structural_failures: tuple[str, ...]
    evaluated_at: datetime

    @property
    def allowed(self) -> bool:
        return not self.structural_failures and not self.failed_gates

    @property
    def failed_gates(self) -> tuple[GateResult, ...]:
        return tuple(g for g in self.gate_results if g.status.blocks_promotion)

    @property
    def binding_constraint(self) -> str:
        """The first thing to fix. Structural failures come first: a missing
        ValidationReport is not a threshold a researcher can nudge."""
        if self.structural_failures:
            return self.structural_failures[0]
        blocking = GateSet.binding_constraint(self.gate_results)
        return "" if blocking is None else blocking.describe()

    def describe(self) -> str:
        head = f"{self.rule.label} under {self.ruleset_version}"
        if self.allowed:
            return f"{head}: ALLOWED ({len(self.gate_results)} criteria passed)"
        return f"{head}: REFUSED -- {self.binding_constraint}"

    def as_evidence(self) -> Evidence:
        """This decision, as a citable piece of transition evidence."""
        return Evidence(
            kind=EvidenceKind.PROMOTION_EVALUATION,
            id=f"promotion:{self.rule.label}:{self.ruleset_fingerprint[:12]}",
            summary=self.describe(),
            detail=self.to_dict(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition": self.rule.label,
            "ruleset_version": self.ruleset_version,
            "ruleset_fingerprint": self.ruleset_fingerprint,
            "allowed": self.allowed,
            "evaluated_at": self.evaluated_at.astimezone(UTC).isoformat(),
            "structural_failures": list(self.structural_failures),
            "binding_constraint": self.binding_constraint,
            "gate_results": [g.to_dict() for g in self.gate_results],
        }


def evaluate_promotion(
    ruleset: PromotionRuleSet,
    from_state: StrategyLifecycle,
    to_state: StrategyLifecycle,
    evidence: PromotionEvidence,
    *,
    strategy_content_hash: str = "",
    now: datetime | None = None,
) -> PromotionDecision:
    """Judge one promotion against its versioned criteria.

    Structural requirements are evaluated first and reported separately from the
    gates, because they are a different kind of failure: a gate says "not good
    enough yet", a structural failure says "the evidence this promotion is
    supposed to rest on does not exist".
    """
    rule = ruleset.rule(from_state, to_state)
    failures: list[str] = []

    if rule.requires_validation_report:
        failures.extend(_check_report(rule, evidence))

    if rule.requires_holdout_consumed_once:
        count = evidence.holdout_consumption_count
        if count is None:
            failures.append(
                "holdout consumption was never checked: ask HoldoutRegistry "
                "whether this content hash has consumed the holdout, rather than "
                "assuming it has"
            )
        elif count == 0:
            failures.append(
                "the holdout has not been consumed: the final 20% is what makes "
                "the verdict out-of-sample, and a promotion without it is a "
                "promotion on the selection sample"
            )
        elif count > 1:
            failures.append(
                f"the holdout was consumed {count} times; it may be consumed "
                "exactly once. More than one look is selection, not validation"
            )

    if rule.requires_human_authorisation:
        auth = evidence.human_authorisation
        if auth is None:
            failures.append(
                f"{rule.label} requires a written human authorisation; none supplied"
            )
        elif strategy_content_hash and not auth.matches(strategy_content_hash, to_state):
            failures.append(
                "the supplied human authorisation names "
                f"{auth.strategy_content_hash[:12]} -> {auth.to_state.value}, not "
                f"{strategy_content_hash[:12]} -> {to_state.value}"
            )

    results = rule.gate_set.evaluate(
        evidence.metrics, not_applicable=frozenset(evidence.not_applicable)
    )
    return PromotionDecision(
        rule=rule,
        ruleset_version=ruleset.version,
        ruleset_fingerprint=ruleset.fingerprint(),
        gate_results=results,
        structural_failures=tuple(failures),
        evaluated_at=now or datetime.now(tz=UTC),
    )


def _check_report(rule: PromotionRule, evidence: PromotionEvidence) -> list[str]:
    report = evidence.validation_report
    if report is None:
        return [
            f"{rule.label} requires a ValidationReport and none was supplied; "
            "a promotion on a remembered result is a promotion on a rumour"
        ]
    out: list[str] = []
    if not report.verdict.promotable:
        out.append(
            f"the ValidationReport verdict is {report.verdict.value.upper()}: "
            f"{report.binding_constraint.describe()}"
        )
    production = PRODUCTION_GATE_SET.fingerprint()
    if report.gate_set_fingerprint != production:
        out.append(
            "the ValidationReport was produced under gate set "
            f"{report.gate_set_version or 'unknown'} "
            f"({report.gate_set_fingerprint[:12] or 'none'}), not the production "
            f"set {PRODUCTION_GATE_SET.version} ({production[:12]}). A report that "
            "cleared a softer bar has not cleared this one"
        )
    blocking = [g for g in report.gate_results if g.status is GateStatus.NOT_EVALUATED]
    if blocking:
        out.append(
            "the ValidationReport has unevaluated gates "
            f"({', '.join(g.gate.name for g in blocking)}); a gate nobody ran is "
            "not a gate that passed"
        )
    return out


# ==========================================================================
# The rule set
# ==========================================================================

_DISCOVERY_TO_RESEARCH = PromotionRule(
    from_state=StrategyLifecycle.DISCOVERY,
    to_state=StrategyLifecycle.RESEARCH,
    rationale=(
        "The cheapest screen there is: does this idea deserve any compute? Both "
        "criteria are properties of the DOCUMENT, so they cost nothing to check "
        "and they refuse the two things that waste the most research time -- an "
        "idea nobody has argued against, and a reparameterisation wearing a new "
        "name."
    ),
    gate_set=GateSet(
        version="lifecycle_discovery_research_v1",
        description="cheap document screens before any compute is spent",
        gates=(
            _g(
                "hypothesis_states_evidence_against",
                "hypothesis_states_evidence_against",
                Comparison.GTE,
                1.0,
                "STRATEGY_STANDARD.md: a hypothesis must carry the published "
                "evidence AGAINST the idea. A document that only argues for "
                "itself has not been thought about, it has been advocated.",
                boolean=True,
            ),
            _g(
                "structurally_novel",
                "structurally_novel",
                Comparison.GTE,
                1.0,
                "research/structure.is_reparameterisation answers this "
                "mechanically. Every strategy added raises the statistical bar "
                "for all the others, so a reparameterisation must be justified "
                "as a sweep of an existing document, not admitted as a new one.",
                boolean=True,
            ),
        ),
    ),
)

_RESEARCH_TO_VALIDATING = PromotionRule(
    from_state=StrategyLifecycle.RESEARCH,
    to_state=StrategyLifecycle.VALIDATING,
    rationale=(
        "A second cheap screen, this time on a quick in-sample run. Its only job "
        "is to avoid spending a full ladder -- CPCV, SPA, stress, a holdout look "
        "that can never be taken back -- on something that is not even "
        "in-sample profitable. Passing this proves NOTHING about the edge."
    ),
    gate_set=GateSet(
        version="lifecycle_research_validating_v1",
        description="cheap in-sample screens before the ladder is run",
        gates=(
            _g(
                "screen_trades",
                "in_sample_trades",
                Comparison.GTE,
                100.0,
                "A screen, not the bar. The bar is 400 at rung 0 of the ladder; "
                "100 is merely enough for the screen's own arithmetic to mean "
                "anything.",
                units="trades",
            ),
            _g(
                "screen_sharpe",
                "in_sample_sharpe",
                Comparison.GTE,
                0.30,
                "In-sample, un-deflated, and therefore worthless as evidence. "
                "Below 0.30 in-sample there is nothing for deflation to survive.",
            ),
            _g(
                "parameter_count",
                "parameter_count",
                Comparison.LTE,
                8.0,
                "Each free parameter multiplies the search and the deflation "
                "penalty. Beyond eight the honest trial count grows faster than "
                "any plausible edge.",
                units="parameters",
            ),
        ),
    ),
)

_VALIDATING_TO_CANDIDATE = PromotionRule(
    from_state=StrategyLifecycle.VALIDATING,
    to_state=StrategyLifecycle.CANDIDATE,
    requires_validation_report=True,
    requires_holdout_consumed_once=True,
    rationale=(
        "Gate A. The only transition in this file whose evidence is a "
        "ValidationReport under the production gate set, with the holdout "
        "consumed exactly once. Everything above this line is a screen; "
        "everything below it is forward observation. This is where the "
        "statistics happen."
    ),
    gate_set=GateSet(
        version="lifecycle_validating_candidate_v1",
        description="declared search size, on top of the full production gate set",
        gates=(
            _g(
                "external_trial_count_declared",
                "external_trial_count",
                Comparison.GTE,
                1.0,
                "The ladder can see this strategy's own sweep; it cannot see "
                "that the campaign also searched eleven others over sixty "
                "instruments. Leaving the external count at zero deflates "
                "against a floor. Requiring it to be non-zero does not make it "
                "honest -- nothing can -- but it refuses the default.",
                units="trials",
            ),
        ),
    ),
)

_CANDIDATE_TO_PAPER = PromotionRule(
    from_state=StrategyLifecycle.CANDIDATE,
    to_state=StrategyLifecycle.PAPER,
    rationale=(
        "The second half of Gate A: the automated configuration check. The "
        "strategy has earned a paper allocation; this transition checks that the "
        "machine it is about to run on cannot reach a live venue."
    ),
    gate_set=GateSet(
        version="lifecycle_candidate_paper_v1",
        description="configuration checks before a strategy runs anywhere",
        gates=(
            _g(
                "live_execution_disabled",
                "live_execution_disabled",
                Comparison.GTE,
                1.0,
                "DEPLOYMENT.md Gate A. V1 shipped a live-execution flag in "
                "committed config and it sat there for months. The check is "
                "automated because the failure mode is that nobody looks.",
                boolean=True,
            ),
            _g(
                "risk_limits_reviewed",
                "risk_limits_reviewed",
                Comparison.GTE,
                1.0,
                "The LimitSet this strategy will trade under has been read by a "
                "human. A paper bot under nobody's risk configuration produces a "
                "track record nobody can interpret.",
                boolean=True,
            ),
            _g(
                "stopping_rules_pre_registered",
                "stopping_rules_pre_registered",
                Comparison.GTE,
                3.0,
                "All three pre-registered stopping rules must exist BEFORE the "
                "strategy produces its first forward observation, not before it "
                "reaches live. A rule calibrated after seeing the forward data "
                "is not pre-registered, and paper data is forward data.",
                units="rules",
            ),
        ),
    ),
)

_FORWARD_AGREEMENT = (
    _g(
        "forward_sharpe_agreement",
        "forward_to_backtest_sharpe_ratio",
        Comparison.GTE,
        0.50,
        "Observed forward Sharpe as a fraction of the backtested Sharpe. Half is "
        "the same benchmark the PSR stopping rule halts against, so promotion "
        "and demotion are measured against ONE number rather than two that can "
        "drift apart. Below half, the forward record is not the strategy the "
        "backtest described.",
        units="ratio",
    ),
    _g(
        "no_divergent_dimensions",
        "divergence_dimensions_flagged",
        Comparison.LTE,
        0.0,
        "DivergenceReport dimensions that diverged at the configured "
        "confidence. Promoting a strategy whose spread, slippage, latency or "
        "regime behaviour already disagrees with expectation promotes the "
        "disagreement too.",
        units="dimensions",
    ),
)

_PAPER_TO_SHADOW = PromotionRule(
    from_state=StrategyLifecycle.PAPER,
    to_state=StrategyLifecycle.SHADOW,
    rationale=(
        "Gate B, first half. Thirty days of continuous paper running with no "
        "unexplained heartbeat gaps. The elapsed time is the point: a gap in the "
        "record is indistinguishable from a flat strategy, and thirty days is "
        "long enough for the operational failures to show up."
    ),
    gate_set=GateSet(
        version="lifecycle_paper_shadow_v1",
        description="Gate B first half: continuous paper operation",
        gates=(
            _g(
                "min_days_in_paper",
                "days_in_state",
                Comparison.GTE,
                30.0,
                "DEPLOYMENT.md Gate B: at least 30 days of continuous paper "
                "running. Calendar time, not trading time -- weekend and "
                "holiday behaviour is part of what is being observed.",
                units="days",
            ),
            _g(
                "min_paper_trades",
                "forward_trades",
                Comparison.GTE,
                40.0,
                "Enough to measure spread, slippage and fill quality with a "
                "usable median. NOT enough to measure a Sharpe: see "
                "VALIDATION_STANDARD.md §6, where separating a Sharpe of 1.0 "
                "from 0.5 takes years. This count buys cost realism, not "
                "performance confirmation.",
                units="trades",
            ),
            _g(
                "max_heartbeat_gap",
                "max_unexplained_heartbeat_gap_hours",
                Comparison.LTE,
                1.0,
                "An unnoticed dead worker is indistinguishable from a strategy "
                "with no signals. One hour is one poll interval's grace.",
                units="hours",
            ),
            *_FORWARD_AGREEMENT,
        ),
    ),
)

_SHADOW_TO_DEMO = PromotionRule(
    from_state=StrategyLifecycle.SHADOW,
    to_state=StrategyLifecycle.DEMO,
    rationale=(
        "Gate B, second half. Shadow mode touches the broker without placing "
        "risk, so this is where reconciliation and the alerting path are proved. "
        "The chaos test is a hard requirement: until a deliberate worker kill "
        "has produced an alert, demo promotion is not safe."
    ),
    gate_set=GateSet(
        version="lifecycle_shadow_demo_v1",
        description="Gate B second half: reconciliation and proven alerting",
        gates=(
            _g(
                "min_days_in_shadow",
                "days_in_state",
                Comparison.GTE,
                14.0,
                "Two weeks against the real venue's quotes and session "
                "boundaries, which is where the calendar assumptions break.",
                units="days",
            ),
            _g(
                "min_shadow_trades",
                "forward_trades",
                Comparison.GTE,
                20.0,
                "Enough shadow intents to compare against the paper fills the "
                "same signals produced.",
                units="trades",
            ),
            _g(
                "worker_kill_alert_proven",
                "worker_kill_alert_proven",
                Comparison.GTE,
                1.0,
                "DEPLOYMENT.md Gate B, stated in the source as a blocker: a "
                "deliberate worker kill in staging must produce both an error "
                "report and an alert within one poll interval.",
                boolean=True,
            ),
            _g(
                "reconciliation_clean_days",
                "reconciliation_clean_days",
                Comparison.GTE,
                7.0,
                "A full week clean on the broker-reference key. Reconciliation "
                "divergence is the one failure that silently makes the ledger "
                "and the broker two different accounts.",
                units="days",
            ),
            *_FORWARD_AGREEMENT,
        ),
    ),
)

_DEMO_TO_APPROVED = PromotionRule(
    from_state=StrategyLifecycle.DEMO,
    to_state=StrategyLifecycle.APPROVED,
    rationale=(
        "Gate C's evidence, gathered. APPROVED means 'everything Gate C asks for "
        "has been demonstrated' and nothing more -- it is deliberately a "
        "separate state from LIVE so that the evidence review and the decision "
        "to risk money are two acts on the record rather than one."
    ),
    gate_set=GateSet(
        version="lifecycle_demo_approved_v1",
        description="Gate C: three months on demo, costs within 1.5x of modelled",
        gates=(
            _g(
                "min_days_in_demo",
                "days_in_state",
                Comparison.GTE,
                90.0,
                "DEPLOYMENT.md Gate C: at least 3 months. Long enough to cross "
                "at least one regime boundary and one quarter-end.",
                units="days",
            ),
            _g(
                "min_demo_trades",
                "forward_trades",
                Comparison.GTE,
                100.0,
                "Gate C's live-equivalent trade count.",
                units="trades",
            ),
            _g(
                "sharpe_inside_backtest_ci",
                "sharpe_inside_backtest_bootstrap_ci",
                Comparison.GTE,
                1.0,
                "Demo Sharpe inside the 90% BLOCK-bootstrap confidence interval "
                "of the backtest Sharpe. Block, not iid: trading returns are "
                "serially dependent and an iid interval is too narrow, which "
                "would fail strategies for being normal.",
                boolean=True,
            ),
            _g(
                "realised_cost_ratio",
                "realised_cost_to_modelled_ratio",
                Comparison.LTE,
                1.5,
                "Realised spread and slippage within 1.5x of modelled. Above "
                "that the stored expectancy for this instrument is overstated "
                "by a known amount and must be re-run, not accepted.",
                units="ratio",
            ),
            _g(
                "unreconciled_fills",
                "unreconciled_fills",
                Comparison.LTE,
                0.0,
                "Zero. Not 'few'. An unreconciled fill means our ledger and the "
                "broker's disagree about what we own.",
                units="fills",
            ),
            *_FORWARD_AGREEMENT,
        ),
    ),
)

_APPROVED_TO_LIVE = PromotionRule(
    from_state=StrategyLifecycle.APPROVED,
    to_state=StrategyLifecycle.LIVE,
    requires_human_authorisation=True,
    rationale=(
        "The decision to risk money. Every numeric criterion here was already "
        "demonstrated at DEMO -> APPROVED; what this transition adds is the "
        "rehearsal of the controls that will be needed when it goes wrong, and a "
        "named human who wrote down why. No automated path reaches this state: "
        "the state machine refuses a non-human actor into LIVE without "
        "consulting this file."
    ),
    gate_set=GateSet(
        version="lifecycle_approved_live_v1",
        description="rehearsed controls and written acceptance before real money",
        gates=(
            _g(
                "min_days_in_approved",
                "days_in_state",
                Comparison.GTE,
                14.0,
                "A deliberate cooling-off period between the evidence review and "
                "the decision. Two weeks is arbitrary; that it is non-zero is "
                "not.",
                units="days",
            ),
            _g(
                "kill_switch_drill_completed",
                "kill_switch_drill_completed",
                Comparison.GTE,
                1.0,
                "DEPLOYMENT.md Gate C: a kill-switch drill executed and timed. "
                "An untested kill switch is a belief about a kill switch.",
                boolean=True,
            ),
            _g(
                "approximations_accepted_in_writing",
                "approximations_accepted_in_writing",
                Comparison.GTE,
                1.0,
                "Every documented approximation -- USD->GBP conversion, static "
                "spreads, zero default slippage, no overnight financing -- "
                "either closed or explicitly accepted in writing. Accepting "
                "them is allowed; not knowing about them is not.",
                boolean=True,
            ),
            _g(
                "stopping_rules_pre_registered",
                "stopping_rules_pre_registered",
                Comparison.GTE,
                3.0,
                "All three rules pre-registered and unchanged since paper. "
                "Checking it again here catches a rule re-registered with "
                "kinder parameters after the forward data was seen.",
                units="rules",
            ),
            _g(
                "stopping_rules_registered_before_forward_data",
                "stopping_rules_registered_before_forward_data",
                Comparison.GTE,
                1.0,
                "A rule whose parameters were fixed after the forward returns "
                "were visible is not pre-registered, however early the file says "
                "it was written.",
                boolean=True,
            ),
            *_FORWARD_AGREEMENT,
        ),
    ),
)

PROMOTION_RULES_V1 = PromotionRuleSet(
    version="lifecycle_promotion_v1",
    description=(
        "Fiboki V2 promotion criteria, one rule per ladder edge. Changing ANY "
        "number here requires a new version string, because decisions carry the "
        "version and a reader relies on it meaning one fixed thing."
    ),
    rules=(
        _DISCOVERY_TO_RESEARCH,
        _RESEARCH_TO_VALIDATING,
        _VALIDATING_TO_CANDIDATE,
        _CANDIDATE_TO_PAPER,
        _PAPER_TO_SHADOW,
        _SHADOW_TO_DEMO,
        _DEMO_TO_APPROVED,
        _APPROVED_TO_LIVE,
    ),
)


def _self_check() -> None:
    """Import-time assertion: exactly one rule per adjacent ladder edge.

    A promotion edge the state machine allows and nothing judges is precisely
    the gap this package was written to close, so it fails at import rather than
    quietly in production.
    """
    expected = set(itertools.pairwise(LADDER))
    actual = {r.key for r in PROMOTION_RULES_V1.rules}
    if actual != expected:
        missing = sorted(f"{a.value}->{b.value}" for a, b in expected - actual)
        extra = sorted(f"{a.value}->{b.value}" for a, b in actual - expected)
        raise AssertionError(
            f"PROMOTION_RULES_V1 must carry one rule per ladder edge; "
            f"missing={missing} unexpected={extra}"
        )


_self_check()
