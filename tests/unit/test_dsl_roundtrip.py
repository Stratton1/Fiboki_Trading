"""Strategy DSL: serialisation, validation and content hashing.

The hash is the load-bearing part. A search process will propose the same
strategy many times under different names; if the hash is not stable and not
semantic, the population fills with duplicates and every ranking is
double-counted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fiboki.core.enums import Timeframe
from fiboki.strategy import (
    IndicatorOperand,
    IndicatorSpec,
    IndicatorVsIndicatorRule,
    PositionManagement,
    RuleSet,
    StopModel,
    StrategyDocument,
    StrategyFamily,
    TakeProfitLeg,
    ThresholdRule,
    TradeDirection,
    compile_strategy,
)
from fiboki.strategy.dsl import SCHEMA_VERSION, ParameterSpec

SEED_DIR = Path(__file__).resolve().parents[2] / "research" / "strategies"

HYPOTHESIS_TEXT = (
    "A deliberately boring test hypothesis that is long enough to satisfy the "
    "schema's minimum length, states an economic mechanism (short-horizon "
    "reversal as liquidity provision), and admits that the published evidence "
    "for oscillator rules in FX is weak once multiple testing is priced in."
)

RSI14 = IndicatorSpec(indicator="rsi", params={"period": 14})
ATR14 = IndicatorSpec(indicator="atr", params={"period": 14})
EMA20 = IndicatorSpec(indicator="ema", params={"period": 20})
EMA50 = IndicatorSpec(indicator="ema", params={"period": 50})
ATR_OP = IndicatorOperand(spec=ATR14)


def make_doc(**overrides) -> StrategyDocument:
    base = {
        "strategy_id": "test_alpha",
        "name": "Test Alpha",
        "hypothesis": HYPOTHESIS_TEXT,
        "family": StrategyFamily.MEAN_REVERSION,
        "universe": ("EURUSD", "GBPUSD"),
        "timeframes": (Timeframe.H1,),
        "direction": TradeDirection.LONG,
        "entry": RuleSet(
            long=(
                ThresholdRule(
                    operand=IndicatorOperand(spec=RSI14), comparator="<", value=30.0
                ),
            )
        ),
        "stop": StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP),
        "take_profits": (TakeProfitLeg(kind="r_multiple", value=2.0, allocation=1.0),),
        "position_management": PositionManagement(max_bars_in_trade=48),
    }
    base.update(overrides)
    return StrategyDocument(**base)


# ------------------------------------------------------------- round trip


def test_json_round_trip_is_exact() -> None:
    doc = make_doc()
    again = StrategyDocument.from_json(doc.to_json())
    assert again == doc
    assert again.to_json() == doc.to_json()
    assert again.content_hash() == doc.content_hash()


@pytest.mark.parametrize("path", sorted(SEED_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_seed_documents_round_trip_and_compile(path: Path) -> None:
    doc = StrategyDocument.from_json(path.read_text())
    assert StrategyDocument.from_json(doc.to_json()).content_hash() == doc.content_hash()
    compiled = compile_strategy(doc)
    assert compiled.warmup_period > 0
    assert compiled.indicators
    # Every seed states its economic story AND the evidence against it.
    assert "EVIDENCE AGAINST" in doc.hypothesis


def test_complexity_score_is_recomputed_not_trusted() -> None:
    doc = make_doc()
    payload = json.loads(doc.to_json())
    assert "complexity_score" in payload  # emitted for greppability
    payload["complexity_score"] = 999.0  # a lie
    reloaded = StrategyDocument.model_validate(payload)
    assert reloaded.complexity_score == doc.complexity_score != 999.0


# ------------------------------------------------------------ content hash


def test_hash_ignores_naming_and_lineage() -> None:
    a = make_doc()
    b = make_doc(
        strategy_id="test_beta",
        name="Test Beta",
        hypothesis=HYPOTHESIS_TEXT + " Renamed, same rules.",
        notes="different notes",
        author="someone_else",
        parent_strategy_ids=("test_alpha",),
    )
    assert a.content_hash() == b.content_hash()
    assert a.strategy_id != b.strategy_id


def test_hash_changes_when_behaviour_changes() -> None:
    a = make_doc()
    changed = make_doc(
        entry=RuleSet(
            long=(
                ThresholdRule(
                    operand=IndicatorOperand(spec=RSI14), comparator="<", value=25.0
                ),
            )
        )
    )
    assert a.content_hash() != changed.content_hash()

    other_stop = make_doc(stop=StopModel(kind="atr_multiple", value=3.0, atr=ATR_OP))
    assert a.content_hash() != other_stop.content_hash()

    other_universe = make_doc(universe=("EURUSD",))
    assert a.content_hash() != other_universe.content_hash()


def test_hash_is_invariant_to_rule_ORDER() -> None:
    """ANDed conditions listed in a different order are the same strategy."""
    r1 = ThresholdRule(operand=IndicatorOperand(spec=RSI14), comparator="<", value=30.0)
    r2 = IndicatorVsIndicatorRule(
        left=IndicatorOperand(spec=EMA20),
        right=IndicatorOperand(spec=EMA50),
        comparator=">",
    )
    a = make_doc(entry=RuleSet(long=(r1, r2)))
    b = make_doc(entry=RuleSet(long=(r2, r1)))
    assert a.content_hash() == b.content_hash()


def test_hash_is_stable_across_processes() -> None:
    """Nothing in the hash depends on dict insertion order or object identity."""
    doc = make_doc()
    first = doc.content_hash()
    for _ in range(5):
        assert StrategyDocument.from_json(doc.to_json()).content_hash() == first
    assert len(first) == 64
    assert doc.short_hash == first[:12]


# -------------------------------------------------------------- rejection


def test_stop_is_mandatory() -> None:
    with pytest.raises(ValidationError, match="stop"):
        StrategyDocument(
            strategy_id="no_stop",
            name="No Stop",
            hypothesis=HYPOTHESIS_TEXT,
            family=StrategyFamily.MOMENTUM,
            universe=("EURUSD",),
            timeframes=(Timeframe.H1,),
            direction=TradeDirection.LONG,
            entry=RuleSet(
                long=(
                    ThresholdRule(
                        operand=IndicatorOperand(spec=RSI14), comparator="<", value=30.0
                    ),
                )
            ),
        )


def test_negative_offset_is_unrepresentable() -> None:
    """The primary structural defence against look-ahead: you cannot write it."""
    with pytest.raises(ValidationError):
        IndicatorOperand(spec=RSI14, offset=-1)


def test_take_profit_allocations_cannot_exceed_the_position() -> None:
    with pytest.raises(ValidationError, match="allocations sum"):
        make_doc(
            take_profits=(
                TakeProfitLeg(kind="r_multiple", value=1.0, allocation=0.7),
                TakeProfitLeg(kind="r_multiple", value=2.0, allocation=0.7),
            )
        )


def test_multi_leg_take_profit_is_representable() -> None:
    """V1 could only ever use targets[0]; scale-out was unexpressible."""
    doc = make_doc(
        take_profits=(
            TakeProfitLeg(kind="r_multiple", value=1.0, allocation=0.5, label="a"),
            TakeProfitLeg(kind="r_multiple", value=2.0, allocation=0.25, label="b"),
            TakeProfitLeg(kind="r_multiple", value=4.0, allocation=0.25, label="c"),
        )
    )
    assert len(doc.take_profits) == 3
    assert sum(leg.allocation for leg in doc.take_profits) == pytest.approx(1.0)


def test_unknown_instrument_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unregistered instruments"):
        make_doc(universe=("EURUSD", "NOTAPAIR"))


def test_unknown_indicator_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown indicator"):
        IndicatorSpec(indicator="quantum_oscillator", params={})


def test_bad_indicator_params_are_rejected() -> None:
    with pytest.raises(ValidationError, match="rejects params"):
        IndicatorSpec(indicator="rsi", params={"period": 1})


def test_unresolvable_output_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IndicatorOperand(spec=RSI14, output="not_a_column")


def test_schema_version_mismatch_is_rejected() -> None:
    doc = make_doc()
    payload = json.loads(doc.to_json())
    payload["schema_version"] = "1.0.0"
    with pytest.raises(ValidationError, match="migrate the document"):
        StrategyDocument.model_validate(payload)
    assert doc.schema_version == SCHEMA_VERSION


def test_direction_and_rules_must_agree() -> None:
    with pytest.raises(ValidationError, match="entry.short is empty"):
        make_doc(direction=TradeDirection.BOTH)
    with pytest.raises(ValidationError, match="excludes long"):
        make_doc(
            direction=TradeDirection.SHORT,
            entry=RuleSet(
                long=(
                    ThresholdRule(
                        operand=IndicatorOperand(spec=RSI14), comparator="<", value=30.0
                    ),
                ),
                short=(
                    ThresholdRule(
                        operand=IndicatorOperand(spec=RSI14), comparator=">", value=70.0
                    ),
                ),
            ),
        )


def test_extra_fields_are_rejected() -> None:
    payload = json.loads(make_doc().to_json())
    payload["secret_knob"] = 3
    with pytest.raises(ValidationError):
        StrategyDocument.model_validate(payload)


# ------------------------------------------------------------- parameters


def test_parameter_domains_are_explicit_and_enforced() -> None:
    with pytest.raises(ValidationError, match="needs min_value and max_value"):
        ParameterSpec(kind="int", default=14)
    with pytest.raises(ValidationError, match="outside"):
        ParameterSpec(kind="int", default=99, min_value=5, max_value=20, step=1)
    with pytest.raises(ValidationError, match="not in choices"):
        ParameterSpec(kind="choice", default="d", choices=("a", "b", "c"))

    spec = ParameterSpec(kind="int", default=14, min_value=10, max_value=14, step=2)
    assert spec.domain() == (10, 12, 14)
    assert ParameterSpec(kind="bool", default=True).domain() == (False, True)
    with pytest.raises(ValueError, match="continuous domain"):
        ParameterSpec(kind="float", default=1.0, min_value=0.0, max_value=2.0).domain()


def test_every_seed_parameter_default_sits_in_its_domain() -> None:
    for path in sorted(SEED_DIR.glob("*.json")):
        doc = StrategyDocument.from_json(path.read_text())
        assert doc.parameters, f"{doc.strategy_id} declares no sweepable parameters"
        for name, spec in doc.parameters.items():
            if spec.kind in ("int", "float"):
                assert spec.min_value <= float(spec.default) <= spec.max_value, name
