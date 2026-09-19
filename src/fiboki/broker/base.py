"""The broker adapter contract.

Two prohibitions define this interface, both of them V1 post-mortem findings:

**An adapter MUST NOT size.** V1's IG adapter recomputed position size from the
live broker balance, so the internal ledger and the venue disagreed by
construction -- no bug was needed, the architecture guaranteed it. Here an
adapter receives an :class:`~fiboki.core.contracts.Order` whose ``size`` was
decided once by :func:`fiboki.portfolio.sizing.size_trade`, and its only
freedom is to express that number in the venue's units.

**An adapter MUST NOT construct a risk decision.** Permission is granted
upstream by :class:`fiboki.risk.gateway.RiskGateway` and nowhere else. An
adapter may *report* that the venue refused an order; it may not decide that
one is acceptable.

``tests/unit/test_adapter_prohibitions.py`` enforces both over the AST.

Idempotency is part of the contract, not an implementation detail: every
adapter must transmit ``Order.client_ref`` to the venue in whatever field the
venue provides for client-supplied references, and must return the venue's own
reference in :attr:`OrderAck.broker_ref`. Reconciliation is keyed on the
broker's reference; V1 compared an internal ``uuid4`` against IG's ``dealId``,
two key spaces that never intersect, so reconciliation could not produce a
clean result even on a perfectly healthy system.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from fiboki.core.contracts import AccountState, Fill, Order, Position
from fiboki.core.enums import ExecutionMode
from fiboki.core.instruments import Instrument

__all__ = [
    "BrokerAdapter",
    "BrokerError",
    "BrokerHealth",
    "BrokerRejected",
    "BrokerUnavailable",
    "DuplicateClientRef",
    "OrderAck",
    "OrderStatus",
]


class BrokerError(Exception):
    """Base for adapter failures."""


class BrokerUnavailable(BrokerError):
    """Transport failed. The order's fate is UNKNOWN, not rejected.

    This distinction is the whole point. V1 treated a timeout as a rejection
    and moved on, which is how a real position ended up with no local record.
    """


class BrokerRejected(BrokerError):
    """The venue positively refused the order. This IS terminal."""


class DuplicateClientRef(BrokerError):
    """The venue has already seen this client reference. Not an error condition
    to retry through -- it is the idempotency key doing its job."""


class OrderStatus(str, Enum):
    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    #: Submitted, fate unconfirmed. Requires reconciliation, never a rejection.
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class OrderAck:
    """The venue's response to a submission.

    ``broker_ref`` is the venue's key and the ONLY key reconciliation uses.
    ``client_ref`` is echoed back so a caller can prove the venue received it.
    """

    status: OrderStatus
    broker_ref: str | None
    client_ref: str
    order_id: str
    submitted_at: pd.Timestamp
    acked_at: pd.Timestamp | None = None
    fill: Fill | None = None
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) and not self.broker_ref:
            raise ValueError(
                "A filled order must carry the venue's reference. Without it the "
                "position cannot be reconciled or closed after a restart."
            )


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    connected: bool
    #: 0.0 unusable .. 1.0 nominal.
    score: float
    checked_at: pd.Timestamp
    message: str = ""
    latency_ms: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("BrokerHealth.score must be in [0, 1]")


class BrokerAdapter(ABC):
    """Every venue, real or simulated, behind one interface."""

    #: The execution mode this adapter is permitted to serve. An adapter that
    #: could serve several declares the most dangerous one it supports.
    mode: ExecutionMode = ExecutionMode.PAPER
    #: Human name used in telemetry and logs.
    venue_name: str = "abstract"

    # -- lifecycle ---------------------------------------------------------

    @abstractmethod
    def connect(self) -> BrokerHealth:
        """Establish (or verify) the session. Idempotent."""

    @abstractmethod
    def health(self) -> BrokerHealth:
        """Current venue health. Cheap; called before every order."""

    # -- state -------------------------------------------------------------

    @abstractmethod
    def account(self) -> AccountState:
        """Balance, equity and margin as the VENUE sees them.

        Reported for reconciliation and display. It is emphatically NOT a
        sizing input: sizing already happened, upstream, once.
        """

    @abstractmethod
    def positions(self) -> tuple[Position, ...]:
        """Open positions as the venue sees them, each carrying ``venue_ref``."""

    @abstractmethod
    def orders(self) -> tuple[OrderAck, ...]:
        """Working (non-terminal) orders as the venue sees them."""

    @abstractmethod
    def market_spec(self, symbol: str) -> Instrument:
        """The venue's contract spec, mapped onto our canonical Instrument."""

    # -- actions -----------------------------------------------------------

    @abstractmethod
    def place_order(self, order: Order) -> OrderAck:
        """Submit ``order`` exactly as sized, carrying ``order.client_ref``.

        Implementations must raise :class:`BrokerUnavailable` (never return a
        rejection) when the outcome is genuinely unknown, and
        :class:`DuplicateClientRef` when the venue reports the reference was
        already used.
        """

    @abstractmethod
    def close_position(
        self, position: Position, *, client_ref: str, reason: str = ""
    ) -> OrderAck:
        """Close ``position`` in full, keyed on its ``venue_ref``."""
