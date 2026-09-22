"""Shared fixtures for the discovery tests.

Not in ``tests/conftest.py``: that file belongs to the data platform, and these
helpers are only needed by Phase K.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.discovery.campaign import BarSet, CellOutcome
from fiboki.discovery.hypothesis import Evidence, Hypothesis
from fiboki.strategy.dsl import StrategyDocument

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "research" / "strategies"


def seed(strategy_id: str) -> StrategyDocument:
    return StrategyDocument.from_json((SEED_DIR / f"{strategy_id}.json").read_text())


def all_seeds() -> tuple[StrategyDocument, ...]:
    return tuple(
        StrategyDocument.from_json(p.read_text()) for p in sorted(SEED_DIR.glob("*.json"))
    )


def a_hypothesis(**overrides: Any) -> Hypothesis:
    payload: dict[str, Any] = {
        "hypothesis_id": "gold_trend_persistence",
        "title": "Gold trends persist at H4 once volatility is above its floor",
        "economic_rationale": (
            "Gold is held by central banks, ETFs and macro funds whose position "
            "changes are large relative to daily turnover and are executed over "
            "days rather than minutes. Slow information diffusion plus that "
            "execution footprint means a break of a multi-day range is followed by "
            "further movement in the same direction more often than a coin flip, "
            "for as long as the flow that caused the break continues."
        ),
        "prediction": (
            "A 20-bar range breakout on XAUUSD H4, filtered to periods of "
            "above-floor realised volatility, has positive expectancy net of an "
            "IG_REALISTIC spread over a sixteen-year sample."
        ),
        "refutation": (
            "Walk-forward efficiency below 50 per cent, or a deflated Sharpe below "
            "0.95 once the whole campaign's trial count is priced in, refutes it."
        ),
        "instruments": ("XAUUSD",),
        "timeframes": (Timeframe.H4,),
        "regimes": ("realised volatility above its 20-bar floor", "trending"),
        "evidence": (
            Evidence(
                direction="for",
                claim=(
                    "Moskowitz, Ooi and Pedersen (2012) document time-series "
                    "momentum across asset classes including commodities."
                ),
                source="Journal of Financial Economics 104(2)",
                strength="moderate",
            ),
            Evidence(
                direction="against",
                claim=(
                    "Coakley, Marzano and Nankervis (2016) find technical trading "
                    "rules do not survive Step-SPA once the full rule universe is "
                    "priced in; managed-futures trend returns have also decayed "
                    "materially since 2009."
                ),
                source="Journal of Banking and Finance 70",
                strength="strong",
            ),
        ),
        "seed_strategy_ids": ("donchian_breakout_atr",),
        "author": "tests",
    }
    payload.update(overrides)
    return Hypothesis.model_validate(payload)


def synthetic_bars(
    n: int = 600, *, instrument: str = "XAUUSD", timeframe: Timeframe = Timeframe.H4
) -> pd.DataFrame:
    """A deterministic OHLC frame. Shape only -- no test asserts on its returns."""
    index = pd.date_range("2015-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series(
        [1000.0 + 5.0 * ((i * 7) % 13) - 3.0 * ((i * 3) % 11) for i in range(n)],
        index=index,
    )
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
        },
        index=index,
    )


def bar_source(
    available: dict[tuple[str, str], BarSet] | None = None,
):
    """A :class:`~fiboki.discovery.campaign.BarSource` over an explicit mapping."""
    table = available or {}

    def source(instrument: str, timeframe: Timeframe) -> BarSet | None:
        return table.get((instrument.upper(), timeframe.value))

    return source


def one_barset(
    instrument: str = "XAUUSD",
    timeframe: Timeframe = Timeframe.H4,
    version: str = "ds_test_0001",
) -> BarSet:
    return BarSet(
        instrument=instrument,
        timeframe=timeframe,
        frame=synthetic_bars(instrument=instrument, timeframe=timeframe),
        dataset_version_id=version,
    )


class RecordingValidator:
    """A :class:`~fiboki.discovery.campaign.CellValidator` that records its inputs.

    It never runs the engine. Its job is to let a test assert what the campaign
    HANDED the ladder -- which is where the external trial count either survives
    or is quietly lost.
    """

    def __init__(self, outcome_for=None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcome_for = outcome_for

    def __call__(self, **kwargs: Any) -> CellOutcome:
        self.calls.append(dict(kwargs))
        if self._outcome_for is not None:
            return self._outcome_for(**kwargs)
        return CellOutcome(error="recording validator: no engine was run")

    @property
    def external_counts(self) -> list[int]:
        return [c["ladder_config"].external_trial_count for c in self.calls]

    @property
    def cell_keys(self) -> list[str]:
        return [c["cell"].key for c in self.calls]
