"""Backtest-versus-paper reconciliation: the same binding, the same closed bars, trade for trade.

The question
------------
A paper record is evidence only if a divergence from the backtest can be
ATTRIBUTED. Without a reconciliation, "the paper bot did worse than the
backtest" cannot be told apart from "the paper runtime and the engine disagree
on what the strategy does", and only the first is information about the
market (V1 could never tell them apart: ``tests/integration/
test_paper_backtest_parity.py`` is the unit-level half of this).

What runs
---------
:func:`reconcile` takes the paper session's own ledger of record (the book's
closed trades, ``trades.jsonl``) and its journal, and re-runs the SAME bound
strategy document through :func:`fiboki.backtest.engine.run_backtest` over the
same closed bars, with the session's wiring: account currency and initial
balance, cost profile (fills, spreads, financing at 21:00 UTC), sizing rule and
risk fraction, book concurrency, the paper runtime's portfolio construction
(``validation.engine_evaluator.research_construction_policy``, byte-identical
to the runtime's by ``tests/integration/test_construction_parity.py``) and the
official calendar's event blackout when one is supplied. Signals are gated to
the paper session's window (its first and last journalled bar); the bars before
it are warmup only. Positions open at the end are left open
(``close_at_end_of_data=False``), as the paper book leaves them.

Every paper trade is matched to a backtest trade on (instrument, direction,
entry time). A matched pair is compared field by field (exit time, entry and
exit price, size, net P&L and each cost), within stated tolerances. Each
difference is a divergence with a reason:

``operator_control``  the paper bot was paused/draining/stopped when the
                      backtest's signal fired (``entries_suppressed_by_control``).
``process_down``      the paper process was down for that bar and caught it up
                      without evaluating signals (``bars_caught_up``).
``paper_refused``     the paper gateway or book refused the entry the backtest
                      took (a check the engine does not model: live spread,
                      quote age, broker health, a book precheck).
``paper_not_running`` the paper journal has no bar for the signal bar at all.
``cascade``           a field that differs only because an EARLIER divergence
                      moved the equity the size is computed from.
``slippage_draw``    the seeded slippage draw differs (keyed on a bar index the two
                      paths count from different origins); zero-mean, reported.
``sizing``, ``exit_path``, ``fill_price``, ``costs``
                      a matched trade whose size, exit, entry price or cost
                      differs, with no earlier divergence to blame.
``unexplained``       none of the above. This is the one that matters.

Known approximations (stated, not hidden)
-----------------------------------------
* The paper feed computes indicators over a SLIDING window
  (``feed.history_bars``); the engine computes over the whole frame handed to
  it. Recursive indicators (EMA, ATR's Wilder smoothing) depend on where they
  start, so a long-running session can differ by a vanishing amount; a signal
  that flips because of it is reported ``unexplained`` until the warmup is
  matched. The report records both window lengths.
* The kill-switch journal is not read here; an entry suppressed by the kill
  switch shows as ``paper_not_running``/``unexplained`` unless the caller
  supplies kill-switch intervals.
* The engine's realised-volatility input to construction is measured over its
  own equity curve, which begins at the warmup prologue (flat) rather than at
  the paper session's first bar.

Deterministic: the same inputs give byte-identical reports (no wall clock in the
comparison; ``generated_at`` is a separate field).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from fiboki.backtest.engine import (
    BacktestConfig,
    BarContext,
    FixedFractionalSizer,
    run_backtest,
)
from fiboki.backtest.exits import exit_policy_from_document
from fiboki.core.enums import Provenance, Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.sim.profiles import get_profile
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    WindowedStrategyRunner,
    frame_digest,
    research_construction_policy,
)
from fiboki.validation.evaluation import DateWindow

__all__ = [
    "MONEY_TOL",
    "PRICE_TOL",
    "SIZE_REL_TOL",
    "Divergence",
    "ReconcileInputs",
    "ReconciliationReport",
    "reconcile",
]

REPORT_SCHEMA = "fiboki.paper_reconciliation/1"
#: Same code path, same fill model: prices should agree to float noise.
PRICE_TOL = 1e-8
#: Money sums accumulate in a different order on the two paths.
MONEY_TOL = 0.005
SIZE_REL_TOL = 1e-9
#: Reasons a divergence can carry, the attributable first.
EXPLAINED = frozenset(
    {"operator_control", "process_down", "paper_refused", "paper_not_running", "cascade", "slippage_draw"}
)


@dataclass(frozen=True, slots=True)
class ReconcileInputs:
    session_id: str
    document: StrategyDocument
    timeframe: Timeframe
    instruments: tuple[str, ...]
    #: Closed mid bars per instrument, UTC, covering warmup + the session window.
    bars: Mapping[str, pd.DataFrame]
    #: The dataset version id each frame came from (provenance on the report).
    dataset_ids: Mapping[str, str]
    initial_balance: float
    account_ccy: str
    profile_name: str
    risk_fraction: float
    sizing_policy_id: str
    max_concurrent: int
    paper_trades: Sequence[Mapping[str, Any]]
    journal_events: Sequence[Mapping[str, Any]]
    wiring_sha256: str = ""
    wiring_version: str = ""
    paper_history_bars: int | None = None
    blackout: Any = None
    #: Optional (start, end) kill-switch intervals, UTC.
    kill_switch_intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]] = ()


@dataclass(frozen=True, slots=True)
class Divergence:
    kind: str  # "field" | "paper_only" | "backtest_only"
    instrument: str
    direction: str
    entry_time: str
    field: str
    paper: float | str | None
    backtest: float | str | None
    delta: float | None
    reason: str
    detail: str

    @property
    def explained(self) -> bool:
        return self.reason in EXPLAINED


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    schema: str
    session_id: str
    status: str  # "match" | "diverged" | "not_evaluated"
    status_reason: str
    window_start: str | None
    window_end: str | None
    paper_trades: int
    backtest_trades: int
    matched: int
    matched_identical: int
    paper_only: int
    backtest_only: int
    outside_window: int
    divergences: tuple[Divergence, ...]
    explained: int
    unexplained: int
    first_divergence_at: str | None
    provenance: dict[str, Any] = field(default_factory=dict)
    approximations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["divergences"] = [asdict(d) for d in self.divergences]
        out["approximations"] = list(self.approximations)
        return out

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()


def not_evaluated(session_id: str, reason: str, **provenance: Any) -> ReconciliationReport:
    return ReconciliationReport(
        schema=REPORT_SCHEMA, session_id=session_id, status="not_evaluated", status_reason=reason,
        window_start=None, window_end=None, paper_trades=0, backtest_trades=0, matched=0,
        matched_identical=0, paper_only=0, backtest_only=0, outside_window=0, divergences=(),
        explained=0, unexplained=0, first_divergence_at=None, provenance=dict(provenance),
    )


class _MultiRunner:
    """One :class:`WindowedStrategyRunner` per instrument, offered every bar."""

    def __init__(self, runners: Mapping[str, WindowedStrategyRunner]) -> None:
        self.runners = dict(runners)

    def on_bar(self, ctx: BarContext) -> Sequence[Any]:
        out: list[Any] = []
        for sym in sorted(self.runners):
            if ctx.has_bar(sym):
                out.extend(self.runners[sym].on_bar(ctx))
        return out


def _ts(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _direction(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def _trade_row(trade: Any) -> dict[str, Any]:
    if isinstance(trade, Mapping):
        return dict(trade)
    return {
        "trade_id": trade.trade_id, "instrument": trade.instrument, "direction": trade.direction,
        "size": trade.size, "entry_price": trade.entry_price, "exit_price": trade.exit_price,
        "entry_time": trade.entry_time, "exit_time": trade.exit_time,
        "exit_reason": getattr(trade.exit_reason, "value", trade.exit_reason),
        "gross_pnl": trade.gross_pnl, "spread_cost": trade.spread_cost,
        "commission": trade.commission, "slippage_cost": trade.slippage_cost,
        "financing_cost": trade.financing_cost, "net_pnl": trade.net_pnl,
    }


def _key(row: Mapping[str, Any]) -> tuple[str, str, pd.Timestamp]:
    return (str(row["instrument"]).upper(), _direction(row["direction"]), _ts(row["entry_time"]))


class _Attribution:
    """Why the paper record has no trade where the backtest has one."""

    def __init__(self, events: Sequence[Mapping[str, Any]], tf: pd.Timedelta,
                 kill_switch: Sequence[tuple[pd.Timestamp, pd.Timestamp]]) -> None:
        self.tf = tf
        self.suppressed: dict[pd.Timestamp, str] = {}
        self.caught_up: set[pd.Timestamp] = set()
        self.seen: set[pd.Timestamp] = set()
        self.refusals: list[tuple[pd.Timestamp, str]] = []
        self.kill_switch = [(_ts(a), _ts(b)) for a, b in kill_switch]
        for e in events:
            kind = e.get("kind")
            if kind == "entries_suppressed_by_control":
                for plan in e.get("plans") or []:
                    if plan.get("signal_bar_time"):
                        self.suppressed[_ts(plan["signal_bar_time"])] = str(e.get("state", ""))
            elif kind == "bar" and e.get("bar_time"):
                stamp = _ts(e["bar_time"])
                self.seen.add(stamp)
                if e.get("caught_up"):
                    self.caught_up.add(stamp)
            elif kind in ("submission_not_accepted", "entry_refused_by_book") and e.get("at"):
                self.refusals.append((_ts(e["at"]), str(e.get("reason", ""))))

    def why_missing(self, signal_bar: pd.Timestamp) -> tuple[str, str]:
        if signal_bar in self.suppressed:
            return "operator_control", f"the bot was {self.suppressed[signal_bar]} on that bar; the entry was suppressed and journalled"
        if signal_bar in self.caught_up:
            return "process_down", "the paper process was down for this bar and caught it up without evaluating signals"
        for start, end in self.kill_switch:
            if start <= signal_bar + self.tf <= end:
                return "operator_control", "the kill switch was armed when this bar closed"
        decided = signal_bar + self.tf
        for at, reason in self.refusals:
            if decided <= at < decided + self.tf:
                return "paper_refused", f"the paper runtime refused the entry: {reason}"
        if signal_bar not in self.seen:
            return "paper_not_running", "the paper journal has no bar for the signal bar"
        return "unexplained", "the paper runtime saw this bar and produced no trade"


def reconcile(inputs: ReconcileInputs) -> ReconciliationReport:
    """Re-run the binding over the same bars and compare trade for trade."""
    sid = inputs.session_id
    tf = pd.Timedelta(minutes=inputs.timeframe.minutes)
    bar_times = sorted(_ts(e["bar_time"]) for e in inputs.journal_events if e.get("kind") == "bar" and e.get("bar_time"))
    if not bar_times:
        return not_evaluated(sid, "the paper journal holds no bar: the session never processed a closed bar")
    missing = [s for s in inputs.instruments if s not in inputs.bars or inputs.bars[s].empty]
    if missing:
        return not_evaluated(sid, f"no stored bars for {missing}: the backtest cannot be run on the paper's bars")
    first, last = bar_times[0], bar_times[-1]
    coverage_end = min(_ts(inputs.bars[s].index[-1]) for s in inputs.instruments)
    coverage_start = max(_ts(inputs.bars[s].index[0]) for s in inputs.instruments)
    window_end = min(last, coverage_end)
    if window_end < first or coverage_start > first:
        return not_evaluated(
            sid,
            f"the stored bars cover {coverage_start} to {coverage_end}; the paper session ran "
            f"{first} to {last}. They must overlap from the session's first bar, with its warmup.",
        )
    window = DateWindow("paper_session", first, window_end + tf)

    document = inputs.document
    compiled = compile_strategy(document)
    policy = exit_policy_from_document(document)
    profile = get_profile(inputs.profile_name)
    runners: dict[str, WindowedStrategyRunner] = {}
    data: dict[str, pd.DataFrame] = {}
    prologue_short: dict[str, int] = {}
    for sym in inputs.instruments:
        frame = inputs.bars[sym]
        frame = frame.loc[frame.index < window.end]
        lo = int(frame.index.searchsorted(window.start, side="left"))
        if lo < compiled.warmup_period:
            prologue_short[sym] = lo
        cols = ["open", "high", "low", "close"] + (["price_basis"] if "price_basis" in frame.columns else [])
        data[sym] = frame[cols]
        runners[sym] = WindowedStrategyRunner(compiled, sym, frame[["open", "high", "low", "close"]].astype(float), inputs.timeframe, window)
    exit_series = None
    if policy.needs_series:
        exit_series = {sym: r.prepared[list(policy.needs_series)] for sym, r in runners.items()}
    config = BacktestConfig(
        initial_balance=float(inputs.initial_balance),
        account_ccy=inputs.account_ccy,
        profile=profile,
        max_concurrent=int(inputs.max_concurrent),
        max_per_instrument=document.position_management.max_concurrent_positions,
        charge_financing=True,
        financing_rollover_hour_utc=21,
        close_at_end_of_data=False,
        strategy_id=document.strategy_id,
        provenance=Provenance.BACKTEST,
        construction=research_construction_policy(),
    )
    result = run_backtest(
        data=data,
        config=config,
        strategy=_MultiRunner(runners),
        sizer=FixedFractionalSizer(
            risk_fraction=float(inputs.risk_fraction),
            policy_id=inputs.sizing_policy_id,
            cost_profile=profile,
        ),
        fx=IdentityFxSource(),
        exit_policy=policy,
        exit_series=exit_series,
        blackout=inputs.blackout,
    )

    backtest_rows = [
        _trade_row(t) for t in result.trades if window.contains(_ts(t.entry_time))
    ]
    paper_all = [_trade_row(t) for t in inputs.paper_trades]
    paper_rows = [t for t in paper_all if window.contains(_ts(t["entry_time"]))]
    outside = len(paper_all) - len(paper_rows)
    attribution = _Attribution(inputs.journal_events, tf, inputs.kill_switch_intervals)

    bt_by = {_key(r): r for r in backtest_rows}
    pp_by = {_key(r): r for r in paper_rows}
    keys = sorted(set(bt_by) | set(pp_by), key=lambda k: (k[2], k[0], k[1]))
    divergences: list[Divergence] = []
    first_at: pd.Timestamp | None = None
    matched = identical = 0

    def add(d: Divergence, at: pd.Timestamp) -> None:
        nonlocal first_at
        divergences.append(d)
        if first_at is None or at < first_at:
            first_at = at

    for key in keys:
        sym, direction, entry = key
        paper, bt = pp_by.get(key), bt_by.get(key)
        after = first_at is not None and first_at < entry
        if paper is None and bt is not None:
            signal_bar = entry - tf
            reason, detail = attribution.why_missing(signal_bar)
            if reason == "unexplained" and after:
                reason, detail = "cascade", (
                    "an earlier divergence changed the book (an open position, the equity) this "
                    "entry depended on"
                )
            add(Divergence("backtest_only", sym, direction, entry.isoformat(), "trade", None,
                           float(bt["net_pnl"]), None, reason, detail), entry)
            continue
        if bt is None and paper is not None:
            add(Divergence("paper_only", sym, direction, entry.isoformat(), "trade", float(paper["net_pnl"]),
                           None, None, "cascade" if after else "unexplained",
                           "an earlier divergence changed the book this entry depended on" if after
                           else "the paper book holds a trade the engine did not take on the same bars"), entry)
            continue
        assert paper is not None and bt is not None
        matched += 1
        diffs = _field_diffs(paper, bt, key, cascade=first_at is not None and first_at < entry)
        if diffs:
            for d in diffs:
                add(d, entry)
        else:
            identical += 1

    explained = sum(1 for d in divergences if d.explained)
    status = "match" if not divergences else "diverged"
    status_reason = (
        f"{matched} paper trade(s) matched the engine trade for trade"
        if status == "match"
        else f"{len(divergences)} divergence(s), {explained} attributed, {len(divergences) - explained} unexplained"
    )
    if prologue_short:
        status_reason += f"; PARTIAL WARMUP for {sorted(prologue_short)}"
    approximations = (
        "Paper indicators are computed over a sliding window of "
        f"{inputs.paper_history_bars or 'unknown'} bars; the engine over the whole frame "
        f"({min(len(f) for f in data.values())} bars or more). Recursive indicators can differ slightly.",
        "Kill-switch intervals " + ("were supplied." if inputs.kill_switch_intervals else "were NOT supplied; a kill-switch suppression shows as unattributed."),
        "The engine's realised volatility for construction starts at its warmup prologue, not at the paper session's first bar.",
    )
    provenance = {
        "wiring_version": inputs.wiring_version,
        "wiring_sha256": inputs.wiring_sha256,
        "account_ccy": inputs.account_ccy,
        "strategy_id": document.strategy_id,
        "strategy_content_hash": document.content_hash(),
        "dataset_ids": dict(inputs.dataset_ids),
        "frame_digests": {sym: frame_digest(f) for sym, f in sorted(data.items())},
        "engine": {k: v for k, v in config.fingerprint().items() if k != "strategy_id"},
        "sizing": {"policy_id": inputs.sizing_policy_id, "risk_fraction": inputs.risk_fraction},
        "blackout": inputs.blackout is not None,
        "prologue_short": prologue_short,
    }
    return ReconciliationReport(
        schema=REPORT_SCHEMA,
        session_id=sid,
        status=status,
        status_reason=status_reason,
        window_start=first.isoformat(),
        window_end=window_end.isoformat(),
        paper_trades=len(paper_rows),
        backtest_trades=len(backtest_rows),
        matched=matched,
        matched_identical=identical,
        paper_only=sum(1 for d in divergences if d.kind == "paper_only"),
        backtest_only=sum(1 for d in divergences if d.kind == "backtest_only"),
        outside_window=outside,
        divergences=tuple(divergences),
        explained=explained,
        unexplained=len(divergences) - explained,
        first_divergence_at=None if first_at is None else first_at.isoformat(),
        provenance=provenance,
        approximations=approximations,
    )


def _field_diffs(
    paper: Mapping[str, Any],
    bt: Mapping[str, Any],
    key: tuple[str, str, pd.Timestamp],
    *,
    cascade: bool,
) -> list[Divergence]:
    """Every field of a matched pair that differs beyond its tolerance."""
    sym, direction, entry = key
    out: list[Divergence] = []

    def why(reason: str) -> str:
        return "cascade" if cascade else reason

    def cmp(name: str, tol: float, reason: str, *, rel: bool = False) -> None:
        a, b = float(paper.get(name) or 0.0), float(bt.get(name) or 0.0)
        limit = tol * max(abs(a), abs(b), 1.0) if rel else tol
        if abs(a - b) > limit:
            out.append(Divergence("field", sym, direction, entry.isoformat(), name, a, b, a - b,
                                  why(reason), _DETAIL[reason]))

    cmp("entry_price", PRICE_TOL, "fill_price")
    cmp("size", SIZE_REL_TOL, "sizing", rel=True)
    if _ts(paper["exit_time"]) != _ts(bt["exit_time"]):
        out.append(Divergence("field", sym, direction, entry.isoformat(), "exit_time",
                              _ts(paper["exit_time"]).isoformat(), _ts(bt["exit_time"]).isoformat(),
                              None, why("exit_path"), _DETAIL["exit_path"]))
    cmp("exit_price", PRICE_TOL, "exit_path")
    for cost in ("spread_cost", "commission", "financing_cost"):
        cmp(cost, MONEY_TOL, "costs")
    # Slippage is drawn by ``rng_for(seed, bar_index, sequence)``: the paper
    # runtime counts bar_index from its first forward bar and the engine from
    # its warmup prologue, so the draws differ BY CONSTRUCTION. Reported, and
    # the net P&L difference it alone explains is attributed to it.
    d_slip = float(paper.get("slippage_cost") or 0.0) - float(bt.get("slippage_cost") or 0.0)
    if abs(d_slip) > MONEY_TOL:
        out.append(Divergence("field", sym, direction, entry.isoformat(), "slippage_cost",
                              float(paper.get("slippage_cost") or 0.0), float(bt.get("slippage_cost") or 0.0),
                              d_slip, "slippage_draw", _DETAIL["slippage_draw"]))
    d_net = float(paper.get("net_pnl") or 0.0) - float(bt.get("net_pnl") or 0.0)
    if abs(d_net) > MONEY_TOL:
        only_slippage = abs(d_net + d_slip) <= MONEY_TOL and not any(
            d.field != "slippage_cost" for d in out
        )
        out.append(Divergence("field", sym, direction, entry.isoformat(), "net_pnl",
                              float(paper.get("net_pnl") or 0.0), float(bt.get("net_pnl") or 0.0), d_net,
                              "slippage_draw" if only_slippage else why("costs"),
                              _DETAIL["slippage_draw" if only_slippage else "costs"]))
    return out


_DETAIL = {
    "fill_price": "the same bar and fill model gave a different entry price",
    "sizing": "the size differs: the equity it was computed from, or the construction weight, differs",
    "exit_path": "the position closed at a different time or price (trail series, intrabar order, financing)",
    "costs": "a cost component or the net P&L differs beyond rounding",
    "slippage_draw": (
        "slippage is drawn per bar by rng_for(seed, bar_index, sequence); the paper runtime "
        "counts bars from its first forward bar, the engine from its warmup prologue, so the "
        "draw differs by construction (zero-mean; not an execution difference)"
    ),
}
