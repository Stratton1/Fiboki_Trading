"""Settings hygiene: strict booleans, a declared env registry, unknown names.

V1 parsed ``anything not in _TRUE`` as False, so ``FIBOKI_COOKIE_SECURE=ture``
silently switched the Secure flag OFF, and a misspelt variable name was simply
ignored.  These tests pin the stricter behaviour.
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from fiboki.api import settings as settings_module
from fiboki.api.settings import (
    ENV_REGISTRY,
    KNOWN_ENV_NAMES,
    SettingsError,
    load_settings,
    parse_bool,
    warn_unknown_env,
)
from fiboki.core.enums import ExecutionMode

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
_NAME = re.compile(r"FIBOKE?I_[A-Z0-9_]+")


# ----------------------------------------------------------- strict booleans


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True), ("true", True), ("TRUE", True), ("Yes", True), ("on", True),
        ("0", False), ("false", False), ("False", False), ("NO", False), ("off", False),
        (" true ", True),
    ],
)
def test_parse_bool_accepts_the_declared_spellings(raw: str, expected: bool) -> None:
    assert parse_bool("FIBOKI_X", raw, default=not expected) is expected


@pytest.mark.parametrize("default", [True, False])
def test_parse_bool_empty_means_default(default: bool) -> None:
    assert parse_bool("FIBOKI_X", "", default) is default
    assert parse_bool("FIBOKI_X", "   ", default) is default


@pytest.mark.parametrize("raw", ["ture", "flase", "2", "y", "enabled", "none", "-1"])
def test_parse_bool_refuses_anything_else(raw: str) -> None:
    with pytest.raises(SettingsError) as info:
        parse_bool("FIBOKI_COOKIE_SECURE", raw, default=True)
    assert "FIBOKI_COOKIE_SECURE" in str(info.value)
    assert repr(raw) in str(info.value)


def test_misspelt_cookie_secure_is_a_startup_error_not_a_silent_false() -> None:
    with pytest.raises(SettingsError, match=r"FIBOKI_COOKIE_SECURE='ture'"):
        load_settings({"FIBOKI_COOKIE_SECURE": "ture"})


def test_cookie_secure_still_parses_the_valid_spellings() -> None:
    assert load_settings({"FIBOKI_COOKIE_SECURE": "false"}).cookie_secure is False
    assert load_settings({"FIBOKI_COOKIE_SECURE": "ON"}).cookie_secure is True
    assert load_settings({}).cookie_secure is True  # default unchanged


def test_settings_error_is_still_caught_as_runtime_and_value_error() -> None:
    assert issubclass(SettingsError, RuntimeError)
    assert issubclass(SettingsError, ValueError)
    with pytest.raises(RuntimeError):
        load_settings({"FIBOKI_EXECUTION_MODE": "liv"})


def test_malformed_numbers_name_the_variable() -> None:
    with pytest.raises(SettingsError, match="FIBOKI_SESSION_TTL='12h'"):
        load_settings({"FIBOKI_SESSION_TTL": "12h"})
    with pytest.raises(SettingsError, match="FIBOKI_WORKER_STALE_SECONDS"):
        load_settings({"FIBOKI_WORKER_STALE_SECONDS": "two minutes"})


def test_defaults_are_unchanged() -> None:
    s = load_settings({})
    assert s.execution_mode is ExecutionMode.PAPER
    assert s.cookie_name == "fiboki_session"
    assert s.cookie_samesite == "strict"
    assert s.session_ttl_seconds == 43200
    assert s.worker_heartbeat_stale_seconds == 120.0
    assert s.state_dir == Path("var")
    assert s.slippage_model == "zero"
    assert s.spread_model == "static_typical"
    assert s.financing_model == "none"
    assert s.fx_conversion_model == "static_rate"


# ------------------------------------------------------------------ registry


def test_registry_names_are_unique_and_well_formed() -> None:
    names = [v.name for v in ENV_REGISTRY]
    assert len(names) == len(set(names))
    assert all(_NAME.fullmatch(n) and n.startswith("FIBOKI_") for n in names)
    assert all(v.description for v in ENV_REGISTRY)


def test_registry_declares_every_variable_load_settings_reads() -> None:
    text = (SRC / "api" / "settings.py").read_text()
    body = text[text.index("def load_settings(") :]
    read = set(re.findall(r'(?:get|_flag_from|_number)\(\s*"(FIBOKI_[A-Z0-9_]+)"', body))
    declared_here = {v.name for v in ENV_REGISTRY if v.read_by == "fiboki.api.settings"}
    assert read, "the parse found nothing; the regex has rotted"
    assert read == declared_here


def test_every_env_name_used_as_a_literal_in_src_is_declared() -> None:
    """A module that starts reading a new FIBOKI_* variable must declare it.

    Only string constants that are EXACTLY a variable name count, so prose in
    docstrings and messages does not.
    """
    undeclared: dict[str, list[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _NAME.fullmatch(node.value)
                and node.value not in KNOWN_ENV_NAMES
            ):
                undeclared.setdefault(node.value, []).append(
                    f"{path.relative_to(SRC)}:{node.lineno}"
                )
    assert not undeclared, f"undeclared env variables: {undeclared}"


def test_required_in_modes_is_only_ever_a_broker_mode() -> None:
    for var in ENV_REGISTRY:
        assert var.required_in_modes <= {ExecutionMode.DEMO, ExecutionMode.LIVE}, var.name


# ------------------------------------------------------------- unknown names


def test_warn_unknown_env_returns_unknown_fiboki_and_v1_names_only() -> None:
    env = {
        "FIBOKI_COOKIE_SECUR": "true",          # misspelt
        "FIBOKEI_LIVE_EXECUTION_ENABLED": "true",  # V1 spelling
        "FIBOKI_COOKIE_SECURE": "true",          # known
        "PATH": "/usr/bin",                      # not ours
        "fiboki_lowercase": "x",                 # not read by anything either
    }
    assert warn_unknown_env(env) == [
        "FIBOKEI_LIVE_EXECUTION_ENABLED",
        "FIBOKI_COOKIE_SECUR",
        "fiboki_lowercase",
    ]


def test_warn_unknown_env_is_empty_for_a_clean_environment() -> None:
    assert warn_unknown_env({n: "" for n in KNOWN_ENV_NAMES} | {"HOME": "/"}) == []


@pytest.mark.parametrize("mode", ["demo", "live"])
def test_unknown_names_are_a_startup_error_in_broker_modes(mode: str) -> None:
    with pytest.raises(SettingsError, match="FIBOKI_COOKIE_SECUR"):
        load_settings({"FIBOKI_EXECUTION_MODE": mode, "FIBOKI_COOKIE_SECUR": "true"})


@pytest.mark.parametrize("mode", ["demo", "live"])
def test_a_clean_environment_still_loads_in_broker_modes(mode: str) -> None:
    s = load_settings({"FIBOKI_EXECUTION_MODE": mode, "FIBOKI_SESSION_SECRET": "x"})
    assert s.execution_mode.value == mode


@pytest.mark.parametrize("mode", ["paper", "backtest", "shadow"])
def test_unknown_names_are_a_single_warning_otherwise(
    mode: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_module, "_warned_unknown", set())
    env = {"FIBOKI_EXECUTION_MODE": mode, "FIBOKEI_DATA_DIR": "/tmp/x"}
    with caplog.at_level(logging.WARNING, logger="fiboki.api.settings"):
        load_settings(env)
        load_settings(env)
    hits = [r for r in caplog.records if "FIBOKEI_DATA_DIR" in r.getMessage()]
    assert len(hits) == 1, "logged once per process, not once per load"
