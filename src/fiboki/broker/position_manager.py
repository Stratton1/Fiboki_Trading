"""Venue-backed position management: ONE book, three venues, one stop at a time.

What was missing
----------------
``broker/paper.py`` drove a :class:`~fiboki.backtest.position.PositionBook` --
the same object ``backtest/engine.py`` drives -- so a paper run and a backtest
of the same document produced byte-identical ledgers. The IG and OANDA adapters
drove nothing. A demo deployment would therefore have attached the hard stop
and the first target at entry and **nothing would have trailed**: no scale-out
leg beyond the first, no breakeven move, no time stop. Every backtest figure
for a multi-leg or trailing document described a strategy the demo account was
not running.

``docs/v2/USER_ACTIONS.md`` D1 named this as the largest single item of
engineering before demo enablement. This module is it.

The constraint, stated once and obeyed everywhere below
--------------------------------------------------------
**A venue holds exactly ONE stop and ONE limit per position.** That is not a
limitation of our adapters; it is what IG and OANDA are. So the manager holds
*two* pictures of every position and keeps them reconciled:

``intent``
    what the shared :class:`PositionBook` says should happen -- the full
    ladder, the trail, the breakeven, the time stop. This is the picture the
    backtest describes.
``venue``
    the one stop and the one limit actually resting at the broker. This is the
    picture that survives our process dying.

At entry the two agree: the order carries the hard stop and the FIRST target.
From then on every divergence the book creates is pushed to the venue as an
amendment or a partial close when a bar closes -- and the residue that cannot
be pushed, because a venue cannot hold a second target, is measured rather than
ignored (see :class:`ManagedExitExposure`).

Four properties this module is built to hold
---------------------------------------------
1. **Every amend is idempotent.** The client reference is derived from the
   position and the DESIRED LEVELS, so re-issuing a level the venue already
   holds collides with its own earlier record and is skipped without a network
   call. Retrying an unconfirmed amend is therefore always safe, which is what
   makes the retry loop below defensible.
2. **Every amend goes through** :class:`~fiboki.broker.execution_service.ExecutionService`.
   Nothing here calls an adapter. The durable intent record is written before
   dispatch for an amendment exactly as it is for an entry, because a crash
   between "we decided to move the stop" and "the venue moved it" leaves the
   same kind of hole.
3. **Every amend is telemetered.** :class:`AmendTelemetry` records what the
   book wanted, what the venue held before, what it holds after, how many
   attempts it took and how long it took. Live-versus-modelled divergence in
   exit MANAGEMENT is measurable with the same seriousness as divergence in
   fills.
4. **An intent that cannot be realised raises an alert.** It never silently
   diverges. A refused amendment is retried with backoff and, if it still
   cannot be realised, reported -- because a stop the operator believes has
   trailed to breakeven and which is actually still at the original level is
   the most expensive class of silent failure this platform can have.

The honest approximation: a MODELLED book against a REAL venue
---------------------------------------------------------------
The book here is fed by bars and by the fill simulator, exactly as the
backtester's is. It is therefore a *modelled* position: its entry price is the
simulator's, not the venue's. That is deliberate and it is what makes the
parity test possible, but it has a consequence worth stating plainly rather
than discovering: **every level the manager derives from the entry price -- the
breakeven move above all -- is derived from the MODELLED entry, not the dealt
one.** Where the venue filled at a different price, the breakeven stop the
manager pushes is off by that difference. The divergence is recorded on every
:class:`AmendTelemetry` row (``modelled_entry`` versus ``venue_entry``) so it
can be measured; it is not corrected, because correcting it would make the
demo run a different strategy from the backtest and destroy the one property
this whole design exists to preserve.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import pandas as pd

from fiboki.backtest.exits import ExitPolicy
from fiboki.backtest.position import (
    AdvanceResult,
    BarSlice,
    ManagedPosition,
    PendingEntry,
    PositionBook,
    plan_legs,
)
from fiboki.broker.base import OrderAck
from fiboki.core.contracts import AccountState, Order, Position
from fiboki.core.enums import ExitReason
from fiboki.core.instruments import get as get_instrument
from fiboki.sim.fills import Bar

__all__ = [
    "MANAGED_EXIT_USER_ACTION_NOTE",
    "AmendKind",
    "AmendTelemetry",
    "BookDriver",
    "DegradedModeRefused",
    "IntentDivergence",
    "ManagedExitExposure",
    "ManagerCycle",
    "PolicyRealisability",
    "RetryPolicy",
    "VenueIntent",
    "VenuePositionManager",
    "assess_policy",
    "queue_entry_for_order",
    "queue_pending_entry",
    "require_venue_realisable",
]


# ==========================================================================
# The operational note. Extends broker/paper.py's USER_ACTION_NOTE.
# ==========================================================================

MANAGED_EXIT_USER_ACTION_NOTE = (
    "MANAGED-EXIT EXPOSURE IS NOW MEASURED. IT IS NOT ELIMINATED.\n"
    "USER ACTION REQUIRED before any strategy is promoted to demo.\n"
    "\n"
    "A venue -- IG, OANDA, and the paper adapter that models them -- holds "
    "exactly ONE stop and ONE limit per position. VenuePositionManager attaches "
    "the hard stop and the first take-profit leg at entry and then pushes every "
    "later leg, every trail step, every breakeven move and every time stop to "
    "the venue as an instruction issued when a bar closes. Those instructions "
    "require this process to be alive.\n"
    "\n"
    "WHAT A DEAD WORKER NOW COSTS, AS A NUMBER: the manager publishes "
    "`managed_exit_exposure` -- the account-currency risk-to-the-attached-stop "
    "of the open size whose intended exit is NOT resting at the venue. A "
    "three-leg document has leg one resting as the venue's limit; legs two and "
    "three are ours, so the size behind them counts. A trail-only document "
    "(donchian_breakout_atr) has NO limit resting at the venue at all, so its "
    "whole size counts and it is the highest-exposure shape we run.\n"
    "\n"
    "DEGRADED MODE. A strategy whose exit policy cannot be realised by a venue "
    "-- more than one take-profit leg, a trailing stop, a breakeven move or a "
    "time stop -- is REFUSED promotion to demo by "
    "`require_venue_realisable()` unless the operator passes "
    "`accept_managed_exit_exposure=True`, which is a named person accepting a "
    "named number. There is no configuration file that grants this and no "
    "environment variable: it is an argument at the call site, because the "
    "acceptance belongs to whoever is promoting the strategy.\n"
    "\n"
    "WHAT TO DO WITH THE NUMBER:\n"
    "  1. Alert on worker heartbeat staleness (obs/alerts.py HEARTBEAT_STALE, "
    "WORKER_DOWN) and treat it as a POSITION-MANAGEMENT incident. The size of "
    "the incident is `managed_exit_exposure` at the moment the heartbeat went "
    "stale.\n"
    "  2. After any restart, `VenuePositionManager.resume()` runs before "
    "trading: it re-derives intent for every recovered position from the "
    "durable order intent and re-attaches what the venue can hold. What it "
    "CANNOT re-derive -- how far a trail had already moved while we were dead -- "
    "is reported as a divergence, not assumed away.\n"
    "  3. A position whose venue-side stop no longer matches our intent is "
    "detected by `reconcile()` and re-amended. If the re-amend cannot be "
    "realised, it alerts. Silence means agreement; it never means 'not "
    "checked'.\n"
    "  4. Prefer documents whose FIRST leg and hard stop alone are an "
    "acceptable outcome, because that is the outcome a dead worker produces.\n"
    "\n"
    "The backtest models the MANAGED case. A backtest figure therefore still "
    "assumes a worker that never dies. That assumption is now MEASURED at every "
    "bar rather than merely stated, but it is still an assumption and it is "
    "still optimistic."
)


# ==========================================================================
# Shared per-bar mechanics
# ==========================================================================


class BookDriver:
    """The per-bar bookkeeping that every :class:`PositionBook` caller needs.

    Extracted verbatim from ``PaperBroker.on_bar``, which is the only reason
    the IG and OANDA managers can claim parity with the backtester rather than
    merely hoping for it: they do not have their own copy of this, they have
    *this*.

    What it owns is deliberately small -- the monotonic bar counter, the
    previous-bar timestamps that the staleness test reads, the declared bar
    intervals and the last bar seen per instrument -- and it owns them because
    a one-bar offset in any of them changes which fills are treated as stale
    and breaks parity only at weekend gaps, which is the worst possible place
    for a bug to hide.
    """

    def __init__(self, book: PositionBook) -> None:
        self.book = book
        self.bar_index = -1
        self.now: pd.Timestamp | None = None
        self.last_bars: dict[str, Bar] = {}
        self.previous_times: dict[str, pd.Timestamp] = {}
        self.intervals: dict[str, pd.Timedelta] = {}

    def set_bar_interval(self, symbol: str, interval: pd.Timedelta) -> None:
        """Declare the expected bar spacing, used for staleness detection.

        The backtester derives this from the median spacing of the whole frame.
        A live feed cannot, so it must be told -- and a session that is not told
        will not detect stale prices, which is why this is explicit rather than
        guessed.
        """
        self.intervals[symbol] = interval

    def slice_for(
        self,
        bars: Mapping[str, Bar],
        *,
        bar_index: int | None = None,
        timestamp: pd.Timestamp | None = None,
        previous_timestamp: pd.Timestamp | None = None,
        series: Mapping[str, Mapping[str, float]] | None = None,
    ) -> BarSlice:
        ts = timestamp or self.now
        if ts is None:
            raise ValueError("slice_for needs a timestamp; no bar has arrived yet")
        return BarSlice(
            index=self.bar_index if bar_index is None else bar_index,
            timestamp=ts,
            bars=dict(bars),
            previous_timestamp=previous_timestamp,
            previous_times=dict(self.previous_times),
            intervals=dict(self.intervals),
            last_closes={sym: bar.close for sym, bar in self.last_bars.items()},
            carried_bars=dict(self.last_bars),
            series={k: dict(v) for k, v in (series or {}).items()},
        )

    def advance(
        self,
        bars: Mapping[str, Bar],
        *,
        bar_index: int,
        timestamp: pd.Timestamp | None = None,
        series: Mapping[str, Mapping[str, float]] | None = None,
    ) -> tuple[AdvanceResult, BarSlice]:
        """One timeline step: financing, reversals, entries, exits."""
        if bar_index <= self.bar_index:
            raise ValueError(
                f"bars must advance monotonically; got {bar_index} after "
                f"{self.bar_index}. Replaying a bar would double-charge financing."
            )
        ts = timestamp
        if ts is None:
            if not bars:
                raise ValueError("advance needs either bars or an explicit timestamp")
            ts = next(iter(bars.values())).timestamp
        previous_timestamp = self.now
        self.bar_index = bar_index
        self.now = ts

        # Roll the staleness bookkeeping BEFORE anything prices a fill. The
        # engine's ``_previous_bar_time`` is the bar before the current one, so
        # ours must be too.
        for sym, bar in bars.items():
            if sym in self.last_bars:
                self.previous_times[sym] = self.last_bars[sym].timestamp
            self.last_bars[sym] = bar

        view = self.slice_for(
            bars,
            bar_index=bar_index,
            timestamp=ts,
            previous_timestamp=previous_timestamp,
            series=series,
        )
        return self.book.advance(view), view


def queue_pending_entry(
    book: PositionBook,
    *,
    instrument: str,
    direction: Any,
    size: float,
    stop_loss: float | None,
    take_profit_prices: Sequence[float],
    take_profit_allocations: Sequence[float],
    take_profit: float | None,
    bar_index: int,
    latency_bars: int,
    strategy_id: str,
    ref: Any,
    seq: int | None = None,
) -> PendingEntry:
    """Queue one entry on the book. THE one place that shape is constructed.

    Shared by the paper adapter and the venue manager so the two cannot drift.
    Three details matter and all three are parity-critical:

    * the sequence number comes from the BOOK's counter, which also feeds the
      fill simulator's counter-based RNG. An extra increment on one side and
      not the other silently decorrelates the two price paths;
    * ``take_profit_prices`` is carried through verbatim, and an order naming
      only ``take_profit`` is read as ONE full-size target, which is what every
      order built before the exit vocabulary existed meant;
    * the entry becomes actionable no earlier than the next bar's open plus the
      profile's latency, because a signal produced on a closed bar cannot be
      acted on at that bar's close without look-ahead.
    """
    prices = tuple(take_profit_prices)
    if not prices and take_profit is not None:
        prices = (float(take_profit),)
    entry = PendingEntry(
        seq=book.next_seq() if seq is None else seq,
        instrument=get_instrument(instrument),
        direction=direction,
        size=size,
        stop_price=stop_loss if stop_loss is not None else 0.0,
        take_profit_prices=prices,
        take_profit_allocations=tuple(take_profit_allocations),
        strategy_id=strategy_id,
        actionable_index=bar_index + 1 + int(latency_bars),
        created_index=bar_index,
        ref=ref,
    )
    book.queue_entry(entry)
    return entry


def queue_entry_for_order(
    book: PositionBook,
    order: Order,
    *,
    bar_index: int,
    latency_bars: int,
    strategy_id: str,
    ref: Any,
    seq: int | None = None,
) -> PendingEntry:
    """:func:`queue_pending_entry`, for a caller that already holds an Order."""
    return queue_pending_entry(
        book,
        instrument=order.instrument,
        direction=order.direction,
        size=order.size,
        stop_loss=order.stop_loss,
        take_profit_prices=order.take_profit_prices,
        take_profit_allocations=order.take_profit_allocations,
        take_profit=order.take_profit,
        bar_index=bar_index,
        latency_bars=latency_bars,
        strategy_id=strategy_id,
        ref=ref,
        seq=seq,
    )


# ==========================================================================
# What a venue can hold, and what it cannot
# ==========================================================================


@dataclass(frozen=True, slots=True)
class VenueIntent:
    """The ONE stop and ONE limit we want resting at the venue for a position.

    Everything else the exit policy declares lives in the book and reaches the
    venue only as a later instruction. This type is deliberately the whole of
    what a venue can be asked to remember.
    """

    stop_loss: float | None
    take_profit: float | None

    def matches(self, other: VenueIntent | None, *, tolerance: float) -> bool:
        if other is None:
            return False
        return _close(self.stop_loss, other.stop_loss, tolerance) and _close(
            self.take_profit, other.take_profit, tolerance
        )


def _close(a: float | None, b: float | None, tolerance: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tolerance


class AmendKind(str, Enum):
    """Why an instruction was issued. Recorded, never inferred."""

    ATTACH = "attach"
    TRAIL = "trail"
    BREAKEVEN = "breakeven"
    NEXT_LEG = "next_leg"
    SCALE_OUT = "scale_out"
    CLOSE = "close"
    REATTACH = "reattach"
    CORRECTION = "correction"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry with exponential backoff, for an IDEMPOTENT instruction.

    An entry order is never retried: a retry that reached the venue twice would
    double a real position, which is why the execution service treats an
    unconfirmed entry as UNKNOWN and reconciles it instead. An AMENDMENT is the
    opposite case -- it is idempotent by construction (see
    :func:`amend_client_ref`), so retrying it costs nothing and NOT retrying it
    means a trail step is lost to one HTTP hiccup.
    """

    attempts: int = 3
    initial_backoff_seconds: float = 0.5
    multiplier: float = 2.0
    max_backoff_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryPolicy.attempts must be at least 1")
        if self.initial_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("backoff seconds must not be negative")

    def backoff(self, attempt: int) -> float:
        """Seconds to wait after ``attempt`` (1-based) has failed."""
        raw = self.initial_backoff_seconds * (self.multiplier ** max(0, attempt - 1))
        return min(raw, self.max_backoff_seconds)


# ==========================================================================
# Telemetry
# ==========================================================================


@dataclass(frozen=True, slots=True)
class AmendTelemetry:
    """One venue instruction, from what the book wanted to what the venue did.

    This is the exit-management analogue of
    :class:`fiboki.data.telemetry.ExecutionTelemetryRecord`. That record makes
    ENTRY divergence measurable -- requested versus filled price, requested
    versus filled size. Without this one, EXIT divergence is invisible: a trail
    that never reached the venue and a trail that reached it and was hit look
    identical in a trade ledger.
    """

    at: pd.Timestamp
    position_id: str
    broker_ref: str | None
    instrument: str
    strategy_id: str
    kind: AmendKind
    applied: bool
    skipped: bool
    attempts: int
    latency_ms: float
    #: What the BOOK wanted.
    intended_stop: float | None = None
    intended_take_profit: float | None = None
    #: What the VENUE held before and after, as the venue reported it.
    venue_stop_before: float | None = None
    venue_stop_after: float | None = None
    venue_take_profit_before: float | None = None
    venue_take_profit_after: float | None = None
    #: The modelled/dealt divergence that every derived level inherits.
    modelled_entry: float | None = None
    venue_entry: float | None = None
    size: float = 0.0
    client_ref: str = ""
    error: str = ""
    mode: str = ""

    @property
    def entry_divergence(self) -> float | None:
        """Dealt minus modelled entry. The error every derived level carries."""
        if self.modelled_entry is None or self.venue_entry is None:
            return None
        return float(self.venue_entry) - float(self.modelled_entry)

    def as_row(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "position_id": self.position_id,
            "broker_ref": self.broker_ref,
            "instrument": self.instrument,
            "strategy_id": self.strategy_id,
            "kind": self.kind.value,
            "applied": self.applied,
            "skipped": self.skipped,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
            "intended_stop": self.intended_stop,
            "intended_take_profit": self.intended_take_profit,
            "venue_stop_before": self.venue_stop_before,
            "venue_stop_after": self.venue_stop_after,
            "venue_take_profit_before": self.venue_take_profit_before,
            "venue_take_profit_after": self.venue_take_profit_after,
            "modelled_entry": self.modelled_entry,
            "venue_entry": self.venue_entry,
            "entry_divergence": self.entry_divergence,
            "size": self.size,
            "client_ref": self.client_ref,
            "error": self.error,
            "mode": self.mode,
        }


class TelemetrySink(Protocol):
    def __call__(self, record: AmendTelemetry) -> None: ...


class AlertSink(Protocol):
    """``(key, message, detail) -> None``.

    A Protocol rather than an import of :mod:`fiboki.obs.alerts`, because
    ``broker`` sits BELOW ``obs`` in the dependency order that
    ``tests/unit/test_layering.py`` enforces. The worker, which sits above
    both, wires this to the real dispatcher.
    """

    def __call__(self, key: str, message: str, detail: Mapping[str, Any]) -> None: ...


# ==========================================================================
# Divergence and exposure
# ==========================================================================


@dataclass(frozen=True, slots=True)
class IntentDivergence:
    """The venue and our intent disagree about a position.

    Named kinds, because the three have different correct responses and lumping
    them together is how V1's reconciliation produced output nobody acted on.
    """

    kind: str
    position_id: str
    broker_ref: str | None
    instrument: str
    intended_stop: float | None = None
    venue_stop: float | None = None
    intended_take_profit: float | None = None
    venue_take_profit: float | None = None
    detail: str = ""

    def summary(self) -> str:
        return (
            f"{self.kind}:{self.instrument}:{self.broker_ref or self.position_id}"
            f" intent_stop={self.intended_stop} venue_stop={self.venue_stop}"
            + (f" {self.detail}" if self.detail else "")
        )

    def as_row(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "position_id": self.position_id,
            "broker_ref": self.broker_ref,
            "instrument": self.instrument,
            "intended_stop": self.intended_stop,
            "venue_stop": self.venue_stop,
            "intended_take_profit": self.intended_take_profit,
            "venue_take_profit": self.venue_take_profit,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ManagedExitExposure:
    """How much open risk currently depends on this worker staying alive.

    THE DEFINITION, stated so it cannot be quietly reinterpreted later:

        the account-currency risk between the current mark and the stop
        ACTUALLY RESTING AT THE VENUE, on the open size whose intended exit is
        NOT resting at the venue.

    A three-leg ladder has leg one resting as the venue's limit; the size
    behind legs two and three is ours to close, so it counts. A trail-only
    document has no limit resting at the venue at all, so its whole size
    counts. A single-target document with no trail, no breakeven and no time
    stop contributes ZERO, which is correct: a dead worker changes nothing
    about it.

    Risk is measured to the VENUE's stop rather than to our intended one
    because the venue's stop is the outcome a dead worker actually produces.
    """

    at: pd.Timestamp
    amount: float
    positions: int
    #: Positions contributing a non-zero amount.
    exposed_positions: int
    account_ccy: str = "GBP"
    per_instrument: Mapping[str, float] = field(default_factory=dict)
    per_strategy: Mapping[str, float] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "managed_exit_exposure": self.amount,
            "account_ccy": self.account_ccy,
            "positions": self.positions,
            "exposed_positions": self.exposed_positions,
            "per_instrument": dict(self.per_instrument),
            "per_strategy": dict(self.per_strategy),
        }


# ==========================================================================
# Policy realisability and the demo gate
# ==========================================================================


class DegradedModeRefused(RuntimeError):
    """A strategy whose exit policy a venue cannot hold was refused promotion."""


@dataclass(frozen=True, slots=True)
class PolicyRealisability:
    """Can this exit policy be expressed by one stop and one limit?

    ``realisable`` is True only for the degenerate shape: at most one
    take-profit leg, no trail, no breakeven, no time stop. Everything else is
    managed client-side and therefore depends on the worker.
    """

    realisable: bool
    features: tuple[str, ...]
    note: str = ""

    def summary(self) -> str:
        if self.realisable:
            return "venue-realisable: one stop and at most one target"
        return "client-managed: " + ", ".join(self.features)


def assess_policy(
    policy: ExitPolicy, *, take_profit_legs: int | None = None
) -> PolicyRealisability:
    """Name every feature of ``policy`` a venue cannot hold.

    ``take_profit_legs`` is the number of legs the DOCUMENT declares, which the
    policy alone cannot always say: a policy carries allocations, and a signal
    carries prices. Pass it when you have it; without it the allocation count
    is used, which is the compiler's own answer.
    """
    features: list[str] = []
    legs = take_profit_legs if take_profit_legs is not None else len(policy.allocations)
    if legs > 1:
        features.append(f"multi_leg_take_profit:{legs}_legs")
    if policy.trailing.active:
        features.append(f"trailing_stop:{policy.trailing.kind.value}")
    if policy.breakeven_at_r is not None:
        features.append(f"breakeven_at_r:{policy.breakeven_at_r}")
    if policy.max_bars_in_trade is not None:
        features.append(f"time_stop:{policy.max_bars_in_trade}_bars")
    if not features:
        return PolicyRealisability(True, (), "one stop and at most one target")
    return PolicyRealisability(
        False,
        tuple(features),
        "these exit rules are managed in our process and freeze if it dies",
    )


def require_venue_realisable(
    policy: ExitPolicy,
    *,
    strategy_id: str,
    accept_managed_exit_exposure: bool = False,
    take_profit_legs: int | None = None,
    operator: str = "",
) -> PolicyRealisability:
    """The demo promotion gate. Refuses, or records an explicit acceptance.

    This is the documented degraded mode of
    :data:`MANAGED_EXIT_USER_ACTION_NOTE`. A strategy whose policy a venue
    cannot hold is refused promotion to demo UNLESS the caller passes
    ``accept_managed_exit_exposure=True`` and names an operator. There is
    deliberately no configuration file and no environment variable that grants
    this: the acceptance is an argument at the call site, because it belongs to
    whoever is doing the promoting rather than to whoever last edited a YAML.
    """
    assessment = assess_policy(policy, take_profit_legs=take_profit_legs)
    if assessment.realisable:
        return assessment
    if not accept_managed_exit_exposure:
        raise DegradedModeRefused(
            f"{strategy_id} declares exit rules a venue cannot hold "
            f"({', '.join(assessment.features)}). They are managed by this "
            "process, so they freeze the moment it dies: the position keeps "
            "whatever stop and target were last attached and nothing trails it. "
            "Promotion to demo requires an operator to accept that exposure "
            "explicitly -- pass accept_managed_exit_exposure=True and name the "
            "operator. See MANAGED_EXIT_USER_ACTION_NOTE."
        )
    if not operator:
        raise DegradedModeRefused(
            f"{strategy_id}: managed-exit exposure was accepted with no operator "
            "named. An acceptance nobody signed is not an acceptance."
        )
    return PolicyRealisability(
        False,
        assessment.features,
        f"accepted by {operator}: {assessment.note}",
    )


# ==========================================================================
# The cycle result
# ==========================================================================


@dataclass(frozen=True, slots=True)
class _FailedInstruction:
    """Stands in for an outcome when the instruction raised on its way out.

    Shaped like a ``SubmitOutcome`` so the telemetry path does not need a
    second branch: a failure has to be recorded in exactly the same row a
    success would be, or the failure rate is not measurable.
    """

    reason: str
    accepted: bool = False
    attempts: int = 1
    intent: Any = None
    skipped: bool = False


@dataclass(slots=True)
class ManagerCycle:
    """What one :meth:`VenuePositionManager.on_bar` did."""

    at: pd.Timestamp
    advance: AdvanceResult
    telemetry: list[AmendTelemetry] = field(default_factory=list)
    divergences: list[IntentDivergence] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    exposure: ManagedExitExposure | None = None

    @property
    def amends_applied(self) -> int:
        return sum(1 for t in self.telemetry if t.applied and not t.skipped)

    @property
    def amends_failed(self) -> list[AmendTelemetry]:
        return [t for t in self.telemetry if not t.applied]


# ==========================================================================
# The manager
# ==========================================================================


class VenuePositionManager:
    """Drives the shared :class:`PositionBook` against a real venue.

    Usage mirrors the paper adapter's, because it IS the paper adapter's loop
    with a venue bolted to the side::

        manager.on_bar(bars, bar_index=i, timestamp=ts, series=columns)
        # ... the worker asks strategies for signals, sizes them, and the
        #     execution service submits; then:
        manager.adopt(order, ack)

    Order construction is emphatically NOT here. ``Order`` is constructed in
    exactly one function in the tree -- ``ExecutionService.submit`` -- and
    ``tests/unit/test_no_gateway_bypass.py`` walks the AST and fails the build
    if a second site appears. This class receives the order that was already
    built and already permitted.
    """

    def __init__(
        self,
        *,
        execution: Any,
        book: PositionBook,
        context_factory: Callable[[Position, str], Any],
        telemetry: TelemetrySink | None = None,
        alert: AlertSink | None = None,
        retry: RetryPolicy | None = None,
        clock: Callable[[], pd.Timestamp] | None = None,
        sleeper: Callable[[float], None] | None = None,
        fx: Any = None,
        account_ccy: str = "GBP",
        latency_bars: int = 0,
        price_tolerance: float = 1e-9,
    ) -> None:
        self.execution = execution
        self.book = book
        self.driver = BookDriver(book)
        self.context_factory = context_factory
        self.telemetry = telemetry
        self.alert = alert
        self.retry = retry or RetryPolicy()
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self.sleeper = sleeper or time.sleep
        self.fx = fx
        self.account_ccy = account_ccy
        self.latency_bars = int(latency_bars)
        self.price_tolerance = float(price_tolerance)

        #: ``broker_ref -> VenueIntent`` as last CONFIRMED by the venue. Not
        #: what we asked for: what it told us it holds. An amendment whose
        #: outcome was unknown leaves this unchanged, so the next bar re-issues
        #: it, which is exactly the behaviour an idempotent instruction earns.
        self.venue_state: dict[str, VenueIntent] = {}
        #: ``broker_ref -> Position`` as the VENUE holds it. This, not the
        #: modelled position, is what every instruction addresses: the adapter
        #: deals against the venue's reference and the venue's size, and the
        #: modelled book is a separate picture that exists to decide WHAT to
        #: instruct. Conflating the two is how a scale-out ends up closing a
        #: size the venue does not have.
        self.venue_positions: dict[str, Position] = {}
        #: ``broker_ref -> dealt entry price``, for the modelled/dealt divergence.
        self.venue_entry: dict[str, float] = {}
        self.cycles: list[ManagerCycle] = []
        self.unrealised_intents: list[IntentDivergence] = []
        #: ``pending entry sequence -> (broker_ref, instrument)``.
        self._pending_orders: dict[int, tuple[str, str]] = {}
        #: ``book sequence -> broker_ref``. Survives the position leaving the
        #: book, which is precisely when a final close still has to be sent.
        self._ref_by_seq: dict[int, str] = {}
        #: ``broker_ref -> (venue position, reason)`` for closes the BOOK made
        #: and the venue has not confirmed (refused by the gateway, an error on
        #: the way out). Re-sent on every bar until applied or the venue no
        #: longer holds the position. Without this a single refused close left
        #: a real position nobody tracked: the book flat, the account not, and
        #: nothing that would ever close it (paper forward, 2026-10-06).
        self.pending_closes: dict[str, tuple[Position, str]] = {}
        self._last_exposure: ManagedExitExposure | None = None

    # ------------------------------------------------------------ plumbing

    def set_bar_interval(self, symbol: str, interval: pd.Timedelta) -> None:
        self.driver.set_bar_interval(symbol, interval)

    @property
    def now(self) -> pd.Timestamp | None:
        return self.driver.now

    @property
    def open_positions(self) -> list[ManagedPosition]:
        return list(self.book.open)

    def mark_to_market(self) -> tuple[float, float]:
        """``(unrealised, gross exposure)`` of the MODELLED book, account ccy.

        The modelled book's, not the venue's. The venue reports its own equity
        through ``adapter.account()`` and the two will differ by exactly the
        accumulated fill divergence -- which is a number worth watching, not a
        number to reconcile away by preferring one side.
        """
        if self.driver.now is None:
            return 0.0, 0.0
        return self.book.mark_to_market(self.driver.slice_for({}))

    def modelled_equity(self) -> float:
        """Balance plus unrealised, as the backtester and the paper adapter mean it.

        Sizing reads equity, so a caller that sized off BALANCE would take
        systematically different sizes from the backtest of the same document
        for as long as any position was open -- the same class of divergence as
        V1's adapter re-sizing off the broker balance, arrived at from the other
        direction.
        """
        unrealised, _exposure = self.mark_to_market()
        return self.book.balance + unrealised

    def account(self) -> AccountState:
        """The modelled account state, shaped for the sizer and the snapshot."""
        unrealised, _exposure = self.mark_to_market()
        equity = self.book.balance + unrealised
        self._peak_equity = max(getattr(self, "_peak_equity", equity), equity)
        return AccountState(
            balance=self.book.balance,
            equity=equity,
            currency=self.account_ccy,
            open_positions=len(self.book.open),
            realised_pnl=self.book.realised,
            unrealised_pnl=unrealised,
            peak_equity=self._peak_equity,
        )

    def _managed_by_ref(self) -> dict[str, ManagedPosition]:
        return {
            str(op.position.venue_ref): op
            for op in self.book.open
            if op.position.venue_ref
        }

    # ------------------------------------------------------------- entries

    def submit(self, plan: Any, context: Any, *, strategy_id: str = "") -> Any:
        """The production path: gateway, venue, then management.

        One call, because the three have to happen in that order and nothing
        should be able to do the middle one alone. The execution service holds
        the gateway decision and the durable intent; this adds the only thing it
        does not have, which is the ladder and the trail that the venue cannot
        hold and somebody therefore has to run.
        """
        outcome = self.execution.submit(plan, context)
        ack = getattr(outcome, "ack", None)
        if not getattr(outcome, "accepted", False) or ack is None or not ack.broker_ref:
            return outcome
        signal = plan.signal
        self._adopt(
            ack,
            instrument=plan.instrument,
            direction=plan.direction,
            size=plan.size,
            stop_loss=signal.stop_price,
            take_profit_prices=tuple(signal.take_profit_prices),
            take_profit_allocations=tuple(signal.take_profit_allocations),
            strategy_id=strategy_id or signal.strategy_id,
        )
        return outcome

    def precheck_entry(self, instrument: str, *, bump: bool = True) -> str | None:
        """Would the book refuse an entry queued now? Ask BEFORE dealing.

        WHY THIS EXISTS, and what it does not fix.

        The venue fills a market order the instant it is sent. The book models
        that fill at the NEXT bar's open, which is the same moment in wall-clock
        terms -- the bar closed, the worker woke, the order went. But it means
        the venue commits before the book's own entry gates have run, and if the
        book then refuses the entry the account holds a position the model does
        not. :meth:`on_bar` detects that, closes the stray position and alerts;
        this method is how a caller avoids creating one in the first place.

        The two gates it can answer EXACTLY, because both are evaluated at the
        index the entry would become actionable at:

        ``cooldown``
            armed only when a position CLOSES, and a reversal close does not arm
            it, so its value cannot change between now and the fill.
        ``max_concurrent`` / ``max_per_instrument``
            counted against the open book NET of reversal closes already
            scheduled to run before the fill. Ignoring those would refuse
            exactly the entry a reversal exists to make room for.

        The gates it CANNOT answer, and which therefore still reach
        ``entry_not_modelled``: an event blackout (evaluated against the fill
        bar's timestamp, which has not happened yet) and every rejection the
        fill simulator itself produces -- a stale price, a closed session, a
        partial fill. Those are properties of a bar we have not seen.
        """
        cfg = self.book.config
        actionable = self.driver.bar_index + 1 + self.latency_bars
        reason: str | None = None
        if actionable <= self.book.cooldown_until.get(instrument, -1):
            reason = "cooldown"
        else:
            closing = {
                pc.position_seq
                for pc in self.book.pending_closes
                if pc.actionable_index <= actionable
            }
            surviving = [op for op in self.book.open if op.seq not in closing]
            if len(surviving) >= cfg.max_concurrent:
                reason = "max_concurrent"
            elif (
                sum(1 for op in surviving if op.position.instrument == instrument)
                >= cfg.max_per_instrument
            ):
                reason = "max_per_instrument"
        if reason is None:
            return None
        if bump:
            self.book.bump(reason)
            # AND CONSUME A SEQUENCE NUMBER. This looks like bookkeeping and is
            # not. The book's counter feeds the fill simulator's COUNTER-BASED
            # RNG, so every path that reaches the same bar must have advanced it
            # the same number of times or the two price paths silently
            # decorrelate -- same trades, different slippage, and a parity test
            # that fails somewhere unrelated three exits later.
            #
            # The backtester and the paper adapter both take a sequence number
            # when the order is QUEUED and refuse it later, at fill time. This
            # method refuses it earlier, so it takes the number the queue would
            # have taken. Refusing without taking it would make the venue path
            # run a different price path from the backtest of the same document.
            self.book.next_seq()
        return reason

    def adopt(self, order: Order, ack: OrderAck, *, strategy_id: str = "") -> PendingEntry:
        """Take responsibility for an order the execution service has placed.

        The order already carries the hard stop and the FIRST target -- that is
        all the venue can hold -- and the book is queued with the WHOLE ladder,
        which is the thing this manager exists to run. The two are linked by the
        venue's own reference, never by an internal id: V1 compared a ``uuid4``
        against IG's ``dealId``, two key spaces that never intersect.
        """
        return self._adopt(
            ack,
            instrument=order.instrument,
            direction=order.direction,
            size=order.size,
            stop_loss=order.stop_loss,
            take_profit_prices=tuple(order.take_profit_prices),
            take_profit_allocations=tuple(order.take_profit_allocations),
            strategy_id=strategy_id or order.plan_id,
            take_profit=order.take_profit,
        )

    def _adopt(
        self,
        ack: OrderAck,
        *,
        instrument: str,
        direction: Any,
        size: float,
        stop_loss: float | None,
        take_profit_prices: tuple[float, ...],
        take_profit_allocations: tuple[float, ...],
        strategy_id: str,
        take_profit: float | None = None,
    ) -> PendingEntry:
        broker_ref = ack.broker_ref
        if not broker_ref:
            raise ValueError(
                f"cannot manage {ack.client_ref!r}: the ack carries no broker "
                "reference, so nothing can be amended or closed against it. "
                "Reconcile it instead."
            )
        if self.driver.bar_index < 0:
            raise ValueError(
                "adopt before the first bar: the manager has no timeline to make "
                "the order actionable on"
            )
        entry = queue_pending_entry(
            self.book,
            instrument=instrument,
            direction=direction,
            size=size,
            stop_loss=stop_loss,
            take_profit_prices=take_profit_prices,
            take_profit_allocations=take_profit_allocations,
            take_profit=take_profit,
            bar_index=self.driver.bar_index,
            latency_bars=self.latency_bars,
            strategy_id=strategy_id,
            ref=broker_ref,
        )
        attached = entry.take_profit_prices[0] if entry.take_profit_prices else None
        self._pending_orders[entry.seq] = (broker_ref, instrument)
        # What the venue holds NOW, from the order we sent it. Recorded rather
        # than assumed on the next bar, so the first trail step is a real diff.
        self.venue_state[broker_ref] = VenueIntent(
            stop_loss=stop_loss, take_profit=attached
        )
        fill = ack.fill
        if fill is not None:
            self.venue_entry[broker_ref] = float(fill.filled_price)
        self.venue_positions[broker_ref] = Position(
            instrument=instrument,
            direction=direction,
            size=float(fill.filled_size) if fill is not None else float(size),
            entry_price=float(fill.filled_price) if fill is not None else 0.0,
            entry_time=(
                fill.filled_at if fill is not None else (self.now or self.clock())
            ),
            stop_loss=float(stop_loss or 0.0),
            take_profit_targets=[float(attached)] if attached is not None else [],
            strategy_id=strategy_id,
            venue_ref=broker_ref,
        )
        return entry

    # -------------------------------------------------------------- the bar

    def on_bar(
        self,
        bars: Mapping[str, Bar],
        *,
        bar_index: int,
        timestamp: pd.Timestamp | None = None,
        series: Mapping[str, Mapping[str, float]] | None = None,
    ) -> ManagerCycle:
        """Advance the book one step, then make the venue agree with it.

        The ORDER of the two halves is not negotiable. The book decides first,
        against the bar that has just closed, exactly as the backtester does;
        only then is the venue told. Asking the venue first, or interleaving the
        two, would let a venue response influence an exit decision, and the
        decision would no longer be the one the backtest reproduces.
        """
        result, view = self.driver.advance(
            bars, bar_index=bar_index, timestamp=timestamp, series=series
        )
        cycle = ManagerCycle(at=view.timestamp, advance=result)

        for event in result.entries:
            record = self._pending_orders.pop(event.pending.seq, None)
            if record is None:
                continue
            broker_ref, _symbol = record
            if not event.filled or event.managed is None:
                # The BOOK refused an entry the venue has already filled. The
                # account holds a position the model does not, which is the one
                # divergence that must never be left standing: nothing would
                # manage it and nothing would close it. So it is CLOSED at the
                # venue and alerted, rather than merely reported.
                stray = self.venue_positions.pop(broker_ref, None)
                self.venue_state.pop(broker_ref, None)
                if stray is not None:
                    stray_reason = f"entry_not_modelled:{event.reason}"
                    if not self._instruct_close(
                        position=stray,
                        size=stray.size,
                        final=True,
                        kind=AmendKind.CORRECTION,
                        reason=stray_reason,
                        cycle=cycle,
                    ):
                        self.pending_closes[broker_ref] = (stray, stray_reason)
                self._raise_unrealised(
                    IntentDivergence(
                        kind="entry_not_modelled",
                        position_id="" if stray is None else stray.position_id,
                        broker_ref=broker_ref,
                        instrument=event.pending.instrument.symbol,
                        detail=f"book refused the entry: {event.reason}",
                    ),
                    cycle,
                    message=(
                        f"{event.pending.instrument.symbol}: the venue filled an "
                        f"order the position book refused ({event.reason}). The "
                        "stray position has been closed at the venue; check why "
                        "the pre-trade check did not catch it."
                    ),
                )
                continue
            event.managed.position.venue_ref = broker_ref
            self._ref_by_seq[event.managed.seq] = broker_ref

        # 1. Closing legs the BOOK produced. Every one is pushed to the venue:
        #    a close of a position the venue has already closed on its own stop
        #    reports PositionNotFound, which is the state we wanted and is
        #    recorded as such rather than retried.
        # 0. Closes decided on an earlier bar that the venue never confirmed.
        #    The decision was made then; this only delivers it.
        self.retry_pending_closes(cycle)

        self._push_closes(result, cycle)

        # 2. Whatever is still open: make the one stop and one limit agree.
        self._push_levels(cycle)

        # 3. The number that says what a dead worker would now cost.
        cycle.exposure = self.measure_managed_exit_exposure(view)
        self._last_exposure = cycle.exposure
        self.cycles.append(cycle)
        return cycle

    def flatten(
        self,
        bars: Mapping[str, Bar],
        *,
        reason: Any,
        positions: Sequence[ManagedPosition] | None = None,
    ) -> ManagerCycle:
        """Close positions at market NOW, in the book and then at the venue.

        The kill switch's FLATTEN, an operator flatten and the end of a replay
        all arrive here. The book decides first, exactly as on any other bar,
        so a flatten's ledger rows are produced by the same code the backtester
        uses and remain comparable.
        """
        view = self.driver.slice_for(bars)
        result = self.book.flatten(view, reason=reason, positions=positions)
        cycle = ManagerCycle(at=view.timestamp, advance=result)
        self.retry_pending_closes(cycle)
        self._push_closes(result, cycle)
        cycle.exposure = self.measure_managed_exit_exposure(view)
        self._last_exposure = cycle.exposure
        self.cycles.append(cycle)
        return cycle

    def finish(self, bars: Mapping[str, Bar]) -> ManagerCycle | None:
        """End of data: close what is left, flagged honestly as END_OF_DATA."""
        if not self.book.open:
            return None
        return self.flatten(bars, reason=ExitReason.END_OF_DATA)

    def _push_closes(self, result: AdvanceResult, cycle: ManagerCycle) -> None:
        """Every closing fill the BOOK produced becomes a venue instruction.

        Including the ones the venue's own resting stop should already have
        taken. That is deliberate: deciding which of our exits the venue would
        have reached by itself means comparing a modelled fill price against a
        resting level and guessing, and a wrong guess in the optimistic
        direction leaves a real position open with the book believing it flat.
        Sending the close unconditionally is safe because
        :class:`~fiboki.broker.base.PositionNotFound` -- "there is no such
        position" -- is the state we wanted and is recorded as ``already_flat``
        rather than retried.
        """
        still_open = {op.seq for op in self.book.open}
        for leg in result.legs:
            ref = self._ref_by_seq.get(leg.position_seq)
            if not ref:
                continue
            venue_position = self.venue_positions.get(ref)
            if venue_position is None:
                continue
            final = leg.final or leg.position_seq not in still_open
            applied = self._instruct_close(
                position=venue_position,
                size=min(leg.size, venue_position.size) if not final else venue_position.size,
                final=final,
                kind=AmendKind.CLOSE if final else AmendKind.SCALE_OUT,
                reason=leg.exit_reason,
                cycle=cycle,
            )
            if final:
                self.venue_state.pop(ref, None)
                self.venue_positions.pop(ref, None)
                self._ref_by_seq.pop(leg.position_seq, None)
                if not applied:
                    self.pending_closes[ref] = (venue_position, str(leg.exit_reason))
                continue
            venue_position.size = max(0.0, venue_position.size - float(leg.size))
            managed = next(
                (op for op in self.book.open if op.seq == leg.position_seq), None
            )
            if managed is None:
                continue
            # The venue's ONE limit now belongs to the NEXT leg. Left alone it
            # would still name the level just banked, and the remainder would be
            # closed a second time at the same price.
            self._instruct_amend(
                managed,
                VenueIntent(
                    stop_loss=managed.position.stop_loss,
                    take_profit=managed.active_target(),
                ),
                kind=AmendKind.NEXT_LEG,
                cycle=cycle,
            )

    def _instruct_close(
        self,
        *,
        position: Position,
        size: float,
        final: bool,
        kind: AmendKind,
        reason: str,
        cycle: ManagerCycle,
    ) -> bool:
        """Send one closing instruction. True when the venue is now flat for it.

        ``already_flat`` (the venue reports no such position) counts as done:
        that is the state the close wanted.
        """
        started = time.monotonic()
        context = self.context_factory(position, "close" if final else "reduce")
        try:
            outcome = (
                self.execution.close(position, context)
                if final
                else self.execution.close_partial(
                    position, size=size, context=context, reason=reason
                )
            )
        except Exception as exc:
            # NOT swallowed and NOT allowed to kill the bar. A closing
            # instruction that raised is a position the book believes flat and
            # the account may not be, which is precisely the divergence this
            # class exists to surface -- so it is recorded and alerted rather
            # than propagated into the worker's loop where one bad position
            # would stop every other position being managed.
            outcome = _FailedInstruction(f"{type(exc).__name__}:{exc}")
        applied = bool(getattr(outcome, "accepted", False))
        already_flat = "already_flat" in str(getattr(outcome, "reason", ""))
        record = AmendTelemetry(
            at=self.now or self.clock(),
            position_id=position.position_id,
            broker_ref=position.venue_ref,
            instrument=position.instrument,
            strategy_id=position.strategy_id,
            kind=kind,
            applied=applied or already_flat,
            skipped=already_flat,
            attempts=int(getattr(outcome, "attempts", 1)),
            latency_ms=(time.monotonic() - started) * 1000.0,
            size=size,
            client_ref=str(getattr(getattr(outcome, "intent", None), "client_ref", "")),
            error="" if applied or already_flat else str(getattr(outcome, "reason", "")),
            modelled_entry=position.entry_price,
            venue_entry=self.venue_entry.get(str(position.venue_ref)),
            mode=str(getattr(self.execution, "mode", "")),
        )
        self._emit(record, cycle)
        if not record.applied:
            self._raise_unrealised(
                IntentDivergence(
                    kind="close_not_realised",
                    position_id=position.position_id,
                    broker_ref=position.venue_ref,
                    instrument=position.instrument,
                    detail=record.error,
                ),
                cycle,
                message=(
                    f"{position.instrument}: the book closed "
                    f"{size} but the venue did not. The modelled ledger and the "
                    f"account now disagree. {record.error}"
                    + (" The close is re-sent every bar until the venue confirms." if final else "")
                ),
            )
        return record.applied

    def retry_pending_closes(self, cycle: ManagerCycle) -> None:
        """Re-send every close the venue has not confirmed. Through the gateway, as before.

        Not a new decision and not a second route: the book closed these
        positions on an earlier bar, and this delivers that same instruction
        through the same ``execution.close`` and the same exit check set.
        """
        for ref, (position, reason) in sorted(self.pending_closes.items()):
            if self._instruct_close(
                position=position,
                size=position.size,
                final=True,
                kind=AmendKind.CLOSE,
                reason=f"retry:{reason}",
                cycle=cycle,
            ):
                self.pending_closes.pop(ref, None)

    def _push_levels(self, cycle: ManagerCycle) -> None:
        for managed in self.book.open:
            ref = managed.position.venue_ref
            if not ref or str(ref) not in self.venue_positions:
                continue
            desired = VenueIntent(
                stop_loss=managed.position.stop_loss,
                take_profit=managed.active_target(),
            )
            if desired.matches(self.venue_state.get(str(ref)), tolerance=self.price_tolerance):
                continue
            kind = AmendKind.TRAIL if managed.stop_trailed else AmendKind.ATTACH
            if managed.breakeven_done and not managed.stop_trailed:
                kind = AmendKind.BREAKEVEN
            self._instruct_amend(managed, desired, kind=kind, cycle=cycle)

    def _instruct_amend(
        self,
        managed: ManagedPosition,
        desired: VenueIntent,
        *,
        kind: AmendKind,
        cycle: ManagerCycle,
    ) -> AmendTelemetry:
        modelled = managed.position
        ref = str(modelled.venue_ref or "")
        # Address the VENUE's position, not the modelled one: the adapter deals
        # against the venue's reference and the venue's size.
        position = self.venue_positions.get(ref) or modelled
        before = self.venue_state.get(ref)
        started = time.monotonic()
        context = self.context_factory(position, "amend")
        try:
            outcome = self.execution.amend(
                position,
                stop_loss=desired.stop_loss,
                take_profit=desired.take_profit,
                context=context,
                retry=self.retry,
                sleeper=self.sleeper,
                reason=kind.value,
            )
        except Exception as exc:
            outcome = _FailedInstruction(f"{type(exc).__name__}:{exc}")
        applied = bool(getattr(outcome, "accepted", False))
        if applied:
            self.venue_state[ref] = desired
            if desired.stop_loss is not None:
                position.stop_loss = float(desired.stop_loss)
            position.take_profit_targets = (
                [float(desired.take_profit)] if desired.take_profit is not None else []
            )
        record = AmendTelemetry(
            at=self.now or self.clock(),
            position_id=position.position_id,
            broker_ref=position.venue_ref,
            instrument=position.instrument,
            strategy_id=position.strategy_id,
            kind=kind,
            applied=applied,
            skipped=bool(getattr(outcome, "skipped", False)),
            attempts=int(getattr(outcome, "attempts", 1)),
            latency_ms=(time.monotonic() - started) * 1000.0,
            intended_stop=desired.stop_loss,
            intended_take_profit=desired.take_profit,
            venue_stop_before=None if before is None else before.stop_loss,
            venue_stop_after=desired.stop_loss if applied else (
                None if before is None else before.stop_loss
            ),
            venue_take_profit_before=None if before is None else before.take_profit,
            venue_take_profit_after=desired.take_profit if applied else (
                None if before is None else before.take_profit
            ),
            modelled_entry=managed.entry_mid,
            venue_entry=self.venue_entry.get(ref),
            size=position.size,
            client_ref=str(getattr(getattr(outcome, "intent", None), "client_ref", "")),
            error="" if applied else str(getattr(outcome, "reason", "")),
            mode=str(getattr(self.execution, "mode", "")),
        )
        self._emit(record, cycle)
        if not applied:
            self._raise_unrealised(
                IntentDivergence(
                    kind="amend_not_realised",
                    position_id=position.position_id,
                    broker_ref=position.venue_ref,
                    instrument=position.instrument,
                    intended_stop=desired.stop_loss,
                    venue_stop=None if before is None else before.stop_loss,
                    intended_take_profit=desired.take_profit,
                    venue_take_profit=None if before is None else before.take_profit,
                    detail=record.error,
                ),
                cycle,
                message=(
                    f"{position.instrument}: could not move the venue's stop to "
                    f"{desired.stop_loss} after {record.attempts} attempt(s). The "
                    f"venue still holds {None if before is None else before.stop_loss}. "
                    f"The position is NOT being managed as the backtest assumes. "
                    f"{record.error}"
                ),
            )
        return record

    # ------------------------------------------------------- reconciliation

    def reconcile(
        self, *, venue_positions: Sequence[Position] | None = None, repair: bool = True
    ) -> list[IntentDivergence]:
        """Does the venue hold what we intend? Detect, then repair.

        THE CHECK THAT MATTERS: a position whose venue-side stop no longer
        matches the manager's intent. It happens for mundane reasons -- an
        amendment whose response was lost and which we therefore did not record
        as applied, an operator moving a stop in the broker's own web UI, a
        venue that silently rounded a level to its tick -- and every one of them
        means the position is running to a level the backtest never modelled.
        """
        cycle = ManagerCycle(at=self.now or self.clock(), advance=AdvanceResult())
        if venue_positions is None:
            venue_positions = self.execution.adapter.positions()
        by_ref = {str(p.venue_ref): p for p in venue_positions if p.venue_ref}
        managed = self._managed_by_ref()
        divergences: list[IntentDivergence] = []

        for ref, op in sorted(managed.items()):
            venue = by_ref.get(ref)
            intended = VenueIntent(
                stop_loss=op.position.stop_loss, take_profit=op.active_target()
            )
            if venue is None:
                self.venue_positions.pop(ref, None)
                divergences.append(
                    IntentDivergence(
                        kind="missing_at_venue",
                        position_id=op.position.position_id,
                        broker_ref=ref,
                        instrument=op.position.instrument,
                        intended_stop=intended.stop_loss,
                        detail=(
                            "we are managing a position the venue does not have. "
                            "Either it closed on its own stop and we have not seen "
                            "the fill, or it never existed."
                        ),
                    )
                )
                continue
            venue_intent = VenueIntent(
                stop_loss=venue.stop_loss or None,
                take_profit=(
                    venue.take_profit_targets[0] if venue.take_profit_targets else None
                ),
            )
            # Record what the venue ACTUALLY holds, whatever we believed. The
            # belief is the thing that was wrong.
            self.venue_state[ref] = venue_intent
            venue.strategy_id = venue.strategy_id or op.position.strategy_id
            self.venue_positions[ref] = venue
            if intended.matches(venue_intent, tolerance=self.price_tolerance):
                continue
            divergence = IntentDivergence(
                kind="stop_mismatch",
                position_id=op.position.position_id,
                broker_ref=ref,
                instrument=op.position.instrument,
                intended_stop=intended.stop_loss,
                venue_stop=venue_intent.stop_loss,
                intended_take_profit=intended.take_profit,
                venue_take_profit=venue_intent.take_profit,
                detail="the venue's resting levels are not the ones we intend",
            )
            divergences.append(divergence)
            self._alert(
                f"stop_mismatch:{ref}",
                (
                    f"{op.position.instrument}: the venue's stop is "
                    f"{venue_intent.stop_loss} but this worker intends "
                    f"{intended.stop_loss}. The position is running to a level the "
                    "backtest did not model."
                ),
                divergence.as_row(),
            )
            if repair:
                self._instruct_amend(
                    op, intended, kind=AmendKind.CORRECTION, cycle=cycle
                )

        for ref in [r for r in self.pending_closes if r not in by_ref]:
            # The venue no longer holds it (its own stop, or a close that did
            # land): the pending close is complete.
            self.pending_closes.pop(ref, None)

        for ref, venue in sorted(by_ref.items()):
            if ref in self.pending_closes and ref not in managed:
                divergences.append(
                    IntentDivergence(
                        kind="close_pending",
                        position_id=venue.position_id,
                        broker_ref=ref,
                        instrument=venue.instrument,
                        venue_stop=venue.stop_loss or None,
                        detail=(
                            "the book closed this position and the venue has not "
                            f"confirmed ({self.pending_closes[ref][1]}); the close "
                            "is re-sent every bar until it is"
                        ),
                    )
                )
                continue
            if ref not in managed:
                divergences.append(
                    IntentDivergence(
                        kind="unmanaged_at_venue",
                        position_id=venue.position_id,
                        broker_ref=ref,
                        instrument=venue.instrument,
                        venue_stop=venue.stop_loss or None,
                        detail=(
                            "a REAL position at the venue that this manager is not "
                            "managing. Its stop is whatever is attached; nothing "
                            "trails it and no leg will be placed."
                        ),
                    )
                )

        if cycle.telemetry:
            self.cycles.append(cycle)
        return divergences

    def resume(
        self,
        recovery: Any,
        *,
        bar_index: int = 0,
        now: pd.Timestamp | None = None,
        reattach: bool = True,
    ) -> list[IntentDivergence]:
        """Startup reconciliation: re-derive intent for recovered positions.

        A restarted worker inherits real positions at the venue and an empty
        book. Everything the exit policy needed in order to manage them -- the
        ladder, the risk-per-unit that every R-multiple is measured against, the
        initial stop -- lived in the dead process's memory.

        What is RECOVERABLE comes from the durable order intent written before
        dispatch: the instrument, direction, size, entry, the original stop and
        the declared take-profit ladder. Those are re-derived here and the
        position is put back in the book so it is managed again from the next
        bar.

        What is NOT recoverable is how far a trail had already moved while we
        were dead, and how long the position had been held in BARS. Both are
        reported as divergences rather than guessed:

        * the trail is re-anchored to the stop the VENUE actually holds, which
          is the honest floor -- it can only be at or better than where we left
          it, because a trail never widens risk;
        * ``bars_held`` restarts at zero, so a time stop measures from the
          restart. A time stop is therefore LONGER than the document says
          across a restart. That is stated on the divergence and is not
          silently corrected, because inventing a bar count would be worse.
        """
        out: list[IntentDivergence] = []
        cycle = ManagerCycle(at=now or self.clock(), advance=AdvanceResult())
        for recovered in getattr(recovery, "positions", ()) or ():
            position = recovered.position
            ref = str(recovered.broker_ref or position.venue_ref or "")
            if not ref:
                continue
            intent = recovered.intent
            if intent is None:
                out.append(
                    IntentDivergence(
                        kind="orphan_unmanageable",
                        position_id=position.position_id,
                        broker_ref=ref,
                        instrument=position.instrument,
                        venue_stop=position.stop_loss or None,
                        detail=(
                            "a real position with NO local order intent. Its ladder "
                            "and its original stop are unknown, so no exit policy "
                            "can be re-derived for it. It keeps whatever stop is "
                            "attached and must be handled by an operator."
                        ),
                    )
                )
                self._alert(
                    f"orphan_unmanageable:{ref}",
                    (
                        f"{position.instrument}: recovered a real position with no "
                        "local record. It cannot be managed and is running on its "
                        "attached stop alone."
                    ),
                    {"broker_ref": ref, "instrument": position.instrument},
                )
                continue

            managed = self._rebuild(position, intent, bar_index=bar_index)
            self.book.open.append(managed)
            self._ref_by_seq[managed.seq] = ref
            self.venue_positions[ref] = position
            venue_intent = VenueIntent(
                stop_loss=position.stop_loss or None,
                take_profit=(
                    position.take_profit_targets[0]
                    if position.take_profit_targets
                    else None
                ),
            )
            self.venue_state[ref] = venue_intent
            if intent.filled_price:
                self.venue_entry[ref] = float(intent.filled_price)

            desired = VenueIntent(
                stop_loss=managed.position.stop_loss,
                take_profit=managed.active_target(),
            )
            out.append(
                IntentDivergence(
                    kind="recovered",
                    position_id=position.position_id,
                    broker_ref=ref,
                    instrument=position.instrument,
                    intended_stop=desired.stop_loss,
                    venue_stop=venue_intent.stop_loss,
                    intended_take_profit=desired.take_profit,
                    venue_take_profit=venue_intent.take_profit,
                    detail=(
                        "intent re-derived from the durable order record. "
                        "bars_held restarts at zero, so any time stop measures "
                        "from this restart and is LONGER than the document says."
                    ),
                )
            )
            if reattach and not desired.matches(
                venue_intent, tolerance=self.price_tolerance
            ):
                self._instruct_amend(
                    managed, desired, kind=AmendKind.REATTACH, cycle=cycle
                )
        if cycle.telemetry:
            self.cycles.append(cycle)
        return out

    def _rebuild(
        self, position: Position, intent: Any, *, bar_index: int
    ) -> ManagedPosition:
        """Reconstruct a :class:`ManagedPosition` from a durable order intent."""
        instrument = get_instrument(position.instrument)
        extra = dict(getattr(intent, "extra", None) or {})
        prices = tuple(float(p) for p in (extra.get("take_profit_prices") or ()))
        allocations = tuple(
            float(a) for a in (extra.get("take_profit_allocations") or ())
        )
        if not prices and intent.take_profit is not None:
            prices = (float(intent.take_profit),)
        entry_price = float(intent.filled_price or position.entry_price)
        # The ORIGINAL stop, not the current one: every R-multiple in the exit
        # policy is measured against the distance from entry to the stop the
        # position was opened with, and re-deriving it from a trailed stop would
        # shrink the denominator and make the trail arm far too early.
        original_stop = float(intent.stop_loss or position.stop_loss or 0.0)
        legs = plan_legs(instrument, position.size, prices, allocations)
        # A COPY. The recovered ``Position`` is the venue's handle and keeps
        # being the venue's handle; the book gets its own, because the whole
        # design rests on the two pictures being separately readable. Sharing
        # one object would make every divergence check compare a thing with
        # itself and pass.
        modelled = Position(
            instrument=position.instrument,
            direction=position.direction,
            size=position.size,
            entry_price=entry_price,
            entry_time=position.entry_time,
            stop_loss=position.stop_loss,
            take_profit_targets=list(position.take_profit_targets),
            strategy_id=position.strategy_id or getattr(intent, "strategy_id", ""),
            venue_ref=position.venue_ref,
        )
        # Legs already banked while we were alive cannot be told apart from legs
        # never reached, so the ladder is re-planned against the size STILL
        # open. A three-leg document that had already banked leg one comes back
        # as a three-leg ladder over the remainder -- more scale-outs than the
        # document declares, at the declared prices. Stated, not hidden.
        managed = ManagedPosition(
            seq=self.book.next_seq(),
            position=modelled,
            instrument=instrument,
            entry_mid=entry_price,
            entry_bar_index=bar_index,
            take_profit=legs[0].price if legs else None,
            entry_costs_account={
                "spread": 0.0,
                "commission": 0.0,
                "slippage": 0.0,
                "premium": 0.0,
            },
            entry_size=position.size,
            legs=legs,
            risk_per_unit=abs(entry_price - original_stop),
            initial_stop=original_stop,
            ref=position.venue_ref,
        )
        # A stop the venue holds BETTER than the original one can only have got
        # there by trailing, so say so: the exit reason on a subsequent stop-out
        # must read TRAILING_STOP and not STOP_LOSS.
        if position.stop_loss and original_stop:
            improved = (position.stop_loss - original_stop) * position.direction.sign
            if improved > 0:
                managed.stop_trailed = True
        return managed

    # ------------------------------------------------------------- exposure

    def measure_managed_exit_exposure(
        self, view: BarSlice | None = None
    ) -> ManagedExitExposure:
        """See :class:`ManagedExitExposure` for the definition. It is not a guess.

        The mark is this bar's close where there is one and the entry otherwise;
        the stop is the one the VENUE holds, because that is the outcome a dead
        worker produces. Where the venue's stop is unknown we fall back to our
        intended stop and the number is then an UNDERSTATEMENT, which is the
        wrong direction to be wrong in -- so an unknown venue stop also counts
        the position as exposed.
        """
        at = (view.timestamp if view is not None else None) or self.now or self.clock()
        total = 0.0
        per_instrument: dict[str, float] = {}
        per_strategy: dict[str, float] = {}
        exposed = 0
        for op in self.book.open:
            ref = str(op.position.venue_ref or "")
            attached = self.venue_state.get(ref)
            target_size = 0.0
            if attached is not None and attached.take_profit is not None:
                # The ONE limit resting at the venue closes exactly the leg it
                # was placed for. Everything behind it is ours.
                if op.next_leg < len(op.legs):
                    target_size = min(op.legs[op.next_leg].size, op.position.size)
                else:
                    target_size = op.position.size
            unattached = max(0.0, op.position.size - target_size)
            if unattached <= 0:
                continue
            stop = (attached.stop_loss if attached else None) or op.position.stop_loss
            mark = op.entry_mid
            if view is not None:
                bar = view.bar_or_last(op.position.instrument)
                if bar is not None:
                    mark = bar.close
                elif op.position.instrument in view.last_closes:
                    mark = float(view.last_closes[op.position.instrument])
            risk_per_unit = abs(float(mark) - float(stop or mark))
            rate = self._rate(op.instrument.quote, at)
            amount = (
                unattached * risk_per_unit * op.instrument.contract_size * rate
            )
            if amount <= 0:
                continue
            exposed += 1
            total += amount
            sym = op.position.instrument
            per_instrument[sym] = per_instrument.get(sym, 0.0) + amount
            sid = op.position.strategy_id or "unattributed"
            per_strategy[sid] = per_strategy.get(sid, 0.0) + amount
        return ManagedExitExposure(
            at=at,
            amount=total,
            positions=len(self.book.open),
            exposed_positions=exposed,
            account_ccy=self.account_ccy,
            per_instrument=per_instrument,
            per_strategy=per_strategy,
        )

    @property
    def managed_exit_exposure(self) -> float:
        """The last measured amount. ``0.0`` before the first bar."""
        return 0.0 if self._last_exposure is None else self._last_exposure.amount

    def _rate(self, from_ccy: str, when: pd.Timestamp) -> float:
        if self.fx is None:
            return 1.0
        return float(self.fx.rate(from_ccy, self.account_ccy, when))

    # -------------------------------------------------------------- sinks

    def _emit(self, record: AmendTelemetry, cycle: ManagerCycle) -> None:
        cycle.telemetry.append(record)
        if self.telemetry is not None:
            self.telemetry(record)

    def _raise_unrealised(
        self, divergence: IntentDivergence, cycle: ManagerCycle, *, message: str
    ) -> None:
        cycle.divergences.append(divergence)
        cycle.alerts.append(message)
        self.unrealised_intents.append(divergence)
        self._alert(
            f"{divergence.kind}:{divergence.broker_ref or divergence.position_id}",
            message,
            divergence.as_row(),
        )

    def _alert(self, key: str, message: str, detail: Mapping[str, Any]) -> None:
        if self.alert is None:
            return
        self.alert(key, message, dict(detail))
