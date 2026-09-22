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
    PositionNotFound,
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

    # -- amendment friction -------------------------------------------------
    #
    # The three rude things a real venue does to a stop amendment, each one
    # separately switchable because they have different correct responses:
    # a refusal is terminal for THIS level and may succeed at the next bar, a
    # rate limit leaves the outcome unknown, and a closed market refuses
    # everything until it opens.
    #
    #: Refuse the Nth and subsequent amendments (1-based). ``None`` disables.
    reject_amend_after: int | None = None
    #: Refuse the first N amendments, then behave. Models "too close to market"
    #: resolving itself once price moves away.
    reject_amend_count: int = 0
    #: Refuse an amendment whose stop is within this many PRICE units of the
    #: venue's reference price for the instrument. IG's minimum stop distance.
    min_stop_distance: float = 0.0
    #: Every amendment raises BrokerUnavailable: the fate is UNKNOWN.
    amend_rate_limited: bool = False
    #: Every amendment and partial close is refused with "market closed".
    market_closed: bool = False
    #: Refuse partial closes outright. Models a venue with no partial-close
    #: route at all, which is the worst case for a multi-leg document.
    reject_partial_closes: bool = False

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
        #: Every amendment the venue was ASKED for, applied or not. The point of
        #: recording refused ones too is that a manager which loses track of a
        #: refused amendment is exactly the failure this venue exists to expose.
        self.amendments: list[dict[str, Any]] = []
        self.partial_closes: list[dict[str, Any]] = []
        self._amend_count = 0

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
        if not ref:
            # UNADDRESSABLE, which is not the same as flat. A position with no
            # broker reference is a local defect: the venue was never told
            # about it, or we lost the key. Reporting it as "already closed"
            # would abandon a real position and call the book clean.
            raise BrokerRejected(
                "cannot close a position with no broker reference; closing is "
                "keyed on the BROKER reference, never on an internal id"
            )
        if ref not in self._positions:
            # PositionNotFound, a BrokerRejected subtype: the venue positively
            # answered "there is no such position", which for a CLOSING
            # instruction is the state the caller wanted.
            raise PositionNotFound(
                f"venue has no position {ref!r}; this is keyed on the BROKER "
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

    # ----------------------------------------------- position management

    def amend_position(
        self,
        position: Position,
        *,
        stop_loss: float | None,
        take_profit: float | None,
        client_ref: str,
        reason: str = "",
    ) -> OrderAck:
        """Amend the ONE stop and ONE limit this venue holds for a position.

        Idempotent by construction: the levels are assigned, not adjusted, so
        re-sending the levels the venue already holds changes nothing and
        succeeds. A manager retrying after an unconfirmed response depends on
        that, and a venue that double-applied an amendment would make every
        retry a fresh risk decision.
        """
        cfg = self.config
        ref = position.venue_ref
        self._amend_count += 1
        attempt = {
            "client_ref": client_ref,
            "broker_ref": ref,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": reason,
            "applied": False,
        }
        self.amendments.append(attempt)

        if not ref:
            raise BrokerRejected(
                "cannot amend a position with no broker reference; amendment is "
                "keyed on the BROKER reference, never on an internal id"
            )
        if ref not in self._positions:
            raise PositionNotFound(
                f"venue has no position {ref!r}; this is keyed on the BROKER "
                "reference, never on an internal id"
            )
        if not self._connected:
            raise BrokerUnavailable("not connected")
        if cfg.market_closed:
            raise BrokerRejected("MARKET_HALTED: the market is closed for this instrument")
        if cfg.amend_rate_limited:
            # A rate limit is UNKNOWN, not refused. The venue may well have
            # applied it and simply not told us.
            raise BrokerUnavailable("RATE_LIMIT_EXCEEDED: amendment outcome unknown")
        if cfg.reject_amend_count and self._amend_count <= cfg.reject_amend_count:
            raise BrokerRejected(
                f"ATTACHED_ORDER_LEVEL_DISTANCE_ERROR: amendment {self._amend_count} "
                "refused (configured)"
            )
        if cfg.reject_amend_after is not None and self._amend_count >= cfg.reject_amend_after:
            raise BrokerRejected(
                f"ATTACHED_ORDER_LEVEL_DISTANCE_ERROR: amendment {self._amend_count} "
                "refused (configured)"
            )

        held = self._positions[ref]
        if stop_loss is not None and cfg.min_stop_distance > 0:
            mark = cfg.prices.get(held.instrument, held.entry_price)
            if abs(float(stop_loss) - mark) < cfg.min_stop_distance:
                raise BrokerRejected(
                    f"ATTACHED_ORDER_LEVEL_DISTANCE_ERROR: stop {stop_loss} is "
                    f"within {cfg.min_stop_distance} of {mark}"
                )

        if stop_loss is not None:
            held.stop_loss = float(stop_loss)
        if take_profit is not None:
            held.take_profit_targets = [float(take_profit)]
        attempt["applied"] = True
        return OrderAck(
            status=OrderStatus.ACCEPTED,
            broker_ref=ref,
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=self._now,
            acked_at=self._now,
            message=reason or "amended",
            raw={"stop_loss": held.stop_loss, "take_profit": list(held.take_profit_targets)},
        )

    def close_partial(
        self, position: Position, *, size: float, client_ref: str, reason: str = ""
    ) -> OrderAck:
        ref = position.venue_ref
        self.partial_closes.append(
            {"client_ref": client_ref, "broker_ref": ref, "size": size, "applied": False}
        )
        if not ref:
            raise BrokerRejected("cannot partially close a position with no broker reference")
        if ref not in self._positions:
            raise PositionNotFound(
                f"venue has no position {ref!r}; this is keyed on the BROKER "
                "reference, never on an internal id"
            )
        if not self._connected:
            raise BrokerUnavailable("not connected")
        if self.config.market_closed:
            raise BrokerRejected("MARKET_HALTED: the market is closed for this instrument")
        if self.config.reject_partial_closes:
            raise BrokerRejected("PARTIAL_CLOSE_NOT_PERMITTED (configured)")
        held = self._positions[ref]
        if size <= 0 or size > held.size + 1e-9:
            raise BrokerRejected(
                f"partial close of {size} is not within the open size {held.size}"
            )
        price = self.config.prices.get(held.instrument, held.entry_price)
        remaining = held.size - float(size)
        if remaining <= 1e-12:
            self._positions.pop(ref)
            rec = self._orders.get(ref)
            if rec is not None:
                rec.status = OrderStatus.CANCELLED
        else:
            held.size = remaining
        self.partial_closes[-1]["applied"] = True
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
                filled_size=float(size),
                filled_price=price,
                requested_price=price,
                filled_at=self._now,
                venue_ref=ref,
                partial=remaining > 1e-12,
            ),
            message=reason or "partially_closed",
        )

    # ------------------------------------------------------------ helpers

    def record_for_client_ref(self, client_ref: str) -> VenueRecord | None:
        ref = self._by_client_ref.get(client_ref)
        return self._orders.get(ref) if ref else None

    @property
    def lost_responses(self) -> tuple[VenueRecord, ...]:
        return tuple(r for r in self._orders.values() if r.response_lost)
