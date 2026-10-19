"""Workspace layouts: registry, presets, versioned server-side layouts, column sets."""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from fiboki.api.workspace_layouts import PANEL_KINDS, PRESETS, LayoutDoc
from tests.api.conftest import ADMIN_PW, OPERATOR_PW, VIEWER_PW, csrf_headers, login

REQUIRED_KINDS = {
    "chart", "watchlist", "news", "calendar", "fleet", "risk", "exposure", "alerts",
    "journal", "research_runs", "market_pulse", "system_board", "notes",
}


def _preset(key: str) -> dict:
    return next(p for p in PRESETS if p.key == key).layout.model_dump(mode="json", by_alias=True)


def test_registry_covers_every_required_kind(client):
    login(client, "joe", ADMIN_PW)
    body = client.get("/api/workspace/panels").json()
    kinds = {item["kind"] for item in body["items"]}
    assert kinds >= REQUIRED_KINDS
    assert set(PANEL_KINDS) == kinds
    chart = next(i for i in body["items"] if i["kind"] == "chart")
    assert chart["follows_symbol"] is True and chart["reads"]


def test_presets_are_the_three_named_layouts_and_validate(client):
    login(client, "joe", ADMIN_PW)
    body = client.get("/api/workspace/presets").json()
    names = [p["name"] for p in body["items"]]
    assert names == ["FX monitoring", "Research", "Incident"]
    for preset in body["items"]:
        LayoutDoc.model_validate(preset["layout"])  # round-trips through the contract
    fx = body["items"][0]["layout"]
    groups = {p["link"]["group"] for p in fx["panels"]}
    assert groups == {"amber"}
    roles = {p["kind"]: p["link"]["role"] for p in fx["panels"]}
    assert roles["watchlist"] == "source" and roles["chart"] == "destination"


def test_layout_lifecycle_is_versioned_and_409s_on_a_stale_version(client):
    login(client, "tom", OPERATOR_PW)
    h = csrf_headers(client)
    assert client.get("/api/workspace/layouts/Desk").status_code == 404
    created = client.put(
        "/api/workspace/layouts/Desk", json={"base_version": 0, "layout": _preset("fx-monitoring")},
        headers=h,
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["version"] == 1
    edited = _preset("fx-monitoring")
    edited["panels"][1]["symbol"] = "GBPUSD"
    second = client.put(
        "/api/workspace/layouts/Desk",
        json={"base_version": 1, "layout": edited, "action": "autosave"},
        headers=h,
    )
    assert second.status_code == 200 and second.json()["data"]["version"] == 2
    stale = client.put(
        "/api/workspace/layouts/Desk", json={"base_version": 1, "layout": _preset("research")},
        headers=h,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "workspace_conflict"
    assert stale.json()["context"]["current_version"] == 2
    # The stale write changed nothing.
    got = client.get("/api/workspace/layouts/Desk").json()["data"]
    assert got["version"] == 2 and got["layout"]["panels"][1]["symbol"] == "GBPUSD"
    listing = client.get("/api/workspace/layouts").json()
    assert [i["name"] for i in listing["items"]] == ["Desk"]
    history = client.get("/api/workspace/layouts/Desk/history").json()["items"]
    assert [(r["version"], r["action"], r["actor"]) for r in history] == [
        (2, "autosave", "tom"),
        (1, "save", "tom"),
    ]


def test_delete_is_a_tombstone_and_a_stale_tab_cannot_resurrect(client):
    login(client, "tom", OPERATOR_PW)
    h = csrf_headers(client)
    client.put("/api/workspace/layouts/Tmp", json={"base_version": 0, "layout": _preset("incident")}, headers=h)
    assert client.delete("/api/workspace/layouts/Tmp?base_version=1", headers=h).status_code == 204
    assert client.get("/api/workspace/layouts/Tmp").status_code == 404
    again = client.put("/api/workspace/layouts/Tmp", json={"base_version": 0, "layout": _preset("incident")}, headers=h)
    assert again.status_code == 409
    revived = client.put("/api/workspace/layouts/Tmp", json={"base_version": 2, "layout": _preset("incident")}, headers=h)
    assert revived.status_code == 200 and revived.json()["data"]["version"] == 3


def test_layouts_are_per_operator(client):
    login(client, "tom", OPERATOR_PW)
    client.put("/api/workspace/layouts/Mine", json={"base_version": 0, "layout": _preset("research")},
               headers=csrf_headers(client))
    client.post("/api/auth/logout", headers=csrf_headers(client))
    login(client, "joe", ADMIN_PW)
    assert client.get("/api/workspace/layouts/Mine").status_code == 404
    assert client.get("/api/workspace/layouts").json()["items"] == []


def test_a_viewer_reads_but_cannot_write(client):
    login(client, "guest", VIEWER_PW)
    assert client.get("/api/workspace/presets").status_code == 200
    refused = client.put("/api/workspace/layouts/X", json={"base_version": 0, "layout": _preset("research")},
                         headers=csrf_headers(client))
    assert refused.status_code == 403


def test_writes_are_audited(client):
    login(client, "tom", OPERATOR_PW)
    h = csrf_headers(client)
    client.put("/api/workspace/layouts/A", json={"base_version": 0, "layout": _preset("research")}, headers=h)
    client.put("/api/workspace/layouts/A", json={"base_version": 0, "layout": _preset("research")}, headers=h)
    rows = client.app.state.audit.entries()
    events = [(r.action, r.outcome) for r in rows if r.action.startswith("workspace.")]
    assert ("workspace.layout.save", "allowed") in events
    assert ("workspace.layout.save", "refused") in events


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d["panels"].append(copy.deepcopy(d["panels"][0])), "unique"),
        (lambda d: d["panels"].pop(), "exactly once"),
        (lambda d: d["root"]["sizes"].__setitem__(0, 80), "sum"),
        (lambda d: d["panels"][0].__setitem__("kind", "order_ticket"), "kind"),
    ],
)
def test_malformed_layouts_are_refused(mutate, needle):
    doc = _preset("fx-monitoring")
    mutate(doc)
    with pytest.raises(ValidationError) as err:
        LayoutDoc.model_validate(doc)
    assert needle in str(err.value)


def test_an_unknown_symbol_is_refused(client):
    login(client, "tom", OPERATOR_PW)
    doc = _preset("fx-monitoring")
    doc["panels"][1]["symbol"] = "NOTREAL"
    r = client.put("/api/workspace/layouts/B", json={"base_version": 0, "layout": doc}, headers=csrf_headers(client))
    assert r.status_code == 422 and r.json()["code"] == "unknown_symbol"


def test_column_sets_are_versioned(client):
    login(client, "tom", OPERATOR_PW)
    h = csrf_headers(client)
    empty = client.get("/api/workspace/column-sets/watchlist").json()["data"]
    assert empty["version"] == 0 and empty["sets"] == []
    body = {"base_version": 0, "sets": [{"name": "Compact", "order": ["symbol", "mid"], "hidden": ["spread"], "pinned": ["symbol"]}], "default": "Compact"}
    saved = client.put("/api/workspace/column-sets/watchlist", json=body, headers=h)
    assert saved.status_code == 200 and saved.json()["data"]["version"] == 1
    assert client.put("/api/workspace/column-sets/watchlist", json=body, headers=h).status_code == 409
    bad = {"base_version": 1, "sets": [], "default": "Nope"}
    assert client.put("/api/workspace/column-sets/watchlist", json=bad, headers=h).status_code == 422
