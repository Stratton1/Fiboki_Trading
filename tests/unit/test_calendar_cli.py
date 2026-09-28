"""``fiboki calendar status`` and ``fiboki calendar check``, through click's runner."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from fiboki.cli import EXIT_FAIL, EXIT_MISUSE, EXIT_OK, app
from fiboki.marketstate.calendar import official_calendar_manifest

runner = CliRunner(mix_stderr=False)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIBOKI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FIBOKI_EXECUTION_MODE", "paper")


def _json(result) -> dict:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def test_the_calendar_group_exists() -> None:
    import typer.main

    command = typer.main.get_command(app)
    assert {"status", "check"} <= set(command.commands["calendar"].commands)  # type: ignore[attr-defined]


def test_status_reports_per_source_counts() -> None:
    result = runner.invoke(app, ["calendar", "status", "--json"])
    assert result.exit_code == EXIT_OK, result.stderr
    payload = _json(result)
    manifest = official_calendar_manifest()
    assert payload["coverage"]["n_events"] == sum(
        m["n_events"] for m in manifest["sources_used"].values()
    )
    for key, meta in manifest["sources_used"].items():
        assert payload["per_source"][key]["n"] == meta["n_events"]
    assert payload["coverage"]["declared_start"].startswith("2024-01-01")
    assert payload["retrieved_at"] == "2026-09-28"


def test_status_renders_for_humans() -> None:
    result = runner.invoke(app, ["calendar", "status"])
    assert result.exit_code == EXIT_OK
    assert "fed_fomc" in result.stdout
    assert "declared complete span" in result.stdout


def test_status_fails_for_a_span_it_cannot_cover() -> None:
    ok = runner.invoke(
        app, ["calendar", "status", "--start", "2024-02-01T00:00:00Z", "--end", "2026-06-01T00:00:00Z"]
    )
    assert ok.exit_code == EXIT_OK
    bad = runner.invoke(
        app, ["calendar", "status", "--start", "2019-01-01T00:00:00Z", "--end", "2026-06-01T00:00:00Z",
              "--json"]
    )
    assert bad.exit_code == EXIT_FAIL
    assert _json(bad)["requested"]["covered"] is False


def test_check_inside_a_known_nfp() -> None:
    result = runner.invoke(app, ["calendar", "check", "2025-03-07T13:45:00Z", "USD", "--json"])
    assert result.exit_code == EXIT_OK
    payload = _json(result)
    assert payload["in_blackout"] is True
    assert payload["answer_trustworthy"] is True
    assert [e["name"] for e in payload["events"]] == ["US Non-Farm Payrolls"]


def test_check_accepts_an_instrument_and_says_why() -> None:
    result = runner.invoke(app, ["calendar", "check", "2025-03-07T13:45:00Z", "EURUSD"])
    assert result.exit_code == EXIT_OK
    assert "IN BLACKOUT" in result.stdout
    assert "Non-Farm Payrolls" in result.stdout


def test_check_a_day_later_is_clear() -> None:
    result = runner.invoke(app, ["calendar", "check", "2025-03-08T13:45:00Z", "USD", "--json"])
    assert result.exit_code == EXIT_OK
    assert _json(result)["in_blackout"] is False


def test_check_refuses_to_guess_outside_coverage() -> None:
    before = runner.invoke(app, ["calendar", "check", "2019-07-31T18:00:00Z", "USD", "--json"])
    assert before.exit_code == EXIT_FAIL
    assert _json(before)["answer_trustworthy"] is False
    aud = runner.invoke(app, ["calendar", "check", "2025-03-08T13:45:00Z", "AUD", "--json"])
    assert aud.exit_code == EXIT_FAIL
    assert "AUD" in " ".join(_json(aud)["why_untrustworthy"])


def test_check_refuses_a_naive_timestamp() -> None:
    result = runner.invoke(app, ["calendar", "check", "2025-03-07 13:45", "USD"])
    assert result.exit_code == EXIT_MISUSE
