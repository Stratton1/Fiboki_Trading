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

Weights are fractions of the per-trade risk budget, not fractions of equity.
A weight of 1.0 means "the full ``risk_fraction``"; the sum across candidates
is capped by :attr:`ConstructionConfig.max_total_weight`, which is what stops
twenty simultaneous candidates from spending twenty budgets.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.contracts import AccountState, Position, Signal
from fiboki.core.enums import AssetClass, StrategyLifecycle
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument

__all__ = [
    "Allocation",
    "AllocationReason",
    "AllocationResult",
    "Allocator",
    "CandidateSignal",
    "ConstructionConfig",
    "CorrelationMatrix",
    "CorrelationPenalisedAllocator",
    "EqualRiskAllocator",
    "PortfolioConstructor",
    "PortfolioSnapshot",
    "VolatilityParityAllocator",
    "get_allocator",
]


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


@dataclass(frozen=True, slots=True)
class ConstructionConfig:
    """Portfolio construction policy. Versioned; stamped onto every result."""

    version: str = "construction_v1"

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

    #: Volatility targeting. ``None`` disables it.
    target_portfolio_vol: float | None = 0.12
    vol_target_max_scale: float = 1.50

    max_margin_utilisation: float = 0.50

    #: Linear de-risking between these drawdown percentages; at or beyond
    #: ``drawdown_zero_pct`` no new risk is allocated at all.
    drawdown_derisk_start_pct: float = 5.0
    drawdown_zero_pct: float = 20.0

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
    reasons: tuple[AllocationReason, ...]
    dropped: bool = False
    drop_reason: str | None = None

    @property
    def signal_id(self) -> str:
        return self.candidate.signal.signal_id

    @property
    def explanation(self) -> str:
        head = f"{self.candidate.strategy_id}/{self.candidate.symbol}"
        body = " -> ".join(str(r) for r in self.reasons)
        tail = f" DROPPED:{self.drop_reason}" if self.dropped else ""
        return f"{head}: base={self.base_weight:.4f} {body} => {self.weight:.4f}{tail}"


@dataclass(frozen=True, slots=True)
class AllocationResult:
    allocations: tuple[Allocation, ...]
    allocator: str
    config_version: str
    as_of: pd.Timestamp
    notes: tuple[str, ...] = ()

    @property
    def accepted(self) -> tuple[Allocation, ...]:
        return tuple(a for a in self.allocations if not a.dropped and a.weight > 0)

    @property
    def total_weight(self) -> float:
        return float(sum(a.weight for a in self.accepted))

    def weight_for(self, signal_id: str) -> float:
        for a in self.allocations:
            if a.signal_id == signal_id:
                return a.weight
        return 0.0

    def as_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "signal_id": a.signal_id,
                "strategy_id": a.candidate.strategy_id,
                "instrument": a.candidate.symbol,
                "base_weight": a.base_weight,
                "weight": a.weight,
                "dropped": a.dropped,
                "drop_reason": a.drop_reason,
                "reasons": [str(r) for r in a.reasons],
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
    scaling, then book-level constraints, then the global budget cap. Changing
    the order changes the answer, so it is not configurable.
    """

    PIPELINE = (
        "lifecycle",
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
    )

    def __init__(
        self,
        allocator: Allocator | str = "equal_risk",
        config: ConstructionConfig | None = None,
    ) -> None:
        self.allocator = get_allocator(allocator) if isinstance(allocator, str) else allocator
        self.config = config or ConstructionConfig()

    # ------------------------------------------------------------------

    def allocate(
        self,
        candidates: Sequence[CandidateSignal],
        snapshot: PortfolioSnapshot,
    ) -> AllocationResult:
        cfg = self.config
        notes: list[str] = []

        if not candidates:
            return AllocationResult((), self.allocator.name, cfg.version, snapshot.as_of,
                                    ("no candidates",))

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
            dropped = False
            drop_reason: str | None = None

            for step in self.PIPELINE:
                if dropped:
                    break
                factor, detail, hard_drop = self._factor(step, c, snapshot, cfg)
                if hard_drop:
                    dropped = True
                    drop_reason = f"{step}:{detail}"
                    reasons.append(AllocationReason(step, 0.0, detail))
                    w = 0.0
                    break
                if factor != 1.0:
                    reasons.append(AllocationReason(step, factor, detail))
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
                )
            )

        allocations = self._apply_total_budget(allocations, cfg, notes)
        return AllocationResult(
            tuple(allocations), self.allocator.name, cfg.version, snapshot.as_of, tuple(notes)
        )

    # ------------------------------------------------------------- factors

    def _factor(
        self,
        step: str,
        c: CandidateSignal,
        snap: PortfolioSnapshot,
        cfg: ConstructionConfig,
    ) -> tuple[float, str, bool]:
        """Return ``(multiplicative_factor, detail, hard_drop)`` for one step."""
        handler = getattr(self, f"_step_{step}")
        return handler(c, snap, cfg)

    def _step_lifecycle(self, c, snap, cfg):
        if c.lifecycle not in cfg.allocatable_lifecycles:
            return 0.0, c.lifecycle.value, True
        return float(cfg.lifecycle_scalars.get(c.lifecycle, 1.0)), c.lifecycle.value, False

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
        scale = min(cfg.vol_target_max_scale, target / realised)
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
        dd = snap.drawdown_pct
        if dd <= cfg.drawdown_derisk_start_pct:
            return 1.0, f"dd={dd:.2f}%", False
        if dd >= cfg.drawdown_zero_pct:
            return 0.0, f"dd={dd:.2f}%>=zero", True
        span = cfg.drawdown_zero_pct - cfg.drawdown_derisk_start_pct
        return (
            float(1.0 - (dd - cfg.drawdown_derisk_start_pct) / span),
            f"dd={dd:.2f}%",
            False,
        )

    def _step_regime(self, c, snap, cfg):
        factor = float(cfg.regime_scalars.get(snap.regime, cfg.regime_scalars.get("unknown", 1.0)))
        if factor <= 0:
            return 0.0, f"regime={snap.regime}", True
        return factor, f"regime={snap.regime}", False

    # ------------------------------------------------------- total budget

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
        out: list[Allocation] = []
        for a in allocations:
            if a.dropped or a.weight <= 0:
                out.append(a)
                continue
            out.append(
                Allocation(
                    candidate=a.candidate,
                    weight=a.weight * scale,
                    base_weight=a.base_weight,
                    reasons=(
                        *a.reasons,
                        AllocationReason("total_budget", scale, f"sum={total:.4f}"),
                    ),
                    dropped=False,
                    drop_reason=None,
                )
            )
        return out


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
