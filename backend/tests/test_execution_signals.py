"""Tests for Phase 3 first-class parent-child audit (bot_signals + execution_attempts).

Covers:
  - Repository helpers create signal/attempt rows with the right shape.
  - One signal can carry many attempts; cascade delete removes children.
  - ``derive_parent_signal_status`` rolls up child statuses correctly.
  - ``/execution/signals`` and ``/execution/signals/{id}/attempts`` endpoints
    return the expected grouped view.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.models import BotSignalModel, ExecutionAttemptModel
from fibokei.db.repository import (
    ATTEMPT_STATUS_CLOSED,
    ATTEMPT_STATUS_FAILED,
    ATTEMPT_STATUS_FILLED,
    ATTEMPT_STATUS_PARTIALLY_FILLED,
    ATTEMPT_STATUS_REJECTED,
    ATTEMPT_STATUS_SKIPPED,
    create_bot_signal,
    create_execution_attempt,
    derive_parent_signal_status,
    list_bot_signals,
    list_execution_attempts,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _signal(db, **overrides) -> BotSignalModel:
    base = {
        "bot_id": "bot01",
        "strategy_id": "bot01_sanyaku",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "direction": "LONG",
    }
    base.update(overrides)
    return create_bot_signal(db, base)


def _attempt(db, signal_id, status, **overrides) -> ExecutionAttemptModel:
    base = {
        "bot_signal_id": signal_id,
        "broker": "paper",
        "environment": "paper",
        "instrument": "EURUSD",
        "status": status,
    }
    base.update(overrides)
    return create_execution_attempt(db, base)


# ── Repository CRUD ────────────────────────────────────────


class TestRepository:
    def test_create_and_list_signals(self, db_session):
        s1 = _signal(db_session, instrument="EURUSD")
        s2 = _signal(db_session, instrument="GBPUSD")
        signals = list_bot_signals(db_session)
        assert {s.id for s in signals} == {s1.id, s2.id}

    def test_filter_signals_by_bot_and_instrument(self, db_session):
        _signal(db_session, bot_id="botA", instrument="EURUSD")
        _signal(db_session, bot_id="botB", instrument="EURUSD")
        _signal(db_session, bot_id="botA", instrument="GBPUSD")
        a_only = list_bot_signals(db_session, bot_id="botA")
        assert len(a_only) == 2
        eur_only = list_bot_signals(db_session, instrument="EURUSD")
        assert len(eur_only) == 2

    def test_attempts_list_filters(self, db_session):
        s = _signal(db_session)
        _attempt(db_session, s.id, ATTEMPT_STATUS_FILLED, broker="ig")
        _attempt(db_session, s.id, ATTEMPT_STATUS_REJECTED, broker="tradovate")
        ig_only = list_execution_attempts(db_session, broker="ig")
        assert len(ig_only) == 1
        rej_only = list_execution_attempts(db_session, status=ATTEMPT_STATUS_REJECTED)
        assert len(rej_only) == 1

    def test_cascade_delete(self, db_session):
        s = _signal(db_session)
        _attempt(db_session, s.id, ATTEMPT_STATUS_FILLED)
        _attempt(db_session, s.id, ATTEMPT_STATUS_REJECTED)
        db_session.delete(s)
        db_session.commit()
        # All attempts should be gone via cascade
        remaining = list_execution_attempts(db_session)
        assert remaining == []


# ── Parent-status derivation ──────────────────────────────


class TestDeriveParentStatus:
    def _atts(self, *statuses):
        return [
            ExecutionAttemptModel(
                bot_signal_id=1,
                broker="paper",
                environment="paper",
                instrument="EURUSD",
                status=s,
            )
            for s in statuses
        ]

    def test_empty(self):
        assert derive_parent_signal_status([]) == "empty"

    def test_all_filled(self):
        atts = self._atts(ATTEMPT_STATUS_FILLED, ATTEMPT_STATUS_FILLED)
        assert derive_parent_signal_status(atts) == "all_filled"

    def test_all_filled_includes_closed_and_partial(self):
        atts = self._atts(
            ATTEMPT_STATUS_FILLED,
            ATTEMPT_STATUS_CLOSED,
            ATTEMPT_STATUS_PARTIALLY_FILLED,
        )
        assert derive_parent_signal_status(atts) == "all_filled"

    def test_all_skipped(self):
        atts = self._atts(ATTEMPT_STATUS_SKIPPED, ATTEMPT_STATUS_SKIPPED)
        assert derive_parent_signal_status(atts) == "all_skipped"

    def test_all_rejected(self):
        atts = self._atts(ATTEMPT_STATUS_REJECTED)
        assert derive_parent_signal_status(atts) == "all_rejected"

    def test_failed_only(self):
        atts = self._atts(ATTEMPT_STATUS_FAILED, ATTEMPT_STATUS_FAILED)
        assert derive_parent_signal_status(atts) == "failed"

    def test_partial_success(self):
        atts = self._atts(ATTEMPT_STATUS_FILLED, ATTEMPT_STATUS_REJECTED)
        assert derive_parent_signal_status(atts) == "partial_success"

    def test_partial_success_with_skip(self):
        atts = self._atts(ATTEMPT_STATUS_FILLED, ATTEMPT_STATUS_SKIPPED)
        assert derive_parent_signal_status(atts) == "partial_success"

    def test_mixed_skip_and_reject(self):
        atts = self._atts(ATTEMPT_STATUS_SKIPPED, ATTEMPT_STATUS_REJECTED)
        assert derive_parent_signal_status(atts) == "mixed"


# ── API endpoints ──────────────────────────────────────────


class TestSignalsAPI:
    def test_list_signals_empty(self, api_client, auth_headers):
        resp = api_client.get("/api/v1/execution/signals", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_grouped_view_with_attempts(self, api_client, auth_headers):
        # Build a signal with two attempts via the same engine/session the app
        # uses, then read via the API endpoint.
        session_factory = api_client.app.state.session_factory
        with session_factory() as db:
            sig = create_bot_signal(db, {
                "bot_id": "bot-x",
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "direction": "LONG",
            })
            create_execution_attempt(db, {
                "bot_signal_id": sig.id,
                "broker": "ig",
                "environment": "demo",
                "instrument": "EURUSD",
                "status": ATTEMPT_STATUS_FILLED,
                "broker_deal_id": "IG-1",
            })
            create_execution_attempt(db, {
                "bot_signal_id": sig.id,
                "broker": "tradovate",
                "environment": "demo",
                "instrument": "EURUSD",
                "status": ATTEMPT_STATUS_SKIPPED,
                "rejection_reason": "Unsupported instrument",
                "error_code": "UNSUPPORTED_INSTRUMENT_TRADOVATE",
            })

        resp = api_client.get("/api/v1/execution/signals", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        s = body[0]
        assert s["bot_id"] == "bot-x"
        assert s["parent_status"] == "partial_success"
        assert s["attempt_count"] == 2
        brokers = sorted(a["broker"] for a in s["attempts"])
        assert brokers == ["ig", "tradovate"]

    def test_signal_attempts_filter(self, api_client, auth_headers):
        session_factory = api_client.app.state.session_factory
        with session_factory() as db:
            sig = create_bot_signal(db, {
                "bot_id": "bot-y",
                "strategy_id": "bot01_sanyaku",
                "instrument": "US500",
                "timeframe": "H1",
                "direction": "LONG",
            })
            create_execution_attempt(db, {
                "bot_signal_id": sig.id,
                "broker": "ig",
                "environment": "demo",
                "instrument": "US500",
                "status": ATTEMPT_STATUS_FILLED,
            })
            create_execution_attempt(db, {
                "bot_signal_id": sig.id,
                "broker": "tradovate",
                "environment": "demo",
                "instrument": "US500",
                "status": ATTEMPT_STATUS_REJECTED,
            })
            sid = sig.id

        resp = api_client.get(
            f"/api/v1/execution/signals/{sid}/attempts?broker=ig",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["broker"] == "ig"

    def test_unknown_signal_returns_404(self, api_client, auth_headers):
        resp = api_client.get(
            "/api/v1/execution/signals/999999", headers=auth_headers
        )
        assert resp.status_code == 404
