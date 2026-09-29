"""OpenAPI is published with its auth semantics; the disarm preflight has moved."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.api.conftest import csrf_headers

REPO = Path(__file__).resolve().parents[2]


def test_openapi_is_reachable_and_states_auth_per_operation(client):
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["sessionCookie"] == {**schemes["sessionCookie"], "type": "apiKey", "in": "cookie"}
    assert schemes["csrfHeader"]["name"] == "X-Fiboki-CSRF"
    paths = schema["paths"]
    stream = paths["/api/stream"]["get"]
    assert stream["x-fiboki-auth"] == "session"
    assert "text/event-stream" in stream["responses"]["200"]["content"]
    ack = paths["/api/system/incidents/{incident_id}/ack"]["post"]
    assert ack["x-fiboki-auth"] == "admin"
    assert ack["security"] == [{"sessionCookie": [], "csrfHeader": []}]
    assert paths["/api/auth/me"]["get"]["x-fiboki-auth"] == "session"
    assert paths["/api/auth/login"]["post"]["x-fiboki-auth"] == "origin"
    assert paths["/api/auth/logout"]["post"]["x-fiboki-auth"] == "csrf"
    assert paths["/api/health"]["get"]["x-fiboki-auth"] == "public"
    for path in (
        "/api/command/attention",
        "/api/system/incidents",
        "/api/markets/overlays/{symbol}",
        "/api/system/kill-switch/disarm/preflight",
    ):
        assert path in paths


def test_every_mutating_operation_is_documented_as_needing_csrf(client):
    schema = client.get("/api/openapi.json").json()
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            if method in {"post", "put", "patch", "delete"} and path not in {
                "/api/auth/login",
                "/api/auth/logout",
            }:
                assert op["x-fiboki-auth"] in {"admin", "operator"}, (method, path)


def test_disarm_preflight_lives_beside_the_kill_switch_and_the_old_path_still_works(admin_client):
    admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "preflight move check"},
        headers=csrf_headers(admin_client),
    ).raise_for_status()
    new = admin_client.get("/api/system/kill-switch/disarm/preflight").json()
    old = admin_client.get("/api/trading/preflight/kill-switch-disarm").json()
    assert new["data"] == old["data"]
    assert new["data"]["active"] is True
    assert any("PAUSE" in line for line in new["data"]["consequences"]["disarm"])


def test_gen_openapi_script_writes_the_schema(tmp_path):
    out = tmp_path / "openapi.json"
    env = {**os.environ, "PYTHON": str(REPO / ".venv" / "bin" / "python")}
    proc = subprocess.run(
        ["bash", str(REPO / "scripts" / "gen-openapi.sh"), str(out)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    schema = json.loads(out.read_text())
    assert "/api/stream" in schema["paths"]
    assert "securitySchemes" in schema["components"]
