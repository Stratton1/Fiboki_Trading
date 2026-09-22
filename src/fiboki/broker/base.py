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
    "AmendNotSupported",
    "BrokerAdapter",
    "BrokerError",
    "BrokerHealth",
    "BrokerRejected",
    "BrokerUnavailable",
    "DuplicateClientRef",
    "OrderAck",
    "OrderStatus",
    "PositionNotFound",
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


class PositionNotFound(BrokerRejected):
    """The venue has no such position.

    A SUBTYPE of :class:`BrokerRejected` because the venue positively answered,
    and a distinct type because for a CLOSING or AMENDING instruction it is not
    a failure: the position we were trying to flatten is already flat. A
    position manager that treated this as an error would retry an instruction
    that can never succeed, and would then alert on a book that is in exactly
    the state it wanted.
    """


class AmendNotSupported(BrokerError):
    """This adapter cannot amend a resting stop or target.

    Raised rather than returning a plausible ack. An adapter that silently
    accepted an amend it did not transmit would make every trail step look
    applied while the venue held a stale level, which is the single most
    expensive lie an adapter can tell a client-side position manager.
    """


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

    # -- venue-side position management ------------------------------------
    #
    # THE CONSTRAINT THESE TWO METHODS EXIST TO EXPRESS: a venue holds exactly
    # ONE stop and ONE limit per position. Everything a strategy document
    # declares beyond that -- later scale-out legs, trail steps, breakeven
    # moves, time stops -- is managed in OUR process and reaches the venue as a
    # stream of amendments and partial closes issued when a bar closes. These
    # are the two instructions that stream is made of.
    #
    # They are NOT abstract, so an adapter that genuinely cannot express them
    # (and a test double that has no need to) is still a valid BrokerAdapter.
    # The default raises rather than returning a plausible ack, because a
    # silently-dropped trail step is indistinguishable from an applied one right
    # up until the position closes at the wrong level.

    def amend_position(
        self,
        position: Position,
        *,
        stop_loss: float | None,
        take_profit: float | None,
        client_ref: str,
        reason: str = "",
    ) -> OrderAck:
        """Replace the resting stop and/or limit on ``position``.

        ``None`` means "leave this level as it is"; removing a level entirely is
        not expressible, because no exit policy in the tree ever widens risk and
        an adapter that could drop a stop is an adapter that will one day drop a
        stop.

        Implementations must be idempotent: re-sending the levels the venue
        already holds must succeed and must not move anything. Callers rely on
        that, because a retry after an unconfirmed response is the normal case.

        Raises :class:`PositionNotFound` when the venue has no such position,
        :class:`BrokerRejected` when the venue refuses the level (too close to
        market is the common one), and :class:`BrokerUnavailable` when the
        outcome is unknown -- including a rate limit, which is unknown rather
        than refused.
        """
        raise AmendNotSupported(
            f"{type(self).__name__} cannot amend a resting stop or target. A "
            "strategy whose exit policy needs more than the one stop and one "
            "limit attached at entry cannot be managed on this venue."
        )

    def close_partial(
        self, position: Position, *, size: float, client_ref: str, reason: str = ""
    ) -> OrderAck:
        """Close ``size`` units of ``position``, leaving the remainder open.

        This is how a scale-out leg beyond the first one reaches the venue. The
        size is the manager's, computed once by the shared position book; an
        adapter converts it into the venue's units and never re-decides it.
        """
        raise AmendNotSupported(
            f"{type(self).__name__} cannot partially close a position. A "
            "multi-leg scale-out cannot be managed on this venue."
        )
