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

Scaled-out positions: ONE Trade row, N exit legs
------------------------------------------------
A position with several take-profit legs closes in pieces, and each piece is a
real fill with its own price, its own costs and its own moment. Two
representations were available and they are not equivalent:

* one ``Trade`` per *fill*, which is the finer ledger but makes ``len(trades)``
  count exit events rather than positions — and ``min_trades`` is a promotion
  gate, so a strategy that scales out three times would have cleared a
  400-trade bar on 134 positions. That is exactly the class of flattering
  arithmetic this project exists to prevent;
* one ``Trade`` per *position*, which keeps every count and every per-trade
  statistic meaning what it has always meant.

V2 emits one ``Trade`` per position and keeps the per-fill detail beside it in
:attr:`BacktestResult.exit_legs`. The two reconcile exactly:
``sum(leg.net_pnl) == sum(trade.net_pnl)``, asserted by
``tests/unit/test_engine_exits.py``. On a scaled-out row ``size`` is the whole
position, ``exit_reason`` is the reason the LAST piece left, ``exit_time`` is
when the last piece left, and ``exit_price`` is the size-weighted mean of the
leg mids — which reproduces ``gross_pnl`` exactly whenever the FX rate did not
move between legs, and is within the rate's drift when it did. A position that
closed in one piece stores that piece's mid verbatim, so nothing about the
single-leg case changes by so much as an ULP.

The rest of the exit vocabulary — trailing stops, breakeven, time stops,
cooldown, reversal, event blackouts — lives in
:mod:`fiboki.backtest.exits` as an :class:`~fiboki.backtest.exits.ExitPolicy`
and is documented there.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

from fiboki.backtest.exits import (
    DEFAULT_EXIT_POLICY,
    BlackoutSource,
    ExitPolicy,
    ReversalMode,
)
from fiboki.backtest.position import (
    BarSlice,
    BookConfig,
    CostBreakdown,
    ExitLeg,
    ManagedPosition,
    PendingClose,
    PendingEntry,
    PositionBook,
    TakeProfitLeg,
    nights_between,
    plan_legs,
    size_for_exit,
    snap_to_step,
)
from fiboki.backtest.version import ENGINE_VERSION
from fiboki.core.contracts import (
    AccountState,
    Position,
    Signal,
    Trade,
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
    SessionCalendar,
)
from fiboki.sim.profiles import IG_REALISTIC, ExecutionProfile

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BarContext",
    "CostBreakdown",
    "ExitLeg",
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


#: Kept as a module-level name because ``portfolio/sizing.py`` is proved
#: equivalent to it by ``tests/unit/test_sizing_authority.py``. The definition
#: itself now lives beside the leg planner that uses it.
_snap_to_step = snap_to_step


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
        """How this engine was wired, as stored on a record.

        Carries ``key_version`` because it is PERSISTED and then compared: the
        fingerprint's SHAPE is a function of this class, so without the stamp a
        reader cannot tell a different configuration from the same configuration
        described by a different generation of the code. ``ENGINE_VERSION`` is the
        right stamp by definition -- it is bumped exactly when a change makes
        stored results incomparable with new ones. See
        :mod:`fiboki.core.versioned_key`.
        """
        return {
            "key_version": ENGINE_VERSION,
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


#: ``CostBreakdown`` and ``ExitLeg`` are defined in ``backtest/position.py``
#: alongside the code that fills them in, and re-exported here because every
#: reader of a ``BacktestResult`` looks for them in this module.


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
    #: One row per closing FILL, in chronological order. A position that scaled
    #: out three times contributes three legs and one ``Trade``.
    exit_legs: list[ExitLeg] = field(default_factory=list)
    exit_policy_fingerprint: dict[str, object] = field(default_factory=dict)

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

    #: The per-FILL ledger. Separate from ``LEDGER_COLUMNS`` because it answers
    #: a different question: the trade ledger says what each POSITION did, this
    #: says what each closing fill did, and a scale-out only shows up here.
    LEG_COLUMNS = (
        "position_seq",
        "instrument",
        "direction",
        "ordinal",
        "size",
        "exit_price",
        "exit_time",
        "exit_reason",
        "gross_pnl",
        "spread_cost",
        "commission",
        "slippage_cost",
        "financing_cost",
        "net_pnl",
        "final",
    )

    def leg_ledger_text(self) -> str:
        lines = ["\t".join(self.LEG_COLUMNS)]
        for leg in self.exit_legs:
            lines.append(
                "\t".join(
                    (
                        repr(leg.position_seq),
                        leg.instrument,
                        leg.direction,
                        repr(leg.ordinal),
                        repr(leg.size),
                        repr(leg.exit_price),
                        leg.exit_time.isoformat(),
                        leg.exit_reason,
                        repr(leg.gross_pnl),
                        repr(leg.spread_cost),
                        repr(leg.commission),
                        repr(leg.slippage_cost),
                        repr(leg.financing_cost),
                        repr(leg.net_pnl),
                        repr(leg.final),
                    )
                )
            )
        return "\n".join(lines) + "\n"

    def leg_ledger_sha256(self) -> str:
        return hashlib.sha256(self.leg_ledger_text().encode("utf-8")).hexdigest()

    @property
    def partial_exits(self) -> int:
        """Closing fills that did NOT empty their position: the scale-outs."""
        return sum(1 for leg in self.exit_legs if not leg.final)

    @property
    def final_equity(self) -> float:
        if self.equity_curve.empty:
            return float("nan")
        return float(self.equity_curve["equity"].iloc[-1])


# --------------------------------------------------------------------------
# Internal bookkeeping
# --------------------------------------------------------------------------


#: The names the engine used before the position lifecycle moved into
#: ``backtest/position.py``. Kept so that a test (and a reader) that reaches for
#: ``engine._OpenPosition`` still finds the object the engine actually uses.
_TakeProfitLeg = TakeProfitLeg
_OpenPosition = ManagedPosition
_PendingOrder = PendingEntry
_PendingClose = PendingClose


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
        exit_policy: ExitPolicy | None = None,
        exit_series: dict[str, pd.DataFrame] | None = None,
        blackout: BlackoutSource | None = None,
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

        self.exit_policy = exit_policy or DEFAULT_EXIT_POLICY
        self.blackout = blackout
        self._series: dict[str, dict[str, np.ndarray]] = {
            sym: {} for sym in self.symbols
        }
        for sym, frame in (exit_series or {}).items():
            if sym not in self.frames:
                raise KeyError(
                    f"exit_series names {sym!r}, which is not one of the instruments "
                    f"{list(self.symbols)} this engine was given bars for"
                )
            if not frame.index.equals(self.frames[sym].index):
                raise ValueError(
                    f"exit_series[{sym!r}] is indexed differently from the OHLC "
                    "frame. A trailing stop read off a misaligned series is a "
                    "trailing stop computed from another bar's volatility."
                )
            for col in frame.columns:
                self._series[sym][str(col)] = frame[col].to_numpy(dtype=np.float64)

        needed = self.exit_policy.needs_series
        if needed:
            for sym in self.symbols:
                missing = [c for c in needed if c not in self._series[sym]]
                if missing:
                    raise KeyError(
                        f"{sym}: the exit policy trails on column(s) {missing}, which "
                        "no exit_series provides. A trail with no series would never "
                        "move, and a strategy that silently never trails is the exact "
                        "defect this policy exists to fix."
                    )

    # ------------------------------------------------------------------ run

    def run(self) -> BacktestResult:
        cfg = self.config
        book = PositionBook(
            sim=self.sim,
            fx=self.fx,
            config=BookConfig(
                account_ccy=cfg.account_ccy,
                max_concurrent=cfg.max_concurrent,
                max_per_instrument=cfg.max_per_instrument,
                charge_financing=cfg.charge_financing,
                financing_rollover_hour_utc=cfg.financing_rollover_hour_utc,
                strategy_id=cfg.strategy_id,
                provenance=cfg.provenance,
            ),
            policy=self.exit_policy,
            blackout=self.blackout,
            initial_balance=cfg.initial_balance,
            latency_bars=cfg.profile.latency_bars,
            financing_profile=cfg.profile.financing,
        )
        self.book = book
        policy = self.exit_policy
        peak_equity = book.balance
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
            view = self._slice(i, ts, prev_ts)

            # -- 1/2/3. financing, reversals, entries at the open, exits.
            #    Every one of these is decided in ``backtest/position.py``, by
            #    the same object the paper adapter drives.
            book.advance(view)

            # -- 4. mark to market on the bar's close
            unrealised, exposure = book.mark_to_market(view)
            equity = book.balance + unrealised
            peak_equity = max(peak_equity, equity)

            eq_ts[written] = ts.to_datetime64()
            eq_balance[written] = book.balance
            eq_equity[written] = equity
            eq_unrealised[written] = unrealised
            eq_exposure[written] = exposure
            eq_open[written] = len(book.open)
            written += 1
            prev_ts = ts

            # -- 5. bankruptcy guard
            if equity <= cfg.bankruptcy_equity:
                bankrupt = True
                book.flatten(view, reason=ExitReason.MARGIN_CALL)
                book.pending = []
                book.pending_closes = []
                break

            # -- 6. the strategy sees the CLOSED bar and may emit signals
            account = AccountState(
                balance=book.balance,
                equity=equity,
                currency=cfg.account_ccy,
                open_positions=len(book.open),
                realised_pnl=book.realised,
                unrealised_pnl=unrealised,
                peak_equity=peak_equity,
            )
            ctx = BarContext(
                timestamp=ts,
                bar_index=i,
                account=account,
                open_positions=tuple(op.position for op in book.open),
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

                # Opposite-signal handling. Scheduled here, executed at the next
                # bar's open, because the signal was produced on a closed bar.
                scheduled = book.schedule_reversal(
                    signal.instrument, signal.direction, index=i
                )
                if scheduled and policy.reversal is ReversalMode.CLOSE_ONLY:
                    book.bump("reversal_close_only")
                    continue

                rate = self._rate(instr.quote, ts)
                size = self.sizer.size_for(signal, instr, account, rate)
                if size <= 0:
                    book.bump("sized_to_zero")
                    continue
                orders_submitted += 1
                book.queue_entry(
                    PendingEntry(
                        seq=book.next_seq(),
                        instrument=instr,
                        direction=signal.direction,
                        size=size,
                        stop_price=signal.stop_price,
                        take_profit_prices=tuple(signal.take_profit_prices),
                        take_profit_allocations=tuple(signal.take_profit_allocations),
                        strategy_id=signal.strategy_id,
                        actionable_index=i + 1 + cfg.profile.latency_bars,
                        created_index=i,
                    )
                )

        # -- end of data: close whatever is left, honestly flagged
        if cfg.close_at_end_of_data and book.open and not bankrupt:
            last_i = n - 1
            ts = self.timeline[last_i]
            book.flatten(
                self._slice(last_i, ts, prev_ts), reason=ExitReason.END_OF_DATA
            )
            eq_balance[written - 1] = book.balance
            eq_equity[written - 1] = book.balance
            eq_unrealised[written - 1] = 0.0
            eq_exposure[written - 1] = 0.0
            eq_open[written - 1] = 0

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
            trades=book.trades,
            equity_curve=equity_curve,
            costs=book.costs,
            config_fingerprint=self.config.fingerprint(),
            data_fingerprint=self.data_fingerprint(),
            rejections=dict(sorted(book.rejections.items())),
            bankrupt=bankrupt,
            signals_seen=signals_seen,
            orders_submitted=orders_submitted,
            exit_legs=book.exit_legs,
            exit_policy_fingerprint=self.exit_policy.fingerprint(),
        )

    # -------------------------------------------------------------- helpers

    def _slice(
        self, i: int, ts: pd.Timestamp, prev_ts: pd.Timestamp | None
    ) -> BarSlice:
        """Assemble what the position book is allowed to see at index ``i``.

        Only instruments with a bar at EXACTLY this timestamp appear in
        ``bars``; the rest contribute a carried bar and a last close, which is
        what marking and an out-of-session flatten need and all they need. The
        book cannot reach past this object into the frames, which is the whole
        reason it can also be driven by a live feed.
        """
        bars: dict[str, Bar] = {}
        carried: dict[str, Bar] = {}
        previous_times: dict[str, pd.Timestamp | None] = {}
        intervals: dict[str, pd.Timedelta | None] = {}
        last_closes: dict[str, float] = {}
        series: dict[str, dict[str, float]] = {}
        for sym in self.symbols:
            j = int(self._positions[sym][i])
            if j < 0:
                continue
            o, h, low, c = self._ohlc[sym][j]
            bar = Bar(ts, float(o), float(h), float(low), float(c))
            carried[sym] = bar
            last_closes[sym] = float(c)
            previous_times[sym] = self.frames[sym].index[j - 1] if j > 0 else None
            intervals[sym] = self._intervals[sym]
            if self._exact[sym][i]:
                bars[sym] = bar
            columns = self._series[sym]
            if columns:
                series[sym] = {name: float(arr[j]) for name, arr in columns.items()}
        return BarSlice(
            index=i,
            timestamp=ts,
            bars=bars,
            previous_timestamp=prev_ts,
            previous_times=previous_times,
            intervals=intervals,
            last_closes=last_closes,
            carried_bars=carried,
            series=series,
        )

    def _allocations_for(self, signal: Signal) -> tuple[float, ...]:
        """Per-leg allocations for this signal, or the policy's fallback.

        The signal wins when it carries them, because only the compiler knows
        which declared leg produced which price. Retained because it states the
        rule in the engine's own vocabulary; the book applies the same rule to
        the order the signal became.
        """
        if signal.take_profit_allocations:
            return tuple(signal.take_profit_allocations)
        allocations = self.exit_policy.allocations
        if allocations and len(allocations) >= len(signal.take_profit_prices):
            return tuple(allocations[: len(signal.take_profit_prices)])
        return ()

    #: Delegated to ``backtest/position.py``. Kept as a static method because it
    #: is the engine's statement of how much a scale-out leg closes, and tests
    #: reach for it there.
    _size_for_exit = staticmethod(size_for_exit)

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
            entry: dict[str, object] = {
                "bars": int(len(arr)),
                "first": self.frames[sym].index[0].isoformat(),
                "last": self.frames[sym].index[-1].isoformat(),
                "sha256": digest,
            }
            series = self._series.get(sym) or {}
            if series:
                # A trailing stop reads these, so a different ATR series is a
                # different backtest. Fingerprinting only the OHLC would let two
                # incomparable runs claim the same data provenance.
                blob = b"".join(
                    name.encode("utf-8") + np.ascontiguousarray(series[name]).tobytes()
                    for name in sorted(series)
                )
                entry["exit_series"] = {
                    "columns": sorted(series),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                }
            out[sym] = entry
        return out


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


_plan_legs = plan_legs


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


_nights_between = nights_between


def run_backtest(
    *,
    data: dict[str, pd.DataFrame],
    config: BacktestConfig,
    strategy: Strategy,
    sizer: Sizer,
    fx: FxRateSource,
    calendar: SessionCalendar | None = None,
    exit_policy: ExitPolicy | None = None,
    exit_series: dict[str, pd.DataFrame] | None = None,
    blackout: BlackoutSource | None = None,
) -> BacktestResult:
    return BacktestEngine(
        data=data,
        config=config,
        strategy=strategy,
        sizer=sizer,
        fx=fx,
        calendar=calendar,
        exit_policy=exit_policy,
        exit_series=exit_series,
        blackout=blackout,
    ).run()
