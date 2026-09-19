"""The engine generation stamp, and what changing it means.

Why a version and not a date
-----------------------------
``backtest/engine.py`` used to read ``signal.take_profit_prices[0]`` and nothing
else. It had no handling for trailing stops, ``move_stop_to_breakeven_at_r``,
``max_bars_in_trade``, ``cooldown_bars_after_exit``,
``allow_reversal_on_opposite_signal`` or ``EventRestriction``, and the per-leg
``allocation`` fractions were discarded. Every number produced before
``backtest/exits.py`` existed is therefore a number for a
**stop-and-first-target-only realisation** of the documents — a different
strategy from the one each document describes. ``donchian_breakout_atr``, which
declares no take-profit at all and relies entirely on an ATR chandelier,
produced six trades in thirteen years under the old engine and 583 under the
new one.

A timestamp cannot express that. Two runs on the same day can straddle the
change, and a reader comparing two stored records has no way to know whether
they are comparable. A version string on the record answers it directly:
``engine_version`` differs, so the numbers are not comparable, full stop.

What is stamped, and what an empty stamp means
-----------------------------------------------
:data:`ENGINE_VERSION` is written onto every :class:`~fiboki.research.artefacts.
BacktestRecord` the research store accepts. A record with an EMPTY stamp was
written before this module existed, which places it before the exit-vocabulary
change by construction — nothing could have written an empty stamp afterwards.
Empty therefore means "pre-change", not "unknown".

When to bump it
---------------
Bump :data:`ENGINE_VERSION` when a change makes stored results incomparable with
new ones: a change to how exits are decided, to how fills are priced, to the
cost decomposition, or to what a ``Trade`` row means. Do NOT bump it for a
refactor that leaves the ledger byte-identical — ``tests/golden`` and the
backtest/paper parity suite are how you know which kind you made.

Bumping it does not delete anything. :meth:`fiboki.research.artefacts.
ResearchStore.sweep_superseded_backtests` appends a supersession note against
every record carrying an older stamp; the records stay, because a result that
was quoted in a decision has to remain readable alongside the reason it should
not have been.
"""
from __future__ import annotations

__all__ = ["ENGINE_VERSION", "ENGINE_VERSION_HISTORY", "is_current", "supersession_reason"]


#: The current engine generation. Every stored backtest is stamped with it.
ENGINE_VERSION = "engine_v2_exit_vocabulary"

#: Every generation, oldest first, with what changed. An empty string is the
#: unstamped generation: everything written before the stamp existed.
ENGINE_VERSION_HISTORY: tuple[tuple[str, str], ...] = (
    (
        "",
        "Unstamped. Stop and FIRST take-profit only: no trailing stop, no "
        "breakeven, no time stop, no cooldown, no reversal, no event blackout, "
        "and per-leg take-profit allocations discarded.",
    ),
    (
        ENGINE_VERSION,
        "Full DSL exit vocabulary: multi-leg partial take-profits with "
        "allocations, trailing stops with activate_after_r, breakeven, time "
        "stops, cooldown, reversal and event blackouts. Shared with the paper "
        "broker through backtest/position.py, so paper and backtest decide "
        "exits with one implementation.",
    ),
)


def is_current(engine_version: str | None) -> bool:
    """Was this result produced by the engine generation running now?"""
    return (engine_version or "") == ENGINE_VERSION


def supersession_reason(engine_version: str | None) -> str:
    """Why a record with this stamp must not be quoted against a current one."""
    found = engine_version or ""
    described = dict(ENGINE_VERSION_HISTORY).get(
        found, "an engine generation this build has no record of"
    )
    return (
        f"Produced by engine generation {found or '(unstamped)'!s}: {described} "
        f"The engine now in use is {ENGINE_VERSION}. The two are not comparable, "
        "so this record is superseded rather than corrected: it is still the "
        "honest output of the run that produced it, and any decision taken on it "
        "needs re-deciding against a current run."
    )
