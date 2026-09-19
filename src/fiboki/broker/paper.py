"""Paper broker: the backtester's fill model, driven as a live venue.

V1 had a paper bot and a backtester with **separately written** execution
logic. Nobody could say whether a paper divergence was alpha decay or a
disagreement between two code paths, which made paper trading useless as
evidence -- its one job.

This adapter does not reimplement filling. It imports
:class:`fiboki.sim.fills.FillSimulator` and drives it with the same profile,
the same intrabar policy, the same calendar, the same bar-index/sequence RNG
derivation and the same per-bar ordering as
:class:`fiboki.backtest.engine.BacktestEngine`:

    1. financing for nights crossed since the previous bar
    2. fill pending orders at this bar's OPEN
    3. resolve exits (including positions opened on this very bar -- gap risk
       is real and a position is exposed the instant it exists)
    4. mark to market on this bar's CLOSE
    5. bankruptcy guard

Step 6 of the engine's loop -- "the strategy sees the closed bar and may emit
signals" -- is deliberately NOT here. Signals are generated upstream, sized once
by :func:`fiboki.portfolio.sizing.size_trade`, and arrive as orders via
:meth:`PaperBroker.place_order` after :meth:`on_bar` returns. That is exactly
where the engine assigns its sequence numbers, so the RNG streams line up.

``tests/integration/test_paper_backtest_parity.py`` runs identical bars through
:class:`~fiboki.backtest.engine.BacktestEngine` and this adapter and asserts
byte-identical fills and P&L. That is the test V1 never had, and it is the only
reason to believe a paper result means anything.

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

from fiboki.broker.base import (
    BrokerAdapter,
    BrokerHealth,
    DuplicateClientRef,
    OrderAck,
    OrderStatus,
)
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
    LegCosts,
    SessionCalendar,
)
from fiboki.sim.profiles import IG_REALISTIC, ExecutionProfile

__all__ = ["PaperBroker", "PaperConfig"]


@dataclass(frozen=True, slots=True)
class PaperConfig:
    """Mirrors the fields of ``BacktestConfig`` that affect execution.

    Anything here that disagrees with the backtest config used for the same
    strategy makes the parity test meaningless, so keep them constructed from
    one source in production code.
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

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.max_concurrent < 1 or self.max_per_instrument < 1:
            raise ValueError("max_concurrent and max_per_instrument must be >= 1")


@dataclass(slots=True)
class _PaperPosition:
    seq: int
    position: Position
    instrument: Instrument
    entry_mid: float
    entry_bar_index: int
    take_profit: float | None
    entry_costs_account: dict[str, float]
    financing_account: float = 0.0
    last_financing_time: pd.Timestamp | None = None
    entry_fx: float = 1.0


@dataclass(slots=True)
class _PaperPending:
    seq: int
    order: Order
    instrument: Instrument
    actionable_index: int
    created_index: int
    broker_ref: str


class PaperBroker(BrokerAdapter):
    """A venue that fills exactly as the backtester does, one bar at a time."""

    mode = ExecutionMode.PAPER
    venue_name = "paper"

    def __init__(
        self,
        *,
        config: PaperConfig,
        fx: FxRateSource,
        calendar: SessionCalendar | None = None,
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

        self.balance = float(config.initial_balance)
        self.realised = 0.0
        self.peak_equity = float(config.initial_balance)
        self.equity = float(config.initial_balance)
        self.unrealised = 0.0
        self.exposure = 0.0
        self.bankrupt = False

        self._pending: list[_PaperPending] = []
        self._open: list[_PaperPosition] = []
        self.trades: list[Trade] = []
        self.rejections: dict[str, int] = {}
        self.fills: list[Fill] = []
        self.equity_curve: list[dict[str, Any]] = []

        self._seq = 0
        self._bar_index = -1
        self._now: pd.Timestamp | None = None
        self._last_bar: dict[str, Bar] = {}
        self._prev_bar_time: dict[str, pd.Timestamp] = {}
        self._intervals: dict[str, pd.Timedelta] = {}
        self._seen_client_refs: dict[str, str] = {}
        self._plan_strategies: dict[str, str] = {}
        self._acks: dict[str, OrderAck] = {}
        self._connected = True

    # ------------------------------------------------------------ plumbing

    def _rate(self, ccy: str, when: pd.Timestamp) -> float:
        return float(self.fx.rate(ccy, self.config.account_ccy, when))

    def _costs_to_account(
        self, instrument: Instrument, leg: LegCosts, ts: pd.Timestamp
    ) -> dict[str, float]:
        quote_rate = self._rate(instrument.quote, ts)
        comm_rate = self._rate(leg.commission_ccy, ts)
        return {
            "spread": leg.spread_quote * quote_rate,
            "slippage": leg.slippage_quote * quote_rate,
            "premium": leg.guaranteed_stop_premium_quote * quote_rate,
            "commission": leg.commission_amount * comm_rate,
        }

    @staticmethod
    def _bump(counter: dict[str, int], key: str) -> None:
        counter[key] = counter.get(key, 0) + 1

    def set_bar_interval(self, symbol: str, interval: pd.Timedelta) -> None:
        """Declare the expected bar spacing, used for staleness detection.

        The backtester derives this from the median spacing of the whole frame.
        A live feed cannot, so it must be told -- and a paper run that is not
        told will not detect stale prices, which is why this is explicit rather
        than guessed.
        """
        self._intervals[symbol] = interval

    # ----------------------------------------------------------- the clock

    def on_bar(
        self,
        bars: Mapping[str, Bar],
        *,
        bar_index: int,
        timestamp: pd.Timestamp | None = None,
    ) -> None:
        """Advance the venue by one timeline step. Mirrors the engine's loop."""
        if bar_index <= self._bar_index:
            raise ValueError(
                f"Paper bars must advance monotonically; got {bar_index} after "
                f"{self._bar_index}. Replaying a bar would double-charge financing."
            )
        ts = timestamp
        if ts is None:
            if not bars:
                raise ValueError("on_bar needs either bars or an explicit timestamp")
            ts = next(iter(bars.values())).timestamp
        prev_ts = self._now
        self._bar_index = bar_index
        self._now = ts

        cfg = self.config

        # Roll the staleness bookkeeping BEFORE anything prices a fill. The
        # engine's ``_previous_bar_time`` is the bar before the current one, so
        # ours must be too -- a one-bar offset here would silently change which
        # fills are treated as stale and break parity in a way that only shows
        # up at weekend gaps.
        for _sym, _bar in bars.items():
            if _sym in self._last_bar:
                self._prev_bar_time[_sym] = self._last_bar[_sym].timestamp
            self._last_bar[_sym] = _bar

        # -- 1. financing -----------------------------------------------
        if cfg.charge_financing and prev_ts is not None:
            for op in self._open:
                nights = _nights_between(
                    op.last_financing_time or op.position.entry_time,
                    ts,
                    cfg.financing_rollover_hour_utc,
                )
                if nights:
                    mark = self._mark(op.position.instrument, bars)
                    charge_quote = (
                        cfg.profile.financing.nightly_charge(
                            op.instrument, op.position.direction, op.position.size, mark
                        )
                        * nights
                    )
                    ccy = cfg.profile.financing.currency_for(op.instrument)
                    op.financing_account += charge_quote * self._rate(ccy, ts)
                    op.position.financing_accrued = op.financing_account
                    op.last_financing_time = ts

        # -- 2. fill pending at this bar's OPEN --------------------------
        still: list[_PaperPending] = []
        for pending in self._pending:
            if pending.actionable_index > bar_index:
                still.append(pending)
                continue
            sym = pending.instrument.symbol
            if sym not in bars:
                still.append(pending)  # instrument not trading: wait
                continue
            if len(self._open) >= cfg.max_concurrent:
                self._bump(self.rejections, "max_concurrent")
                self._finalise_ack(pending, OrderStatus.REJECTED, "max_concurrent")
                continue
            if (
                sum(1 for p in self._open if p.position.instrument == sym)
                >= cfg.max_per_instrument
            ):
                self._bump(self.rejections, "max_per_instrument")
                self._finalise_ack(pending, OrderStatus.REJECTED, "max_per_instrument")
                continue

            bar = bars[sym]
            order = pending.order
            fill = self.sim.simulate_entry(
                instrument=pending.instrument,
                direction=order.direction,
                size=order.size,
                bar=bar,
                bar_index=bar_index,
                stop=order.stop_loss if order.stop_loss is not None else 0.0,
                sequence=pending.seq,
                previous_time=self._prev_bar_time.get(sym),
                bar_interval=self._intervals.get(sym),
            )
            if not fill.filled:
                reason = fill.reject_reason.value if fill.reject_reason else "unknown"
                self._bump(self.rejections, reason)
                self._finalise_ack(pending, OrderStatus.REJECTED, reason)
                continue

            fx_rate = self._rate(pending.instrument.quote, ts)
            entry_costs = self._costs_to_account(pending.instrument, fill.costs, ts)
            tp = order.take_profit
            pos = Position(
                instrument=sym,
                direction=order.direction,
                size=fill.filled_size,
                entry_price=fill.mid_price,
                entry_time=ts,
                stop_loss=fill.effective_stop,
                take_profit_targets=[tp] if tp is not None else [],
                strategy_id=self._strategy_for(order.plan_id),
                venue_ref=pending.broker_ref,
            )
            self._seq += 1
            self._open.append(
                _PaperPosition(
                    seq=self._seq,
                    position=pos,
                    instrument=pending.instrument,
                    entry_mid=fill.mid_price,
                    entry_bar_index=bar_index,
                    take_profit=tp,
                    entry_costs_account=entry_costs,
                    last_financing_time=ts,
                    entry_fx=fx_rate,
                )
            )
            recorded = Fill(
                order_id=order.order_id,
                instrument=sym,
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
        self._pending = still

        # -- 3. exits ----------------------------------------------------
        survivors: list[_PaperPosition] = []
        for op in self._open:
            sym = op.position.instrument
            if sym not in bars:
                survivors.append(op)
                continue
            bar = bars[sym]
            op.position.update_excursion(bar.high, bar.low)
            if bar_index > op.entry_bar_index:
                op.position.bars_held += 1

            exit_fill = self.sim.resolve_exit(
                instrument=op.instrument,
                direction=op.position.direction,
                size=op.position.size,
                bar=bar,
                bar_index=bar_index,
                stop=op.position.stop_loss,
                take_profit=op.take_profit,
                sequence=op.seq,
                previous_time=self._prev_bar_time.get(sym),
                bar_interval=self._intervals.get(sym),
            )
            if not exit_fill.exited:
                survivors.append(op)
                continue
            trade = self._close(op, exit_fill, ts)
            self.trades.append(trade)
            self.realised += trade.net_pnl
            self.balance += trade.net_pnl
        self._open = survivors

        # -- 4. mark to market on the close -----------------------------
        self._mark_to_market(bars, ts)

        # -- 5. bankruptcy guard ----------------------------------------
        if self.equity <= self.config.bankruptcy_equity:
            self.bankrupt = True
            self.force_close_all(bars, reason=ExitReason.MARGIN_CALL)
            self._pending = []

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

    def _mark(self, symbol: str, bars: Mapping[str, Bar]) -> float:
        if symbol in bars:
            return bars[symbol].close
        last = self._last_bar.get(symbol)
        if last is None:
            raise KeyError(f"No mark available for {symbol}")
        return last.close

    def _mark_to_market(self, bars: Mapping[str, Bar], ts: pd.Timestamp) -> None:
        unrealised = 0.0
        exposure = 0.0
        for op in self._open:
            mark = self._mark(op.position.instrument, bars)
            rate = self._rate(op.instrument.quote, ts)
            unrealised += op.position.unrealised_quote(mark, op.instrument.contract_size) * rate
            unrealised -= op.financing_account
            exposure += abs(mark * op.position.size * op.instrument.contract_size * rate)
        self.unrealised = unrealised
        self.exposure = exposure
        self.equity = self.balance + unrealised
        self.peak_equity = max(self.peak_equity, self.equity)
        self.equity_curve.append(
            {
                "timestamp": ts,
                "balance": self.balance,
                "equity": self.equity,
                "unrealised": unrealised,
                "exposure": exposure,
                "open_positions": len(self._open),
            }
        )

    def _close(self, op: _PaperPosition, exit_fill, ts: pd.Timestamp) -> Trade:
        """Identical arithmetic to ``BacktestEngine._close``.

        ``net == gross - spread - commission - slippage - financing`` holds
        exactly, and the guaranteed-stop premium is folded into commission so
        that identity cannot be broken by adding a cost category.
        """
        cfg = self.config
        instr = op.instrument
        exit_costs = self._costs_to_account(instr, exit_fill.costs, ts)
        fx_rate = self._rate(instr.quote, ts)

        gross_quote = (
            (exit_fill.mid_price - op.entry_mid)
            * op.position.direction.sign
            * op.position.size
            * instr.contract_size
        )
        gross = gross_quote * fx_rate

        spread_cost = op.entry_costs_account["spread"] + exit_costs["spread"]
        commission = op.entry_costs_account["commission"] + exit_costs["commission"]
        slippage_cost = op.entry_costs_account["slippage"] + exit_costs["slippage"]
        premium = op.entry_costs_account["premium"] + exit_costs["premium"]
        financing = op.financing_account
        commission += premium

        net = gross - spread_cost - commission - slippage_cost - financing
        unit_to_account = op.position.size * instr.contract_size * fx_rate

        return Trade(
            instrument=op.position.instrument,
            direction=op.position.direction,
            size=op.position.size,
            entry_price=op.entry_mid,
            exit_price=exit_fill.mid_price,
            entry_time=op.position.entry_time,
            exit_time=ts,
            exit_reason=exit_fill.reason or ExitReason.END_OF_DATA,
            gross_pnl=gross,
            spread_cost=spread_cost,
            commission=commission,
            slippage_cost=slippage_cost,
            financing_cost=financing,
            net_pnl=net,
            account_ccy=cfg.account_ccy,
            strategy_id=op.position.strategy_id or cfg.strategy_id,
            bars_held=op.position.bars_held,
            max_adverse_excursion=op.position.max_adverse_excursion * unit_to_account,
            max_favourable_excursion=op.position.max_favourable_excursion * unit_to_account,
            provenance=cfg.provenance,
            fx_rate_used=fx_rate,
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
        closed: list[Trade] = []
        for op in list(self._open):
            sym = op.position.instrument
            bar = bars.get(sym) or self._last_bar.get(sym)
            if bar is None:
                continue
            exit_fill = self.sim.market_exit(
                instrument=op.instrument,
                direction=op.position.direction,
                size=op.position.size,
                bar=bar,
                bar_index=self._bar_index,
                reason=reason,
                sequence=op.seq,
                previous_time=self._prev_bar_time.get(sym),
                bar_interval=self._intervals.get(sym),
            )
            trade = self._close(op, exit_fill, ts)
            self.trades.append(trade)
            self.realised += trade.net_pnl
            self.balance += trade.net_pnl
            closed.append(trade)
            self._open.remove(op)
        self._mark_to_market(bars, ts)
        return tuple(closed)

    def finish(self, bars: Mapping[str, Bar]) -> None:
        """End of data: close what is left, flagged honestly as END_OF_DATA."""
        if self._open:
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
            balance=self.balance,
            equity=self.equity,
            currency=self.config.account_ccy,
            margin_used=self._margin_used(),
            open_positions=len(self._open),
            realised_pnl=self.realised,
            unrealised_pnl=self.unrealised,
            peak_equity=self.peak_equity,
        )

    def _margin_used(self) -> float:
        total = 0.0
        for op in self._open:
            leverage = op.instrument.retail_leverage or 1.0
            mark = self._last_bar.get(op.position.instrument)
            price = mark.close if mark else op.entry_mid
            rate = op.entry_fx
            total += abs(price * op.position.size * op.instrument.contract_size * rate) / leverage
        return total

    def positions(self) -> tuple[Position, ...]:
        return tuple(op.position for op in self._open)

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
            for p in self._pending
        )

    def market_spec(self, symbol: str) -> Instrument:
        return get_instrument(symbol)

    def place_order(self, order: Order) -> OrderAck:
        """Queue ``order`` for this instrument's NEXT bar open.

        Note what is absent: no sizing. ``order.size`` was decided once,
        upstream, and is queued unchanged.
        """
        if order.client_ref in self._seen_client_refs:
            raise DuplicateClientRef(
                f"client_ref {order.client_ref!r} already submitted to the paper venue "
                f"as {self._seen_client_refs[order.client_ref]}"
            )
        if self._bar_index < 0:
            raise ValueError("place_order before the first bar: the venue has no price yet")

        self._seq += 1
        broker_ref = f"PAPER-{self._seq:08d}"
        self._seen_client_refs[order.client_ref] = broker_ref
        ts = self._now or pd.Timestamp.now(tz="UTC")
        self._pending.append(
            _PaperPending(
                seq=self._seq,
                order=order,
                instrument=get_instrument(order.instrument),
                actionable_index=self._bar_index + 1 + self.config.profile.latency_bars,
                created_index=self._bar_index,
                broker_ref=broker_ref,
            )
        )
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
        for op in list(self._open):
            if op.position.position_id != position.position_id:
                continue
            sym = op.position.instrument
            bar = self._last_bar.get(sym)
            if bar is None:
                raise ValueError(f"No bar available to close {sym}")
            exit_fill = self.sim.market_exit(
                instrument=op.instrument,
                direction=op.position.direction,
                size=op.position.size,
                bar=bar,
                bar_index=self._bar_index,
                reason=ExitReason.RISK_HALT,
                sequence=op.seq,
                previous_time=self._prev_bar_time.get(sym),
                bar_interval=self._intervals.get(sym),
            )
            trade = self._close(op, exit_fill, ts)
            self.trades.append(trade)
            self.realised += trade.net_pnl
            self.balance += trade.net_pnl
            self._open.remove(op)
            return OrderAck(
                status=OrderStatus.FILLED,
                broker_ref=op.position.venue_ref or f"PAPER-CLOSE-{op.seq:08d}",
                client_ref=client_ref,
                order_id=client_ref,
                submitted_at=ts,
                acked_at=ts,
                fill=Fill(
                    order_id=client_ref,
                    instrument=sym,
                    direction=op.position.direction.opposite,
                    filled_size=op.position.size,
                    filled_price=exit_fill.exit_price,
                    requested_price=exit_fill.mid_price,
                    filled_at=ts,
                    spread_paid=exit_fill.half_spread,
                    slippage=exit_fill.slippage,
                    venue_ref=op.position.venue_ref,
                ),
                message=reason or "closed",
            )
        raise KeyError(f"No open paper position {position.position_id}")

    def _finalise_ack(self, pending: _PaperPending, status: OrderStatus, message: str) -> None:
        ts = self._now or pd.Timestamp.now(tz="UTC")
        self._acks[pending.order.client_ref] = OrderAck(
            status=status,
            broker_ref=pending.broker_ref,
            client_ref=pending.order.client_ref,
            order_id=pending.order.order_id,
            submitted_at=ts,
            acked_at=ts,
            message=message,
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


def _nights_between(last: pd.Timestamp, now: pd.Timestamp, rollover_hour: int) -> int:
    """Identical to the engine's helper: rollover crossings in ``(last, now]``."""
    if now <= last:
        return 0
    anchor = last.normalize() + pd.Timedelta(hours=rollover_hour)
    if anchor <= last:
        anchor += pd.Timedelta(days=1)
    if anchor > now:
        return 0
    return int((now - anchor) // pd.Timedelta(days=1)) + 1


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
