"""The seed documents' DEFAULT BINDING must reproduce their old behaviour exactly.

Rewriting the five shipped strategies to use parameter references touched every
number in them. The risk is obvious and would be invisible: a transcription slip
turns ``period=20`` into ``period=200`` and the strategy still compiles, still
trades, and still looks plausible -- the research record just quietly refers to a
different strategy from the one the hypothesis argues for.

So the pre-change documents are kept, byte for byte, in
``tests/golden/seed_documents_v0/``, and this test runs BOTH versions through the
real engine on the same bars and demands an identical trade ledger. It compares
the ledger, not a summary: a ledger hash is only equal when every fill, every
cost component and every exit reason agrees.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    FixedFractionalSizer,
    run_backtest,
)
from fiboki.core.enums import Provenance, Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import WindowedStrategyRunner
from fiboki.validation.evaluation import DateWindow

ROOT = Path(__file__).resolve().parents[2]
V0_DIR = ROOT / "tests" / "golden" / "seed_documents_v0"
CURRENT_DIR = ROOT / "research" / "strategies"
SEED_IDS = sorted(p.stem for p in V0_DIR.glob("*.json"))

#: Long enough to clear the 602-bar warmup of the MACD/EMA document and still
#: leave a few years of tradeable bars behind it.
N_BARS = 3200


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    frame = synthetic_ohlcv(N_BARS, with_volume=False)
    # Priced like gold rather than like a currency pair, so the ATR stops and
    # the pip-denominated spread model land in a realistic part of their range
    # for the instrument these documents are actually run on.
    return frame * 1000.0


def _run(document: StrategyDocument, frame: pd.DataFrame, symbol: str) -> BacktestResult:
    compiled = compile_strategy(document)
    window = DateWindow("parity", frame.index[0], frame.index[-1] + pd.Timedelta(hours=4))
    runner = WindowedStrategyRunner(compiled, symbol, frame, Timeframe.H4, window)
    config = BacktestConfig(
        initial_balance=100_000.0,
        account_ccy=get_instrument(symbol).quote,
        max_concurrent=document.position_management.max_concurrent_positions,
        max_per_instrument=document.position_management.max_concurrent_positions,
        strategy_id="parity",
        provenance=Provenance.BACKTEST,
    )
    return run_backtest(
        data={symbol: frame[["open", "high", "low", "close"]]},
        config=config,
        strategy=runner,
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=IdentityFxSource(),
    )


def _symbol(doc: StrategyDocument) -> str:
    return "XAUUSD" if "XAUUSD" in doc.universe else doc.universe[0]


@pytest.mark.parametrize("strategy_id", SEED_IDS)
def test_the_default_binding_reproduces_the_pre_binding_ledger(
    strategy_id: str, bars: pd.DataFrame
) -> None:
    before = StrategyDocument.from_json((V0_DIR / f"{strategy_id}.json").read_text())
    after = StrategyDocument.from_json(
        (CURRENT_DIR / f"{strategy_id}.json").read_text()
    ).bind_defaults()

    assert not before.unbound_parameters(), "the v0 document was a literal document"
    assert after.unbound_parameters() == ()

    symbol = _symbol(before)
    assert _symbol(after) == symbol

    old, new = _run(before, bars, symbol), _run(after, bars, symbol)
    assert new.ledger_sha256() == old.ledger_sha256(), (
        f"{strategy_id}: the default binding changed the trade ledger. "
        f"{len(old.trades)} trades before, {len(new.trades)} after."
    )
    assert new.rejections == old.rejections
    assert new.signals_seen == old.signals_seen


@pytest.mark.parametrize("strategy_id", SEED_IDS)
def test_the_default_binding_reproduces_the_warmup_and_indicator_set(
    strategy_id: str,
) -> None:
    before = compile_strategy(
        StrategyDocument.from_json((V0_DIR / f"{strategy_id}.json").read_text())
    )
    after = compile_strategy(
        StrategyDocument.from_json(
            (CURRENT_DIR / f"{strategy_id}.json").read_text()
        ).bind_defaults()
    )
    assert after.warmup_period == before.warmup_period
    assert [i.name for i in after.indicators] == [i.name for i in before.indicators]
    assert sorted(after.required_columns) == sorted(before.required_columns)


def test_at_least_one_seed_actually_trades_on_these_bars(bars: pd.DataFrame) -> None:
    """Otherwise the parity test above would be comparing two empty ledgers.

    A test that passes because nothing happened is the most expensive kind of
    green, so the fixture is held to producing real trades.
    """
    totals = {}
    for strategy_id in SEED_IDS:
        doc = StrategyDocument.from_json(
            (CURRENT_DIR / f"{strategy_id}.json").read_text()
        ).bind_defaults()
        totals[strategy_id] = len(_run(doc, bars, _symbol(doc)).trades)
    assert sum(totals.values()) > 0, totals
    assert sum(1 for v in totals.values() if v > 0) >= 3, totals
