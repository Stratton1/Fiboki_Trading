"""The compiled strategy cannot read a bar after the one it is evaluating.

Three independent proofs:

* **Future corruption.** Replace every bar after ``idx`` with nonsense; the
  signal at ``idx`` must be identical.
* **Truncation equivalence.** Compute indicators over ``df[:idx+1]`` only; the
  signal must be identical to the one produced from the full-history frame.
  This is backtest/paper parity: the paper bot is always standing on the last
  bar and has no history after it.
* **Warmup derivation.** The warmup is a function of the document's indicators,
  not a constant. V1 hardcoded 98 for every bot.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction, Timeframe
from fiboki.indicators.base import _corrupt_future
from fiboki.strategy import (
    CompilationError,
    IndicatorOperand,
    IndicatorSpec,
    PositionManagement,
    RuleSet,
    StopModel,
    StrategyDocument,
    StrategyFamily,
    ThresholdRule,
    TradeDirection,
    compile_strategy,
)
from fiboki.strategy.primitives import CrossoverRule, IndicatorVsPriceRule, PriceOperand

SEED_DIR = Path(__file__).resolve().parents[2] / "research" / "strategies"
SEED_PATHS = sorted(SEED_DIR.glob("*.json"))
SEED_IDS = [p.stem for p in SEED_PATHS]


def seed(path: Path) -> StrategyDocument:
    """Load a seed document AT ITS DECLARED DEFAULTS.

    The shipped documents are templates: they reference the parameters they
    declare, and ``compile_strategy`` refuses a document that still holds an
    unresolved reference. Causality is a property of the compiled strategy, so
    it is tested on a binding -- the one the document itself declares. That the
    template cannot be compiled at all is asserted separately, in
    ``test_a_template_cannot_be_compiled``.
    """
    return StrategyDocument.from_json(path.read_text()).bind_defaults()


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(1200)


def _fingerprint(sig: Signal | None) -> tuple | None:
    """Everything about a signal except its random id."""
    if sig is None:
        return None
    return (
        sig.strategy_id,
        sig.instrument,
        sig.timeframe,
        sig.direction,
        sig.bar_time,
        round(sig.reference_price, 12),
        round(sig.stop_price, 12),
        tuple(round(p, 12) for p in sig.take_profit_prices),
    )


def _symbol(doc: StrategyDocument) -> str:
    return "EURUSD" if "EURUSD" in doc.universe else doc.universe[0]


# ------------------------------------------------------------- corruption


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_signals_ignore_every_future_bar(path: Path, bars: pd.DataFrame) -> None:
    doc = seed(path)
    strat = compile_strategy(doc)
    sym, tf = _symbol(doc), doc.timeframes[0]

    clean = strat.prepare(bars)
    checked = 0
    for idx in range(strat.warmup_period, len(bars) - 1, 7):
        dirty = strat.prepare(_corrupt_future(bars, idx))
        a = _fingerprint(strat.generate_signal(clean, idx, sym, tf))
        b = _fingerprint(strat.generate_signal(dirty, idx, sym, tf))
        assert a == b, f"{doc.strategy_id} bar {idx} changed when the future changed"
        checked += 1
    assert checked > 20


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_truncated_history_gives_the_same_signal(path: Path, bars: pd.DataFrame) -> None:
    """Backtest/paper parity: the last bar of a live frame behaves identically."""
    doc = seed(path)
    strat = compile_strategy(doc)
    sym, tf = _symbol(doc), doc.timeframes[0]
    full = strat.prepare(bars)

    for idx in range(strat.warmup_period, len(bars), 53):
        live_frame = bars.iloc[: idx + 1]  # exactly what a paper bot holds
        a = _fingerprint(strat.generate_signal(full, idx, sym, tf))
        b = _fingerprint(strat.generate_signal(live_frame, idx, sym, tf))
        assert a == b, f"{doc.strategy_id}: backtest and live disagree at bar {idx}"


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_repeated_evaluation_is_deterministic(path: Path, bars: pd.DataFrame) -> None:
    doc = seed(path)
    strat = compile_strategy(doc)
    sym, tf = _symbol(doc), doc.timeframes[0]
    prepared = strat.prepare(bars)
    idxs = range(strat.warmup_period, len(bars), 29)
    first = [_fingerprint(strat.generate_signal(prepared, i, sym, tf)) for i in idxs]
    second = [_fingerprint(strat.generate_signal(prepared, i, sym, tf)) for i in idxs]
    assert first == second


# ----------------------------------------------------------------- warmup


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_warmup_is_derived_from_the_indicators(path: Path) -> None:
    doc = seed(path)
    strat = compile_strategy(doc)
    expected_indicator = max(i.warmup_period for i in strat.indicators)
    expected_lookback = max(r.max_lookback() for r in doc.all_rules())
    assert strat.warmup_period == expected_indicator + expected_lookback + 1
    assert strat.warmup_period >= expected_indicator


def test_warmup_actually_varies_between_strategies() -> None:
    """V1 returned 98 for every bot. A derived warmup must differ per document."""
    warmups = {
        p.stem: compile_strategy(seed(p)).warmup_period
        for p in SEED_PATHS
    }
    assert len(set(warmups.values())) > 1, warmups
    assert warmups["macd_ema_trend_hybrid"] > warmups["fib_golden_pocket_pullback"]


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_no_signal_before_warmup(path: Path, bars: pd.DataFrame) -> None:
    doc = seed(path)
    strat = compile_strategy(doc)
    prepared = strat.prepare(bars)
    sym, tf = _symbol(doc), doc.timeframes[0]
    for idx in range(0, min(strat.warmup_period, len(bars))):
        assert strat.generate_signal(prepared, idx, sym, tf) is None


# ------------------------------------------------------------- invariants


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_every_emitted_signal_has_a_tradeable_stop(path: Path, bars: pd.DataFrame) -> None:
    doc = seed(path)
    strat = compile_strategy(doc)
    prepared = strat.prepare(bars)
    sym, tf = _symbol(doc), doc.timeframes[0]
    emitted = 0
    for idx in range(strat.warmup_period, len(bars)):
        sig = strat.generate_signal(prepared, idx, sym, tf)
        if sig is None:
            continue
        emitted += 1
        assert sig.stop_distance > 0
        if sig.direction is Direction.LONG:
            assert sig.stop_price < sig.reference_price
            assert all(p > sig.reference_price for p in sig.take_profit_prices)
        else:
            assert sig.stop_price > sig.reference_price
            assert all(p < sig.reference_price for p in sig.take_profit_prices)
        # Targets are ordered nearest-first so the engine can scale out in order.
        distances = [
            abs(p - sig.reference_price) for p in sig.take_profit_prices
        ]
        assert distances == sorted(distances)
        assert sig.bar_time == prepared.index[idx]
    assert emitted > 0, f"{doc.strategy_id} never fired on the fixture"


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_instrument_and_timeframe_are_policed(path: Path, bars: pd.DataFrame) -> None:
    doc = seed(path)
    strat = compile_strategy(doc)
    prepared = strat.prepare(bars)
    idx = strat.warmup_period + 5
    with pytest.raises(CompilationError, match="permitted universe"):
        strat.generate_signal(prepared, idx, "XAGUSD" if "XAGUSD" not in doc.universe else "HK50")
    bad_tf = next(t for t in Timeframe if t not in doc.timeframes)
    with pytest.raises(CompilationError, match="permitted timeframe"):
        strat.generate_signal(prepared, idx, _symbol(doc), bad_tf)


def test_out_of_range_index_raises(bars: pd.DataFrame) -> None:
    doc = seed(SEED_PATHS[0])
    strat = compile_strategy(doc)
    with pytest.raises(IndexError):
        strat.generate_signal(bars, len(bars))
    with pytest.raises(IndexError):
        strat.generate_signal(bars, -1)


def test_naive_timestamps_are_refused(bars: pd.DataFrame) -> None:
    doc = seed(SEED_PATHS[0])
    strat = compile_strategy(doc)
    naive = bars.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(CompilationError, match="tz-aware UTC"):
        strat.generate_signal(naive, strat.warmup_period + 1, _symbol(doc))


# ------------------------------------------------------- compiler guards


def _minimal(**overrides) -> StrategyDocument:
    hyp = (
        "A minimal document used to exercise compiler guards. It states a "
        "mechanism -- momentum continuation -- and concedes that the evidence "
        "for moving-average rules in FX did not survive Step-SPA correction in "
        "Coakley, Marzano and Nankervis (2016)."
    )
    base = {
        "strategy_id": "guard_probe",
        "name": "Guard Probe",
        "hypothesis": hyp,
        "family": StrategyFamily.MOMENTUM,
        "universe": ("EURUSD",),
        "timeframes": (Timeframe.H1,),
        "direction": TradeDirection.LONG,
        "entry": RuleSet(
            long=(
                ThresholdRule(
                    operand=IndicatorOperand(
                        spec=IndicatorSpec(indicator="rsi", params={"period": 14})
                    ),
                    comparator="<",
                    value=30.0,
                ),
            )
        ),
        "stop": StopModel(
            kind="atr_multiple",
            value=2.0,
            atr=IndicatorOperand(spec=IndicatorSpec(indicator="atr", params={"period": 14})),
        ),
        "position_management": PositionManagement(max_bars_in_trade=20),
    }
    base.update(overrides)
    return StrategyDocument(**base)


def test_column_collision_between_two_swing_lookbacks_is_caught() -> None:
    """Two SwingDetectors write the same columns; the later would win silently."""
    s2 = IndicatorSpec(indicator="swing", params={"lookback": 2})
    s7 = IndicatorSpec(indicator="swing", params={"lookback": 7})
    doc = _minimal(
        entry=RuleSet(
            long=(
                IndicatorVsPriceRule(
                    indicator=IndicatorOperand(spec=s2, output="last_swing_low"),
                    comparator="<",
                    price=PriceOperand(field="close"),
                ),
                IndicatorVsPriceRule(
                    indicator=IndicatorOperand(spec=s7, output="last_swing_high"),
                    comparator=">",
                    price=PriceOperand(field="close"),
                ),
            )
        )
    )
    with pytest.raises(CompilationError, match="both write column"):
        compile_strategy(doc)


def test_compilation_is_deterministic() -> None:
    doc = _minimal()
    a, b = compile_strategy(doc), compile_strategy(doc)
    assert a.warmup_period == b.warmup_period
    assert [s.key for s in a.indicator_specs] == [s.key for s in b.indicator_specs]
    assert a.content_hash == b.content_hash == doc.content_hash()


def test_indicator_specs_are_deduplicated_and_sorted() -> None:
    rsi = IndicatorSpec(indicator="rsi", params={"period": 14})
    doc = _minimal(
        entry=RuleSet(
            long=(
                ThresholdRule(
                    operand=IndicatorOperand(spec=rsi), comparator="<", value=30.0
                ),
                ThresholdRule(
                    operand=IndicatorOperand(spec=rsi, offset=1), comparator="<", value=35.0
                ),
            )
        )
    )
    compiled = compile_strategy(doc)
    keys = [s.key for s in compiled.indicator_specs]
    assert len(keys) == len(set(keys))
    assert keys == sorted(keys)
    # The offset=1 operand widens the warmup, not the indicator set.
    assert compiled.warmup_period == max(i.warmup_period for i in compiled.indicators) + 2


def test_crossover_needs_one_extra_bar_of_lookback() -> None:
    fast = IndicatorOperand(spec=IndicatorSpec(indicator="ema", params={"period": 5}))
    slow = IndicatorOperand(spec=IndicatorSpec(indicator="ema", params={"period": 20}))
    doc = _minimal(
        entry=RuleSet(long=(CrossoverRule(fast=fast, slow=slow, direction="above"),))
    )
    compiled = compile_strategy(doc)
    assert compiled.warmup_period == max(i.warmup_period for i in compiled.indicators) + 2


def test_contradictory_both_sided_document_emits_nothing(bars: pd.DataFrame) -> None:
    """If long and short both trigger, guessing a side is worse than abstaining."""
    rsi = IndicatorSpec(indicator="rsi", params={"period": 14})
    always = ThresholdRule(
        operand=IndicatorOperand(spec=rsi), comparator=">", value=-1.0
    )
    doc = _minimal(
        strategy_id="contradiction",
        direction=TradeDirection.BOTH,
        entry=RuleSet(long=(always,), short=(always,)),
    )
    strat = compile_strategy(doc)
    prepared = strat.prepare(bars)
    for idx in range(strat.warmup_period, len(bars), 37):
        assert strat.generate_signal(prepared, idx, "EURUSD") is None


def test_compiler_never_reads_the_chikou_display_series() -> None:
    """The V1 landmine cannot reach a compiled strategy: it is not a column."""
    for path in SEED_PATHS:
        doc = seed(path)
        strat = compile_strategy(doc)
        assert not any("chikou_span" in c for c in strat.required_columns)


def test_unprepared_frame_is_prepared_on_the_truncated_view(bars: pd.DataFrame) -> None:
    """The slow path must also see only history."""
    doc = seed(SEED_PATHS[0])
    strat = compile_strategy(doc)
    idx = strat.warmup_period + 40
    raw = _fingerprint(strat.generate_signal(bars, idx, _symbol(doc)))
    prepped = _fingerprint(strat.generate_signal(strat.prepare(bars), idx, _symbol(doc)))
    assert raw == prepped
    assert not np.isnan(bars["close"].iloc[idx])


# ---------------------------------------------------- unresolved references


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_a_template_cannot_be_compiled(path: Path) -> None:
    """The shipped template refuses to compile. That is the point of binding.

    A document holding ``{"$param": "rsi_period"}`` has no rsi period. The old
    arrangement could not express the distinction at all -- the number was a
    literal in the rule and the declared domain was decoration -- so a "sweep"
    reported values it had never run.
    """
    template = StrategyDocument.from_json(path.read_text())
    assert template.unbound_parameters(), f"{path.stem} references no parameters"
    with pytest.raises(CompilationError, match="unresolved parameter reference"):
        compile_strategy(template)
    assert compile_strategy(template.bind_defaults()).warmup_period > 0
