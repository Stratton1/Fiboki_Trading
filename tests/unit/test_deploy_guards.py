"""The CI gates, tested. A guard nobody has seen catch anything is decoration.

Two scripts are covered:

``scripts/check_live_flags.py``
    V1 shipped ``FIBOKEI_LIVE_EXECUTION_ENABLED: "true"`` inside a committed
    ``render.yaml`` and it stayed there for months. The planted-literal test
    below uses that exact line, misspelling and all.

``scripts/lockfile.py``
    Records and verifies the resolved environment, so a stored result is
    attributable to an environment rather than to a hope.

The CI workflow itself is also asserted structurally: deployment must be gated
on green CI rather than running on push, which is the thing V1 got wrong.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def live_flags():
    return load("check_live_flags")


@pytest.fixture(scope="module")
def lockfile():
    return load("lockfile")


# ============================================================ live-flag check

#: THE line, verbatim, from V1's render.yaml. Note FIBOKEI, not FIBOKI.
V1_RENDER_YAML = """\
services:
  - type: web
    name: fiboki-api
    envVars:
      - key: FIBOKEI_LIVE_EXECUTION_ENABLED
        value: "true"
      - key: PYTHON_VERSION
        value: "3.11"
"""


def test_the_check_catches_the_exact_v1_planted_literal(tmp_path, live_flags):
    """The regression test for the incident this script exists to prevent."""
    (tmp_path / "render.yaml").write_text(V1_RENDER_YAML, encoding="utf-8")
    findings = live_flags.scan([tmp_path])
    assert len(findings) == 1
    assert findings[0].key == "FIBOKEI_LIVE_EXECUTION_ENABLED"
    assert findings[0].value == '"true"'
    assert live_flags.main([str(tmp_path)]) == 1


def test_the_check_tolerates_the_misspelling_and_any_prefix(tmp_path, live_flags):
    """A check that only matched the CORRECT spelling would have missed the
    exact bug it was written for."""
    for name, key in [
        ("a.env", "FIBOKEI_LIVE_EXECUTION_ENABLED"),
        ("b.env", "FIBOKI_LIVE_EXECUTION_ENABLED"),
        ("c.env", "LIVE_EXECUTION_ENABLED"),
        ("d.env", "APP_ENABLE_LIVE_TRADING"),
        ("e.env", "ALLOW_LIVE_ORDERS"),
        ("f.env", "REAL_MONEY_ENABLED"),
    ]:
        (tmp_path / name).write_text(f"{key}=true\n", encoding="utf-8")
    findings = live_flags.scan([tmp_path])
    assert len(findings) == 6


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "on", '"true"', "'1'"])
def test_every_truthy_spelling_is_caught(tmp_path, live_flags, value):
    (tmp_path / "x.env").write_text(f"FIBOKI_LIVE_EXECUTION_ENABLED={value}\n", encoding="utf-8")
    assert len(live_flags.scan([tmp_path])) == 1


@pytest.mark.parametrize("value", ["false", "0", "no", '""', "off"])
def test_a_falsey_literal_is_allowed(tmp_path, live_flags, value):
    (tmp_path / "x.env").write_text(f"FIBOKI_LIVE_EXECUTION_ENABLED={value}\n", encoding="utf-8")
    assert live_flags.scan([tmp_path]) == []


def test_a_runtime_reference_is_not_flagged(tmp_path, live_flags):
    """``${LIVE_ENABLED}`` is resolved at deploy time, not baked in."""
    (tmp_path / "compose.yml").write_text(
        "environment:\n  FIBOKI_LIVE_EXECUTION_ENABLED: ${LIVE_ENABLED}\n", encoding="utf-8"
    )
    assert live_flags.scan([tmp_path]) == []


def test_an_unrelated_flag_is_not_flagged(tmp_path, live_flags):
    (tmp_path / "x.env").write_text(
        "FIBOKI_LIVE_RELOAD=true\nDEBUG=true\nENABLE_METRICS=true\n", encoding="utf-8"
    )
    assert live_flags.scan([tmp_path]) == []


def test_documentation_can_opt_out_explicitly(tmp_path, live_flags):
    (tmp_path / "README.env").write_text(
        'FIBOKI_LIVE_EXECUTION_ENABLED=true  # live-flag-check: allow\n', encoding="utf-8"
    )
    assert live_flags.scan([tmp_path]) == []


def test_the_scan_skips_virtualenvs_and_caches(tmp_path, live_flags):
    venv = tmp_path / ".venv" / "cfg"
    venv.mkdir(parents=True)
    (venv / "x.env").write_text("FIBOKI_LIVE_EXECUTION_ENABLED=true\n", encoding="utf-8")
    assert live_flags.scan([tmp_path]) == []


def test_the_repository_itself_is_clean(live_flags):
    """This assertion is the point of the whole script."""
    findings = live_flags.scan([REPO_ROOT])
    assert findings == [], "\n".join(f.render(REPO_ROOT) for f in findings)


def test_the_check_exits_zero_on_a_clean_tree(tmp_path, live_flags):
    (tmp_path / "ok.yml").write_text("key: value\n", encoding="utf-8")
    assert live_flags.main([str(tmp_path)]) == 0


# =================================================================== lockfile


def test_recording_then_verifying_the_current_environment(tmp_path, lockfile):
    path = tmp_path / "requirements.lock"
    lock = lockfile.record(path)
    assert path.exists()
    assert "numpy==" in path.read_text()
    assert lock.digest

    result = lockfile.verify(path)
    assert result.ok, result.report()


def test_a_missing_lockfile_is_not_silently_ok(tmp_path, lockfile):
    result = lockfile.verify(tmp_path / "nope.lock")
    assert result.ok is False
    assert result.lockfile_missing is True


def test_a_changed_numerical_pin_is_an_error_not_a_warning(tmp_path, lockfile):
    """A result computed against a different numpy is not comparable."""
    path = tmp_path / "requirements.lock"
    lockfile.record(path)
    text = path.read_text().replace("numpy==", "numpy==0.0.0-")  # force drift
    path.write_text(text.replace("numpy==0.0.0-", "numpy==1.0.0\n#orig numpy=="))
    result = lockfile.verify(path)
    assert result.ok is False
    assert "numpy" in result.numerical_drift
    assert "NUMERICAL DRIFT" in result.summary()
    # And the CLI refuses to downgrade it to a warning.
    assert lockfile.main(["verify", "--path", str(path), "--warn"]) == 1


def test_an_extra_package_is_tolerated_by_default(tmp_path, lockfile):
    """Failing CI over a developer's ipython is how a drift check gets disabled."""
    path = tmp_path / "requirements.lock"
    lock = lockfile.record(path)
    packages = dict(lock.packages)
    packages.pop(sorted(packages)[0])
    path.write_text(
        lockfile.Lockfile(packages, lock.python, lock.platform, lock.recorded_at).to_text(),
        encoding="utf-8",
    )
    assert lockfile.verify(path).ok is True
    assert lockfile.verify(path, strict_extras=True).ok is False


def test_the_committed_lockfile_exists_and_parses(lockfile):
    path = REPO_ROOT / "deploy" / "requirements.lock"
    assert path.exists(), "deploy/requirements.lock is not committed"
    parsed = lockfile.Lockfile.parse(path.read_text(encoding="utf-8"))
    assert "numpy" in parsed.packages
    assert "pandas" in parsed.packages
    assert parsed.digest


# =================================================================== workflow


@pytest.fixture(scope="module")
def workflow_text():
    assert WORKFLOW.exists(), ".github/workflows/ci.yml is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def test_deployment_is_gated_on_green_ci_and_not_run_on_push(workflow_text):
    """V1 deployed on every push, with tests on an independent job."""
    deploy_block = workflow_text.split("  deploy:", 1)[1]
    assert "needs: [gate]" in deploy_block
    assert "needs.gate.result == 'success'" in deploy_block
    # Only a tag or an explicit dispatch.
    assert "startsWith(github.ref, 'refs/tags/v')" in deploy_block
    assert "workflow_dispatch" in deploy_block
    # And a protected environment, so a human approves.
    assert "environment:" in deploy_block


def test_the_gate_requires_success_not_merely_not_failure(workflow_text):
    """`!= failure` would let a CANCELLED or SKIPPED job pass as green."""
    gate = workflow_text.split("  gate:", 1)[1].split("  deploy:", 1)[0]
    assert '"$result" != "success"' in gate
    for required in ("lint", "typecheck", "test", "golden", "lockfile", "audit"):
        assert required in gate


def test_every_required_job_exists(workflow_text):
    for job in ("lint:", "typecheck:", "test:", "golden:", "live-flags:", "lockfile:", "audit:"):
        assert f"  {job}" in workflow_text, f"the {job} job is missing"


def test_the_golden_tests_are_their_own_required_job(workflow_text):
    golden = workflow_text.split("  golden:", 1)[1].split("  live-flags:", 1)[0]
    assert "-m golden" in golden
    # A `pytest -m golden` that collects nothing exits 0 and shows green forever.
    assert "NO golden tests were collected" in golden
    # A skipped golden test is a failure here.
    assert "skipped" in golden


def test_the_test_job_has_a_timeout(workflow_text):
    test_job = workflow_text.split("  test:", 1)[1].split("  golden:", 1)[0]
    assert "timeout-minutes:" in test_job


def test_ci_self_tests_the_live_flag_scanner_against_a_planted_flag(workflow_text):
    flags = workflow_text.split("  live-flags:", 1)[1].split("  lockfile:", 1)[0]
    # The V1 misspelling is planted, assembled from halves so that the workflow
    # file does not itself contain the committed literal the scanner rejects.
    assert "FIBOKEI_LIVE_EXECUTION" in flags
    assert "_ENABLED" in flags
    assert "did NOT catch a planted flag" in flags


def test_the_workflow_self_test_would_actually_reject_its_own_planted_file(
    tmp_path, live_flags
):
    """Reproduce what the CI step writes, and assert the scanner rejects it.

    Without this, the CI self-test could silently stop planting a matching key
    (a renamed variable, a broken printf) and pass forever by proving nothing.
    """
    half_a, half_b = "FIBOKEI_LIVE_EXECUTION", "_ENABLED"
    planted = (
        "services:\n  - type: web\n    envVars:\n"
        f"      - key: {half_a}{half_b}\n        value: \"true\"\n"
    )
    (tmp_path / "render.yaml").write_text(planted, encoding="utf-8")
    assert live_flags.main([str(tmp_path)]) == 1
