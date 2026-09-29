"""The engine's portfolio-construction seam: fingerprint, sizing, refusals, no conviction.

The byte-for-byte parity with the paper runtime is
``tests/integration/test_construction_parity.py``; the default-policy pins on
the five seed documents are ``tests/golden/test_golden_construction_engine.py``.
This file holds the properties those two rely on.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

import fiboki.validation.run as validation_run
from fiboki.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    ConstructionRequest,
    FixedFractionalSizer,
    FixedSizeSizer,
    PrecomputedSignals,
    run_backtest,
)
from fiboki.core.contracts import AccountState, Signal
from fiboki.core.enums import Direction, Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.portfolio.construction import ConstructionConfig, ConvictionPolicy, StrategyTier
from fiboki.portfolio.engine_policy import BacktestConstructionPolicy
from fiboki.portfolio.sizing import SizingPolicy, size_trade
from fiboki.sim.profiles import IG_REALISTIC
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluatorConfig,
    research_construction_policy,
)
from fiboki.validation.evaluation import DateWindow
from tests.exec_fixtures import synthetic_frame

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
ROOT = Path(__file__).resolve().parents[2]
SYMBOL = "EURUSD"


def _signals(frame: pd.DataFrame, every: int = 7, stop: float = 0.0025) -> list[Signal]:
    out = []
    for i in range(30, len(frame), every):
        ref = float(frame["close"].iloc[i])
        long = (i // every) % 2 == 0
        sign = 1.0 if long else -1.0
        out.append(
            Signal(
                strategy_id="seam", instrument=SYMBOL, timeframe="H1",
                direction=Direction.LONG if long else Direction.SHORT,
                bar_time=frame.index[i], reference_price=ref,
                stop_price=ref - sign * stop, take_profit_prices=(ref + sign * 3 * stop,),
            )
        )
    return out


def _run(construction, *, sizer=None, frame=None, max_per_instrument=1):
    frame = frame if frame is not None else synthetic_frame(n=600, seed=21)
    return run_backtest(
        data={SYMBOL: frame},
        config=BacktestConfig(
            initial_balance=100_000.0, account_ccy="USD", profile=IG_REALISTIC,
            max_concurrent=2, max_per_instrument=max_per_instrument, strategy_id="seam",
            construction=construction,
        ),
        strategy=PrecomputedSignals(_signals(frame)),
        sizer=sizer or FixedFractionalSizer(risk_fraction=0.01),
        fx=IdentityFxSource(),
    )


# ------------------------------------------------------------- fingerprint


def test_the_engine_default_is_the_flat_path_and_says_so() -> None:
    config = BacktestConfig(initial_balance=1.0)
    assert config.construction is None
    assert config.fingerprint()["construction"] == "none"
    result = _run(None)
    assert result.config_fingerprint["construction"] == "none"
    assert result.allocation_ledger == [] and result.trade_allocation_index == []


def test_the_policy_is_versioned_and_every_number_is_fingerprinted() -> None:
    base = research_construction_policy()
    fp = BacktestConfig(initial_balance=1.0, construction=base).fingerprint()["construction"]
    assert fp["construction_version"] == "construction_v2"
    assert fp["allocator"] == "equal_risk" and fp["tier"] == "probationary"
    assert fp["conviction_policy"] == "conviction_v1"
    assert fp["conviction_source"].startswith("none")
    assert "D-A3" in fp["conviction_source"]
    assert fp["regime_source"].startswith("none")
    assert fp["realised_portfolio_vol"].startswith("risk.accounting.realised_portfolio_vol")
    assert fp["instrument_vol"].startswith("not computed")
    moved = dataclasses.replace(base, config=ConstructionConfig(drawdown_pause_pct=14.0))
    assert moved.fingerprint()["config_sha256"] != fp["config_sha256"]
    assert moved.fingerprint()["construction_version"] == fp["construction_version"]
    vp = dataclasses.replace(base, allocator="volatility_parity")
    assert vp.fingerprint()["instrument_vol"].startswith("measured")


def test_an_unlabelled_injected_source_is_refused() -> None:
    with pytest.raises(ValueError, match="realised_vol_source"):
        BacktestConstructionPolicy(realised_vol=lambda s: 0.0)
    with pytest.raises(ValueError, match="regime_source_label"):
        BacktestConstructionPolicy(regime_source=lambda sym, view: "trend")
    with pytest.raises(KeyError):
        BacktestConstructionPolicy(allocator="no_such_allocator")


# ------------------------------------------------------------------ sizing


@pytest.mark.parametrize("weight", [1.0, 0.6, 0.45, 0.1234567, 0.0])
@pytest.mark.parametrize("symbol", ["EURUSD", "XAUUSD", "USDJPY"])
def test_the_weighted_sizer_matches_size_trade_exactly(weight: float, symbol: str) -> None:
    instrument = get_instrument(symbol)
    ref = {"EURUSD": 1.1, "XAUUSD": 2300.0, "USDJPY": 150.0}[symbol]
    signal = Signal(
        strategy_id="s", instrument=symbol, timeframe="H1", direction=Direction.LONG,
        bar_time=pd.Timestamp("2024-06-03 09:00", tz="UTC"), reference_price=ref,
        stop_price=ref * 0.996,
    )
    account = AccountState(balance=98_765.43, equity=98_765.43, currency="USD")
    for risk_fraction in (0.0025, 0.0075, 0.01):
        ours = FixedFractionalSizer(risk_fraction=0.01).allocated(
            risk_fraction=risk_fraction, portfolio_weight=weight
        ).size_for(signal, instrument, account, 0.79)
        outcome = size_trade(
            signal=signal, instrument=instrument, account=account, fx_quote_to_account=0.79,
            policy=SizingPolicy(risk_fraction=risk_fraction), portfolio_weight=weight,
        )
        theirs = outcome.plan.size if outcome.plan is not None else 0.0
        assert ours == theirs


def test_weight_one_is_the_unweighted_sizer_to_the_last_bit() -> None:
    frame = synthetic_frame(n=600, seed=21)
    a = _run(None, sizer=FixedFractionalSizer(risk_fraction=0.01), frame=frame)
    b = _run(None, sizer=FixedFractionalSizer(risk_fraction=0.01, portfolio_weight=1.0), frame=frame)
    assert a.ledger_text() == b.ledger_text()
    with pytest.raises(ValueError, match="never increase"):
        FixedFractionalSizer(portfolio_weight=1.5)


def test_a_sizer_that_cannot_take_an_allocation_is_refused() -> None:
    with pytest.raises(TypeError, match="cannot size at an allocation"):
        _run(research_construction_policy(), sizer=FixedSizeSizer(size=1000.0))


def test_the_sizer_risk_fraction_is_a_ceiling_on_the_tier() -> None:
    policy = dataclasses.replace(research_construction_policy(), tier=StrategyTier.FLAGSHIP)
    capped = _run(policy, sizer=FixedFractionalSizer(risk_fraction=0.001))
    assert capped.allocation_ledger
    assert {row["base_risk_pct"] for row in capped.allocation_ledger} == {0.1}
    uncapped = _run(policy, sizer=FixedFractionalSizer(risk_fraction=0.05))
    assert {row["base_risk_pct"] for row in uncapped.allocation_ledger} == {2.0}


# ---------------------------------------------------------- the ledger rows


def test_every_offered_signal_is_a_row_and_a_refusal_is_counted() -> None:
    result = _run(research_construction_policy(), max_per_instrument=2)
    rows = result.allocation_ledger
    assert len(rows) == result.signals_seen
    dropped = [r for r in rows if r["outcome"] == "allocation_dropped"]
    assert dropped, "a same-instrument signal while one is held should be refused"
    assert all(r["drop_reason"].startswith("instrument_correlation:") for r in dropped)
    assert result.rejections["allocation_dropped"] == len(dropped)
    assert all(r["size"] == 0.0 for r in dropped)
    queued = [r for r in rows if r["outcome"] == "queued"]
    assert all(r["size"] > 0 and 0.0 < r["weight"] <= 1.0 for r in queued)
    assert result.orders_submitted == len(queued)
    assert all(r["risk_pct"] <= r["base_risk_pct"] + 1e-12 for r in rows)
    steps = [step for step, _f, _d in queued[0]["trace"]]
    assert steps[0] == "lifecycle" and steps[-1] == "conviction"
    assert "signal_id" not in queued[0], "a random UUID would defeat ledger comparison"


def test_the_ledger_is_deterministic() -> None:
    a = _run(research_construction_policy())
    b = _run(research_construction_policy())
    assert a.allocation_ledger_text() == b.allocation_ledger_text()
    assert a.ledger_text() == b.ledger_text()


# ------------------------------------------ conviction: inadmissible (D-A3)


def _names_conviction(names) -> list[str]:
    return [n for n in names if "conviction" in n.lower()]


def test_no_engine_entry_point_accepts_a_conviction_source() -> None:
    assert not _names_conviction(inspect.signature(BacktestEngine.__init__).parameters)
    assert not _names_conviction(inspect.signature(run_backtest).parameters)
    assert not _names_conviction(f.name for f in dataclasses.fields(BacktestConfig))
    assert not _names_conviction(f.name for f in dataclasses.fields(ConstructionRequest))
    assert not _names_conviction(
        f.name for f in dataclasses.fields(BacktestConstructionPolicy)
    )
    with pytest.raises(TypeError):
        run_backtest(  # type: ignore[call-arg]
            data={}, config=BacktestConfig(initial_balance=1.0), strategy=None,
            sizer=None, fx=IdentityFxSource(), conviction=object(),
        )
    with pytest.raises(TypeError):
        BacktestConstructionPolicy(conviction=object())  # type: ignore[call-arg]


def test_the_engine_policy_constructs_candidates_with_a_literal_none_conviction() -> None:
    tree = ast.parse((SRC / "portfolio" / "engine_policy.py").read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "CandidateSignal"
    ]
    assert calls, "the policy builds no candidates; the assertion below would be vacuous"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "conviction" in kw, "conviction must be stated, not left to a default"
        value = kw["conviction"]
        assert isinstance(value, ast.Constant) and value.value is None


def test_the_engine_code_never_touches_a_conviction() -> None:
    tree = ast.parse((SRC / "backtest" / "engine.py").read_text(encoding="utf-8"))
    touched = []
    for node in ast.walk(tree):
        name = (
            getattr(node, "id", None) or getattr(node, "attr", None)
            or getattr(node, "arg", None) or getattr(node, "name", None)
        )
        if isinstance(name, str) and "conviction" in name.lower():
            touched.append((type(node).__name__, name, getattr(node, "lineno", 0)))
        if isinstance(node, ast.ImportFrom) and any(
            "conviction" in a.name.lower() for a in node.names
        ):
            touched.append(("ImportFrom", node.module, node.lineno))
    assert touched == []


def test_an_enabled_conviction_policy_still_multiplies_by_exactly_one() -> None:
    policy = dataclasses.replace(
        research_construction_policy(),
        config=ConstructionConfig(conviction=ConvictionPolicy(enabled=True)),
    )
    result = _run(policy)
    assert result.allocation_ledger
    for row in result.allocation_ledger:
        step, factor, detail = row["trace"][-1] if not row["dropped"] else (
            "conviction", 1.0, "missing (dropped earlier)"
        )
        assert step == "conviction" and factor == 1.0
        assert detail.startswith("missing")
    baseline = _run(research_construction_policy())
    assert [r["size"] for r in result.allocation_ledger] == [
        r["size"] for r in baseline.allocation_ledger
    ]


# --------------------------------------------------------- validation wiring


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(2400, with_volume=False) * 1000.0


def _evaluator(bars, **kwargs) -> EngineEvaluator:
    document = StrategyDocument.from_json(
        (ROOT / "research" / "strategies" / "donchian_breakout_atr.json").read_text()
    )
    return EngineEvaluator(
        document=document, frame=bars, dataset_version_id="synthetic_v1",
        config=EvaluatorConfig(
            instrument="XAUUSD", timeframe=Timeframe.H4, account_ccy="USD",
            initial_balance=100_000.0,
        ),
        fx=IdentityFxSource(),
        **kwargs,
    )


def test_validation_defaults_to_the_paper_policy_and_keys_the_cache_on_it(bars) -> None:
    default = _evaluator(bars)
    flat = _evaluator(bars, construction=None)
    assert isinstance(default.construction, BacktestConstructionPolicy)
    assert default.engine_fingerprint()["construction"]["construction_version"] == (
        "construction_v2"
    )
    assert flat.engine_fingerprint()["construction"] == "none"
    assert default.engine_config_hash() != flat.engine_config_hash()
    window = DateWindow("w", bars.index[800], bars.index[-1])
    ev = default({}, window)
    assert ev.meta["construction"]["construction_version"] == "construction_v2"
    assert ev.meta["allocation_decisions"] > 0
    assert len(ev.meta["allocation_ledger_sha256"]) == 64
    assert flat({}, window).meta["construction"] == "none"


def test_run_validation_passes_the_policy(monkeypatch, bars) -> None:
    seen: list[object] = []

    class _Stop(Exception):
        pass

    def capture(**kwargs):
        seen.append(kwargs["construction"])
        raise _Stop

    monkeypatch.setattr(validation_run, "EngineEvaluator", capture)
    document = StrategyDocument.from_json(
        (ROOT / "research" / "strategies" / "donchian_breakout_atr.json").read_text()
    )
    common = dict(
        document=document, bars=bars, dataset_version_id="synthetic_v1",
        instrument="XAUUSD", timeframe="H4", registry=None, account_ccy="USD",
    )
    for construction in (validation_run.RESEARCH_CONSTRUCTION, None):
        with pytest.raises(_Stop):
            validation_run.run_validation(**common, construction=construction)
    assert isinstance(seen[0], BacktestConstructionPolicy) and seen[1] is None
    with pytest.raises(_Stop):
        validation_run.run_validation(**common)
    assert isinstance(seen[2], BacktestConstructionPolicy)
    with pytest.raises(ValueError, match="construction="):
        validation_run.run_validation(**common, construction="flat please")
