"""Sign-in works with a scrypt directory entry and still with a legacy sha256 one.

The conftest directory is all legacy sha256 entries, which is itself the
backwards-compatibility test: every other API test signs in through it.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.routers.auth import hash_password
from fiboki.api.settings import load_settings
from tests.api.conftest import ADMIN_PW, ORIGIN, login


@pytest.fixture
def scrypt_client(api_env, monkeypatch):
    monkeypatch.setenv(
        "FIBOKI_OPERATORS",
        f"joe:admin:{hash_password('scrypt-admin-pw')},tom:operator:{hash_password('pw-2')}",
    )
    app = create_app(load_settings(api_env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def test_a_scrypt_entry_signs_in(scrypt_client) -> None:
    ok = login(scrypt_client, "joe", "scrypt-admin-pw")
    assert ok.status_code == 200, ok.text
    assert ok.json()["role"] == "admin"


def test_a_wrong_password_against_scrypt_is_refused(scrypt_client) -> None:
    assert login(scrypt_client, "joe", "scrypt-admin-pW").status_code == 401
    assert login(scrypt_client, "nobody", "scrypt-admin-pw").status_code == 401


def test_a_legacy_entry_still_signs_in_and_logs_a_rotation_warning(client, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="fiboki.api.auth"):
        assert login(client, "joe", ADMIN_PW).status_code == 200
    assert any("LEGACY unsalted sha256" in r.getMessage() for r in caplog.records)
