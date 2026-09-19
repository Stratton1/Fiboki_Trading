"""A configurable fake venue, for testing the things that only break in production.

The paper adapter models *market* friction honestly. This models *venue*
friction dishonestly-on-purpose: it does the specific rude things real brokers
do, on demand and deterministically, so the execution service can be tested
against them.

Supported failure modes
-----------------------
``latency_ms``
    Reported on the ack, and accumulated so a test can assert pacing.
``reject_after`` / ``reject_client_refs`` / ``rejection_probability``
    Positive refusals. Terminal, and distinguishable from a timeout.
``partial_fill_fraction``
    The venue fills less than you asked for. The remainder is *rejected*, not
    silently forgotten -- V1 recorded the requested size and moved on.
``disconnect_after`` / :meth:`disconnect`
    Transport failure. Raises :class:`BrokerUnavailable`, so the caller must
    treat the order's fate as UNKNOWN. It is emphatically not a rejection.
``ack_then_fail``
    **The important one.** The venue accepts the order, assigns a deal
    reference and opens a real position -- and then the response never reaches
    us. This is the crash-mid-order scenario that left V1 with real positions
    and no local record, forever. A caller that handles this correctly writes a
    PENDING intent before dispatch, marks it UNKNOWN on the exception, and
    recovers the broker reference at the next reconciliation by matching on the
    client reference the venue kept.
Duplicate submission
    The venue remembers every ``client_ref`` it has seen and raises
    :class:`DuplicateClientRef` on a repeat, exactly as a venue honouring a
    client-supplied idempotency key does.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.broker.base import (
    BrokerAdapter,
    BrokerHealth,
    BrokerRejected,
    BrokerUnavailable,
    DuplicateClientRef,
    OrderAck,
    OrderStatus,
)
from fiboki.core.contracts import AccountState, Fill, Order, Position
from fiboki.core.enums import ExecutionMode
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import round_size

__all__ = ["SimulatedVenue", "VenueConfig", "VenueRecord"]


@dataclass(slots=True)
class VenueConfig:
    latency_ms: float = 25.0
    #: Reject the Nth and subsequent orders (1-based). ``None`` disables.
    reject_after: int | None = None
    reject_client_refs: frozenset[str] = frozenset()
    rejection_probability: float = 0.0
    #: Fill this fraction of the requested size. 1.0 means full fills.
    partial_fill_fraction: float = 1.0
    #: Raise BrokerUnavailable from the Nth order onward (1-based).
    disconnect_after: int | None = None
    #: Accept + record the order at the venue, then fail the response.
    ack_then_fail: bool = False
    #: Number of orders the ack-then-fail behaviour applies to before the venue
    #: starts responding normally again. ``None`` means "always".
    ack_then_fail_count: int | None = None
    seed: int = 20260919
    #: Reference prices used to fill, keyed by symbol. Missing symbols use the
    #: order's limit price, then 1.0.
    prices: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.rejection_probability <= 1.0:
            raise ValueError("rejection_probability must be in [0, 1]")
        if not 0.0 < self.partial_fill_fraction <= 1.0:
            raise ValueError("partial_fill_fraction must be in (0, 1]")


@dataclass(slots=True)
class VenueRecord:
    """What the VENUE believes. The point of reconciliation is to compare
    this with what we believe, keyed on ``broker_ref``."""

    broker_ref: str
    client_ref: str
    order_id: str
    instrument: str
    direction: str
    requested_size: float
    filled_size: float
    price: float
    status: OrderStatus
    received_at: pd.Timestamp
    #: True when the venue accepted the order but the caller never learned so.
    response_lost: bool = False


class SimulatedVenue(BrokerAdapter):
    """A fake venue that lies in specific, configurable, deterministic ways."""

    mode = ExecutionMode.SHADOW
    venue_name = "simulated"

    def __init__(
        self,
        config: VenueConfig | None = None,
        *,
        balance: float = 100_000.0,
        currency: str = "GBP",
        now: pd.Timestamp | None = None,
    ) -> None:
        self.config = config or VenueConfig()
        self._balance = balance
        self._currency = currency
        self._now = now or pd.Timestamp("2024-01-01", tz="UTC")
        self._rng = np.random.default_rng(self.config.seed)
        self._counter = itertools.count(1)
        self._orders: dict[str, VenueRecord] = {}          # broker_ref -> record
        self._by_client_ref: dict[str, str] = {}           # client_ref -> broker_ref
        self._positions: dict[str, Position] = {}          # broker_ref -> position
        self._connected = True
        self._order_count = 0
        self._ack_then_fail_used = 0
        self.total_latency_ms = 0.0
        self.requests: list[dict[str, Any]] = []

    # ------------------------------------------------------------ controls

    def set_time(self, now: pd.Timestamp) -> None:
        self._now = now

    def disconnect(self, message: str = "operator disconnect") -> None:
        self._connected = False
        self._disconnect_message = message

    def reconnect(self) -> None:
        self._connected = True

    # ------------------------------------------------------------ adapter

    def connect(self) -> BrokerHealth:
        self._connected = True
        return self.health()

    def health(self) -> BrokerHealth:
        return BrokerHealth(
            connected=self._connected,
            score=1.0 if self._connected else 0.0,
            checked_at=self._now,
            message="simulated venue" if self._connected else "disconnected",
            latency_ms=self.config.latency_ms,
        )

    def account(self) -> AccountState:
        return AccountState(
            balance=self._balance,
            equity=self._balance,
            currency=self._currency,
            open_positions=len(self._positions),
            peak_equity=self._balance,
        )

    def positions(self) -> tuple[Position, ...]:
        return tuple(self._positions[k] for k in sorted(self._positions))

    def orders(self) -> tuple[OrderAck, ...]:
        """Every order the VENUE has a record of, including ones whose response
        was lost. This is what makes recovery possible at all."""
        out: list[OrderAck] = []
        for ref in sorted(self._orders):
            rec = self._orders[ref]
            out.append(
                OrderAck(
                    status=rec.status,
                    broker_ref=rec.broker_ref,
                    client_ref=rec.client_ref,
                    order_id=rec.order_id,
                    submitted_at=rec.received_at,
                    acked_at=rec.received_at,
                    message="response_lost" if rec.response_lost else "",
                    raw={"filled_size": rec.filled_size, "price": rec.price},
                )
            )
        return tuple(out)

    def market_spec(self, symbol: str) -> Instrument:
        return get_instrument(symbol)

    # ------------------------------------------------------------- place

    def place_order(self, order: Order) -> OrderAck:
        self.requests.append(
            {"client_ref": order.client_ref, "instrument": order.instrument, "size": order.size}
        )

        # Duplicate detection comes FIRST. An idempotency key that is only
        # honoured when the venue is otherwise healthy is not an idempotency key.
        if order.client_ref in self._by_client_ref:
            raise DuplicateClientRef(
                f"client_ref {order.client_ref!r} already submitted; venue ref "
                f"{self._by_client_ref[order.client_ref]}"
            )

        if not self._connected:
            raise BrokerUnavailable(getattr(self, "_disconnect_message", "not connected"))

        self._order_count += 1
        cfg = self.config
        self.total_latency_ms += cfg.latency_ms

        if cfg.disconnect_after is not None and self._order_count >= cfg.disconnect_after:
            self._connected = False
            self._disconnect_message = f"disconnected after order {self._order_count}"
            raise BrokerUnavailable(self._disconnect_message)

        if order.client_ref in cfg.reject_client_refs:
            raise BrokerRejected(f"venue refused {order.client_ref!r} (configured)")
        if cfg.reject_after is not None and self._order_count >= cfg.reject_after:
            raise BrokerRejected(f"venue refused order {self._order_count} (configured)")
        if cfg.rejection_probability > 0 and float(self._rng.random()) < cfg.rejection_probability:
            raise BrokerRejected("venue refused order (probabilistic)")

        instrument = get_instrument(order.instrument)
        price = cfg.prices.get(order.instrument, order.limit_price or 1.0)
        filled = order.size
        if cfg.partial_fill_fraction < 1.0:
            filled = round_size(instrument, order.size * cfg.partial_fill_fraction)
            if filled <= 0:
                raise BrokerRejected("partial fill rounded to zero")

        broker_ref = f"SIMDEAL-{next(self._counter):08d}"
        status = (
            OrderStatus.PARTIALLY_FILLED if filled < order.size else OrderStatus.FILLED
        )
        lose_response = cfg.ack_then_fail and (
            cfg.ack_then_fail_count is None
            or self._ack_then_fail_used < cfg.ack_then_fail_count
        )

        record = VenueRecord(
            broker_ref=broker_ref,
            client_ref=order.client_ref,
            order_id=order.order_id,
            instrument=order.instrument,
            direction=order.direction.value,
            requested_size=order.size,
            filled_size=filled,
            price=price,
            status=status,
            received_at=self._now,
            response_lost=lose_response,
        )
        # The venue commits FIRST, exactly as a real one does. Everything after
        # this point is about whether we get to hear about it.
        self._orders[broker_ref] = record
        self._by_client_ref[order.client_ref] = broker_ref
        self._positions[broker_ref] = Position(
            instrument=order.instrument,
            direction=order.direction,
            size=filled,
            entry_price=price,
            entry_time=self._now,
            stop_loss=order.stop_loss if order.stop_loss is not None else 0.0,
            take_profit_targets=[order.take_profit] if order.take_profit is not None else [],
            venue_ref=broker_ref,
        )

        if lose_response:
            self._ack_then_fail_used += 1
            raise BrokerUnavailable(
                f"venue accepted {order.client_ref!r} as {broker_ref} but the "
                "response was lost in transit"
            )

        return OrderAck(
            status=status,
            broker_ref=broker_ref,
            client_ref=order.client_ref,
            order_id=order.order_id,
            submitted_at=self._now,
            acked_at=self._now,
            fill=Fill(
                order_id=order.order_id,
                instrument=order.instrument,
                direction=order.direction,
                filled_size=filled,
                filled_price=price,
                requested_price=order.limit_price or price,
                filled_at=self._now,
                venue_ref=broker_ref,
                partial=filled < order.size,
            ),
            message="filled" if status is OrderStatus.FILLED else "partially_filled",
            raw={"latency_ms": cfg.latency_ms},
        )

    def close_position(
        self, position: Position, *, client_ref: str, reason: str = ""
    ) -> OrderAck:
        ref = position.venue_ref
        if not ref or ref not in self._positions:
            raise BrokerRejected(
                f"venue has no position {ref!r}; closing is keyed on the BROKER "
                "reference, never on an internal id"
            )
        if not self._connected:
            raise BrokerUnavailable("not connected")
        held = self._positions.pop(ref)
        rec = self._orders.get(ref)
        if rec is not None:
            rec.status = OrderStatus.CANCELLED
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=ref,
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=self._now,
            acked_at=self._now,
            fill=Fill(
                order_id=client_ref,
                instrument=held.instrument,
                direction=held.direction.opposite,
                filled_size=held.size,
                filled_price=self.config.prices.get(held.instrument, held.entry_price),
                requested_price=held.entry_price,
                filled_at=self._now,
                venue_ref=ref,
            ),
            message=reason or "closed",
        )

    # ------------------------------------------------------------ helpers

    def record_for_client_ref(self, client_ref: str) -> VenueRecord | None:
        ref = self._by_client_ref.get(client_ref)
        return self._orders.get(ref) if ref else None

    @property
    def lost_responses(self) -> tuple[VenueRecord, ...]:
        return tuple(r for r in self._orders.values() if r.response_lost)
