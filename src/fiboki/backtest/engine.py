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

Entry locks
-----------
A document's ``locks`` block travels on ``ExitPolicy.locks`` and is enforced
HERE, on the decision bar, by :class:`~fiboki.backtest.locks.LockBook`: every
closed position is reported to it, and a signal whose instrument is locked is
refused before it is sized or queued, recorded as ``instrument_lock`` in
``rejections`` and in :attr:`BacktestResult.lock_blocks`. The risk gateway's
``instrument_lock`` check asks the same module the same question on the same
bar, rebuilt from the trade ledger, which is how paper and live agree with this.
A run whose policy declares no locks never constructs a lock book and is
byte-identical to a run before locks existed.

Portfolio construction (research/paper sizing parity)
-----------------------------------------------------
The paper runtime sizes every bar's signals through portfolio construction
(tier base risk, health, confidence, correlation, concentration, vol target,
margin, drawdown throttle, regime, correlated open risk, conviction) and then
calls the sizer once at ``tier base risk x weight``. With
``BacktestConfig.construction`` set, the engine does the same on each decision
bar against its OWN simulated book: the signals that survive the reversal and
lock filters are offered together to :class:`ConstructionPolicy.allocate`, and
each accepted one is sized once by the configured sizer at
``risk_fraction = base_risk_pct / 100`` and ``portfolio_weight = weight``. Every
decision, including a refusal, is a row of :attr:`BacktestResult.
allocation_ledger` (compact JSON) and each trade row links to the decision
that opened it.

The engine cannot import ``portfolio`` (``tests/unit/test_layering.py``: rank
50 below rank 60), so the policy is a Protocol here and
``fiboki.portfolio.engine_policy.BacktestConstructionPolicy`` satisfies it. For
the same reason the engine-level default is ``None`` (the flat
``risk_fraction`` path, fingerprinted ``construction=none``); the research
entry points (``validation.engine_evaluator``, ``validation.run``) default to
the paper runtime's policy. There is no conviction input anywhere on this
path: historical LLM convictions are inadmissible in a backtest (plan D-A3),
so the conviction step always reads "missing" and multiplies by exactly 1.0.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import numpy as np
import pandas as pd

from fiboki.backtest.exits import (
    DEFAULT_EXIT_POLICY,
    BlackoutSource,
    ExitPolicy,
    ReversalMode,
)
from fiboki.backtest.locks import (
    LockBook,
    LockRegistration,
    timeframe_for_interval,
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
from fiboki.core.enums import ExitReason, Provenance, Timeframe
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
    "SIZING_POLICY_V1",
    "SIZING_POLICY_V2",
    "AllocationDecision",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BarContext",
    "ConstructionPolicy",
    "ConstructionRequest",
    "CostBreakdown",
    "ExitLeg",
    "FixedFractionalSizer",
    "FixedSizeSizer",
    "PrecomputedSignals",
    "Sizer",
    "Strategy",
    "expected_entry_time",
    "run_backtest",
    "stop_out_cost_per_unit",
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


#: Sizing rule identifiers. ``v1`` risks ``risk_fraction`` of equity over the
#: bare stop distance; ``v2`` over the stop distance PLUS the expected cost of
#: getting in and stopped out. See :func:`stop_out_cost_per_unit`.
SIZING_POLICY_V1 = "fixed_fractional_v1"
SIZING_POLICY_V2 = "fixed_fractional_v2"
SIZING_POLICIES = (SIZING_POLICY_V1, SIZING_POLICY_V2)

#: The cost profile a v2 sizer prices its stop-out with when none is named.
#: IG_REALISTIC because it is the research default
#: (``EvaluatorConfig.profile_name``) AND the paper broker's default
#: (``PaperBrokerConfig.profile``): a paper plan and a backtest of the same
#: signal therefore size identically unless someone names a different profile,
#: and the one used is recorded in the sizing fingerprint either way. It is a
#: stated default, not an inference from the run: a backtest under
#: SEVERE_STRESS with an unnamed cost profile sizes on IG costs and says so.
DEFAULT_SIZING_COST_PROFILE: ExecutionProfile = IG_REALISTIC


def expected_entry_time(signal: Signal) -> pd.Timestamp:
    """When the entry this signal causes is expected to fill: the NEXT bar's open.

    A signal is dated on its bar's open stamp and acted on once that bar has
    closed, so the entry fills ``timeframe`` later. An unparseable timeframe
    falls back to the signal's own bar time, which is the hour the strategy saw.
    """
    try:
        minutes = Timeframe(str(signal.timeframe)).minutes
    except ValueError:
        return signal.bar_time
    return signal.bar_time + pd.Timedelta(minutes=minutes)


def stop_out_cost_per_unit(
    profile: ExecutionProfile, instrument: Instrument, signal: Signal
) -> float:
    """Expected cost, in PRICE units per unit of size, of entering and being stopped.

    The realised loss of a position stopped exactly at its level is not the
    stop distance. With both-leg costs (``sim/fills.py``) it is::

        stop_distance + half_spread (entry) + half_spread (exit)
                      + slippage (entry, market order) + slippage (exit, stop order)
      = stop_distance + spread + 2 * E[slippage]

    ``spread`` is the profile's full spread at the expected ENTRY hour and the
    signal's reference price; ``E[slippage]`` is
    :func:`fiboki.sim.profiles.expected_slippage_price`. Worked example
    (IG_REALISTIC, EURUSD, 5-pip stop, 1-pip spread): slippage is
    0.30 * 2 * 0.4 = 0.24 pips per fill, so the per-unit risk is
    5 + 1 + 0.48 = 6.48 pips and a v1 size risks 6.48 / 5 = 1.296 x its stated
    fraction at the stop. The audit's 25% figure counted slippage once.

    Stated approximations: the exit spread is priced at the ENTRY hour (the exit
    hour is unknown when sizing), stale-bar widening is not anticipated,
    commission is excluded (it is per ticket and in its own currency, so it is
    not linear in size), and financing is excluded (it depends on the holding
    period). Each makes the v2 figure a floor on the realised stop-out loss,
    never a ceiling.
    """
    hour = int(expected_entry_time(signal).hour)
    spread = profile.spread_price(instrument, hour, float(signal.reference_price))
    return float(spread + 2.0 * profile.expected_slippage_price(instrument))


@dataclass(frozen=True, slots=True)
class FixedFractionalSizer:
    """Risk a fixed fraction of CURRENT equity per trade, capped by leverage.

    ``risk_fraction`` is the fraction of equity lost if the stop fills exactly
    at its level. Under ``fixed_fractional_v2`` (the default) that loss includes
    the spread and the expected slippage of both legs, priced by
    ``cost_profile``; under ``fixed_fractional_v1`` it is the bare stop
    distance, which understates the realised stop-out loss by the costs. Gaps
    can and do exceed either -- this is a sizing intent, not a loss guarantee,
    and the engine's realised MAE distribution is where you find out how often
    the intent failed.

    ``cost_profile=None`` under v2 means :data:`DEFAULT_SIZING_COST_PROFILE`
    (IG_REALISTIC), the same default :class:`fiboki.portfolio.sizing.SizingPolicy`
    uses, so the paper path and the backtest agree by construction. A caller
    that wants sizing priced on its run's own frictions names them
    (``EngineEvaluator`` passes its configured profile).

    Kept arithmetically identical to :func:`fiboki.portfolio.sizing.size_trade`;
    ``tests/unit/test_sizing_authority.py`` pins the two together, including
    ``portfolio_weight``: the risk budget is ``equity * risk_fraction *
    portfolio_weight`` evaluated in that order, as ``size_trade`` does, so a
    construction-weighted size is byte-identical on both paths. At the default
    weight of exactly 1.0 the product is the unweighted one to the last bit.
    """

    risk_fraction: float = 0.01
    max_leverage: float | None = None
    policy_id: str = SIZING_POLICY_V2
    cost_profile: ExecutionProfile | None = None
    #: The fraction of the per-trade budget portfolio construction allocated.
    #: Set per trade by :meth:`allocated`; the run-level sizer keeps 1.0.
    portfolio_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.policy_id not in SIZING_POLICIES:
            raise ValueError(
                f"unknown sizing policy {self.policy_id!r}; known {SIZING_POLICIES}"
            )
        if not 0.0 <= self.portfolio_weight <= 1.0:
            raise ValueError(
                f"portfolio_weight must lie in [0, 1], got {self.portfolio_weight}: "
                "construction may reduce a size, never increase it"
            )

    def allocated(self, *, risk_fraction: float, portfolio_weight: float) -> FixedFractionalSizer:
        """This sizer at a construction allocation: the tier base risk and the weight.

        The paper runtime's equivalent is ``size_trade(policy=replace(policy,
        risk_fraction=base_risk_pct / 100), portfolio_weight=weight)``.
        """
        return replace(self, risk_fraction=risk_fraction, portfolio_weight=portfolio_weight)

    @property
    def resolved_cost_profile(self) -> ExecutionProfile | None:
        if self.policy_id == SIZING_POLICY_V1:
            return None
        return self.cost_profile or DEFAULT_SIZING_COST_PROFILE

    def fingerprint(self) -> dict[str, object]:
        return {
            "sizer": type(self).__name__,
            "policy_id": self.policy_id,
            "risk_fraction": self.risk_fraction,
            "max_leverage": self.max_leverage,
            "cost_profile": (
                self.resolved_cost_profile.name
                if self.resolved_cost_profile is not None
                else None
            ),
            "cost_profile_defaulted": (
                self.policy_id == SIZING_POLICY_V2 and self.cost_profile is None
            ),
        }

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
        risk_account = account.equity * self.risk_fraction * self.portfolio_weight
        if risk_account <= 0:
            return 0.0
        per_unit_price = stop_distance
        profile = self.resolved_cost_profile
        if profile is not None:
            per_unit_price = stop_distance + stop_out_cost_per_unit(
                profile, instrument, signal
            )
        risk_per_unit_account = per_unit_price * instrument.contract_size * fx_quote_to_account
        if risk_per_unit_account <= 0:
            return 0.0
        size = risk_account / risk_per_unit_account

        # Never above the instrument's regulatory cap, whatever was asked for:
        # the same rule as ``SizingPolicy.leverage_for``.
        leverage = (
            instrument.retail_leverage
            if self.max_leverage is None
            else min(self.max_leverage, instrument.retail_leverage)
        )
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
# Portfolio construction seam (the policy lives in ``portfolio``)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConstructionRequest:
    """The engine's book on one decision bar: all an allocation may read.

    Mirrors what the paper runtime hands ``PortfolioConstructor`` (the venue's
    ``account()``, ``positions()``, its equity curve, the bars seen so far) and
    nothing more. There is deliberately no conviction field: a backtest has no
    admissible LLM conviction (plan D-A3).
    """

    as_of: pd.Timestamp
    #: Balance, equity, peak and ``margin_used`` computed exactly as
    #: ``PaperBroker.account()`` computes them.
    account: AccountState
    open_positions: tuple[Position, ...]
    #: The bar's signals that survived the reversal and lock filters, in the
    #: order the strategy emitted them.
    signals: tuple[Signal, ...]
    account_ccy: str
    #: ``sizer.risk_fraction``: the configured sizing policy is a CEILING on
    #: the tier base risk, exactly as ``SignalEvaluator`` applies it.
    risk_ceiling_fraction: float | None
    #: The sizing rule's cost profile (``None`` under ``fixed_fractional_v1``).
    #: Open risk to stop is cost-inclusive under it, the same definition as a
    #: plan's ``risk_amount`` and as ``RiskContextBuilder.open_risk_by_instrument``.
    sizing_cost_profile: ExecutionProfile | None
    #: ``quote currency -> account currency`` at :attr:`as_of`.
    rate: Callable[[str], float]
    #: Bars 0..now INCLUSIVE for an instrument (the strategy's own view).
    history: Callable[[str], pd.DataFrame]
    #: The marked-to-market equity curve up to and including this bar.
    equity_curve: Callable[[], pd.Series]
    #: This bar, as the position book sees it (a regime source may read it).
    view: BarSlice


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    """One signal's allocation, as the engine needs it to size (or refuse) it."""

    signal_id: str
    #: The tier's base risk per trade, % of equity, after the ceiling.
    base_risk_pct: float
    #: Fraction of the base risk, in [0, 1].
    weight: float
    dropped: bool
    drop_reason: str | None
    #: The compact, JSON-serialisable record of every step (the "why this
    #: size" row). The engine adds ``bar_time``, ``size`` and ``outcome``.
    record: Mapping[str, Any]


class ConstructionPolicy(Protocol):
    """Allocates a decision bar's signals against the engine's own book.

    Satisfied by ``fiboki.portfolio.engine_policy.BacktestConstructionPolicy``
    (the engine may not import ``portfolio``). ``fingerprint()`` enters
    :meth:`BacktestConfig.fingerprint` and therefore every stored result and
    every evaluation cache key.
    """

    def fingerprint(self) -> dict[str, object]: ...

    def allocate(self, request: ConstructionRequest) -> Sequence[AllocationDecision]: ...


#: How a run without portfolio construction is fingerprinted.
CONSTRUCTION_NONE = "none"


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
    #: What an OHLC frame WITHOUT a ``price_basis`` column is taken to be. The
    #: fill model assumes mid bars; a frame that says it is BID or ASK is refused
    #: whatever this says (convert it with ``data.providers.histdata.bid_to_mid``
    #: first). ``True`` records ``assumed_mid`` against that instrument in the
    #: data fingerprint, so the assumption is visible on every stored result;
    #: ``False`` refuses an unlabelled frame outright.
    assume_mid: bool = True
    #: Portfolio construction on each decision bar. ``None`` (the engine-level
    #: default, forced by the layering rule: see the module docstring) is the
    #: flat ``risk_fraction`` path, byte-identical to every pinned ledger and
    #: fingerprinted ``construction=none``. Research runs pass the paper
    #: runtime's policy (``validation.engine_evaluator.
    #: research_construction_policy``), so research and paper size alike.
    construction: ConstructionPolicy | None = None

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
            "assume_mid": self.assume_mid,
            # The construction policy version (and everything else that can
            # move a size) rather than an ENGINE_VERSION bump: ``none`` and a
            # policy are different runs of the same engine generation.
            "construction": (
                CONSTRUCTION_NONE
                if self.construction is None
                else dict(self.construction.fingerprint())
            ),
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
    #: One row per signal refused by an entry lock: the decision bar, the
    #: instrument, the strategy and the lock's code. Empty for a run whose
    #: policy declares no locks.
    lock_blocks: list[dict[str, object]] = field(default_factory=list)
    #: One row per signal offered to portfolio construction, in decision order:
    #: the policy's record (tier, base risk, weight, every step's factor and
    #: detail) plus ``bar_time``, ``size`` and ``outcome`` (``queued``,
    #: ``allocation_dropped`` or ``sized_to_zero``). Empty under
    #: ``construction=None``.
    allocation_ledger: list[dict[str, Any]] = field(default_factory=list)
    #: Aligned 1:1 with :attr:`trades`: the index into
    #: :attr:`allocation_ledger` of the decision that opened each trade.
    trade_allocation_index: list[int | None] = field(default_factory=list)

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
        frame = pd.DataFrame(rows, columns=list(self.LEDGER_COLUMNS))
        if self.allocation_ledger:
            # "Why this size", on the trade row, as compact JSON. Absent under
            # construction=None so that frame is unchanged.
            frame["allocation"] = self.trade_allocations()
        return frame

    def trade_allocations(self) -> list[str | None]:
        """The allocation record behind each trade, compact JSON, aligned with trades."""
        out: list[str | None] = []
        for k in range(len(self.trades)):
            idx = (
                self.trade_allocation_index[k]
                if k < len(self.trade_allocation_index)
                else None
            )
            out.append(None if idx is None else allocation_json(self.allocation_ledger[idx]))
        return out

    def allocation_ledger_text(self) -> str:
        """Canonical text of the size-and-reason ledger: one compact JSON row per line."""
        return "".join(allocation_json(row) + "\n" for row in self.allocation_ledger)

    def allocation_ledger_sha256(self) -> str:
        return hashlib.sha256(self.allocation_ledger_text().encode("utf-8")).hexdigest()

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

        if config.construction is not None and not callable(getattr(sizer, "allocated", None)):
            raise TypeError(
                f"BacktestConfig.construction is set but {type(sizer).__name__} cannot "
                "size at an allocation (it has no allocated(risk_fraction=, "
                "portfolio_weight=)). Use FixedFractionalSizer, or construction=None: "
                "a sizer that ignored the weight would report construction it never did."
            )
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
        self.price_basis: dict[str, str] = {}
        for sym in self.symbols:
            frame, basis = _validate_frame(sym, data[sym], assume_mid=config.assume_mid)
            self.frames[sym] = frame
            self.price_basis[sym] = basis
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

        # Entry locks count SESSION bars of one timeframe. It is read off the
        # data here and each signal is held to it below; a lock counted in bars
        # of a guessed size would be a lock of a guessed length.
        self.lock_registration: LockRegistration | None = None
        locks = self.exit_policy.locks
        if locks is not None and locks.active:
            frames = {timeframe_for_interval(self._intervals[s]) for s in self.symbols}
            if len(frames) != 1:
                raise ValueError(
                    "entry locks need one timeframe per run; these frames have bar "
                    f"sizes {sorted(t.value for t in frames)}"
                )
            self.lock_registration = LockRegistration(locks, frames.pop())

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
        lock_book = (
            LockBook(default=self.lock_registration)
            if self.lock_registration is not None
            else None
        )
        self.lock_book = lock_book
        lock_blocks: list[dict[str, object]] = []
        construction = cfg.construction
        allocation_ledger: list[dict[str, Any]] = []
        #: pending-entry seq -> ledger row, until the entry fills or is refused
        allocation_by_pending: dict[int, int] = {}
        #: managed-position seq -> ledger row, once filled
        allocation_by_position: dict[int, int] = {}
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
            advanced = book.advance(view)
            if lock_book is not None:
                for trade in advanced.trades:
                    lock_book.on_close(trade)
            if allocation_by_pending:
                for event in advanced.entries:
                    row = allocation_by_pending.pop(event.pending.seq, None)
                    if row is not None and event.filled and event.managed is not None:
                        allocation_by_position[event.managed.seq] = row

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
            batch: list[tuple[Signal, Instrument]] = []
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

                # Entry locks, AFTER the reversal is scheduled: a lock refuses
                # new risk and never stands in the way of an exit.
                if lock_book is not None:
                    reg = self.lock_registration
                    if reg is not None and signal.timeframe != reg.timeframe.value:
                        raise ValueError(
                            f"signal timeframe {signal.timeframe} on a "
                            f"{reg.timeframe.value} run: entry locks would be counted "
                            "in bars of the wrong size"
                        )
                    lock = lock_book.is_locked(signal.instrument, signal.strategy_id, ts)
                    if lock is not None:
                        book.bump("instrument_lock")
                        lock_blocks.append(
                            {
                                "bar_time": ts,
                                "instrument": signal.instrument,
                                "strategy_id": signal.strategy_id,
                                "direction": signal.direction.value,
                                "lock": lock.code,
                            }
                        )
                        continue

                if construction is not None:
                    # Allocated together with the rest of this bar's signals,
                    # below, exactly as the paper runtime allocates a batch.
                    batch.append((signal, instr))
                    continue

                rate = self._rate(instr.quote, ts)
                size = self.sizer.size_for(signal, instr, account, rate)
                if size <= 0:
                    book.bump("sized_to_zero")
                    continue
                orders_submitted += 1
                self._queue(book, signal, instr, size, i)

            # -- 7. portfolio construction, then ONE sizing call per accepted
            #    candidate (``SignalEvaluator.evaluate`` on the paper path).
            if batch:
                assert construction is not None
                decisions = {
                    d.signal_id: d
                    for d in construction.allocate(
                        self._construction_request(
                            book, view, ctx, account, [s for s, _ in batch],
                            eq_ts, eq_equity, written,
                        )
                    )
                }
                bar_time = ts.isoformat()
                for signal, instr in batch:
                    decision = decisions.get(signal.signal_id)
                    if decision is None:
                        raise RuntimeError(
                            f"construction returned no decision for signal on "
                            f"{signal.instrument} at {ts}; a signal may be refused, "
                            "never silently skipped"
                        )
                    row = {**dict(decision.record), "bar_time": bar_time}
                    if decision.dropped or decision.weight <= 0:
                        book.bump("allocation_dropped")
                        allocation_ledger.append(
                            {**row, "size": 0.0, "outcome": "allocation_dropped"}
                        )
                        continue
                    rate = self._rate(instr.quote, ts)
                    sizer = self.sizer.allocated(  # type: ignore[attr-defined]
                        risk_fraction=decision.base_risk_pct / 100.0,
                        portfolio_weight=decision.weight,
                    )
                    size = sizer.size_for(signal, instr, account, rate)
                    if size <= 0:
                        book.bump("sized_to_zero")
                        allocation_ledger.append(
                            {**row, "size": 0.0, "outcome": "sized_to_zero"}
                        )
                        continue
                    orders_submitted += 1
                    entry = self._queue(book, signal, instr, size, i)
                    allocation_by_pending[entry.seq] = len(allocation_ledger)
                    allocation_ledger.append({**row, "size": size, "outcome": "queued"})

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
            config_fingerprint={
                **self.config.fingerprint(),
                "sizing": sizer_fingerprint(self.sizer),
            },
            data_fingerprint=self.data_fingerprint(),
            rejections=dict(sorted(book.rejections.items())),
            bankrupt=bankrupt,
            signals_seen=signals_seen,
            orders_submitted=orders_submitted,
            exit_legs=book.exit_legs,
            exit_policy_fingerprint=self.exit_policy.fingerprint(),
            lock_blocks=lock_blocks,
            allocation_ledger=allocation_ledger,
            trade_allocation_index=(
                _trade_allocation_index(book, allocation_by_position)
                if allocation_ledger
                else []
            ),
        )

    # -------------------------------------------------------------- helpers

    def _queue(
        self, book: PositionBook, signal: Signal, instr: Instrument, size: float, i: int
    ) -> PendingEntry:
        return book.queue_entry(
            PendingEntry(
                seq=book.next_seq(),
                instrument=instr,
                direction=signal.direction,
                size=size,
                stop_price=signal.stop_price,
                take_profit_prices=tuple(signal.take_profit_prices),
                take_profit_allocations=tuple(signal.take_profit_allocations),
                strategy_id=signal.strategy_id,
                actionable_index=i + 1 + self.config.profile.latency_bars,
                created_index=i,
            )
        )

    def _construction_request(
        self,
        book: PositionBook,
        view: BarSlice,
        ctx: BarContext,
        account: AccountState,
        signals: list[Signal],
        eq_ts: np.ndarray,
        eq_equity: np.ndarray,
        written: int,
    ) -> ConstructionRequest:
        """The book as ``PaperBroker.account()`` / ``positions()`` would report it."""
        cfg = self.config
        ts = view.timestamp

        def equity_curve() -> pd.Series:
            # One row per bar, as the paper venue's ``equity_curve`` has.
            return pd.Series(
                eq_equity[:written].copy(),
                index=pd.DatetimeIndex(eq_ts[:written], tz="UTC"),
            )

        return ConstructionRequest(
            as_of=ts,
            account=replace(account, margin_used=_margin_used(book, view)),
            open_positions=tuple(op.position for op in book.open),
            signals=tuple(signals),
            account_ccy=cfg.account_ccy,
            risk_ceiling_fraction=getattr(self.sizer, "risk_fraction", None),
            sizing_cost_profile=getattr(self.sizer, "resolved_cost_profile", None),
            rate=lambda ccy: self._rate(ccy, ts),
            history=ctx.history,
            equity_curve=equity_curve,
            view=view,
        )

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
                "price_basis": self.price_basis.get(sym, "assumed_mid"),
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


def allocation_json(row: Mapping[str, Any]) -> str:
    """Compact, canonical JSON for one allocation row (sorted keys, no spaces).

    ``json`` writes a float with ``repr``, which round-trips exactly, so two
    rows are the same text only when every factor is the same double.
    """
    return json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str)


def _margin_used(book: PositionBook, view: BarSlice) -> float:
    """Margin in use, computed as ``PaperBroker._margin_used`` computes it.

    Marked at the instrument's latest close (the paper venue's last delivered
    bar), at the entry FX rate, over the instrument's retail leverage.
    """
    total = 0.0
    for op in book.open:
        leverage = op.instrument.retail_leverage or 1.0
        mark = view.last_closes.get(op.position.instrument)
        price = mark if mark is not None else op.entry_mid
        rate = op.entry_fx
        total += abs(price * op.position.size * op.instrument.contract_size * rate) / leverage
    return total


def _trade_allocation_index(
    book: PositionBook, by_position: Mapping[int, int]
) -> list[int | None]:
    """Link each trade to the decision that opened it, via its final exit leg.

    The book appends a position's FINAL leg immediately before its ``Trade`` on
    every close path (exit, reversal, flatten), so the k-th final leg belongs to
    the k-th trade.
    """
    finals = [leg.position_seq for leg in book.exit_legs if leg.final]
    out: list[int | None] = []
    for k in range(len(book.trades)):
        seq = finals[k] if k < len(finals) else None
        out.append(None if seq is None else by_position.get(seq))
    return out


_plan_legs = plan_legs


#: ``price_basis`` values the fill model can price both sides from.
EXECUTABLE_PRICE_BASES = frozenset({"mid", "synthetic_mid"})

_UTC_NAMES = frozenset({"UTC", "Etc/UTC", "tzutc()", "UTC+00:00", "Etc/Universal", "Universal", "Zulu"})


def _is_utc(tz: object) -> bool:
    return tz is not None and (str(tz) in _UTC_NAMES or getattr(tz, "zone", None) == "UTC")


def sizer_fingerprint(sizer: object) -> dict[str, object]:
    """How a run was sized, for the result's config fingerprint."""
    fp = getattr(sizer, "fingerprint", None)
    if callable(fp):
        return dict(fp())
    out: dict[str, object] = {"sizer": type(sizer).__name__}
    size = getattr(sizer, "size", None)
    if isinstance(size, int | float):
        out["size"] = float(size)
    policy = getattr(sizer, "policy", None)
    if policy is not None and hasattr(policy, "fingerprint"):
        out["policy"] = policy.fingerprint()
    return out


def _validate_frame(
    symbol: str, frame: pd.DataFrame, *, assume_mid: bool = True
) -> tuple[pd.DataFrame, str]:
    """The OHLC the engine will trade on, and the price basis it is on.

    Refuses a frame whose ``price_basis`` column says BID, ASK or LAST: the fill
    model assumes mid bars, so a long entered on BID bars pays bid + half
    spread, which is the true MID -- half a spread too cheap -- and a short's
    stop triggers on BID highs where the real trigger is the ASK. The error is
    directional (it flatters long-biased strategies), so it is refused rather
    than tolerated. A frame without the column is taken as mid only under
    ``assume_mid``, and is then recorded as ``assumed_mid``.

    Requires a UTC index, not merely a tz-aware one: financing rollovers and
    the financing-day rule read the UTC hour and weekday, so a frame in
    Europe/London shifts every rollover by the DST offset.
    """
    required = ("open", "high", "low", "close")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise KeyError(f"{symbol}: OHLC frame missing columns {missing}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"{symbol}: frame index must be a DatetimeIndex")
    if frame.index.tz is None:
        raise ValueError(f"{symbol}: frame index must be timezone-aware UTC")
    if not _is_utc(frame.index.tz):
        raise ValueError(
            f"{symbol}: frame index is in {frame.index.tz}, not UTC. Convert with "
            ".tz_convert('UTC') first; financing and session rules read UTC hours."
        )
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{symbol}: frame index must be sorted ascending")
    if frame.index.has_duplicates:
        raise ValueError(f"{symbol}: frame index has duplicate timestamps")
    if len(frame) == 0:
        raise ValueError(f"{symbol}: frame is empty")
    if "price_basis" in frame.columns:
        bases = sorted({str(getattr(b, "value", b)).lower() for b in frame["price_basis"].unique()})
        bad = [b for b in bases if b not in EXECUTABLE_PRICE_BASES]
        if bad:
            raise ValueError(
                f"{symbol}: price_basis {bad} is not executable on both sides. The "
                "fill model assumes MID bars; convert BID bars with "
                "fiboki.data.providers.histdata.bid_to_mid (and record the assumed "
                "spread) before running. Trading BID bars as mid understates a "
                "long's entry cost by half a spread and a short's stop-outs."
            )
        if len(bases) != 1:
            raise ValueError(f"{symbol}: frame mixes price bases {bases}")
        basis = bases[0]
    elif assume_mid:
        basis = "assumed_mid"
    else:
        raise ValueError(
            f"{symbol}: frame has no price_basis column and assume_mid is False; "
            "say what the prices are rather than let the engine guess"
        )
    sub = frame[list(required)].astype(np.float64)
    if not np.isfinite(sub.to_numpy()).all():
        raise ValueError(f"{symbol}: OHLC contains NaN or inf; repair upstream")
    return sub, basis


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
