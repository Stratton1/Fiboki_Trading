"""The 100 generated strategy documents in ``research/generated/`` (campaign K5).

AGENTS.md: "Do not add strategies to make the roster bigger ... A new document
must say why it is a different bet rather than a reparameterisation --
``research/structure.is_reparameterisation`` answers that mechanically." The
generator answers it per document by structure hash; these tests re-derive the
answer from the files on disk rather than trusting the manifest, and check the
ordinary obligations of any document: it loads, it is healthy, every reference
resolves, every declared value compiles, and it runs deterministically on real
bars.

The real-bar smoke run reuses ``tests/unit/test_tsmom_seed.py``'s approach:
the starter EURUSD H1 parquet, HistData's EST clock corrected to true UTC,
resampled to H4, OHLC only. The bars are BID; nothing here is a performance
claim, and any trade count including zero is acceptable. Only determinism and
the absence of an exception are asserted.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType

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
from fiboki.discovery.mutation import compiles
from fiboki.research.structure import is_reparameterisation, structure_hash
from fiboki.strategy import StrategyDocument, load_seed_registry
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.registry import StrategyRegistry
from fiboki.validation.engine_evaluator import WindowedStrategyRunner
from fiboki.validation.evaluation import DateWindow

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "research" / "generate_families.py"
GENERATED = ROOT / "research" / "generated"
SEED_DIR = ROOT / "research" / "strategies"
STARTER_H1 = ROOT / "data" / "starter" / "histdata" / "eurusd" / "eurusd_h1.parquet"
FAMILIES = (
    "trend_pullback",
    "breakout_vol",
    "mean_reversion_band",
    "momentum_oscillator",
    "ichimoku_variants",
    "session_breakout",
)


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_families_under_test", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: its dataclasses resolve string annotations by
    # looking their module up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    return _load_generator()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((GENERATED / "MANIFEST.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def documents() -> dict[str, StrategyDocument]:
    """File stem -> document, read from disk exactly as a loader would."""
    return {
        p.stem: StrategyDocument.from_json(p.read_text(encoding="utf-8"))
        for p in sorted(GENERATED.glob("*.json"))
        if p.name != "MANIFEST.json"
    }


@pytest.fixture(scope="module")
def seeds() -> dict[str, StrategyDocument]:
    return {d.strategy_id: d for d in load_seed_registry(SEED_DIR)}


def _family(stem: str) -> str:
    return stem.split("__", 1)[0]


# ------------------------------------------------ reproducible, and complete


def test_regeneration_is_byte_identical(generator, tmp_path) -> None:
    """The directory is generated output: regenerate and compare every byte."""
    generator.write(generator.generate(), tmp_path)
    fresh = {p.name: p.read_bytes() for p in sorted(tmp_path.glob("*.json"))}
    committed = {p.name: p.read_bytes() for p in sorted(GENERATED.glob("*.json"))}
    assert sorted(fresh) == sorted(committed)
    changed = [name for name in fresh if fresh[name] != committed[name]]
    assert changed == [], f"regenerate research/generated: {changed[:5]}"


def test_the_manifest_matches_the_files_on_disk(manifest, documents) -> None:
    assert manifest["generator_sha256"] == hashlib.sha256(GENERATOR.read_bytes()).hexdigest()
    assert manifest["sample_size"] == 100
    assert manifest["grammar_size_after_dedupe"] <= manifest["grammar_size_before_dedupe"]
    rows = {row["file"]: row for row in manifest["documents"]}
    assert sorted(rows) == sorted(f"{stem}.json" for stem in documents)
    for stem, document in documents.items():
        row = rows[f"{stem}.json"]
        assert row["strategy_id"] == document.strategy_id
        assert row["content_hash"] == document.content_hash()
        assert row["structure_hash"] == structure_hash(document)
        assert stem.endswith("__" + row["structure_hash"][:8])
        assert stem == "__".join(
            (row["family"], row["entry"], row["filter"], row["exit"], row["structure_hash"][:8])
        )


def test_exactly_100_documents_and_every_family_has_at_least_12(manifest, documents) -> None:
    assert len(documents) == 100
    counts = Counter(_family(stem) for stem in documents)
    assert set(counts) == set(FAMILIES)
    assert min(counts.values()) >= 12, counts
    assert dict(counts) == manifest["sample_per_family"]


# ---------------------------------------------------- every document healthy


def test_every_document_is_attributed_and_names_its_cell(documents) -> None:
    for stem, document in documents.items():
        family, entry, flt, exit_, _ = stem.split("__")
        assert document.author == "fiboki-v2-generator"
        assert f"family={family} entry={entry} filter={flt} exit={exit_}" in document.notes
        assert "ECONOMIC STORY" in document.hypothesis
        assert "EVIDENCE AGAINST, STATED PLAINLY" in document.hypothesis
        assert f"GRAMMAR CELL. Entry '{entry}'" in document.hypothesis
        assert "EURUSD" in document.universe  # the smoke run needs it


def test_every_document_passes_the_registry_health_check(documents) -> None:
    registry = StrategyRegistry()
    registry.load_directory(GENERATED, pattern="*__*.json")
    assert len(registry) == 100
    report = registry.health_check()
    assert report.errors == (), [(i.strategy_id, i.code, i.detail) for i in report.errors]
    banned = [i for i in report.issues if i.code in ("unmanaged_runner", "stop_only_exit")]
    assert banned == [], banned


def test_every_document_compiles_and_every_reference_resolves(documents) -> None:
    for stem, document in documents.items():
        ok, why = compiles(document)
        assert ok, (stem, why)
        # Every declared knob is referenced, and nothing referenced is undeclared.
        assert set(document.unbound_parameters()) == set(document.parameters), stem
        bound = document.bind_defaults()
        assert bound.unbound_parameters() == (), stem
        assert set(bound.binding or {}) == set(document.parameters), stem


def test_declared_grids_are_small(documents) -> None:
    for stem, document in documents.items():
        assert 1 <= len(document.parameters) <= 3, stem
        cells = 1
        for name, p in document.parameters.items():
            assert p.kind == "choice", (stem, name)
            assert 2 <= len(p.domain()) <= 3, (stem, name)
            cells *= len(p.domain())
        assert cells <= 36, (stem, cells)


def test_every_declared_value_compiles(documents) -> None:
    """Choice domains are small enough to bind the FULL grid of every document."""
    for stem, document in documents.items():
        names = sorted(document.parameters)
        grid = [{}]
        for name in names:
            grid = [dict(g, **{name: v}) for g in grid for v in document.parameters[name].domain()]
        for values in grid:
            compile_strategy(document.bind(values))  # raises on any infeasible cell
        assert len(grid) >= 2, stem


# -------------------------------------------------- structurally distinct


def test_structure_hashes_are_distinct_and_none_is_a_seed(documents, seeds) -> None:
    hashes = {stem: structure_hash(d) for stem, d in documents.items()}
    assert len(set(hashes.values())) == 100
    seed_hashes = {sid: structure_hash(d) for sid, d in seeds.items()}
    assert len(seed_hashes) == 6
    assert not set(hashes.values()) & set(seed_hashes.values())


@pytest.mark.parametrize(
    ("family", "seed_id"),
    [
        ("breakout_vol", "donchian_breakout_atr"),
        ("ichimoku_variants", "ichimoku_kumo_trend"),
        ("session_breakout", "donchian_breakout_atr"),
        ("mean_reversion_band", "rsi_band_mean_reversion"),
        ("trend_pullback", "macd_ema_trend_hybrid"),
        ("momentum_oscillator", "tsmom_dual_horizon"),
    ],
)
def test_no_family_member_is_a_reparameterisation_of_its_nearest_seed(
    documents, seeds, family, seed_id
) -> None:
    """The families that borrow a seed's mechanism are checked against that seed,
    unbound and at their default bindings (both are what research memory sees)."""
    seed = seeds[seed_id]
    members = [d for stem, d in documents.items() if _family(stem) == family]
    assert len(members) >= 12
    for document in members:
        assert structure_hash(document) != structure_hash(seed)
        assert not is_reparameterisation(document, seed)
        assert not is_reparameterisation(document.bind_defaults(), seed.bind_defaults())


def test_breakout_vol_differs_from_donchian_in_its_setup_not_only_its_context(
    documents, seeds
) -> None:
    """Different family token or universe alone would also change the hash; the
    difference that matters is the compression setup, so assert it directly."""
    donchian_setup = {r.op for r in seeds["donchian_breakout_atr"].setup.all_rules()}
    for stem, document in documents.items():
        if _family(stem) != "breakout_vol":
            continue
        indicators = {
            s.indicator
            for rule in document.setup.all_rules()
            for s in rule.required_indicators()
        }
        assert {"realised_volatility", "bollinger", "keltner"} <= indicators, stem
        assert {r.op for r in document.setup.all_rules()} != donchian_setup


def test_session_breakout_is_gated_by_the_london_open(documents) -> None:
    for stem, document in documents.items():
        if _family(stem) != "session_breakout":
            continue
        windows = [r for r in document.setup.all_rules() if r.op == "session_window"]
        assert windows and all((w.start_hour_utc, w.end_hour_utc) == (7, 10) for w in windows)
        assert Timeframe.H1 in document.timeframes and Timeframe.D1 not in document.timeframes


# ------------------------------------------------------------- smoke run


def _starter_h4() -> pd.DataFrame:
    raw = pd.read_parquet(STARTER_H1)
    evidence = detect_timestamp_convention(raw.index)
    assert evidence.looks_fixed_offset, evidence.summary()
    corrected = raw.set_axis(
        convert_histdata_index(raw.index, already_mislabelled_utc=True), axis=0
    )
    h1 = canonical_frame(
        corrected, instrument="EURUSD", timeframe=Timeframe.H1, price_basis=PriceBasis.BID
    )
    return resample(h1, Timeframe.H4, source=Timeframe.H1)[["open", "high", "low", "close"]]


def _run(document: StrategyDocument, frame: pd.DataFrame) -> BacktestResult:
    """Same engine path as ``test_tsmom_seed._run``."""
    symbol, tf = "EURUSD", Timeframe.H4
    compiled = compile_strategy(document)
    window = DateWindow(
        "generated", frame.index[0], frame.index[-1] + pd.Timedelta(minutes=tf.minutes)
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


def _smoke_pick(documents: dict[str, StrategyDocument]) -> list[str]:
    """Ten documents, round-robin over families in sorted-stem order, H4 only.

    Two per family where possible: six families give one each, then four more.
    Every family declares H4; session_breakout declares no D1 at all.
    """
    by_family = {
        f: sorted(s for s, d in documents.items()
                  if _family(s) == f and Timeframe.H4 in d.timeframes)
        for f in FAMILIES
    }
    picked: list[str] = []
    for round_ in range(2):
        for f in FAMILIES:
            if len(picked) < 10 and len(by_family[f]) > round_:
                picked.append(by_family[f][round_])
    return picked


@pytest.mark.slow
def test_ten_documents_run_deterministically_on_the_starter_bars(documents) -> None:
    frame = _starter_h4()
    picked = _smoke_pick(documents)
    assert len(picked) == 10
    assert {_family(s) for s in picked} == set(FAMILIES)
    trades: dict[str, int] = {}
    for stem in picked:
        bound = documents[stem].bind_defaults()
        first, second = _run(bound, frame), _run(bound, frame)
        assert first.ledger_text() == second.ledger_text(), stem
        assert first.ledger_sha256() == second.ledger_sha256(), stem
        assert first.rejections == second.rejections, stem
        assert first.signals_seen == second.signals_seen, stem
        trades[stem] = len(first.trades)
    # Reported, not asserted: any count, including zero, is acceptable here.
    print("\nsmoke trade counts (EURUSD H4 starter bars, default binding):")
    for stem, n in trades.items():
        print(f"  {n:4d}  {stem}")
