"""Tests for IG safety controls — kill switch, audit logs, feature flags."""

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fibokei.core.feature_flags import FeatureFlags
from fibokei.db.database import init_db, get_session_factory
from fibokei.db.models import Base, ExecutionAuditModel, KillSwitchModel
from fibokei.db.repository import (
    activate_kill_switch,
    deactivate_kill_switch,
    get_execution_audit,
    get_kill_switch,
    save_execution_audit,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


# ---------- Kill switch tests ----------


class TestKillSwitch:
    def test_get_creates_default_inactive(self, db_session):
        ks = get_kill_switch(db_session)
        assert ks.is_active is False
        assert ks.reason is None

    def test_activate(self, db_session):
        ks = activate_kill_switch(db_session, reason="Emergency", activated_by="joe")
        assert ks.is_active is True
        assert ks.reason == "Emergency"
        assert ks.activated_by == "joe"
        assert ks.activated_at is not None

    def test_deactivate(self, db_session):
        activate_kill_switch(db_session, reason="Test")
        ks = deactivate_kill_switch(db_session)
        assert ks.is_active is False
        assert ks.deactivated_at is not None

    def test_single_row(self, db_session):
        """Kill switch is always a single-row table."""
        ks1 = get_kill_switch(db_session)
        ks2 = get_kill_switch(db_session)
        assert ks1.id == ks2.id

    def test_activate_deactivate_cycle(self, db_session):
        activate_kill_switch(db_session, reason="First")
        deactivate_kill_switch(db_session)
        activate_kill_switch(db_session, reason="Second")
        ks = get_kill_switch(db_session)
        assert ks.is_active is True
        assert ks.reason == "Second"


# ---------- Execution audit tests ----------


class TestExecutionAudit:
    def test_save_and_retrieve(self, db_session):
        save_execution_audit(db_session, {
            "execution_mode": "paper",
            "action": "place_order",
            "instrument": "EURUSD",
            "direction": "BUY",
            "size": 1.0,
            "status": "success",
            "bot_id": "bot01",
        })
        entries = get_execution_audit(db_session)
        assert len(entries) == 1
        assert entries[0].instrument == "EURUSD"
        assert entries[0].action == "place_order"

    def test_filter_by_mode(self, db_session):
        save_execution_audit(db_session, {
            "execution_mode": "paper",
            "action": "place_order",
            "instrument": "EURUSD",
            "status": "success",
        })
        save_execution_audit(db_session, {
            "execution_mode": "ig_demo",
            "action": "place_order",
            "instrument": "GBPUSD",
            "status": "success",
        })
        paper = get_execution_audit(db_session, execution_mode="paper")
        assert len(paper) == 1
        assert paper[0].instrument == "EURUSD"

        ig = get_execution_audit(db_session, execution_mode="ig_demo")
        assert len(ig) == 1
        assert ig[0].instrument == "GBPUSD"

    def test_filter_by_bot_id(self, db_session):
        save_execution_audit(db_session, {
            "execution_mode": "paper",
            "action": "close_position",
            "instrument": "USDJPY",
            "status": "success",
            "bot_id": "bot_a",
        })
        save_execution_audit(db_session, {
            "execution_mode": "paper",
            "action": "place_order",
            "instrument": "AUDUSD",
            "status": "failed",
            "bot_id": "bot_b",
        })
        entries = get_execution_audit(db_session, bot_id="bot_a")
        assert len(entries) == 1
        assert entries[0].instrument == "USDJPY"

    def test_audit_with_error(self, db_session):
        save_execution_audit(db_session, {
            "execution_mode": "ig_demo",
            "action": "place_order",
            "instrument": "EURUSD",
            "status": "failed",
            "error_message": "Market closed",
        })
        entries = get_execution_audit(db_session)
        assert entries[0].error_message == "Market closed"

    def test_audit_limit(self, db_session):
        for i in range(5):
            save_execution_audit(db_session, {
                "execution_mode": "paper",
                "action": f"action_{i}",
                "instrument": "EURUSD",
                "status": "success",
            })
        entries = get_execution_audit(db_session, limit=3)
        assert len(entries) == 3


# ---------- Feature flags tests ----------


class TestFeatureFlags:
    def test_default_paper_mode(self):
        flags = FeatureFlags()
        assert flags.execution_mode == "paper"

    def test_ig_demo_mode(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("FIBOKEI_LIVE_EXECUTION_ENABLED", "true")
            mp.setenv("FIBOKEI_IG_PAPER_MODE", "true")
            flags = FeatureFlags()
            assert flags.execution_mode == "ig_demo"
            assert flags.live_execution_enabled is True
            assert flags.ig_paper_mode is True

    def test_execution_mode_property(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("FIBOKEI_LIVE_EXECUTION_ENABLED", "false")
            flags = FeatureFlags()
            assert flags.execution_mode == "paper"


# ---------- API endpoint tests ----------


class TestExecutionAPI:
    def test_get_execution_mode(self, api_client, auth_headers):
        resp = api_client.get("/api/v1/execution/mode", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "paper"
        assert data["kill_switch_active"] is False

    def test_kill_switch_activate_deactivate(self, api_client, auth_headers):
        # Activate
        resp = api_client.post(
            "/api/v1/execution/kill-switch/activate",
            json={"reason": "Test emergency"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True
        assert resp.json()["reason"] == "Test emergency"

        # Check status
        resp = api_client.get("/api/v1/execution/kill-switch", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

        # Deactivate
        resp = api_client.post(
            "/api/v1/execution/kill-switch/deactivate",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_audit_log_empty(self, api_client, auth_headers):
        resp = api_client.get("/api/v1/execution/audit", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_system_status_includes_execution_mode(self, api_client, auth_headers):
        resp = api_client.get("/api/v1/system/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "execution_mode" in data
        assert data["execution_mode"] == "paper"
        assert "kill_switch_active" in data
        assert data["kill_switch_active"] is False
