"""Watchlists over recorded quotes (routers/monitors.py)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fiboki.data.recorder import MarketState, QuoteRecord, QuoteRecorder
from tests.api.conftest import OPERATOR_PW, VIEWER_PW, csrf_headers, login


def seed_quotes(state_dir, now: datetime) -> None:
    rows = []
    for i in range(60):  # EURUSD: one quote a minute for the last hour, rising
        rows.append(QuoteRecord(timestamp=now - timedelta(minutes=60 - i, seconds=-5), instrument="EURUSD",
                                bid=1.1000 + i * 0.0001, ask=1.1002 + i * 0.0001,
                                provider="oanda_practice_pricing", market_state=MarketState.OPEN))
    # GBPUSD: last quote ten minutes ago (stale while its session is open)
    rows.append(QuoteRecord(timestamp=now - timedelta(minutes=10), instrument="GBPUSD", bid=1.30, ask=1.3010,
                            provider="oanda_practice_pricing", market_state=MarketState.OPEN))
    rows.sort(key=lambda r: r.timestamp)
    with QuoteRecorder(state_dir / "quotes") as rec:
        for r in rows:
            rec.record(r)


@pytest.fixture
def quoted(client, api_env, monkeypatch):
    from pathlib import Path

    # Freeze the session calendars open so the verdicts do not depend on the weekday the suite runs.
    monkeypatch.setattr("fiboki.api.routers.monitors._session",
                        lambda symbol, now: __import__("fiboki.api.routers.monitors", fromlist=["SessionView"])
                        .SessionView(open=True, calendar="test", trading_hours="test"))
    seed_quotes(Path(api_env["FIBOKI_STATE_DIR"]), datetime.now(tz=UTC))
    login(client, "tom", OPERATOR_PW)
    return client


def test_builtin_lists_are_offered_until_saved_over(quoted):
    names = [w["name"] for w in quoted.get("/api/markets/watchlists").json()["items"]]
    assert names == ["Majors", "GBP crosses", "Metals and indices", "Crypto"]
    h = csrf_headers(quoted)
    saved = quoted.put("/api/markets/watchlists/Majors", json={"base_version": 0, "symbols": ["EURUSD", "GBPUSD"]}, headers=h)
    assert saved.status_code == 200 and saved.json()["items"][0]["version"] == 1
    items = quoted.get("/api/markets/watchlists").json()["items"]
    majors = next(w for w in items if w["name"] == "Majors")
    assert majors["builtin"] is False and majors["symbols"] == ["EURUSD", "GBPUSD"]
    assert sum(w["name"] == "Majors" for w in items) == 1


def test_watchlist_crud_is_versioned(quoted):
    h = csrf_headers(quoted)
    assert quoted.put("/api/markets/watchlists/Mine", json={"base_version": 0, "symbols": ["EURUSD"]}, headers=h).status_code == 200
    stale = quoted.put("/api/markets/watchlists/Mine", json={"base_version": 0, "symbols": ["GBPUSD"]}, headers=h)
    assert stale.status_code == 409 and stale.json()["code"] == "workspace_conflict"
    bad = quoted.put("/api/markets/watchlists/Mine", json={"base_version": 1, "symbols": ["NOPE"]}, headers=h)
    assert bad.status_code == 422
    dup = quoted.put("/api/markets/watchlists/Mine", json={"base_version": 1, "symbols": ["EURUSD", "eurusd"]}, headers=h)
    assert dup.status_code == 422
    gone = quoted.delete("/api/markets/watchlists/Mine?base_version=1", headers=h)
    assert gone.status_code == 200 and "Mine" not in [w["name"] for w in gone.json()["items"]]


def test_a_viewer_cannot_write_a_watchlist(client):
    login(client, "guest", VIEWER_PW)
    r = client.put("/api/markets/watchlists/X", json={"base_version": 0, "symbols": ["EURUSD"]}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_rows_carry_quote_sparkline_session_age_and_verdict(quoted):
    page = quoted.get("/api/markets/quotes?symbols=EURUSD,GBPUSD,USDJPY&window=1h").json()
    rows = {r["symbol"]: r for r in page["items"]}
    eur = rows["EURUSD"]
    assert eur["data_state"] == "live"
    assert eur["mid"]["provenance"] == "broker_demo" and eur["mid"]["value"] == pytest.approx(1.1060, abs=1e-9)
    assert eur["spread_pips"]["value"] == pytest.approx(2.0)
    assert eur["change_pct"]["value"] > 0 and eur["tones"]["change"] == "rise"
    assert len(eur["sparkline"]["points"]) == 48
    assert eur["session"]["open"] is True and eur["chart_href"] == "/markets/EURUSD"
    assert rows["GBPUSD"]["data_state"] == "stale" and rows["GBPUSD"]["tones"]["age"] == "stale"
    jpy = rows["USDJPY"]
    assert jpy["data_state"] == "none" and jpy["mid"]["value"] is None and jpy["age_s"]["value"] is None
    assert page["source"]["kind"] == "live"


def test_watchlist_quotes_and_an_absent_recorder(client):
    login(client, "tom", OPERATOR_PW)
    page = client.get("/api/markets/watchlists/Majors/quotes").json()
    assert [r["symbol"] for r in page["items"]][:2] == ["EURUSD", "GBPUSD"]
    assert page["source"]["kind"] == "absent"
    assert any(c["code"] == "recorder_never_ran" for c in page["caveats"])
    assert {r["data_state"] for r in page["items"]} <= {"none", "closed"}
    assert client.get("/api/markets/watchlists/Nope/quotes").status_code == 404
    assert client.get("/api/markets/quotes?symbols=EURUSD&window=7d").status_code == 422
