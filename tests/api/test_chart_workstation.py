"""Chart workstation routes (Wave 2B): compare, data window, events, templates, price alerts.

Synthetic and local: EURUSD and GBPUSD H1 datasets written through the real
store, one paper session, and the official calendar shipped in the repo.
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from tests.api.conftest import ADMIN_PW, OPERATOR_PW, ORIGIN, VIEWER_PW, csrf_headers, login
from tests.api.test_chart_replay import _write
from tests.api.test_overlays import _write_session
from tests.data_fixtures import make_bars

N = 600


@pytest.fixture
def world(tmp_path, api_env):
    eur = make_bars(periods=N)
    # GBPUSD misses three bars the EURUSD set has: compare must gap, not fill.
    gbp = make_bars(instrument="GBPUSD", periods=N, seed=5)
    gbp = gbp.drop(gbp.index[[50, 51, 52]])
    v_eur = _write(tmp_path / "data", eur)
    v_gbp = _write(tmp_path / "data", gbp)
    _write_session(tmp_path / "paper", eur)
    env = {
        **api_env,
        "FIBOKI_DATA_ROOT": str(tmp_path / "data"),
        "FIBOKI_PAPER_ROOT": str(tmp_path / "paper"),
    }
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        yield {"client": client, "eur": eur, "gbp": gbp, "v_eur": v_eur, "v_gbp": v_gbp}


# ------------------------------------------------------------------ compare


def test_compare_rebases_the_other_close_to_percent_on_the_base_bars(world):
    eur, gbp = world["eur"], world["gbp"]
    body = world["client"].get(
        "/api/markets/compare/EURUSD",
        params={"with": "GBPUSD", "timeframe": "H1", "limit": 100,
                "to": eur.index[99].isoformat()},
    ).json()
    data = body["data"]
    assert data["base"] == "EURUSD" and data["symbol"] == "GBPUSD"
    assert data["unit"] == "percent"
    assert data["base_dataset_version_id"] == world["v_eur"]
    assert data["dataset_version_id"] == world["v_gbp"]
    times = [pd.Timestamp(p["t"]) for p in data["points"]]
    assert times == list(eur.index[:100])
    anchor = float(gbp["close"].iloc[0])
    assert data["anchor_close"]["value"] == anchor
    assert data["points"][0]["v"] == 0.0
    for point in data["points"]:
        stamp = pd.Timestamp(point["t"])
        if stamp in gbp.index:
            expected = (float(gbp.loc[stamp, "close"]) / anchor - 1.0) * 100.0
            assert point["v"] == pytest.approx(expected, abs=1e-12)
        else:
            assert point["v"] is None
    assert data["unmatched"] == 3
    assert [c["code"] for c in body["caveats"]] == ["compare_unaligned_bars"]


def test_compare_is_deterministic_and_validates_its_inputs(world):
    client = world["client"]
    params = {"with": "GBPUSD", "timeframe": "H1", "limit": 50}
    first = client.get("/api/markets/compare/EURUSD", params=params).json()["data"]
    again = client.get("/api/markets/compare/EURUSD", params=params).json()["data"]
    assert first == again
    assert client.get("/api/markets/compare/EURUSD", params={"with": "NOPE"}).status_code == 404
    assert client.get(
        "/api/markets/compare/EURUSD", params={"with": "GBPUSD", "timeframe": "H7"}
    ).status_code == 400


def test_compare_without_market_data_is_unavailable_not_flat(client):
    response = client.get("/api/markets/compare/EURUSD", params={"with": "GBPUSD"})
    assert response.status_code == 503
    assert response.json()["code"] == "market_data_not_mounted"


# -------------------------------------------------------------- data window


def test_data_window_serves_every_indicator_and_overlay_at_one_bar(world):
    from fiboki.indicators import ATR, Ichimoku

    eur = world["eur"]
    # 30 minutes into bar 100 (the paper trade's entry bar): the bar is 100.
    at = eur.index[100] + pd.Timedelta(minutes=30)
    body = world["client"].get(
        "/api/markets/datawindow/EURUSD", params={"t": at.isoformat(), "timeframe": "H1"}
    ).json()
    data = body["data"]
    assert pd.Timestamp(data["t"]) == eur.index[100]
    assert pd.Timestamp(data["t_end"]) == eur.index[100] + pd.Timedelta(hours=1)
    assert data["dataset_version_id"] == world["v_eur"]
    assert data["bar"]["c"] == float(eur["close"].iloc[100])
    values = {v["name"]: v["value"] for v in data["values"]}
    ich = Ichimoku()
    expected = ich.compute(eur).iloc[100]
    assert values[f"{ich.name}_tenkan"] == float(expected[f"{ich.name}_tenkan"])
    # State columns are served too (the chart hides them by default).
    assert f"{ich.name}_price_vs_cloud" in values
    atr = ATR(14)
    assert values[atr.name] == float(atr.compute(eur)[atr.name].iloc[100])
    # The paper trade entered at bar 100's open: its entry fill is on this bar.
    assert [f["role"] for f in data["fills"]] == ["entry"]
    assert all(pd.Timestamp(e["t"]) >= eur.index[100] for e in data["events"])


def test_data_window_needs_a_bar_at_or_before_t(world):
    early = world["eur"].index[0] - pd.Timedelta(days=1)
    response = world["client"].get(
        "/api/markets/datawindow/EURUSD", params={"t": early.isoformat()}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "no_bar_at_time"


# ---------------------------------------------------------------- templates


TEMPLATE = {
    "layers": {"drawings": True, "trades": False, "events": True, "indicators": True},
    "groups": {"atr_14|atr_14": False, "ichimoku_9_26_52_26_26|price": True},
    "strategy_ids": ["donchian_breakout_atr"],
    "trade_source": "backtest",
}


def test_templates_are_saved_per_operator_versioned_and_audited(client):
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    empty = client.get("/api/chart/templates").json()["data"]
    assert empty["templates"] == [] and empty["can_edit"] is True
    url = "/api/chart/templates/Ichimoku%20only"
    created = client.put(url, json={"base_version": 0, "template": TEMPLATE}, headers=csrf_headers(client))
    assert created.status_code == 200, created.text
    [row] = created.json()["data"]["templates"]
    assert row["name"] == "Ichimoku only" and row["version"] == 1
    assert row["template"] == TEMPLATE
    # A stale write is refused, nothing overwritten.
    stale = client.put(url, json={"base_version": 0, "template": {}}, headers=csrf_headers(client))
    assert stale.status_code == 409 and stale.json()["code"] == "template_conflict"
    updated = client.put(url, json={"base_version": 1, "template": {**TEMPLATE, "trade_source": "both"}},
                         headers=csrf_headers(client))
    assert updated.json()["data"]["templates"][0]["version"] == 2
    # Another operator sees none of Tom's.
    client.post("/api/auth/logout", headers=csrf_headers(client))
    assert login(client, "joe", ADMIN_PW).status_code == 200
    assert client.get("/api/chart/templates").json()["data"]["templates"] == []
    client.post("/api/auth/logout", headers=csrf_headers(client))
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    gone = client.delete(f"{url}?base_version=2", headers=csrf_headers(client))
    assert gone.status_code == 200 and gone.json()["data"]["templates"] == []
    actions = [(e.action, e.outcome) for e in client.app.state.audit.entries()]
    assert ("chart.templates.put", "allowed") in actions
    assert ("chart.templates.put", "refused") in actions
    assert ("chart.templates.delete", "allowed") in actions


def test_a_template_is_display_choices_only(client):
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    bad_layer = client.put("/api/chart/templates/x", json={"base_version": 0, "template": {"layers": {"orders": True}}},
                           headers=csrf_headers(client))
    assert bad_layer.status_code == 422
    smuggled = client.put("/api/chart/templates/x",
                          json={"base_version": 0, "template": {"stop_loss": 1.1}},
                          headers=csrf_headers(client))
    assert smuggled.status_code == 422
    assert client.put("/api/chart/templates/..%2Fx", json={"base_version": 0, "template": {}},
                      headers=csrf_headers(client)).status_code in (404, 422)


def test_a_viewer_reads_but_cannot_save_templates(client):
    assert login(client, "guest", VIEWER_PW).status_code == 200
    assert client.get("/api/chart/templates").json()["data"]["can_edit"] is False
    response = client.put("/api/chart/templates/x", json={"base_version": 0, "template": {}},
                          headers=csrf_headers(client))
    assert response.status_code == 403


# ------------------------------------------------------------- price alerts


def test_price_alert_direction_is_decided_by_the_platform(world):
    client = world["client"]
    eur = world["eur"]
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    last = float(eur["close"].iloc[-1])
    above = client.post("/api/alerts/price", json={"symbol": "EURUSD", "price": last + 0.01,
                                                   "drawing_id": "d_1", "origin": "level"},
                        headers=csrf_headers(client))
    assert above.status_code == 201, above.text
    below = client.post("/api/alerts/price", json={"symbol": "EURUSD", "price": last - 0.01,
                                                   "origin": "zone_bottom"},
                        headers=csrf_headers(client))
    data = below.json()["data"]
    assert [a["condition"] for a in data["alerts"]] == ["cross_above", "cross_below"]
    first = data["alerts"][0]
    assert first["reference_close"] == last
    assert pd.Timestamp(first["reference_time"]) == eur.index[-1]
    assert first["dataset_version_id"] == world["v_eur"]
    assert first["drawing_id"] == "d_1" and first["status"] == "armed"
    # Honest about the gap: nothing evaluates these yet.
    assert data["evaluated"] is False
    assert [c["code"] for c in below.json()["caveats"]] == ["price_alerts_not_evaluated"]
    assert ("alerts.price.create", "allowed") in [
        (e.action, e.outcome) for e in client.app.state.audit.entries()
    ]
    listed = client.get("/api/alerts/price", params={"symbol": "EURUSD"}).json()["data"]["alerts"]
    assert len(listed) == 2
    at_close = client.post("/api/alerts/price", json={"symbol": "EURUSD", "price": last},
                           headers=csrf_headers(client))
    assert at_close.status_code == 422


def test_price_alerts_need_the_operator_role_and_csrf(world):
    client = world["client"]
    assert login(client, "guest", VIEWER_PW).status_code == 200
    refused = client.post("/api/alerts/price", json={"symbol": "EURUSD", "price": 1.2},
                          headers=csrf_headers(client))
    assert refused.status_code == 403
    client.post("/api/auth/logout", headers=csrf_headers(client))
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    no_csrf = client.post("/api/alerts/price", json={"symbol": "EURUSD", "price": 1.2},
                          headers={"Origin": ORIGIN})
    assert no_csrf.status_code == 403


# -------------------------------------------------------------- go to date


def test_bars_and_overlays_centre_on_a_date_when_asked(world):
    eur = world["eur"]
    client = world["client"]
    target = eur.index[200] + pd.Timedelta(minutes=20)
    bars = client.get(
        "/api/markets/bars/EURUSD", params={"timeframe": "H1", "limit": 100, "around": target.isoformat()}
    ).json()["data"]["bars"]
    times = [pd.Timestamp(b["t"]) for b in bars]
    assert times == list(eur.index[150:250])
    overlays = client.get(
        "/api/markets/overlays/EURUSD",
        params={"timeframe": "H1", "limit": 100, "around": target.isoformat()},
    ).json()["data"]
    assert pd.Timestamp(overlays["window_from"]) == eur.index[150]
    assert pd.Timestamp(overlays["window_to"]) == eur.index[249]
    # Near the end the window is clamped, never short.
    late = client.get(
        "/api/markets/bars/EURUSD",
        params={"timeframe": "H1", "limit": 100, "around": eur.index[-3].isoformat()},
    ).json()["data"]["bars"]
    assert [pd.Timestamp(b["t"]) for b in late] == list(eur.index[-100:])
    # Without `around` nothing changes: the latest bars.
    latest = client.get("/api/markets/bars/EURUSD", params={"timeframe": "H1", "limit": 5}).json()
    assert [pd.Timestamp(b["t"]) for b in latest["data"]["bars"]] == list(eur.index[-5:])


def test_levels_and_zones_are_stored_with_the_drawings_and_alert_from_their_edges(world):
    """A horizontal level and a rectangle zone round-trip through the drawing
    store; an alert created from the zone's top edge names the drawing."""
    client = world["client"]
    eur = world["eur"]
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    t0, t1 = eur.index[10].isoformat(), eur.index[40].isoformat()
    last = float(eur["close"].iloc[-1])
    drawings = [
        {"id": "lvl_1", "kind": "hline", "points": [{"t": t0, "price": last + 0.002}]},
        {"id": "zone_1", "kind": "rect", "points": [{"t": t0, "price": last - 0.004},
                                                    {"t": t1, "price": last - 0.001}]},
    ]
    put = client.put("/api/markets/drawings/EURUSD", json={"base_version": 0, "drawings": drawings},
                     headers=csrf_headers(client))
    assert put.status_code == 200, put.text
    stored = {d["id"]: d for d in client.get("/api/markets/drawings/EURUSD").json()["data"]["drawings"]}
    assert stored["lvl_1"]["kind"] == "hline" and stored["zone_1"]["kind"] == "rect"
    top = max(p["price"] for p in stored["zone_1"]["points"])
    made = client.post("/api/alerts/price", json={"symbol": "EURUSD", "price": top,
                                                  "drawing_id": "zone_1", "origin": "zone_top"},
                       headers=csrf_headers(client))
    assert made.status_code == 201
    [alert] = made.json()["data"]["alerts"]
    assert alert["drawing_id"] == "zone_1" and alert["origin"] == "zone_top"
    assert alert["condition"] == "cross_below"  # the zone sits under the last close


# ------------------------------------------------------------------ events


def test_events_come_from_the_newest_official_calendar_and_focus_by_id(client):
    from fiboki.marketstate.calendar import (
        latest_official_calendar_version,
        load_official_calendar,
    )

    version = latest_official_calendar_version()
    calendar = load_official_calendar(version=version)
    usd = [e for e in calendar.all_events() if e.currency == "USD"]
    assert usd, "the official calendar holds USD events"
    pick = usd[len(usd) // 2]
    lo = (pick.event_time - pd.Timedelta(days=3)).isoformat()
    hi = (pick.event_time + pd.Timedelta(days=3)).isoformat()
    body = client.get(
        "/api/markets/events/EURUSD", params={"from": lo, "to": hi, "event_id": pick.event_id}
    ).json()
    data = body["data"]
    assert data["calendar_version"] == version
    assert data["focus"]["event_id"] == pick.event_id
    assert pd.Timestamp(data["focus"]["t"]) == pick.event_time
    assert pick.event_id in {e["event_id"] for e in data["events"]}
    assert all(e["currency"] in ("EUR", "USD") for e in data["events"])
    assert {e["impact"] for e in data["events"]} <= {"high", "medium", "low"}
    assert all(e["dataset_version_id"].startswith(f"official_calendar:{version}") for e in data["events"])
    missing = client.get("/api/markets/events/EURUSD", params={"event_id": "nope"})
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_event"


def test_overlay_events_are_stamped_with_the_version_they_were_loaded_from(world):
    from fiboki.marketstate.calendar import latest_official_calendar_version

    data = world["client"].get("/api/markets/overlays/EURUSD", params={"timeframe": "H1"}).json()["data"]
    version = latest_official_calendar_version()
    for event in data["events"]:
        assert event["dataset_version_id"].startswith(f"official_calendar:{version}")
