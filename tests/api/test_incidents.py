"""Incident read model: derived from the alert log and the kill-switch journal,
deduplicated, with a timeline, and an audited acknowledge and note."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.routers.incidents import (
    REOPEN_AFTER,
    derive_incidents,
    incident_title,
    occurrences_from_alerts,
)
from fiboki.api.settings import load_settings
from fiboki.core.enums import Provenance
from tests.api.conftest import ADMIN_PW, OPERATOR_PW, ORIGIN, csrf_headers, login

#: Two hours ago, so an acknowledgement made now is after every occurrence.
T0 = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(hours=2)


def _alert(event: str, at: datetime, *, severity: str = "error", source: str = "watchdog",
           message: str = "", **extra) -> dict:
    return {
        "event": event,
        "severity": severity,
        "message": message or f"{event} from {source}",
        "at": at.isoformat(),
        "source": source,
        "correlation_id": "",
        "dedupe_key": extra.pop("dedupe_key", f"{event}:{source}"),
        "context": {},
    }


@pytest.fixture
def alert_log(tmp_path, monkeypatch):
    path = tmp_path / "alerts.jsonl"
    rows = [
        _alert("heartbeat_stale", T0),
        _alert("heartbeat_stale", T0 + timedelta(minutes=5)),
        _alert("heartbeat_stale", T0 + timedelta(minutes=10)),
        _alert("worker_down", T0 + timedelta(minutes=11), severity="critical"),
        # Trade lifecycle at info: not an incident.
        _alert("signal_generated", T0, severity="info", source="donchian"),
        # Owned by the kill-switch journal: ignored here, never double counted.
        _alert("kill_switch_activated", T0, severity="critical", source="api"),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    monkeypatch.setenv("FIBOKI_ALERT_LOG", str(path))
    return path


@pytest.fixture
def incident_client(api_env, alert_log):
    app = create_app(load_settings(api_env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def _by_event(body) -> dict[str, dict]:
    return {i["event"]: i for i in body["items"]}


def test_repeated_alerts_fold_into_one_incident_with_a_timeline(incident_client):
    body = incident_client.get("/api/system/incidents").json()
    events = _by_event(body)
    assert set(events) == {"heartbeat_stale", "worker_down"}
    stale = events["heartbeat_stale"]
    assert stale["occurrences"]["value"] == 3
    assert stale["occurrences"]["provenance"] == "paper"
    assert stale["status"] == "open"
    assert [e["kind"] for e in stale["timeline"]] == ["occurrence"] * 3
    assert stale["deep_link"] == f"/system/incidents/{stale['id']}"
    assert events["worker_down"]["severity"] == "critical"
    codes = {c["code"] for c in body["caveats"]}
    assert "alert_log_lines_skipped" in codes


def test_incident_ids_are_stable_across_reads_and_processes(incident_client, api_env):
    first = {i["id"] for i in incident_client.get("/api/system/incidents").json()["items"]}
    app2 = create_app(load_settings(api_env), configure_logs=False)
    with TestClient(app2, base_url=ORIGIN) as other:
        second = {i["id"] for i in other.get("/api/system/incidents").json()["items"]}
    assert first == second


def test_a_feed_failure_title_drops_the_exception_chain():
    message = (
        "paper worker cycle failed 1x consecutively: ProviderError: "
        "every candle fetch failed: EURUSD: ConnectError: [Errno 8] nodename"
    )
    assert incident_title(message) == "paper worker cycle failed 1x consecutively"
    rows = [_alert("broker_unhealthy", T0, message=message, dedupe_key="cycle_fail:paper:broker_unhealthy")]
    (incident,) = derive_incidents(occurrences_from_alerts(rows), [], count_provenance=Provenance.PAPER)
    assert incident.title == "paper worker cycle failed 1x consecutively"
    assert incident.timeline[0].text == message


def test_a_gap_longer_than_the_reopen_window_is_a_new_incident():
    rows = [
        _alert("data_stale", T0),
        _alert("data_stale", T0 + REOPEN_AFTER + timedelta(minutes=1)),
    ]
    incidents = derive_incidents(occurrences_from_alerts(rows), [], count_provenance=Provenance.PAPER)
    assert len(incidents) == 2
    assert len({i.id for i in incidents}) == 2


def test_no_alert_log_is_a_caveat_not_a_quiet_system(client, monkeypatch):
    monkeypatch.delenv("FIBOKI_ALERT_LOG", raising=False)
    body = client.get("/api/system/incidents").json()
    assert body["items"] == []
    (caveat,) = [c for c in body["caveats"] if c["code"] == "alert_log_not_configured"]
    assert caveat["severity"] == "warning"


def test_kill_switch_arm_opens_and_disarm_resolves_an_incident(admin_client, monkeypatch):
    monkeypatch.delenv("FIBOKI_ALERT_LOG", raising=False)
    admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "flatten", "reason": "incident drill, flatten all"},
        headers=csrf_headers(admin_client),
    ).raise_for_status()
    (incident,) = admin_client.get("/api/system/incidents").json()["items"]
    assert (incident["event"], incident["status"], incident["severity"]) == (
        "kill_switch_activated",
        "open",
        "critical",
    )
    admin_client.post(
        "/api/system/kill-switch/disarm",
        json={"reason": "drill complete, disarming"},
        headers=csrf_headers(admin_client),
    ).raise_for_status()
    (incident,) = admin_client.get("/api/system/incidents").json()["items"]
    assert incident["status"] == "resolved"
    assert [e["kind"] for e in incident["timeline"]] == ["occurrence", "resolved"]


# ------------------------------------------------------------------ writes


def _stale_id(client) -> str:
    return _by_event(client.get("/api/system/incidents").json())["heartbeat_stale"]["id"]


def test_admin_acknowledges_with_a_reason_and_it_is_audited(incident_client):
    assert login(incident_client, "joe", ADMIN_PW).status_code == 200
    incident_id = _stale_id(incident_client)
    response = incident_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "worker restarted by hand"},
        headers=csrf_headers(incident_client),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "acknowledged"
    assert data["acknowledged_by"] == "joe"
    assert data["timeline"][-1]["kind"] == "ack"
    audit = incident_client.app.state.audit.tail(5)
    assert audit[0].action == "incident.ack"
    assert audit[0].outcome == "allowed"
    assert audit[0].target == incident_id
    assert audit[0].reason == "worker restarted by hand"
    # A second acknowledgement of the same, unrecurred incident is refused.
    again = incident_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "acknowledging it twice"},
        headers=csrf_headers(incident_client),
    )
    assert again.status_code == 409
    assert again.json()["code"] == "incident_already_acknowledged"


def test_a_note_is_appended_to_the_timeline_and_audited(incident_client):
    assert login(incident_client, "joe", ADMIN_PW).status_code == 200
    incident_id = _stale_id(incident_client)
    response = incident_client.post(
        f"/api/system/incidents/{incident_id}/note",
        json={"text": "Laptop slept; lid closed at 02:55."},
        headers=csrf_headers(incident_client),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "open", "a note is not an acknowledgement"
    assert data["timeline"][-1] == {
        **data["timeline"][-1],
        "kind": "note",
        "actor": "joe",
        "text": "Laptop slept; lid closed at 02:55.",
    }
    assert incident_client.app.state.audit.tail(1)[0].action == "incident.note"


def test_a_recurrence_after_the_ack_re_opens_the_incident(incident_client, alert_log):
    assert login(incident_client, "joe", ADMIN_PW).status_code == 200
    incident_id = _stale_id(incident_client)
    incident_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "seen, investigating"},
        headers=csrf_headers(incident_client),
    ).raise_for_status()
    later = datetime.now(tz=UTC) + timedelta(seconds=5)
    with alert_log.open("a") as handle:
        handle.write(json.dumps(_alert("heartbeat_stale", later)) + "\n")
    items = incident_client.get("/api/system/incidents").json()["items"]
    (stale,) = [i for i in items if i["event"] == "heartbeat_stale"]
    assert stale["id"] == incident_id, "within the reopen window: the same incident"
    assert stale["status"] == "open", "a recurrence after the ack re-opens it"
    assert stale["occurrences"]["value"] == 4
    assert [e["kind"] for e in stale["timeline"]][-2:] == ["ack", "occurrence"]


def test_ack_needs_a_real_reason(incident_client):
    assert login(incident_client, "joe", ADMIN_PW).status_code == 200
    incident_id = _stale_id(incident_client)
    response = incident_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "ok"},
        headers=csrf_headers(incident_client),
    )
    assert response.status_code == 422


def test_ack_needs_csrf_and_origin(incident_client):
    assert login(incident_client, "joe", ADMIN_PW).status_code == 200
    incident_id = _stale_id(incident_client)
    no_csrf = incident_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "no csrf token sent"},
        headers={"Origin": ORIGIN},
    )
    assert (no_csrf.status_code, no_csrf.json()["code"]) == (403, "csrf_failed")
    foreign = csrf_headers(incident_client)
    foreign["Origin"] = "https://evil.example"
    response = incident_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "foreign origin post"},
        headers=foreign,
    )
    assert (response.status_code, response.json()["code"]) == (403, "origin_not_allowed")


def test_an_operator_cannot_acknowledge_yet_and_the_refusal_is_audited(incident_client):
    """Admin-only until the security test's policy is deliberately changed."""
    assert login(incident_client, "tom", OPERATOR_PW).status_code == 200
    incident_id = _stale_id(incident_client)
    response = incident_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "operator acknowledging"},
        headers=csrf_headers(incident_client),
    )
    assert response.status_code == 403
    assert incident_client.app.state.audit.tail(1)[0].outcome == "refused"


def test_ack_of_an_unknown_incident_is_404_and_audited(incident_client):
    assert login(incident_client, "joe", ADMIN_PW).status_code == 200
    response = incident_client.post(
        "/api/system/incidents/inc_doesnotexist/ack",
        json={"reason": "acknowledging nothing"},
        headers=csrf_headers(incident_client),
    )
    assert response.status_code == 404
    entry = incident_client.app.state.audit.tail(1)[0]
    assert (entry.action, entry.outcome) == ("incident.ack", "failed")


def test_incident_detail_route(incident_client):
    incident_id = _stale_id(incident_client)
    body = incident_client.get(f"/api/system/incidents/{incident_id}").json()
    assert body["data"]["id"] == incident_id
    assert incident_client.get("/api/system/incidents/inc_nope").status_code == 404
