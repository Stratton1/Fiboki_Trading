"""Event-driven, deterministic backtest engine.

Design commitments, each of which exists because V1 violated it
---------------------------------------------------------------
1. **No look-ahead, structurally.** A strategy is handed a *slice* of history
   ending at the current closed bar. Signals it returns become pending orders
   that are actionable no earlier than the NEXT bar's open. There is no code
   path that fills at the signal bar's close. ``tests/unit/test_no_lookahead.py``
   mutates future bars and asserts past trades are byte-identical.
2. **Equity is marked to market every bar.** V1 appended to its equity curve
   only when a trade closed, so a position that ran 40% against you before
   recovering contributed nothing to reported drawdown. Every drawdown,
   Calmar and ulcer figure V1 produced was therefore understated.
3. **Currency conversion is mandatory.** The engine refuses to construct
   without an :class:`~fiboki.core.money.FxRateSource`. Passing
   ``IdentityFxSource()`` is legal only when it can actually satisfy the
   conversions; if it cannot it raises rather than returning 1.0.
4. **Multiple concurrent positions.** V1 held one position at a time, which
   made correlation, concentration and margin effects structurally invisible.
5. **Determinism.** Every container iterated in the engine is either a list in
   insertion order or a sorted sequence. No position or instrument is ever
   ordered by a UUID.

P&L decomposition convention (read this before reading a Trade row)
--------------------------------------------------------------------
``Trade.entry_price`` and ``Trade.exit_price`` are **mid** prices. ``gross_pnl``
is the mid-to-mid move converted to the account currency, and::

    net_pnl == gross_pnl - spread_cost - commission - slippage_cost - financing_cost

holds exactly, so any row can be checked with a calculator. The price actually
dealt is ``entry_price + direction.sign * (half_spread + slippage)``; it is not
stored separately because storing both invites the two to drift apart. Charging
the spread as an explicit cost on BOTH legs is arithmetically identical to
dealing at bid/ask on both legs — and unlike V1, it cannot be half-applied
without the identity above failing.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from fiboki.core.contracts import (
    AccountState,
    Position,
    Signal,
    Trade,
    TradePlan,
)
from fiboki.core.enums import ExitReason, Provenance
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import FxRateSource, round_size
from fiboki.sim.fills import (
    AlwaysOpenCalendar,
    Bar,
    FillSimulator,
    IntrabarPolicy,
    LegCosts,
    SessionCalendar,
)
from fiboki.sim.profiles import IG_REALISTIC, ExecutionProfile

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BarContext",
    "CostBreakdown",
    "FixedFractionalSizer",
    "FixedSizeSizer",
    "PrecomputedSignals",
    "Sizer",
    "Strategy",
    "run_backtest",
]


# --------------------------------------------------------------------------
# Protocols
# --------------------------------------------------------------------------


class Strategy(Protocol):
    def on_bar(self, ctx: BarContext) -> Sequence[Signal]: ...


class Sizer(Protocol):
    def size_for(
        self,
        signal: Signal,
        instrument: Instrument,
        account: AccountState,
        fx_quote_to_account: float,
    ) -> float: ...


# --------------------------------------------------------------------------
# Sizers (risk logic lives here, never inside a strategy)
# --------------------------------------------------------------------------


def _snap_to_step(size: float, step: float, rel_tol: float = 1e-9) -> float:
    """Absorb binary representation error before flooring to the size step.

    ``1000 / (0.0050 * 0.80)`` evaluates to 249999.99999999997 in IEEE 754, not
    250000. ``round_size`` would floor that to 249,999 and lose a unit of
    exposure for a reason that has nothing to do with risk — and worse, the
    loss would depend on the exact FX rate, so the same sizing rule would give
    different answers on different days for no economic reason.

    This snaps a value lying within ``rel_tol`` of a step boundary onto that
    boundary, then leaves the flooring to ``round_size``. The adjustment is at
    most one part in a billion, which is orders of magnitude smaller than any
    instrument's size step, so it cannot manufacture risk the rule did not
    intend.
    """
    if step <= 0 or size <= 0:
        return size
    nearest = round(size / step)
    if nearest > 0 and abs(size - nearest * step) <= rel_tol * max(size, step):
        return nearest * step
    return size


@dataclass(frozen=True, slots=True)
class FixedSizeSizer:
    """Constant size. Useful for golden tests where sizing must not vary."""

    size: float

    def size_for(
        self,
        signal: Signal,
        instrument: Instrument,
        account: AccountState,
        fx_quote_to_account: float,
    ) -> float:
        return round_size(instrument, _snap_to_step(self.size, instrument.size_step))


@dataclass(frozen=True, slots=True)
class FixedFractionalSizer:
    """Risk a fixed fraction of CURRENT equity per trade, capped by leverage.

    ``risk_fraction`` is fraction of equity lost if the stop fills exactly at
    its level. Gaps can and do exceed it — that is the honest part: this is a
    sizing intent, not a loss guarantee, and the engine's realised MAE
    distribution is where you find out how often the intent failed.
    """

    risk_fraction: float = 0.01
    max_leverage: float | None = None

    def size_for(
        self,
        signal: Signal,
        instrument: Instrument,
        account: AccountState,
        fx_quote_to_account: float,
    ) -> float:
        stop_distance = signal.stop_distance
        if stop_distance <= 0:
            return 0.0
        risk_account = account.equity * self.risk_fraction
        if risk_account <= 0:
            return 0.0
        risk_per_unit_account = stop_distance * instrument.contract_size * fx_quote_to_account
        if risk_per_unit_account <= 0:
            return 0.0
        size = risk_account / risk_per_unit_account

        leverage = self.max_leverage or instrument.retail_leverage
        notional_per_unit = (
            signal.reference_price * instrument.contract_size * fx_quote_to_account
        )
        if notional_per_unit > 0:
            size = min(size, account.equity * leverage / notional_per_unit)
        return round_size(instrument, _snap_to_step(size, instrument.size_step))


# --------------------------------------------------------------------------
# A trivial strategy adapter for replaying a fixed signal list
# --------------------------------------------------------------------------


class PrecomputedSignals:
    """Replays a fixed list of signals, keyed by bar time.

    Deliberately restrictive: it asserts that every signal it emits carries the
    bar time it is emitted on, so a test cannot accidentally inject a signal
    dated in the future.
    """

    def __init__(self, signals: Sequence[Signal]) -> None:
        self._by_time: dict[pd.Timestamp, list[Signal]] = {}
        for s in signals:
            self._by_time.setdefault(s.bar_time, []).append(s)

    def on_bar(self, ctx: BarContext) -> Sequence[Signal]:
        return tuple(self._by_time.get(ctx.timestamp, ()))


# --------------------------------------------------------------------------
# Config, context, results
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BacktestConfig:
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
    close_at_end_of_data: bool = True
    strategy_id: str = "unnamed"
    provenance: Provenance = Provenance.BACKTEST

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.max_concurrent < 1 or self.max_per_instrument < 1:
            raise ValueError("max_concurrent and max_per_instrument must be >= 1")
        if not 0 <= self.financing_rollover_hour_utc <= 23:
            raise ValueError("financing_rollover_hour_utc must be an hour of day")

    def fingerprint(self) -> dict[str, object]:
        return {
            "initial_balance": self.initial_balance,
            "account_ccy": self.account_ccy,
            "profile": self.profile.fingerprint(),
            "intrabar_policy": self.intrabar_policy.value,
            "max_concurrent": self.max_concurrent,
            "max_per_instrument": self.max_per_instrument,
            "charge_financing": self.charge_financing,
            "financing_rollover_hour_utc": self.financing_rollover_hour_utc,
            "bankruptcy_equity": self.bankruptcy_equity,
            "reject_on_stale": self.reject_on_stale,
            "close_at_end_of_data": self.close_at_end_of_data,
            "strategy_id": self.strategy_id,
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True, slots=True)
class BarContext:
    """What a strategy is allowed to see: everything up to and including now."""

    timestamp: pd.Timestamp
    bar_index: int
    account: AccountState
    open_positions: tuple[Position, ...]
    _frames: dict[str, pd.DataFrame]
    _cursor: dict[str, int]

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(sorted(self._frames))

    def has_bar(self, instrument: str) -> bool:
        return self._cursor.get(instrument, -1) >= 0

    def history(self, instrument: str) -> pd.DataFrame:
        """Bars 0..current INCLUSIVE. Slicing is what prevents look-ahead."""
        end = self._cursor.get(instrument, -1)
        if end < 0:
            return self._frames[instrument].iloc[:0]
        return self._frames[instrument].iloc[: end + 1]

    def bar(self, instrument: str) -> Bar | None:
        end = self._cursor.get(instrument, -1)
        if end < 0:
            return None
        row = self._frames[instrument].iloc[end]
        return Bar(
            self._frames[instrument].index[end],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )


@dataclass(slots=True)
class CostBreakdown:
    """Totals in the ACCOUNT currency."""

    spread: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    financing: float = 0.0
    guaranteed_stop_premium: float = 0.0
    entry_leg_spread: float = 0.0
    exit_leg_spread: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.spread
            + self.commission
            + self.slippage
            + self.financing
            + self.guaranteed_stop_premium
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "spread": self.spread,
            "commission": self.commission,
            "slippage": self.slippage,
            "financing": self.financing,
            "guaranteed_stop_premium": self.guaranteed_stop_premium,
            "entry_leg_spread": self.entry_leg_spread,
            "exit_leg_spread": self.exit_leg_spread,
            "total": self.total,
        }


@dataclass(slots=True)
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.DataFrame
    costs: CostBreakdown
    config_fingerprint: dict[str, object]
    data_fingerprint: dict[str, object]
    rejections: dict[str, int]
    bankrupt: bool = False
    signals_seen: int = 0
    orders_submitted: int = 0

    # Columns whose values define the ledger. UUIDs are excluded on purpose:
    # they are random by construction and would defeat the determinism test
    # they are most often mistaken for evidence of.
    LEDGER_COLUMNS = (
        "instrument",
        "direction",
        "size",
        "entry_price",
        "exit_price",
        "entry_time",
        "exit_time",
        "exit_reason",
        "gross_pnl",
        "spread_cost",
        "commission",
        "slippage_cost",
        "financing_cost",
        "net_pnl",
        "account_ccy",
        "bars_held",
        "max_adverse_excursion",
        "max_favourable_excursion",
        "fx_rate_used",
    )

    def ledger_frame(self) -> pd.DataFrame:
        rows = []
        for t in self.trades:
            rows.append(
                {
                    "instrument": t.instrument,
                    "direction": t.direction.value,
                    "size": t.size,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "exit_reason": t.exit_reason.value,
                    "gross_pnl": t.gross_pnl,
                    "spread_cost": t.spread_cost,
                    "commission": t.commission,
                    "slippage_cost": t.slippage_cost,
                    "financing_cost": t.financing_cost,
                    "net_pnl": t.net_pnl,
                    "account_ccy": t.account_ccy,
                    "bars_held": t.bars_held,
                    "max_adverse_excursion": t.max_adverse_excursion,
                    "max_favourable_excursion": t.max_favourable_excursion,
                    "fx_rate_used": t.fx_rate_used,
                }
            )
        return pd.DataFrame(rows, columns=list(self.LEDGER_COLUMNS))

    def ledger_text(self) -> str:
        """Canonical text form. ``repr`` of a float round-trips exactly in CPython."""
        lines = ["\t".join(self.LEDGER_COLUMNS)]
        for t in self.trades:
            lines.append(
                "\t".join(
                    (
                        t.instrument,
                        t.direction.value,
                        repr(t.size),
                        repr(t.entry_price),
                        repr(t.exit_price),
                        t.entry_time.isoformat(),
                        t.exit_time.isoformat(),
                        t.exit_reason.value,
                        repr(t.gross_pnl),
                        repr(t.spread_cost),
                        repr(t.commission),
                        repr(t.slippage_cost),
                        repr(t.financing_cost),
                        repr(t.net_pnl),
                        t.account_ccy,
                        repr(t.bars_held),
                        repr(t.max_adverse_excursion),
                        repr(t.max_favourable_excursion),
                        repr(t.fx_rate_used),
                    )
                )
            )
        return "\n".join(lines) + "\n"

    def ledger_sha256(self) -> str:
        return hashlib.sha256(self.ledger_text().encode("utf-8")).hexdigest()

    @property
    def final_equity(self) -> float:
        if self.equity_curve.empty:
            return float("nan")
        return float(self.equity_curve["equity"].iloc[-1])


# --------------------------------------------------------------------------
# Internal bookkeeping
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _OpenPosition:
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
class _PendingOrder:
    seq: int
    plan: TradePlan
    instrument: Instrument
    actionable_index: int
    created_index: int


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class BacktestEngine:
    """Run one strategy over one or more instruments on a shared timeline."""

    def __init__(
        self,
        *,
        data: dict[str, pd.DataFrame],
        config: BacktestConfig,
        strategy: Strategy,
        sizer: Sizer,
        fx: FxRateSource,
        calendar: SessionCalendar | None = None,
    ) -> None:
        if fx is None:
            raise ValueError(
                "BacktestEngine requires an FxRateSource. V1 assumed 1.0 and "
                "mis-stated every non-account-currency result; V2 will not start "
                "without an explicit conversion policy."
            )
        if not data:
            raise ValueError("BacktestEngine requires at least one instrument frame")

        self.config = config
        self.strategy = strategy
        self.sizer = sizer
        self.fx = fx
        self.sim = FillSimulator(
            profile=config.profile,
            intrabar_policy=config.intrabar_policy,
            calendar=calendar or AlwaysOpenCalendar(),
            reject_on_stale=config.reject_on_stale,
        )

        # Sorted for determinism: iteration order of `data` must never matter.
        self.symbols: tuple[str, ...] = tuple(sorted(data))
        self.frames: dict[str, pd.DataFrame] = {}
        self.instruments: dict[str, Instrument] = {}
        for sym in self.symbols:
            frame = _validate_frame(sym, data[sym])
            self.frames[sym] = frame
            self.instruments[sym] = get_instrument(sym)

        self.timeline: pd.DatetimeIndex = _union_timeline(self.frames, self.symbols)
        self._positions: dict[str, np.ndarray] = {
            sym: self.frames[sym].index.searchsorted(self.timeline, side="right") - 1
            for sym in self.symbols
        }
        # Vectorised "does this instrument have a bar at exactly this timestamp".
        # np.isin on a DatetimeIndex falls back to an O(n*m) scan, which cost 40s
        # on a 40k-bar EURUSD run; a searchsorted comparison is O(n log n).
        self._exact: dict[str, np.ndarray] = {}
        tl = self.timeline.asi8
        for sym in self.symbols:
            pos = self._positions[sym]
            own = self.frames[sym].index.asi8
            found = pos >= 0
            exact = np.zeros(len(tl), dtype=bool)
            exact[found] = own[pos[found]] == tl[found]
            self._exact[sym] = exact
        self._ohlc: dict[str, np.ndarray] = {
            sym: self.frames[sym][["open", "high", "low", "close"]].to_numpy(dtype=np.float64)
            for sym in self.symbols
        }
        self._intervals: dict[str, pd.Timedelta] = {
            sym: _median_interval(self.frames[sym].index) for sym in self.symbols
        }

    # ------------------------------------------------------------------ run

    def run(self) -> BacktestResult:
        cfg = self.config
        balance = float(cfg.initial_balance)
        peak_equity = balance
        realised = 0.0

        open_positions: list[_OpenPosition] = []
        pending: list[_PendingOrder] = []
        trades: list[Trade] = []
        costs = CostBreakdown()
        rejections: dict[str, int] = {}
        seq_counter = 0
        signals_seen = 0
        orders_submitted = 0
        bankrupt = False

        n = len(self.timeline)
        eq_ts = np.empty(n, dtype="datetime64[ns]")
        eq_balance = np.full(n, np.nan)
        eq_equity = np.full(n, np.nan)
        eq_unrealised = np.full(n, np.nan)
        eq_exposure = np.full(n, 0.0)
        eq_open = np.zeros(n, dtype=np.int64)
        written = 0

        prev_ts: pd.Timestamp | None = None

        for i in range(n):
            ts = self.timeline[i]

            # -- 1. financing for nights crossed since the previous timeline bar
            if cfg.charge_financing and prev_ts is not None:
                for op in open_positions:
                    nights = _nights_between(
                        op.last_financing_time or op.position.entry_time,
                        ts,
                        cfg.financing_rollover_hour_utc,
                    )
                    if nights:
                        mark = self._last_close(op.position.instrument, i)
                        charge_quote = (
                            cfg.profile.financing.nightly_charge(
                                op.instrument, op.position.direction, op.position.size, mark
                            )
                            * nights
                        )
                        ccy = cfg.profile.financing.currency_for(op.instrument)
                        charge_acct = charge_quote * self._rate(ccy, ts)
                        op.financing_account += charge_acct
                        op.position.financing_accrued += charge_acct
                        op.last_financing_time = ts

            # -- 2. fill pending orders at this bar's open
            still_pending: list[_PendingOrder] = []
            for order in pending:
                if order.actionable_index > i:
                    still_pending.append(order)
                    continue
                sym = order.instrument.symbol
                if not self._has_bar(sym, i):
                    still_pending.append(order)  # instrument not trading: wait
                    continue
                if len(open_positions) >= cfg.max_concurrent:
                    _bump(rejections, "max_concurrent")
                    continue
                if sum(1 for p in open_positions if p.position.instrument == sym) >= cfg.max_per_instrument:
                    _bump(rejections, "max_per_instrument")
                    continue

                bar = self._bar(sym, i)
                fill = self.sim.simulate_entry(
                    instrument=order.instrument,
                    direction=order.plan.direction,
                    size=order.plan.size,
                    bar=bar,
                    bar_index=i,
                    stop=order.plan.signal.stop_price,
                    sequence=order.seq,
                    previous_time=self._previous_bar_time(sym, i),
                    bar_interval=self._intervals[sym],
                )
                if not fill.filled:
                    _bump(rejections, fill.reject_reason.value if fill.reject_reason else "unknown")
                    continue

                fx_rate = self._rate(order.instrument.quote, ts)
                entry_costs = self._costs_to_account(order.instrument, fill.costs, ts)
                costs.spread += entry_costs["spread"]
                costs.entry_leg_spread += entry_costs["spread"]
                costs.commission += entry_costs["commission"]
                costs.slippage += entry_costs["slippage"]
                costs.guaranteed_stop_premium += entry_costs["premium"]

                tp = (
                    order.plan.signal.take_profit_prices[0]
                    if order.plan.signal.take_profit_prices
                    else None
                )
                pos = Position(
                    instrument=sym,
                    direction=order.plan.direction,
                    size=fill.filled_size,
                    entry_price=fill.mid_price,
                    entry_time=ts,
                    stop_loss=fill.effective_stop,
                    take_profit_targets=[tp] if tp is not None else [],
                    strategy_id=order.plan.signal.strategy_id or cfg.strategy_id,
                )
                seq_counter += 1
                open_positions.append(
                    _OpenPosition(
                        seq=seq_counter,
                        position=pos,
                        instrument=order.instrument,
                        entry_mid=fill.mid_price,
                        entry_bar_index=i,
                        take_profit=tp,
                        entry_costs_account=entry_costs,
                        last_financing_time=ts,
                        entry_fx=fx_rate,
                    )
                )
            pending = still_pending

            # -- 3. exits (positions opened this bar included: gap risk is real)
            survivors: list[_OpenPosition] = []
            for op in open_positions:
                sym = op.position.instrument
                if not self._has_bar(sym, i):
                    survivors.append(op)
                    continue
                bar = self._bar(sym, i)
                op.position.update_excursion(bar.high, bar.low)
                if i > op.entry_bar_index:
                    op.position.bars_held += 1

                exit_fill = self.sim.resolve_exit(
                    instrument=op.instrument,
                    direction=op.position.direction,
                    size=op.position.size,
                    bar=bar,
                    bar_index=i,
                    stop=op.position.stop_loss,
                    take_profit=op.take_profit,
                    sequence=op.seq,
                    previous_time=self._previous_bar_time(sym, i),
                    bar_interval=self._intervals[sym],
                )
                if not exit_fill.exited:
                    survivors.append(op)
                    continue

                trade = self._close(op, exit_fill, ts, costs)
                trades.append(trade)
                realised += trade.net_pnl
                balance += trade.net_pnl
            open_positions = survivors

            # -- 4. mark to market on the bar's close
            unrealised = 0.0
            exposure = 0.0
            for op in open_positions:
                mark = self._last_close(op.position.instrument, i)
                rate = self._rate(op.instrument.quote, ts)
                unrealised += op.position.unrealised_quote(mark, op.instrument.contract_size) * rate
                unrealised -= op.financing_account
                exposure += abs(mark * op.position.size * op.instrument.contract_size * rate)
            equity = balance + unrealised
            peak_equity = max(peak_equity, equity)

            eq_ts[written] = ts.to_datetime64()
            eq_balance[written] = balance
            eq_equity[written] = equity
            eq_unrealised[written] = unrealised
            eq_exposure[written] = exposure
            eq_open[written] = len(open_positions)
            written += 1
            prev_ts = ts

            # -- 5. bankruptcy guard
            if equity <= cfg.bankruptcy_equity:
                bankrupt = True
                for op in open_positions:
                    bar = self._bar_or_last(op.position.instrument, i)
                    exit_fill = self.sim.market_exit(
                        instrument=op.instrument,
                        direction=op.position.direction,
                        size=op.position.size,
                        bar=bar,
                        bar_index=i,
                        reason=ExitReason.MARGIN_CALL,
                        sequence=op.seq,
                        previous_time=self._previous_bar_time(op.position.instrument, i),
                        bar_interval=self._intervals[op.position.instrument],
                    )
                    trade = self._close(op, exit_fill, ts, costs)
                    trades.append(trade)
                    balance += trade.net_pnl
                open_positions = []
                pending = []
                break

            # -- 6. the strategy sees the CLOSED bar and may emit signals
            account = AccountState(
                balance=balance,
                equity=equity,
                currency=cfg.account_ccy,
                open_positions=len(open_positions),
                realised_pnl=realised,
                unrealised_pnl=unrealised,
                peak_equity=peak_equity,
            )
            ctx = BarContext(
                timestamp=ts,
                bar_index=i,
                account=account,
                open_positions=tuple(op.position for op in open_positions),
                _frames=self.frames,
                _cursor={sym: int(self._positions[sym][i]) for sym in self.symbols},
            )
            emitted = self.strategy.on_bar(ctx)
            for signal in emitted or ():
                signals_seen += 1
                if signal.bar_time != ts:
                    raise ValueError(
                        f"Strategy emitted a signal dated {signal.bar_time} while the "
                        f"engine was on bar {ts}. A signal may only be dated on the bar "
                        "that produced it — anything else is look-ahead."
                    )
                if signal.instrument not in self.instruments:
                    raise KeyError(f"Signal for unknown instrument {signal.instrument!r}")
                instr = self.instruments[signal.instrument]
                rate = self._rate(instr.quote, ts)
                size = self.sizer.size_for(signal, instr, account, rate)
                if size <= 0:
                    _bump(rejections, "sized_to_zero")
                    continue
                plan = TradePlan(
                    signal=signal,
                    size=size,
                    account_ccy=cfg.account_ccy,
                    risk_amount=signal.stop_distance * size * instr.contract_size * rate,
                    sizing_basis=type(self.sizer).__name__,
                    max_leverage_applied=instr.retail_leverage,
                )
                seq_counter += 1
                orders_submitted += 1
                pending.append(
                    _PendingOrder(
                        seq=seq_counter,
                        plan=plan,
                        instrument=instr,
                        actionable_index=i + 1 + cfg.profile.latency_bars,
                        created_index=i,
                    )
                )

        # -- end of data: close whatever is left, honestly flagged
        if cfg.close_at_end_of_data and open_positions and not bankrupt:
            last_i = n - 1
            ts = self.timeline[last_i]
            for op in open_positions:
                bar = self._bar_or_last(op.position.instrument, last_i)
                exit_fill = self.sim.market_exit(
                    instrument=op.instrument,
                    direction=op.position.direction,
                    size=op.position.size,
                    bar=bar,
                    bar_index=last_i,
                    reason=ExitReason.END_OF_DATA,
                    sequence=op.seq,
                    previous_time=self._previous_bar_time(op.position.instrument, last_i),
                    bar_interval=self._intervals[op.position.instrument],
                )
                trade = self._close(op, exit_fill, ts, costs)
                trades.append(trade)
                balance += trade.net_pnl
            eq_balance[written - 1] = balance
            eq_equity[written - 1] = balance
            eq_unrealised[written - 1] = 0.0
            eq_exposure[written - 1] = 0.0
            eq_open[written - 1] = 0
            open_positions = []

        equity_curve = pd.DataFrame(
            {
                "balance": eq_balance[:written],
                "equity": eq_equity[:written],
                "unrealised": eq_unrealised[:written],
                "exposure": eq_exposure[:written],
                "open_positions": eq_open[:written],
            },
            index=pd.DatetimeIndex(eq_ts[:written], tz="UTC", name="timestamp"),
        )
        running_peak = equity_curve["equity"].cummax()
        equity_curve["drawdown"] = equity_curve["equity"] - running_peak
        equity_curve["drawdown_pct"] = np.where(
            running_peak.to_numpy() > 0,
            equity_curve["drawdown"].to_numpy() / running_peak.to_numpy() * 100.0,
            0.0,
        )

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            costs=costs,
            config_fingerprint=self.config.fingerprint(),
            data_fingerprint=self.data_fingerprint(),
            rejections=dict(sorted(rejections.items())),
            bankrupt=bankrupt,
            signals_seen=signals_seen,
            orders_submitted=orders_submitted,
        )

    # -------------------------------------------------------------- helpers

    def _close(
        self,
        op: _OpenPosition,
        exit_fill,
        ts: pd.Timestamp,
        costs: CostBreakdown,
    ) -> Trade:
        cfg = self.config
        instr = op.instrument
        exit_costs = self._costs_to_account(instr, exit_fill.costs, ts)
        costs.spread += exit_costs["spread"]
        costs.exit_leg_spread += exit_costs["spread"]
        costs.commission += exit_costs["commission"]
        costs.slippage += exit_costs["slippage"]
        costs.guaranteed_stop_premium += exit_costs["premium"]
        costs.financing += op.financing_account

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
        # The guaranteed-stop premium is a commission-like charge; folding it in
        # keeps `net = gross - the four named costs` exactly true.
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

    def _rate(self, from_ccy: str, when: pd.Timestamp) -> float:
        return float(self.fx.rate(from_ccy, self.config.account_ccy, when))

    def _has_bar(self, sym: str, i: int) -> bool:
        return bool(self._exact[sym][i])

    def _bar(self, sym: str, i: int) -> Bar:
        j = int(self._positions[sym][i])
        o, h, low, c = self._ohlc[sym][j]
        return Bar(self.timeline[i], float(o), float(h), float(low), float(c))

    def _bar_or_last(self, sym: str, i: int) -> Bar:
        j = int(self._positions[sym][i])
        if j < 0:
            raise IndexError(f"No bar at or before index {i} for {sym}")
        o, h, low, c = self._ohlc[sym][j]
        return Bar(self.timeline[i], float(o), float(h), float(low), float(c))

    def _last_close(self, sym: str, i: int) -> float:
        j = int(self._positions[sym][i])
        if j < 0:
            raise IndexError(f"No close at or before index {i} for {sym}")
        return float(self._ohlc[sym][j, 3])

    def _previous_bar_time(self, sym: str, i: int) -> pd.Timestamp | None:
        j = int(self._positions[sym][i])
        if j <= 0:
            return None
        return self.frames[sym].index[j - 1]

    def data_fingerprint(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for sym in self.symbols:
            arr = self._ohlc[sym]
            digest = hashlib.sha256(
                np.ascontiguousarray(arr).tobytes()
                + self.frames[sym].index.asi8.tobytes()
            ).hexdigest()
            out[sym] = {
                "bars": int(len(arr)),
                "first": self.frames[sym].index[0].isoformat(),
                "last": self.frames[sym].index[-1].isoformat(),
                "sha256": digest,
            }
        return out


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _validate_frame(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    required = ("open", "high", "low", "close")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise KeyError(f"{symbol}: OHLC frame missing columns {missing}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"{symbol}: frame index must be a DatetimeIndex")
    if frame.index.tz is None:
        raise ValueError(f"{symbol}: frame index must be timezone-aware UTC")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{symbol}: frame index must be sorted ascending")
    if frame.index.has_duplicates:
        raise ValueError(f"{symbol}: frame index has duplicate timestamps")
    if len(frame) == 0:
        raise ValueError(f"{symbol}: frame is empty")
    sub = frame[list(required)].astype(np.float64)
    if not np.isfinite(sub.to_numpy()).all():
        raise ValueError(f"{symbol}: OHLC contains NaN or inf; repair upstream")
    return sub


def _union_timeline(frames: dict[str, pd.DataFrame], symbols: tuple[str, ...]) -> pd.DatetimeIndex:
    idx = frames[symbols[0]].index
    for sym in symbols[1:]:
        idx = idx.union(frames[sym].index)
    return pd.DatetimeIndex(idx).sort_values()


def _median_interval(index: pd.DatetimeIndex) -> pd.Timedelta:
    if len(index) < 3:
        return pd.Timedelta(hours=1)
    deltas = np.diff(index.asi8)
    return pd.Timedelta(int(np.median(deltas)), unit="ns")


def _nights_between(last: pd.Timestamp, now: pd.Timestamp, rollover_hour: int) -> int:
    """Number of ``rollover_hour`` UTC crossings in the half-open interval (last, now].

    Weekend triple-swap is NOT modelled. That understates financing on
    Wednesday-held positions by up to two nights per week — a documented
    approximation, not a silent one.
    """
    if now <= last:
        return 0
    anchor = last.normalize() + pd.Timedelta(hours=rollover_hour)
    if anchor <= last:
        anchor += pd.Timedelta(days=1)
    if anchor > now:
        return 0
    return int((now - anchor) // pd.Timedelta(days=1)) + 1


def run_backtest(
    *,
    data: dict[str, pd.DataFrame],
    config: BacktestConfig,
    strategy: Strategy,
    sizer: Sizer,
    fx: FxRateSource,
    calendar: SessionCalendar | None = None,
) -> BacktestResult:
    return BacktestEngine(
        data=data, config=config, strategy=strategy, sizer=sizer, fx=fx, calendar=calendar
    ).run()
