"""Trading routes over HTTP, with a paper journal and without one.

With a journal (``FIBOKI_PAPER_ROOT`` at ``tests/fixtures/paper_journal``) every
row and account figure is PAPER, in the journal's own currency, with an as-of
time and a ``live`` source note. Without one the seed fixture is served, the
source note says ``seed``, and NO figure on any trading surface carries an
executed provenance except account figures that are explicitly missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from tests.api.conftest import ADMIN_PW, ORIGIN, _sha, login

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"
EXECUTED = {"paper", "shadow", "broker_demo", "broker_live"}
ROUTES = (
    "/api/trading/trades",
    "/api/trading/positions",
    "/api/trading/portfolio",
    "/api/trading/risk",
    "/api/trading/exposure",
)


def _client(tmp_path, monkeypatch, paper_root: Path | None):
    monkeypatch.setenv("FIBOKI_OPERATORS", f"joe:admin:{_sha(ADMIN_PW)}")
    env = {
        "FIBOKI_STATE_DIR": str(tmp_path / "state"),
        "FIBOKI_ALLOWED_ORIGINS": ORIGIN,
        "FIBOKI_COOKIE_SECURE": "false",
        "FIBOKI_SESSION_SECRET": "test-secret-not-for-production",
    }
    if paper_root is not None:
        # Settings.paper_root is parsed from the mapping handed to load_settings,
        # never from os.environ, so the test states it the way a deployment does.
        env["FIBOKI_PAPER_ROOT"] = str(paper_root)
    settings = load_settings(env)
    return TestClient(create_app(settings, configure_logs=False), base_url=ORIGIN)


@pytest.fixture
def journal_client(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, FIXTURE) as client:
        assert login(client, "joe", ADMIN_PW).status_code == 200
        yield client


@pytest.fixture
def seed_client(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, None) as client:
        assert login(client, "joe", ADMIN_PW).status_code == 200
        yield client


#: Configured limits, not measurements: labelled with the deployment's
#: execution provenance whatever the trade record is.
LIMIT_KEYS = {"max_daily_loss_pct", "max_drawdown_limit_pct"}


def _figures(node, key: str = "") -> list[dict]:
    out: list[dict] = []
    if isinstance(node, dict):
        if {"provenance", "value", "unit"} <= node.keys() and key not in LIMIT_KEYS:
            out.append(node)
        for name, value in node.items():
            out.extend(_figures(value, name))
    elif isinstance(node, list):
        for item in node:
            out.extend(_figures(item, key))
    return out


# ------------------------------------------------------------ with journal


def test_trades_are_the_journal_rows(journal_client):
    body = journal_client.get("/api/trading/trades?limit=500").json()
    assert body["total"] == 283
    assert body["source"]["kind"] == "live"
    assert body["source"]["as_of"].startswith("2025-12-31T21:00:00")
    assert {r["provenance"] for r in body["items"]} == {"paper"}
    row = body["items"][0]
    assert row["net_pnl"]["unit"] == "USD", "a USD P&L must not be labelled GBP"
    assert row["size"]["unit"] == "units"
    assert row["net_pnl"]["as_of"] is not None
    assert row["r_multiple"]["value"] is None
    assert row["r_multiple"]["caveats"][0]["code"] == "value_unavailable"
    assert "paper_journal" in {c["code"] for c in body["caveats"]}

    paper = journal_client.get("/api/trading/trades?provenance=paper&limit=50").json()
    assert paper["items"] and {r["provenance"] for r in paper["items"]} == {"paper"}


def test_positions_are_the_journal_open_book(journal_client):
    body = journal_client.get("/api/trading/positions").json()
    assert body["source"]["kind"] == "live"
    (row,) = body["items"]
    assert row["provenance"] == "paper"
    assert row["instrument"] == "XAUUSD"
    assert row["mark_price"]["value"] is None
    assert row["unrealised_pnl"]["unit"] == "USD"
    assert row["unrealised_pnl"]["value"] == pytest.approx(136_875.69, abs=0.01)


def test_portfolio_account_comes_from_the_session_summaries(journal_client):
    body = journal_client.get("/api/trading/portfolio").json()
    data = body["data"]
    assert body["source"]["kind"] == "live"
    assert data["balance"]["provenance"] == "paper"
    assert data["balance"]["unit"] == "USD"
    assert data["balance"]["value"] == pytest.approx(182_618.54, abs=0.02)
    assert data["realised_pnl"]["value"] == pytest.approx(-17_381.46, abs=0.02)
    assert data["open_positions"]["value"] == 1.0
    assert data["balance"]["as_of"].startswith("2025-12-31T21:00:00")
    assert data["provenance_mix"] == {"paper": 283}
    assert len(data["equity_curve"]["points"]) == 283
    assert data["max_drawdown_pct"]["value"] is not None
    codes = [c["message"] for c in body["caveats"]]
    assert any("independent paper sessions" in m for m in codes)


def test_risk_uses_the_journal_account(journal_client):
    body = journal_client.get("/api/trading/risk").json()
    data = body["data"]
    assert body["source"]["kind"] == "live"
    assert data["drawdown_pct"]["provenance"] == "paper"
    assert data["drawdown_pct"]["value"] is not None
    assert data["drawdown_pct"]["sample_size"] == 283
    assert data["daily_loss_pct"]["value"] is not None
    assert data["margin_utilisation_pct"]["value"] is None


def test_exposure_is_paper_and_marked_estimated_at_entry(journal_client):
    body = journal_client.get("/api/trading/exposure").json()
    assert body["source"]["kind"] == "live"
    assert body["items"]
    assert {f["provenance"] for f in _figures(body["items"])} == {"paper"}
    assert "exposure_at_entry_price" in {c["code"] for c in body["caveats"]}
    instrument = next(r for r in body["items"] if r["key"] == "instrument:XAUUSD")
    assert instrument["exposure_pct"]["estimated"] is True


# ---------------------------------------------------------- without journal


@pytest.mark.parametrize("path", ROUTES)
def test_seed_surfaces_are_labelled_seed(seed_client, path):
    body = seed_client.get(path).json()
    assert body["source"]["kind"] == "seed"
    assert "seed_fixture" in {c["code"] for c in body["caveats"]}


@pytest.mark.parametrize("path", ROUTES)
def test_no_seed_value_carries_an_executed_provenance(seed_client, path):
    """An executed provenance may only appear on a figure that is MISSING."""
    body = seed_client.get(path).json()
    for fig in _figures(body):
        if fig["provenance"] in EXECUTED:
            assert fig["value"] is None, (path, fig)
    for row in body.get("items", []):
        if "provenance" in row:
            assert row["provenance"] not in EXECUTED, (path, row)


def test_seed_account_is_unknown_not_25000(seed_client):
    data = seed_client.get("/api/trading/portfolio").json()["data"]
    for key in ("balance", "equity", "realised_pnl", "unrealised_pnl", "open_positions"):
        assert data[key]["value"] is None, key
        assert data[key]["caveats"][0]["code"] == "value_unavailable"
    assert data["equity_curve"]["points"] == []


def test_seed_risk_is_unknown_not_zero(seed_client):
    data = seed_client.get("/api/trading/risk").json()["data"]
    assert data["daily_loss_pct"]["value"] is None
    assert data["drawdown_pct"]["value"] is None


def test_seed_paper_filter_is_empty_and_says_seed(seed_client):
    body = seed_client.get("/api/trading/trades?provenance=paper").json()
    assert body["items"] == []
    assert body["source"]["kind"] == "seed"
