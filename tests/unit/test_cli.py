"""The operator CLI: command surface, exit codes, and a doctor that diagnoses.

Exit codes matter here because a launchd KeepAlive block and a make target
both read them. A CLI that prints a red X and exits 0 is a CLI that lies to
its supervisor.
"""
from __future__ import annotations

import json

import pytest

from fiboki.cli import EXIT_FAIL, EXIT_MISUSE, EXIT_OK, _diagnose, app, main

BASE_ENV = {
    "FIBOKI_EXECUTION_MODE": "paper",
    "FIBOKI_DATA_ROOT": "/tmp/fiboki-data",
    "FIBOKI_ALERT_LOG": "/tmp/alerts.jsonl",
    "FIBOKI_EXPECTED_WORKERS": "research@mac:1",
}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIBOKI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FIBOKI_STATE_DB", raising=False)
    monkeypatch.delenv("FIBOKI_LIVE_EXECUTION_ENABLED", raising=False)
    monkeypatch.setenv("FIBOKI_EXECUTION_MODE", "paper")


def run(args, capsys=None):
    code = main(args)
    return code


# ---------------------------------------------------------------- the surface

EXPECTED_SURFACE = {
    "data": {"ingest", "validate", "version", "migrate-v1"},
    "research": {"sweep", "validate", "campaign", "memory"},
    "strategy": {"list", "show", "bind", "compile"},
    "worker": {"run", "status"},
    "system": {"health", "doctor", "metrics"},
    "broker": {"status", "reconcile"},
    "killswitch": {"pause", "flatten", "status"},
}


def _command_names():
    import typer.main

    command = typer.main.get_command(app)
    groups = {}
    for name, sub in command.commands.items():  # type: ignore[attr-defined]
        groups[name] = set(getattr(sub, "commands", {}).keys())
    return groups


def test_the_required_command_surface_exists():
    groups = _command_names()
    for group, commands in EXPECTED_SURFACE.items():
        assert group in groups, f"`fiboki {group}` is missing"
        missing = commands - groups[group]
        assert not missing, f"`fiboki {group}` is missing {sorted(missing)}"


def test_version_reports_the_pinned_numerical_stack(capsys):
    assert run(["version", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["fiboki"] == "2.0.0"
    for module in ("numpy", "pandas", "scipy", "pyarrow"):
        assert payload[module] != "MISSING"


# --------------------------------------------------------------------- doctor


def test_doctor_reports_a_missing_data_root_with_the_export_to_run(monkeypatch):
    monkeypatch.delenv("FIBOKI_DATA_ROOT", raising=False)
    findings = {f.name: f for f in _diagnose(dict(BASE_ENV, FIBOKI_DATA_ROOT=""))}
    root = findings["data root"]
    assert root.ok is False
    assert "export FIBOKI_DATA_ROOT" in root.fix


def test_doctor_flags_an_environment_with_live_execution_enabled():
    """The V1 render.yaml incident, surfaced at the operator's terminal."""
    findings = {
        f.name: f
        for f in _diagnose(dict(BASE_ENV, FIBOKI_LIVE_EXECUTION_ENABLED="true"))
    }
    mode = findings["execution mode"]
    assert mode.ok is False
    assert "LIVE EXECUTION IS ENABLED" in mode.fix
    assert "render.yaml" in mode.fix


def test_doctor_passes_the_execution_mode_check_in_paper():
    findings = {f.name: f for f in _diagnose(dict(BASE_ENV))}
    assert findings["execution mode"].ok is True


def test_doctor_warns_when_only_the_console_alert_channel_exists():
    findings = {f.name: f for f in _diagnose({"FIBOKI_EXECUTION_MODE": "paper"})}
    channels = findings["alert channels"]
    assert channels.ok is False
    assert "goes nowhere" in channels.fix


def test_doctor_explains_why_expected_workers_matters():
    findings = {f.name: f for f in _diagnose({"FIBOKI_EXECUTION_MODE": "paper"})}
    expected = findings["expected workers"]
    assert expected.ok is False
    assert "NEVER STARTED" in expected.fix


def test_doctor_checks_the_state_database_is_actually_writable():
    findings = {f.name: f for f in _diagnose(dict(BASE_ENV))}
    assert findings["state database"].ok is True
    assert "reachable" in findings["state database"].detail


def test_doctor_exits_non_zero_when_something_is_wrong(capsys):
    code = run(["system", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert code == EXIT_FAIL
    # Every failing finding carries a fix, not just a symptom.
    for finding in payload["findings"]:
        if not finding["ok"]:
            assert finding["fix"], f"{finding['name']} fails with no remedy"


def test_doctor_checks_the_pinned_numerical_dependencies():
    findings = {f.name: f for f in _diagnose(dict(BASE_ENV))}
    pinned = findings["pinned dependencies"]
    assert pinned.ok is True, pinned.detail


def test_doctor_verifies_the_dependency_lockfile():
    findings = {f.name: f for f in _diagnose(dict(BASE_ENV))}
    assert "dependency lockfile" in findings


# --------------------------------------------------------------------- worker


def test_worker_status_on_an_empty_state_db_is_not_an_error(capsys):
    assert run(["worker", "status", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["workers"] == []


def test_worker_status_reports_a_down_worker_and_exits_non_zero(capsys, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from fiboki.workers.base import WORKER_HEARTBEAT, WorkerStore

    db = tmp_path / "state.db"
    monkeypatch.setenv("FIBOKI_STATE_DB", str(db))
    store = WorkerStore.sqlite_at(db)
    stale = datetime.now(tz=UTC) - timedelta(hours=2)
    with store.engine.begin() as conn:
        from sqlalchemy import insert

        conn.execute(
            insert(WORKER_HEARTBEAT).values(
                worker_id="research@mac:1",
                kind="research",
                beat_at=stale,
                started_at=stale,
                status="idle",
            )
        )
    store.close()

    code = run(["worker", "status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["workers"][0]["verdict"] == "down"
    assert code == EXIT_FAIL


def test_worker_run_rejects_an_unknown_kind():
    assert run(["worker", "run", "nonsense"]) == EXIT_MISUSE


def test_worker_run_refuses_to_assemble_a_live_worker_from_flags():
    """A live worker built from command-line flags is one whose risk
    configuration nobody reviewed."""
    assert run(["worker", "run", "live"]) == EXIT_MISUSE


# --------------------------------------------------------------------- broker


def test_broker_status_reports_the_mode(capsys):
    assert run(["broker", "status", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "paper"
    assert payload["live_execution_enabled"] is False


def test_broker_status_surfaces_an_enabled_live_flag(capsys, monkeypatch):
    monkeypatch.setenv("FIBOKI_LIVE_EXECUTION_ENABLED", "true")
    run(["broker", "status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["live_execution_enabled"] is True


def test_broker_reconcile_flags_unresolved_intents(tmp_path, capsys):
    path = tmp_path / "intents.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"client_ref": "a", "state": "filled"}),
                json.dumps({"client_ref": "b", "state": "unknown"}),
            ]
        ),
        encoding="utf-8",
    )
    code = run(["broker", "reconcile", "--intents", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["unresolved"] == {"unknown": 1}
    # An UNKNOWN intent may be a real position. Non-zero is correct.
    assert code == EXIT_FAIL


def test_broker_reconcile_on_a_clean_store_exits_zero(tmp_path):
    path = tmp_path / "intents.jsonl"
    path.write_text(json.dumps({"client_ref": "a", "state": "filled"}), encoding="utf-8")
    assert run(["broker", "reconcile", "--intents", str(path), "--json"]) == EXIT_OK


def test_broker_reconcile_on_a_missing_store_is_misuse():
    assert run(["broker", "reconcile", "--intents", "/nope/intents.jsonl"]) == EXIT_MISUSE


# ----------------------------------------------------------------- killswitch


def test_killswitch_pause_then_status(tmp_path, capsys):
    journal = tmp_path / "ks.jsonl"
    assert run(["killswitch", "pause", "--journal", str(journal), "--reason", "spread blew out"]) == EXIT_OK
    capsys.readouterr()
    assert run(["killswitch", "status", "--journal", str(journal), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["active"] is True
    assert payload["blocks_new_risk"] is True


def test_killswitch_flatten_says_nothing_is_closed_without_a_worker(tmp_path, capsys):
    journal = tmp_path / "ks.jsonl"
    run(["killswitch", "flatten", "--journal", str(journal), "--reason", "operator call"])
    # rich wraps to the terminal width, so compare on normalised whitespace.
    out = " ".join(capsys.readouterr().out.split())
    # The dangerous misunderstanding is "I ran flatten, so I am flat".
    assert "NOTHING HAS BEEN CLOSED" in out
    assert "fiboki worker status" in out


def test_killswitch_status_on_a_fresh_install_is_not_an_error(tmp_path):
    assert run(["killswitch", "status", "--journal", str(tmp_path / "none.jsonl")]) == EXIT_OK


# ------------------------------------------------------------------- research


def test_research_sweep_status_distinguishes_no_data_from_done(tmp_path, capsys):
    from fiboki.workers.research_worker import CellOutcome, CheckpointStore

    db = tmp_path / "sweep.db"
    with CheckpointStore(db) as store:
        store.begin_sweep("s1", 10)
        for i in range(8):
            store.record("s1", f"c{i}", CellOutcome.done({}))
        for i in (8, 9):
            store.record("s1", f"c{i}", CellOutcome.no_data("gap"))

    code = run(
        ["research", "sweep", "s1", "--checkpoints", str(db), "--status", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["done"] == 8
    assert payload["counts"]["no_data"] == 2
    assert payload["complete"] is False
    # 20% no data is above the 2% default threshold.
    assert code == EXIT_FAIL


def test_research_sweep_status_reports_a_genuinely_complete_sweep(tmp_path, capsys):
    from fiboki.workers.research_worker import CellOutcome, CheckpointStore

    db = tmp_path / "sweep.db"
    with CheckpointStore(db) as store:
        store.begin_sweep("s1", 3)
        for i in range(3):
            store.record("s1", f"c{i}", CellOutcome.done({}))
    assert run(["research", "sweep", "s1", "--checkpoints", str(db), "--status", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True


def test_research_campaign_plans_before_it_submits(tmp_path, capsys):
    campaign = tmp_path / "c.json"
    campaign.write_text(
        json.dumps({"name": "phase1", "jobs": [{"job_type": "backtest"}] * 5}),
        encoding="utf-8",
    )
    assert run(["research", "campaign", str(campaign), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["jobs"] == 5


def test_research_campaign_rejects_broken_json(tmp_path):
    campaign = tmp_path / "c.json"
    campaign.write_text("{not json", encoding="utf-8")
    assert run(["research", "campaign", str(campaign)]) == EXIT_MISUSE


# ------------------------------------------------------------------- system


def test_system_metrics_emits_prometheus_exposition(capsys):
    assert run(["system", "metrics"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "# TYPE fiboki_worker_up gauge" in out


def test_system_health_runs_real_checks(capsys):
    run(["system", "health", "--json"])
    payload = json.loads(capsys.readouterr().out)
    names = {check["name"] for check in payload["checks"]}
    assert {"database", "worker_heartbeat", "queue_depth"} <= names
    # The database check really executed SELECT 1.
    database = next(c for c in payload["checks"] if c["name"] == "database")
    assert database["status"] == "ok"
    assert database["duration_ms"] >= 0


# ------------------------------------------------------------------ strategy


def test_strategy_list_on_a_missing_directory_is_misuse():
    assert run(["strategy", "list", "--dir", "/nope/strategies"]) == EXIT_MISUSE


def test_data_validate_on_a_missing_file_is_misuse():
    assert run(["data", "validate", "/nope/bars.parquet"]) == EXIT_MISUSE


def test_data_ingest_refuses_to_fall_back_to_synthetic_data(tmp_path, capsys):
    """V1's provider fallback wrote fabricated bars into the canonical layer."""
    code = run(
        [
            "data",
            "ingest",
            "-i",
            "EURUSD",
            "--start",
            "2024-01-01",
            "--end",
            "2024-02-01",
            "--root",
            str(tmp_path / "store"),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_FAIL
    assert "does NOT fall back to synthetic data" in captured.out + captured.err


def test_data_ingest_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "store"
    assert (
        run(
            [
                "data",
                "ingest",
                "-i",
                "EURUSD",
                "--start",
                "2024-01-01",
                "--end",
                "2024-02-01",
                "--root",
                str(root),
                "--dry-run",
            ]
        )
        == EXIT_OK
    )
    assert not root.exists()


def test_migrate_v1_check_refuses_rather_than_writing(tmp_path, capsys):
    """``--check`` promises "write nothing". The underlying ``migrate`` has no
    dry-run mode, so honouring the flag by calling it anyway would WRITE.

    Silently doing the opposite of what a flag says is the class of bug this
    whole rebuild exists to remove, so the command refuses instead.
    """
    source = tmp_path / "v1"
    source.mkdir()
    code = run(["data", "migrate-v1", str(source), str(tmp_path / "v2"), "--check"])
    captured = " ".join((capsys.readouterr().err).split())
    assert code == EXIT_MISUSE
    assert "no dry-run mode" in captured
    assert not (tmp_path / "v2").exists()
