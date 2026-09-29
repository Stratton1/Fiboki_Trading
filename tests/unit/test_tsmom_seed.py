"""The sixth seed document, ``tsmom_dual_horizon``, earns its place mechanically.

AGENTS.md: "Do not add strategies to make the roster bigger ... A new document
must say why it is a different bet rather than a reparameterisation --
``research/structure.is_reparameterisation`` answers that mechanically." These
tests are that answer, plus the ordinary obligations of any seed: it loads, it
is healthy, every reference resolves, and it runs deterministically on real bars.

The real-bar run uses the starter EURUSD H1 parquet. The document does not
permit H1 (the compiler refuses it: ``H1 is not a permitted timeframe``), so the
H1 bars are resampled to the two timeframes it DOES declare. Two facts about
that file are handled explicitly rather than assumed away:

* it is on HistData's fixed EST clock labelled as UTC (weekly open at 17:00
  "UTC"); the test checks that evidence and applies the provider's +5h
  correction before resampling, so D1/H4 buckets are true-UTC buckets;
* it is BID, not mid. The engine is fed OHLC only, as in
  ``tests/integration/test_seed_binding_parity.py``. A constant half-spread
  offset does not change the sign of a return, which is all this document
  reads, but the ledger's prices are bid prices and nothing here is a
  performance claim.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    FixedFractionalSizer,
    run_backtest,
)
from fiboki.core.enums import Provenance, Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.data.providers.histdata import (
    convert_histdata_index,
    detect_timestamp_convention,
)
from fiboki.data.resample import resample
from fiboki.data.schema import PriceBasis, canonical_frame
from fiboki.research.structure import is_reparameterisation, structure_hash
from fiboki.strategy import StrategyDocument, load_seed_registry
from fiboki.strategy.compiler import CompilationError, compile_strategy
from fiboki.strategy.primitives import ConstantOperand, CrossoverRule, ThresholdRule
from fiboki.validation.engine_evaluator import WindowedStrategyRunner
from fiboki.validation.evaluation import DateWindow

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "research" / "strategies"
BUILDER = SEED_DIR / "build_seed_documents.py"
STARTER_H1 = ROOT / "data" / "starter" / "histdata" / "eurusd" / "eurusd_h1.parquet"
TSMOM = "tsmom_dual_horizon"
OTHER_SEEDS = (
    "donchian_breakout_atr",
    "fib_golden_pocket_pullback",
    "ichimoku_kumo_trend",
    "macd_ema_trend_hybrid",
    "rsi_band_mean_reversion",
)


@pytest.fixture(scope="module")
def registry():
    return load_seed_registry(SEED_DIR)


@pytest.fixture(scope="module")
def tsmom(registry) -> StrategyDocument:
    return registry.get(TSMOM)


# ---------------------------------------------------------- (a) loads, healthy


def test_the_document_loads_through_the_registry_and_is_healthy(registry, tsmom) -> None:
    assert TSMOM in registry
    report = registry.health_check()
    assert report.ok, [(i.strategy_id, i.code, i.detail) for i in report.errors]
    mine = [(i.severity, i.code, i.detail) for i in report.issues if i.strategy_id == TSMOM]
    # Stop plus a time exit: neither ``stop_only_exit`` nor ``unmanaged_runner``.
    assert mine == [], mine
    assert tsmom.take_profits == ()
    assert tsmom.trailing is None
    assert tsmom.position_management.max_bars_in_trade is not None


def test_the_json_on_disk_is_what_the_builder_produces(tsmom) -> None:
    """The file is generated, never hand-edited."""
    spec = importlib.util.spec_from_file_location("build_seed_documents", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = module.tsmom_dual_horizon
    assert built.content_hash() == tsmom.content_hash()
    assert built.to_json() == tsmom.to_json()


def test_the_signal_is_the_sign_of_a_return(tsmom) -> None:
    """Pins the claim the hypothesis makes: roc against zero, not a price level."""
    for side, setup_cmp, direction in (("long", ">", "above"), ("short", "<", "below")):
        (setup,) = tsmom.setup.for_direction(side)
        assert isinstance(setup, ThresholdRule)
        assert setup.operand.spec.indicator == "roc"
        assert (setup.comparator, setup.value) == (setup_cmp, 0.0)
        (entry,) = tsmom.entry.for_direction(side)
        assert isinstance(entry, CrossoverRule)
        assert entry.fast.spec.indicator == "roc"
        assert entry.slow == ConstantOperand(value=0.0)
        assert entry.direction == direction


# ------------------------------------------- (b) a different bet, mechanically


@pytest.mark.parametrize("seed_id", OTHER_SEEDS)
def test_it_is_not_a_reparameterisation_of_any_other_seed(registry, tsmom, seed_id) -> None:
    other = registry.get(seed_id)
    assert not is_reparameterisation(tsmom, other)
    assert not is_reparameterisation(tsmom.bind_defaults(), other.bind_defaults())
    assert structure_hash(tsmom) != structure_hash(other)
    assert tsmom.content_hash() != other.content_hash()


def test_the_other_seeds_are_exactly_the_rest_of_the_roster(registry) -> None:
    """If the roster changes, the parametrisation above must change with it."""
    assert set(registry.ids()) == {TSMOM, *OTHER_SEEDS}


# ------------------------------------------------ (c) every reference resolves


def test_every_param_ref_resolves_under_the_default_binding(tsmom) -> None:
    declared = set(tsmom.parameters)
    assert set(tsmom.unbound_parameters()) == declared, "every declared knob is referenced"
    bound = tsmom.bind_defaults()
    assert bound.unbound_parameters() == ()
    assert bound.binding == {
        "atr_stop_multiple": 3.0,
        "fast_lookback": 20,
        "holding_bars": 21,
        "slow_lookback": 126,
    }
    compiled = compile_strategy(bound)
    assert compiled.warmup_period > 126, "the slow lookback drives warmup"


def test_the_declared_domain_is_small_and_exact(tsmom) -> None:
    domains = {name: spec.domain() for name, spec in tsmom.parameters.items()}
    assert domains == {
        "slow_lookback": (63, 126, 252),
        "fast_lookback": (10, 20, 40),
        "atr_stop_multiple": (2.0, 3.0),
        "holding_bars": (21, 42),
    }


def test_h1_is_refused_because_the_document_does_not_declare_it(tsmom) -> None:
    compiled = compile_strategy(tsmom.bind_defaults())
    frame = _starter_h1()[["open", "high", "low", "close"]]
    with pytest.raises(CompilationError, match="not a permitted timeframe"):
        compiled.generate_signal(
            compiled.prepare(frame), len(frame) - 1, instrument="EURUSD",
            timeframe=Timeframe.H1,
        )


# ------------------------------------------- (d)+(e) deterministic, and trades


def _starter_h1() -> pd.DataFrame:
    raw = pd.read_parquet(STARTER_H1)
    evidence = detect_timestamp_convention(raw.index)
    assert evidence.looks_fixed_offset, evidence.summary()
    corrected = raw.set_axis(
        convert_histdata_index(raw.index, already_mislabelled_utc=True), axis=0
    )
    return canonical_frame(
        corrected, instrument="EURUSD", timeframe=Timeframe.H1, price_basis=PriceBasis.BID
    )


@pytest.fixture(scope="module")
def starter_bars() -> dict[Timeframe, pd.DataFrame]:
    h1 = _starter_h1()
    return {
        tf: resample(h1, tf, source=Timeframe.H1)[["open", "high", "low", "close"]]
        for tf in (Timeframe.D1, Timeframe.H4)
    }


def _run(document: StrategyDocument, frame: pd.DataFrame, tf: Timeframe) -> BacktestResult:
    """Same engine path as ``test_seed_binding_parity._run``, at the bar's own timeframe."""
    symbol = "EURUSD"
    compiled = compile_strategy(document)
    window = DateWindow(
        "tsmom", frame.index[0], frame.index[-1] + pd.Timedelta(minutes=tf.minutes)
    )
    runner = WindowedStrategyRunner(compiled, symbol, frame, tf, window)
    config = BacktestConfig(
        initial_balance=100_000.0,
        account_ccy=get_instrument(symbol).quote,
        max_concurrent=document.position_management.max_concurrent_positions,
        max_per_instrument=document.position_management.max_concurrent_positions,
        strategy_id=document.strategy_id,
        provenance=Provenance.BACKTEST,
    )
    return run_backtest(
        data={symbol: frame},
        config=config,
        strategy=runner,
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=IdentityFxSource(),
    )


@pytest.mark.parametrize("tf", [Timeframe.D1, Timeframe.H4], ids=lambda t: t.value)
def test_two_runs_on_the_starter_bars_produce_identical_ledgers(
    tsmom, starter_bars, tf
) -> None:
    bound = tsmom.bind_defaults()
    frame = starter_bars[tf]
    first, second = _run(bound, frame, tf), _run(bound, frame, tf)
    assert first.ledger_text() == second.ledger_text()
    assert first.ledger_sha256() == second.ledger_sha256()
    assert first.rejections == second.rejections
    assert first.signals_seen == second.signals_seen


@pytest.mark.parametrize("tf", [Timeframe.D1, Timeframe.H4], ids=lambda t: t.value)
def test_the_default_binding_trades_on_the_starter_bars(tsmom, starter_bars, tf) -> None:
    """A determinism test over two empty ledgers would prove nothing.

    The precondition is stated rather than assumed: the sample must be longer
    than the warmup, or zero trades would be the honest answer and this test
    would say so instead of passing on an empty ledger.
    """
    bound = tsmom.bind_defaults()
    frame = starter_bars[tf]
    warmup = compile_strategy(bound).warmup_period
    assert len(frame) > warmup, (
        f"{tf.value}: {len(frame)} bars cannot clear a {warmup}-bar warmup; "
        "zero trades here would be the sample's fault, not the strategy's"
    )
    result = _run(bound, frame, tf)
    assert result.signals_seen > 0, result.rejections
    assert len(result.trades) >= 1, (len(frame), warmup, result.rejections)
