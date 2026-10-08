"""GET /api/command/attention: ranked on the server, with its weights pinned."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from fiboki.api.platform import WorkerBeat
from fiboki.api.provenance import Figure
from fiboki.api.routers.command import (
    CATEGORY_WEIGHT,
    SEVERITY_WEIGHT,
    AttentionItem,
    current_workers,
    rank_attention,
    score_of,
)
from fiboki.core.enums import Provenance
from tests.api.conftest import csrf_headers

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _item(id: str, category: str, severity: str, as_of: datetime | None = NOW) -> AttentionItem:
    return AttentionItem(
        id=id,
        category=category,
        severity=severity,
        title=id,
        reason=id,
        deep_link="/",
        as_of=as_of,
        score=Figure(value=float(score_of(category, severity)), provenance=Provenance.PAPER),
    )


def test_the_weights_are_pinned():
    """Changing a weight reorders every operator's queue: make it a reviewed diff."""
    assert SEVERITY_WEIGHT == {"critical": 1000, "error": 700, "warning": 400, "info": 200}
    assert CATEGORY_WEIGHT == {
        "kill_switch": 90,
        "limit_breach": 80,
        "incident": 70,
        "stale_worker": 60,
        "health": 50,
        "strategy_review": 40,
        "caveat": 0,
    }


def test_severity_always_dominates_category():
    gaps = sorted(SEVERITY_WEIGHT.values())
    smallest_gap = min(b - a for a, b in pairwise(gaps))
    spread = max(CATEGORY_WEIGHT.values()) - min(CATEGORY_WEIGHT.values())
    assert smallest_gap > spread
    ranked = rank_attention(
        [_item("warn-halt", "kill_switch", "warning"), _item("crit-caveat", "caveat", "critical")]
    )
    assert [i.id for i in ranked] == ["crit-caveat", "warn-halt"]


def test_within_a_severity_category_decides_then_recency_then_id():
    older = NOW - timedelta(hours=1)
    ranked = rank_attention(
        [
            _item("b-review", "strategy_review", "error"),
            _item("a-incident-old", "incident", "error", as_of=older),
            _item("z-incident-new", "incident", "error"),
            _item("breach", "limit_breach", "error"),
            _item("y-incident-new", "incident", "error"),
        ]
    )
    assert [i.id for i in ranked] == [
        "breach",
        "y-incident-new",
        "z-incident-new",
        "a-incident-old",
        "b-review",
    ]


def test_a_replaced_process_is_not_a_stale_worker():
    """The old pid stays idle in the table after launchd starts a new one."""
    old = WorkerBeat(
        "paper@old:1", "paper", "idle", NOW - timedelta(hours=2), 7200.0
    )
    live = WorkerBeat("paper@new:2", "paper", "idle", NOW, 1.0)
    stopped = WorkerBeat(
        "paper@dead:3", "paper", "stopped", NOW, 1.0
    )
    chosen = current_workers([old, stopped, live])
    assert [w.worker_id for w in chosen] == ["paper@new:2"]


def test_an_unset_as_of_sorts_after_a_dated_item():
    ranked = rank_attention(
        [_item("undated", "health", "warning", as_of=None), _item("dated", "health", "warning")]
    )
    assert [i.id for i in ranked] == ["dated", "undated"]


def test_endpoint_returns_a_ranked_queue_with_deep_links(client, monkeypatch):
    monkeypatch.delenv("FIBOKI_ALERT_LOG", raising=False)
    body = client.get("/api/command/attention").json()
    items = body["items"]
    assert items, "a deployment with no worker and a seed fixture has plenty to say"
    scores = [i["score"]["value"] for i in items]
    assert scores == sorted(scores, reverse=True)
    by_id = {i["id"]: i for i in items}
    # No worker has ever beaten here.
    worker = by_id["stale_worker:file_missing"]
    assert (worker["category"], worker["severity"], worker["deep_link"]) == (
        "stale_worker",
        "error",
        "/system",
    )
    # The seed fixture is served, which is a warning-level caveat on the record.
    assert any(i["category"] == "caveat" and "seed_fixture" in i["id"] for i in items)
    # The missing alert log is itself an item: an empty incident list is not quiet.
    assert "caveat:alert_log_not_configured" in by_id
    assert all(i["score"]["provenance"] == "paper" for i in items)
    assert body["caveats"][0]["code"] == "attention_ranking"


def test_an_armed_flatten_goes_to_the_top(admin_client, monkeypatch):
    monkeypatch.delenv("FIBOKI_ALERT_LOG", raising=False)
    admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "flatten", "reason": "attention ranking drill"},
        headers=csrf_headers(admin_client),
    ).raise_for_status()
    items = admin_client.get("/api/command/attention").json()["items"]
    assert items[0]["id"] == "kill_switch:armed"
    assert items[0]["severity"] == "critical"
    assert items[0]["deep_link"] == "/risk"
    # The arm also opened an incident, ranked just below the switch itself.
    incident = next(i for i in items if i["category"] == "incident")
    assert items.index(incident) == 1


def test_fixture_exposure_never_raises_a_breach(client, monkeypatch):
    """The seed fixture's generated positions breach limits; that is not news."""
    monkeypatch.delenv("FIBOKI_ALERT_LOG", raising=False)
    assert any(r["breached"] for r in client.get("/api/trading/exposure").json()["items"])
    items = client.get("/api/command/attention").json()["items"]
    assert not any(i["category"] == "limit_breach" for i in items)


def test_journal_exposure_breaches_are_critical(tmp_path, api_env):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from fiboki.api.app import create_app
    from fiboki.api.settings import load_settings
    from tests.api.conftest import ORIGIN

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"
    shutil.copytree(fixture, tmp_path / "paper")
    env = {**api_env, "FIBOKI_PAPER_ROOT": str(tmp_path / "paper")}
    with TestClient(create_app(load_settings(env), configure_logs=False), base_url=ORIGIN) as c:
        platform = c.app.state.platform
        # The fixture's one open XAUUSD position is 15% of equity; a 10% cap
        # makes the measured (not generated) book breach it.
        platform.limits = dataclasses.replace(platform.limits, max_instrument_exposure_pct=10.0)
        breached = [r for r in c.get("/api/trading/exposure").json()["items"] if r["breached"]]
        items = c.get("/api/command/attention").json()["items"]
    assert [r["key"] for r in breached] == ["instrument:XAUUSD"]
    ids = {i["id"] for i in items if i["category"] == "limit_breach"}
    assert ids == {"limit_breach:instrument:XAUUSD"}
    assert items[0]["id"] == "limit_breach:instrument:XAUUSD"
    assert all(
        i["severity"] == "critical" for i in items if i["category"] == "limit_breach"
    )


def test_an_acknowledged_incident_leaves_the_queue(admin_client, tmp_path, monkeypatch):
    log = tmp_path / "alerts.jsonl"
    at = datetime.now(tz=UTC) - timedelta(minutes=30)
    log.write_text(
        json.dumps(
            {
                "event": "reconciliation_divergence",
                "severity": "critical",
                "message": "book and venue disagree",
                "at": at.isoformat(),
                "source": "live",
                "dedupe_key": "reconciliation_divergence:live",
            }
        )
        + "\n"
    )
    monkeypatch.setenv("FIBOKI_ALERT_LOG", str(log))
    items = admin_client.get("/api/command/attention").json()["items"]
    incident = next(i for i in items if i["category"] == "incident")
    assert incident["severity"] == "critical"
    incident_id = incident["deep_link"].rsplit("/", 1)[-1]
    admin_client.post(
        f"/api/system/incidents/{incident_id}/ack",
        json={"reason": "reconciled by hand, venue was right"},
        headers=csrf_headers(admin_client),
    ).raise_for_status()
    after = admin_client.get("/api/command/attention").json()["items"]
    assert not any(i["category"] == "incident" for i in after)


def test_a_source_that_raises_is_an_item_not_a_silent_gap(client, monkeypatch):
    monkeypatch.delenv("FIBOKI_ALERT_LOG", raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("lifecycle store corrupt")

    monkeypatch.setattr(type(client.app.state.platform), "lifecycle", property(boom))
    body = client.get("/api/command/attention").json()
    ids = {i["id"] for i in body["items"]}
    assert "source_unreadable:lifecycle" in ids
    assert any(c["code"] == "attention_incomplete" for c in body["caveats"])
