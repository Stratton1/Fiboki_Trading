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
from typing import Any

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


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()
    adjusted_size: float | None = None
    checks_run: tuple[str, ...] = ()
    decided_at: pd.Timestamp | None = None

    @staticmethod
    def allow(checks: tuple[str, ...]) -> "RiskDecision":
        return RiskDecision(True, (), None, checks)

    @staticmethod
    def block(reason: str, checks: tuple[str, ...] = ()) -> "RiskDecision":
        return RiskDecision(False, (reason,), None, checks)


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
    take_profit: float | None = None
    created_at: pd.Timestamp | None = None
    order_id: str = field(default_factory=lambda: _uid("ord"))

    def __post_init__(self) -> None:
        if not self.client_ref:
            raise ValueError(
                "Order.client_ref is mandatory. Without a client-generated "
                "idempotency key a retry can double a position."
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
