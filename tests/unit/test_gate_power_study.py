"""E-1: the pre-registration parses, and the power-study skeleton runs at tiny scale.

The study measures the gates; it must not move them. So this test also pins
that GATE_SET_V2 is still the audited set the pre-registration names.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from fiboki.validation.gates import GATE_SET_V2, GateSet

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
    # Registered before exec: the script defines dataclasses, and a dataclass
    # looks its module up in sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_preregistration_is_complete_and_filed() -> None:
    """Filed 2026-09-30: the fields the draft left to the operator are now set,
    and once set they are frozen (a change here is a re-filing, not an edit)."""
    doc = json.loads(PREREG.read_text(encoding="utf-8"))
    assert doc["schema"] == "fiboki-preregistration:1"
    assert doc["id"] == "gate_calibration_e1"
    assert doc["injected_sharpe_grid"]["values"] == [0.0, 0.03, 0.05, 0.08, 0.12]
    assert doc["targets"] == {"size_max": 0.05, "power_at_sr_0_08_min": 0.5}
    assert {"H0_size", "H1_power"} <= set(doc["hypotheses"])
    assert "decision_rule" in doc and "v2.1.0-calibrated" in doc["decision_rule"]
    assert doc["status"].startswith("FILED 2026-09-30")
    assert doc["filed_at"] == "2026-09-30T20:40:00Z"
    assert doc["filed_commit"] == "efb5fce"
    assert doc["decision_date"] == "2026-10-02"  # after filing; before any result
    assert doc["decision_date"] > doc["filed_at"][:10]
    assert doc["code"]["gate_set_version_under_test"] == GATE_SET_V2.version


def test_the_filed_commit_is_in_this_repository_history() -> None:
    """filed_commit must name a commit that exists, and that commit must already
    carry the FILED status (the filing and its record are the same content)."""
    import subprocess

    doc = json.loads(PREREG.read_text(encoding="utf-8"))
    sha = doc["filed_commit"]
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                      capture_output=True).returncode != 0:
        pytest.skip("not a git checkout")
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
                      capture_output=True).returncode != 0:
        pytest.skip(f"commit {sha} not present in this checkout (shallow or unfetched)")
    shown = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{sha}:research/preregistration/gate_calibration_e1.json"],
        capture_output=True, text=True, check=True,
    ).stdout
    at_filing = json.loads(shown)
    assert at_filing["status"].startswith("FILED 2026-09-30")
    assert at_filing["filed_at"] == doc["filed_at"]
    assert at_filing["decision_date"] == doc["decision_date"]
    assert at_filing["filing_decisions"] == doc["filing_decisions"]


def test_no_gate_threshold_moved_while_e1_is_pending() -> None:
    """The thresholds E-1 will calibrate, as they stand (validation/gates.py)."""
    assert GATE_SET_V2.version == "v2.0.0-audit"
    assert GATE_SET_V2.by_name("min_trades").threshold == 400
    assert GATE_SET_V2.by_name("deflated_sharpe").threshold == 0.95
    assert GATE_SET_V2.by_name("parameter_plateau").threshold == 1.25


def test_the_skeleton_runs_at_tiny_scale(tmp_path: Path) -> None:
    out = tmp_path / "e1.json"
    code = _script().main([
        "--replicates", "2", "--sr", "0", "0.5", "--folds", "3",
        "--spa-bootstraps", "40", "--stress-samples", "5", "--out", str(out),
    ])
    assert code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["evidence"] is False  # the synthetic process is not evidence
    assert result["gate_set_version"] == GATE_SET_V2.version
    assert result["summary"]["0"]["role"] == "size"
    assert result["summary"]["0"]["n"] == 2
    # A per-period Sharpe of 0.5 on ~1,600 periods is an enormous edge: the
    # harness must be able to promote SOMETHING or it measures nothing.
    assert result["summary"]["0.5"]["promoted"] == 2
    assert len(result["runs"]) == 4


def test_the_skeleton_is_deterministic(tmp_path: Path) -> None:
    module = _script()
    args = ["--replicates", "1", "--sr", "0.5", "--folds", "3",
            "--spa-bootstraps", "40", "--stress-samples", "5"]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    module.main([*args, "--out", str(a)])
    module.main([*args, "--out", str(b)])
    ra, rb = (json.loads(p.read_text()) for p in (a, b))
    # Wall-clock fields (every key starting "wall_") are the only permitted difference.
    for r in (ra, rb):
        for key in [k for k in r if k.startswith("wall_")]:
            r.pop(key)
    assert ra == rb


@pytest.mark.parametrize("process", ["block_bootstrap_real_returns", "perturbed_price_paths"])
def test_the_evidence_processes_are_not_faked(process: str) -> None:
    """Without real bars there is no stand-in: an evidence process refuses.

    Both are implemented now (tests/unit/test_gate_power_study_real.py); what
    this pins is that neither can be produced from the synthetic machinery.
    """
    module = _script()
    with pytest.raises(module.RealDataRequired, match="built from real bars"):
        module.make_evaluator(process, 0.08, 1)


def test_the_default_run_records_the_audited_fingerprint(tmp_path: Path) -> None:
    out = tmp_path / "e1.json"
    _script().main([
        "--replicates", "1", "--sr", "0.5", "--folds", "3",
        "--spa-bootstraps", "40", "--stress-samples", "5", "--out", str(out),
    ])
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["gate_set_version"] == "v2.0.0-audit"
    assert result["gate_set_fingerprint"] == GATE_SET_V2.fingerprint()
    # Measuring mode (the default) runs on the 150-trade floor every judged set
    # admits; the audited set's own 400-trade gate decides at rung 0.
    assert result["ladder_mode"] == "measuring"
    assert result["config"]["min_trades"] == 150
    assert result["judged_gate_sets"]["v2.0.0-audit"] == GATE_SET_V2.fingerprint()
    # every known set except the one that requires 8 folds (this run has 3)
    assert set(result["judged_gate_sets"]) == set(_script().GATE_SETS) - {"c_hit_8fold"}
    assert set(result["summary_by_gate_set"]) == set(result["judged_gate_sets"])
    for run in result["runs"]:
        assert set(run["under"]) == set(result["judged_gate_sets"])
        # the top level is the primary set's own verdict, repeated under its name
        primary = run["under"]["v2.0.0-audit"]
        assert (run["promoted"], run["binding"], run["gate_status"]) == (
            primary["promoted"], primary["binding"], primary["gate_status"]
        )


def test_fail_fast_mode_is_the_ladder_as_shipped_and_agrees_with_measuring(tmp_path: Path) -> None:
    """``--ladder-mode fail_fast`` runs ValidationLadder.run under one set on the
    audited floor; its verdicts are the measuring run's verdicts under that set."""
    module = _script()
    args = ["--replicates", "2", "--sr", "0", "0.5", "--folds", "3",
            "--spa-bootstraps", "40", "--stress-samples", "5"]
    ff, me = tmp_path / "ff.json", tmp_path / "me.json"
    assert module.main([*args, "--ladder-mode", "fail_fast", "--out", str(ff)]) == 0
    assert module.main([*args, "--out", str(me)]) == 0
    fail_fast = json.loads(ff.read_text(encoding="utf-8"))
    measuring = json.loads(me.read_text(encoding="utf-8"))
    assert fail_fast["ladder_mode"] == "fail_fast"
    assert fail_fast["config"]["min_trades"] == 400
    assert fail_fast["judged_gate_sets"] == {"v2.0.0-audit": GATE_SET_V2.fingerprint()}
    assert "under" not in fail_fast["runs"][0] and "summary_by_gate_set" not in fail_fast
    assert fail_fast["summary"] == measuring["summary"]
    for a, b in zip(fail_fast["runs"], measuring["runs"], strict=True):
        assert (a["sr"], a["replicate"]) == (b["sr"], b["replicate"])
        assert (a["promoted"], a["verdict"], a["binding_kind"], a["binding"]) == (
            b["promoted"], b["verdict"], b["binding_kind"], b["binding"]
        )
    with pytest.raises(ValueError, match="not judged under"):
        module.rows_under(fail_fast["runs"], "c_min_trl")
    assert module.rows_under(measuring["runs"], "c_min_trl")[0]["under"]


def test_a_candidate_gate_set_runs_on_the_tiny_synthetic_process(tmp_path: Path) -> None:
    from fiboki.validation.gates import GATE_SET_V2_1_CANDIDATES

    out = tmp_path / "e1_c_min_trl.json"
    code = _script().main([
        "--replicates", "2", "--sr", "0", "0.5", "--folds", "3", "--spa-bootstraps", "40",
        "--stress-samples", "5", "--gate-set", "c_min_trl", "--out", str(out),
    ])
    assert code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    candidate = GATE_SET_V2_1_CANDIDATES["c_min_trl"]
    assert result["gate_set_version"] == "v2.1.0-candidate:c_min_trl"
    assert result["gate_set_fingerprint"] == candidate.fingerprint()
    assert result["config"]["min_trades"] == 150  # the MinTRL metric's floor
    for run in result["runs"]:
        assert "min_track_record" in run["gate_status"]
        assert "min_trades" not in run["gate_status"]
        assert "min_track_record" in run["gate_values"]  # replaced gates are recorded
    assert result["summary"]["0.5"]["promoted"] == 2


@pytest.mark.parametrize("name", ["v2.1.0-calibrated", "GATE_SET_V2", "c_bogus"])
def test_an_unknown_gate_set_is_refused(tmp_path: Path, name: str) -> None:
    module = _script()
    with pytest.raises(SystemExit):
        module.main(["--replicates", "1", "--sr", "0", "--gate-set", name,
                     "--out", str(tmp_path / "x.json")])
    with pytest.raises(ValueError, match="unknown gate set"):
        module.gate_set_named(name)
    assert not (tmp_path / "x.json").exists()


def test_judge_rows_reproduces_every_judged_set_and_can_judge_a_new_one(tmp_path: Path) -> None:
    """A measuring row carries enough to re-judge any gate set: for the sets the
    run judged, judge_rows must equal what the ladder reported under them."""
    module = _script()
    out = tmp_path / "e1.json"
    assert module.main([
        "--replicates", "3", "--sr", "0", "0.1", "0.5", "--folds", "3",
        "--spa-bootstraps", "40", "--stress-samples", "5", "--out", str(out),
    ]) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))["runs"]
    keys = ("promoted", "verdict", "binding_kind", "binding", "gate_status", "gate_values")
    for name, gate_set in module.judged_gate_sets(3).items():
        judged = module.judge_rows(rows, gate_set)
        recorded = module.rows_under(rows, name)
        for j, r in zip(judged, recorded, strict=True):
            assert {k: j[k] for k in keys} == {k: r[k] for k in keys}, name
    # A set nobody named at run time: the audited set with the plateau gate removed.
    without_plateau = GateSet(
        version="v2.1.0-candidate:test_no_plateau",
        gates=tuple(g for g in GATE_SET_V2.gates if g.name != "parameter_plateau"),
    )
    judged = module.judge_rows(rows, without_plateau)
    assert all("parameter_plateau" not in j["gate_status"] for j in judged)
    # Removing a gate can only promote more, never fewer.
    assert sum(j["promoted"] for j in judged) >= sum(r["promoted"] for r in rows)
    # A fail-fast row carries no measurement and says so.
    ff = tmp_path / "ff.json"
    assert module.main(["--replicates", "1", "--sr", "0.5", "--folds", "3", "--spa-bootstraps", "40",
                        "--stress-samples", "5", "--ladder-mode", "fail_fast", "--out", str(ff)]) == 0
    with pytest.raises(ValueError, match="no measurement"):
        module.judge_rows(json.loads(ff.read_text(encoding="utf-8"))["runs"], GATE_SET_V2)


def test_the_eight_fold_candidate_is_judged_only_at_eight_folds(tmp_path: Path) -> None:
    module = _script()
    assert "c_hit_8fold" not in module.judged_gate_sets(5)
    assert "c_hit_8fold" in module.judged_gate_sets(8)
    assert set(module.judged_gate_sets(8)) == set(module.GATE_SETS)
    with pytest.raises(SystemExit):  # as the primary set, it insists on its fold count
        module.main(["--replicates", "1", "--sr", "0.5", "--gate-set", "c_hit_8fold", "--folds", "5",
                     "--out", str(tmp_path / "x.json")])
    out = tmp_path / "e8.json"
    assert module.main(["--replicates", "1", "--sr", "0.5", "--folds", "8", "--spa-bootstraps", "40",
                        "--stress-samples", "5", "--gate-set", "c_hit_8fold", "--out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["config"]["walk_forward_folds"] == 8
    assert set(result["judged_gate_sets"]) == set(module.GATE_SETS)
    (row,) = result["runs"]
    assert row["gate_status"]["oos_window_hit_rate"] in ("pass", "fail")
    # each fold evaluates the selected and the fixed parameters on its test window
    assert len(row["trade_counts"]["wf_test"]) == 16


PREREG_E2 = ROOT / "research" / "preregistration" / "gate_calibration_e2.json"


def test_the_e2_draft_names_the_candidate_sets_as_they_are_in_code() -> None:
    """The draft's fingerprints are the candidate sets' fingerprints: a threshold
    change in code without a re-draft would be a threshold moved after the
    expectations were written. Until it is FILED, no candidate rate is quotable."""
    from fiboki.validation.gates import CANDIDATE_LADDER_FOLDS, GATE_SET_V2_1_CANDIDATES

    doc = json.loads(PREREG_E2.read_text(encoding="utf-8"))
    assert doc["schema"] == "fiboki-preregistration:1"
    assert doc["id"] == "gate_calibration_e2"
    assert doc["code"]["base_gate_set"].endswith(GATE_SET_V2.fingerprint())
    assert set(doc["candidate_gate_sets"]) == set(GATE_SET_V2_1_CANDIDATES)
    for name, entry in doc["candidate_gate_sets"].items():
        assert entry["fingerprint"] == GATE_SET_V2_1_CANDIDATES[name].fingerprint(), name
        assert entry["expectation_stated_before_results"]
    assert "walk_forward_folds = 8" in doc["candidate_gate_sets"]["c_hit_8fold"]["with"]
    assert CANDIDATE_LADDER_FOLDS == {"c_hit_8fold": 8}
    assert doc["targets"] == {"size_max": 0.05, "power_at_sr_0_08_min": 0.5}
    assert "c_dsr_family is never admitted" in doc["decision_rule"]
    if doc["status"].startswith("DRAFT"):
        assert doc["filed_at"] is None and doc["filed_commit"] is None and doc["decision_date"] is None
    else:
        assert doc["status"].startswith("FILED") and doc["filed_at"] and doc["filed_commit"]
        assert doc["decision_date"] > doc["filed_at"][:10]


def test_the_equivalence_check_passes_a_faithful_replay_and_fails_a_tampered_one(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("e1_equivalence", ROOT / "scripts" / "e1_equivalence.py")
    eq = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(eq)
    module = _script()
    args = ["--replicates", "3", "--sr", "0", "0.1", "0.5", "--folds", "3",
            "--spa-bootstraps", "40", "--stress-samples", "5"]
    ff, me = tmp_path / "ff.json", tmp_path / "me.json"
    assert module.main([*args, "--ladder-mode", "fail_fast", "--out", str(ff)]) == 0
    assert module.main([*args, "--out", str(me)]) == 0
    stored, replay = (json.loads(p.read_text(encoding="utf-8")) for p in (ff, me))
    report = eq.compare(stored, replay)
    assert report["equivalent"] is True and report["rows_compared"] == 9
    assert eq.main([str(ff), str(me), "--out", str(tmp_path / "r.json")]) == 0
    tampered = json.loads(json.dumps(replay))
    row = next(r for r in tampered["runs"] if r["promoted"])
    row["under"]["v2.0.0-audit"]["promoted"] = False
    report = eq.compare(stored, tampered)
    assert report["equivalent"] is False and len(report["disagreements"]) == 1
    with pytest.raises(ValueError, match="fail-fast"):
        eq.compare(replay, replay)
