"""The ops job runner: reconciles a paper session against the backtest engine.

A separate process (``fiboki ops jobs``), never a thread in the API. It claims
queued jobs from :class:`fiboki.ops.jobs.OpsJobQueue`, assembles the inputs of
:func:`fiboki.ops.reconcile.reconcile` from what the session persisted, runs
it and writes the report where ``GET /api/trading/sessions/{id}/reconciliation``
reads it.

Inputs, and where each comes from
---------------------------------
* the wiring: the COMMITTED wiring file whose ``version`` the session's
  summary names; its sha256 must equal the one stamped on the session's journal
  rows, or the job is ``not_evaluated`` (the file changed since the session ran,
  so "the same binding" can no longer be reconstructed honestly);
* the strategy: :func:`fiboki.entrypoints.paper_forward.load_strategy` (the
  document bound to its declared defaults, refused unless its content hash is
  the wired one);
* the bars: the data store's latest VALIDATED dataset per instrument and
  timeframe (``DataStore.read_latest``), over warmup plus the session window;
  its version id is on the report. A window the store does not cover yet is
  ``not_evaluated`` (or truncated, and the report says so);
* the session's ``trades.jsonl`` and ``journal.jsonl`` (the ledger of record
  and its events), and the kill-switch journal's activation intervals;
* the official calendar for the event blackout, as the paper book applies it.
"""
from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.durable import read_payloads
from fiboki.entrypoints.paper_forward import (
    DEFAULT_WIRING,
    PaperForwardWiring,
    WiringError,
    load_strategy,
    load_wiring,
)
from fiboki.obs.logging import get_logger
from fiboki.ops.jobs import OpsJob, OpsJobQueue
from fiboki.ops.reconcile import ReconcileInputs, ReconciliationReport, not_evaluated, reconcile

__all__ = ["find_wiring", "reconcile_session", "run_jobs"]

_log = get_logger("fiboki.entrypoints.ops_jobs")
WIRING_DIR = DEFAULT_WIRING.parent
_REPO_STRATEGIES = Path(__file__).resolve().parents[3] / "research" / "strategies"

BarsLoader = Callable[[str, Any, pd.Timestamp, pd.Timestamp], tuple[pd.DataFrame, str]]


def find_wiring(version: str, *, directory: Path = WIRING_DIR) -> PaperForwardWiring | None:
    for path in sorted(directory.glob("*.json")):
        try:
            wired = load_wiring(path)
        except WiringError:
            continue
        if wired.version == version:
            return wired
    return None


def _store_loader(settings: Any) -> BarsLoader:
    def load(symbol: str, timeframe: Any, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, str]:
        from fiboki.data.store import DataStore

        if settings.data_root is None:
            raise FileNotFoundError("no data root is configured (FIBOKI_DATA_ROOT)")
        with DataStore(settings.data_root) as store:
            frame, version = store.read_latest(symbol, timeframe, start=start, end_inclusive=end)
        return frame, version.version_id

    return load


def _kill_switch_intervals(settings: Any) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    from fiboki.risk.killswitch import FileKillSwitchJournal

    path = Path(settings.killswitch_path)
    if not path.exists():
        return []
    out: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    since: pd.Timestamp | None = None
    for event in FileKillSwitchJournal(path).events():
        if event.action == "activate" and since is None:
            since = pd.Timestamp(event.at)
        elif event.action == "deactivate" and since is not None:
            out.append((since, pd.Timestamp(event.at)))
            since = None
    if since is not None:
        out.append((since, pd.Timestamp.now(tz="UTC")))
    return out


def reconcile_session(
    settings: Any,
    session_id: str,
    *,
    bars_loader: BarsLoader | None = None,
    wiring_dir: Path = WIRING_DIR,
    strategies_dir: Path | None = None,
    calendar: Any = "official",
) -> ReconciliationReport:
    paths = getattr(settings, "paths", None)
    paper_root = Path(paths.paper_root) if paths is not None else Path(settings.state_dir) / "paper"
    summary_path = paper_root / session_id / "summary.json"
    if not summary_path.exists():
        return not_evaluated(session_id, f"no paper session {session_id!r} under {paper_root}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    version = str(summary.get("wiring_version") or "")
    if not version:
        return not_evaluated(session_id, "the session's summary names no wiring version: not a forward session")
    wired = find_wiring(version, directory=wiring_dir)
    if wired is None:
        return not_evaluated(session_id, f"no committed wiring file has version {version!r}")
    stamped = str(summary.get("wiring_sha256") or "")
    if stamped and stamped != wired.sha256:
        return not_evaluated(
            session_id,
            f"the committed wiring {wired.path.name} hashes to {wired.sha256[:12]} but the session ran "
            f"under {stamped[:12]}: the binding that traded cannot be reconstructed from today's file",
            wiring_sha256=stamped,
        )
    ledger = Path(settings.state_dir) / wired.ledger_dir
    events = [json.loads(p) for p in read_payloads(ledger / "journal.jsonl")] if (ledger / "journal.jsonl").exists() else []
    trades = [json.loads(p) for p in read_payloads(ledger / "trades.jsonl")] if (ledger / "trades.jsonl").exists() else []
    bar_times = sorted(pd.Timestamp(e["bar_time"]) for e in events if e.get("kind") == "bar" and e.get("bar_time"))
    if not bar_times:
        return not_evaluated(session_id, "the session's journal holds no bar")
    (entry,) = wired.strategies
    try:
        document = load_strategy(entry, strategies_dir or _REPO_STRATEGIES)
    except WiringError as exc:
        return not_evaluated(session_id, str(exc), wiring_sha256=wired.sha256)
    tf = pd.Timedelta(minutes=wired.timeframe.minutes)
    warm = int(wired.feed.get("history_bars", 1500))
    start = bar_times[0] - tf * (warm + 10)
    end = bar_times[-1]
    loader = bars_loader or _store_loader(settings)
    bars: dict[str, pd.DataFrame] = {}
    ids: dict[str, str] = {}
    for sym in wired.instruments:
        try:
            frame, vid = loader(sym, wired.timeframe, start, end)
        except Exception as exc:
            return not_evaluated(
                session_id, f"no stored bars for {sym} {wired.timeframe.value}: {type(exc).__name__}: {exc}",
                wiring_sha256=wired.sha256,
            )
        bars[sym], ids[sym] = frame, vid
    blackout = None
    if calendar == "official":
        try:
            from fiboki.marketstate.calendar import load_official_calendar

            blackout = load_official_calendar()
        except Exception as exc:  # reported on the report's provenance
            _log.warning("official calendar unavailable for reconciliation", extra={"error": str(exc)})
    elif calendar is not None:
        blackout = calendar
    return reconcile(
        ReconcileInputs(
            session_id=session_id,
            document=document,
            timeframe=wired.timeframe,
            instruments=wired.instruments,
            bars=bars,
            dataset_ids=ids,
            initial_balance=wired.initial_balance,
            account_ccy=wired.account_ccy,
            profile_name=wired.broker_profile,
            risk_fraction=wired.risk_fraction,
            sizing_policy_id=wired.sizing_policy_id,
            max_concurrent=wired.max_concurrent,
            paper_trades=trades,
            journal_events=events,
            wiring_sha256=wired.sha256,
            wiring_version=wired.version,
            paper_history_bars=warm,
            blackout=blackout,
            kill_switch_intervals=_kill_switch_intervals(settings),
        )
    )


def run_jobs(
    settings: Any,
    *,
    once: bool = True,
    interval_s: float = 30.0,
    max_jobs: int = 0,
    sleeper: Callable[[float], None] = time.sleep,
    **reconcile_kwargs: Any,
) -> int:
    """Claim and run queued jobs. Returns the number run. ``once`` drains the queue and stops."""
    queue = OpsJobQueue(settings.paths.ops_dir)
    runner = f"{socket.gethostname()}:{os.getpid()}"
    done = 0
    while True:
        pending = queue.pending()
        for job in pending:
            _run_one(settings, queue, job, runner, reconcile_kwargs)
            done += 1
            if max_jobs and done >= max_jobs:
                return done
        if once:
            return done
        sleeper(interval_s)


def _run_one(settings: Any, queue: OpsJobQueue, job: OpsJob, runner: str, kwargs: dict[str, Any]) -> None:
    running = queue.transition(job, "running", runner=runner)
    try:
        report = reconcile_session(settings, job.session_id, **kwargs)
    except Exception as exc:
        _log.error("reconciliation failed", extra={"job_id": job.job_id, "error": f"{type(exc).__name__}: {exc}"})
        queue.transition(running, "failed", error=f"{type(exc).__name__}: {exc}")
        return
    body = {
        "report": report.as_dict(),
        "digest": report.digest(),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "requested_by": job.requested_by,
        "reason": job.reason,
    }
    path = queue.write_result(running, body)
    queue.transition(
        running, "succeeded", result_path=str(path.name),
        detail={"status": report.status, "unexplained": report.unexplained, "divergences": len(report.divergences)},
    )
