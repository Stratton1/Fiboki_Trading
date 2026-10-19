"""Market Pulse (GET /api/markets/pulse): movers, currency strength, volatility, sessions, all server-side."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import fiboki.api.routers.monitors as monitors
from fiboki.data.recorder import MarketState, QuoteRecord, QuoteRecorder
from tests.api.conftest import OPERATOR_PW, login

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # Monday: FX and metals open
PATHS = {  # symbol: (first mid, last mid)
    "EURUSD": (1.1000, 1.1110),   # +1.00 %
    "GBPUSD": (1.3000, 1.2935),   # -0.50 %
    "USDJPY": (150.00, 150.30),   # +0.20 %
    "XAUUSD": (2600.0, 2652.0),   # +2.00 % (not FX: out of strength)
}


def seed(state: Path) -> None:
    rows = []
    for sym, (a, b) in PATHS.items():
        for i in range(25):  # hourly-ish over the last 24 h, linear path
            t = NOW - timedelta(hours=24) + timedelta(minutes=58 * i, seconds=30)
            mid = a + (b - a) * i / 24
            rows.append(QuoteRecord(timestamp=t, instrument=sym, bid=mid * (1 - 1e-5), ask=mid * (1 + 1e-5),
                                    provider="oanda_practice_pricing", market_state=MarketState.OPEN))
    rows.sort(key=lambda r: (r.timestamp, r.instrument))
    with QuoteRecorder(state / "quotes") as rec:
        for r in rows:
            rec.record(r)


@pytest.fixture
def pulsed(client, api_env, monkeypatch):
    monkeypatch.setattr(monitors, "_now", lambda at=None: NOW)
    seed(Path(api_env["FIBOKI_STATE_DIR"]))
    login(client, "tom", OPERATOR_PW)
    return client


def test_pulse_covers_every_recorded_instrument(pulsed):
    view = pulsed.get("/api/markets/pulse?window=24h").json()["data"]
    assert view["instruments_recorded"] == 4 and view["instruments_registered"] > 100
    assert sorted(r["symbol"] for r in view["rows"]) == sorted(PATHS)
    assert len(view["sessions"]) == 4 and {s["key"] for s in view["sessions"]} >= {"london", "new_york"}


def test_movers_by_hand(pulsed):
    view = pulsed.get("/api/markets/pulse").json()["data"]
    assert [m["symbol"] for m in view["movers_up"]] == ["XAUUSD", "EURUSD", "USDJPY"]
    assert [m["symbol"] for m in view["movers_down"]] == ["GBPUSD"]
    eur = next(m for m in view["movers_up"] if m["symbol"] == "EURUSD")
    assert eur["change_pct"]["value"] == pytest.approx(1.0, abs=1e-6)
    assert eur["change_pct"]["provenance"] == "broker_demo"


def test_currency_strength_by_hand(pulsed):
    view = pulsed.get("/api/markets/pulse").json()["data"]
    by = {s["currency"]: s for s in view["strength"]}
    # EUR: +1.0 (EURUSD). GBP: -0.5 (GBPUSD). USD: mean(-1.0, +0.5, +0.2) = -0.1.
    assert by["EUR"]["score_pct"]["value"] == pytest.approx(1.0, abs=1e-6)
    assert by["GBP"]["score_pct"]["value"] == pytest.approx(-0.5, abs=1e-6)
    assert by["USD"]["score_pct"]["value"] == pytest.approx((-1.0 + 0.5 + 0.2) / 3, abs=1e-6)
    assert by["USD"]["pairs"]["value"] == 3 and "XAU" not in by
    assert [s["currency"] for s in view["strength"]][0] == "EUR"


def test_volatility_ranked_and_pulse_is_deterministic(pulsed):
    a = pulsed.get("/api/markets/pulse").json()
    b = pulsed.get("/api/markets/pulse").json()
    assert a["data"] == b["data"]
    vols = [v["realised_vol_pct"]["value"] for v in a["data"]["volatility"]]
    assert vols == sorted(vols, reverse=True) and all(v is not None and v >= 0 for v in vols)
    assert a["data"]["rows"][0]["tones"]  # the API's pulse tones are attached


def test_pulse_without_a_recorder_is_absent(client, monkeypatch):
    monkeypatch.setattr(monitors, "_now", lambda at=None: NOW)
    login(client, "tom", OPERATOR_PW)
    body = client.get("/api/markets/pulse").json()
    assert body["source"]["kind"] == "absent" and body["data"]["rows"] == []
    assert any(c["code"] == "recorder_never_ran" for c in body["caveats"])
