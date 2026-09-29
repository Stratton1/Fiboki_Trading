"""``fiboki worker run research`` opens the DURABLE job ledger.

The research worker used to construct ``Orchestrator()`` (an in-memory
ledger), so every queued job, attempt record and idempotency key vanished on
each launchd restart. It must open ``<FIBOKI_STATE_DIR>/jobs.sqlite``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fiboki import cli
from fiboki.agents.orchestrator import Orchestrator
from fiboki.workers import research_worker as research_worker_module


def test_research_worker_opens_state_dir_jobs_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("FIBOKI_STATE_DIR", str(state))
    monkeypatch.setenv("FIBOKI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FIBOKI_STATE_DB", raising=False)
    monkeypatch.delenv("FIBOKI_AGENT_CYCLES", raising=False)
    seen: dict[str, Any] = {}

    class _Probe:
        def __init__(self, orchestrator: Orchestrator, store: Any, config: Any, **_: Any) -> None:
            seen["orchestrator"] = orchestrator
            self.worker_id = "research@test:1"

        def run(self) -> int:
            return 0

    monkeypatch.setattr(research_worker_module, "ResearchWorker", _Probe)
    assert cli.main(["worker", "run", "research", "--once"]) == 0
    orchestrator: Orchestrator = seen["orchestrator"]
    assert orchestrator.path == state / "jobs.sqlite"
    assert cli.research_jobs_ledger_path() == state / "jobs.sqlite"
    assert (state / "jobs.sqlite").exists()
    orchestrator.close()
