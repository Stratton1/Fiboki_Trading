"""The exit vocabulary the Strategy DSL declares, expressed as engine data.

Why this module exists
----------------------
``strategy/dsl.py`` has always been able to *say* "scale out half at 1.5R, trail
the rest on a 3x ATR chandelier once 1R is banked, give up after 120 bars, and
stand aside for two bars afterwards". Until this module existed, ``backtest/
engine.py`` could only *hear* "one stop and one target", because it read
``signal.take_profit_prices[0]`` and nothing else. Every number the platform has
produced so far is therefore a number for a stop-and-first-target-only
realisation of these documents, which is a materially different strategy: the
``donchian_breakout_atr`` seed declares NO take-profit at all and relies
entirely on an ATR chandelier, so on XAUUSD H4 it produced six trades in
thirteen years, every one of them running to its hard stop.

The shape of the fix
--------------------
An :class:`ExitPolicy` is plain, frozen, hashable data derived once from a bound
:class:`~fiboki.strategy.dsl.StrategyDocument`. The engine consumes the policy
and never sees the document, so:

* the policy can be constructed by hand in a golden test without building a
  whole strategy document;
* the engine keeps its single downward dependency on ``core`` and ``sim`` — the
  one function in here that reads the DSL is a *builder*, called by whoever owns
  the document, and it is the only thing in ``backtest/`` that imports
  ``strategy``;
* the policy's fingerprint is recorded on the result, so a ledger can name the
  exit vocabulary that produced it.

Dependency inversion for the economic calendar
----------------------------------------------
``EventRestriction`` needs a blackout query, which lives in
``marketstate/calendar.py`` — a package ABOVE ``backtest/`` in the dependency
order recorded in ``docs/v2/ARCHITECTURE.md`` §2 and now enforced by
``tests/unit/test_layering.py``. Importing it here would invert the arrow, so
the engine codes against :class:`BlackoutSource`, a structural protocol that
``fiboki.marketstate.calendar.EconomicCalendar`` already satisfies. The caller
supplies the calendar.

**With no calendar supplied, or with an empty one, every blackout query returns
False and a backtest trades straight through FOMC and NFP.** That is not a bug
in this module and it is not silently repaired here; see
``fiboki.marketstate.calendar.USER_ACTION_NOTE``, which is the verbatim text for
the USER_ACTIONS document.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd

from fiboki.backtest.version import ENGINE_VERSION

if TYPE_CHECKING:  # pragma: no cover - typing only, never an import at runtime
    from fiboki.strategy.dsl import StrategyDocument

__all__ = [
    "NO_TRAIL",
    "BlackoutSource",
    "EventBlackout",
    "ExitPolicy",
    "ReversalMode",
    "TrailKind",
    "TrailSpec",
    "exit_policy_from_document",
]


# --------------------------------------------------------------------------
# Trailing
# --------------------------------------------------------------------------


class TrailKind(str, Enum):
    """How a trailing stop derives its candidate level on a CLOSED bar.

    ``ATR_CHANDELIER``
        ``extreme_since_entry - value * ATR`` for a long (mirrored for a short),
        where ``extreme_since_entry`` is the highest high the position has seen.
        This is the classic Chandelier Exit and the one three of the five seed
        documents declare.
    ``FIXED_DISTANCE``
        ``extreme_since_entry - value`` in PRICE units. The DSL cannot currently
        express it; the engine can, so a golden test can pin the mechanics
        without an ATR series in the way.
    ``PERCENT``
        ``extreme_since_entry * (1 - value/100)`` for a long. Trailed from the
        extreme rather than from the close, which is the conventional reading of
        a "10% trailing stop" and the more conservative of the two.
    ``INDICATOR_LINE``
        The named column's value on the closed bar (a Kijun, a moving average).
    ``BREAKEVEN_AFTER_R``
        Move the stop to the entry price once ``activate_after_r`` is reached and
        then stop trailing. Equivalent to
        ``PositionManagement.move_stop_to_breakeven_at_r``; both are accepted
        because both appear in the seed documents.
    """

    NONE = "none"
    ATR_CHANDELIER = "atr_chandelier"
    FIXED_DISTANCE = "fixed_distance"
    PERCENT = "percent"
    INDICATOR_LINE = "indicator_line"
    BREAKEVEN_AFTER_R = "breakeven_after_r"


@dataclass(frozen=True, slots=True)
class TrailSpec:
    """One trailing rule.

    ``activate_after_r`` is a multiple of the position's INITIAL risk per unit
    (``|entry - initial stop|``). The trail is inert until the position's
    favourable excursion reaches it, which is what makes "let it breathe for 1R,
    then trail" expressible.
    """

    kind: TrailKind = TrailKind.NONE
    value: float = 0.0
    activate_after_r: float = 0.0
    atr_column: str | None = None
    level_column: str | None = None

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("TrailSpec.value must be >= 0")
        if self.activate_after_r < 0:
            raise ValueError("TrailSpec.activate_after_r must be >= 0")
        if self.kind is TrailKind.ATR_CHANDELIER:
            if not self.atr_column:
                raise ValueError("atr_chandelier trailing needs an atr_column")
            if self.value <= 0:
                raise ValueError("atr_chandelier trailing needs value > 0")
        if self.kind is TrailKind.INDICATOR_LINE and not self.level_column:
            raise ValueError("indicator_line trailing needs a level_column")
        if self.kind in (TrailKind.FIXED_DISTANCE, TrailKind.PERCENT) and self.value <= 0:
            raise ValueError(f"{self.kind.value} trailing needs value > 0")

    @property
    def active(self) -> bool:
        return self.kind is not TrailKind.NONE

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(c for c in (self.atr_column, self.level_column) if c)

    def fingerprint(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "value": float(self.value),
            "activate_after_r": float(self.activate_after_r),
            "atr_column": self.atr_column,
            "level_column": self.level_column,
        }

    def candidate(
        self,
        *,
        sign: int,
        extreme: float,
        close: float,
        atr: float,
        level: float,
    ) -> float | None:
        """The level this rule proposes on a closed bar, or ``None``.

        ``sign`` is +1 for a long, -1 for a short. Returning ``None`` means "this
        bar cannot say" (a NaN indicator, a trail that is not this kind) and the
        caller must leave the stop alone rather than invent a level.
        """
        if self.kind is TrailKind.ATR_CHANDELIER:
            if not math.isfinite(atr) or atr <= 0:
                return None
            return extreme - sign * self.value * atr
        if self.kind is TrailKind.FIXED_DISTANCE:
            return extreme - sign * self.value
        if self.kind is TrailKind.PERCENT:
            return extreme * (1.0 - sign * self.value / 100.0)
        if self.kind is TrailKind.INDICATOR_LINE:
            if not math.isfinite(level):
                return None
            return level
        # NONE and BREAKEVEN_AFTER_R propose nothing of their own; breakeven is
        # handled by the engine's breakeven rule, which this builder populates.
        _ = close
        return None


NO_TRAIL = TrailSpec()


# --------------------------------------------------------------------------
# Reversal
# --------------------------------------------------------------------------


class ReversalMode(str, Enum):
    """What an open position does when the strategy signals the other way.

    ``IGNORE``
        Nothing. The opposite signal becomes an ordinary order and is then
        refused by ``max_per_instrument``. This is what
        ``allow_reversal_on_opposite_signal = False`` means today.
    ``CLOSE_ONLY``
        Close the position at the next bar's open and do NOT open the new one.
        The DSL has no field for this; the engine supports it so that a document
        schema which grows one has somewhere to land.
    ``REVERSE``
        Close at the next bar's open and let the new order fill on that same
        bar. ``allow_reversal_on_opposite_signal = True``.
    """

    IGNORE = "ignore"
    CLOSE_ONLY = "close_only"
    REVERSE = "reverse"


# --------------------------------------------------------------------------
# Event restrictions
# --------------------------------------------------------------------------


class BlackoutSource(Protocol):
    """The one question ``backtest/`` asks an economic calendar.

    ``fiboki.marketstate.calendar.EconomicCalendar`` satisfies this structurally.
    The protocol exists so the engine does not import upwards.
    """

    def in_blackout(
        self,
        instrument: str,
        ts: pd.Timestamp,
        *,
        minutes_before: int = 30,
        minutes_after: int = 30,
        min_impact: Any = "high",
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class EventBlackout:
    """When a strategy declines to open a position.

    ``blocked_event_tags`` is carried for the audit trail and for a future
    calendar that can filter by ``recurring_key``; the blackout query itself
    filters by impact, which is the axis
    :class:`~fiboki.marketstate.calendar.EconomicCalendar` exposes.
    """

    minutes_before: int = 0
    minutes_after: int = 0
    blocked_event_tags: tuple[str, ...] = ()
    avoid_month_end: bool = False
    avoid_rollover_hour: bool = False
    min_impact: str = "high"

    def __post_init__(self) -> None:
        if self.minutes_before < 0 or self.minutes_after < 0:
            raise ValueError("blackout margins must be non-negative")

    @property
    def queries_calendar(self) -> bool:
        return self.minutes_before > 0 or self.minutes_after > 0

    @property
    def active(self) -> bool:
        return self.queries_calendar or self.avoid_month_end or self.avoid_rollover_hour

    def fingerprint(self) -> dict[str, Any]:
        return {
            "minutes_before": int(self.minutes_before),
            "minutes_after": int(self.minutes_after),
            "blocked_event_tags": list(self.blocked_event_tags),
            "avoid_month_end": bool(self.avoid_month_end),
            "avoid_rollover_hour": bool(self.avoid_rollover_hour),
            "min_impact": self.min_impact,
        }

    def blocks(
        self,
        instrument: str,
        ts: pd.Timestamp,
        *,
        rollover_hour: int,
        calendar: BlackoutSource | None,
    ) -> str | None:
        """The reason this bar is closed to new entries, or ``None``.

        Evaluated on the bar the position would actually be OPENED on, not the
        bar that produced the signal. The distinction is not cosmetic. On an H4
        clock with bars at 01/05/09/13/17/21 UTC and a 21:00 rollover,
        evaluating ``avoid_rollover_hour`` on the signal bar would block the
        signal stamped 21:00 — whose entry lands at 01:00, well clear of the
        rollover — and would happily let the signal stamped 17:00 deal straight
        into the 21:00 open, which is the exact moment the execution profile
        triples the spread. The restriction is about when you are dealing.

        The three rules are checked in a fixed order so the reason recorded in
        ``BacktestResult.rejections`` is deterministic.

        SHARP EDGE, stated rather than papered over: ``avoid_rollover_hour``
        compares whole hours, so on a series whose bars are STAMPED at the
        rollover hour it blocks every entry. A D1 series stamped 21:00 UTC
        against the default 21:00 rollover is exactly that case, and the run
        would produce zero trades with a large ``rollover_hour`` rejection
        count. The count is the evidence; nothing here silently disables the
        rule, because a restriction that quietly switches itself off is worse
        than one that visibly bites. On H4 it removes one entry bar in six.
        """
        if self.avoid_rollover_hour and int(ts.hour) == int(rollover_hour):
            return "rollover_hour"
        if self.avoid_month_end and _is_month_end(ts):
            return "month_end"
        if self.queries_calendar and calendar is not None:
            blocked = calendar.in_blackout(
                instrument,
                ts,
                minutes_before=self.minutes_before,
                minutes_after=self.minutes_after,
                min_impact=self.min_impact,
            )
            if blocked:
                return "event_blackout"
        return None


def _is_month_end(ts: pd.Timestamp) -> bool:
    return bool(ts.day == ts.days_in_month)


# --------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    """Everything the engine needs to manage a position after it is open.

    ``allocations`` is a FALLBACK. Per-signal allocations travel on
    ``Signal.take_profit_allocations``, because only the compiler knows which
    declared leg produced which price once the prices have been sorted by
    distance and de-duplicated. The policy's copy is what a hand-built signal in
    a test (or a strategy that is not a compiled document) gets instead.
    """

    allocations: tuple[float, ...] = ()
    trailing: TrailSpec = NO_TRAIL
    breakeven_at_r: float | None = None
    max_bars_in_trade: int | None = None
    cooldown_bars_after_exit: int = 0
    reversal: ReversalMode = ReversalMode.IGNORE
    events: EventBlackout | None = None

    def __post_init__(self) -> None:
        total = sum(self.allocations)
        if any(a <= 0.0 or a > 1.0 for a in self.allocations):
            raise ValueError("every allocation must lie in (0, 1]")
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"allocations sum to {total:.6f} > 1.0; a position cannot be "
                "closed more than once"
            )
        if self.breakeven_at_r is not None and self.breakeven_at_r < 0:
            raise ValueError("breakeven_at_r must be >= 0")
        if self.max_bars_in_trade is not None and self.max_bars_in_trade < 1:
            raise ValueError("max_bars_in_trade must be >= 1")
        if self.cooldown_bars_after_exit < 0:
            raise ValueError("cooldown_bars_after_exit must be >= 0")

    @property
    def needs_series(self) -> tuple[str, ...]:
        return self.trailing.columns

    def fingerprint(self) -> dict[str, Any]:
        """Every field of this policy that can move a fill, as stored on a record.

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
            "allocations": [float(a) for a in self.allocations],
            "trailing": self.trailing.fingerprint(),
            "breakeven_at_r": None if self.breakeven_at_r is None else float(self.breakeven_at_r),
            "max_bars_in_trade": (
                None if self.max_bars_in_trade is None else int(self.max_bars_in_trade)
            ),
            "cooldown_bars_after_exit": int(self.cooldown_bars_after_exit),
            "reversal": self.reversal.value,
            "events": None if self.events is None else self.events.fingerprint(),
        }


DEFAULT_EXIT_POLICY = ExitPolicy()


# --------------------------------------------------------------------------
# The builder (the only place in backtest/ that reads the DSL)
# --------------------------------------------------------------------------


def _bound_number(name: str, value: Any) -> float:
    """A DSL field that must be a NUMBER by the time the engine sees it.

    Every numeric field in the schema is ``float | ParamRef``, and
    :func:`exit_policy_from_document` already refuses an unbound document. This
    turns "already refused" into "provably a number here", so a reference that
    slipped through some future path raises with its own name rather than
    becoming ``float("<ParamRef ...>")`` or, worse, something that happens to
    coerce.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(
            f"{name} is {value!r}, not a number. The document is not fully bound; "
            "bind it before deriving an exit policy."
        )
    return float(value)


def exit_policy_from_document(document: StrategyDocument) -> ExitPolicy:
    """Translate a BOUND strategy document's exit vocabulary into an ExitPolicy.

    Refuses an unbound document: a ``{"$param": ...}`` reference where a trail
    multiple should be would otherwise be coerced to something numeric-looking
    and the engine would trail on a number nobody chose.
    """
    from fiboki.strategy.dsl import StrategyDocument as _Doc  # local: see module docstring

    if not isinstance(document, _Doc):  # pragma: no cover - defensive
        raise TypeError("exit_policy_from_document needs a StrategyDocument")
    unbound = document.unbound_parameters()
    if unbound:
        raise ValueError(
            f"{document.strategy_id}: cannot derive an exit policy from a document "
            f"with unresolved parameter reference(s) {list(unbound)}. Bind it first."
        )

    pm = document.position_management
    trailing = _trail_from_model(document.trailing)

    breakeven = pm.move_stop_to_breakeven_at_r
    if document.trailing is not None and document.trailing.kind == "breakeven_after_r":
        # The document expressed breakeven through the TRAILING model instead of
        # through position_management. Both are honoured, and when both are
        # present the EARLIER activation wins: a document that says breakeven at
        # 1R in one field and 2R in the other means breakeven, and taking the
        # later of the two would quietly make the position riskier than either
        # statement asks for.
        from_trail = _bound_number(
            "trailing.activate_after_r", document.trailing.activate_after_r
        )
        breakeven = (
            from_trail
            if breakeven is None
            else min(_bound_number("move_stop_to_breakeven_at_r", breakeven), from_trail)
        )

    reversal = (
        ReversalMode.REVERSE if pm.allow_reversal_on_opposite_signal else ReversalMode.IGNORE
    )
    ev = document.events
    events = EventBlackout(
        minutes_before=int(ev.block_minutes_before),
        minutes_after=int(ev.block_minutes_after),
        blocked_event_tags=tuple(ev.blocked_event_tags),
        avoid_month_end=bool(ev.avoid_month_end),
        avoid_rollover_hour=bool(ev.avoid_rollover_hour),
    )

    return ExitPolicy(
        allocations=tuple(float(leg.allocation) for leg in document.take_profits),
        trailing=trailing,
        breakeven_at_r=(
            None
            if breakeven is None
            else _bound_number("move_stop_to_breakeven_at_r", breakeven)
        ),
        max_bars_in_trade=(
            None
            if pm.max_bars_in_trade is None
            else int(_bound_number("max_bars_in_trade", pm.max_bars_in_trade))
        ),
        cooldown_bars_after_exit=int(
            _bound_number("cooldown_bars_after_exit", pm.cooldown_bars_after_exit)
        ),
        reversal=reversal,
        events=events if events.active else None,
    )


def _trail_from_model(model: Any) -> TrailSpec:
    if model is None or model.kind == "none":
        return NO_TRAIL
    atr_col = model.atr.column if model.atr is not None else None
    level_col = model.level.column if model.level is not None else None
    value = _bound_number("trailing.value", model.value)
    activate = _bound_number("trailing.activate_after_r", model.activate_after_r)
    if model.kind == "atr_chandelier":
        return TrailSpec(
            kind=TrailKind.ATR_CHANDELIER,
            value=value,
            activate_after_r=activate,
            atr_column=atr_col,
        )
    if model.kind == "indicator_line":
        return TrailSpec(
            kind=TrailKind.INDICATOR_LINE,
            value=value,
            activate_after_r=activate,
            level_column=level_col,
        )
    if model.kind == "percent":
        if value <= 0:
            # The schema allows value=0 for a percent trail, which is a trail
            # that never moves. Say so as "no trail" rather than constructing a
            # degenerate one whose stop would sit exactly on the extreme.
            return NO_TRAIL
        return TrailSpec(
            kind=TrailKind.PERCENT,
            value=value,
            activate_after_r=activate,
        )
    if model.kind == "breakeven_after_r":
        # Not a trail: it is a one-shot stop move, handled by breakeven_at_r.
        return NO_TRAIL
    raise ValueError(f"unhandled trailing kind {model.kind!r}")  # pragma: no cover
