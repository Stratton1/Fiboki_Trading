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

__all__ = [
    "ENGINE_V3_REASONS",
    "ENGINE_VERSION",
    "ENGINE_VERSION_HISTORY",
    "is_current",
    "supersession_reason",
]


#: The current engine generation. Every stored backtest is stamped with it.
ENGINE_VERSION = "engine_v3_realism"

#: Why ``engine_v3_realism`` superseded ``engine_v2_exit_vocabulary``, one line
#: per change, in the order of the backend audit (F_backend_audit.md section 2).
#: Every one of these can change a stored number; none of them is a refactor.
ENGINE_V3_REASONS: tuple[str, ...] = (
    "P1-1 FCA/ESMA retail leverage by currency set (ESMA 2018/796, FCA PS19/18): "
    "AUDUSD and NZDUSD 30->20, the ten major crosses (EURGBP, EURJPY, GBPJPY, "
    "EURCHF, CADJPY, CHFJPY, GBPCAD, GBPCHF, EURCAD, CADCHF) 20->30, XAGUSD "
    "20->10, HK50 20->10; the cap binds in sizing on tight stops.",
    "P1-3 price basis: a frame labelled BID/ASK/LAST is refused; research "
    "converts BID bars to SYNTHETIC_MID with half the instrument's typical "
    "spread (bid_to_mid) and records it; an unlabelled frame is recorded as "
    "assumed_mid.",
    "P1-16 sizing fixed_fractional_v2 is the default: risk per unit is the stop "
    "distance plus the spread and two fills of expected slippage, priced by the "
    "named cost profile (the evaluator's own profile in research, IG_REALISTIC "
    "when unnamed); positions are smaller by that ratio.",
    "FixedFractionalSizer clips a requested max_leverage to the instrument's "
    "regulatory cap, as SizingPolicy.leverage_for always did.",
    "P2-7 financing is charged on business-day rollovers with a triple day "
    "(Wednesday for FX and metals, Friday for indices and energy) and nothing "
    "on Saturday or Sunday, instead of every calendar night.",
    "P2-9 minimum stop distance is per asset class in IG_REALISTIC and "
    "SEVERE_STRESS (FX unchanged; metals, indices and energy a multiple of the "
    "typical spread), so non-FX rejections at the minimum stop change.",
    "P2-13 Metrics.sharpe is computed on daily (17:00 New York) resampled "
    "equity; the bar-return figure moves to sharpe_bar_based and Lo's (2002) "
    "autocorrelation-adjusted figure is reported beside it.",
    "P3-4 the engine requires a UTC index, not merely a tz-aware one.",
)

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
        "engine_v2_exit_vocabulary",
        "Full DSL exit vocabulary: multi-leg partial take-profits with "
        "allocations, trailing stops with activate_after_r, breakeven, time "
        "stops, cooldown, reversal and event blackouts. Shared with the paper "
        "broker through backtest/position.py, so paper and backtest decide "
        "exits with one implementation. Superseded by engine_v3_realism: "
        "wrong leverage caps on 14 instruments, BID bars traded as mid, sizing "
        "that ignored costs, calendar-night financing and bar-return Sharpe.",
    ),
    (
        ENGINE_VERSION,
        "Realism corrections from the 2026-09 backend audit: "
        + " ".join(ENGINE_V3_REASONS),
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
