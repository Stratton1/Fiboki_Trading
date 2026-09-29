"""Shared fixtures for the entry-lock tests: a lock-declaring document and bars.

The document is deliberately simple and deliberately bad -- an EMA crossover
with a tight ATR stop on a random walk -- because what the lock tests need is a
steady supply of stop-outs, take-profits and time exits, not an edge.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fiboki.strategy.dsl import StrategyDocument

HYPOTHESIS = (
    "TEST FIXTURE, NOT A STRATEGY. An EMA 5/20 crossover with a one-ATR stop is "
    "used here only because on a random walk it produces a steady mixture of "
    "stop-outs, take-profits and time exits, which is what exercising entry "
    "locks requires. Evidence against: every published study of short moving-"
    "average crossovers in FX after costs; there is no edge here and none is "
    "claimed."
)


def _ema(period: int, offset: int = 0) -> dict[str, Any]:
    return {
        "kind": "indicator",
        "spec": {"indicator": "ema", "params": {"period": period}},
        "output": "",
        "offset": offset,
    }


def _atr() -> dict[str, Any]:
    return {
        "kind": "indicator",
        "spec": {"indicator": "atr", "params": {"period": 14}},
        "output": "",
        "offset": 0,
    }


def lock_document_payload(
    locks: dict[str, Any] | None = None,
    *,
    strategy_id: str = "lock_fixture_ema",
    universe: tuple[str, ...] = ("EURUSD",),
    timeframes: tuple[str, ...] = ("H1",),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "2.0.0",
        "strategy_id": strategy_id,
        "name": "Lock fixture EMA crossover",
        "hypothesis": HYPOTHESIS,
        "family": "trend_following",
        "universe": list(universe),
        "timeframes": list(timeframes),
        "direction": "both",
        "entry": {
            "long": [
                {"op": "crossover", "fast": _ema(5), "slow": _ema(20), "direction": "above"}
            ],
            "short": [
                {"op": "crossover", "fast": _ema(5), "slow": _ema(20), "direction": "below"}
            ],
        },
        "stop": {"kind": "atr_multiple", "value": 1.0, "atr": _atr()},
        "take_profits": [
            {"kind": "r_multiple", "value": 1.5, "allocation": 1.0, "label": "tp"}
        ],
        "position_management": {"max_bars_in_trade": 30},
        "events": {"avoid_rollover_hour": False},
    }
    if locks is not None:
        payload["locks"] = locks
    return payload


def lock_document(locks: dict[str, Any] | None = None, **kwargs: Any) -> StrategyDocument:
    return StrategyDocument.model_validate(lock_document_payload(locks, **kwargs))


def session_frame(n: int = 2400, seed: int = 11, start: str = "2024-01-01") -> pd.DataFrame:
    """H1 EURUSD-like bars WITHOUT the interbank weekend.

    Friday 22:00 to Sunday 22:00 UTC is removed, as a real FX feed would have
    it, so the lock arithmetic is tested against the calendar it will meet.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=int(n * 1.5), freq="h", tz="UTC")
    dow, hour = idx.dayofweek, idx.hour
    weekend = (dow == 5) | ((dow == 4) & (hour >= 22)) | ((dow == 6) & (hour < 22))
    idx = idx[~weekend][:n]
    close = 1.10 + np.cumsum(rng.normal(0, 0.0009, len(idx)))
    high = close + np.abs(rng.normal(0, 0.0005, len(idx)))
    low = close - np.abs(rng.normal(0, 0.0005, len(idx)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    open_ = np.clip(open_, low, high)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)
