"""One HealthThresholds value, resolved in settings, read by every surface (audit F P2-20).

The same worker's staleness was written out in the CLI (120/300), the health
check (120/300), the watchdog (120/300), settings (FIBOKI_WORKER_STALE_SECONDS)
and the live worker (900), so the surfaces could disagree.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fiboki.api.settings import SettingsError, load_settings
from fiboki.obs.alerts import WatchdogThresholds
from fiboki.obs.health import DEFAULT_HEALTH_THRESHOLDS, HealthThresholds, WorkerHeartbeatCheck

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"


def test_settings_resolve_the_thresholds_from_the_environment() -> None:
    s = load_settings(
        {
            "FIBOKI_WORKER_STALE_SECONDS": "60",
            "FIBOKI_WORKER_DOWN_SECONDS": "240",
            "FIBOKI_DATA_STALE_SECONDS": "1200",
        }
    )
    assert s.health == HealthThresholds(60.0, 240.0, 1200.0)
    assert s.worker_heartbeat_stale_seconds == 60.0  # the legacy reader sees the same value
    assert load_settings({}).health == DEFAULT_HEALTH_THRESHOLDS


def test_every_surface_derives_from_the_one_value() -> None:
    h = HealthThresholds(45.0, 90.0, 600.0)
    check = WorkerHeartbeatCheck.from_thresholds(lambda: [], h)
    watch = WatchdogThresholds.from_health(h)
    assert (check.stale_after_seconds, check.down_after_seconds) == (45.0, 90.0)
    assert (watch.stale_after_seconds, watch.down_after_seconds) == (45.0, 90.0)


@pytest.mark.parametrize(
    ("stale", "down"), [("300", "120"), ("120", "120"), ("0", "300"), ("-5", "300")]
)
def test_incoherent_thresholds_are_a_startup_error(stale: str, down: str) -> None:
    with pytest.raises(SettingsError):
        load_settings({"FIBOKI_WORKER_STALE_SECONDS": stale, "FIBOKI_WORKER_DOWN_SECONDS": down})


def test_the_cli_worker_status_defaults_are_not_literals() -> None:
    """``fiboki worker status`` takes its defaults from Settings.health."""
    tree = ast.parse((SRC / "cli.py").read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "worker_status"
    )
    for arg, default in zip(fn.args.args[-len(fn.args.defaults):], fn.args.defaults, strict=True):
        if arg.arg in {"stale_after", "down_after"}:
            assert isinstance(default, ast.Call) and isinstance(default.args[0], ast.Constant)
            assert default.args[0].value is None, arg.arg
