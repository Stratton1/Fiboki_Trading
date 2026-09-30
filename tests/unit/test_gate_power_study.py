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
