"""Process A (the CLI) pauses; process B's gateway blocks its very next order.

This is the test the audit asked for (F, P0-1 item 4). Before the fix the CLI
wrote ``~/.fiboki/killswitch.jsonl``, the API read ``<state_dir>/killswitch.jsonl``
and the paper gateway read memory, so this exact sequence traded straight
through the operator's pause.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fiboki.core.enums import ExecutionMode
from fiboki.core.paths import resolve_paths
from fiboki.risk.gateway import InMemoryAttemptRecorder, RiskGateway
from fiboki.risk.killswitch import KillSwitch
from tests.exec_fixtures import make_context

REPO = Path(__file__).resolve().parents[2]


def _cli(args: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "fiboki.cli", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_cli_pause_in_another_process_blocks_this_gateways_next_evaluate(tmp_path) -> None:
    state = tmp_path / "state"
    env = {**os.environ, "FIBOKI_STATE_DIR": str(state), "COLUMNS": "200"}
    env.pop("FIBOKI_EXECUTION_MODE", None)

    # Process B: a gateway composed the way a worker must be, from the resolved
    # paths, BEFORE the operator acts.
    gateway = RiskGateway(
        mode=ExecutionMode.PAPER,
        kill_switch=KillSwitch.from_paths(resolve_paths({"FIBOKI_STATE_DIR": str(state)})),
        recorder=InMemoryAttemptRecorder(),
    )
    assert gateway.evaluate(make_context()).allowed

    # Process A: the operator, from an unrelated working directory.
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    done = _cli(["killswitch", "pause", "--reason", "cross-process test", "--operator", "joe"],
                env, elsewhere)
    assert done.returncode == 0, done.stderr + done.stdout
    assert str(state / "killswitch.jsonl") in done.stdout.replace("\n", "")

    decision = gateway.evaluate(make_context())
    assert not decision.allowed
    assert "kill_switch_pause_blocks_new_risk" in decision.reasons

    status = _cli(["killswitch", "status", "--json"], env, elsewhere)
    assert status.returncode == 0, status.stderr
    assert '"active": true' in status.stdout and '"journal_is_resolved": true' in status.stdout


def test_the_cli_says_when_its_journal_is_not_the_one_gateways_read(tmp_path) -> None:
    env = {**os.environ, "FIBOKI_STATE_DIR": str(tmp_path / "state"), "COLUMNS": "200"}
    stray = tmp_path / "stray.jsonl"
    done = _cli(["killswitch", "pause", "--reason", "x", "--journal", str(stray)], env, tmp_path)
    assert done.returncode == 0, done.stderr
    assert "NOT the journal" in done.stdout
