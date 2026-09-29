"""Domain contracts shared by every layer.

Layering, enforced by tests/unit/test_layering.py:

    ALPHA      strategy -> Signal          "I think price goes up here"
    PORTFOLIO  sizing   -> TradePlan       "therefore hold exactly this much"
    RISK       gateway  -> RiskDecision    "and you are/aren't allowed to"
    EXECUTION  adapter  -> Order -> Fill   "here is what the venue actually did"

A Signal carries NO size. A TradePlan carries exactly ONE size, decided once.
Adapters convert units and never re-decide size — V1 sized the same signal three
times (bot, router, IG adapter) and the ledger disagreed with the broker.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

import pandas as pd

from fiboki.core.enums import (
    Direction,
    ExecutionMode,
    ExitReason,
    OrderType,
    Provenance,
)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------- ALPHA


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy's directional opinion at a closed bar. Carries no size."""

    strategy_id: str
    instrument: str
    timeframe: str
    direction: Direction
    bar_time: pd.Timestamp
    reference_price: float
    stop_price: float
    take_profit_prices: tuple[float, ...] = ()
    #: Fraction of the position each take-profit leg closes, aligned 1:1 with
    #: ``take_profit_prices``. Empty means "unallocated", which the engine reads
    #: as the pre-multi-leg behaviour: the FIRST price is a full-size target and
    #: the rest are ignored. Only the compiler can populate this, because only
    #: the compiler knows which declared leg produced which price once the
    #: prices have been sorted by distance and de-duplicated.
    take_profit_allocations: tuple[float, ...] = ()
    confidence: float = 1.0
    rationale: str = ""
    features: dict[str, float] = field(default_factory=dict)
    signal_id: str = field(default_factory=lambda: _uid("sig"))

    def __post_init__(self) -> None:
        if self.stop_price <= 0 or self.reference_price <= 0:
            raise ValueError("Signal requires positive reference and stop prices")
        # A stop on the wrong side is not a tradeable opinion. V1 allowed this and
        # relied on a downstream sanitiser; V2 refuses it at the source.
        if self.direction is Direction.LONG and self.stop_price >= self.reference_price:
            raise ValueError(
                f"LONG stop {self.stop_price} must be below entry {self.reference_price}"
            )
        if self.direction is Direction.SHORT and self.stop_price <= self.reference_price:
            raise ValueError(
                f"SHORT stop {self.stop_price} must be above entry {self.reference_price}"
            )
        for tp in self.take_profit_prices:
            if self.direction is Direction.LONG and tp <= self.reference_price:
                raise ValueError(f"LONG take-profit {tp} must be above entry")
            if self.direction is Direction.SHORT and tp >= self.reference_price:
                raise ValueError(f"SHORT take-profit {tp} must be below entry")
        if self.take_profit_allocations:
            if len(self.take_profit_allocations) != len(self.take_profit_prices):
                raise ValueError(
                    f"take_profit_allocations has {len(self.take_profit_allocations)} "
                    f"entries for {len(self.take_profit_prices)} prices. A leg whose "
                    "allocation cannot be named is a leg the engine would have to "
                    "guess a size for."
                )
            if any(a <= 0.0 or a > 1.0 for a in self.take_profit_allocations):
                raise ValueError("each take-profit allocation must lie in (0, 1]")
            total = sum(self.take_profit_allocations)
            if total > 1.0 + 1e-9:
                raise ValueError(
                    f"take-profit allocations sum to {total:.6f} > 1.0; a position "
                    "cannot be closed more than once"
                )
        if self.bar_time.tzinfo is None:
            raise ValueError("Signal.bar_time must be timezone-aware UTC")

    @property
    def stop_distance(self) -> float:
        return abs(self.reference_price - self.stop_price)


# ------------------------------------------------------------ PORTFOLIO


@dataclass(frozen=True, slots=True)
class TradePlan:
    """The single authoritative sizing decision for a signal."""

    signal: Signal
    size: float
    account_ccy: str
    risk_amount: float
    sizing_basis: str
    max_leverage_applied: float
    portfolio_weight: float = 1.0
    plan_id: str = field(default_factory=lambda: _uid("plan"))

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("TradePlan.size must be positive; reject upstream instead")

    @property
    def direction(self) -> Direction:
        return self.signal.direction

    @property
    def instrument(self) -> str:
        return self.signal.instrument


# ----------------------------------------------------------------- RISK


#: Reason prefixes that record what a SHADOW control would have done. A shadow
#: reason is evidence for a later evaluation, never a block: ``allowed`` is
#: decided by :attr:`RiskDecision.blocking_reasons` alone. Adding a prefix here
#: is how a new shadow channel becomes non-blocking, so the list is short and
#: every entry names its channel.
SHADOW_REASON_PREFIXES: tuple[str, ...] = ("event_veto_shadow:",)


def is_shadow_reason(reason: str) -> bool:
    return reason.startswith(SHADOW_REASON_PREFIXES)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()
    adjusted_size: float | None = None
    checks_run: tuple[str, ...] = ()
    decided_at: pd.Timestamp | None = None

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        """The reasons that decide ``allowed``: everything except shadow notes."""
        return tuple(r for r in self.reasons if not is_shadow_reason(r))

    @property
    def shadow_reasons(self) -> tuple[str, ...]:
        """What shadow controls would have done. Never blocks."""
        return tuple(r for r in self.reasons if is_shadow_reason(r))

    @staticmethod
    def allow(checks: tuple[str, ...]) -> RiskDecision:
        return RiskDecision(True, (), None, checks)

    @staticmethod
    def block(reason: str, checks: tuple[str, ...] = ()) -> RiskDecision:
        return RiskDecision(False, (reason,), None, checks)


# --------------------------------------------------------------- EVENTS
#
# The event channel (docs/v2/AGENTIC_INTEGRATION_PLAN.md §4). An LLM classifies
# recorded headlines; its output is filed in a quarantined store; a
# deterministic policy in ``marketstate/events.py`` reads it and may, when
# enabled, VETO a new entry. These contracts are what crosses that boundary.
# They carry no free text, no prices, no stops and no sizes: the model's
# rationale stays in the quarantined table, and nothing here can express an
# order, a size or a limit.

#: The closed event vocabulary. ``none`` is a real answer ("not market news").
EventType = Literal[
    "central_bank",
    "macro_release",
    "geopolitical",
    "market_structure",
    "liquidity_shock",
    "natural_disaster",
    "other",
    "none",
]
#: The closed exposure vocabulary an annotation may name. Currency codes for
#: the eight FX currencies; asset buckets for metals, oil and the four index
#: regions. An instrument maps onto these deterministically
#: (``marketstate.events.instrument_buckets``); the model never names a symbol.
EventBucket = Literal[
    "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD",
    "XAU", "XAG", "OIL",
    "INDEX_US", "INDEX_EU", "INDEX_UK", "INDEX_JP",
]
EVENT_TYPES: tuple[str, ...] = get_args(EventType)
EVENT_BUCKETS: tuple[str, ...] = get_args(EventBucket)
EVENT_SEVERITIES: tuple[int, ...] = (0, 1, 2, 3)


def _aware(name: str, ts: pd.Timestamp) -> None:
    if not isinstance(ts, pd.Timestamp) or ts.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware UTC pd.Timestamp, got {ts!r}")


@dataclass(frozen=True, slots=True)
class EventAnnotation:
    """One classified event, as the deterministic veto policy consumes it.

    ``observed_at`` is the first-seen instant of the earliest headline it cites
    (our clock, not the vendor's); ``available_at`` is when the annotation was
    written, so a point-in-time reader at ``t`` may use it only if
    ``available_at <= t``. ``model_id``/``model_digest``/``manifest_hash`` pin
    what produced it. Construct only in ``marketstate/events.py`` (AST test).
    """

    annotation_id: str
    source_ids: tuple[str, ...]
    event_type: str
    currencies: tuple[str, ...]
    severity: int
    scheduled: bool
    confidence: float
    observed_at: pd.Timestamp
    available_at: pd.Timestamp
    model_id: str
    model_digest: str
    manifest_hash: str
    policy_version: str

    def __post_init__(self) -> None:
        if not self.annotation_id:
            raise ValueError("an event annotation needs an id")
        if not self.source_ids or any(not s for s in self.source_ids):
            raise ValueError("an event annotation cites at least one headline id")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"event_type {self.event_type!r} is not in {EVENT_TYPES}")
        unknown = [c for c in self.currencies if c not in EVENT_BUCKETS]
        if unknown:
            raise ValueError(f"currencies {unknown} are not in {EVENT_BUCKETS}")
        if len(set(self.currencies)) != len(self.currencies):
            raise ValueError("currencies contains duplicates")
        if self.severity not in EVENT_SEVERITIES or isinstance(self.severity, bool):
            raise ValueError(f"severity {self.severity!r} is not one of {EVENT_SEVERITIES}")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence {self.confidence!r} is outside [0, 1]")
        _aware("observed_at", self.observed_at)
        _aware("available_at", self.available_at)
        if self.available_at < self.observed_at:
            raise ValueError(
                f"available_at {self.available_at} precedes observed_at {self.observed_at}: "
                "an annotation cannot exist before the headline it classifies was seen"
            )
        if not self.model_id or not self.model_digest:
            raise ValueError("an event annotation must pin its model id and weights digest")
        if not self.policy_version:
            raise ValueError("an event annotation must name its policy version")


@dataclass(frozen=True, slots=True)
class VetoReason:
    """Why the event policy would block a NEW entry. Identifiers only."""

    annotation_id: str
    event_type: str
    #: The buckets shared by the instrument and the annotation.
    buckets: tuple[str, ...]
    severity: int
    confidence: float
    observed_at: pd.Timestamp
    policy_version: str

    @property
    def code(self) -> str:
        return (
            f"{self.event_type}:{'+'.join(self.buckets)}:sev{self.severity}:"
            f"{self.annotation_id}"
        )


@dataclass(frozen=True, slots=True)
class VetoAssessment:
    """The event policy's answer for one instrument at one instant.

    ``available`` is False when the annotation store is missing, unreadable or
    stale; that is fail-OPEN by design (a veto can only block, so an absent
    veto is the conservative-for-trading default) and it is always paired with
    an operator alert by the source. ``veto`` may still be set when stale:
    annotations already on file remain valid evidence.
    """

    policy_version: str
    enabled: bool
    available: bool
    veto: VetoReason | None = None
    detail: str = ""


# ------------------------------------------------------------ EXECUTION


@dataclass(frozen=True, slots=True)
class Order:
    """An instruction to a venue. Carries an idempotency key from birth."""

    plan_id: str
    instrument: str
    direction: Direction
    size: float
    order_type: OrderType
    mode: ExecutionMode
    client_ref: str
    limit_price: float | None = None
    stop_loss: float | None = None
    #: The ONE limit a venue can hold against this position. A real broker
    #: accepts one stop and one target, so this is the first (nearest) leg and
    #: nothing more.
    take_profit: float | None = None
    #: The FULL take-profit ladder the strategy declared, and the fraction of
    #: the position each leg closes, aligned 1:1 with it. A venue cannot hold
    #: more than one target, so a multi-leg scale-out is managed client-side by
    #: whoever holds the position — which means it depends on that process being
    #: alive. These two fields exist so the ladder survives the trip from the
    #: sized plan to the position manager without a second source of truth; an
    #: adapter that can only attach one target reads ``take_profit`` and ignores
    #: them. Empty means "one full-size target", which is what every order built
    #: before the exit vocabulary existed meant.
    take_profit_prices: tuple[float, ...] = ()
    take_profit_allocations: tuple[float, ...] = ()
    created_at: pd.Timestamp | None = None
    order_id: str = field(default_factory=lambda: _uid("ord"))

    def __post_init__(self) -> None:
        if not self.client_ref:
            raise ValueError(
                "Order.client_ref is mandatory. Without a client-generated "
                "idempotency key a retry can double a position."
            )
        if self.take_profit_allocations and len(self.take_profit_allocations) != len(
            self.take_profit_prices
        ):
            raise ValueError(
                f"Order carries {len(self.take_profit_allocations)} take-profit "
                f"allocation(s) for {len(self.take_profit_prices)} price(s). A leg "
                "whose allocation cannot be named is a leg the position manager "
                "would have to guess a size for."
            )


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    instrument: str
    direction: Direction
    filled_size: float
    filled_price: float
    requested_price: float
    filled_at: pd.Timestamp
    spread_paid: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    venue_ref: str | None = None
    partial: bool = False

    @property
    def slippage_signed(self) -> float:
        """Positive means the fill was worse than requested."""
        return (self.filled_price - self.requested_price) * self.direction.sign


@dataclass(slots=True)
class Position:
    instrument: str
    direction: Direction
    size: float
    entry_price: float
    entry_time: pd.Timestamp
    stop_loss: float
    take_profit_targets: list[float] = field(default_factory=list)
    strategy_id: str = ""
    bars_held: int = 0
    max_favourable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    financing_accrued: float = 0.0
    venue_ref: str | None = None
    position_id: str = field(default_factory=lambda: _uid("pos"))

    def unrealised_quote(self, price: float, contract_size: float = 1.0) -> float:
        return (price - self.entry_price) * self.direction.sign * self.size * contract_size

    def update_excursion(self, high: float, low: float) -> None:
        best = high if self.direction is Direction.LONG else low
        worst = low if self.direction is Direction.LONG else high
        self.max_favourable_excursion = max(
            self.max_favourable_excursion,
            (best - self.entry_price) * self.direction.sign,
        )
        self.max_adverse_excursion = min(
            self.max_adverse_excursion,
            (worst - self.entry_price) * self.direction.sign,
        )


@dataclass(frozen=True, slots=True)
class Trade:
    """A closed round trip. All monetary fields are in the ACCOUNT currency."""

    instrument: str
    direction: Direction
    size: float
    entry_price: float
    exit_price: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    exit_reason: ExitReason
    gross_pnl: float
    spread_cost: float
    commission: float
    slippage_cost: float
    financing_cost: float
    net_pnl: float
    account_ccy: str
    strategy_id: str = ""
    bars_held: int = 0
    max_adverse_excursion: float = 0.0
    max_favourable_excursion: float = 0.0
    provenance: Provenance = Provenance.BACKTEST
    fx_rate_used: float = 1.0
    trade_id: str = field(default_factory=lambda: _uid("trd"))

    @property
    def total_costs(self) -> float:
        return self.spread_cost + self.commission + self.slippage_cost + self.financing_cost

    @property
    def duration(self) -> pd.Timedelta:
        return self.exit_time - self.entry_time


@dataclass(slots=True)
class AccountState:
    balance: float
    equity: float
    currency: str = "GBP"
    margin_used: float = 0.0
    open_positions: int = 0
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    peak_equity: float = 0.0

    @property
    def free_margin(self) -> float:
        return self.equity - self.margin_used

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100.0)


@dataclass(frozen=True, slots=True)
class ExecutionTelemetry:
    """Recorded for every order, in every mode. Feeds backtest-vs-live divergence."""

    order_id: str
    signal_time: pd.Timestamp
    decision_time: pd.Timestamp
    submit_time: pd.Timestamp
    ack_time: pd.Timestamp | None
    requested_price: float
    filled_price: float | None
    requested_size: float
    filled_size: float
    rejected_size: float
    spread_at_decision: float
    latency_ms: float
    venue_error: str | None
    market_regime: str | None
    mode: ExecutionMode
    extra: dict[str, Any] = field(default_factory=dict)
