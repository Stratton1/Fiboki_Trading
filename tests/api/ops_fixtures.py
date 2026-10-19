"""Shared builders for the Wave 3O API tests: stored backtests and forward sessions."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fiboki.backtest.version import ENGINE_VERSION
from fiboki.research.artefacts import BacktestRecord, ResearchStore
from fiboki.research.experiment import ExperimentLedger

TRADE_FIELDS = (
    "trade_id", "strategy_id", "instrument", "direction", "size", "entry_price", "exit_price",
    "entry_time", "exit_time", "exit_reason", "gross_pnl", "spread_cost", "commission",
    "slippage_cost", "financing_cost", "net_pnl", "account_ccy", "provenance", "initial_stop",
)


def synthetic_trades(
    *, seed: int, n: int, strategy_id: str, start: str = "2024-01-02", instrument: str = "EURUSD",
    pnl: np.ndarray | None = None, provenance: str = "backtest", size: float = 10_000.0,
    cost: float = 1.2,
) -> list[dict[str, Any]]:
    """One trade per weekday; R = net / (stop distance * size)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n, tz="UTC")
    values = pnl if pnl is not None else rng.normal(5.0, 40.0, n)
    out = []
    for i, (day, net) in enumerate(zip(days, values, strict=True)):
        entry = 1.10
        stop = entry - 0.0050
        out.append({
            "trade_id": f"{strategy_id}-{i}", "strategy_id": strategy_id, "instrument": instrument,
            "direction": "long", "size": size, "entry_price": entry, "exit_price": entry + float(net) / size,
            "entry_time": (day + pd.Timedelta(hours=2)).isoformat(),
            "exit_time": (day + pd.Timedelta(hours=10)).isoformat(),
            "exit_reason": "stop_loss" if net < 0 else "take_profit",
            "gross_pnl": float(net) + cost, "spread_cost": cost, "commission": 0.0, "slippage_cost": 0.0,
            "financing_cost": 0.0, "net_pnl": float(net), "account_ccy": "USD", "provenance": provenance,
            "initial_stop": stop, "fx_rate_used": 1.0,
        })
    return out


def add_backtest(db: Path, strategy_id: str, trades: list[dict[str, Any]], *, initial: float = 10_000.0,
                 risk_fraction: float = 0.01) -> str:
    with ResearchStore(ledger=ExperimentLedger(db)) as store:
        rec = store.add_backtest(BacktestRecord(
            strategy_id=strategy_id, instruments=("EURUSD",), timeframe="H4", engine_version=ENGINE_VERSION,
            config_fingerprint={"initial_balance": initial, "account_ccy": "USD",
                                "sizing": {"risk_fraction": risk_fraction, "policy_id": "fixed_fractional_v2"}},
            metrics={"initial_equity": initial}, trades=tuple(trades), label="backtest",
        ))
    return rec.backtest_id


def write_forward_session(root: Path, name: str, strategy_id: str, trades: list[dict[str, Any]], *,
                          initial: float = 100_000.0, extra: dict[str, Any] | None = None) -> str:
    d = root / f"forward-{name}"
    d.mkdir(parents=True, exist_ok=True)
    net = sum(t["net_pnl"] for t in trades)
    last = max((t["exit_time"] for t in trades), default=pd.Timestamp.now(tz="UTC").isoformat())
    (d / "summary.json").write_text(json.dumps({
        "provenance": "paper", "source": "fiboki paper forward", "wiring_version": name,
        "strategy_id": strategy_id, "instrument": "EURUSD", "timeframe": "H4", "account_ccy": "USD",
        "initial_balance": initial, "balance": initial + net, "equity": initial + net,
        "dataset_last_bar": last, "written_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "trades": len(trades), "open_positions": 0, **(extra or {}),
    }))
    with (d / "trades.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(TRADE_FIELDS), extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            writer.writerow({**t, "provenance": "paper", "strategy_id": strategy_id})
    (d / "positions.csv").write_text("")
    return f"forward-{name}"
