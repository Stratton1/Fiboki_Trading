"""Where the ops reports read their inputs (Wave 3O). Read-only; nothing here decides.

* :func:`reference_backtest` -- a strategy's latest CURRENT stored backtest run
  in the research ledger (engine generation current, not superseded), its
  trades and their R multiples. The maturity report's drawdown band and cost
  expectation, the self-correlation of candidates, the risk-of-ruin
  calculator's default distribution and the portfolio weights all start here.
* :func:`session_span` -- a forward session's first and last journalled bar,
  read from its runtime journal and cached by the file's size and mtime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.durable import read_payloads

__all__ = ["ReferenceBacktest", "reference_backtest", "session_span"]


@dataclass(frozen=True, slots=True)
class ReferenceBacktest:
    strategy_id: str
    run_id: str | None
    label: str
    trades: list[dict[str, Any]] = field(default_factory=list)
    r_multiples: list[float] = field(default_factory=list)
    initial_equity: float | None = None
    risk_fraction: float | None = None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.run_id is not None and bool(self.trades)


def _contract_size(symbol: str) -> float:
    try:
        return float(instrument_registry.get(symbol).contract_size)
    except Exception:
        return 1.0


def reference_backtest(settings: Any, strategy_id: str) -> ReferenceBacktest:
    """The latest current stored backtest run of ``strategy_id``, or why there is none."""
    from fiboki.api.errors import ApiError
    from fiboki.api.routers.chart import _ledger_path
    from fiboki.api.routers.research_analytics import _list_runs, _load_run
    from fiboki.research import tearsheet as ts

    if _ledger_path(settings) is None or not Path(_ledger_path(settings)).exists():
        return ReferenceBacktest(strategy_id, None, "no research ledger",
                                 reason="No experiment ledger is configured, so no stored backtest can be read.")
    try:
        runs, _ = _list_runs(settings, strategy_id=strategy_id, limit=50)
    except ApiError as exc:
        return ReferenceBacktest(strategy_id, None, "research ledger unreadable", reason=str(exc))
    current = [r for r in runs if r.run.engine_current and not r.run.superseded and r.run.kind == "backtest"]
    if not current:
        return ReferenceBacktest(
            strategy_id, None, "no current backtest",
            reason=f"The research ledger holds no current-engine, unsuperseded backtest of {strategy_id}.",
        )
    run_id = current[0].run.run_id
    try:
        _view, record, _report = _load_run(settings, run_id)
    except ApiError as exc:
        return ReferenceBacktest(strategy_id, run_id, f"run {run_id}", reason=str(exc))
    trades = [t for t in record.get("trades") or [] if isinstance(t, dict)]
    rs: list[float] = []
    for t in trades:
        r = ts.r_multiple(t, _contract_size(str(t.get("instrument", ""))))
        if r is not None:
            rs.append(float(r))
    metrics = record.get("metrics") or {}
    cfg = record.get("config_fingerprint") or {}
    sizing = cfg.get("sizing") if isinstance(cfg.get("sizing"), dict) else {}
    initial = ts._num(metrics.get("initial_equity")) or ts._num(cfg.get("initial_balance"))
    rf = ts._num((sizing or {}).get("risk_fraction"))
    return ReferenceBacktest(
        strategy_id=strategy_id, run_id=run_id, label=f"backtest {run_id}", trades=trades,
        r_multiples=rs if len(rs) == len(trades) else [], initial_equity=initial, risk_fraction=rf,
        reason="" if trades else f"Run {run_id} recorded no trades.",
    )


_SPANS: dict[str, tuple[tuple[int, int], tuple[pd.Timestamp | None, pd.Timestamp | None]]] = {}


def session_span(ledger_dir: Path | None) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if ledger_dir is None:
        return None, None
    path = ledger_dir / "journal.jsonl"
    try:
        st = path.stat()
    except FileNotFoundError:
        return None, None
    sig = (st.st_size, st.st_mtime_ns)
    hit = _SPANS.get(str(path))
    if hit is not None and hit[0] == sig:
        return hit[1]
    first = last = None
    for payload in read_payloads(path):
        row = json.loads(payload)
        if row.get("kind") == "bar" and row.get("bar_time"):
            stamp = pd.Timestamp(row["bar_time"])
            first = stamp if first is None else min(first, stamp)
            last = stamp if last is None else max(last, stamp)
    _SPANS[str(path)] = (sig, (first, last))
    return first, last
