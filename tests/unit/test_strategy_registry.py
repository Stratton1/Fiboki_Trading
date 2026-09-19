"""Strategy registry: deduplication by content hash, and the health check."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fiboki.core.enums import Timeframe
from fiboki.strategy import (
    DuplicateStrategyError,
    IndicatorOperand,
    IndicatorSpec,
    PositionManagement,
    RuleSet,
    StopModel,
    StrategyDocument,
    StrategyFamily,
    StrategyRegistry,
    TakeProfitLeg,
    ThresholdRule,
    TradeDirection,
    load_seed_registry,
)

SEED_DIR = Path(__file__).resolve().parents[2] / "research" / "strategies"

HYP = (
    "A test hypothesis long enough for the schema. Mechanism: short-horizon "
    "reversal as liquidity provision (Nagel 2012). Evidence against: oscillator "
    "rules on FX did not survive Step-SPA correction in Coakley, Marzano and "
    "Nankervis (2016), so the honest prior is an edge near zero after spread."
)
RSI = IndicatorSpec(indicator="rsi", params={"period": 14})
ATR_OP = IndicatorOperand(spec=IndicatorSpec(indicator="atr", params={"period": 14}))


def doc(strategy_id: str, threshold: float = 30.0, **kw) -> StrategyDocument:
    base = {
        "strategy_id": strategy_id,
        "name": strategy_id.replace("_", " ").title(),
        "hypothesis": HYP,
        "family": StrategyFamily.MEAN_REVERSION,
        "universe": ("EURUSD",),
        "timeframes": (Timeframe.H1,),
        "direction": TradeDirection.LONG,
        "entry": RuleSet(
            long=(
                ThresholdRule(
                    operand=IndicatorOperand(spec=RSI), comparator="<", value=threshold
                ),
            )
        ),
        "stop": StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP),
        "take_profits": (TakeProfitLeg(kind="r_multiple", value=2.0, allocation=1.0),),
        "position_management": PositionManagement(max_bars_in_trade=24),
    }
    base.update(kw)
    return StrategyDocument(**base)


# ------------------------------------------------------------ dedup


def test_identical_content_under_a_different_name_is_rejected() -> None:
    reg = StrategyRegistry()
    reg.register(doc("alpha"))
    with pytest.raises(DuplicateStrategyError, match="semantically identical"):
        reg.register(doc("beta"))
    assert reg.ids() == ["alpha"]


def test_different_content_registers_fine() -> None:
    reg = StrategyRegistry()
    reg.register(doc("alpha", threshold=30.0))
    reg.register(doc("beta", threshold=25.0))
    assert len(reg) == 2
    assert reg.by_hash(doc("alpha").content_hash()).strategy_id == "alpha"
    assert reg.by_hash("deadbeef") is None


def test_re_registering_the_same_document_is_idempotent() -> None:
    reg = StrategyRegistry()
    h1 = reg.register(doc("alpha"))
    h2 = reg.register(doc("alpha"))
    assert h1 == h2
    assert len(reg) == 1


def test_changing_a_registered_id_requires_replace() -> None:
    reg = StrategyRegistry()
    reg.register(doc("alpha", threshold=30.0))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(doc("alpha", threshold=20.0))
    reg.register(doc("alpha", threshold=20.0), replace=True)
    assert len(reg) == 1
    assert reg.get("alpha").entry.long[0].value == 20.0
    # The stale hash must not linger and block an unrelated strategy.
    reg.register(doc("gamma", threshold=30.0))
    assert len(reg) == 2


def test_membership_iteration_and_lookup() -> None:
    reg = StrategyRegistry()
    reg.register(doc("alpha"))
    assert "alpha" in reg
    assert [d.strategy_id for d in reg] == ["alpha"]
    with pytest.raises(KeyError):
        reg.get("nope")


# ------------------------------------------------------- health check


def test_seed_registry_is_healthy() -> None:
    reg = load_seed_registry(SEED_DIR)
    report = reg.health_check()
    assert len(reg) == 5
    assert report.ok, [(i.strategy_id, i.code, i.detail) for i in report.errors]
    assert report.checked == 5
    assert "5 strategies checked" in report.summary()


def test_health_check_flags_a_stop_only_exit() -> None:
    reg = StrategyRegistry()
    reg.register(
        doc("naked", take_profits=(), trailing=None,
            position_management=PositionManagement())
    )
    report = reg.health_check()
    assert report.ok  # a warning, not an error
    assert [i.code for i in report.warnings] == ["stop_only_exit"]


def test_health_check_flags_volume_indicators_on_fx() -> None:
    """The V1 OBV trap: an indicator that can never produce a signal on FX."""
    obv = IndicatorSpec(indicator="obv", params={"on_unavailable": "mark"})
    reg = StrategyRegistry()
    reg.register(
        doc(
            "obv_on_fx",
            entry=RuleSet(
                long=(
                    ThresholdRule(
                        operand=IndicatorOperand(spec=obv, output="obv"),
                        comparator=">",
                        value=0.0,
                    ),
                )
            ),
        )
    )
    codes = [i.code for i in reg.health_check().warnings]
    assert "volume_on_fx" in codes


def test_health_check_flags_a_missing_parent() -> None:
    reg = StrategyRegistry()
    reg.register(doc("orphan", parent_strategy_ids=("ghost",)))
    assert "missing_parent" in [i.code for i in reg.health_check().warnings]


def test_health_check_flags_an_unmanaged_runner() -> None:
    reg = StrategyRegistry()
    reg.register(
        doc(
            "partial",
            take_profits=(TakeProfitLeg(kind="r_multiple", value=1.0, allocation=0.5),),
            trailing=None,
        )
    )
    assert "unmanaged_runner" in [i.code for i in reg.health_check().warnings]


# ------------------------------------------------------------ loading


def test_load_directory_reads_every_seed() -> None:
    reg = StrategyRegistry()
    hashes = reg.load_directory(SEED_DIR)
    assert len(hashes) == len(set(hashes)) == 5
    assert "ichimoku_kumo_trend" in reg.ids()


def test_load_directory_rejects_a_hand_edited_complexity_score(tmp_path: Path) -> None:
    payload = json.loads(doc("handedited").to_json())
    payload["complexity_score"] = 42.0
    (tmp_path / "a.json").write_text(json.dumps(payload))
    reg = StrategyRegistry()
    with pytest.raises(ValueError, match="edited by hand"):
        reg.load_directory(tmp_path)


def test_load_directory_needs_a_real_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        StrategyRegistry().load_directory(tmp_path / "missing")


def test_compiled_returns_one_per_document() -> None:
    reg = load_seed_registry(SEED_DIR)
    compiled = reg.compiled()
    assert len(compiled) == len(reg)
    assert {c.strategy_id for c in compiled} == set(reg.ids())
