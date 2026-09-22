"""Tests for Phase 5 per-account reconciliation status."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.repository import create_execution_account
from fibokei.execution.reconciliation import (
    RECON_STATUS_CLEAN,
    RECON_STATUS_CREDENTIALS_MISSING,
    RECON_STATUS_MISMATCH,
    RECON_STATUS_UNAVAILABLE,
    reconcile_account,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


class _FakeAdapter:
    """Adapter spy returning canned positions or raising."""

    def __init__(self, positions=None, raise_on_get=None):
        self._positions = positions or []
        self._raise_on_get = raise_on_get

    def place_order(self, order):
        return {}

    def cancel_order(self, order_id):
        return True

    def modify_order(self, order_id, changes):
        return {}

    def get_positions(self):
        if self._raise_on_get is not None:
            raise self._raise_on_get
        return list(self._positions)

    def get_account_info(self):
        return {}

    def close_position(self, position_id):
        return {}

    def partial_close(self, position_id, pct):
        return {}


class TestReconcileAccount:
    def test_paper_account_always_clean(self, db_session):
        with db_session as s:
            paper = create_execution_account(
                s, {"name": "Paper2", "broker": "paper", "environment": "paper"}
            )
            paper_id = paper.id
            paper_obj = paper
        status = reconcile_account(
            paper_obj,
            [{"deal_id": "p1", "instrument": "EURUSD", "direction": "BUY", "size": 1}],
        )
        assert status.status == RECON_STATUS_CLEAN
        assert status.account_id == paper_id

    def test_clean_when_positions_match(self, db_session):
        with db_session as s:
            ig = create_execution_account(
                s, {"name": "IG", "broker": "ig", "environment": "demo"}
            )
            ig_obj = ig
        positions = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
        ]
        adapter = _FakeAdapter(positions=positions)
        status = reconcile_account(ig_obj, positions, adapter=adapter)
        assert status.status == RECON_STATUS_CLEAN
        assert status.matched == 1

    def test_mismatch_when_broker_extra_position(self, db_session):
        with db_session as s:
            ig = create_execution_account(
                s, {"name": "IG", "broker": "ig", "environment": "demo"}
            )
            ig_obj = ig
        adapter = _FakeAdapter(positions=[
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
        ])
        # Fiboki has no positions tracked → broker side is "extra"
        status = reconcile_account(ig_obj, [], adapter=adapter)
        assert status.status == RECON_STATUS_MISMATCH
        assert status.mismatch_count == 1
        assert status.mismatches[0].type == "missing_in_fiboki"

    def test_credentials_missing_surfaces_typed_status(self, db_session):
        with db_session as s:
            ig = create_execution_account(
                s, {"name": "IG", "broker": "ig", "environment": "demo"}
            )
            ig_obj = ig

        class _Err(Exception):
            pass

        adapter = _FakeAdapter(
            raise_on_get=_Err("Tradovate credentials not configured. MISSING_CREDENTIALS")
        )
        status = reconcile_account(ig_obj, [], adapter=adapter)
        assert status.status == RECON_STATUS_CREDENTIALS_MISSING

    def test_unavailable_on_other_error(self, db_session):
        with db_session as s:
            ig = create_execution_account(
                s, {"name": "IG", "broker": "ig", "environment": "demo"}
            )
            ig_obj = ig
        adapter = _FakeAdapter(raise_on_get=ConnectionError("network down"))
        status = reconcile_account(ig_obj, [], adapter=adapter)
        assert status.status == RECON_STATUS_UNAVAILABLE
        assert "network down" in status.detail


class TestReconcileAccountAPI:
    def test_paper_account_endpoint(self, api_client, auth_headers):
        accounts = api_client.get(
            "/api/v1/execution/accounts", headers=auth_headers
        ).json()
        paper_id = accounts[0]["id"]
        resp = api_client.get(
            f"/api/v1/execution/accounts/{paper_id}/reconcile", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == RECON_STATUS_CLEAN
        assert body["broker"] == "paper"

    def test_unknown_account_returns_404(self, api_client, auth_headers):
        resp = api_client.get(
            "/api/v1/execution/accounts/9999999/reconcile", headers=auth_headers
        )
        assert resp.status_code == 404
