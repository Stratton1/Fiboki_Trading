"""The service the live worker calls, and the state the API and gateway read.

Everything else in this package is a pure function over data. This is the object
that owns the wiring: on each tick it compares expectation against observation,
evaluates the pre-registered stopping rules, folds both into a degradation score,
applies the automatic demotion that follows, raises the alerts, and leaves a
queryable current state behind.

The asymmetry that runs through the whole thing
------------------------------------------------
**This service can demote and cannot promote.** :meth:`evaluate` moves strategies
down -- into WATCH, DEGRADED and QUARANTINED -- automatically, under an
``AUTOMATED_RULE`` actor. :meth:`promote` exists, but it refuses any actor that
is not a named human for every stage from PAPER upwards, and the state machine
refuses LIVE to a non-human regardless of what this file does. A monitor that
could put risk back on is not a monitor.

What this service deliberately does NOT do
-------------------------------------------
* **It does not import** :mod:`fiboki.risk` **or** :mod:`fiboki.portfolio`. The
  risk gateway consumes a ``StrategyView`` that its caller assembles; this
  service supplies the three fields that view needs through
  :meth:`status`, and the live worker joins them. Importing the gateway here
  would put an edge into the dependency graph pointing the wrong way and would
  make the gateway's lifecycle check testable only through this service.
* **It does not fetch anything.** Expectations and observations are handed in.
  The service reads a clock only when a caller omits a timestamp.
* **It does not start a thread.** A monitor that schedules itself inside the API
  process is how V1 ended up with heartbeat freshness computed when a human
  loaded a page. The worker owns the timer; this owns the evaluation.

Three alert events, not one severity dial
-----------------------------------------
:class:`fiboki.obs.alerts.AlertEvent` used to carry only ``STRATEGY_DEGRADED``,
so every alert raised here was that event with the severity carrying the whole
difference. A channel filtering on the event could not tell "this is drifting"
from "a pre-registered rule has taken it out of service". ``obs/alerts.py`` now
has ``STRATEGY_HALTED`` and ``STRATEGY_QUARANTINED`` as well, and this service
routes to them:

============================  ==========================  =========
Occasion                      Event                       Severity
============================  ==========================  =========
divergence, no demotion       ``STRATEGY_DEGRADED``       WARNING
automatic demotion            ``STRATEGY_DEGRADED``       ERROR
demotion to QUARANTINED       ``STRATEGY_QUARANTINED``    ERROR
a stopping rule fired         ``STRATEGY_HALTED``         CRITICAL
============================  ==========================  =========
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fiboki.core.enums import StrategyLifecycle
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
    DivergenceReport,
    Expectation,
    ForwardMonitor,
    Observation,
)
from fiboki.lifecycle.promotion import (
    PROMOTION_RULES_V1,
    PromotionDecision,
    PromotionEvidence,
    PromotionRuleSet,
    evaluate_promotion,
)
from fiboki.lifecycle.state import (
    RUNNING_STATES,
    Actor,
    Evidence,
    EvidenceKind,
    LifecycleError,
    LifecycleStateMachine,
    StrategyRecord,
    TransitionRecord,
)
from fiboki.lifecycle.stopping_rules import (
    ALL_RULE_KINDS,
    HaltRegistry,
    InMemoryHaltJournal,
    InMemoryPreRegistrationStore,
    PreRegistrationStore,
    RuleEvaluation,
    RuleObservation,
    RuleRegistration,
    StoppingRuleKind,
    build_rule,
)
from fiboki.obs.alerts import Alert, AlertDispatcher, AlertEvent, Severity

__all__ = [
    "DEGRADATION_ACTOR",
    "LifecycleEvaluation",
    "LifecycleService",
    "PromotionRefused",
    "StrategyStatus",
    "divergence_evidence",
]

DEGRADATION_ACTOR = Actor.rule("lifecycle:degradation")
"""The named actor for an automatic demotion driven by the degradation score.
Automated rules must name themselves; "the system" is not an actor."""


class PromotionRefused(LifecycleError):
    """The promotion criteria were not met. Carries the decision that refused."""

    def __init__(self, decision: PromotionDecision) -> None:
        self.decision = decision
        super().__init__(decision.describe())


def divergence_evidence(report: DivergenceReport) -> Evidence:
    """A :class:`DivergenceReport` as citable transition evidence."""
    return Evidence(
        kind=EvidenceKind.MONITOR_RESULT,
        id=(
            f"divergence:{report.strategy_content_hash[:12]}"
            f"@{report.at.astimezone(UTC).isoformat()}"
        ),
        summary=report.summary(),
        detail=report.to_dict(),
    )


# ==========================================================================
# Outputs
# ==========================================================================


@dataclass(frozen=True, slots=True)
class LifecycleEvaluation:
    """Everything one tick concluded about one strategy."""

    strategy_content_hash: str
    at: datetime
    state_before: StrategyLifecycle
    state_after: StrategyLifecycle
    divergence: DivergenceReport | None
    rule_evaluations: tuple[RuleEvaluation, ...]
    degradation: DegradationScore
    verdict: DegradationVerdict | None
    transitions: tuple[TransitionRecord, ...] = ()
    alerts: tuple[Alert, ...] = ()
    latched_halts: tuple[StoppingRuleKind, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def demoted(self) -> bool:
        return self.state_after is not self.state_before

    @property
    def fired_rules(self) -> tuple[RuleEvaluation, ...]:
        return tuple(e for e in self.rule_evaluations if e.fired)

    def summary(self) -> str:
        head = f"{self.strategy_content_hash[:12]} {self.state_before.value}"
        if self.demoted:
            head += f" -> {self.state_after.value}"
        fired = ", ".join(e.kind.value for e in self.fired_rules)
        tail = f"; rules fired: {fired}" if fired else ""
        div = "" if self.divergence is None else f"; {self.divergence.summary()}"
        return f"{head} [{self.degradation.describe()}]{tail}{div}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_content_hash": self.strategy_content_hash,
            "at": self.at.astimezone(UTC).isoformat(),
            "state_before": self.state_before.value,
            "state_after": self.state_after.value,
            "demoted": self.demoted,
            "divergence": None if self.divergence is None else self.divergence.to_dict(),
            "rule_evaluations": [e.to_dict() for e in self.rule_evaluations],
            "degradation": self.degradation.to_dict(),
            "verdict": None if self.verdict is None else self.verdict.to_dict(),
            "transitions": [t.to_dict() for t in self.transitions],
            "alerts": [a.to_dict() for a in self.alerts],
            "latched_halts": [k.value for k in self.latched_halts],
            "notes": list(self.notes),
            "summary": self.summary(),
        }


@dataclass(frozen=True, slots=True)
class StrategyStatus:
    """The queryable current state. What the API renders and the worker joins.

    ``lifecycle``, ``health`` and ``degraded`` are exactly the three fields
    :class:`fiboki.risk.gateway.StrategyView` needs. The worker builds the view;
    this package does not import the gateway.
    """

    strategy_id: str
    strategy_content_hash: str
    lifecycle: StrategyLifecycle
    health: float
    degraded: bool
    band: DegradationBand
    entered_state_at: datetime
    latched_halts: tuple[StoppingRuleKind, ...] = ()
    missing_rule_registrations: tuple[StoppingRuleKind, ...] = ()
    last_evaluated_at: datetime | None = None
    last_score: float | None = None
    last_confidence: float | None = None
    n_transitions: int = 0

    @property
    def ever_evaluated(self) -> bool:
        return self.last_evaluated_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_content_hash": self.strategy_content_hash,
            "lifecycle": self.lifecycle.value,
            "health": self.health,
            "degraded": self.degraded,
            "band": self.band.value,
            "entered_state_at": self.entered_state_at.astimezone(UTC).isoformat(),
            "latched_halts": [k.value for k in self.latched_halts],
            "missing_rule_registrations": [
                k.value for k in self.missing_rule_registrations
            ],
            "last_evaluated_at": (
                None
                if self.last_evaluated_at is None
                else self.last_evaluated_at.astimezone(UTC).isoformat()
            ),
            "last_score": self.last_score,
            "last_confidence": self.last_confidence,
            "ever_evaluated": self.ever_evaluated,
            "n_transitions": int(self.n_transitions),
        }


# ==========================================================================
# The service
# ==========================================================================


class LifecycleService:
    """Ties the state machine, the monitors, the rules and the alerts together."""

    def __init__(
        self,
        *,
        machine: LifecycleStateMachine | None = None,
        registrations: PreRegistrationStore | None = None,
        halts: HaltRegistry | None = None,
        dispatcher: AlertDispatcher | None = None,
        monitor: ForwardMonitor | None = None,
        degradation_config: DegradationConfig = DEGRADATION_CONFIG_V1,
        promotion_rules: PromotionRuleSet = PROMOTION_RULES_V1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.machine = machine or LifecycleStateMachine()
        self.registrations = registrations or InMemoryPreRegistrationStore()
        self.halts = halts or HaltRegistry(InMemoryHaltJournal())
        self.dispatcher = dispatcher
        self.monitor = monitor or ForwardMonitor()
        self.degradation_config = degradation_config
        self.promotion_rules = promotion_rules
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self._trackers: dict[str, DegradationTracker] = {}
        self._last: dict[str, LifecycleEvaluation] = {}

    # ---------------------------------------------------------- lifecycle

    def register_strategy(
        self,
        *,
        strategy_id: str,
        strategy_content_hash: str,
        actor: Actor,
        reason: str,
        at: datetime | None = None,
    ) -> StrategyRecord:
        return self.machine.register(
            strategy_id=strategy_id,
            strategy_content_hash=strategy_content_hash,
            actor=actor,
            reason=reason,
            at=at or self.clock(),
        )

    def pre_register_rules(
        self,
        strategy_content_hash: str,
        *,
        parameters: Mapping[StoppingRuleKind, Any],
        registered_by: str,
        reason: str,
        at: datetime | None = None,
    ) -> dict[StoppingRuleKind, RuleRegistration]:
        """Register the stopping rules. Refuses a partial set.

        All three or none: a strategy carrying a drawdown limit and no CUSUM is
        protected against the failure mode it would have noticed anyway and blind
        to the one it would not.
        """
        missing = [k for k in ALL_RULE_KINDS if k not in parameters]
        if missing:
            raise LifecycleError(
                "all three stopping rules must be registered together; missing "
                + ", ".join(k.value for k in missing)
            )
        when = at or self.clock()
        return {
            kind: self.registrations.register(
                strategy_content_hash=strategy_content_hash,
                kind=kind,
                parameters=parameters[kind],
                registered_by=registered_by,
                reason=reason,
                at=when,
            )
            for kind in ALL_RULE_KINDS
        }

    def promote(
        self,
        strategy_content_hash: str,
        to_state: StrategyLifecycle,
        *,
        actor: Actor,
        reason: str,
        evidence: PromotionEvidence,
        extra_evidence: Sequence[Evidence] = (),
        at: datetime | None = None,
    ) -> StrategyRecord:
        """Evaluate the promotion criteria and, if met, make the transition.

        Raises :class:`PromotionRefused` carrying the decision, so the caller gets
        the binding constraint rather than a boolean.
        """
        record = self.machine.record(strategy_content_hash)
        decision = evaluate_promotion(
            self.promotion_rules,
            record.state,
            to_state,
            evidence,
            strategy_content_hash=strategy_content_hash,
            now=at or self.clock(),
        )
        if not decision.allowed:
            raise PromotionRefused(decision)
        items = [decision.as_evidence(), *extra_evidence]
        if evidence.validation_report is not None:
            items.append(
                Evidence(
                    kind=EvidenceKind.VALIDATION_REPORT,
                    id=evidence.validation_report.content_hash(),
                    summary=evidence.validation_report.summary(),
                )
            )
        if evidence.human_authorisation is not None:
            items.append(evidence.human_authorisation.as_evidence())
        return self.machine.transition(
            strategy_content_hash,
            to_state,
            actor=actor,
            reason=reason,
            evidence=items,
            at=at or self.clock(),
        )

    def release_halt(
        self,
        strategy_content_hash: str,
        kind: StoppingRuleKind,
        *,
        operator: str,
        reason: str,
        at: datetime | None = None,
    ) -> None:
        """Explicit operator reversal of one latched halt.

        Clearing the latch does NOT move the strategy back up the ladder: that is
        a separate transition, by a named human, on the record. Two acts, two
        rows, because releasing the halt and putting the risk back on are two
        different decisions.
        """
        self.halts.release(
            strategy_content_hash, kind, operator=operator, reason=reason, at=at
        )

    # ------------------------------------------------------------ evaluate

    def evaluate(
        self,
        strategy_content_hash: str,
        *,
        expectation: Expectation | None = None,
        observation: Observation | None = None,
        at: datetime | None = None,
    ) -> LifecycleEvaluation:
        """One monitoring tick. Called by the live worker on its schedule."""
        now = at or (observation.observed_at if observation is not None else self.clock())
        record = self.machine.record(strategy_content_hash)
        state_before = record.state
        notes: list[str] = []
        alerts: list[Alert] = []
        transitions: list[TransitionRecord] = []

        report: DivergenceReport | None = None
        if expectation is not None and observation is not None:
            report = self.monitor.compare(expectation, observation, at=now)
        else:
            notes.append(
                "no expectation/observation pair supplied: the divergence "
                "dimensions were not evaluated, which is not the same as agreeing"
            )

        rule_evaluations = self._evaluate_rules(strategy_content_hash, observation, now)
        missing = self.registrations.missing_rules(strategy_content_hash)
        if missing and state_before in RUNNING_STATES:
            notes.append(
                "a RUNNING strategy has no registration for "
                + ", ".join(k.value for k in missing)
                + ": it is producing forward data that nothing pre-registered "
                "can halt on"
            )

        for evaluation in rule_evaluations:
            if not evaluation.fired:
                continue
            event = self.halts.record_firing(evaluation)
            if event is not None:
                alerts.append(
                    self._alert(
                        AlertEvent.STRATEGY_HALTED,
                        Severity.CRITICAL,
                        f"stopping rule {evaluation.kind.value} fired for "
                        f"{strategy_content_hash[:12]}: {evaluation.describe()}",
                        strategy_content_hash=strategy_content_hash,
                        rule=evaluation.kind.value,
                        registration_id=evaluation.registration_id,
                        statistic=evaluation.statistic,
                        threshold=evaluation.threshold,
                        requires_operator_release=True,
                    )
                )

        latched = self.halts.latched(strategy_content_hash)
        score = score_degradation(
            report,
            rule_evaluations,
            config=self.degradation_config,
            latched_halts=len(latched),
        )
        tracker = self._tracker(record)
        verdict = tracker.observe(score)

        state_after = state_before
        if verdict.changed and verdict.worsened:
            target = verdict.target_state
            if target is not None:
                transition = self._demote(
                    record, target, verdict, report, rule_evaluations, now
                )
                if transition is not None:
                    transitions.append(transition)
                    state_after = target
                    alerts.append(
                        self._alert(
                            (
                                AlertEvent.STRATEGY_QUARANTINED
                                if target is StrategyLifecycle.QUARANTINED
                                else AlertEvent.STRATEGY_DEGRADED
                            ),
                            Severity.ERROR,
                            f"{strategy_content_hash[:12]} demoted "
                            f"{state_before.value} -> {target.value}: {verdict.reason}",
                            strategy_content_hash=strategy_content_hash,
                            from_state=state_before.value,
                            to_state=target.value,
                            score=score.score,
                            confidence=score.confidence,
                            automatic=True,
                        )
                    )
                else:
                    notes.append(
                        f"the degradation score supports {target.value} but "
                        f"{state_before.value} -> {target.value} is not a legal "
                        "transition; the band moved and the lifecycle did not"
                    )
        elif report is not None and report.n_flagged and not verdict.changed:
            alerts.append(
                self._alert(
                    AlertEvent.STRATEGY_DEGRADED,
                    Severity.WARNING,
                    f"{strategy_content_hash[:12]} diverging: {report.summary()}",
                    strategy_content_hash=strategy_content_hash,
                    dimensions=",".join(d.dimension.value for d in report.diverged),
                    score=score.score,
                )
            )

        if verdict.recommended_band is not None:
            notes.append(
                f"the score has supported a recovery to "
                f"{verdict.recommended_band.value} for {verdict.consecutive} "
                "consecutive evaluations; recovery requires an explicit operator "
                "action and was NOT applied"
            )

        result = LifecycleEvaluation(
            strategy_content_hash=strategy_content_hash,
            at=now,
            state_before=state_before,
            state_after=state_after,
            divergence=report,
            rule_evaluations=tuple(rule_evaluations),
            degradation=score,
            verdict=verdict,
            transitions=tuple(transitions),
            alerts=tuple(a for a in alerts if a is not None),
            latched_halts=self.halts.latched(strategy_content_hash),
            notes=tuple(notes),
        )
        self._last[strategy_content_hash] = result
        return result

    def evaluate_all(
        self,
        inputs: Mapping[str, tuple[Expectation, Observation]],
        *,
        at: datetime | None = None,
    ) -> tuple[LifecycleEvaluation, ...]:
        """Evaluate every RUNNING strategy. The live worker's whole tick.

        A running strategy with no entry in ``inputs`` is still evaluated, with
        its divergence dimensions unevaluated and a note saying so -- because a
        strategy that stopped producing observations is the case a monitor keyed
        only on supplied inputs would silently skip.
        """
        out = []
        for record in self.machine.in_state(*RUNNING_STATES):
            pair = inputs.get(record.strategy_content_hash)
            out.append(
                self.evaluate(
                    record.strategy_content_hash,
                    expectation=pair[0] if pair else None,
                    observation=pair[1] if pair else None,
                    at=at,
                )
            )
        return tuple(out)

    # --------------------------------------------------------------- reads

    def status(self, strategy_content_hash: str) -> StrategyStatus:
        """The current state, for the API and for the worker's ``StrategyView``."""
        record = self.machine.record(strategy_content_hash)
        last = self._last.get(strategy_content_hash)
        tracker = self._trackers.get(strategy_content_hash)
        band = (
            tracker.band
            if tracker is not None
            else DegradationBand.from_lifecycle(record.state)
        )
        score = last.degradation.score if last is not None else None
        # Health is 1 - score ONLY where a score exists. An unevaluated strategy
        # is not healthy; it is unobserved, and `ever_evaluated` says so.
        health = 1.0 if score is None else float(max(0.0, min(1.0, 1.0 - score)))
        latched = self.halts.latched(strategy_content_hash)
        return StrategyStatus(
            strategy_id=record.strategy_id,
            strategy_content_hash=strategy_content_hash,
            lifecycle=record.state,
            health=health,
            degraded=bool(
                latched
                or band is not DegradationBand.HEALTHY
                or record.state in (StrategyLifecycle.DEGRADED, StrategyLifecycle.QUARANTINED)
            ),
            band=band,
            entered_state_at=record.entered_state_at(),
            latched_halts=latched,
            missing_rule_registrations=self.registrations.missing_rules(
                strategy_content_hash
            ),
            last_evaluated_at=last.at if last is not None else None,
            last_score=score,
            last_confidence=last.degradation.confidence if last is not None else None,
            n_transitions=len(record.history),
        )

    def lifecycle_of(self, strategy_content_hash: str) -> StrategyLifecycle:
        """The current lifecycle state. Raises for an unregistered strategy."""
        return self.machine.state(strategy_content_hash)

    def statuses(self) -> tuple[StrategyStatus, ...]:
        return tuple(self.status(r.strategy_content_hash) for r in self.machine.records())

    def last_evaluation(self, strategy_content_hash: str) -> LifecycleEvaluation | None:
        return self._last.get(strategy_content_hash)

    # ----------------------------------------------------------- internals

    def _tracker(self, record: StrategyRecord) -> DegradationTracker:
        tracker = self._trackers.get(record.strategy_content_hash)
        if tracker is None:
            tracker = DegradationTracker(
                config=self.degradation_config,
                band=DegradationBand.from_lifecycle(record.state),
            )
            self._trackers[record.strategy_content_hash] = tracker
        return tracker

    def _evaluate_rules(
        self,
        strategy_content_hash: str,
        observation: Observation | None,
        now: datetime,
    ) -> list[RuleEvaluation]:
        active = self.registrations.active_rules(strategy_content_hash)
        if not active:
            return []
        rule_obs = RuleObservation(
            strategy_content_hash=strategy_content_hash,
            returns=observation.returns if observation is not None else None,
            at=now,
        )
        return [build_rule(reg).evaluate(rule_obs) for reg in active.values()]

    def _demote(
        self,
        record: StrategyRecord,
        target: StrategyLifecycle,
        verdict: DegradationVerdict,
        report: DivergenceReport | None,
        rule_evaluations: Sequence[RuleEvaluation],
        now: datetime,
    ) -> TransitionRecord | None:
        if self.machine.edge(record.state, target) is None:
            return None
        fired = [e for e in rule_evaluations if e.fired]
        actor = (
            Actor.rule(f"stopping_rule:{fired[0].kind.value}")
            if fired
            else DEGRADATION_ACTOR
        )
        items: list[Evidence] = [
            Evidence(
                kind=EvidenceKind.MONITOR_RESULT,
                id=(
                    f"degradation:{record.strategy_content_hash[:12]}"
                    f"@{now.astimezone(UTC).isoformat()}"
                ),
                summary=verdict.reason,
                detail=verdict.to_dict(),
            )
        ]
        if report is not None:
            items.append(divergence_evidence(report))
        items.extend(e.as_evidence() for e in fired)
        updated = self.machine.transition(
            record.strategy_content_hash,
            target,
            actor=actor,
            reason=verdict.reason,
            evidence=items,
            at=now,
        )
        return updated.history[-1]

    def _alert(
        self, event: AlertEvent, severity: Severity, message: str, **context: Any
    ) -> Alert | None:
        if self.dispatcher is None:
            return Alert(
                event=event,
                message=message,
                severity=severity,
                at=self.clock(),
                source="lifecycle",
                context=dict(context),
            )
        return self.dispatcher.fire(
            event,
            message,
            severity=severity,
            source="lifecycle",
            # The event is part of the key: a halt and a demotion on the same
            # strategy in the same tick are two facts, and suppressing the
            # second because the first looked similar is how an operator learns
            # about a quarantine from a dashboard instead of an alert.
            dedupe_key=(
                f"lifecycle:{context.get('strategy_content_hash', '')}"
                f":{event.value}:{severity.value}"
            ),
            **context,
        )
