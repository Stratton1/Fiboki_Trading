"""One path resolver for every process (audit F, P0-1 / B-06).

The kill switch used to resolve three ways: the CLI defaulted to
``~/.fiboki/killswitch.jsonl``, the API to ``<FIBOKI_STATE_DIR>/killswitch.jsonl``
and the paper gateway to memory. These tests pin the single rule and prove the
API settings and the CLI compute the same journal from the same environment.
"""
from __future__ import annotations

import ast
from pathlib import Path

from fiboki.api.settings import load_settings
from fiboki.core.paths import resolve_paths

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"


def test_every_ledger_lives_under_the_state_dir() -> None:
    paths = resolve_paths({"FIBOKI_STATE_DIR": "/srv/fiboki/var", "HOME": "/Users/joe"})
    root = Path("/srv/fiboki/var")
    assert paths.state_dir == root and paths.state_dir_from_env
    for ledger in (
        paths.killswitch_journal,
        paths.intent_ledger,
        paths.audit_ledger,
        paths.api_audit,
        paths.holdout_db,
        paths.experiment_db,
        paths.news_store,
        paths.events_store,
        paths.alert_outbox,
        paths.paper_root,
    ):
        assert root in ledger.parents, ledger
    assert paths.killswitch_journal == root / "killswitch.jsonl"
    assert paths.home == Path("/Users/joe/.fiboki")
    assert paths.heartbeat_db == Path("/Users/joe/.fiboki/state.db")


def test_it_is_pure_and_deterministic() -> None:
    env = {"FIBOKI_STATE_DIR": "~/state", "HOME": "/h"}
    assert resolve_paths(env) == resolve_paths(dict(env))
    assert resolve_paths(env).state_dir == Path("/h/state")


def test_the_relative_default_is_reported_and_anchorable() -> None:
    bare = resolve_paths({})
    assert bare.state_dir == Path("var") and not bare.state_dir_from_env
    anchored = resolve_paths({}, cwd="/repo")
    assert anchored.killswitch_journal == Path("/repo/var/killswitch.jsonl")


def test_explicit_overrides_win() -> None:
    paths = resolve_paths(
        {
            "FIBOKI_STATE_DIR": "/s",
            "FIBOKI_EXPERIMENT_DB": "/e/x.sqlite",
            "FIBOKI_STATE_DB": "/d/state.db",
            "FIBOKI_ALERT_LOG": "/s/alerts.jsonl",
        }
    )
    assert paths.experiment_db == Path("/e/x.sqlite") and paths.experiment_db_from_env
    assert paths.heartbeat_db == Path("/d/state.db")
    assert paths.alert_log == Path("/s/alerts.jsonl")
    assert resolve_paths({}).alert_log is None


def test_api_settings_and_cli_resolve_the_same_kill_switch_journal(tmp_path, monkeypatch) -> None:
    env = {"FIBOKI_STATE_DIR": str(tmp_path / "state")}
    settings = load_settings(env)
    monkeypatch.setenv("FIBOKI_STATE_DIR", env["FIBOKI_STATE_DIR"])
    from fiboki.cli import _killswitch_journal

    cli_path, _ = _killswitch_journal(None)
    assert settings.killswitch_path == cli_path == tmp_path / "state" / "killswitch.jsonl"


def test_the_cli_has_no_kill_switch_default_of_its_own() -> None:
    """No string literal naming a killswitch journal path may appear in cli.py."""
    tree = ast.parse((SRC / "cli.py").read_text())
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "killswitch.jsonl" in n.value
    ]
    assert literals == [], literals


def test_core_paths_imports_nothing_from_fiboki() -> None:
    tree = ast.parse((SRC / "core" / "paths.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("fiboki"), node.module
