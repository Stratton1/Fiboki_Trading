"""Domain types for the multi-broker execution router.

Phase 1 of the fan-out execution architecture. These types are deliberately
broker-neutral: a bot produces a ``NormalisedTradePlan``, the router fans it
out to one or more ``ResolvedTarget`` instances, and each dispatch produces
one ``ExecutionAttempt``.

In Phase 2 these will be backed by ``execution_accounts`` and
``bot_execution_targets`` tables. In Phase 1 they are constructed from
environment variables at worker startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from fibokei.execution.adapter import ExecutionAdapter

# ── Router modes ─────────────────────────────────────────────────────────

#: Legacy mode — exactly the pre-Phase-1 behaviour: at most one execution
#: target (paper or IG) selected by the existing ``FIBOKEI_LIVE_EXECUTION_ENABLED``
#: flag. Sizing uses the bot's PaperAccount equity (dynamic).
ROUTER_MODE_LEGACY_SINGLE = "legacy_single"

#: Fan-out mode — every enabled execution account receives every bot signal.
#: Sizing uses each target's ``allocated_capital`` (static).
ROUTER_MODE_ENV_GLOBAL_FANOUT = "env_global_fanout"

#: DB-driven targets — Phase 2. Each bot has an explicit list of targets in
#: the database. Not implemented in Phase 1.
ROUTER_MODE_DB_TARGETS = "db_targets"

VALID_ROUTER_MODES = frozenset(
    {ROUTER_MODE_LEGACY_SINGLE, ROUTER_MODE_ENV_GLOBAL_FANOUT, ROUTER_MODE_DB_TARGETS}
)


# ── Brokers and environments ─────────────────────────────────────────────

BROKER_PAPER = "paper"
BROKER_IG = "ig"
BROKER_TRADOVATE = "tradovate"
KNOWN_BROKERS = frozenset({BROKER_PAPER, BROKER_IG, BROKER_TRADOVATE})

ENV_PAPER = "paper"
ENV_DEMO = "demo"
ENV_LIVE = "live"
KNOWN_ENVIRONMENTS = frozenset({ENV_PAPER, ENV_DEMO, ENV_LIVE})


# ── Symbol resolution outcome ────────────────────────────────────────────


@dataclass(frozen=True)
class UnsupportedSymbol:
    """Returned when a target cannot trade a given Fiboki symbol.

    Carries an explicit ``code`` so the audit log can record the precise
    reason without operators having to parse free-form strings.
    """

    code: str
    detail: str = ""


# ── Trade plan ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalisedTradePlan:
    """Broker-neutral trade plan emitted by a bot.

    The plan does not carry size — sizing happens per-target inside the
    router because each target uses its own allocated capital. Direction is
    expressed in strategy terms (``LONG`` / ``SHORT``); each adapter is
    responsible for translating to broker-specific direction codes.
    """

    bot_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit_targets: tuple[float, ...]
    bar_time: datetime
    signal_timestamp: datetime
    max_bars_in_trade: int = 100


# ── Resolved target ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedTarget:
    """A fully resolved execution destination, ready to receive orders.

    The router holds a list of these. Each target encapsulates the broker,
    the environment (paper/demo/live), the operator-allocated capital used
    for sizing, the per-trade risk percentage, and the adapter instance.
    """

    target_id: str
    name: str
    broker: str
    environment: str
    allocated_capital: float
    risk_per_trade_pct: float
    is_enabled: bool
    adapter: ExecutionAdapter
    # Hard-block live unless the operator has explicitly opted in.
    live_allowed: bool = False
    # Phase-1 deferred: per-account loss limits, sizing-mode overrides etc.

    def is_environment_allowed(self) -> bool:
        """Refuse live unless ``live_allowed`` is explicitly True."""
        if self.environment == ENV_LIVE:
            return self.live_allowed
        return self.environment in (ENV_PAPER, ENV_DEMO)


# ── Execution attempt (audit-shaped) ─────────────────────────────────────

# Status vocabulary — kept stable across brokers and phases:
ATTEMPT_FILLED = "filled"          # Broker accepted and reported a fill/deal id
ATTEMPT_PAPER_FILLED = "paper_filled"  # Paper instant-fill (no real broker)
ATTEMPT_REJECTED = "rejected"      # Broker rejected (pre-flight or post-flight)
ATTEMPT_SKIPPED = "skipped"        # Router skipped before dispatch (gate failed)
ATTEMPT_ERROR = "error"            # Adapter raised an unexpected exception

VALID_ATTEMPT_STATUSES = frozenset(
    {ATTEMPT_FILLED, ATTEMPT_PAPER_FILLED, ATTEMPT_REJECTED, ATTEMPT_SKIPPED, ATTEMPT_ERROR}
)


@dataclass
class ExecutionAttempt:
    """One child attempt of a fan-out signal.

    Mutable on purpose — the router progressively populates fields as it
    moves through validation gates, sizing, dispatch, and result parsing.
    The serialised form (``to_dict``) is what gets persisted into the
    ``execution_audit`` table's ``detail_json`` column in Phase 1.
    """

    parent_signal_id: str
    target_id: str
    target_name: str
    broker: str
    environment: str
    account_capital: float
    risk_pct: float
    instrument: str
    direction: str
    requested_price: Optional[float] = None
    broker_symbol: Optional[str] = None
    requested_size: Optional[float] = None
    adjusted_size: Optional[float] = None
    filled_size: Optional[float] = None
    filled_price: Optional[float] = None
    status: str = "pending"
    broker_order_id: Optional[str] = None
    broker_deal_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    error_code: Optional[str] = None
    latency_ms: Optional[int] = None
    slippage_pips: Optional[float] = None
    extra: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        """Did this attempt result in an open broker position?

        True for both real-broker fills and paper fills — the bot stores the
        deal id for close-on-exit dispatch.
        """
        return self.status in (ATTEMPT_FILLED, ATTEMPT_PAPER_FILLED) and bool(
            self.broker_deal_id
        )

    def to_dict(self) -> dict:
        """JSON-safe representation for audit persistence."""
        return {
            "parent_signal_id": self.parent_signal_id,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "broker": self.broker,
            "environment": self.environment,
            "account_capital": self.account_capital,
            "risk_pct": self.risk_pct,
            "instrument": self.instrument,
            "direction": self.direction,
            "requested_price": self.requested_price,
            "broker_symbol": self.broker_symbol,
            "requested_size": self.requested_size,
            "adjusted_size": self.adjusted_size,
            "filled_size": self.filled_size,
            "filled_price": self.filled_price,
            "status": self.status,
            "broker_order_id": self.broker_order_id,
            "broker_deal_id": self.broker_deal_id,
            "rejection_reason": self.rejection_reason,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
            "slippage_pips": self.slippage_pips,
            "extra": dict(self.extra),
        }


# ── Symbol resolver protocol ─────────────────────────────────────────────


SymbolResolver = Callable[[str], "str | UnsupportedSymbol"]
"""A function that maps a Fiboki symbol to a broker-specific symbol/contract,
or returns ``UnsupportedSymbol`` if no mapping is defined."""
