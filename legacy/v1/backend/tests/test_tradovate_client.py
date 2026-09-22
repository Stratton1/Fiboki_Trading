"""Tests for the Tradovate REST client (Phase 1 scaffold).

Real HTTP is never invoked. Authentication, env-gating, and credential-
handling rules are tested in isolation.
"""

from __future__ import annotations

import pytest

from fibokei.execution.tradovate_client import (
    TRADOVATE_DEMO_BASE,
    TRADOVATE_LIVE_BASE,
    TradovateClient,
    TradovateClientError,
    TradovateSession,
)

_ENV_VARS = [
    "FIBOKEI_TRADOVATE_USERNAME",
    "FIBOKEI_TRADOVATE_PASSWORD",
    "FIBOKEI_TRADOVATE_CID",
    "FIBOKEI_TRADOVATE_SECRET",
    "FIBOKEI_TRADOVATE_APP_ID",
    "FIBOKEI_TRADOVATE_APP_VERSION",
    "FIBOKEI_TRADOVATE_DEVICE_ID",
    "FIBOKEI_TRADOVATE_ACCOUNT_ID",
    "FIBOKEI_TRADOVATE_ENV",
    "FIBOKEI_TRADOVATE_BASE_URL",
    "FIBOKEI_TRADOVATE_LIVE_ALLOWED",
    "FIBOKEI_LIVE_EXECUTION_ENABLED",
]


@pytest.fixture
def clean_env(monkeypatch):
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


class TestEnvDefaults:
    def test_defaults_to_demo_url(self, clean_env):
        c = TradovateClient()
        assert c.env == "demo"
        assert c.base_url == TRADOVATE_DEMO_BASE

    def test_explicit_live_env_uses_live_url(self, clean_env):
        clean_env.setenv("FIBOKEI_TRADOVATE_ENV", "live")
        c = TradovateClient()
        assert c.env == "live"
        assert c.base_url == TRADOVATE_LIVE_BASE

    def test_base_url_override(self, clean_env):
        clean_env.setenv("FIBOKEI_TRADOVATE_BASE_URL", "https://stub.example/v1")
        c = TradovateClient()
        assert c.base_url == "https://stub.example/v1"


class TestSafetyGates:
    def test_live_url_blocked_without_explicit_flag(self, clean_env):
        clean_env.setenv("FIBOKEI_TRADOVATE_ENV", "live")
        clean_env.setenv("FIBOKEI_TRADOVATE_USERNAME", "u")
        clean_env.setenv("FIBOKEI_TRADOVATE_PASSWORD", "p")
        clean_env.setenv("FIBOKEI_TRADOVATE_CID", "1")
        clean_env.setenv("FIBOKEI_TRADOVATE_SECRET", "s")
        c = TradovateClient()
        with pytest.raises(TradovateClientError) as exc:
            c.authenticate()
        assert exc.value.error_code == "LIVE_BLOCKED"

    def test_live_url_blocked_without_global_flag(self, clean_env):
        clean_env.setenv("FIBOKEI_TRADOVATE_ENV", "live")
        clean_env.setenv("FIBOKEI_TRADOVATE_LIVE_ALLOWED", "true")
        # FIBOKEI_LIVE_EXECUTION_ENABLED unset
        clean_env.setenv("FIBOKEI_TRADOVATE_USERNAME", "u")
        clean_env.setenv("FIBOKEI_TRADOVATE_PASSWORD", "p")
        clean_env.setenv("FIBOKEI_TRADOVATE_CID", "1")
        clean_env.setenv("FIBOKEI_TRADOVATE_SECRET", "s")
        c = TradovateClient()
        with pytest.raises(TradovateClientError) as exc:
            c.authenticate()
        assert exc.value.error_code == "LIVE_BLOCKED"


class TestCredentials:
    def test_missing_credentials_raises(self, clean_env):
        c = TradovateClient()
        with pytest.raises(TradovateClientError) as exc:
            c.authenticate()
        assert exc.value.error_code == "MISSING_CREDENTIALS"

    def test_has_credentials_property(self, clean_env):
        c = TradovateClient()
        assert c.has_credentials is False
        clean_env.setenv("FIBOKEI_TRADOVATE_USERNAME", "u")
        clean_env.setenv("FIBOKEI_TRADOVATE_PASSWORD", "p")
        clean_env.setenv("FIBOKEI_TRADOVATE_CID", "1")
        clean_env.setenv("FIBOKEI_TRADOVATE_SECRET", "s")
        c2 = TradovateClient()
        assert c2.has_credentials is True


class TestSession:
    def test_empty_session_invalid(self):
        s = TradovateSession()
        assert not s.is_valid

    def test_fresh_session_valid(self):
        import time

        s = TradovateSession(access_token="t", created_at=time.time())
        assert s.is_valid

    def test_expired_session_invalid(self):
        import time

        s = TradovateSession(access_token="t", created_at=time.time() - 10 * 3600)
        assert not s.is_valid

    def test_headers(self):
        s = TradovateSession(access_token="abc")
        assert s.headers == {"Authorization": "Bearer abc"}
