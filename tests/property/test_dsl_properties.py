"""Property tests over the Strategy DSL.

Generated documents, not hand-picked ones: whatever the search process invents,
it must compile, compile deterministically, derive a warmup consistent with its
indicators, and evaluate without reading the future.
"""
from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from synthetic_prices import synthetic_ohlcv

from fiboki.core.enums import Timeframe
from fiboki.indicators.base import _corrupt_future
from fiboki.strategy import (
    AllOfRule,
    AnyOfRule,
    ConstantOperand,
    CrossoverRule,
    IndicatorOperand,
    IndicatorSpec,
    IndicatorVsIndicatorRule,
    IndicatorVsPriceRule,
    NotRule,
    PositionManagement,
    PriceOperand,
    RegimeGateRule,
    RuleSet,
    SessionWindowRule,
    StopModel,
    StrategyDocument,
    StrategyFamily,
    TakeProfitLeg,
    ThresholdRule,
    TradeDirection,
    compile_strategy,
)

SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

HYP = (
    "Property-test document. Mechanism: generic momentum/reversal expression "
    "assembled by a search process. Evidence against: moving-average and "
    "oscillator rule families on FX did not survive Step-SPA correction in "
    "Coakley, Marzano and Nankervis (2016), so the prior on any such document "
    "is an edge of approximately zero before costs."
)

# Small periods so a 400-bar fixture warms every indicator up.
POOL = [
    IndicatorSpec(indicator="rsi", params={"period": 7}),
    IndicatorSpec(indicator="ema", params={"period": 10}),
    IndicatorSpec(indicator="sma", params={"period": 12}),
    IndicatorSpec(indicator="atr", params={"period": 10}),
    IndicatorSpec(indicator="adx", params={"period": 7}),
    IndicatorSpec(indicator="bollinger", params={"period": 10, "num_std": 2.0}),
    IndicatorSpec(indicator="donchian", params={"period": 10}),
    IndicatorSpec(indicator="macd", params={"fast": 4, "slow": 9, "signal": 3}),
    IndicatorSpec(
        indicator="ichimoku",
        params={
            "tenkan_period": 4,
            "kijun_period": 8,
            "senkou_b_period": 12,
            "senkou_shift": 5,
            "chikou_shift": 6,
        },
    ),
    IndicatorSpec(indicator="realised_volatility", params={"period": 10}),
]
ATR_OP = IndicatorOperand(spec=IndicatorSpec(indicator="atr", params={"period": 10}))
COMPARATORS = st.sampled_from([">", ">=", "<", "<="])


@st.composite
def indicator_operands(draw) -> IndicatorOperand:
    spec = draw(st.sampled_from(POOL))
    output = draw(st.sampled_from(spec.build().output_columns))
    return IndicatorOperand(spec=spec, output=output, offset=draw(st.integers(0, 3)))


price_operands = st.builds(
    PriceOperand,
    field=st.sampled_from(["open", "high", "low", "close"]),
    offset=st.integers(0, 3),
)
constant_operands = st.builds(
    ConstantOperand, value=st.floats(-50.0, 50.0, allow_nan=False, allow_infinity=False)
)
any_operands = st.one_of(indicator_operands(), price_operands, constant_operands)


leaf_rules = st.one_of(
    st.builds(
        ThresholdRule,
        operand=any_operands,
        comparator=COMPARATORS,
        value=st.floats(-50.0, 100.0, allow_nan=False, allow_infinity=False),
    ),
    st.builds(
        IndicatorVsIndicatorRule,
        left=indicator_operands(),
        right=indicator_operands(),
        comparator=COMPARATORS,
    ),
    st.builds(
        IndicatorVsPriceRule,
        indicator=indicator_operands(),
        price=price_operands,
        comparator=COMPARATORS,
    ),
    st.builds(
        CrossoverRule,
        fast=any_operands,
        slow=any_operands,
        direction=st.sampled_from(["above", "below"]),
    ),
    st.builds(
        RegimeGateRule,
        metric=indicator_operands(),
        min_value=st.floats(-10.0, 10.0, allow_nan=False, allow_infinity=False),
        max_value=st.floats(20.0, 90.0, allow_nan=False, allow_infinity=False),
    ),
    st.builds(
        SessionWindowRule,
        start_hour_utc=st.integers(0, 23),
        end_hour_utc=st.integers(0, 23),
    ),
)

rules = st.recursive(
    leaf_rules,
    lambda children: st.one_of(
        st.builds(AllOfRule, rules=st.lists(children, min_size=1, max_size=3).map(tuple)),
        st.builds(AnyOfRule, rules=st.lists(children, min_size=1, max_size=3).map(tuple)),
        st.builds(NotRule, rule=children),
    ),
    max_leaves=4,
)


@st.composite
def documents(draw) -> StrategyDocument:
    entry = tuple(draw(st.lists(rules, min_size=1, max_size=3)))
    regime = tuple(draw(st.lists(rules, min_size=0, max_size=2)))
    legs = draw(st.integers(1, 3))
    return StrategyDocument(
        strategy_id="prop_" + draw(st.from_regex(r"\A[a-z0-9_]{3,12}\Z", fullmatch=True)),
        name="Property Document",
        hypothesis=HYP,
        family=draw(st.sampled_from(list(StrategyFamily))),
        universe=tuple(
            draw(st.lists(st.sampled_from(["EURUSD", "GBPUSD", "XAUUSD"]),
                          min_size=1, max_size=3, unique=True))
        ),
        timeframes=tuple(
            draw(st.lists(st.sampled_from([Timeframe.H1, Timeframe.H4, Timeframe.D1]),
                          min_size=1, max_size=3, unique=True))
        ),
        direction=TradeDirection.LONG,
        regime=regime,
        entry=RuleSet(long=entry),
        stop=StopModel(
            kind="atr_multiple",
            value=draw(st.floats(0.5, 4.0, allow_nan=False, allow_infinity=False)),
            atr=ATR_OP,
        ),
        take_profits=tuple(
            TakeProfitLeg(kind="r_multiple", value=float(i + 1), allocation=1.0 / legs)
            for i in range(legs)
        ),
        position_management=PositionManagement(max_bars_in_trade=30),
    )


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(400)


# ------------------------------------------------------------- properties


@SETTINGS
@given(doc=documents())
def test_any_valid_document_compiles(doc: StrategyDocument) -> None:
    compiled = compile_strategy(doc)
    assert compiled.warmup_period > 0
    assert compiled.strategy_id == doc.strategy_id
    assert len(set(compiled.required_columns)) == len(compiled.required_columns)


@SETTINGS
@given(doc=documents())
def test_compilation_is_deterministic(doc: StrategyDocument) -> None:
    a, b = compile_strategy(doc), compile_strategy(doc)
    assert a.warmup_period == b.warmup_period
    assert [s.key for s in a.indicator_specs] == [s.key for s in b.indicator_specs]
    assert a.required_columns == b.required_columns
    assert a.content_hash == b.content_hash


@SETTINGS
@given(doc=documents())
def test_warmup_equals_the_derived_formula(doc: StrategyDocument) -> None:
    compiled = compile_strategy(doc)
    indicator_warmup = max(i.warmup_period for i in compiled.indicators)
    lookback = max((r.max_lookback() for r in doc.all_rules()), default=0)
    assert compiled.warmup_period == indicator_warmup + lookback + 1


@SETTINGS
@given(doc=documents())
def test_content_hash_survives_a_json_round_trip(doc: StrategyDocument) -> None:
    reloaded = StrategyDocument.from_json(doc.to_json())
    assert reloaded.content_hash() == doc.content_hash()
    assert reloaded.complexity_score == doc.complexity_score


@SETTINGS
@given(doc=documents(), cut=st.integers(0, 120))
def test_generated_documents_never_read_the_future(
    doc: StrategyDocument, cut: int, bars: pd.DataFrame
) -> None:
    compiled = compile_strategy(doc)
    idx = compiled.warmup_period + cut
    if idx >= len(bars) - 1:
        return
    sym = doc.universe[0]
    clean = compiled.generate_signal(compiled.prepare(bars), idx, sym, doc.timeframes[0])
    dirty_frame = compiled.prepare(_corrupt_future(bars, idx))
    dirty = compiled.generate_signal(dirty_frame, idx, sym, doc.timeframes[0])
    if clean is None or dirty is None:
        assert (clean is None) == (dirty is None)
        return
    assert clean.direction == dirty.direction
    assert clean.reference_price == pytest.approx(dirty.reference_price, abs=1e-12)
    assert clean.stop_price == pytest.approx(dirty.stop_price, abs=1e-12)
    assert clean.take_profit_prices == pytest.approx(dirty.take_profit_prices, abs=1e-12)


@SETTINGS
@given(doc=documents(), cut=st.integers(0, 120))
def test_emitted_signals_always_satisfy_the_signal_contract(
    doc: StrategyDocument, cut: int, bars: pd.DataFrame
) -> None:
    """Signal's constructor enforces stop/TP side; the compiler must never trip it."""
    compiled = compile_strategy(doc)
    idx = compiled.warmup_period + cut
    if idx >= len(bars):
        return
    sig = compiled.generate_signal(
        compiled.prepare(bars), idx, doc.universe[0], doc.timeframes[0]
    )
    if sig is None:
        return
    assert sig.stop_price < sig.reference_price  # all generated docs are long-only
    assert all(p > sig.reference_price for p in sig.take_profit_prices)
    assert sig.stop_distance > 0


@SETTINGS
@given(rule_list=st.lists(rules, min_size=2, max_size=4, unique_by=lambda r: r.model_dump_json()))
def test_rule_order_does_not_change_the_content_hash(rule_list) -> None:
    def build(order):
        return StrategyDocument(
            strategy_id="order_probe",
            name="Order Probe",
            hypothesis=HYP,
            family=StrategyFamily.HYBRID,
            universe=("EURUSD",),
            timeframes=(Timeframe.H1,),
            direction=TradeDirection.LONG,
            entry=RuleSet(long=tuple(order)),
            stop=StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP),
            position_management=PositionManagement(max_bars_in_trade=20),
        )

    assert build(rule_list).content_hash() == build(list(reversed(rule_list))).content_hash()


@SETTINGS
@given(offset=st.integers(min_value=-50, max_value=-1))
def test_future_offsets_are_unrepresentable(offset: int) -> None:
    with pytest.raises(ValueError):
        IndicatorOperand(spec=POOL[0], offset=offset)
    with pytest.raises(ValueError):
        PriceOperand(field="close", offset=offset)
