"""A degradation score, and the hysteresis that stops it flapping.

``StrategyView.degraded`` blocks at the risk gateway and ``AlertEvent.
STRATEGY_DEGRADED`` exists in the alert taxonomy, but until now **nothing
computed either of them**. This module is what computes them: it folds the
:class:`~fiboki.lifecycle.monitor.DivergenceReport` and the stopping-rule
evaluations into one number in ``[0, 1]``, and turns that number into the
WATCH -> DEGRADED -> QUARANTINED progression.

Why a score and not a rule
--------------------------
The stopping rules are the rules: pre-registered, unarguable, and they halt. This
score is the thing that runs between them -- the gradual signal that says a
strategy is drifting before any single pre-registered threshold has been
breached, so an operator gets to look at it while it is still a question rather
than a loss.

The two failure modes it is built against
-----------------------------------------
**Flapping.** A single threshold on a noisy statistic produces a strategy that
oscillates between PAPER and WATCH every evaluation, which is worse than no
signal at all: after the third oscillation nobody reads it. Two mechanisms
prevent it. Entering a band needs a higher score than staying in it (a gap, not a
line), and entering needs the score to hold for several consecutive evaluations
(dwell time, not an instant).

**Ignorance scored as health.** Dimensions the monitor could not evaluate are
excluded from the score and their weight is redistributed across the dimensions
that WERE evaluated, rather than being scored as zero. A strategy with no data
therefore scores whatever its available evidence says, and
:attr:`DegradationScore.confidence` -- the fraction of the weight that was
actually observed -- is reported alongside so a low-confidence score is not
mistaken for a clean bill of health.

Recovery is not automatic, by default
--------------------------------------
``allow_automatic_recovery`` defaults to ``False``. The band machinery computes a
recovery when the score falls below the exit threshold and stays there, but it
reports it as a **recommendation** rather than applying it, because
:mod:`fiboki.lifecycle.state` requires a named human for every recovery edge and
a monitor that could un-halt what it halted would not be a halt. Setting the flag
to ``True`` is for simulation and for the tests that demonstrate the hysteresis.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fiboki.core.enums import StrategyLifecycle
from fiboki.lifecycle.monitor import (
    DivergenceDimension,
    DivergenceReport,
    DivergenceStatus,
)
from fiboki.lifecycle.stopping_rules import RuleEvaluation

__all__ = [
    "DEGRADATION_CONFIG_V1",
    "DegradationBand",
    "DegradationConfig",
    "DegradationScore",
    "DegradationTracker",
    "DegradationVerdict",
    "score_degradation",
]


class DegradationBand(str, Enum):
    """Health bands, ordered. Each maps to at most one lifecycle state."""

    HEALTHY = "healthy"
    WATCH = "watch"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"

    @property
    def depth(self) -> int:
        return {"healthy": 0, "watch": 1, "degraded": 2, "quarantined": 3}[self.value]

    @property
    def lifecycle_state(self) -> StrategyLifecycle | None:
        """``None`` for HEALTHY: a healthy strategy stays where it is."""
        return {
            DegradationBand.HEALTHY: None,
            DegradationBand.WATCH: StrategyLifecycle.WATCH,
            DegradationBand.DEGRADED: StrategyLifecycle.DEGRADED,
            DegradationBand.QUARANTINED: StrategyLifecycle.QUARANTINED,
        }[self]

    @classmethod
    def from_lifecycle(cls, state: StrategyLifecycle) -> DegradationBand:
        return {
            StrategyLifecycle.WATCH: cls.WATCH,
            StrategyLifecycle.DEGRADED: cls.DEGRADED,
            StrategyLifecycle.QUARANTINED: cls.QUARANTINED,
        }.get(state, cls.HEALTHY)


#: Which divergence dimensions belong to which component of the score.
_COMPONENTS: dict[str, tuple[DivergenceDimension, ...]] = {
    "return": (
        DivergenceDimension.RETURN,
        DivergenceDimension.SHARPE,
        DivergenceDimension.WIN_RATE,
        DivergenceDimension.RETURN_DISTRIBUTION,
    ),
    "cost": (
        DivergenceDimension.SPREAD,
        DivergenceDimension.SLIPPAGE,
        DivergenceDimension.LATENCY,
        DivergenceDimension.REJECTED_ORDERS,
    ),
    "risk": (
        DivergenceDimension.DRAWDOWN,
        DivergenceDimension.TRADE_FREQUENCY,
    ),
    "regime": (DivergenceDimension.REGIME,),
}


@dataclass(frozen=True, slots=True)
class DegradationConfig:
    """Versioned degradation policy. Stamped onto every score."""

    version: str = "lifecycle_degradation_v1"

    weights: Mapping[str, float] = field(
        default_factory=lambda: {
            # Costs carry nearly as much weight as returns on purpose. A return
            # shortfall over a short forward window is frequently luck; a cost
            # shortfall is a measurement, and it invalidates every stored
            # expectancy for the instrument rather than merely disappointing.
            "return": 0.32,
            "cost": 0.26,
            "risk": 0.20,
            "regime": 0.10,
            "rules": 0.12,
        }
    )

    # -- band thresholds. ENTER is always strictly above EXIT: the gap IS the
    #    hysteresis, and closing it would reintroduce the flapping.
    #
    #    The levels are chosen for REACHABILITY against the weights above, which
    #    is the check nobody does and which quietly disarms most scoring systems.
    #    With full coverage the heaviest single component is `return` at 0.32 and
    #    the next is `cost` at 0.26, so a WATCH entry of 0.35 would be
    #    unreachable by ANY single component failing completely -- spread,
    #    slippage AND latency all three times their model would score 0.22 and
    #    raise nothing at all. WATCH therefore enters at 0.20, which a single
    #    component reaches once about three-quarters of its dimensions are
    #    adverse, because WATCH means "one thing is clearly wrong, go and look".
    #    DEGRADED at 0.40 needs roughly two components; QUARANTINED at 0.65 needs
    #    most of them, and in practice is reached by a fired stopping rule, which
    #    forces it regardless of the score.
    #
    #    A low WATCH entry costs nothing in false alarms: `Divergence.severity`
    #    is exactly 0.0 for any dimension that is not DIVERGED, and DIVERGED is
    #    decided on a Holm-adjusted p-value, so a clean report scores 0.000 and
    #    no threshold in this range can be tripped by noise alone.
    watch_enter: float = 0.20
    watch_exit: float = 0.12
    degraded_enter: float = 0.40
    degraded_exit: float = 0.28
    quarantine_enter: float = 0.65
    quarantine_exit: float = 0.50

    # -- dwell times, asymmetric on purpose. Worsening acts on less evidence
    #    than recovering, because the costs of the two errors are not equal.
    consecutive_to_worsen: int = 2
    consecutive_to_recover: int = 3

    allow_automatic_recovery: bool = False
    min_confidence: float = 0.30
    """Below this fraction of observed weight the score is reported but never
    acted on: it is a measurement of ignorance, not of degradation."""

    def __post_init__(self) -> None:
        pairs = (
            ("watch", self.watch_enter, self.watch_exit),
            ("degraded", self.degraded_enter, self.degraded_exit),
            ("quarantine", self.quarantine_enter, self.quarantine_exit),
        )
        for name, enter, exit_ in pairs:
            if not 0.0 < exit_ < enter < 1.0001:
                raise ValueError(
                    f"{name}: require 0 < exit ({exit_}) < enter ({enter}); the "
                    "gap between them is the hysteresis"
                )
        if not self.watch_enter < self.degraded_enter < self.quarantine_enter:
            raise ValueError("band entry thresholds must increase with depth")
        if self.consecutive_to_worsen < 1 or self.consecutive_to_recover < 1:
            raise ValueError("dwell times must be at least 1 evaluation")
        total = sum(float(v) for v in self.weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {total}")

    def enter_threshold(self, band: DegradationBand) -> float:
        return {
            DegradationBand.WATCH: self.watch_enter,
            DegradationBand.DEGRADED: self.degraded_enter,
            DegradationBand.QUARANTINED: self.quarantine_enter,
        }[band]

    def exit_threshold(self, band: DegradationBand) -> float:
        return {
            DegradationBand.WATCH: self.watch_exit,
            DegradationBand.DEGRADED: self.degraded_exit,
            DegradationBand.QUARANTINED: self.quarantine_exit,
        }[band]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "weights": dict(self.weights),
            "bands": {
                b.value: {
                    "enter": self.enter_threshold(b),
                    "exit": self.exit_threshold(b),
                }
                for b in (
                    DegradationBand.WATCH,
                    DegradationBand.DEGRADED,
                    DegradationBand.QUARANTINED,
                )
            },
            "consecutive_to_worsen": self.consecutive_to_worsen,
            "consecutive_to_recover": self.consecutive_to_recover,
            "allow_automatic_recovery": self.allow_automatic_recovery,
            "min_confidence": self.min_confidence,
        }


DEGRADATION_CONFIG_V1 = DegradationConfig()


@dataclass(frozen=True, slots=True)
class DegradationScore:
    """One number, and every component that produced it."""

    score: float
    confidence: float
    """Fraction of the total weight that had evidence behind it. A score of 0.1
    at 20% confidence is not a healthy strategy, it is an unobserved one."""
    components: Mapping[str, float | None]
    """Component score, or ``None`` where nothing could be evaluated."""
    config_version: str
    forced_band: DegradationBand | None = None
    """Set when a pre-registered stopping rule fired. A fired rule is not noise
    and does not wait for dwell time."""
    notes: tuple[str, ...] = ()

    @property
    def actionable(self) -> bool:
        return self.forced_band is not None or self.confidence > 0.0

    def describe(self) -> str:
        parts = ", ".join(
            f"{k}={'-' if v is None else format(v, '.2f')}"
            for k, v in sorted(self.components.items())
        )
        forced = f", FORCED {self.forced_band.value}" if self.forced_band else ""
        return (
            f"degradation {self.score:.3f} (confidence {self.confidence:.0%}{forced}) "
            f"[{parts}]"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "components": dict(self.components),
            "config_version": self.config_version,
            "forced_band": self.forced_band.value if self.forced_band else None,
            "notes": list(self.notes),
            "description": self.describe(),
        }


def score_degradation(
    report: DivergenceReport | None,
    rule_evaluations: Sequence[RuleEvaluation] = (),
    *,
    config: DegradationConfig = DEGRADATION_CONFIG_V1,
    latched_halts: int = 0,
) -> DegradationScore:
    """Fold a divergence report and the rule evaluations into one score.

    Unevaluated dimensions do not contribute and do not dilute: their component's
    weight is removed from the denominator, so the score is the weighted mean over
    the evidence that exists. ``confidence`` reports how much weight that was.
    """
    components: dict[str, float | None] = {}
    notes: list[str] = []

    for name, dimensions in _COMPONENTS.items():
        if report is None:
            components[name] = None
            continue
        severities = []
        for dim in dimensions:
            d = report.dimension(dim)
            if d is None or d.status is DivergenceStatus.NOT_EVALUATED:
                continue
            severities.append(d.severity)
        components[name] = (
            float(sum(severities) / len(severities)) if severities else None
        )

    evaluated = [e for e in rule_evaluations if e.status.value != "not_evaluated"]
    fired = [e for e in evaluated if e.fired]
    if evaluated or latched_halts:
        components["rules"] = (
            1.0 if (fired or latched_halts) else 0.0
        )
    else:
        components["rules"] = None
        notes.append("no stopping rule could be evaluated on this forward record")

    observed_weight = sum(
        float(config.weights.get(k, 0.0)) for k, v in components.items() if v is not None
    )
    total_weight = sum(float(v) for v in config.weights.values())
    if observed_weight <= 0.0:
        return DegradationScore(
            score=0.0,
            confidence=0.0,
            components=components,
            config_version=config.version,
            notes=(
                *notes,
                "nothing could be evaluated: this is a score of zero because "
                "there is no evidence, NOT because the strategy is healthy",
            ),
        )

    weighted = sum(
        float(config.weights.get(k, 0.0)) * float(v)
        for k, v in components.items()
        if v is not None
    )
    score = float(min(1.0, max(0.0, weighted / observed_weight)))

    forced: DegradationBand | None = None
    if fired or latched_halts:
        forced = DegradationBand.QUARANTINED
        names = sorted({e.kind.value for e in fired})
        notes.append(
            "pre-registered stopping rule fired"
            + (f" ({', '.join(names)})" if names else " earlier and is still latched")
            + ": quarantine is immediate and does not wait for dwell time"
        )
    if report is not None and report.cost_divergence:
        notes.append(
            "a cost or execution-quality dimension diverged: every stored "
            "expectancy for this instrument is overstated by a measurable "
            "amount and must be re-run rather than accepted"
        )

    confidence = float(observed_weight / total_weight)
    if confidence < config.min_confidence:
        notes.append(
            f"confidence {confidence:.0%} is below the {config.min_confidence:.0%} "
            "floor: this score is reported but must not be acted on"
        )
    return DegradationScore(
        score=score,
        confidence=confidence,
        components=components,
        config_version=config.version,
        forced_band=forced,
        notes=tuple(notes),
    )


# ==========================================================================
# Hysteresis
# ==========================================================================


@dataclass(frozen=True, slots=True)
class DegradationVerdict:
    """What one observation concluded about the band."""

    band: DegradationBand
    previous_band: DegradationBand
    score: DegradationScore
    changed: bool
    pending_band: DegradationBand | None
    consecutive: int
    reason: str
    recommended_band: DegradationBand | None = None
    """Set when the score supports a RECOVERY that was not applied because
    ``allow_automatic_recovery`` is off. An operator acts on this; the service
    does not."""

    @property
    def worsened(self) -> bool:
        return self.band.depth > self.previous_band.depth

    @property
    def target_state(self) -> StrategyLifecycle | None:
        return self.band.lifecycle_state

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band.value,
            "previous_band": self.previous_band.value,
            "changed": self.changed,
            "worsened": self.worsened,
            "pending_band": self.pending_band.value if self.pending_band else None,
            "consecutive": int(self.consecutive),
            "reason": self.reason,
            "recommended_band": (
                self.recommended_band.value if self.recommended_band else None
            ),
            "score": self.score.to_dict(),
        }


@dataclass(slots=True)
class DegradationTracker:
    """Per-strategy band state with dwell time. The only mutable object here.

    One tracker per strategy content hash, held by
    :class:`fiboki.lifecycle.service.LifecycleService` between evaluations. Its
    state is small and recoverable -- the band can be read back from the lifecycle
    record and the dwell counter is at worst re-served -- so it is deliberately
    NOT persisted: a counter that survives a restart but whose evaluations did not
    would claim consecutive evidence it never had.
    """

    config: DegradationConfig = DEGRADATION_CONFIG_V1
    band: DegradationBand = DegradationBand.HEALTHY
    pending_band: DegradationBand | None = None
    consecutive: int = 0
    history: list[float] = field(default_factory=list)

    def reset(self, band: DegradationBand) -> None:
        """Set the band directly. What an operator's release calls."""
        self.band = band
        self.pending_band = None
        self.consecutive = 0

    def observe(self, score: DegradationScore) -> DegradationVerdict:
        previous = self.band
        self.history.append(score.score)

        # A fired stopping rule is not a noisy statistic. It bypasses dwell.
        if score.forced_band is not None and score.forced_band.depth > previous.depth:
            self.band = score.forced_band
            self.pending_band = None
            self.consecutive = 0
            return DegradationVerdict(
                band=self.band,
                previous_band=previous,
                score=score,
                changed=True,
                pending_band=None,
                consecutive=0,
                reason=(
                    "a pre-registered stopping rule fired; quarantine is "
                    "immediate and does not wait for dwell time"
                ),
            )

        if score.confidence < self.config.min_confidence:
            self.pending_band = None
            self.consecutive = 0
            return DegradationVerdict(
                band=previous,
                previous_band=previous,
                score=score,
                changed=False,
                pending_band=None,
                consecutive=0,
                reason=(
                    f"confidence {score.confidence:.0%} below the "
                    f"{self.config.min_confidence:.0%} floor: not enough evidence "
                    "to move a band in either direction"
                ),
            )

        target = self._target(previous, score.score)
        if target is previous:
            self.pending_band = None
            self.consecutive = 0
            return DegradationVerdict(
                band=previous,
                previous_band=previous,
                score=score,
                changed=False,
                pending_band=None,
                consecutive=0,
                reason=(
                    f"score {score.score:.3f} sits inside the {previous.value} "
                    "band's hysteresis gap"
                ),
            )

        worsening = target.depth > previous.depth
        needed = (
            self.config.consecutive_to_worsen
            if worsening
            else self.config.consecutive_to_recover
        )
        if self.pending_band is target:
            self.consecutive += 1
        else:
            self.pending_band = target
            self.consecutive = 1

        if self.consecutive < needed:
            return DegradationVerdict(
                band=previous,
                previous_band=previous,
                score=score,
                changed=False,
                pending_band=target,
                consecutive=self.consecutive,
                reason=(
                    f"{target.value} pending: {self.consecutive} of {needed} "
                    "consecutive evaluations. A single reading is noise"
                ),
            )

        if not worsening and not self.config.allow_automatic_recovery:
            return DegradationVerdict(
                band=previous,
                previous_band=previous,
                score=score,
                changed=False,
                pending_band=target,
                consecutive=self.consecutive,
                recommended_band=target,
                reason=(
                    f"the score has supported {target.value} for "
                    f"{self.consecutive} consecutive evaluations, but recovery "
                    "requires an explicit operator action"
                ),
            )

        self.band = target
        self.pending_band = None
        self.consecutive = 0
        return DegradationVerdict(
            band=target,
            previous_band=previous,
            score=score,
            changed=True,
            pending_band=None,
            consecutive=0,
            reason=(
                f"score {score.score:.3f} held {'above' if worsening else 'below'} "
                f"the {target.value} threshold for {needed} consecutive evaluations"
            ),
        )

    # ------------------------------------------------------------ internals

    def _target(self, current: DegradationBand, score: float) -> DegradationBand:
        """The band this score supports, given where we are.

        Worsening may jump straight to QUARANTINED -- a demotion is allowed to
        drop several stages. Recovery moves ONE band at a time, however good the
        score looks, because a single good evaluation after a quarantine is the
        least trustworthy number in the system.
        """
        deeper = [
            b
            for b in (
                DegradationBand.QUARANTINED,
                DegradationBand.DEGRADED,
                DegradationBand.WATCH,
            )
            if b.depth > current.depth and score >= self.config.enter_threshold(b)
        ]
        if deeper:
            return max(deeper, key=lambda b: b.depth)
        if current is DegradationBand.HEALTHY:
            return current
        if score <= self.config.exit_threshold(current):
            order = (
                DegradationBand.HEALTHY,
                DegradationBand.WATCH,
                DegradationBand.DEGRADED,
                DegradationBand.QUARANTINED,
            )
            return order[current.depth - 1]
        return current
