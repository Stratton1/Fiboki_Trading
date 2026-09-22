"""Security contract tests.

The enumeration test at the bottom is the important one: it walks every route
the app actually registers and asserts that each mutating one is behind the
admin dependency. V1 had a ``require_admin`` that was applied to two routes out
of the dozen that needed it, and nothing noticed.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from fiboki.api.security import CSRF_COOKIE, CSRF_HEADER, require_admin
from tests.api.conftest import ADMIN_PW, ORIGIN, csrf_headers, login

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

#: Routes that mutate but are deliberately not admin-gated, each with a reason.
NON_ADMIN_MUTATING = {
    "/api/auth/login": "issues a session; gating it on a session is circular",
    "/api/auth/logout": "must always be reachable, whatever your role",
}


def _routes(app):
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _all_dependencies(dependant):
    for sub in dependant.dependencies:
        yield sub
        yield from _all_dependencies(sub)


def _mutating_routes(app):
    return [
        r
        for r in _routes(app)
        if (set(r.methods) & MUTATING) and r.path not in NON_ADMIN_MUTATING
    ]


def test_there_are_mutating_routes_to_check(client):
    """Guards the guard: an empty enumeration would pass vacuously."""
    assert len(_mutating_routes(client.app)) >= 5


def test_every_mutating_route_requires_admin(client):
    missing = [
        f"{sorted(route.methods)} {route.path}"
        for route in _mutating_routes(client.app)
        if not any(
            sub.call is require_admin for sub in _all_dependencies(route.dependant)
        )
    ]
    assert not missing, (
        "These mutating routes are not behind require_admin: " + ", ".join(missing)
    )


def test_no_route_accepts_a_credential_in_the_url(client):
    for route in _routes(client.app):
        lowered = route.path.lower()
        for banned in ("token", "password", "secret", "apikey", "api_key", "session"):
            assert banned not in lowered, f"{route.path} puts {banned!r} in a URL"


def test_mutating_request_without_origin_is_refused(admin_client):
    response = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "testing the origin check"},
        headers={CSRF_HEADER: admin_client.cookies.get(CSRF_COOKIE, "")},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_missing"


def test_mutating_request_from_a_foreign_origin_is_refused(admin_client):
    headers = csrf_headers(admin_client)
    headers["Origin"] = "https://evil.example"
    response = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "cross site attempt"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_mutating_request_without_csrf_token_is_refused(admin_client):
    response = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "missing the csrf header"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_referer_is_accepted_when_origin_is_absent(admin_client):
    headers = csrf_headers(admin_client)
    del headers["Origin"]
    headers["Referer"] = f"{ORIGIN}/trading/risk"
    response = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "referer fallback path"},
        headers=headers,
    )
    assert response.status_code == 200


def test_session_cookie_is_httponly_and_samesite(client):
    response = login(client, "joe", ADMIN_PW)
    raw = response.headers.get_list("set-cookie")
    session = next(c for c in raw if c.startswith("fiboki_session="))
    assert "HttpOnly" in session
    assert "samesite=strict" in session.lower()
    csrf = next(c for c in raw if c.startswith(f"{CSRF_COOKIE}="))
    # The CSRF cookie must be readable by the page to be echoed into a header.
    assert "HttpOnly" not in csrf


def test_operator_cannot_arm_the_kill_switch(operator_client):
    response = operator_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "operator should not be allowed"},
        headers=csrf_headers(operator_client),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "insufficient_role"


def test_rbac_refusal_is_written_to_the_audit_trail(operator_client, client):
    operator_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "operator should not be allowed"},
        headers=csrf_headers(operator_client),
    )
    entries = operator_client.get("/api/intelligence/audit").json()["items"]
    assert any(e["outcome"] == "refused" and "rbac.denied" in e["action"] for e in entries)


def test_login_is_rate_limited(client):
    for _ in range(5):
        assert login(client, "joe", "wrong-password").status_code == 401
    locked = login(client, "joe", "wrong-password")
    assert locked.status_code == 429
    assert locked.json()["code"] == "login_locked_out"


def test_unknown_user_and_wrong_password_are_indistinguishable(client):
    a = login(client, "joe", "wrong-password").json()
    b = login(client, "nobody-at-all", "wrong-password").json()
    assert a["code"] == b["code"] == "invalid_credentials"
    assert a["detail"] == b["detail"]


def test_unauthenticated_read_is_refused(client):
    assert client.get("/api/auth/me").status_code == 401


def test_error_body_never_leaks_internals(client):
    body = login(client, "joe", "nope").json()
    text = str(body)
    for leak in ("Traceback", "/home/", "sqlite", "sha256", "hmac"):
        assert leak not in text
    assert body["correlation_id"]
