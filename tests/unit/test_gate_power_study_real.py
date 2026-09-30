"""E-1's two evidence processes: block-bootstrapped real returns and perturbed real paths.

``research/preregistration/gate_calibration_e1.json`` names them as the only
data-generating processes that count as evidence. These tests pin the pieces a
reader must be able to trust before any E-1 number is read:

* the block length grows with serial dependence (Politis-White / Patton);
* the bootstrap removes the real edge and injects exactly the edge asked for;
* the overlay mark-up hits its target Sharpe exactly, on the sample it is
  calibrated on;
* a perturbed path keeps the real start, calendar and bar count, is OHLC-
  coherent on every bar, and never draws from the holdout;
* both processes run end to end through the real ladder (and, for the paths,
  the real engine) on the starter bars, write the full schema, and reproduce
  byte for byte apart from wall-clock fields.

Starter-data runs here are harness checks. Two years of HistData FX are not the
validated universe, and nothing below asserts a size or a power.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.validation.evaluation import DateWindow
from fiboki.validation.gates import GATE_SET_V2

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "research" / "preregistration" / "gate_calibration_e1.json"
SCRIPT = ROOT / "scripts" / "gate_power_study.py"


def _script():
    name = "gate_power_study"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


def _strip_wall(obj: Any) -> Any:
    """Everything except wall-clock fields, which are the only permitted difference."""
    if isinstance(obj, dict):
        return {k: _strip_wall(v) for k, v in obj.items() if not k.startswith("wall_")}
    if isinstance(obj, list):
        return [_strip_wall(v) for v in obj]
    return obj


# ------------------------------------------------------------ pre-registration


def test_the_search_size_is_filled_from_k5_and_the_filing_decisions_follow_it() -> None:
    doc = json.loads(PREREG.read_text(encoding="utf-8"))
    size = doc["search_size"]
    assert size["campaign_candidates"] == 106
    assert size["grid_points_per_candidate"] == 8
    assert "K5" in size["filing_note"] and "operator" in size["filing_note"]
    # Filed: the external trial count per candidate is K5's true count less the
    # candidate's own grid, exactly as the search_size rule prescribes.
    fd = doc["filing_decisions"]
    k5_true_trials = 20_904  # research/reports/RESEARCH_LEDGER.md, K5 result
    assert fd["external_trial_count_per_candidate"] == k5_true_trials - size["grid_points_per_candidate"]
    assert "1 - (1 - p0)^106" in fd["size_basis"]
    assert fd["process_order"].startswith("block_bootstrap_real_returns first")
    assert doc["filed_at"] is not None and doc["filed_commit"] is not None
    status = {p["name"]: p["status"] for p in doc["data_generating_processes"]}
    for name in ("block_bootstrap_real_returns", "perturbed_price_paths"):
        assert status[name] == "implemented in scripts/gate_power_study.py (2026-09-30)"
    # The instrument uses the filed search size, not one of its own.
    from fiboki.validation.evaluation import ParameterGrid

    module = _script()
    assert ParameterGrid.from_axes(module.BOOTSTRAP_AXES).full_size() == 8
    assert module.campaign_grid_shape() == (8, 2)
    assert set(module.campaign_sweep_axes()) >= {
        "donchian_breakout_atr", "ichimoku_kumo_trend", "macd_ema_trend_hybrid",
        "fib_golden_pocket_pullback", "rsi_band_mean_reversion",
    }


# ----------------------------------------------------------------- block length


def _ar1(phi: float, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n)
    x = np.empty(n)
    x[0] = e[0]
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


def test_the_block_length_grows_with_autocorrelation_and_is_deterministic() -> None:
    module = _script()
    est = {phi: module.block_length_for(_ar1(phi, 2_000, 7)) for phi in (0.0, 0.5, 0.9)}
    b = {phi: e["stationary"] for phi, e in est.items()}
    assert b[0.0] < b[0.5] < b[0.9]
    assert b[0.9] > 5.0  # strong dependence -> long blocks
    assert est[0.5] == module.block_length_for(_ar1(0.5, 2_000, 7))
    assert "Patton" in est[0.5]["method"]


def test_the_stationary_stream_has_the_requested_mean_block_length() -> None:
    module = _script()
    stream = module.StationaryStream(1_000, 8.0, np.random.default_rng(3))
    idx = np.concatenate([stream.draw(5_000), stream.draw(15_000)])
    breaks = np.flatnonzero(np.diff(idx) != 1)
    breaks = breaks[idx[breaks] != 999]  # a circular wrap continues the block
    assert 7.0 < idx.size / (breaks.size + 1) < 9.0
    counts = np.bincount(idx, minlength=1_000)
    assert counts.min() > 0  # every observation reachable


# ---------------------------------------------------------- the bootstrap shift


def _fat_tailed_seed(module: Any, n: int = 500) -> Any:
    rng = np.random.default_rng(11)
    ret = 0.002 + 0.01 * rng.standard_t(3, size=n)  # a real edge and fat tails
    data = {
        "ret": ret,
        "spread": np.full(n, 0.0001), "slippage": np.full(n, 0.00005),
        "financing": np.zeros(n), "commission": np.zeros(n),
        "gap_s": np.full(n, 3_600.0), "hold_s": np.full(n, 7_200.0),
        "long": rng.random(n) < 0.5,
        "instrument": np.array(["EURUSD"] * n, dtype=object),
        "exit_reason": np.array(["stop_loss"] * n, dtype=object),
    }
    return module.SeedSeries("fat", data, module.block_length_for(ret), [])


@pytest.mark.parametrize("sr", [0.0, 0.5])
def test_demeaning_removes_the_real_edge_and_the_shift_injects_exactly_sr(sr: float) -> None:
    module = _script()
    seed = _fat_tailed_seed(module)
    start = pd.Timestamp("2020-01-01", tz="UTC")
    end = pd.Timestamp("2023-01-01", tz="UTC")
    rep = module.BootstrapReplicate(seed, 5, start, end, 60)
    grid, index, distance = module.bootstrap_grid()
    ev = module.BootstrapEvaluator(rep, sr, index, distance, "v")(
        dict(module.BOOTSTRAP_DEFAULTS), DateWindow("all", start, end)
    )
    r = np.array([t.net_pnl for t in ev.trades]) / module.NOTIONAL
    assert r.size > 20_000
    se = r.std(ddof=1) / np.sqrt(r.size)
    target = sr * seed.sd
    assert abs(r.mean() - target) < 4 * se  # the seed's own +0.2% edge is gone
    assert abs(r.mean() / r.std(ddof=1) - sr) < 0.03
    # Aligned period basis whose column sums to the window's net profit.
    assert ev.returns_basis == "period" and ev.returns.size == int((end - start).total_seconds() // 3600)
    assert ev.returns.sum() == pytest.approx(ev.net_profit)
    # The plateau: full shift within one step, linear decay beyond.
    assert [module.plateau_factor(d) for d in range(4)] == [1.0, 1.0, pytest.approx(0.85), pytest.approx(0.7)]


def test_grid_points_share_a_calendar_and_correlate_as_declared() -> None:
    module = _script()
    seed = _fat_tailed_seed(module)
    start = pd.Timestamp("2020-01-01", tz="UTC")
    end = pd.Timestamp("2022-01-01", tz="UTC")
    rep = module.BootstrapReplicate(seed, 9, start, end, 60)
    grid, index, distance = module.bootstrap_grid()
    evaluator = module.BootstrapEvaluator(rep, 0.0, index, distance, "v")
    window = DateWindow("all", start, end)
    a, b = (evaluator(p, window) for p in grid.points()[:2])
    assert a.n_trades == b.n_trades and a.returns.size == b.returns.size
    ra = np.array([t.net_pnl for t in a.trades])
    rb = np.array([t.net_pnl for t in b.trades])
    assert abs(np.corrcoef(ra, rb)[0, 1] - module.CROSS_GRID_CORRELATION) < 0.05


# ---------------------------------------------------------------- the overlay


@pytest.mark.parametrize("sr", [0.0, 0.08, 0.5])
def test_the_markup_hits_the_target_sharpe_exactly(sr: float) -> None:
    module = _script()
    rng = np.random.default_rng(1)
    pnl = -3.0 + 20.0 * rng.standard_t(4, size=300)
    marks = rng.random(300) < module.OVERLAY_FRACTION
    m = module.solve_markup(pnl, marks, sr)
    adjusted = pnl + m * marks
    assert adjusted.mean() / adjusted.std(ddof=1) == pytest.approx(sr, abs=1e-9)


def test_an_unreachable_sharpe_is_refused_not_approximated() -> None:
    module = _script()
    rng = np.random.default_rng(2)
    marks = rng.random(200) < 0.5
    with pytest.raises(ValueError, match="no mark-up"):
        module.solve_markup(rng.standard_normal(200), marks, 5.0)
    with pytest.raises(ValueError, match="no trade carries"):
        module.solve_markup(rng.standard_normal(200), np.zeros(200, dtype=bool), 0.1)


# -------------------------------------------------------------- perturbed paths


@pytest.fixture(scope="module")
def eurusd_h4() -> Any:
    module = _script()
    series, skipped, _ = module.load_series(
        starter=True, data_root=None, instruments=["EURUSD"], timeframe=Timeframe.H4,
        bars_from=None,
    )
    assert not skipped
    return series[0]


def test_a_perturbed_path_keeps_the_start_the_calendar_and_ohlc_coherence(eurusd_h4) -> None:
    module = _script()
    real = eurusd_h4.frame
    path, info = module.perturb_path(real, eurusd_h4.research_window, np.random.default_rng(4))
    assert len(path) == len(real) == info["n_bars"]
    assert path.index.equals(real.index)
    assert path.iloc[0][["open", "high", "low", "close"]].tolist() == real.iloc[0][
        ["open", "high", "low", "close"]
    ].tolist()
    o, h, lo, c = (path[k].to_numpy() for k in ("open", "high", "low", "close"))
    assert (lo <= np.minimum(o, c)).all() and (np.maximum(o, c) <= h).all()
    assert info["coherence_clamped"] == 0
    assert not np.allclose(c, real["close"].to_numpy())  # it is a different path
    assert (path["price_basis"] == real["price_basis"]).all()


def test_a_perturbed_path_never_draws_from_the_holdout() -> None:
    module = _script()
    idx = pd.date_range("2020-01-01", periods=1_000, freq="4h", tz="UTC")
    close = 1.0 + 0.0001 * np.arange(1_000)
    close[800:] *= 1.5  # a holdout jump no research bar contains
    frame = pd.DataFrame({"open": close, "high": close * 1.001, "low": close * 0.999,
                          "close": close}, index=idx)
    research = DateWindow("research", idx[0], idx[800])
    path, _ = module.perturb_path(frame, research, np.random.default_rng(5))
    ratio = path["close"].to_numpy()[1:] / path["close"].to_numpy()[:-1]
    assert ratio.max() < 1.01


# ------------------------------------------------------------------ end to end

BB_ARGS = [
    "--process", "block_bootstrap_real_returns", "--starter",
    "--instruments", "EURUSD", "GBPUSD", "--replicates", "2", "--sr", "0", "0.5",
    "--folds", "3", "--spa-bootstraps", "40", "--stress-samples", "5",
]
PP_ARGS = [
    "--process", "perturbed_price_paths", "--starter", "--instruments", "EURUSD",
    "--documents", "donchian_breakout_atr", "--replicates", "1", "--sr", "0.08",
    "--folds", "3", "--spa-bootstraps", "40", "--stress-samples", "5",
    "--calibration-paths", "2",
]


def _twice(tmp_path_factory: Any, args: list[str], tag: str) -> tuple[dict, dict]:
    module = _script()
    out = []
    for i in range(2):
        path = tmp_path_factory.mktemp(f"{tag}{i}") / "e1.json"
        assert module.main([*args, "--out", str(path)]) == 0
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out[0], out[1]


@pytest.fixture(scope="module")
def bootstrap_runs(tmp_path_factory) -> tuple[dict, dict]:
    return _twice(tmp_path_factory, BB_ARGS, "bb")


@pytest.fixture(scope="module")
def path_runs(tmp_path_factory) -> tuple[dict, dict]:
    return _twice(tmp_path_factory, PP_ARGS, "pp")


def _common_schema(result: dict, process: str, srs: list[str], n_runs: int) -> None:
    assert result["process"] == process
    assert result["evidence"] is False  # a starter pilot is never evidence
    assert "not evidence" in result["note"].lower()
    assert result["gate_set_version"] == GATE_SET_V2.version
    assert set(result["summary"]) == set(srs) == set(result["secondary"])
    assert len(result["runs"]) == n_runs
    gate_names = {g.name for g in GATE_SET_V2.gates}
    for row in result["runs"]:
        assert set(row["gate_status"]) == gate_names
        assert row["sanity_n_trades"] is not None and "research" in row["trade_counts"]
    for sec in result["secondary"].values():
        assert set(sec["gate_rejection_rate"]) == gate_names
        assert sec["trade_counts_per_window"]["sanity_defaults"]["n"] >= 1
    for key in ("wall_seconds", "wall_seconds_per_ladder_run", "wall_seconds_source_build"):
        assert result[key] >= 0.0
    source = result["source"]
    assert source["mode"] == "starter" and source["calendar"]["n_events"] > 0
    for s in source["series"]:
        assert "sha256=" in s["source_ref"]
        # the holdout is fixed from the bars and excluded from the source
        assert s["research_window"]["end"] == s["holdout_window_excluded"]["start"]
        assert s["research_window"]["end"] < s["data_end"]


@pytest.mark.slow
def test_block_bootstrap_runs_end_to_end_through_the_real_ladder(bootstrap_runs) -> None:
    result, _ = bootstrap_runs
    _common_schema(result, "block_bootstrap_real_returns", ["0", "0.5"], 4)
    pool = result["source"]["pool"]
    assert {s["strategy_id"] for s in pool["seeds"]} | {
        e["strategy_id"] for e in pool["excluded_seeds"]
    } == {"donchian_breakout_atr", "fib_golden_pocket_pullback", "ichimoku_kumo_trend",
          "macd_ema_trend_hybrid", "rsi_band_mean_reversion", "tsmom_dual_horizon"}
    for s in pool["seeds"]:
        assert s["block_length"]["stationary"] >= 1.0
        for cell in s["cells"]:
            assert len(cell["strategy_content_hash"]) == 64 and cell["ledger_sha256"]
    # every run names the seed it drew from, and seeds rotate across replicates
    assert [r["seed_document"] for r in result["runs"][:2]] == [
        s["strategy_id"] for s in pool["seeds"][:2]
    ]
    for sr in ("0", "0.5"):
        assert 0 <= result["summary"][sr]["promoted"] <= 2


@pytest.mark.slow
def test_block_bootstrap_is_reproducible(bootstrap_runs) -> None:
    a, b = bootstrap_runs
    assert _strip_wall(a) == _strip_wall(b)


@pytest.mark.slow
def test_perturbed_paths_run_end_to_end_through_the_real_engine(path_runs, eurusd_h4) -> None:
    result, _ = path_runs
    _common_schema(result, "perturbed_price_paths", ["0.08"], 1)
    (row,) = result["runs"]
    assert row["cell"] == "donchian_breakout_atr@EURUSD"
    assert row["path"]["n_bars"] == len(eurusd_h4.frame)
    assert row["path"]["coherence_clamped"] == 0
    cal = row["calibration"]
    assert cal["status"] == "calibrated"
    assert cal["calibration_per_trade_sharpe"] == pytest.approx(0.08, abs=1e-9)
    assert len(cal["calibration_path_sha256"]) == 2
    assert row["path"]["path_sha256"] not in cal["calibration_path_sha256"]
    assert row["evaluations"] >= 1 and row["sanity_n_trades"] > 0


@pytest.mark.slow
def test_perturbed_paths_are_reproducible(path_runs) -> None:
    a, b = path_runs
    assert _strip_wall(a) == _strip_wall(b)


def test_an_evidence_process_without_bars_refuses(tmp_path: Path) -> None:
    module = _script()
    with pytest.raises(module.RealDataRequired):
        module.main(["--process", "perturbed_price_paths", "--out", str(tmp_path / "x.json")])
    with pytest.raises(SystemExit):
        module.main(["--starter", "--out", str(tmp_path / "y.json")])


# ------------------------------------------------------------ shards and resume


def _strip_run_bookkeeping(doc: dict) -> dict:
    """Everything a merged result must reproduce of the single run: not wall-clock,
    not the shard/merge bookkeeping, and not the data-root spelling."""
    out = {k: v for k, v in _strip_wall(doc).items()
           if k not in ("shard", "partial", "merged_from")}
    if "source" in out:
        out["source"] = {k: v for k, v in out["source"].items() if k != "data_root"}
    return out


@pytest.mark.slow
def test_shards_merge_into_the_single_run_byte_for_byte(bootstrap_runs, tmp_path: Path) -> None:
    """Two shards of one replicate each, merged, equal the unsharded run's rows and rates."""
    module = _script()
    whole, _ = bootstrap_runs
    shards = []
    for start in (0, 1):
        path = tmp_path / f"shard{start}.json"
        assert module.main([*BB_ARGS, "--replicate-range", str(start), str(start + 1),
                            "--out", str(path)]) == 0
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["shard"] == {"start": start, "end": start + 1, "of": 2}
        assert doc["partial"] is False
        assert {r["replicate"] for r in doc["runs"]} == {start}
        shards.append(path)
    merged_path = tmp_path / "merged.json"
    assert module.main(["--merge", *map(str, shards), "--out", str(merged_path)]) == 0
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    assert merged["merged_from"] == [str(p) for p in shards]
    assert _strip_run_bookkeeping(merged) == _strip_run_bookkeeping(whole)


@pytest.mark.slow
def test_a_merge_refuses_gaps_overlaps_partials_and_other_studies(bootstrap_runs, tmp_path: Path) -> None:
    module = _script()
    whole, _ = bootstrap_runs
    only_first = tmp_path / "first.json"
    assert module.main([*BB_ARGS, "--replicate-range", "0", "1", "--out", str(only_first)]) == 0
    with pytest.raises(ValueError, match="not covered"):
        module.merge_shards([only_first])
    with pytest.raises(ValueError, match="in both"):
        module.merge_shards([only_first, only_first])
    partial = tmp_path / "partial.json"
    doc = json.loads(only_first.read_text(encoding="utf-8"))
    partial.write_text(json.dumps({**doc, "partial": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="partial"):
        module.merge_shards([partial])
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**doc, "gate_set_fingerprint": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        module.merge_shards([only_first, other])


@pytest.mark.slow
def test_an_interrupted_run_resumes_from_its_checkpoint(bootstrap_runs, tmp_path: Path) -> None:
    """A checkpoint holding replicate 0 only, resumed, yields the full run."""
    module = _script()
    whole, _ = bootstrap_runs
    out = tmp_path / "e1.json"
    # Fabricate the checkpoint an interrupted 2-replicate run leaves: the header
    # of a real run over the same shard, with replicate 0's rows and partial=True.
    first = tmp_path / "first.json"
    assert module.main([*BB_ARGS, "--replicate-range", "0", "1", "--out", str(first)]) == 0
    doc = json.loads(first.read_text(encoding="utf-8"))
    checkpoint = {**doc, "shard": {"start": 0, "end": 2, "of": 2}, "partial": True}
    out.write_text(json.dumps(checkpoint), encoding="utf-8")
    assert module.main([*BB_ARGS, "--resume", "--out", str(out)]) == 0
    resumed = json.loads(out.read_text(encoding="utf-8"))
    assert resumed["partial"] is False
    assert _strip_run_bookkeeping(resumed) == _strip_run_bookkeeping(whole)
    # A checkpoint from a different configuration is refused, not silently extended.
    out.write_text(json.dumps({**checkpoint, "config": {**checkpoint["config"], "seed": 1}}),
                   encoding="utf-8")
    with pytest.raises(ValueError, match="different run"):
        module.main([*BB_ARGS, "--resume", "--out", str(out)])


def test_a_replicate_range_outside_the_study_is_refused(tmp_path: Path) -> None:
    module = _script()
    for bad in (["2", "3"], ["1", "1"], ["-1", "1"]):
        with pytest.raises(SystemExit):
            module.main(["--replicates", "2", "--sr", "0", "--replicate-range", *bad,
                         "--out", str(tmp_path / "x.json")])
