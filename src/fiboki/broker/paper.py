"""Paper broker: the backtester's POSITION MANAGER, driven as a live venue.

V1 had a paper bot and a backtester with **separately written** execution
logic. Nobody could say whether a paper divergence was alpha decay or a
disagreement between two code paths, which made paper trading useless as
evidence -- its one job.

V2's first answer was to share the fill model: this adapter imported
:class:`fiboki.sim.fills.FillSimulator` and re-implemented the per-bar loop
around it. That held exactly as long as the two loops stayed the same shape.
They did not. ``backtest/engine.py`` grew the full DSL exit vocabulary --
multi-leg partial take-profits with allocations, trailing stops with
``activate_after_r``, breakeven, time stops, cooldown, reversal and event
blackouts -- and this adapter kept one take-profit and no trail, so every paper
number was a number for a different strategy from the one the backtest reported.

So the loop is shared now, not just the fill model. Everything that decides what
happens to an open position lives in :class:`fiboki.backtest.position.
PositionBook`, and this adapter drives the same object the engine does:

    1. financing for nights crossed since the previous bar
    2. reversal closes scheduled by an earlier opposite signal
    3. fill pending orders at this bar's OPEN
    4. resolve exits, INCLUDING positions opened on this very bar
    5. mark to market on this bar's CLOSE
    6. bankruptcy guard

Step 7 of the engine's loop -- "the strategy sees the closed bar and may emit
signals" -- is deliberately NOT here. Signals are generated upstream, sized once
by :func:`fiboki.portfolio.sizing.size_trade`, and arrive as orders via
:meth:`PaperBroker.place_order` after :meth:`on_bar` returns. That is exactly
where the engine assigns its sequence numbers, so the RNG streams line up --
and the sequence counter itself is now the book's, so an extra increment on one
side and not the other is no longer possible.

``tests/integration/test_paper_backtest_parity.py`` runs identical bars through
:class:`~fiboki.backtest.engine.BacktestEngine` and this adapter and asserts
byte-identical fills and P&L across the whole exit vocabulary. That is the test
V1 never had, and it is the only reason to believe a paper result means
anything.

One exit policy per broker, for the same reason as one per engine run
----------------------------------------------------------------------
:class:`PaperConfig` carries a single :class:`~fiboki.backtest.exits.ExitPolicy`,
because a :class:`~fiboki.backtest.engine.BacktestEngine` run carries a single
one and parity is asserted against a run. Two strategies with different exit
vocabularies need two brokers, exactly as they need two backtests.

The operational risk this creates, stated rather than buried
-------------------------------------------------------------
A venue holds ONE stop and ONE limit per position. A multi-leg scale-out and a
trailing stop are therefore managed in OUR process: the venue is told the hard
stop and the first target, and every subsequent leg and every trail step is an
instruction we send when a bar closes. **If this process dies, the position
keeps whatever stop and target were last attached at the venue and nothing
trails it.** That is a real operational exposure, it is not fixable by writing
better client code, and it belongs in ``docs/v2/USER_ACTIONS.md`` rather than in
a docstring nobody reads at 03:00.

Known, deliberate difference: a real paper deployment sees bars arrive one at a
time and cannot see the future, whereas the backtester holds the whole frame.
Structurally this adapter is in the live position -- it is handed one bar and
never sees another -- which is why parity holds rather than being asserted.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from fiboki.backtest.exits import DEFAULT_EXIT_POLICY, BlackoutSource, ExitPolicy
from fiboki.backtest.position import (
    BarSlice,
    BookConfig,
    ManagedPosition,
    PendingEntry,
    PositionBook,
)
from fiboki.broker.base import (
    BrokerAdapter,
    BrokerHealth,
    DuplicateClientRef,
    OrderAck,
    OrderStatus,
)
from fiboki.broker.position_manager import BookDriver, queue_entry_for_order
from fiboki.core.contracts import (
    AccountState,
    Fill,
    Order,
    Position,
    Trade,
)
from fiboki.core.enums import ExecutionMode, ExitReason, Provenance
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import FxRateSource
from fiboki.sim.fills import (
    AlwaysOpenCalendar,
    Bar,
    FillSimulator,
    IntrabarPolicy,
    SessionCalendar,
)
from fiboki.sim.profiles import IG_REALISTIC, ExecutionProfile

__all__ = ["USER_ACTION_NOTE", "PaperBroker", "PaperConfig"]


USER_ACTION_NOTE = (
    "CLIENT-SIDE EXITS DEPEND ON THE WORKER BEING ALIVE.\n"
    "A venue -- IG, OANDA, and the paper adapter that models them -- holds "
    "exactly ONE stop and ONE limit per position. Every strategy document that "
    "declares more than that (a multi-leg scale-out, a trailing stop, a "
    "breakeven move, a time stop) is therefore managed by OUR process: the venue "
    "is given the hard stop and the first target, and every subsequent leg and "
    "every trail step is an instruction we send when a bar closes.\n"
    "CONSEQUENCE: if the live worker dies, the position does NOT become "
    "unprotected -- the hard stop stays attached at the venue -- but it stops "
    "being managed. The trail freezes at wherever it last moved to, the second "
    "and third take-profit legs are never placed, and the time stop never fires. "
    "A position intended to bank half at 1.5R and trail the rest will instead "
    "run to a stale stop or to the original hard stop.\n"
    "This is NOT fixable in client code and is not a defect in it. The mitigation "
    "is operational:\n"
    "  1. Alert on worker heartbeat staleness (obs/alerts.py HEARTBEAT_STALE, "
    "WORKER_DOWN) and treat it as a position-management incident, not just an "
    "infrastructure one.\n"
    "  2. After any restart, run `fiboki broker reconcile` BEFORE trading: the "
    "worker does this in resume(), and an orphan found there is a real position "
    "whose management we had lost.\n"
    "  3. Prefer documents whose FIRST leg and hard stop alone are an acceptable "
    "outcome, because that is the outcome a dead worker produces.\n"
    "  4. Treat a long trail-only document (donchian_breakout_atr) as the "
    "highest-exposure case: with no take-profit attached at the venue at all, an "
    "unmanaged position has only its hard stop.\n"
    "The backtest models the MANAGED case, so a backtest figure assumes a worker "
    "that never dies. That assumption is not modelled anywhere and should be "
    "read as an optimistic one."
)


@dataclass(frozen=True, slots=True)
class PaperConfig:
    """Mirrors the fields of ``BacktestConfig`` that affect execution.

    Anything here that disagrees with the backtest config used for the same
    strategy makes the parity test meaningless, so keep them constructed from
    one source in production code. ``exit_policy`` is the same object the engine
    is handed; a paper run with the default policy against a backtest run with a
    trailing stop is two different strategies wearing one name.
    """

    initial_balance: float
    account_ccy: str = "GBP"
    profile: ExecutionProfile = IG_REALISTIC
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.STOP_FIRST
    max_concurrent: int = 1
    max_per_instrument: int = 1
    charge_financing: bool = True
    financing_rollover_hour_utc: int = 21
    bankruptcy_equity: float = 0.0
    reject_on_stale: bool = False
    strategy_id: str = "unnamed"
    provenance: Provenance = Provenance.PAPER
    exit_policy: ExitPolicy = DEFAULT_EXIT_POLICY

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.max_concurrent < 1 or self.max_per_instrument < 1:
            raise ValueError("max_concurrent and max_per_instrument must be >= 1")


@dataclass(slots=True)
class _PaperPending:
    """The venue-side record of a queued order: the ack, not the economics."""

    entry: PendingEntry
    order: Order
    broker_ref: str


class PaperBroker(BrokerAdapter):
    """A venue that manages positions exactly as the backtester does."""

    mode = ExecutionMode.PAPER
    venue_name = "paper"

    def __init__(
        self,
        *,
        config: PaperConfig,
        fx: FxRateSource,
        calendar: SessionCalendar | None = None,
        blackout: BlackoutSource | None = None,
    ) -> None:
        if fx is None:
            raise ValueError(
                "PaperBroker requires an FxRateSource. A paper ledger denominated "
                "in the wrong currency is worse than no paper ledger."
            )
        self.config = config
        self.fx = fx
        self.sim = FillSimulator(
            profile=config.profile,
            intrabar_policy=config.intrabar_policy,
            calendar=calendar or AlwaysOpenCalendar(),
            reject_on_stale=config.reject_on_stale,
        )
        self.book = PositionBook(
            sim=self.sim,
            fx=fx,
            config=BookConfig(
                account_ccy=config.account_ccy,
                max_concurrent=config.max_concurrent,
                max_per_instrument=config.max_per_instrument,
                charge_financing=config.charge_financing,
                financing_rollover_hour_utc=config.financing_rollover_hour_utc,
                strategy_id=config.strategy_id,
                provenance=config.provenance,
            ),
            policy=config.exit_policy,
            blackout=blackout,
            initial_balance=config.initial_balance,
            latency_bars=config.profile.latency_bars,
            financing_profile=config.profile.financing,
        )

        self.peak_equity = float(config.initial_balance)
        self.equity = float(config.initial_balance)
        self.unrealised = 0.0
        self.exposure = 0.0
        self.bankrupt = False

        self.fills: list[Fill] = []
        self.equity_curve: list[dict[str, Any]] = []

        # The per-bar mechanics live in BookDriver, which the IG and OANDA
        # position managers drive too. This adapter having its own copy of the
        # monotonic counter and the previous-bar bookkeeping is exactly how the
        # paper and backtest loops drifted apart the first time.
        self._driver = BookDriver(self.book)
        self._seen_client_refs: dict[str, str] = {}
        self._plan_strategies: dict[str, str] = {}
        self._acks: dict[str, OrderAck] = {}
        self._pending_by_seq: dict[int, _PaperPending] = {}
        self._connected = True

    # ---------------------------------------------- the book's state, read

    @property
    def balance(self) -> float:
        return self.book.balance

    @property
    def realised(self) -> float:
        return self.book.realised

    @property
    def trades(self) -> list[Trade]:
        return self.book.trades

    @property
    def rejections(self) -> dict[str, int]:
        return self.book.rejections

    @property
    def exit_legs(self) -> list[Any]:
        """The per-FILL ledger. A scaled-out position contributes several."""
        return self.book.exit_legs

    @property
    def costs(self) -> Any:
        return self.book.costs

    # ------------------------------------------------------------ plumbing

    @property
    def _bar_index(self) -> int:
        return self._driver.bar_index

    @property
    def _now(self) -> pd.Timestamp | None:
        return self._driver.now

    @property
    def _last_bar(self) -> dict[str, Bar]:
        """The most recent bar per instrument. Read by the risk context builder
        for a mark price, so it stays part of this adapter's surface."""
        return self._driver.last_bars

    def set_bar_interval(self, symbol: str, interval: pd.Timedelta) -> None:
        """Declare the expected bar spacing, used for staleness detection.

        The backtester derives this from the median spacing of the whole frame.
        A live feed cannot, so it must be told -- and a paper run that is not
        told will not detect stale prices, which is why this is explicit rather
        than guessed.
        """
        self._driver.set_bar_interval(symbol, interval)

    def register_plan(self, plan_id: str, strategy_id: str) -> None:
        """Associate a plan with its strategy for attribution on the Trade row.

        An ``Order`` carries no ``strategy_id`` -- deliberately, since a venue
        has no business knowing which alpha produced a ticket. The execution
        service registers the association so the paper ledger can attribute a
        trade exactly as the backtester does.
        """
        self._plan_strategies[plan_id] = strategy_id

    def _strategy_for(self, plan_id: str) -> str:
        return self._plan_strategies.get(plan_id) or self.config.strategy_id

    # ----------------------------------------------------------- the clock

    def on_bar(
        self,
        bars: Mapping[str, Bar],
        *,
        bar_index: int,
        timestamp: pd.Timestamp | None = None,
        series: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        """Advance the venue by one timeline step. Drives the engine's book.

        ``series`` carries the indicator columns a trailing rule reads (an ATR,
        a Kijun) for THIS bar. A policy that trails on a column nobody supplies
        never moves its stop, which is the exact defect the exit policy exists
        to fix, so the book is told and the absence is visible rather than
        silently benign.
        """
        result, view = self._driver.advance(
            bars, bar_index=bar_index, timestamp=timestamp, series=series
        )
        self._record_entries(result, view.timestamp)
        self._mark_to_market(view)

        if self.equity <= self.config.bankruptcy_equity:
            self.bankrupt = True
            self.force_close_all(bars, reason=ExitReason.MARGIN_CALL)
            self.book.pending = []
            self.book.pending_closes = []
            self._pending_by_seq.clear()

    def _slice(
        self,
        bars: Mapping[str, Bar],
        bar_index: int,
        ts: pd.Timestamp,
        prev_ts: pd.Timestamp | None,
        series: Mapping[str, Mapping[str, float]] | None,
    ) -> BarSlice:
        return self._driver.slice_for(
            bars,
            bar_index=bar_index,
            timestamp=ts,
            previous_timestamp=prev_ts,
            series=series,
        )

    def _record_entries(self, result: Any, ts: pd.Timestamp) -> None:
        """Turn the book's entry decisions into venue acks and Fill rows."""
        for event in result.entries:
            pending = self._pending_by_seq.pop(event.pending.seq, None)
            if pending is None:  # pragma: no cover - defensive
                continue
            order = pending.order
            if not event.filled:
                self._acks[order.client_ref] = OrderAck(
                    status=OrderStatus.REJECTED,
                    broker_ref=pending.broker_ref,
                    client_ref=order.client_ref,
                    order_id=order.order_id,
                    submitted_at=ts,
                    acked_at=ts,
                    message=event.reason,
                )
                continue
            fill = event.fill
            managed = event.managed
            if managed is not None:
                managed.position.venue_ref = pending.broker_ref
            recorded = Fill(
                order_id=order.order_id,
                instrument=order.instrument,
                direction=order.direction,
                filled_size=fill.filled_size,
                filled_price=fill.filled_price,
                requested_price=fill.mid_price,
                filled_at=ts,
                spread_paid=fill.half_spread,
                commission=fill.costs.commission_amount,
                slippage=fill.slippage,
                venue_ref=pending.broker_ref,
                partial=fill.partial,
            )
            self.fills.append(recorded)
            self._acks[order.client_ref] = OrderAck(
                status=OrderStatus.FILLED,
                broker_ref=pending.broker_ref,
                client_ref=order.client_ref,
                order_id=order.order_id,
                submitted_at=ts,
                acked_at=ts,
                fill=recorded,
                message="paper_filled",
            )

    def _mark_to_market(self, view: BarSlice) -> None:
        unrealised, exposure = self.book.mark_to_market(view)
        self.unrealised = unrealised
        self.exposure = exposure
        self.equity = self.book.balance + unrealised
        self.peak_equity = max(self.peak_equity, self.equity)
        self.equity_curve.append(
            {
                "timestamp": view.timestamp,
                "balance": self.book.balance,
                "equity": self.equity,
                "unrealised": unrealised,
                "exposure": exposure,
                "open_positions": len(self.book.open),
            }
        )

    # -------------------------------------------------------- flatten path

    def force_close_all(
        self,
        bars: Mapping[str, Bar],
        *,
        reason: ExitReason = ExitReason.RISK_HALT,
    ) -> tuple[Trade, ...]:
        """Close every open position at market NOW. Used by kill-switch FLATTEN."""
        ts = self._now
        if ts is None:
            raise ValueError("force_close_all called before any bar was delivered")
        view = self._slice(bars, self._bar_index, ts, self._now, None)
        result = self.book.flatten(view, reason=reason)
        self._mark_to_market(view)
        return tuple(result.trades)

    def finish(self, bars: Mapping[str, Bar]) -> None:
        """End of data: close what is left, flagged honestly as END_OF_DATA."""
        if self.book.open:
            self.force_close_all(bars, reason=ExitReason.END_OF_DATA)

    # ------------------------------------------------- BrokerAdapter API

    def connect(self) -> BrokerHealth:
        self._connected = True
        return self.health()

    def health(self) -> BrokerHealth:
        return BrokerHealth(
            connected=self._connected,
            score=1.0 if self._connected else 0.0,
            checked_at=self._now or pd.Timestamp.now(tz="UTC"),
            message="paper venue",
            latency_ms=0.0,
        )

    def account(self) -> AccountState:
        return AccountState(
            balance=self.book.balance,
            equity=self.equity,
            currency=self.config.account_ccy,
            margin_used=self._margin_used(),
            open_positions=len(self.book.open),
            realised_pnl=self.book.realised,
            unrealised_pnl=self.unrealised,
            peak_equity=self.peak_equity,
        )

    def _margin_used(self) -> float:
        total = 0.0
        for op in self.book.open:
            leverage = op.instrument.retail_leverage or 1.0
            mark = self._last_bar.get(op.position.instrument)
            price = mark.close if mark else op.entry_mid
            rate = op.entry_fx
            total += abs(price * op.position.size * op.instrument.contract_size * rate) / leverage
        return total

    def positions(self) -> tuple[Position, ...]:
        return tuple(op.position for op in self.book.open)

    def orders(self) -> tuple[OrderAck, ...]:
        ts = self._now or pd.Timestamp.now(tz="UTC")
        return tuple(
            OrderAck(
                status=OrderStatus.ACCEPTED,
                broker_ref=p.broker_ref,
                client_ref=p.order.client_ref,
                order_id=p.order.order_id,
                submitted_at=ts,
                acked_at=ts,
                message="working",
            )
            for p in self._pending_by_seq.values()
        )

    def market_spec(self, symbol: str) -> Instrument:
        return get_instrument(symbol)

    def place_order(self, order: Order) -> OrderAck:
        """Queue ``order`` for this instrument's NEXT bar open.

        Note what is absent: no sizing. ``order.size`` was decided once,
        upstream, and is queued unchanged. The take-profit LADDER is carried
        through verbatim; an order that names only ``take_profit`` is read as a
        single full-size target, which is what it has always meant.
        """
        if order.client_ref in self._seen_client_refs:
            raise DuplicateClientRef(
                f"client_ref {order.client_ref!r} already submitted to the paper venue "
                f"as {self._seen_client_refs[order.client_ref]}"
            )
        if self._bar_index < 0:
            raise ValueError("place_order before the first bar: the venue has no price yet")

        seq = self.book.next_seq()
        broker_ref = f"PAPER-{seq:08d}"
        self._seen_client_refs[order.client_ref] = broker_ref
        ts = self._now or pd.Timestamp.now(tz="UTC")

        entry = queue_entry_for_order(
            self.book,
            order,
            bar_index=self._bar_index,
            latency_bars=self.config.profile.latency_bars,
            strategy_id=self._strategy_for(order.plan_id),
            ref=broker_ref,
            # The sequence number was taken ABOVE, to name the broker
            # reference. Taking a second one here would advance the book's
            # counter once more on the paper path than on the engine's, and the
            # counter feeds the fill simulator's counter-based RNG -- so the two
            # price paths would silently decorrelate and parity would fail in a
            # way no individual assertion points at.
            seq=seq,
        )
        self._pending_by_seq[seq] = _PaperPending(entry, order, broker_ref)

        ack = OrderAck(
            status=OrderStatus.ACCEPTED,
            broker_ref=broker_ref,
            client_ref=order.client_ref,
            order_id=order.order_id,
            submitted_at=ts,
            acked_at=ts,
            message="queued_for_next_bar_open",
        )
        self._acks[order.client_ref] = ack
        return ack

    def close_position(
        self, position: Position, *, client_ref: str, reason: str = ""
    ) -> OrderAck:
        ts = self._now or pd.Timestamp.now(tz="UTC")
        target: ManagedPosition | None = None
        for op in self.book.open:
            if op.position.position_id == position.position_id:
                target = op
                break
        if target is None:
            raise KeyError(f"No open paper position {position.position_id}")

        sym = target.position.instrument
        if self._last_bar.get(sym) is None:
            raise ValueError(f"No bar available to close {sym}")
        view = self._slice({}, self._bar_index, ts, self._now, None)
        result = self.book.flatten(
            view, reason=ExitReason.RISK_HALT, positions=[target]
        )
        self._mark_to_market(view)
        exit_fill = result.fills[-1]
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=target.position.venue_ref or f"PAPER-CLOSE-{target.seq:08d}",
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=ts,
            acked_at=ts,
            fill=Fill(
                order_id=client_ref,
                instrument=sym,
                direction=target.position.direction.opposite,
                filled_size=result.legs[-1].size,
                filled_price=exit_fill.exit_price,
                requested_price=exit_fill.mid_price,
                filled_at=ts,
                spread_paid=exit_fill.half_spread,
                slippage=exit_fill.slippage,
                venue_ref=target.position.venue_ref,
            ),
            message=reason or "closed",
        )

    def ack_for(self, client_ref: str) -> OrderAck | None:
        return self._acks.get(client_ref)

    # ----------------------------------------------------------- reporting

    def equity_frame(self) -> pd.DataFrame:
        if not self.equity_curve:
            return pd.DataFrame()
        frame = pd.DataFrame(self.equity_curve).set_index("timestamp")
        frame.index = pd.DatetimeIndex(frame.index, name="timestamp")
        return frame


def bars_at(frames: Mapping[str, pd.DataFrame], ts: pd.Timestamp) -> dict[str, Bar]:
    """Helper for drivers: the bars that exist at exactly ``ts``."""
    out: dict[str, Bar] = {}
    for sym, frame in frames.items():
        if ts in frame.index:
            row = frame.loc[ts]
            out[sym] = Bar(
                ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            )
    return out
