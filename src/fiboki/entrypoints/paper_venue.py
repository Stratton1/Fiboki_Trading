"""The paper VENUE the forward runtime deals against: instant fills at the last close.

Why a separate venue and not :class:`~fiboki.broker.paper.PaperBroker`
---------------------------------------------------------------------
``PaperBroker`` IS a position book: it queues an order for the next bar's open
and models every exit itself. That is the right shape for a replay and for the
paper/backtest parity test. It is the wrong shape for the composition the
forward runtime uses, which is the production one: a venue that deals a market
order the instant it is sent, and a
:class:`~fiboki.broker.position_manager.VenuePositionManager` that drives the
shared :class:`~fiboki.backtest.position.PositionBook` against it and pushes
every stop move, scale-out leg and close to the venue as an instruction. That
is exactly how the OANDA practice venue will be driven in DEMO; here the venue
is simulated so the path can run in PAPER with nothing reaching a broker.

What this venue models, and what it does not
--------------------------------------------
* **A market order fills immediately at the feed's last closed-bar close,
  plus or minus the execution profile's half spread for that UTC hour.**
  The close is the last price the process has; the profile is the same cost
  table the fill simulator charges. The fill is recorded with its price, and
  the venue holds the position with the one stop and one limit the order
  carried.
* **The P&L OF RECORD IS THE BOOK'S, not this venue's.** The manager's book
  models the same entry at the NEXT bar's open through the fill simulator,
  exactly as the backtester does, so the forward ledger stays comparable to a
  backtest of the same document. The venue's dealt price is recorded beside
  it; the difference (close versus next open, plus the cost model) is the
  measured divergence, not an error to reconcile away.
  :meth:`account` therefore reports the MODELLED account supplied by the
  composition root.
* **The venue does not trigger its own resting stop or limit.** Every exit is
  decided by the book on a closed bar and pushed here as a close; a close of a
  position this venue no longer holds raises
  :class:`~fiboki.broker.base.PositionNotFound`, which the execution service
  records as ``already_flat``.
* **No partial fills, no rejections, no latency.** The friction a venue adds
  is :class:`~fiboki.broker.simulated_venue.SimulatedVenue`'s job in tests.
* **In memory.** A process restart loses the venue and its positions, exactly
  as it loses the book; the forward journal records the positions that were
  open at the last bar so the loss is visible (see ``entrypoints/paper_forward``).

Sizing: none. ``order.size`` is dealt unchanged; this module never calls a
sizer (``tests/unit/test_paper_forward_compose.py`` asserts it over the AST).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

import pandas as pd

from fiboki.broker.base import (
    BrokerAdapter,
    BrokerHealth,
    BrokerRejected,
    DuplicateClientRef,
    OrderAck,
    OrderStatus,
    PositionNotFound,
)
from fiboki.core.contracts import AccountState, Fill, Order, Position
from fiboki.core.enums import Direction, ExecutionMode
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.sim.profiles import ExecutionProfile

__all__ = ["ForwardPaperVenue"]


class ForwardPaperVenue(BrokerAdapter):
    """Instant-fill paper venue over the forward feed's closed-bar prices."""

    mode = ExecutionMode.PAPER
    venue_name = "paper-forward"
    name = "paper-forward"

    def __init__(
        self,
        *,
        profile: ExecutionProfile,
        account: Callable[[], AccountState],
        clock: Callable[[], pd.Timestamp],
        strategy_id: str = "",
    ) -> None:
        self.profile = profile
        self._account = account
        self.clock = clock
        self.strategy_id = strategy_id
        #: ``symbol -> (bar close time, close price)``, from the feed.
        self.marks: dict[str, tuple[pd.Timestamp, float]] = {}
        self._positions: dict[str, Position] = {}
        self._seen: dict[str, str] = {}
        self._seq = 0
        self.fills: list[Fill] = []

    # -- the feed tells us prices -----------------------------------------

    def observe_closes(
        self,
        closes: Mapping[str, pd.Timestamp],
        prices: Mapping[str, float] | None = None,
    ) -> None:
        for symbol, price in (prices or {}).items():
            closed_at = closes.get(symbol)
            if closed_at is not None:
                self.marks[symbol] = (closed_at, float(price))

    def _price(self, instrument: Instrument, direction: Direction) -> tuple[float, float, float]:
        mark = self.marks.get(instrument.symbol)
        if mark is None:
            raise BrokerRejected(
                f"paper venue has no closed-bar price for {instrument.symbol} yet; "
                "it will not invent one"
            )
        closed_at, mid = mark
        half = float(self.profile.half_spread_price(instrument, int(closed_at.hour), mid))
        return mid + direction.sign * half, mid, half

    def _next_ref(self) -> str:
        self._seq += 1
        return f"PFWD-{self._seq:08d}"

    # -- BrokerAdapter -----------------------------------------------------

    def connect(self) -> BrokerHealth:
        return self.health()

    def health(self) -> BrokerHealth:
        return BrokerHealth(
            connected=True,
            score=1.0,
            checked_at=self.clock(),
            message="paper venue: instant fill at the last closed bar's close",
            latency_ms=0.0,
        )

    def account(self) -> AccountState:
        """The MODELLED account (the book's). See the module docstring."""
        return self._account()

    def positions(self) -> tuple[Position, ...]:
        return tuple(self._positions.values())

    def orders(self) -> tuple[OrderAck, ...]:
        """Working orders. Always none: every order fills or is refused at once."""
        return ()

    def market_spec(self, symbol: str) -> Instrument:
        return get_instrument(symbol)

    def place_order(self, order: Order) -> OrderAck:
        if order.client_ref in self._seen:
            raise DuplicateClientRef(
                f"client_ref {order.client_ref!r} already dealt as {self._seen[order.client_ref]}"
            )
        instrument = get_instrument(order.instrument)
        price, mid, half = self._price(instrument, order.direction)
        now = self.clock()
        ref = self._next_ref()
        self._seen[order.client_ref] = ref
        fill = Fill(
            order_id=order.order_id,
            instrument=order.instrument,
            direction=order.direction,
            filled_size=float(order.size),
            filled_price=price,
            requested_price=mid,
            filled_at=now,
            spread_paid=half,
            commission=0.0,
            slippage=0.0,
            venue_ref=ref,
        )
        self.fills.append(fill)
        self._positions[ref] = Position(
            instrument=order.instrument,
            direction=order.direction,
            size=float(order.size),
            entry_price=price,
            entry_time=now,
            stop_loss=float(order.stop_loss or 0.0),
            take_profit_targets=[float(order.take_profit)] if order.take_profit is not None else [],
            strategy_id=self.strategy_id,
            venue_ref=ref,
        )
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=ref,
            client_ref=order.client_ref,
            order_id=order.order_id,
            submitted_at=now,
            acked_at=now,
            fill=fill,
            message="paper_forward_filled_at_last_close",
            raw={"filled_size": float(order.size), "mid": mid},
        )

    def _held(self, position: Position) -> Position:
        ref = str(position.venue_ref or "")
        held = self._positions.get(ref)
        if held is None:
            raise PositionNotFound(f"paper venue holds no position {ref!r}")
        return held

    def _exit_fill(self, held: Position, size: float, client_ref: str) -> Fill:
        instrument = get_instrument(held.instrument)
        price, mid, half = self._price(instrument, held.direction.opposite)
        fill = Fill(
            order_id=client_ref,
            instrument=held.instrument,
            direction=held.direction.opposite,
            filled_size=float(size),
            filled_price=price,
            requested_price=mid,
            filled_at=self.clock(),
            spread_paid=half,
            venue_ref=held.venue_ref,
            partial=size + 1e-9 < held.size,
        )
        self.fills.append(fill)
        return fill

    def close_position(
        self, position: Position, *, client_ref: str, reason: str = ""
    ) -> OrderAck:
        held = self._held(position)
        fill = self._exit_fill(held, held.size, client_ref)
        del self._positions[str(held.venue_ref)]
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=str(held.venue_ref),
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=fill.filled_at,
            acked_at=fill.filled_at,
            fill=fill,
            message=reason or "closed",
        )

    def close_partial(
        self, position: Position, *, size: float, client_ref: str, reason: str = ""
    ) -> OrderAck:
        held = self._held(position)
        if size <= 0 or size > held.size + 1e-9:
            raise BrokerRejected(
                f"partial close of {size} is not within the open size {held.size}"
            )
        fill = self._exit_fill(held, size, client_ref)
        remaining = held.size - float(size)
        if remaining <= 1e-9:
            del self._positions[str(held.venue_ref)]
        else:
            self._positions[str(held.venue_ref)] = replace(held, size=remaining)
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=str(held.venue_ref),
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=fill.filled_at,
            acked_at=fill.filled_at,
            fill=fill,
            message=reason or "partially_closed",
        )

    def amend_position(
        self,
        position: Position,
        *,
        stop_loss: float | None,
        take_profit: float | None,
        client_ref: str,
        reason: str = "",
    ) -> OrderAck:
        held = self._held(position)
        if stop_loss is None and take_profit is None:
            raise BrokerRejected("an amendment that changes nothing is a caller bug")
        if stop_loss is not None:
            held.stop_loss = float(stop_loss)
        if take_profit is not None:
            held.take_profit_targets = [float(take_profit)]
        now = self.clock()
        return OrderAck(
            status=OrderStatus.ACCEPTED,
            broker_ref=str(held.venue_ref),
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=now,
            acked_at=now,
            message=reason or "amended",
        )
