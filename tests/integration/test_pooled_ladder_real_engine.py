"""E-4's pooled candidate, wired end to end to ``backtest/engine.py``.

Three synthetic USD-quoted series (one a price-scaled copy of another, so two
sleeves trade the same moves), one seed document, the real
:class:`~fiboki.validation.engine_evaluator.EngineEvaluator` per sleeve, pooled by
:class:`~fiboki.validation.pooled.PooledEvaluator`, through the real ladder.

Pinned:

* PARITY: every sleeve's trades in a pooled evaluation are exactly the trades the
  single-series engine path produces for the same binding and window (same ledger
  hash), so pooling adds no second backtester and backtest/paper parity holds
  sleeve by sleeve (filing, falsification F4);
* the effective count reflects the copied series (DEFF well above 1);
* the ladder runs every rung on the pooled candidate, and the main stage's
  deferred holdout claims nothing.

No verdict is asserted: whether this strategy passes on synthetic bars is not a
property of the platform.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import choppy_ohlc

from fiboki.core.enums import Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import EngineEvaluator, EvaluationCache, EvaluatorConfig
from fiboki.validation.evaluation import DateWindow
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.pooled import (
    PooledEvaluator,
    PooledHoldoutRegistry,
    PooledSleeve,
    rungs_with_deferred_holdout,
)
from fiboki.validation.report import RungOutcome, Verdict

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "research" / "strategies" / "rsi_band_mean_reversion.json"
#: A WIRING bar (as test_validation_ladder_real_engine.py): synthetic hourly bars
#: of three series cannot reach the production floor, and no verdict is asserted.
TEST_GATES = GATE_SET_V2.with_overrides("test-pooled-wiring-only", min_trades=20)


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    eur = choppy_ohlc(3000, seed=11)
    gbp = eur * 1.12  # the same moves at another price level: a correlated sleeve
    aud = choppy_ohlc(3000, seed=29)
    return {"EURUSD": eur, "GBPUSD": gbp, "AUDUSD": aud}


@pytest.fixture(scope="module")
def document() -> StrategyDocument:
    return StrategyDocument.from_json(DOC.read_text())


def _engine(document, symbol: str, frame: pd.DataFrame) -> EngineEvaluator:
    return EngineEvaluator(
        document=document, frame=frame, dataset_version_id=f"synthetic_{symbol}_h1",
        config=EvaluatorConfig(instrument=symbol, timeframe=Timeframe.H1, account_ccy="USD",
                               initial_balance=100_000.0, min_window_bars=20),
        fx=IdentityFxSource(), cache=EvaluationCache(),
    )


@pytest.fixture(scope="module")
def pool(document, frames):
    engines = {s: _engine(document, s, f) for s, f in frames.items()}
    pooled = PooledEvaluator(tuple(
        PooledSleeve(s, engines[s], frames[s].index, engines[s].dataset_version_id) for s in sorted(frames)
    ))
    return pooled, engines


def test_every_sleeve_trades_exactly_as_the_single_series_path(document, frames, pool) -> None:
    pooled, _ = pool
    index = frames["EURUSD"].index
    window = DateWindow("w", index[200], index[2500])
    params = document.default_values()
    ev = pooled(params, window)
    total = 0
    for sleeve in ev.meta["sleeves"]:
        fresh = _engine(document, sleeve["instrument"], frames[sleeve["instrument"]])(params, window)
        assert sleeve["ledger_sha256"] == fresh.meta["ledger_sha256"]
        assert sleeve["n_trades"] == fresh.n_trades
        mine = [t for t in ev.trades if t.instrument == sleeve["instrument"]]
        assert [(t.entry_time, t.exit_time, t.net_pnl, t.spread_cost) for t in mine] == [
            (t.entry_time, t.exit_time, t.net_pnl, t.spread_cost) for t in sorted(
                fresh.trades, key=lambda t: (t.entry_time, t.exit_time))
        ]
        total += fresh.n_trades
    assert ev.n_trades == total > 0
    assert ev.meta["opening_equity"] == pytest.approx(300_000.0)
    # GBPUSD copies EURUSD's moves: the pool is worth fewer than its raw trades.
    assert ev.meta["design_effect"]["deff"] > 1.3
    assert ev.n_trades_effective < ev.n_trades


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_the_pooled_candidate_runs_the_ladder_and_the_deferred_holdout_claims_nothing(
    document, frames, pool,
) -> None:
    pooled, engines = pool
    inner = HoldoutRegistry.in_memory()
    for s in pooled.sleeves:
        f = frames[s.instrument]
        inner.define(s.dataset_version_id, data_start=f.index[0], data_end=f.index[-1])
    ledger = PooledHoldoutRegistry(inner, [s.dataset_version_id for s in pooled.sleeves])
    candidate = engines["EURUSD"].candidate(
        max_points=4, max_values_per_axis=2, include=("rsi_period", "rsi_ceiling"),
    )
    report = ValidationLadder(
        config=LadderConfig(min_trades=20, selection_metric="net_profit", walk_forward_folds=2,
                            stress_samples=10, spa_bootstraps=60),
        gate_set=TEST_GATES, rungs=rungs_with_deferred_holdout(),
    ).run(candidate, pooled, registry=ledger, dataset_version_id=pooled.dataset_version_id,
          actor="test:pooled-wiring")
    r0 = report.rung(0)
    assert r0.outcome is not RungOutcome.NOT_REACHED
    assert r0.metrics["design_effect"]["m_sleeves"] == 3
    assert r0.metrics["n_trades_effective"] < r0.metrics["n_trades"]
    assert report.verdict is not Verdict.PROMOTE
    assert report.dataset_version_id == pooled.dataset_version_id
    assert inner.consumptions() == []
    if report.rung(2).outcome is not RungOutcome.NOT_REACHED:
        for fold in report.rung(2).metrics["folds"]:
            assert fold["oos_trades_effective"] <= fold["oos_trades"]
