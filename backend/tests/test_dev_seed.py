"""Tests for the DEV-ONLY seed endpoint."""

import os

import pytest


@pytest.fixture
def dev_client():
    """Create test client with FIBOKEI_DEV_SEED enabled."""
    os.environ["FIBOKEI_DEV_SEED"] = "1"
    os.environ["FIBOKEI_JWT_SECRET"] = "test-secret-for-testing"
    os.environ["FIBOKEI_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["FIBOKEI_USER_JOE_PASSWORD"] = "testpass123"
    os.environ["FIBOKEI_LOCAL_DEV"] = "1"

    from fibokei.api.app import create_app
    from starlette.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        yield client

    os.environ.pop("FIBOKEI_DEV_SEED", None)


@pytest.fixture
def dev_auth(dev_client):
    """Get auth headers for dev client."""
    res = dev_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestDevSeed:
    def test_seed_creates_backtest(self, dev_client, dev_auth):
        res = dev_client.post("/api/v1/dev/seed/backtest", headers=dev_auth)
        assert res.status_code == 200
        data = res.json()
        assert data["seeded"] is True
        assert data["backtest_run_id"] > 0

    def test_seed_is_idempotent(self, dev_client, dev_auth):
        res1 = dev_client.post("/api/v1/dev/seed/backtest", headers=dev_auth)
        assert res1.status_code == 200
        id1 = res1.json()["backtest_run_id"]

        res2 = dev_client.post("/api/v1/dev/seed/backtest", headers=dev_auth)
        assert res2.status_code == 200
        assert res2.json()["seeded"] is False
        assert res2.json()["backtest_run_id"] == id1

    def test_seed_backtest_has_trades(self, dev_client, dev_auth):
        res = dev_client.post("/api/v1/dev/seed/backtest", headers=dev_auth)
        bt_id = res.json()["backtest_run_id"]

        trades_res = dev_client.get(
            f"/api/v1/backtests/{bt_id}/trades", headers=dev_auth
        )
        assert trades_res.status_code == 200
        data = trades_res.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) == 5

    def test_seed_not_available_without_flag(self):
        """Without FIBOKEI_DEV_SEED, the route must not be registered."""
        saved = os.environ.pop("FIBOKEI_DEV_SEED", None)
        os.environ["FIBOKEI_JWT_SECRET"] = "test-secret-for-testing"
        os.environ["FIBOKEI_DATABASE_URL"] = "sqlite:///:memory:"
        os.environ["FIBOKEI_LOCAL_DEV"] = "1"

        from fibokei.api.app import create_app
        from starlette.testclient import TestClient

        app = create_app()
        with TestClient(app) as client:
            res = client.post("/api/v1/dev/seed/backtest")
            assert res.status_code in (404, 405, 422)

        if saved:
            os.environ["FIBOKEI_DEV_SEED"] = saved
