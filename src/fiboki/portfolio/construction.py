"""Portfolio-aware allocation across concurrent candidate signals.

V1 had no portfolio layer at all. Each bot sized its own signal against fleet
equity, so twelve strategies could each take "1% risk" on six correlated FX
majors at the same time and call the result 12% risk when the realised
one-factor exposure was closer to 50%.

This module decides *how much of the risk budget each concurrent candidate is
entitled to*, before :func:`fiboki.portfolio.sizing.size_trade` turns that
entitlement into units. It is aware of:

    strategy correlation, instrument correlation, currency exposure (net base
    and quote across the FX book), asset-class concentration, volatility
    targeting, margin, existing drawdown, regime, strategy confidence and
    strategy degradation state.

Three allocators sit behind one interface:

    * :class:`EqualRiskAllocator`          -- flat risk budget per candidate
    * :class:`VolatilityParityAllocator`   -- inverse-volatility budget
    * :class:`CorrelationPenalisedAllocator` -- inverse of each candidate's
      average correlation to the rest of the candidate set

The allocator produces a *base* weight. A fixed, ordered pipeline of portfolio
adjustments then scales it, and **every** scaling factor is recorded on the
allocation with the step that applied it. An allocation you cannot explain is
an allocation you should not have taken, so the reasoning is data, not a log
line.

Weights are fractions of the candidate's TIER BASE RISK, not fractions of
equity. A strategy's evidence tier (``docs/v2/RISK_GOVERNANCE.md`` Governor 1:
Probationary 0.25%, Established 0.75%, Proven 1.5%, Flagship 2.0% of equity per
trade) sets :attr:`Allocation.base_risk_pct`; the weight scales it; and the
risk actually intended is ``base_risk_pct * weight``. The sum of weights across
candidates is capped by :attr:`ConstructionConfig.max_total_weight`, which is
what stops twenty simultaneous candidates from spending twenty budgets, and
the book's total risk is capped at :attr:`ConstructionConfig.max_total_risk_pct`.

Down only (construction_v2)
---------------------------
Every step's factor is at most 1.0 and a final ``tier_cap`` pass clamps each
weight to :attr:`ConstructionConfig.max_scale_vs_tier` (asserted <= 1.0), so
**the final risk of a candidate never exceeds its tier's base risk**. v1 let
volatility targeting scale UP by 1.5x (``vol_target_max_scale``); that fought
the per-trade gateway cap and the plan's down-only principle (audit F P1-11),
and it now refuses to be configured above 1.0.

The conviction channel
----------------------
``_step_conviction`` is the ONLY consumer of a
:class:`~fiboki.core.contracts.ConvictionReading` (an agent debate's verdict).
A versioned :class:`ConvictionPolicy` maps it to a factor in ``[floor, 1.0]``:
only disagreement with the signal dampens; agreement, no view, a missing or a
stale reading are all exactly 1.0. The policy is ``enabled=False`` by default
and, even when enabled, takes effect only if the operator's signed
:class:`~fiboki.core.tier.AgentInfluenceTier` permits dampening (T3). In every
other case the would-be factor is still computed and logged as a
:class:`ShadowFactor` with ``Provenance.SHADOW``, so the shadow evaluation has
data from day one.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from fiboki.core.contracts import AccountState, ConvictionReading, Position, Signal
from fiboki.core.enums import AssetClass, Provenance, StrategyLifecycle
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.core.tier import AgentInfluenceTier, TierReading

__all__ = [
    "COARSE_REGIMES",
    "CONSTRUCTION_VERSION",
    "CONVICTION_POLICY_VERSION",
    "Allocation",
    "AllocationReason",
    "AllocationResult",
    "Allocator",
    "CandidateSignal",
    "ConstructionConfig",
    "ConvictionPolicy",
    "CorrelationMatrix",
    "CorrelationPenalisedAllocator",
    "EqualRiskAllocator",
    "PortfolioConstructor",
    "PortfolioSnapshot",
    "ShadowFactor",
    "StrategyTier",
    "VolatilityParityAllocator",
    "coarse_regime",
    "get_allocator",
]

#: Stamped on every allocation. v2: tier base risk, step drawdown throttle,
#: correlated and total open-risk budgets, final down-only cap, conviction step.
CONSTRUCTION_VERSION = "construction_v2"
CONVICTION_POLICY_VERSION = "conviction_v1"


class StrategyTier(str, Enum):
    """Evidence tier (RISK_GOVERNANCE.md Governor 1). Values are persisted.

    A strategy enters at PROBATIONARY and cannot skip a tier. Nothing in the
    code computes a promotion yet (no strategy has a forward record), so the
    runtime's default ``tier_source`` answers PROBATIONARY for everything.
    """

    PROBATIONARY = "probationary"
    ESTABLISHED = "established"
    PROVEN = "proven"
    FLAGSHIP = "flagship"


@dataclass(frozen=True, slots=True)
class ConvictionPolicy:
    """How a debate's verdict may change a size. Versioned; stamped; OFF by default.

    ============================  ==========================================
    Reading                       Factor
    ============================  ==========================================
    missing / stale / not yet     1.0 (never a size change: an LLM outage
    available / wrong instrument  cannot move book risk)
    stance ``none``               1.0
    agrees with the signal        1.0 (agreement never adds size)
    disagrees, strength 1         ``weak_disagreement_factor`` (0.9)
    disagrees, strength 2         ``strong_disagreement_factor`` (0.75, the floor)
    ============================  ==========================================

    Note the contrast with ``regime_scalars["unknown"] = 0.6``: there, not
    knowing reduces size; here, "unknown" must equal the pre-feature baseline,
    or the availability of a model server would modulate book risk.
    """

    #: The lowest factor any policy version may set. The LLM can at most halve
    #: a size and can never drop a candidate.
    ABSOLUTE_FLOOR: ClassVar[float] = 0.5

    version: str = CONVICTION_POLICY_VERSION
    enabled: bool = False
    strong_disagreement_factor: float = 0.75
    weak_disagreement_factor: float = 0.9
    #: Present so the ceiling is a stated, asserted number rather than an
    #: absence. It may not exceed 1.0: raising it would need an
    #: :class:`~fiboki.core.tier.AgentInfluenceTier` that permits upsizing, and
    #: none exists (``AgentInfluenceTier.permits_upsizing`` is False for all).
    max_factor: float = 1.0
    #: A verdict valid for longer than this is refused (factor 1.0).
    max_validity_hours: float = 24.0

    def __post_init__(self) -> None:
        if self.max_factor > 1.0:
            raise ValueError(
                f"ConvictionPolicy.max_factor={self.max_factor} > 1.0. No "
                "AgentInfluenceTier permits upsizing (T3 permits dampening only, "
                "and no higher tier grants size), so the channel is down-only by "
                "construction."
            )
        if not (
            self.ABSOLUTE_FLOOR
            <= self.strong_disagreement_factor
            <= self.weak_disagreement_factor
            <= 1.0
        ):
            raise ValueError(
                "require 0.5 <= strong_disagreement_factor <= weak_disagreement_factor "
                f"<= 1.0, got {self.strong_disagreement_factor} / "
                f"{self.weak_disagreement_factor}"
            )
        if not self.strong_disagreement_factor <= self.max_factor:
            raise ValueError("max_factor below the floor would dampen every candidate")
        if self.max_validity_hours <= 0:
            raise ValueError("max_validity_hours must be positive")

    @property
    def floor(self) -> float:
        return self.strong_disagreement_factor

    def stamp(self) -> dict[str, Any]:
        return {
            "conviction_policy": self.version,
            "conviction_enabled_config": self.enabled,
            "conviction_floor": self.floor,
            "conviction_max_factor": self.max_factor,
        }


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CorrelationMatrix:
    """A labelled symmetric correlation matrix with an explicit default.

    Missing pairs return ``default`` rather than 0.0. Assuming zero correlation
    for a pair you have not measured is the single most dangerous default in
    portfolio construction: it is maximally permissive exactly where you know
    least. The default is therefore a constructor argument, and the
    conservative choice (used by :class:`ConstructionConfig`) is a positive
    number.
    """

    labels: tuple[str, ...] = ()
    matrix: tuple[tuple[float, ...], ...] = ()
    default: float = 0.0

    def __post_init__(self) -> None:
        n = len(self.labels)
        if len(self.matrix) != n or any(len(row) != n for row in self.matrix):
            raise ValueError("CorrelationMatrix must be square and match its labels")
        if len(set(self.labels)) != n:
            raise ValueError("CorrelationMatrix labels must be unique")
        for row in self.matrix:
            for v in row:
                if not -1.0000001 <= v <= 1.0000001:
                    raise ValueError(f"Correlation {v} outside [-1, 1]")

    @classmethod
    def from_mapping(
        cls, pairs: Mapping[tuple[str, str], float], *, default: float = 0.0
    ) -> CorrelationMatrix:
        labels = sorted({x for pair in pairs for x in pair})
        idx = {lab: i for i, lab in enumerate(labels)}
        m = np.full((len(labels), len(labels)), default, dtype=float)
        np.fill_diagonal(m, 1.0)
        for (a, b), v in pairs.items():
            m[idx[a], idx[b]] = v
            m[idx[b], idx[a]] = v
        return cls(tuple(labels), tuple(tuple(r) for r in m.tolist()), default)

    @classmethod
    def identity(cls, labels: Sequence[str], *, default: float = 0.0) -> CorrelationMatrix:
        labs = tuple(labels)
        m = np.full((len(labs), len(labs)), default, dtype=float)
        np.fill_diagonal(m, 1.0)
        return cls(labs, tuple(tuple(r) for r in m.tolist()), default)

    def get(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        try:
            i = self.labels.index(a)
            j = self.labels.index(b)
        except ValueError:
            return self.default
        return float(self.matrix[i][j])


@dataclass(frozen=True, slots=True)
class CandidateSignal:
    """One strategy's signal offered to the portfolio for a risk allocation."""

    signal: Signal
    #: Realised annualised volatility of the instrument, as a fraction (0.08 = 8%).
    annualised_vol: float = 0.10
    #: Strategy health, 0.0 (fully degraded) .. 1.0 (healthy). Distinct from
    #: signal confidence: a confident signal from a degrading strategy is still
    #: a signal from a degrading strategy.
    health: float = 1.0
    lifecycle: StrategyLifecycle = StrategyLifecycle.PAPER
    notes: str = ""
    #: Evidence tier; sets the base risk this candidate's weight scales.
    tier: StrategyTier = StrategyTier.PROBATIONARY
    #: This candidate's OWN instrument regime. ``None`` falls back to the
    #: snapshot's book-level label (the v1 behaviour).
    regime: str | None = None
    #: The latest debate verdict for this instrument, or ``None``. Read by
    #: ``_step_conviction`` and nothing else (AST-tested).
    conviction: ConvictionReading | None = None

    @property
    def strategy_id(self) -> str:
        return self.signal.strategy_id

    @property
    def symbol(self) -> str:
        return self.signal.instrument

    @property
    def instrument(self) -> Instrument:
        return get_instrument(self.signal.instrument)

    @property
    def key(self) -> str:
        return self.signal.signal_id

    def __post_init__(self) -> None:
        if self.annualised_vol <= 0:
            raise ValueError(
                f"annualised_vol must be positive for {self.symbol}; a zero-vol "
                "instrument would receive an infinite inverse-vol weight"
            )
        if not 0.0 <= self.health <= 1.0:
            raise ValueError("health must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Everything the portfolio knows about itself at decision time.

    Exposures are signed notionals in the ACCOUNT currency. Currency exposure
    is *net*: long EURUSD is +EUR and -USD, so a simultaneous long EURUSD and
    short EURGBP partially nets in EUR and does not in USD/GBP.
    """

    account: AccountState
    as_of: pd.Timestamp
    open_positions: tuple[Position, ...] = ()
    instrument_exposure: Mapping[str, float] = field(default_factory=dict)
    strategy_exposure: Mapping[str, float] = field(default_factory=dict)
    currency_exposure: Mapping[str, float] = field(default_factory=dict)
    asset_class_exposure: Mapping[str, float] = field(default_factory=dict)
    instrument_correlation: CorrelationMatrix = field(default_factory=CorrelationMatrix)
    strategy_correlation: CorrelationMatrix = field(default_factory=CorrelationMatrix)
    #: Realised annualised volatility of the portfolio's equity curve, a fraction.
    realised_portfolio_vol: float = 0.0
    margin_available: float = 0.0
    #: Free-form regime label from :mod:`fiboki.marketstate`; unknown regimes
    #: are scaled by ``ConstructionConfig.regime_scalars`` default.
    regime: str = "unknown"
    #: Risk to stop of the open book, ACCOUNT currency, per instrument. ``None``
    #: means NOT MEASURED (not zero): the open-risk budgets then say so on
    #: every allocation (``open_risk_unmeasured``) and in the result stamp. The
    #: runtime always measures it.
    open_risk_by_instrument: Mapping[str, float] | None = None

    @property
    def equity(self) -> float:
        return self.account.equity

    @property
    def drawdown_pct(self) -> float:
        return self.account.drawdown_pct

    @property
    def margin_utilisation(self) -> float:
        if self.account.equity <= 0:
            return 1.0
        return max(0.0, self.account.margin_used / self.account.equity)

    @property
    def open_risk_pct(self) -> float | None:
        """Total open risk to stop as a percentage of equity; ``None`` if unmeasured."""
        if self.open_risk_by_instrument is None or self.equity <= 0:
            return None
        return sum(max(0.0, float(v)) for v in self.open_risk_by_instrument.values()) / (
            self.equity
        ) * 100.0


def _default_tier_risk() -> Mapping[StrategyTier, float]:
    # RISK_GOVERNANCE.md Governor 1, "Risk per trade", % of equity.
    return {
        StrategyTier.PROBATIONARY: 0.25,
        StrategyTier.ESTABLISHED: 0.75,
        StrategyTier.PROVEN: 1.50,
        StrategyTier.FLAGSHIP: 2.00,
    }


def _default_tier_concurrency() -> Mapping[StrategyTier, int]:
    # RISK_GOVERNANCE.md Governor 1, "Max concurrent positions", per strategy.
    return {
        StrategyTier.PROBATIONARY: 2,
        StrategyTier.ESTABLISHED: 4,
        StrategyTier.PROVEN: 6,
        StrategyTier.FLAGSHIP: 8,
    }


@dataclass(frozen=True, slots=True)
class ConstructionConfig:
    """Portfolio construction policy. Versioned; stamped onto every result.

    The numbers below that come from ``docs/v2/RISK_GOVERNANCE.md`` say so.
    Changing one is a policy amendment: bump :attr:`version`.
    """

    version: str = CONSTRUCTION_VERSION

    #: Total risk budget spendable across all concurrent candidates, as a
    #: multiple of the single-trade risk fraction.
    max_total_weight: float = 3.0
    max_weight_per_candidate: float = 1.0
    min_weight_to_trade: float = 0.10

    #: Above this average correlation a candidate is penalised toward zero.
    correlation_soft_cap: float = 0.40
    correlation_hard_cap: float = 0.85
    #: Assumed correlation for pairs with no measurement. Deliberately positive.
    unmeasured_correlation: float = 0.30

    #: Caps as a MULTIPLE of equity, on gross notional. Above 1.0 on purpose:
    #: on a 30:1 FX instrument a 1%-risk position is several times equity in
    #: notional. See the note in :mod:`fiboki.risk.limits`.
    max_instrument_notional_pct: float = 10.0
    max_asset_class_notional_pct: float = 20.0
    max_currency_notional_pct: float = 15.0

    #: Volatility targeting. ``None`` disables it. The scale may only REDUCE
    #: (``vol_target_max_scale <= 1.0``): a calm book does not earn extra size.
    target_portfolio_vol: float | None = 0.12
    vol_target_max_scale: float = 1.0

    max_margin_utilisation: float = 0.50

    #: Drawdown throttle, a STEP function (RISK_GOVERNANCE.md Governor 3):
    #: below ``drawdown_derisk_start_pct`` normal; then x ``drawdown_derisk_factor``
    #: (0.6) until ``drawdown_severe_pct``; then x ``drawdown_severe_factor``
    #: (0.3) with PROBATIONARY strategies suspended; at ``drawdown_pause_pct``
    #: PAUSE (no new risk); at ``drawdown_zero_pct`` FLATTEN is required (still
    #: no new risk here; flattening is the kill switch's job, not this module's).
    #: Boundaries belong to the stricter band. Stateless: the policy's "lift
    #: only after ten trading days above the threshold" hysteresis is NOT
    #: implemented and is a stated gap.
    drawdown_derisk_start_pct: float = 5.0
    drawdown_derisk_factor: float = 0.6
    drawdown_severe_pct: float = 10.0
    drawdown_severe_factor: float = 0.3
    drawdown_pause_pct: float = 15.0
    drawdown_zero_pct: float = 20.0

    #: Evidence tier -> base risk per trade, % of equity (Governor 1).
    tier_risk_pct: Mapping[StrategyTier, float] = field(default_factory=_default_tier_risk)
    #: Evidence tier -> max concurrent open positions per strategy (Governor 1).
    tier_max_concurrent: Mapping[StrategyTier, int] = field(
        default_factory=_default_tier_concurrency
    )
    #: A hard ceiling on the base risk, % of equity, whatever the tier says.
    #: The runtime sets it from ``SizingPolicy.risk_fraction`` so a configured
    #: sizing policy can only ever LOWER the tier table. ``None``: no ceiling.
    risk_ceiling_pct: float | None = None
    #: The final weight is clamped to this. Asserted <= 1.0, so the final risk
    #: of a candidate never exceeds its tier's base risk.
    max_scale_vs_tier: float = 1.0

    #: Total open risk to stop, % of equity, across the whole book plus every
    #: candidate in the batch (Governor 2: 6%).
    max_total_risk_pct: float = 6.0
    #: The correlation-aware budget: ``sum(|rho(c, i)| * open_risk_i)`` plus the
    #: candidate's own risk may not exceed this (Governor 2 treats correlated
    #: positions as one position, and one position's instrument cap is 3%).
    #: Unmeasured pairs count at :attr:`unmeasured_correlation`.
    max_correlated_risk_pct: float = 3.0

    #: The agent conviction channel. Disabled by default; see ConvictionPolicy.
    conviction: ConvictionPolicy = field(default_factory=ConvictionPolicy)

    #: Multipliers applied by market regime. Missing regimes use ``1.0``.
    regime_scalars: Mapping[str, float] = field(
        default_factory=lambda: {
            "trend": 1.0,
            "range": 0.7,
            "high_vol": 0.5,
            "crisis": 0.25,
            "unknown": 0.6,
        }
    )

    #: Lifecycle states permitted to receive a risk allocation at all.
    allocatable_lifecycles: frozenset[StrategyLifecycle] = frozenset(
        {
            StrategyLifecycle.PAPER,
            StrategyLifecycle.SHADOW,
            StrategyLifecycle.DEMO,
            StrategyLifecycle.APPROVED,
            StrategyLifecycle.LIVE,
            StrategyLifecycle.WATCH,
        }
    )
    #: States that may trade but at reduced size.
    lifecycle_scalars: Mapping[StrategyLifecycle, float] = field(
        default_factory=lambda: {
            StrategyLifecycle.WATCH: 0.5,
            StrategyLifecycle.DEMO: 0.75,
        }
    )

    def __post_init__(self) -> None:
        if self.max_total_weight <= 0:
            raise ValueError("max_total_weight must be positive")
        if not 0.0 <= self.correlation_soft_cap <= self.correlation_hard_cap <= 1.0:
            raise ValueError("require 0 <= correlation_soft_cap <= correlation_hard_cap <= 1")
        if self.drawdown_zero_pct <= self.drawdown_derisk_start_pct:
            raise ValueError("drawdown_zero_pct must exceed drawdown_derisk_start_pct")
        if not (
            self.drawdown_derisk_start_pct
            < self.drawdown_severe_pct
            < self.drawdown_pause_pct
            <= self.drawdown_zero_pct
        ):
            raise ValueError(
                "require derisk_start < severe < pause <= zero for the drawdown throttle"
            )
        if not 0.0 < self.drawdown_severe_factor <= self.drawdown_derisk_factor <= 1.0:
            raise ValueError("require 0 < severe_factor <= derisk_factor <= 1")
        # DOWN ONLY. Every one of these would otherwise be a way to make a
        # position larger than its tier's base risk.
        if self.vol_target_max_scale > 1.0:
            raise ValueError(
                f"vol_target_max_scale={self.vol_target_max_scale} > 1.0: volatility "
                "targeting may reduce size, never increase it (audit F P1-11)"
            )
        if not 0.0 < self.max_scale_vs_tier <= 1.0:
            raise ValueError("max_scale_vs_tier must lie in (0, 1]")
        for label, scalars in (
            ("regime_scalars", self.regime_scalars),
            ("lifecycle_scalars", self.lifecycle_scalars),
        ):
            bad = {k: v for k, v in scalars.items() if not 0.0 <= float(v) <= 1.0}
            if bad:
                raise ValueError(f"{label} must lie in [0, 1]; got {bad}")
        missing = [t.value for t in StrategyTier if t not in self.tier_risk_pct]
        if missing:
            raise ValueError(f"tier_risk_pct has no entry for {missing}")
        bad_risk = {t: v for t, v in self.tier_risk_pct.items() if not 0.0 < float(v) <= 5.0}
        if bad_risk:
            raise ValueError(f"tier_risk_pct must lie in (0, 5]% of equity; got {bad_risk}")
        if self.risk_ceiling_pct is not None and self.risk_ceiling_pct <= 0:
            raise ValueError("risk_ceiling_pct must be positive when set")
        if self.max_total_risk_pct <= 0 or self.max_correlated_risk_pct <= 0:
            raise ValueError("risk budgets must be positive")

    def base_risk_pct(self, tier: StrategyTier) -> float:
        """The tier's base risk per trade, % of equity, after the ceiling."""
        pct = float(self.tier_risk_pct[tier])
        if self.risk_ceiling_pct is not None:
            pct = min(pct, float(self.risk_ceiling_pct))
        return pct


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocationReason:
    """One recorded step of the construction pipeline."""

    step: str
    factor: float
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.step}x{self.factor:.4f}" + (f" ({self.detail})" if self.detail else "")


@dataclass(frozen=True, slots=True)
class Allocation:
    candidate: CandidateSignal
    weight: float
    base_weight: float
    #: The steps whose factor was not 1.0, plus the conviction step always.
    reasons: tuple[AllocationReason, ...]
    dropped: bool = False
    drop_reason: str | None = None
    #: The tier's base risk per trade, % of equity (after the ceiling).
    base_risk_pct: float = 0.0
    #: EVERY step that ran, including those whose factor was exactly 1.0, in
    #: order. This is "why this size" for the operator view: a step that did
    #: nothing is still a step that was checked.
    trace: tuple[AllocationReason, ...] = ()

    @property
    def signal_id(self) -> str:
        return self.candidate.signal.signal_id

    @property
    def risk_pct(self) -> float:
        """The risk this allocation intends, % of equity. Never above ``base_risk_pct``."""
        return 0.0 if self.dropped else self.base_risk_pct * self.weight

    @property
    def explanation(self) -> str:
        head = f"{self.candidate.strategy_id}/{self.candidate.symbol}"
        body = " -> ".join(str(r) for r in self.reasons)
        tail = f" DROPPED:{self.drop_reason}" if self.dropped else ""
        return (
            f"{head}: tier={self.candidate.tier.value} base_risk={self.base_risk_pct:.4f}% "
            f"base={self.base_weight:.4f} {body} => {self.weight:.4f} "
            f"(risk {self.risk_pct:.4f}%){tail}"
        )


@dataclass(frozen=True, slots=True)
class ShadowFactor:
    """What an agent channel WOULD have done to one allocation. Always SHADOW.

    Written for every candidate whatever the policy's ``enabled`` flag or the
    agent tier, so a pre-registered shadow evaluation has a complete record.
    ``applied_factor`` is what actually multiplied the weight (1.0 unless the
    policy was effective); ``would_be_factor`` is what the policy computed.
    """

    channel: str
    signal_id: str
    strategy_id: str
    instrument: str
    direction: str
    would_be_factor: float
    applied_factor: float
    state: str
    effective: bool
    policy_version: str
    agent_tier: str
    as_of: pd.Timestamp
    artefact_id: str | None = None
    provenance: Provenance = Provenance.SHADOW

    def as_row(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "signal_id": self.signal_id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "direction": self.direction,
            "would_be_factor": self.would_be_factor,
            "applied_factor": self.applied_factor,
            "state": self.state,
            "effective": self.effective,
            "policy_version": self.policy_version,
            "agent_tier": self.agent_tier,
            "as_of": self.as_of.isoformat(),
            "artefact_id": self.artefact_id,
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True, slots=True)
class AllocationResult:
    allocations: tuple[Allocation, ...]
    allocator: str
    config_version: str
    as_of: pd.Timestamp
    notes: tuple[str, ...] = ()
    #: Policy versions and the agent tier in force: stamped on every row.
    stamp: Mapping[str, Any] = field(default_factory=dict)
    #: One row per candidate per agent channel, ``Provenance.SHADOW``.
    shadow: tuple[ShadowFactor, ...] = ()

    @property
    def accepted(self) -> tuple[Allocation, ...]:
        return tuple(a for a in self.allocations if not a.dropped and a.weight > 0)

    @property
    def total_weight(self) -> float:
        return float(sum(a.weight for a in self.accepted))

    @property
    def total_risk_pct(self) -> float:
        return float(sum(a.risk_pct for a in self.accepted))

    def weight_for(self, signal_id: str) -> float:
        for a in self.allocations:
            if a.signal_id == signal_id:
                return a.weight
        return 0.0

    def allocation_for(self, signal_id: str) -> Allocation | None:
        for a in self.allocations:
            if a.signal_id == signal_id:
                return a
        return None

    def as_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "signal_id": a.signal_id,
                "strategy_id": a.candidate.strategy_id,
                "instrument": a.candidate.symbol,
                "tier": a.candidate.tier.value,
                "base_risk_pct": a.base_risk_pct,
                "base_weight": a.base_weight,
                "weight": a.weight,
                "risk_pct": a.risk_pct,
                "dropped": a.dropped,
                "drop_reason": a.drop_reason,
                "reasons": [str(r) for r in a.reasons],
                "trace": [
                    {"step": r.step, "factor": r.factor, "detail": r.detail} for r in a.trace
                ],
                "allocator": self.allocator,
                "config_version": self.config_version,
                **dict(self.stamp),
            }
            for a in self.allocations
        ]


# --------------------------------------------------------------------------
# Allocators
# --------------------------------------------------------------------------


class Allocator(ABC):
    """Produces the BASE weight per candidate, before portfolio adjustments."""

    name: str = "abstract"

    @abstractmethod
    def base_weights(
        self,
        candidates: Sequence[CandidateSignal],
        snapshot: PortfolioSnapshot,
        config: ConstructionConfig,
    ) -> dict[str, float]:
        """Return ``signal_id -> weight`` in [0, max_weight_per_candidate]."""


class EqualRiskAllocator(Allocator):
    """Every candidate gets the same risk budget. The honest baseline."""

    name = "equal_risk"

    def base_weights(
        self,
        candidates: Sequence[CandidateSignal],
        snapshot: PortfolioSnapshot,
        config: ConstructionConfig,
    ) -> dict[str, float]:
        if not candidates:
            return {}
        w = min(config.max_weight_per_candidate, config.max_total_weight / len(candidates))
        return {c.key: w for c in candidates}


class VolatilityParityAllocator(Allocator):
    """Budget proportional to 1/vol, so each candidate contributes similar risk.

    Note this is *inverse volatility*, not true risk parity: it ignores the
    covariance terms. With correlated candidates it under-states the portfolio's
    aggregate risk, which is precisely why the correlation penalty below still
    applies on top of it. Naming it "volatility parity" rather than "risk
    parity" is deliberate.
    """

    name = "volatility_parity"

    def base_weights(
        self,
        candidates: Sequence[CandidateSignal],
        snapshot: PortfolioSnapshot,
        config: ConstructionConfig,
    ) -> dict[str, float]:
        if not candidates:
            return {}
        inv = {c.key: 1.0 / c.annualised_vol for c in candidates}
        total = sum(inv.values())
        if total <= 0:
            return {c.key: 0.0 for c in candidates}
        budget = config.max_total_weight
        return {
            k: min(config.max_weight_per_candidate, budget * v / total) for k, v in inv.items()
        }


class CorrelationPenalisedAllocator(Allocator):
    """Budget proportional to a candidate's diversification contribution.

    Each candidate's score is ``1 - mean(|rho|)`` against the other candidates,
    using the instrument correlation matrix and falling back to
    ``config.unmeasured_correlation`` for unmeasured pairs. A candidate
    perfectly correlated with the rest of the set scores zero and receives
    nothing.
    """

    name = "correlation_penalised"

    def base_weights(
        self,
        candidates: Sequence[CandidateSignal],
        snapshot: PortfolioSnapshot,
        config: ConstructionConfig,
    ) -> dict[str, float]:
        if not candidates:
            return {}
        if len(candidates) == 1:
            return {candidates[0].key: min(config.max_weight_per_candidate, config.max_total_weight)}

        corr = snapshot.instrument_correlation
        scores: dict[str, float] = {}
        for c in candidates:
            others = [o for o in candidates if o.key != c.key]
            rhos = [
                abs(_lookup_corr(corr, c.symbol, o.symbol, config.unmeasured_correlation))
                for o in others
            ]
            mean_rho = sum(rhos) / len(rhos) if rhos else 0.0
            scores[c.key] = max(0.0, 1.0 - mean_rho)

        total = sum(scores.values())
        if total <= 0:
            return {c.key: 0.0 for c in candidates}
        budget = config.max_total_weight
        return {
            k: min(config.max_weight_per_candidate, budget * v / total) for k, v in scores.items()
        }


_ALLOCATORS: dict[str, type[Allocator]] = {
    EqualRiskAllocator.name: EqualRiskAllocator,
    VolatilityParityAllocator.name: VolatilityParityAllocator,
    CorrelationPenalisedAllocator.name: CorrelationPenalisedAllocator,
}


def get_allocator(name: str) -> Allocator:
    if name not in _ALLOCATORS:
        raise KeyError(f"Unknown allocator {name!r}. Known: {sorted(_ALLOCATORS)}")
    return _ALLOCATORS[name]()


def _lookup_corr(corr: CorrelationMatrix, a: str, b: str, fallback: float) -> float:
    if a == b:
        return 1.0
    if a in corr.labels and b in corr.labels:
        return corr.get(a, b)
    return fallback


# --------------------------------------------------------------------------
# The constructor
# --------------------------------------------------------------------------


class PortfolioConstructor:
    """Runs an allocator, then the fixed adjustment pipeline, recording each step.

    The pipeline order is fixed and deliberate: eligibility gates first (so a
    quarantined strategy never consumes budget), then per-candidate quality
    scaling, then book-level constraints, then the agent conviction channel;
    then three batch passes: the total weight budget, the total risk cap and
    the final down-only tier cap. Changing the order changes the answer, so it
    is not configurable.

    Every per-candidate factor is at most 1.0 and the last pass clamps each
    weight to ``max_scale_vs_tier`` (<= 1.0), so ``Allocation.risk_pct <=
    Allocation.base_risk_pct`` always (``tests/unit/test_construction_policy.py``,
    property test).
    """

    PIPELINE = (
        "lifecycle",
        "tier",
        "health",
        "confidence",
        "instrument_correlation",
        "strategy_correlation",
        "instrument_concentration",
        "asset_class_concentration",
        "currency_exposure",
        "volatility_target",
        "margin",
        "drawdown",
        "regime",
        "correlated_risk_budget",
        "conviction",
    )
    #: Batch passes run after the per-candidate pipeline, in this order.
    BATCH_PASSES = ("total_budget", "total_risk_cap", "tier_cap")
    #: Steps that need the weight so far (they compare planned risk to a budget).
    _WEIGHT_AWARE = frozenset({"correlated_risk_budget"})
    #: Steps recorded in ``reasons`` even when their factor is exactly 1.0.
    _ALWAYS_REASON = frozenset({"conviction"})

    def __init__(
        self,
        allocator: Allocator | str = "equal_risk",
        config: ConstructionConfig | None = None,
        *,
        agent_tier: TierReading | AgentInfluenceTier | None = None,
    ) -> None:
        self.allocator = get_allocator(allocator) if isinstance(allocator, str) else allocator
        self.config = config or ConstructionConfig()
        if agent_tier is None:
            reading = TierReading.default("not_supplied")
        elif isinstance(agent_tier, AgentInfluenceTier):
            reading = TierReading(tier=agent_tier, source="explicit")
        else:
            reading = agent_tier
        self.agent_tier: TierReading = reading
        self._shadow: list[ShadowFactor] = []

    # ------------------------------------------------------------------

    @property
    def conviction_effective(self) -> bool:
        """The conviction policy changes sizes only if enabled AND the tier permits."""
        return bool(self.config.conviction.enabled and self.agent_tier.tier.permits_dampen)

    @property
    def conviction_gated_by_tier(self) -> bool:
        """Enabled in config but held in shadow by the tier: the caller must alert."""
        return bool(self.config.conviction.enabled and not self.agent_tier.tier.permits_dampen)

    def stamp(self, snapshot: PortfolioSnapshot | None = None) -> dict[str, Any]:
        """Everything a reader needs to reproduce or distrust an allocation."""
        cfg = self.config
        out: dict[str, Any] = {
            "construction_version": cfg.version,
            "allocator": self.allocator.name,
            **cfg.conviction.stamp(),
            "conviction_effective": self.conviction_effective,
            "conviction_gated_by_tier": self.conviction_gated_by_tier,
            **self.agent_tier.stamp(),
            "max_total_risk_pct": cfg.max_total_risk_pct,
            "max_correlated_risk_pct": cfg.max_correlated_risk_pct,
            "max_scale_vs_tier": cfg.max_scale_vs_tier,
            "risk_ceiling_pct": cfg.risk_ceiling_pct,
        }
        if snapshot is not None:
            out["open_risk_measured"] = snapshot.open_risk_by_instrument is not None
            out["open_risk_pct"] = snapshot.open_risk_pct
            out["drawdown_pct"] = snapshot.drawdown_pct
        return out

    def allocate(
        self,
        candidates: Sequence[CandidateSignal],
        snapshot: PortfolioSnapshot,
    ) -> AllocationResult:
        cfg = self.config
        notes: list[str] = []
        stamp = self.stamp(snapshot)
        self._shadow = []

        if not candidates:
            return AllocationResult((), self.allocator.name, cfg.version, snapshot.as_of,
                                    ("no candidates",), stamp)

        # Deterministic order: two identical candidate sets must allocate
        # identically whatever order they arrived in.
        ordered = tuple(
            sorted(candidates, key=lambda c: (c.strategy_id, c.symbol, c.signal.signal_id))
        )

        base = self.allocator.base_weights(ordered, snapshot, cfg)
        allocations: list[Allocation] = []

        for c in ordered:
            b = float(base.get(c.key, 0.0))
            w = b
            reasons: list[AllocationReason] = []
            trace: list[AllocationReason] = []
            dropped = False
            drop_reason: str | None = None

            for step in self.PIPELINE:
                if dropped:
                    break
                factor, detail, hard_drop = self._factor(step, c, snapshot, cfg, w)
                if factor > 1.0:  # pragma: no cover - every handler returns <= 1
                    raise AssertionError(f"{step} returned factor {factor} > 1.0")
                if hard_drop:
                    dropped = True
                    drop_reason = f"{step}:{detail}"
                    reason = AllocationReason(step, 0.0, detail)
                    reasons.append(reason)
                    trace.append(reason)
                    w = 0.0
                    break
                reason = AllocationReason(step, factor, detail)
                trace.append(reason)
                if factor != 1.0 or step in self._ALWAYS_REASON:
                    reasons.append(reason)
                w *= factor

            if not dropped and w < cfg.min_weight_to_trade:
                dropped = True
                drop_reason = f"below_min_weight:{w:.4f}<{cfg.min_weight_to_trade}"
                w = 0.0

            allocations.append(
                Allocation(
                    candidate=c,
                    weight=min(w, cfg.max_weight_per_candidate),
                    base_weight=b,
                    reasons=tuple(reasons),
                    dropped=dropped,
                    drop_reason=drop_reason,
                    base_risk_pct=cfg.base_risk_pct(c.tier),
                    trace=tuple(trace),
                )
            )

        allocations = self._apply_total_budget(allocations, cfg, notes)
        allocations = self._apply_total_risk_cap(allocations, snapshot, cfg, notes)
        allocations = self._apply_tier_cap(allocations, cfg)
        return AllocationResult(
            tuple(allocations),
            self.allocator.name,
            cfg.version,
            snapshot.as_of,
            tuple(notes),
            stamp,
            tuple(self._shadow),
        )

    # ------------------------------------------------------------- factors

    def _factor(
        self,
        step: str,
        c: CandidateSignal,
        snap: PortfolioSnapshot,
        cfg: ConstructionConfig,
        w: float = 1.0,
    ) -> tuple[float, str, bool]:
        """Return ``(multiplicative_factor, detail, hard_drop)`` for one step."""
        handler = getattr(self, f"_step_{step}")
        if step in self._WEIGHT_AWARE:
            return handler(c, snap, cfg, w)
        return handler(c, snap, cfg)

    def _step_lifecycle(self, c, snap, cfg):
        if c.lifecycle not in cfg.allocatable_lifecycles:
            return 0.0, c.lifecycle.value, True
        return float(cfg.lifecycle_scalars.get(c.lifecycle, 1.0)), c.lifecycle.value, False

    def _step_tier(self, c, snap, cfg):
        """Governor 1: the tier sets the base risk and caps concurrent positions."""
        cap = cfg.tier_max_concurrent.get(c.tier)
        held = sum(1 for p in snap.open_positions if p.strategy_id == c.strategy_id)
        base = cfg.base_risk_pct(c.tier)
        detail = f"tier={c.tier.value} base_risk={base:.4f}% open={held}/{cap}"
        if cap is not None and held >= cap:
            return 0.0, f"{detail} at_concurrency_cap", True
        return 1.0, detail, False

    def _step_health(self, c, snap, cfg):
        # Degradation scales risk down linearly and a fully degraded strategy is
        # dropped rather than given a tiny allocation: a broken strategy in
        # small size is still a broken strategy.
        if c.health <= 0.0:
            return 0.0, "fully_degraded", True
        return float(c.health), f"health={c.health:.2f}", False

    def _step_confidence(self, c, snap, cfg):
        conf = max(0.0, min(1.0, float(c.signal.confidence)))
        if conf <= 0.0:
            return 0.0, "zero_confidence", True
        return conf, f"confidence={conf:.2f}", False

    def _step_instrument_correlation(self, c, snap, cfg):
        held = sorted({p.instrument for p in snap.open_positions})
        if not held:
            return 1.0, "no_open_book", False
        rhos = [
            abs(_lookup_corr(snap.instrument_correlation, c.symbol, h, cfg.unmeasured_correlation))
            for h in held
        ]
        worst = max(rhos)
        if worst >= cfg.correlation_hard_cap:
            return 0.0, f"rho={worst:.2f}>=hard_cap", True
        return _corr_factor(worst, cfg), f"max_rho={worst:.2f}", False

    def _step_strategy_correlation(self, c, snap, cfg):
        others = sorted({p.strategy_id for p in snap.open_positions if p.strategy_id})
        others = [s for s in others if s != c.strategy_id]
        if not others:
            return 1.0, "no_other_strategies", False
        rhos = [
            abs(
                _lookup_corr(
                    snap.strategy_correlation, c.strategy_id, s, cfg.unmeasured_correlation
                )
            )
            for s in others
        ]
        worst = max(rhos)
        if worst >= cfg.correlation_hard_cap:
            return 0.0, f"strategy_rho={worst:.2f}>=hard_cap", True
        return _corr_factor(worst, cfg), f"max_strategy_rho={worst:.2f}", False

    def _step_instrument_concentration(self, c, snap, cfg):
        eq = snap.equity
        if eq <= 0:
            return 0.0, "non_positive_equity", True
        used = abs(float(snap.instrument_exposure.get(c.symbol, 0.0)))
        cap = cfg.max_instrument_notional_pct * eq
        return _headroom_factor(used, cap, f"{c.symbol}_notional")

    def _step_asset_class_concentration(self, c, snap, cfg):
        eq = snap.equity
        if eq <= 0:
            return 0.0, "non_positive_equity", True
        cls = c.instrument.asset_class
        key = cls.value if isinstance(cls, AssetClass) else str(cls)
        used = abs(float(snap.asset_class_exposure.get(key, 0.0)))
        cap = cfg.max_asset_class_notional_pct * eq
        return _headroom_factor(used, cap, f"{key}_notional")

    def _step_currency_exposure(self, c, snap, cfg):
        eq = snap.equity
        if eq <= 0:
            return 0.0, "non_positive_equity", True
        instr = c.instrument
        cap = cfg.max_currency_notional_pct * eq
        worst_factor = 1.0
        worst_detail = "currency_ok"
        for ccy in (instr.base, instr.quote):
            used = abs(float(snap.currency_exposure.get(ccy, 0.0)))
            f, detail, drop = _headroom_factor(used, cap, f"{ccy}_net")
            if drop:
                return f, detail, drop
            if f < worst_factor:
                worst_factor, worst_detail = f, detail
        return worst_factor, worst_detail, False

    def _step_volatility_target(self, c, snap, cfg):
        target = cfg.target_portfolio_vol
        if target is None or target <= 0:
            return 1.0, "vol_targeting_off", False
        realised = snap.realised_portfolio_vol
        if realised <= 0:
            # No measurement yet. Neutral rather than a free scale-up: an
            # unmeasured portfolio is not a low-volatility portfolio.
            return 1.0, "no_realised_vol", False
        scale = min(cfg.vol_target_max_scale, 1.0, target / realised)
        return float(scale), f"target/realised={target:.3f}/{realised:.3f}", False

    def _step_margin(self, c, snap, cfg):
        util = snap.margin_utilisation
        cap = cfg.max_margin_utilisation
        if cap <= 0:
            return 1.0, "margin_cap_off", False
        if util >= cap:
            return 0.0, f"margin_utilisation={util:.2f}>=cap", True
        return max(0.0, 1.0 - util / cap), f"margin_utilisation={util:.2f}", False

    def _step_drawdown(self, c, snap, cfg):
        """Governor 3, as a step function. The boundary belongs to the stricter band."""
        dd = snap.drawdown_pct
        if dd >= cfg.drawdown_zero_pct:
            return 0.0, f"dd={dd:.2f}%>={cfg.drawdown_zero_pct}%:flatten_required", True
        if dd >= cfg.drawdown_pause_pct:
            return 0.0, f"dd={dd:.2f}%>={cfg.drawdown_pause_pct}%:pause", True
        if dd >= cfg.drawdown_severe_pct:
            if c.tier is StrategyTier.PROBATIONARY:
                return 0.0, f"dd={dd:.2f}%>={cfg.drawdown_severe_pct}%:probationary_suspended", True
            return float(cfg.drawdown_severe_factor), f"dd={dd:.2f}%:severe", False
        if dd >= cfg.drawdown_derisk_start_pct:
            return float(cfg.drawdown_derisk_factor), f"dd={dd:.2f}%:derisk", False
        return 1.0, f"dd={dd:.2f}%", False

    def _step_regime(self, c, snap, cfg):
        raw = c.regime if c.regime is not None else snap.regime
        regime = coarse_regime(raw)
        factor = float(cfg.regime_scalars.get(regime, cfg.regime_scalars.get("unknown", 1.0)))
        detail = f"regime={regime}" if regime == raw else f"regime={regime} (from {raw})"
        if factor <= 0:
            return 0.0, detail, True
        return min(1.0, factor), detail, False

    def _step_correlated_risk_budget(self, c, snap, cfg, w):
        """Governor 2, correlation-aware: correlated open risk plus this candidate.

        ``sum_i |rho(c, i)| * open_risk_i`` with the same instrument at 1.0 and
        an unmeasured pair at ``unmeasured_correlation`` (0.30, not 0.0). The
        candidate is scaled so that total fits under ``max_correlated_risk_pct``.
        """
        risks = snap.open_risk_by_instrument
        if risks is None:
            return 1.0, "open_risk_unmeasured", False
        eq = snap.equity
        if eq <= 0:
            return 0.0, "non_positive_equity", True
        correlated = 0.0
        for sym in sorted(risks):
            r = max(0.0, float(risks[sym]))
            if r <= 0:
                continue
            rho = abs(
                _lookup_corr(snap.instrument_correlation, c.symbol, sym, cfg.unmeasured_correlation)
            )
            correlated += rho * r / eq * 100.0
        headroom = cfg.max_correlated_risk_pct - correlated
        planned = cfg.base_risk_pct(c.tier) * w
        detail = (
            f"correlated_open={correlated:.4f}% planned={planned:.4f}% "
            f"cap={cfg.max_correlated_risk_pct}%"
        )
        if headroom <= 0:
            return 0.0, f"{detail} no_headroom", True
        if planned <= 0 or planned <= headroom:
            return 1.0, detail, False
        return float(headroom / planned), detail, False

    def _step_conviction(self, c, snap, cfg):
        """The ONLY consumer of a ConvictionReading. Down only; shadow unless T3.

        The policy computes a would-be factor for every candidate. It is
        applied only when the policy is enabled AND the agent tier permits
        dampening; otherwise the applied factor is exactly 1.0. Either way a
        :class:`ShadowFactor` is recorded with ``Provenance.SHADOW``.
        """
        policy = cfg.conviction
        reading = c.conviction
        would_be, state = _conviction_factor(policy, reading, c.signal, snap.as_of)
        effective = self.conviction_effective
        applied = would_be if effective else 1.0
        artefact = None if reading is None else reading.artefact_id
        self._shadow.append(
            ShadowFactor(
                channel="conviction",
                signal_id=c.signal.signal_id,
                strategy_id=c.strategy_id,
                instrument=c.symbol,
                direction=c.signal.direction.value,
                would_be_factor=would_be,
                applied_factor=applied,
                state=state,
                effective=effective,
                policy_version=policy.version,
                agent_tier=self.agent_tier.tier.value,
                as_of=snap.as_of,
                artefact_id=artefact,
            )
        )
        detail = (
            f"{state} would_be={would_be:.4f} applied={applied:.4f} "
            f"policy={policy.version} enabled={policy.enabled} "
            f"tier={self.agent_tier.tier.value}"
            + (f" artefact={artefact}" if artefact else "")
            + ("" if effective else " SHADOW")
        )
        return applied, detail, False

    # ------------------------------------------------------- batch passes

    def _apply_total_budget(
        self,
        allocations: list[Allocation],
        cfg: ConstructionConfig,
        notes: list[str],
    ) -> list[Allocation]:
        live = [a for a in allocations if not a.dropped and a.weight > 0]
        total = sum(a.weight for a in live)
        if total <= cfg.max_total_weight or total <= 0:
            return allocations

        scale = cfg.max_total_weight / total
        notes.append(
            f"total weight {total:.4f} exceeded max_total_weight "
            f"{cfg.max_total_weight}; scaled all by {scale:.4f}"
        )
        return [
            _rescaled(a, scale, AllocationReason("total_budget", scale, f"sum={total:.4f}"))
            if not a.dropped and a.weight > 0 else a
            for a in allocations
        ]

    def _apply_total_risk_cap(
        self,
        allocations: list[Allocation],
        snap: PortfolioSnapshot,
        cfg: ConstructionConfig,
        notes: list[str],
    ) -> list[Allocation]:
        """Governor 2: open risk plus the whole batch's new risk <= 6% of equity."""
        live = [a for a in allocations if not a.dropped and a.weight > 0]
        if not live:
            return allocations
        open_pct = snap.open_risk_pct
        if open_pct is None:
            notes.append(
                "open risk NOT MEASURED: total_risk_cap applied to this batch's new "
                "risk only; the risk gateway's max_account_risk check still runs"
            )
            open_pct = 0.0
        planned = sum(a.risk_pct for a in live)
        headroom = cfg.max_total_risk_pct - open_pct
        detail = (
            f"open={open_pct:.4f}% batch={planned:.4f}% cap={cfg.max_total_risk_pct}%"
        )
        if headroom <= 0:
            notes.append(f"total_risk_cap reached ({detail}); no new risk allocated")
            return [
                _dropped(a, "total_risk_cap", detail) if not a.dropped and a.weight > 0 else a
                for a in allocations
            ]
        if planned <= headroom:
            return allocations
        scale = headroom / planned
        notes.append(f"total_risk_cap bound ({detail}); scaled all by {scale:.4f}")
        return [
            _rescaled(a, scale, AllocationReason("total_risk_cap", scale, detail))
            if not a.dropped and a.weight > 0 else a
            for a in allocations
        ]

    def _apply_tier_cap(
        self, allocations: list[Allocation], cfg: ConstructionConfig
    ) -> list[Allocation]:
        """The last word: no weight above ``max_scale_vs_tier`` (<= 1.0)."""
        out: list[Allocation] = []
        for a in allocations:
            if a.dropped or a.weight <= cfg.max_scale_vs_tier:
                out.append(a)
                continue
            factor = cfg.max_scale_vs_tier / a.weight
            out.append(
                _rescaled(
                    a, factor,
                    AllocationReason(
                        "tier_cap", factor,
                        f"weight={a.weight:.4f}>max_scale_vs_tier={cfg.max_scale_vs_tier}",
                    ),
                    exact=cfg.max_scale_vs_tier,
                )
            )
        return out


def _rescaled(
    a: Allocation, scale: float, reason: AllocationReason, *, exact: float | None = None
) -> Allocation:
    return replace(
        a,
        weight=exact if exact is not None else a.weight * scale,
        reasons=(*a.reasons, reason),
        trace=(*a.trace, reason),
    )


def _dropped(a: Allocation, step: str, detail: str) -> Allocation:
    reason = AllocationReason(step, 0.0, detail)
    return replace(
        a,
        weight=0.0,
        dropped=True,
        drop_reason=f"{step}:{detail}",
        reasons=(*a.reasons, reason),
        trace=(*a.trace, reason),
    )


#: The coarse labels ``ConstructionConfig.regime_scalars`` is keyed on.
COARSE_REGIMES: tuple[str, ...] = ("trend", "range", "high_vol", "crisis", "unknown")


def coarse_regime(label: str | None) -> str:
    """Map a regime label to one of :data:`COARSE_REGIMES`. Deterministic.

    ``marketstate`` reports a five-axis key, ``direction|volatility|persistence|
    liquidity|stress`` (e.g. ``up|high|trending|deep|calm``), which never
    matched a key of ``regime_scalars``, so under v1 every real regime fell
    through to ``unknown`` (0.6). The mapping, most restrictive first:

    * a malformed key, or an ``unknown`` volatility, persistence or stress axis
      -> ``unknown``;
    * stress ``stressed`` -> ``crisis``;
    * volatility ``high``/``extreme``, or stress ``elevated`` -> ``high_vol``;
    * persistence ``trending`` -> ``trend``;
    * persistence ``mean_reverting`` or ``random`` -> ``range``.

    A label that is already coarse passes through; anything else is ``unknown``.
    ``portfolio`` may not import ``marketstate``, so this parses the string.
    """
    if not label:
        return "unknown"
    if label in COARSE_REGIMES:
        return label
    parts = str(label).split("|")
    if len(parts) != 5:
        return "unknown"
    _direction, volatility, persistence, _liquidity, stress = parts
    if "unknown" in (volatility, persistence, stress):
        return "unknown"
    if stress == "stressed":
        return "crisis"
    if volatility in ("high", "extreme") or stress == "elevated":
        return "high_vol"
    if persistence == "trending":
        return "trend"
    if persistence in ("mean_reverting", "random"):
        return "range"
    return "unknown"


def _conviction_factor(
    policy: ConvictionPolicy,
    reading: ConvictionReading | None,
    signal: Signal,
    at: pd.Timestamp,
) -> tuple[float, str]:
    """Map a reading to ``(factor, state)``. Called only by ``_step_conviction``.

    Every branch that is not "disagrees" returns exactly 1.0. The factor is
    never above ``policy.max_factor`` (<= 1.0) and never below ``policy.floor``
    (>= 0.5).
    """
    if reading is None:
        return 1.0, "missing"
    if reading.instrument != signal.instrument:
        return 1.0, "instrument_mismatch"
    when = pd.Timestamp(at)
    if when.tzinfo is None:
        return 1.0, "clock_not_utc"
    if reading.as_of > when:
        return 1.0, "not_yet_available"
    if when >= reading.valid_until:
        return 1.0, "stale"
    if (reading.valid_until - reading.as_of) > pd.Timedelta(hours=policy.max_validity_hours):
        return 1.0, "validity_exceeds_policy"
    if reading.stance == "none":
        return 1.0, "no_view"
    if reading.stance == signal.direction.value:
        return min(1.0, policy.max_factor), "agrees"
    factor = (
        policy.strong_disagreement_factor
        if reading.strength >= 2
        else policy.weak_disagreement_factor
    )
    factor = max(policy.floor, min(factor, policy.max_factor, 1.0))
    return float(factor), f"disagrees_strength_{reading.strength}"


def _corr_factor(rho: float, cfg: ConstructionConfig) -> float:
    """Linear taper from 1.0 at the soft cap to 0.0 at the hard cap."""
    if rho <= cfg.correlation_soft_cap:
        return 1.0
    span = cfg.correlation_hard_cap - cfg.correlation_soft_cap
    if span <= 0:
        return 0.0
    return max(0.0, 1.0 - (rho - cfg.correlation_soft_cap) / span)


def _headroom_factor(used: float, cap: float, label: str) -> tuple[float, str, bool]:
    """Scale by remaining headroom against a cap; drop when the cap is reached."""
    if cap <= 0:
        return 0.0, f"{label}:cap<=0", True
    if used >= cap:
        return 0.0, f"{label}={used:.0f}>=cap={cap:.0f}", True
    headroom = 1.0 - used / cap
    if not math.isfinite(headroom):
        return 0.0, f"{label}:non_finite", True
    return float(headroom), f"{label}_headroom={headroom:.2f}", False
