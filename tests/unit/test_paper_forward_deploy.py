"""Supervision for the paper-forward process, and the two launchd defects (P2-17, P2-18).

Run for real against temporary directories, as ``tests/unit/test_desktop_scripts.py``
does. ``launchctl`` and ``caffeinate`` do not exist on Linux, so the dev-up
guard is driven by a STUB ``launchctl`` on ``PATH``; that is the only fake.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
LAUNCHD = REPO / "deploy" / "launchd"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("FIBOKI_", "FIBOKEI_"))}
    return subprocess.run(argv, env={**base, **(env or {})}, cwd=REPO, capture_output=True,
                          text=True, timeout=60, check=False)


def test_the_paper_plist_is_a_standard_process_that_forces_paper() -> None:
    plist = plistlib.loads((LAUNCHD / "uk.fiboki.paper.plist").read_bytes())
    assert plist["Label"] == "uk.fiboki.paper"
    assert plist["ProgramArguments"] == [
        "/bin/bash", "__FIBOKI_ROOT__/scripts/fiboki-service.sh", "paper"]
    assert plist["ProcessType"] == "Standard", "Background QoS is throttled (P2-18)"
    assert "LowPriorityIO" not in plist and "Nice" not in plist
    assert plist["EnvironmentVariables"] == {"FIBOKI_EXECUTION_MODE": "paper"}
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ExitTimeOut"] > 30, "longer than WorkerConfig.shutdown_grace_seconds"
    assert plist["ThrottleInterval"] >= 30
    text = (LAUNCHD / "uk.fiboki.paper.plist").read_text()
    assert "OANDA_PRACTICE_TOKEN" not in text.split("-->", 1)[1], "no credential in a plist"


def test_the_legacy_research_worker_plist_is_gone() -> None:
    """P2-17: two definitions of one worker meant two supervisors fighting over a lease."""
    assert not (LAUNCHD / "com.fiboki.research-worker.plist").exists()
    labels = [plistlib.loads(p.read_bytes())["Label"] for p in LAUNCHD.glob("*.plist")]
    assert labels.count("uk.fiboki.worker") == 1
    assert all(label.startswith("uk.fiboki.") for label in labels)


def test_the_installer_writes_the_paper_service_when_asked(tmp_path: Path) -> None:
    target = tmp_path / "LaunchAgents"
    proc = _run(["bash", str(SCRIPTS / "launchd-install.sh"), "--services", "paper",
                 "--target", str(target)])
    assert proc.returncode == 0, proc.stderr
    plist = plistlib.loads((target / "uk.fiboki.paper.plist").read_bytes())
    assert plist["ProgramArguments"] == ["/bin/bash", f"{REPO}/scripts/fiboki-service.sh", "paper"]
    assert plist["StandardOutPath"] == f"{REPO}/var/logs/paper.log"


def test_the_service_wrapper_runs_the_committed_wiring_with_a_sleep_assertion() -> None:
    text = (SCRIPTS / "fiboki-service.sh").read_text()
    case = text[text.index("  paper)"):text.index("  *)")]
    assert "paper forward" in case
    assert '--wiring "$ROOT/src/fiboki/entrypoints/wiring/paper_forward_v1.json"' in case
    assert (REPO / "src" / "fiboki" / "entrypoints" / "wiring" / "paper_forward_v1.json").exists()
    assert "caffeinate -is -w $$" in case, "held for the life of the exec'd process"
    assert case.index("caffeinate -is") < case.index('exec "$ROOT/.venv/bin/fiboki"')
    # Paper is forced AFTER the env file is read, for this service as for the others.
    assert text.index('done < "$ENV_FILE"') < text.index("export FIBOKI_EXECUTION_MODE=paper")


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def test_dev_up_refuses_while_launchd_services_are_loaded(tmp_path: Path) -> None:
    """P2-17: dev-up's pkill would kill launchd's worker and launchd would restart it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "pkill-ran"
    _stub(bin_dir, "launchctl", 'echo "123\t0\tuk.fiboki.worker"\necho "-\t0\tcom.apple.x"')
    _stub(bin_dir, "pkill", f'touch "{marker}"')
    _stub(bin_dir, "nohup", f'touch "{marker}"')
    proc = _run(["bash", str(SCRIPTS / "dev-up.sh")],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "REFUSING TO START" in proc.stderr and "uk.fiboki.worker" in proc.stderr
    assert "launchd-install.sh --unload" in proc.stderr
    assert not marker.exists(), "nothing was killed or started"


def test_the_dev_up_guard_runs_before_anything_is_killed_and_skips_without_launchctl() -> None:
    text = (SCRIPTS / "dev-up.sh").read_text()
    guard = text.index("command -v launchctl")
    assert guard < text.index("\npkill -f"), "the check must precede the first pkill"
    assert guard < text.index("\nnohup ")
    # Absent launchctl (Linux) the guard is skipped rather than failing the script.
    assert "if command -v launchctl >/dev/null 2>&1; then" in text
