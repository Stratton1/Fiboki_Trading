"""POST/GET /api/trading/sessions/{id}/control and the fleet's control column."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from tests.api.conftest import ADMIN_PW, OPERATOR_PW, ORIGIN, csrf_headers, login
from tests.api.test_operator_core import _forward_session


def _app(tmp_path, api_env) -> TestClient:
    root = tmp_path / "paper"
    _forward_session(root, name="paper_forward_v1", written_at=datetime.now(tz=UTC))
    env = {**api_env, "FIBOKI_PAPER_ROOT": str(root)}
    return TestClient(create_app(load_settings(env), configure_logs=False), base_url=ORIGIN)


def test_admin_pauses_and_resumes_a_bot_and_both_are_audited(tmp_path, api_env):
    with _app(tmp_path, api_env) as c:
        assert login(c, "joe", ADMIN_PW).status_code == 200
        sid = "forward-paper_forward_v1"
        before = c.get(f"/api/trading/sessions/{sid}/control").json()["data"]
        assert before["state"] == "active" and before["allowed_actions"] == ["pause", "drain"]
        r = c.post(
            f"/api/trading/sessions/{sid}/control",
            json={"action": "pause", "reason": "reviewing a stop placement"},
            headers=csrf_headers(c),
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["state"] == "paused" and data["blocks_entries"] and data["actor"] == "joe"
        assert data["history"][0]["reason"] == "reviewing a stop placement"
        fleet = c.get("/api/trading/fleet").json()["items"]
        row = next(x for x in fleet if x["session_id"] == sid)
        assert row["control_state"] == "paused" and "joe" in row["control_note"]
        # A second pause is refused with the reason and audited as refused.
        again = c.post(
            f"/api/trading/sessions/{sid}/control",
            json={"action": "pause", "reason": "pressing pause again"},
            headers=csrf_headers(c),
        )
        assert again.status_code == 409 and again.json()["code"] == "control_transition_refused"
        assert c.post(
            f"/api/trading/sessions/{sid}/control",
            json={"action": "resume", "reason": "review complete, resume"},
            headers=csrf_headers(c),
        ).json()["data"]["state"] == "active"
        audit = c.app.state.audit.entries()
        acts = [(e.action, e.outcome) for e in audit if e.action.startswith("bot.")]
        assert acts == [("bot.pause", "allowed"), ("bot.pause", "refused"), ("bot.resume", "allowed")]
        assert c.app.state.audit.verify_chain() == (True, 0)


def test_an_operator_cannot_control_a_bot_and_a_reason_is_required(tmp_path, api_env):
    with _app(tmp_path, api_env) as c:
        assert login(c, "tom", OPERATOR_PW).status_code == 200
        r = c.post(
            "/api/trading/sessions/forward-paper_forward_v1/control",
            json={"action": "pause", "reason": "operator tries to pause"},
            headers=csrf_headers(c),
        )
        assert r.status_code == 403
    with _app(tmp_path / "b", api_env) as c:
        assert login(c, "joe", ADMIN_PW).status_code == 200
        r = c.post(
            "/api/trading/sessions/forward-paper_forward_v1/control",
            json={"action": "pause", "reason": "no"},
            headers=csrf_headers(c),
        )
        assert r.status_code == 422


def test_a_replay_or_unknown_session_cannot_be_controlled(tmp_path, api_env):
    with _app(tmp_path, api_env) as c:
        assert login(c, "joe", ADMIN_PW).status_code == 200
        r = c.post(
            "/api/trading/sessions/replay-2025/control",
            json={"action": "pause", "reason": "pausing a replay"},
            headers=csrf_headers(c),
        )
        assert r.status_code == 404
        assert c.get("/api/trading/sessions/replay-2025/control").status_code == 404
        listed = c.get("/api/trading/controls").json()
        assert [i["session_id"] for i in listed["items"]] == ["forward-paper_forward_v1"]
        assert listed["source"]["kind"] == "absent"
