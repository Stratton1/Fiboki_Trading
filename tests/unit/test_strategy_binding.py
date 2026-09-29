"""Parameter binding: from a declared domain to a runnable strategy.

Before this existed, ``StrategyDocument.parameters`` declared sweep domains and
nothing in the system connected them to the numbers the rules actually used. A
"sweep" over ``rsi_period`` could therefore report five results that had all run
the same RSI period, and nothing anywhere would notice. These tests pin the four
properties that make the connection trustworthy:

1. a reference resolves to exactly the value it was given, everywhere it occurs;
2. an unresolved reference cannot reach the engine;
3. a value outside the declared domain is refused;
4. two bindings of one template are two strategies, by content hash -- which is
   what the holdout registry and the experiment ledger key on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fiboki.core.enums import Timeframe
from fiboki.strategy import (
    CompilationError,
    IndicatorOperand,
    IndicatorSpec,
    OutOfDomainError,
    ParameterSpec,
    ParamRef,
    PositionManagement,
    RegimeGateRule,
    RuleSet,
    StopModel,
    StrategyDocument,
    StrategyFamily,
    TakeProfitLeg,
    ThresholdRule,
    TradeDirection,
    UnboundParameterError,
    UnknownParameterError,
    compile_strategy,
)

SEED_DIR = Path(__file__).resolve().parents[2] / "research" / "strategies"
SEED_PATHS = sorted(SEED_DIR.glob("*.json"))
SEED_IDS = [p.stem for p in SEED_PATHS]

HYPOTHESIS = (
    "A test hypothesis long enough for the schema. Mechanism: short-horizon "
    "reversal as liquidity provision (Nagel 2012). EVIDENCE AGAINST: oscillator "
    "rules on FX did not survive Step-SPA correction in Coakley, Marzano and "
    "Nankervis (2016), so the honest prior is an edge near zero after spread."
)

ATR_OP = IndicatorOperand(spec=IndicatorSpec(indicator="atr", params={"period": 14}))
ADX_OP = IndicatorOperand(spec=IndicatorSpec(indicator="adx", params={"period": 14}))


def template(**overrides) -> StrategyDocument:
    """A document whose numbers are ALL references, in four different positions.

    An indicator parameter, a rule threshold, a regime bound and a stop multiple
    -- so the walk that finds references is exercised at every depth it has to
    handle, not just the easy one.
    """
    base = {
        "strategy_id": "bindable_probe",
        "name": "Bindable Probe",
        "hypothesis": HYPOTHESIS,
        "family": StrategyFamily.MEAN_REVERSION,
        "universe": ("EURUSD",),
        "timeframes": (Timeframe.H1,),
        "direction": TradeDirection.LONG,
        "regime": (RegimeGateRule(metric=ADX_OP, max_value=ParamRef(param="adx_ceiling")),),
        "entry": RuleSet(
            long=(
                ThresholdRule(
                    operand=IndicatorOperand(
                        spec=IndicatorSpec(
                            indicator="rsi", params={"period": ParamRef(param="rsi_period")}
                        )
                    ),
                    comparator="<",
                    value=ParamRef(param="rsi_ceiling"),
                ),
            )
        ),
        "stop": StopModel(
            kind="atr_multiple", value=ParamRef(param="stop_atr"), atr=ATR_OP
        ),
        "take_profits": (TakeProfitLeg(kind="r_multiple", value=2.0, allocation=1.0),),
        "position_management": PositionManagement(max_bars_in_trade=24),
        "parameters": {
            "rsi_period": ParameterSpec(
                kind="int", default=14, min_value=7, max_value=21, step=1
            ),
            "rsi_ceiling": ParameterSpec(
                kind="float", default=30.0, min_value=20.0, max_value=45.0, step=5.0
            ),
            "adx_ceiling": ParameterSpec(
                kind="float", default=25.0, min_value=12.0, max_value=35.0, step=1.0
            ),
            "stop_atr": ParameterSpec(
                kind="float", default=2.0, min_value=1.0, max_value=4.0, step=0.5
            ),
        },
    }
    base.update(overrides)
    return StrategyDocument(**base)


def defaults(doc: StrategyDocument, **overrides) -> dict:
    return {**doc.default_values(), **overrides}


# --------------------------------------------------------------- discovery


def test_a_template_names_every_parameter_it_references() -> None:
    doc = template()
    assert doc.unbound_parameters() == (
        "adx_ceiling",
        "rsi_ceiling",
        "rsi_period",
        "stop_atr",
    )
    assert not doc.is_bound()


def test_binding_resolves_every_position_a_reference_can_occupy() -> None:
    bound = template().bind(
        {"rsi_period": 21, "rsi_ceiling": 35.0, "adx_ceiling": 18.0, "stop_atr": 3.0}
    )
    assert bound.unbound_parameters() == ()
    assert bound.is_bound()
    # nested indicator parameter
    assert bound.entry.long[0].operand.spec.params == {"period": 21}
    assert bound.entry.long[0].operand.column == "rsi_21"
    # rule threshold
    assert bound.entry.long[0].value == 35.0
    # regime bound
    assert bound.regime[0].max_value == 18.0
    # exit model
    assert bound.stop.value == 3.0


def test_the_declared_domains_survive_binding() -> None:
    """A bound document must still be able to say what COULD have been swept."""
    doc = template()
    bound = doc.bind_defaults()
    assert set(bound.parameters) == set(doc.parameters)
    assert bound.parameters["rsi_period"].max_value == 21


def test_binding_records_the_complete_effective_set() -> None:
    doc = template()
    bound = doc.bind(defaults(doc, rsi_period=9))
    assert bound.binding == {
        "adx_ceiling": 25.0,
        "rsi_ceiling": 30.0,
        "rsi_period": 9,
        "stop_atr": 2.0,
    }


# ------------------------------------------------------------- round trip


def test_a_bound_document_round_trips_through_json() -> None:
    bound = template().bind_defaults()
    again = StrategyDocument.from_json(bound.to_json())
    assert again == bound
    assert again.content_hash() == bound.content_hash()


def test_a_template_round_trips_through_json_with_its_references_intact() -> None:
    doc = template()
    payload = json.loads(doc.to_json())
    assert payload["entry"]["long"][0]["value"] == {"$param": "rsi_ceiling"}
    again = StrategyDocument.from_json(doc.to_json())
    assert again.unbound_parameters() == doc.unbound_parameters()
    assert again.content_hash() == doc.content_hash()


def test_binding_is_deterministic() -> None:
    doc = template()
    values = defaults(doc, rsi_period=11, stop_atr=2.5)
    hashes = {doc.bind(values).content_hash() for _ in range(5)}
    assert len(hashes) == 1
    # ... and identical after a serialisation round trip of the template.
    reloaded = StrategyDocument.from_json(doc.to_json())
    assert reloaded.bind(values).content_hash() == hashes.pop()


# ------------------------------------------------------------ content hash


def test_different_bindings_are_different_strategies() -> None:
    doc = template()
    a = doc.bind(defaults(doc, rsi_period=14))
    b = doc.bind(defaults(doc, rsi_period=21))
    assert a.content_hash() != b.content_hash()
    assert a.strategy_id == b.strategy_id


def test_the_same_binding_is_the_same_strategy() -> None:
    doc = template()
    values = defaults(doc, rsi_ceiling=40.0)
    assert doc.bind(values).content_hash() == doc.bind(dict(values)).content_hash()


def test_a_binding_differs_from_its_own_template() -> None:
    doc = template()
    assert doc.bind_defaults().content_hash() != doc.content_hash()


def test_a_parameter_nothing_references_still_separates_its_bindings() -> None:
    """Otherwise two runs of a campaign collide in the holdout registry.

    ``unused`` changes no rule, so the substituted literals are identical. The
    recorded binding is what keeps them apart -- and it must, because the ledger
    has to be able to tell two campaign cells apart even when the engine cannot.
    """
    doc = template(
        parameters={
            **template().parameters,
            "unused": ParameterSpec(
                kind="int", default=1, min_value=1, max_value=5, step=1
            ),
        }
    )
    a = doc.bind(defaults(doc, unused=1))
    b = doc.bind(defaults(doc, unused=4))
    assert a.content_hash() != b.content_hash()


# --------------------------------------------------------------- refusals


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_an_unresolved_reference_refuses_to_compile(path: Path) -> None:
    doc = StrategyDocument.from_json(path.read_text())
    with pytest.raises(CompilationError, match="unresolved parameter reference"):
        compile_strategy(doc)


def test_a_partially_bound_document_still_refuses_to_compile() -> None:
    """The dangerous case: SOME references resolved, so it looks almost runnable."""
    doc = template()
    with pytest.raises(UnboundParameterError, match="no value supplied"):
        doc.bind({"rsi_period": 14, "rsi_ceiling": 30.0})


def test_a_missing_value_is_never_silently_defaulted() -> None:
    """The declared default exists, and bind() still refuses to reach for it.

    Reaching for it is precisely how a campaign reports a swept value while the
    engine ran another one. ``bind_defaults()`` is the way to say "the defaults
    are what I meant", and it says so in the recorded binding.
    """
    doc = template()
    assert doc.parameters["stop_atr"].default == 2.0
    with pytest.raises(UnboundParameterError, match="NOT defaulted"):
        doc.bind({"rsi_period": 14, "rsi_ceiling": 30.0, "adx_ceiling": 25.0})
    assert doc.bind_defaults().stop.value == 2.0


def test_a_reference_with_no_value_names_itself_in_the_error() -> None:
    doc = template()
    with pytest.raises(UnboundParameterError, match="stop_atr"):
        doc.bind({"rsi_period": 14, "rsi_ceiling": 30.0, "adx_ceiling": 25.0})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("rsi_period", 99),  # above max
        ("rsi_period", 3),  # below min
        ("rsi_period", 12.5),  # not integral
        ("rsi_ceiling", 100.0),
        ("stop_atr", 0.0),
        ("adx_ceiling", "twenty"),
    ],
)
def test_a_value_outside_the_declared_domain_is_refused(name: str, value) -> None:
    doc = template()
    with pytest.raises(OutOfDomainError, match=name):
        doc.bind(defaults(doc, **{name: value}))


def test_an_undeclared_parameter_cannot_be_bound() -> None:
    doc = template()
    with pytest.raises(UnknownParameterError, match="rsi_perid"):
        doc.bind(defaults(doc) | {"rsi_perid": 14})


def test_a_binding_field_naming_an_undeclared_parameter_is_refused() -> None:
    """A hand-edited document cannot claim a binding it does not have."""
    payload = json.loads(template().bind_defaults().to_json())
    payload["binding"]["invented"] = 3
    with pytest.raises(ValueError, match="does not declare"):
        StrategyDocument.model_validate(payload)


def test_a_binding_field_outside_the_domain_is_refused() -> None:
    payload = json.loads(template().bind_defaults().to_json())
    payload["binding"]["rsi_period"] = 99
    with pytest.raises(ValueError, match="outside the declared domain"):
        StrategyDocument.model_validate(payload)


# ------------------------------------------------------------- seed sanity


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_every_seed_reference_names_a_declared_parameter(path: Path) -> None:
    doc = StrategyDocument.from_json(path.read_text())
    assert set(doc.unbound_parameters()) <= set(doc.parameters)
    assert doc.unbound_parameters(), f"{path.stem} declares parameters but uses none"


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_every_seed_binds_at_both_ends_of_every_referenced_domain(path: Path) -> None:
    """The domain a document declares must be a domain it can actually run.

    A declared range whose endpoints do not compile is a sweep definition that
    fails halfway through a campaign, on a machine nobody is watching.
    """
    doc = StrategyDocument.from_json(path.read_text())
    referenced = set(doc.unbound_parameters())
    for name in sorted(referenced):
        spec = doc.parameters[name]
        # A choice domain has no endpoints: every declared value must compile.
        values = spec.choices if spec.kind == "choice" else (spec.min_value, spec.max_value)
        for value in values:
            cast = value if spec.kind == "choice" else (
                int(value) if spec.kind == "int" else float(value)
            )
            bound = doc.bind(defaults(doc, **{name: cast}))
            assert compile_strategy(bound).warmup_period > 0


@pytest.mark.parametrize("path", SEED_PATHS, ids=SEED_IDS)
def test_every_cell_of_a_seed_s_declared_grid_is_a_real_strategy(path: Path) -> None:
    """A declared domain must be feasible JOINTLY, not just one axis at a time.

    Found by this test on the day binding landed: ``macd_ema_trend_hybrid``
    declared ``macd_fast`` up to 20 and ``macd_slow`` from 18, so 243 of its
    2,187 declared cells asked for fast >= slow -- which MACD refuses. Nothing
    could notice before, because nothing ever built a strategy from a declared
    domain. A campaign would have died part-way through a sweep, on a machine
    nobody was watching, after paying for the cells that came first.

    Same class of defect, second shape: ``fast_ema`` and ``slow_ema`` both
    admitted 100, and two EMAs of one period write the same column, which the
    compiler refuses.
    """
    from fiboki.validation.evaluation import ParameterGrid

    doc = StrategyDocument.from_json(path.read_text())
    grid = ParameterGrid.from_document(doc, max_values_per_axis=3, max_points=10**9)
    infeasible = []
    for point in grid.points():
        try:
            compile_strategy(doc.bind({**doc.default_values(), **point}))
        except (ValueError, CompilationError) as exc:
            infeasible.append((point, str(exc).splitlines()[0]))
    assert not infeasible, (
        f"{path.stem}: {len(infeasible)} of {grid.full_size()} declared grid cells "
        f"are not strategies. First: {infeasible[0]}"
    )


def test_an_incoherent_pair_of_domains_raises_a_CATCHABLE_error() -> None:
    """A campaign must be able to record an infeasible cell, not die on it."""
    from fiboki.strategy.dsl import InfeasibleBindingError

    doc = template(
        regime=(),
        entry=RuleSet(
            long=(
                ThresholdRule(
                    operand=IndicatorOperand(
                        spec=IndicatorSpec(
                            indicator="macd",
                            params={
                                "fast": ParamRef(param="macd_fast"),
                                "slow": ParamRef(param="macd_slow"),
                                "signal": 9,
                            },
                        ),
                        output="line",
                    ),
                    comparator=">",
                    value=0.0,
                ),
            )
        ),
        stop=StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP),
        parameters={
            "macd_fast": ParameterSpec(
                kind="int", default=12, min_value=6, max_value=30, step=1
            ),
            "macd_slow": ParameterSpec(
                kind="int", default=26, min_value=18, max_value=40, step=1
            ),
        },
    )
    # inside both declared domains, and still not a strategy
    with pytest.raises(InfeasibleBindingError, match="lies inside every declared domain"):
        doc.bind({"macd_fast": 30, "macd_slow": 18})
    assert compile_strategy(doc.bind_defaults()).warmup_period > 0
