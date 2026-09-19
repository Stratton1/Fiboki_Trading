"""Helpers for the agent-layer tests.

A plain module rather than a conftest, so these keep working no matter what
else lands in ``tests/conftest.py``.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fiboki.agents.capabilities import CapabilityResolver
from fiboki.agents.orchestrator import ManualClock, Orchestrator, submitter_for
from fiboki.agents.research_store import ResearchStore
from fiboki.agents.tools import InMemoryBarSource, ToolContext
from fiboki.core.enums import Timeframe
from fiboki.data.schema import PriceBasis, canonical_frame
from fiboki.strategy.dsl import StrategyDocument
from fiboki.strategy.registry import StrategyRegistry

HYPOTHESIS = (
    "ECONOMIC STORY. A fast moving average crossing a slow one is the cheapest "
    "possible statement that short-horizon price is above its longer-horizon "
    "equilibrium and rising. If time-series momentum exists at this horizon then a "
    "crossover expresses it with two parameters and no curve to fit. EVIDENCE "
    "AGAINST. Moving-average rules on FX have repeatedly failed step-wise "
    "multiple-testing correction; the honest prior is an edge of approximately zero "
    "before costs and negative after spread. This document exists as a research "
    "fixture, not as a candidate."
)


def ema_crossover_document(
    strategy_id: str = "ema_cross_fixture",
    *,
    fast: int = 5,
    slow: int = 20,
    atr_period: int = 14,
    stop_multiple: float = 2.0,
    universe: tuple[str, ...] = ("EURUSD",),
    timeframes: tuple[str, ...] = ("H1",),
) -> StrategyDocument:
    """A minimal, valid, compilable document that trades often on noise."""

    def ema(period: int) -> dict[str, Any]:
        return {
            "kind": "indicator",
            "spec": {"indicator": "ema", "params": {"period": period}},
            "output": "",
            "offset": 0,
        }

    atr = {
        "kind": "indicator",
        "spec": {"indicator": "atr", "params": {"period": atr_period}},
        "output": "",
        "offset": 0,
    }
    payload: dict[str, Any] = {
        "schema_version": "2.0.0",
        "strategy_id": strategy_id,
        "name": "EMA Crossover Fixture",
        "hypothesis": HYPOTHESIS,
        "family": "trend_following",
        "universe": list(universe),
        "timeframes": list(timeframes),
        "direction": "both",
        "regime": [],
        "entry": {
            "long": [{"op": "crossover", "fast": ema(fast), "slow": ema(slow),
                      "direction": "above"}],
            "short": [{"op": "crossover", "fast": ema(fast), "slow": ema(slow),
                       "direction": "below"}],
        },
        "filters": [],
        "stop": {
            "kind": "atr_multiple",
            "value": stop_multiple,
            "atr": atr,
            "buffer_atr": 0.0,
            "min_distance_atr": 0.0,
        },
        "take_profits": [
            {"kind": "r_multiple", "value": 2.0, "allocation": 1.0,
             "atr": None, "level": None, "label": "tp1"}
        ],
        "position_management": {
            "max_concurrent_positions": 1,
            "allow_pyramiding": False,
            "max_pyramid_legs": 1,
            "allow_reversal_on_opposite_signal": False,
            "cooldown_bars_after_exit": 0,
        },
        "author": "test_fixture",
    }
    return StrategyDocument.model_validate(payload)


def trending_bars(
    *,
    instrument: str = "EURUSD",
    timeframe: Timeframe = Timeframe.H1,
    periods: int = 1500,
    start: str = "2024-01-01 00:00",
    seed: int = 7,
    drift: float = 0.00004,
) -> pd.DataFrame:
    """Synthetic bars with mild autocorrelated drift, so a crossover trades."""
    idx = pd.date_range(
        start=start, periods=periods, freq=f"{timeframe.minutes}min", tz="UTC",
        name="timestamp",
    )
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0, 0.0006, periods)
    # An AR(1) on the shock makes runs long enough for a crossover to catch one.
    for i in range(1, periods):
        shocks[i] += 0.25 * shocks[i - 1]
    close = 1.10 + np.cumsum(shocks + drift * np.sin(np.arange(periods) / 90.0))
    open_ = np.empty(periods)
    open_[0] = close[0] - shocks[0]
    open_[1:] = close[:-1]
    wick = np.abs(rng.normal(0.0, 0.0004, periods)) + 0.0001
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    frame = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.integers(100, 4000, periods)},
        index=idx,
    )
    return canonical_frame(
        frame, instrument=instrument, timeframe=timeframe, price_basis=PriceBasis.MID
    )


class Harness:
    """A fully wired, entirely offline agent environment."""

    def __init__(
        self,
        *,
        strategy: StrategyDocument | None = None,
        bars: pd.DataFrame | None = None,
        instrument: str = "EURUSD",
        timeframe: Timeframe = Timeframe.H1,
        register_handlers: bool = True,
    ) -> None:
        from fiboki.agents.audit import AuditLedger
        from fiboki.agents.jobs import register_research_handlers

        self.research = ResearchStore()
        self.strategies = StrategyRegistry()
        self.document = strategy or ema_crossover_document()
        self.strategies.register(self.document)
        self.frame = bars if bars is not None else trending_bars(
            instrument=instrument, timeframe=timeframe
        )
        self.bars = InMemoryBarSource({(instrument, timeframe.value): self.frame})
        self.clock = ManualClock()
        self.orchestrator = Orchestrator(clock=self.clock)
        if register_handlers:
            register_research_handlers(
                self.orchestrator,
                research=self.research,
                strategies=self.strategies,
                bars=self.bars,
            )
        self.resolver = CapabilityResolver()
        self.ledger = AuditLedger()
        self.context = ToolContext(
            research=self.research,
            strategies=self.strategies,
            bars=self.bars,
            submit_job=submitter_for(self.orchestrator),
            default_queue="research",
        )

    def window(self) -> tuple[str, str]:
        return str(self.frame.index[0]), str(self.frame.index[-1])


__all__ = [
    "HYPOTHESIS",
    "Harness",
    "ema_crossover_document",
    "trending_bars",
]
