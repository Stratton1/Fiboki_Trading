"""E-4's study script end to end on the starter bars: a HARNESS check.

Two years of HistData FX are not the universe E-4 names, and nothing here
asserts a size, a power or a verdict. Pinned: P0 and the main stage run through
the real engine and the real ladder, write the filed schema, reproduce byte for
byte apart from wall-clock fields, shard and merge into the single run, resume
from a checkpoint, and the main stage reads no holdout.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pooled_universe_study.py"
TINY = ["--folds", "2", "--spa-bootstraps", "30", "--stress-samples", "5"]

pytestmark = [pytest.mark.slow, pytest.mark.timeout(900)]


def _script():
    name = "pooled_universe_study"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _strip_wall(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_wall(v) for k, v in obj.items()
                if not k.startswith("wall_") and k not in ("merged_from", "shard")}
    if isinstance(obj, list):
        return [_strip_wall(v) for v in obj]
    return obj


def _p0(out: Path, *extra: str) -> dict[str, Any]:
    argv = ["p0", "--starter", "--timeframe", "H4", "--documents", "donchian_breakout_atr",
            "--replicates", "2", "--sr", "0", "0.5", *TINY, "--out", str(out), *extra]
    assert _script().main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def p0_runs(tmp_path_factory):
    d = tmp_path_factory.mktemp("e4_p0")
    return d, _p0(d / "a.json"), _p0(d / "b.json")


def test_p0_runs_end_to_end_and_writes_the_filed_schema(p0_runs) -> None:
    _, run, _ = p0_runs
    assert run["study"] == "E-4" and run["stage"] == "p0" and run["process"] == "pooled_block_bootstrap"
    assert run["evidence"] is False and run["partial"] is False
    assert run["config"]["external_trial_count"] == 21_960
    assert run["gate_set_version"] == "v2.1.0-calibrated"
    assert len(run["runs"]) == 4
    for row in run["runs"]:
        assert row["document"] == "donchian_breakout_atr" and len(row["sleeves"]) >= 2
        assert row["pooled"]["n_trades_effective"] <= row["pooled"]["n_trades_raw"]
    assert set(run["summary"]) == {"0", "0.5"}
    assert {"z_calibration_at_sr_0", "min_trl_gate_pass_at_sr_0", "size_family_wise_union_bound"} <= set(run["e4"])
    src = run["source"]["documents"][0]
    assert src["n_sleeves"] >= 2 and src["block_length_days"]["stationary"] >= 1.0


def test_p0_is_reproducible(p0_runs) -> None:
    _, a, b = p0_runs
    assert _strip_wall(a) == _strip_wall(b)


def test_p0_shards_merge_into_the_single_run_and_resume(p0_runs) -> None:
    e4 = _script()
    d, single, _ = p0_runs
    s0 = d / "s0.json"
    _p0(s0, "--replicate-range", "0", "1")
    s1 = d / "s1.json"
    _p0(s1, "--replicate-range", "1", "2")
    merged = e4.merge([s0, s1])
    assert _strip_wall(merged) == _strip_wall(single)
    with pytest.raises(ValueError, match="not covered"):
        e4.merge([s0])
    with pytest.raises(ValueError, match="both"):
        e4.merge([s0, s0, s1])
    # resume: the completed shard is read back, nothing re-run, same rows
    again = _p0(s0, "--replicate-range", "0", "1", "--resume")
    assert _strip_wall(again["runs"]) == _strip_wall(json.loads(s0.read_text())["runs"])
    with pytest.raises(ValueError, match="different run"):
        _p0(s0, "--replicate-range", "0", "1", "--resume", "--seed", "7")


def _main(out: Path, reports: Path, *extra: str) -> dict[str, Any]:
    argv = ["main", "--starter", "--seeds-only", "--documents", "donchian_breakout_atr",
            "tsmom_dual_horizon", *TINY, "--out", str(out), "--reports-dir", str(reports), *extra]
    assert _script().main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_main_stage_runs_every_candidate_reads_no_holdout_and_merges(tmp_path: Path) -> None:
    e4 = _script()
    single = _main(tmp_path / "all.json", tmp_path / "reports")
    assert single["stage"] == "main" and single["plan"]["n_candidates"] == 4
    assert [r["key"] for r in single["runs"]] == [
        "donchian_breakout_atr@H4", "donchian_breakout_atr@D1",
        "tsmom_dual_horizon@H4", "tsmom_dual_horizon@D1",
    ]
    for row in single["runs"]:
        assert row["status"] == "run", row
        assert row["verdict"] != "promote"
        assert row["rung_outcomes"]["6"] == "not_reached"
        assert row["external_trial_count"] == 20_904 + single["plan"]["planned_trials"] - row["grid_points"]
        report = json.loads(Path(row["report"]["path"]).read_text())
        assert report["strategy_content_hash"] == row["default_binding_content_hash"]
    k0 = _main(tmp_path / "k0.json", tmp_path / "r0", "--shard", "0", "2")
    k1 = _main(tmp_path / "k1.json", tmp_path / "r1", "--shard", "1", "2")
    assert [r["index"] for r in k0["runs"]] == [0, 2] and [r["index"] for r in k1["runs"]] == [1, 3]
    merged = e4.merge([tmp_path / "k0.json", tmp_path / "k1.json"])

    def comparable(doc):
        out = _strip_wall(doc)
        for r in out["runs"]:
            r.pop("report", None)
        return out

    assert comparable(merged) == comparable(single)
