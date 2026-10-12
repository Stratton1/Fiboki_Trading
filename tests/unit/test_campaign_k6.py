"""K6: the generator's determinism and dedupe, the trial accounting, the screen's
accounting and its equivalence to rung 0, the signal bound, and shard resume.

No verdict about any strategy is asserted: synthetic bars say nothing about
markets. What is pinned is that every generated document is counted, that the
screen removes nothing rung 0 would keep, and that a run can stop and resume
without losing or double-counting a document.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "tests"))

import campaign_k6 as k6  # noqa: E402
import generate_families_k6 as gen  # noqa: E402

from fiboki.core.enums import Timeframe  # noqa: E402
from fiboki.core.money import IdentityFxSource  # noqa: E402
from fiboki.research.structure import structure_hash  # noqa: E402
from fiboki.strategy.compiler import compile_strategy  # noqa: E402
from fiboki.validation.engine_evaluator import (  # noqa: E402
    EngineEvaluator,
    EvaluationCache,
    EvaluatorConfig,
)
from fiboki.validation.gates import PRODUCTION_GATE_SET  # noqa: E402
from fiboki.validation.ladder import LadderConfig, ValidationLadder  # noqa: E402
from fiboki.validation.pooled import rungs_with_deferred_holdout  # noqa: E402

# ---------------------------------------------------------------- generator


@pytest.fixture(scope="module")
def small() -> gen.Generated:
    return gen.generate(300)


def test_generation_is_deterministic(small: gen.Generated) -> None:
    again = gen.generate(300)
    assert again.manifest["cells_sha256"] == small.manifest["cells_sha256"]
    assert again.manifest["hash_chain_sha256"] == small.manifest["hash_chain_sha256"]
    assert [c.code for _, c in again.cells] == [c.code for _, c in small.cells]


def test_a_different_seed_is_a_different_campaign(small: gen.Generated) -> None:
    other = gen.generate(300, seed=gen.RNG_SEED + 1, check=False)
    assert other.manifest["cells_sha256"] != small.manifest["cells_sha256"]


def test_kept_documents_are_distinct_on_every_hash(small: gen.Generated) -> None:
    docs = [gen.build_document(i, c) for i, c in small.cells]
    for key in (structure_hash, gen.rules_hash, gen.body_hash):
        values = [key(d) for d in docs]
        assert len(set(values)) == len(values), key.__name__
    prior = gen.prior_rules_hashes()
    assert len(prior) >= 100  # six seeds and K5's hundred, less exact repeats
    assert not {gen.rules_hash(d) for d in docs} & set(prior)
    assert [i for i, _ in small.cells] == list(range(len(small.cells)))


def test_a_cell_with_a_prior_documents_rules_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    order = gen.permutation(gen.grammar_size())
    first = next(gen.cell_at(int(f)) for f in order if gen.exclusion(gen.cell_at(int(f))) is None)
    planted = gen.rules_hash(gen.build_document(0, first))
    real = gen.prior_rules_hashes
    monkeypatch.setattr(gen, "prior_rules_hashes", lambda: {**real(), planted: "planted_seed"})
    out = gen.generate(20, check=False)
    assert out.manifest["dropped"] == {"rules of a seed or K5 document": 1}
    assert first.code not in {c.code for _, c in out.cells}


def test_cell_codes_round_trip_and_exclusions_hold(small: gen.Generated) -> None:
    for _, cell in small.cells:
        assert gen.Cell.from_code(cell.code) == cell
        assert gen.exclusion(cell) is None
    assert gen.exclusion(gen.Cell("rsi_side", "rsi_50_cross", "none", "none", "none",
                                  "tp_r", "both")) is not None
    assert gen.exclusion(gen.Cell("none", "tk_cross", "none", "none", "none",
                                  "swing_tp", "both")) is not None


def test_documents_are_cost_aware_and_small_grids(small: gen.Generated) -> None:
    for index, cell in small.cells[:120]:
        doc = gen.build_document(index, cell)
        compile_strategy(doc.bind_defaults())
        assert gen.declared_grid_points(doc) <= gen.MAX_GRID_POINTS
        assert Timeframe.H1 not in doc.timeframes
        stop = doc.stop
        if stop.kind == "atr_multiple":
            assert float(stop.value) >= 2.0
        assert float(stop.min_distance_atr) >= 1.0
        for name, p in doc.parameters.items():
            dom = [float(v) for v in p.domain()]
            if name == "exit_size" and cell.exit in ("tp_r", "tp_trail", "swing_tp"):
                assert min(dom) >= 2.0
            if name == "exit_size" and cell.exit == "time_exit":
                assert min(dom) >= 20
            if name == "exit_size" and cell.exit == "atr_target":
                assert min(dom) >= 3.0


def test_materialise_changes_only_the_universe(small: gen.Generated) -> None:
    index, cell = small.cells[7]
    a = gen.materialise(index, cell, ("EURUSD", "GBPUSD"))
    b = gen.materialise(index, cell, ("AUDUSD", "EURUSD", "USDJPY"))
    assert gen.body_hash(a) == gen.body_hash(b)
    assert gen.rules_hash(a) == gen.rules_hash(b)
    assert a.content_hash() != b.content_hash()


def test_written_cells_verify_against_their_manifest(small: gen.Generated, tmp_path: Path) -> None:
    gen.write(small, tmp_path)
    assert [c.code for _, c in gen.read_cells(tmp_path)] == [c.code for _, c in small.cells]
    first = (tmp_path / gen.CELLS_NAME).read_bytes()
    gen.write(small, tmp_path)
    assert (tmp_path / gen.CELLS_NAME).read_bytes() == first  # mtime-free gzip
    manifest = json.loads((tmp_path / gen.MANIFEST_NAME).read_text())
    manifest["cells_sha256"] = "0" * 64
    (tmp_path / gen.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        gen.read_cells(tmp_path)


def test_committed_manifest_is_the_filed_size() -> None:
    manifest = json.loads((gen.OUT / gen.MANIFEST_NAME).read_text())
    assert manifest["size"] == 100_000 and manifest["rng_seed"] == gen.RNG_SEED
    assert sum(manifest["per_family"].values()) == 100_000
    assert manifest["checked"] is True
    cells = gen.read_cells(gen.OUT)
    assert len(cells) == 100_000 and len({c.code for _, c in cells}) == 100_000


# --------------------------------------------------------------- accounting


def test_every_document_counts_in_the_declared_n(small: gen.Generated) -> None:
    cells = small.cells
    h4_only = sum(1 for _, c in cells if c.filter == "london_ny")
    assert k6.screen_trials(cells, ("H4", "D1")) == 2 * len(cells) - h4_only
    assert k6.screen_trials(cells, ("H4",)) == len(cells)
    d = k6.declared_trials(cells)
    assert d["screen_trials"] == k6.N_UNIVERSES_DECLARED * (2 * len(cells) - h4_only)
    assert d["ladder_trials_max"] == k6.LADDER_CAP * 7 * 2 * k6.N_UNIVERSES_DECLARED
    assert d["declared_true_n"] == k6.PRIOR_TRIALS + d["screen_trials"] + d["ladder_trials_max"]
    assert k6.external_trials(d["declared_true_n"], 8) == d["declared_true_n"] - 8


def test_deflation_bar_rises_with_n_like_sqrt_2_ln_n() -> None:
    rows = k6.deflation_table([1_000, 21_968, 400_000])
    emax = [r["expected_max_z"] for r in rows]
    assert emax == sorted(emax)
    for r in rows:
        assert abs(r["expected_max_z"] - r["sqrt_2_ln_n"]) < 0.6
        assert r["certifiable_T1000"] > r["sr0_T1000"]
        assert math.isclose(r["sr0_T4000"] * 2, r["sr0_T1000"], rel_tol=1e-3)


def test_universes_are_names_and_unknown_ones_are_refused() -> None:
    fx = k6.resolve_universe("fx_pooled_k5")
    assert len(fx["instruments"]) == 16 and len(fx["sha256"]) == 64
    with pytest.raises(SystemExit):
        k6.resolve_universe("fx_everything")
    # The crypto NAME resolves once fiboki.research.universes exists (wave/crypto is merged),
    # exactly as the filing says; every evidence run on it is still refused.
    crypto = k6.resolve_universe("crypto_majors_v1")
    assert crypto["instruments"] and len(crypto["sha256"]) == 64
    ok, why = k6.admitted("H4", "crypto_majors_v1", None, evidence=True)
    assert not ok and "crypto_majors_v1" in why


def test_an_evidence_run_is_refused_while_the_filing_is_draft() -> None:
    assert k6.filing()["status"].startswith("DRAFT")
    with pytest.raises(SystemExit):
        k6.require_filed(evidence=True)
    k6.require_filed(evidence=False)


# --------------------------------------------------------- screen on a pool


@dataclass
class _Series:
    instrument: str
    timeframe: Timeframe
    frame: pd.DataFrame
    dataset_version_id: str


def _bars(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    core = np.zeros(n)
    for i in range(1, n):
        core[i] = 0.92 * core[i - 1] + rng.normal(0.0, 0.004)
    close = 1.2 * np.exp(core + np.cumsum(rng.normal(0.0, 0.001, n)))
    wick = np.abs(rng.normal(0, 0.0012, n)) * close
    open_ = np.concatenate([[close[0]], close[:-1]])
    idx = pd.date_range("2012-01-02", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": np.maximum.reduce([close + wick, open_, close]),
                         "low": np.minimum.reduce([close - wick, open_, close]),
                         "close": close}, index=idx)


SYMBOLS = ("AUDUSD", "EURUSD", "GBPUSD")


@pytest.fixture(scope="module")
def series() -> list[_Series]:
    return [_Series(s, Timeframe.H4, _bars(2600, 100 + k), f"synthetic_{s}_h4")
            for k, s in enumerate(SYMBOLS)]


def _make(document: Any, s: _Series, cache: Any) -> EngineEvaluator:
    return EngineEvaluator(
        document=document, frame=s.frame, dataset_version_id=s.dataset_version_id,
        config=EvaluatorConfig(instrument=s.instrument, timeframe=Timeframe.H4,
                               account_ccy="USD", initial_balance=100_000.0),
        fx=IdentityFxSource(), cache=cache if cache is not None else EvaluationCache(),
    )


@pytest.fixture(scope="module")
def documents(small: gen.Generated) -> list[tuple[int, Any]]:
    return [(i, gen.materialise(i, c, SYMBOLS)) for i, c in small.cells[:8]]


def test_signal_bound_is_never_below_the_trades(series, documents) -> None:
    for _, doc in documents[:6]:
        for s in series:
            ev = _make(doc, s, None)
            window = k6.DateWindow("research", s.frame.index[0], s.frame.index[2000])
            result = ev(doc.default_values(), window)
            bound = ev.signal_count(doc.default_values(), window)
            assert result.meta["signals_emitted"] <= bound
            assert result.n_trades <= bound
            if not result.meta["bankrupt"]:
                assert result.meta["signals_emitted"] == bound


def test_screen_counts_every_document_and_equals_rung_zero(series, documents,
                                                           tmp_path: Path) -> None:
    header = {"study": "K6", "stage": "screen", "indices": [i for i, _ in documents],
              "n_documents": len(documents), "shard": {"k": 0, "of": 1}}
    out = k6.ShardFile(tmp_path / "s.jsonl", header, resume=False)
    state = k6.run_stage("screen", documents=documents, series=series, make=_make, out=out,
                         declared_true_n=400_000)
    out.close()
    assert state == "done"
    rows = out.rows
    assert sorted(rows) == [i for i, _ in documents]  # every document has a row
    assert all(r["status"] in ("run", "skipped", "error") for r in rows.values())
    assert not [r for r in rows.values() if r["status"] == "error"], rows
    assert any(r.get("stage") == "S0b" for r in rows.values()), "S0b must be exercised"
    assert any(r.get("stage") == "S0a" for r in rows.values()), "S0a must be exercised"
    # The screen verdict is the fail-fast ladder's rung-0 verdict, at both stages.
    for index, doc in documents:
        pool = k6.build_pool(doc, series, _make, cache=True)
        assert not isinstance(pool, str)
        report = ValidationLadder(config=LadderConfig(external_trial_count=400_000),
                                  gate_set=PRODUCTION_GATE_SET,
                                  rungs=rungs_with_deferred_holdout()).run(
            pool.candidate, pool.evaluator, registry=pool.ledger,
            dataset_version_id=pool.evaluator.dataset_version_id)
        assert report.rungs[0].passed == rows[index]["passed"], (index, rows[index])
        if rows[index].get("stage") == "S0b":
            assert rows[index]["n_trades"] <= rows[index]["signal_bound"]


def test_resume_loses_nothing_and_redoes_a_torn_row(series, documents, tmp_path: Path) -> None:
    docs = documents[:4]
    header = {"study": "K6", "stage": "screen", "indices": [i for i, _ in docs],
              "n_documents": len(docs), "shard": {"k": 0, "of": 1}}
    path = tmp_path / "r.jsonl"
    out = k6.ShardFile(path, header, resume=False)
    assert k6.run_stage("screen", documents=docs, series=series, make=_make, out=out,
                        declared_true_n=1, max_documents=2) == "limit"
    out.close()
    with pytest.raises(SystemExit):
        k6.ShardFile(path, header, resume=False)  # never overwritten by accident
    with pytest.raises(SystemExit):
        k6.ShardFile(path, {**header, "stage": "ladder"}, resume=True)  # a different run
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "row", "index": ' + str(docs[2][0]) + ', "trunc')  # torn
    out = k6.ShardFile(path, header, resume=True)
    assert sorted(out.rows) == sorted(i for i, _ in docs[:2])
    assert k6.run_stage("screen", documents=docs, series=series, make=_make, out=out,
                        declared_true_n=1) == "done"
    out.close()
    _, rows = k6.read_shard(path)
    assert sorted(int(r["index"]) for r in rows) == sorted(i for i, _ in docs)


def test_merge_refuses_gaps_and_overlaps(tmp_path: Path) -> None:
    def shard(name: str, k: int, indices: list[int]) -> Path:
        path = tmp_path / name
        header = {"kind": "header", "study": "K6", "stage": "screen", "indices": indices,
                  "n_documents": 4, "shard": {"k": k, "of": 2}}
        lines = [json.dumps(header)] + [json.dumps({"kind": "row", "index": i, "status": "run",
                                                    "passed": i == 3}) for i in indices]
        path.write_text("\n".join(lines) + "\n")
        return path

    a, b = shard("a.jsonl", 0, [0, 2]), shard("b.jsonl", 1, [1, 3])
    merged = k6.merge([a, b])
    assert merged["summary"]["survivor_indices"] == [3]
    with pytest.raises(ValueError):
        k6.merge([a])  # a gap
    with pytest.raises(ValueError):
        k6.merge([a, b, shard("c.jsonl", 1, [1, 3])])  # an overlap
    assert k6.shard_indices(10, 1, 3) == [1, 4, 7]


def test_the_error_stop_rule_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("instrument bug")

    monkeypatch.setattr(k6, "screen_one", broken)
    monkeypatch.setattr(k6, "ERROR_STOP_AFTER", 3)

    @dataclass
    class _Doc:
        strategy_id: str

    docs = [(i, _Doc(f"k6_{i:06d}")) for i in range(6)]
    header = {"study": "K6", "stage": "screen", "indices": list(range(6)), "n_documents": 6,
              "shard": {"k": 0, "of": 1}}
    out = k6.ShardFile(tmp_path / "e.jsonl", header, resume=False)
    assert k6.run_stage("screen", documents=docs, series=[], make=_make, out=out,
                        declared_true_n=1) == "errors"
    out.close()
    assert len(out.rows) == 3 and all(r["status"] == "error" for r in out.rows.values())
