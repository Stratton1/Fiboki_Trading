"""Fixtures for the HTTP API tests.

Each test gets an isolated state directory, so the kill-switch journal and the
audit trail from one test cannot leak into another.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.security import CSRF_COOKIE, CSRF_HEADER
from fiboki.api.settings import load_settings

ORIGIN = "https://workstation.fiboki.test"
ADMIN_PW = "admin-password-for-tests"
OPERATOR_PW = "operator-password-for-tests"
VIEWER_PW = "viewer-password-for-tests"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    directory = ",".join(
        [
            f"joe:admin:{_sha(ADMIN_PW)}",
            f"tom:operator:{_sha(OPERATOR_PW)}",
            f"guest:viewer:{_sha(VIEWER_PW)}",
        ]
    )
    monkeypatch.setenv("FIBOKI_OPERATORS", directory)
    return {
        "FIBOKI_STATE_DIR": str(tmp_path / "state"),
        "FIBOKI_ALLOWED_ORIGINS": ORIGIN,
        "FIBOKI_COOKIE_SECURE": "false",
        "FIBOKI_SESSION_SECRET": "test-secret-not-for-production",
        "FIBOKI_BUILD_SHA": "deadbeef",
    }


@pytest.fixture
def client(api_env):
    settings = load_settings(api_env)
    app = create_app(settings, configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": ORIGIN},
    )


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, CSRF_HEADER: client.cookies.get(CSRF_COOKIE, "")}


@pytest.fixture
def admin_client(client):
    response = login(client, "joe", ADMIN_PW)
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def operator_client(client):
    response = login(client, "tom", OPERATOR_PW)
    assert response.status_code == 200, response.text
    return client
