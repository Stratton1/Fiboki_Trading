"""Price and level alert routes: CRUD (versioned, audited), notifications, read marker, evaluator state."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fiboki.core.paths import resolve_paths
from fiboki.data.recorder import MarketState, QuoteRecord
from fiboki.obs.price_alerts import PriceAlertStore
from tests.api.conftest import OPERATOR_PW, VIEWER_PW, csrf_headers, login


def _store(api_env) -> PriceAlertStore:
    return PriceAlertStore(resolve_paths({"FIBOKI_STATE_DIR": api_env["FIBOKI_STATE_DIR"]}).price_alerts_db)


def _quote(at: datetime, mid: float) -> QuoteRecord:
    return QuoteRecord(timestamp=at, instrument="EURUSD", bid=mid - 0.0001, ask=mid + 0.0001,
                       provider="oanda_practice_pricing", market_state=MarketState.OPEN)


def test_rule_crud_is_versioned_and_audited(client):
    login(client, "tom", OPERATOR_PW)
    h = csrf_headers(client)
    created = client.post("/api/alerts/rules", json={"instrument": "eurusd", "kind": "above", "level": 1.1}, headers=h)
    assert created.status_code == 201, created.text
    rule = created.json()["data"]["rules"][0]
    assert rule["instrument"] == "EURUSD" and rule["state"] == "armed" and rule["version"] == 1
    assert rule["level"]["value"] == 1.1
    assert created.json()["data"]["evaluator"]["state"] == "never"
    upd = {"instrument": "EURUSD", "kind": "cross", "level": 1.2, "mode": "rearm", "base_version": 1}
    ok = client.put(f"/api/alerts/rules/{rule['id']}", json=upd, headers=h)
    assert ok.status_code == 200 and ok.json()["data"]["rules"][0]["version"] == 2
    stale = client.put(f"/api/alerts/rules/{rule['id']}", json=upd, headers=h)
    assert stale.status_code == 409 and stale.json()["code"] == "alert_rule_conflict"
    assert client.delete(f"/api/alerts/rules/{rule['id']}?base_version=2", headers=h).status_code == 204
    assert client.get("/api/alerts/rules").json()["data"]["rules"] == []
    actions = [(e.action, e.outcome) for e in client.app.state.audit.entries() if e.action.startswith("alerts.")]
    assert ("alerts.price_rule.create", "allowed") in actions and ("alerts.price_rule.update", "refused") in actions


def test_invalid_rules_are_refused(client):
    login(client, "tom", OPERATOR_PW)
    h = csrf_headers(client)
    assert client.post("/api/alerts/rules", json={"instrument": "NOPE", "kind": "above", "level": 1}, headers=h).status_code == 422
    assert client.post("/api/alerts/rules", json={"instrument": "EURUSD", "kind": "above", "level": -1}, headers=h).status_code == 422
    assert client.post("/api/alerts/rules", json={"instrument": "EURUSD", "kind": "buy", "level": 1}, headers=h).status_code == 422


def test_a_viewer_reads_but_cannot_write(client):
    login(client, "guest", VIEWER_PW)
    assert client.get("/api/alerts/rules").status_code == 200
    r = client.post("/api/alerts/rules", json={"instrument": "EURUSD", "kind": "above", "level": 1.1},
                    headers=csrf_headers(client))
    assert r.status_code == 403


def test_notifications_unread_and_read_marker(client, api_env):
    login(client, "tom", OPERATOR_PW)
    h = csrf_headers(client)
    client.post("/api/alerts/rules", json={"instrument": "EURUSD", "kind": "cross", "level": 1.1, "mode": "rearm"}, headers=h)
    now = datetime.now(tz=UTC)
    _store(api_env).evaluate([_quote(now + timedelta(seconds=1), 1.09), _quote(now + timedelta(seconds=2), 1.11),
                             _quote(now + timedelta(seconds=3), 1.09)], now=now)
    body = client.get("/api/alerts/notifications").json()["data"]
    assert body["total"] == 2 and body["unread"] == 2 and body["read_through"] == 0
    assert [n["read"] for n in body["items"]] == [False, False]
    assert body["items"][0]["tones"] == {"state": "accent"} and body["items"][0]["price"]["provenance"] == "broker_demo"
    assert body["evaluator"]["state"] == "live"
    newest = body["items"][0]["seq"]
    read = client.post("/api/alerts/notifications/read", json={"through_seq": newest}, headers=h).json()["data"]
    assert read["unread"] == 0 and read["read_through"] == newest and all(n["read"] for n in read["items"])
    back = client.post("/api/alerts/notifications/read", json={"through_seq": 0}, headers=h).json()["data"]
    assert back["read_through"] == newest


def test_a_silent_evaluator_is_stale(client, api_env):
    login(client, "tom", OPERATOR_PW)
    store = _store(api_env)
    store.evaluate([_quote(datetime(2026, 1, 1, tzinfo=UTC), 1.1)], now=datetime(2026, 1, 1, tzinfo=UTC))
    assert client.get("/api/alerts/notifications").json()["data"]["evaluator"]["state"] == "stale"
    assert Path(store.path).exists()
