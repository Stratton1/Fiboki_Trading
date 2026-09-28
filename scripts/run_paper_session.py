#!/usr/bin/env python
"""Replay a real PAPER session through the assembled runtime and record it.

What this runs
--------------
``fiboki.workers.runtime.build_replay_session`` end to end:

    market data -> market state -> strategy -> sizing -> risk gateway ->
    execution service -> paper venue -> telemetry

on bars read out of a marked :class:`~fiboki.data.store.DataStore` -- the
migrated V2 store, by dataset version, not a loose parquet file. The strategy is
a REAL seed document from ``research/strategies``, compiled through
``fiboki.strategy.compiler``, so the signals are the platform's own and not a
stand-in written for this script.

Economic calendar guard
-----------------------
Before replaying, the committed official calendar
(``fiboki.marketstate.calendar.load_official_calendar``) must cover the replay's
span and the instrument's currencies, or the script refuses with
``USER_ACTION_NOTE``. ``--allow-empty-calendar`` is the explicit opt-out and is
recorded in ``summary.json``. KNOWN GAP: the calendar is CHECKED here but not
yet wired into the risk gateway (``build_replay_session`` takes no event
source), so the gateway's ``event_blackout`` check still sees no events.

PAPER ONLY. ``build_replay_session`` constructs its ``ExecutionService`` with
``ExecutionMode.PAPER`` and a ``PaperBroker``; there is no argument here that
could widen that, and none of the five live controls is touched.

Causality
---------
Indicators are computed once over the whole frame and then indexed by the
replay cursor. That is safe only because every indicator in the library is
proved causal (``tests/unit/test_indicator_causality.py``): the value on row i
is the same computed over the full frame as over ``frame[:i+1]``. The signal for
bar i is evaluated on bar i's CLOSE and acted on afterwards, which is the closed
-candle rule the platform runs under everywhere else.

What it writes
--------------
``--out``:
    ``summary.json``     bars, signals, gateway attempts, blocks by reason,
                         trades, P&L, and the four live risk inputs.
    ``telemetry.jsonl``  every gateway attempt, allowed or blocked, one per line.
    ``trades.csv``       every closed trade.
    ``positions.csv``    positions still open when the replay ended.
``--store-db``:
    the ``WorkerStore`` SQLite the session used (leases, intents, submissions).

Usage::

    python scripts/run_paper_session.py \\
        --data-root var/datastore --instrument XAUUSD --timeframe H4 \\
        --strategy donchian_breakout_atr
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:  # pragma: no cover - script bootstrap
    sys.path.insert(0, str(REPO / "src"))

from fiboki.broker.paper import PaperConfig  # noqa: E402
from fiboki.core.contracts import Signal  # noqa: E402
from fiboki.core.enums import Timeframe  # noqa: E402
from fiboki.core.money import IdentityFxSource  # noqa: E402
from fiboki.data.schema import DatasetKind  # noqa: E402
from fiboki.data.store import DataStore  # noqa: E402
from fiboki.marketstate.calendar import (  # noqa: E402
    CalendarError,
    instrument_currencies,
    load_official_calendar,
)
from fiboki.portfolio.sizing import SizingPolicy  # noqa: E402
from fiboki.risk.limits import PAPER_LIMITS  # noqa: E402
from fiboki.sim.profiles import IG_REALISTIC  # noqa: E402
from fiboki.strategy.compiler import compile_strategy  # noqa: E402
from fiboki.strategy.dsl import StrategyDocument  # noqa: E402
from fiboki.workers.base import WorkerStore  # noqa: E402
from fiboki.workers.live_worker import LiveWorkerConfig  # noqa: E402
from fiboki.workers.runtime import build_replay_session  # noqa: E402


class DocumentSignalSource:
    """A compiled seed document, driven by the replay cursor.

    The runtime hands a signal source ``(symbol, history, now)`` where
    ``history`` is the raw OHLC seen so far. The compiled strategy wants an
    indexed frame WITH its indicators, so the indicators are prepared once over
    the whole frame here and the bar is located by ``len(history) - 1``. The
    timestamp is asserted against the prepared frame so a cursor/frame mismatch
    fails loudly instead of silently signalling on the wrong bar.
    """

    def __init__(
        self, document: StrategyDocument, frame: pd.DataFrame, timeframe: Timeframe
    ) -> None:
        # bind_defaults(), never a sweep: this session reports how the document
        # AS WRITTEN behaves. Picking parameters here would make the run a
        # single draw from an unreported search.
        self.document = document.bind_defaults()
        self.compiled = compile_strategy(self.document)
        self.prepared = self.compiled.prepare(frame)
        self.timeframe = timeframe
        self.signals_emitted = 0
        self.evaluations = 0

    @property
    def warmup_period(self) -> int:
        return int(self.compiled.warmup_period)

    def __call__(
        self, symbol: str, history: pd.DataFrame, now: pd.Timestamp
    ) -> list[Signal]:
        idx = len(history) - 1
        if idx < self.warmup_period or idx >= len(self.prepared):
            return []
        if self.prepared.index[idx] != now:
            raise AssertionError(
                f"replay cursor and prepared frame disagree at {idx}: "
                f"{self.prepared.index[idx]} vs {now}"
            )
        self.evaluations += 1
        signal = self.compiled.generate_signal(
            self.prepared, idx, instrument=symbol, timeframe=self.timeframe
        )
        if signal is None:
            return []
        self.signals_emitted += 1
        return [signal]


def _rowify(obj: Any) -> dict[str, Any]:
    """One record as a flat dict, whatever kind of object the venue returns."""
    if isinstance(obj, dict):
        source = obj
    elif is_dataclass(obj) and not isinstance(obj, type):
        source = asdict(obj)
    elif hasattr(obj, "model_dump"):
        source = obj.model_dump(mode="json")
    elif hasattr(obj, "__dict__"):
        source = dict(vars(obj))
    elif hasattr(obj, "_asdict"):
        source = obj._asdict()
    else:  # pragma: no cover - defensive
        source = {"value": obj}
    out: dict[str, Any] = {}
    for key, value in source.items():
        if key.startswith("_"):
            continue
        if hasattr(value, "value") and not isinstance(value, (str, int, float)):
            out[key] = value.value
        elif isinstance(value, (list, tuple, dict, set)):
            out[key] = json.dumps(value, default=str)
        elif isinstance(value, pd.Timestamp):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _write_csv(path: Path, records: list[Any]) -> int:
    rows = [_rowify(r) for r in records]
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def _calendar_guard(symbol: str, ohlc: pd.DataFrame, *, allow_empty: bool) -> dict[str, Any]:
    """Refuse to replay blind through scheduled events unless explicitly allowed."""
    calendar = load_official_calendar()
    cov = calendar.coverage()
    record: dict[str, Any] = {
        "source": "fiboki.marketstate.calendar.load_official_calendar",
        "declared_start": str(cov.declared_start),
        "declared_end": str(cov.declared_end),
        "currencies": list(cov.currencies),
        "replay_start": ohlc.index[0].isoformat(),
        "replay_end": ohlc.index[-1].isoformat(),
        "allow_empty_calendar": allow_empty,
        "wired_into_gateway": False,
    }
    try:
        calendar.assert_populated(
            start=ohlc.index[0], end=ohlc.index[-1], currencies=instrument_currencies(symbol)
        )
        record["covered"] = True
    except CalendarError as exc:
        record["covered"] = False
        if not allow_empty:
            raise SystemExit(
                f"refusing to replay {symbol}: {exc}\n"
                "Pass --allow-empty-calendar to replay anyway (recorded in summary.json)."
            ) from exc
        print(f"  WARNING calendar does not cover this replay: {str(exc).splitlines()[0]}")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=REPO / "var" / "datastore")
    parser.add_argument("--instrument", default="XAUUSD")
    parser.add_argument("--timeframe", default="H4")
    parser.add_argument("--strategy", default="donchian_breakout_atr")
    parser.add_argument(
        "--bars",
        type=int,
        default=0,
        help="Replay only the first N bars. 0 (default) replays the whole dataset.",
    )
    parser.add_argument("--initial-balance", type=float, default=100_000.0)
    parser.add_argument("--account-ccy", default="USD")
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--max-concurrent", type=int, default=2)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "research" / "reports" / "paper_session_xauusd_h4",
    )
    parser.add_argument("--store-db", type=Path, default=REPO / "var" / "paper" / "session.sqlite")
    parser.add_argument(
        "--allow-empty-calendar",
        action="store_true",
        help=(
            "Replay even when the official economic calendar does not cover the "
            "replay span or the instrument's currencies. Recorded in summary.json."
        ),
    )
    args = parser.parse_args(argv)

    timeframe = Timeframe(args.timeframe.upper())
    symbol = args.instrument.upper()

    store = DataStore(args.data_root)
    frame, version = store.read_latest(symbol, timeframe, kind=DatasetKind.VALIDATED)
    store.close()
    ohlc = frame[["open", "high", "low", "close"]].astype(float)
    if args.bars:
        ohlc = ohlc.iloc[: args.bars]

    args.calendar_guard = _calendar_guard(symbol, ohlc, allow_empty=args.allow_empty_calendar)

    document = StrategyDocument.from_json(
        (REPO / "research" / "strategies" / f"{args.strategy}.json").read_text(
            encoding="utf-8"
        )
    )
    source = DocumentSignalSource(document, ohlc, timeframe)

    args.out.mkdir(parents=True, exist_ok=True)
    args.store_db.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"replaying {len(ohlc):,} {symbol} {timeframe.value} bars "
        f"{ohlc.index[0]} .. {ohlc.index[-1]}"
    )
    print(f"  dataset  {version.version_id}")
    print(f"  strategy {document.strategy_id} (warmup {source.warmup_period})")

    with WorkerStore.sqlite_at(args.store_db) as handle:
        session = build_replay_session(
            frames={symbol: ohlc},
            signal_source=source,
            store=handle,
            paper_config=PaperConfig(
                initial_balance=args.initial_balance,
                account_ccy=args.account_ccy,
                profile=IG_REALISTIC,
                max_concurrent=args.max_concurrent,
                max_per_instrument=1,
                strategy_id=document.strategy_id,
            ),
            sizing_policy=SizingPolicy(risk_fraction=args.risk_fraction),
            fx=IdentityFxSource(),
            timeframe=timeframe.value,
            warmup=source.warmup_period,
            limits=PAPER_LIMITS,
            worker_config=LiveWorkerConfig(
                max_cycles=len(ohlc),
                idle_sleep_seconds=0.0,
                busy_sleep_seconds=0.0,
                reconcile_every_cycles=500,
                lifecycle_every_cycles=500,
                lease_name=f"paper-session-{symbol}-{timeframe.value}",
            ),
        )
        exit_code = session.worker.run(install_signals=False)
        summary = session.summary()
        telemetry = session.telemetry()
        trades = list(session.broker.trades)
        open_positions = list(session.broker.book.open)
        exit_legs = list(session.broker.exit_legs)

    return _report(args, session, summary, telemetry, trades, exit_legs,
                   open_positions, document, version, ohlc, source, exit_code)


def _report(
    args: Any,
    session: Any,
    summary: dict[str, Any],
    telemetry: list[dict[str, Any]],
    trades: list[Any],
    exit_legs: list[Any],
    open_positions: list[Any],
    document: StrategyDocument,
    version: Any,
    ohlc: pd.DataFrame,
    source: DocumentSignalSource,
    exit_code: int,
) -> int:
    """Write the four artefacts and print the numbers."""
    with (args.out / "telemetry.jsonl").open("w", encoding="utf-8") as handle:
        for attempt in telemetry:
            handle.write(json.dumps(attempt, default=str) + "\n")
    n_trades = _write_csv(args.out / "trades.csv", trades)
    _write_csv(args.out / "exit_legs.csv", exit_legs)
    n_open = _write_csv(args.out / "positions.csv", open_positions)

    def _total(field: str) -> float:
        return sum(float(getattr(t, field, 0.0) or 0.0) for t in trades)

    # The venue records costs per component, not as one number. Summing a
    # non-existent ``costs`` attribute would have reported zero cost on a run
    # that paid spread, slippage and financing.
    cost_fields = ("spread_cost", "commission", "slippage_cost", "financing_cost")
    cost_breakdown = {f: round(_total(f), 2) for f in cost_fields}
    gross = _total("gross_pnl")
    costs = sum(cost_breakdown.values())
    net = _total("net_pnl")
    wins = sum(1 for t in trades if float(getattr(t, "net_pnl", 0.0) or 0.0) > 0)

    payload = {
        "provenance": "paper",
        "execution_mode": str(getattr(session.execution, "mode", "")),
        "note": (
            "A PAPER replay through the assembled runtime. Every figure here is "
            "measured from this run; none is a fixture. The realism limits the "
            "platform declares still apply: spreads come from the IG_REALISTIC "
            "profile's STATIC typical values rather than from quoted spreads, "
            "and FX conversion is a static rate -- here an identity source, "
            "because the account currency and the instrument's quote currency "
            "are the same, so this run exercises no conversion at all. The cost "
            "components actually charged are broken out below rather than "
            "asserted: read cost_breakdown, not this sentence."
        ),
        "cost_breakdown": cost_breakdown,
        "instrument": args.instrument.upper(),
        "timeframe": args.timeframe.upper(),
        "dataset_version_id": version.version_id,
        "dataset_first_bar": ohlc.index[0].isoformat(),
        "dataset_last_bar": ohlc.index[-1].isoformat(),
        "strategy_id": document.strategy_id,
        "strategy_template_content_hash": document.content_hash(),
        "strategy_bound_content_hash": source.document.content_hash(),
        "parameters": "declared defaults (document.bind_defaults()); no sweep",
        "warmup_period": source.warmup_period,
        "broker_profile": "IG_REALISTIC",
        "initial_balance": args.initial_balance,
        "account_ccy": args.account_ccy,
        "risk_fraction": args.risk_fraction,
        "worker_exit_code": exit_code,
        "signal_evaluations": source.evaluations,
        "signals_emitted_by_strategy": source.signals_emitted,
        "gross_pnl": round(gross, 2),
        "costs": round(costs, 2),
        "net_pnl": round(net, 2),
        "wins": wins,
        "losses": n_trades - wins,
        "win_rate_pct": round(100.0 * wins / n_trades, 2) if n_trades else None,
        "open_positions_at_end": n_open,
        "economic_calendar": getattr(args, "calendar_guard", None),
        **summary,
    }
    (args.out / "summary.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    print()
    for key in (
        "bars_replayed",
        "signals_seen",
        "unsized",
        "gateway_attempts",
        "gateway_blocked",
        "submissions",
        "accepted",
        "trades",
        "exit_legs",
        "open_positions",
        "balance",
        "equity",
    ):
        print(f"  {key:22s} {payload.get(key)}")
    print(f"  {'net_pnl':22s} {payload['net_pnl']}  (gross {payload['gross_pnl']}, costs {payload['costs']})")
    print(f"  {'win_rate_pct':22s} {payload['win_rate_pct']}")
    print("  block_reasons:")
    for reason, count in (summary.get("block_reasons") or {}).items():
        print(f"      {reason:34s} {count}")
    print("  rejections:")
    for reason, count in (summary.get("rejections") or {}).items():
        print(f"      {reason:34s} {count}")
    print("  risk_inputs_live:", summary.get("risk_inputs_live"))
    print()
    print(f"  -> {args.out}")
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
