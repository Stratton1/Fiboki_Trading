"""Tests for EvaluationPhase model, repository functions, and API endpoints.

Covers:
  - archive_current_phase: assigns existing bots/trades, sets is_active=False
  - create_new_phase: creates active phase, rejects duplicate active
  - transition_to_new_phase: archives + creates atomically
  - Phase API endpoints: list, get, active, transition, export
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.models import (
    Base,
    EvaluationPhaseModel,
    PaperBotModel,
    PaperTradeModel,
)
from fibokei.db.repository import (
    archive_current_phase,
    create_new_phase,
    get_active_phase,
    get_phase,
    list_phases,
    transition_to_new_phase,
)


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _make_bot(session: Session, bot_id: str) -> PaperBotModel:
    bot = PaperBotModel(
        bot_id=bot_id,
        strategy_id="bot01_sanyaku",
        instrument="EURUSD",
        timeframe="H1",
        state="stopped",
    )
    session.add(bot)
    session.flush()
    return bot


def _make_trade(session: Session, bot: PaperBotModel, pnl: float) -> PaperTradeModel:
    trade = PaperTradeModel(
        paper_bot_id=bot.id,
        bot_id=bot.bot_id,
        strategy_id=bot.strategy_id,
        instrument=bot.instrument,
        direction="LONG",
        entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        entry_price=1.1000,
        exit_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
        exit_price=1.1050 if pnl > 0 else 1.0950,
        exit_reason="take_profit_hit" if pnl > 0 else "stop_loss_hit",
        pnl=pnl,
        bars_in_trade=10,
    )
    session.add(trade)
    session.flush()
    return trade


# ── Repository Tests ─────────────────────────────────────────


class TestArchiveCurrentPhase:
    def test_creates_phase_if_none_exists(self, db_session):
        bot = _make_bot(db_session, "bot001")
        _make_trade(db_session, bot, 50.0)
        db_session.commit()

        phase = archive_current_phase(
            db_session,
            phase_name="Phase A",
            phase_label="phase_a",
            initial_balance=1000.0,
        )

        assert phase.is_active is False
        assert phase.phase_label == "phase_a"
        assert phase.total_trades == 1
        assert abs(phase.net_pnl - 50.0) < 0.01
        assert phase.archived_at is not None

    def test_assigns_unassigned_bots(self, db_session):
        bot1 = _make_bot(db_session, "bot001")
        bot2 = _make_bot(db_session, "bot002")
        db_session.commit()

        phase = archive_current_phase(db_session, phase_name="Phase A", phase_label="phase_a")

        db_session.refresh(bot1)
        db_session.refresh(bot2)
        # Bots get their phase_id tagged but are NOT marked archived — they
        # are cross-phase entities that continue running into the next phase.
        # See archive_current_phase() in repository.py: only trades carry an
        # archived_at; bots' archived_at stays None. The earlier assertion
        # was a stale leftover from the original phase model where bots were
        # archived alongside their trades.
        assert bot1.phase_id == phase.id
        assert bot2.phase_id == phase.id
        assert bot1.archived_at is None
        assert bot2.archived_at is None

    def test_assigns_unassigned_trades(self, db_session):
        bot = _make_bot(db_session, "bot001")
        t1 = _make_trade(db_session, bot, 25.0)
        t2 = _make_trade(db_session, bot, -10.0)
        db_session.commit()

        phase = archive_current_phase(db_session, phase_name="Phase A", phase_label="phase_a")

        db_session.refresh(t1)
        db_session.refresh(t2)
        assert t1.phase_id == phase.id
        assert t2.phase_id == phase.id

    def test_computes_final_balance(self, db_session):
        bot = _make_bot(db_session, "bot001")
        _make_trade(db_session, bot, 30.0)
        _make_trade(db_session, bot, -15.0)
        db_session.commit()

        phase = archive_current_phase(
            db_session,
            phase_name="Phase A",
            phase_label="phase_a",
            initial_balance=1000.0,
        )

        assert abs(phase.net_pnl - 15.0) < 0.01
        assert abs(phase.final_balance - 1015.0) < 0.01

    def test_does_not_reassign_already_assigned_bots(self, db_session):
        # Create phase A with bot1
        bot1 = _make_bot(db_session, "bot001")
        t1 = _make_trade(db_session, bot1, 20.0)
        db_session.commit()
        phase_a = archive_current_phase(db_session, phase_name="Phase A", phase_label="phase_a")

        # Create bot2 after the transition (not yet assigned)
        bot2 = _make_bot(db_session, "bot002")
        _make_trade(db_session, bot2, 5.0)
        db_session.commit()

        # Archive again
        phase_a2 = archive_current_phase(db_session, phase_name="Phase A again", phase_label="phase_a_v2")

        db_session.refresh(t1)
        db_session.refresh(bot1)
        # bot1/t1 should still have phase_a.id (not re-assigned)
        assert bot1.phase_id == phase_a.id
        assert t1.phase_id == phase_a.id


class TestCreateNewPhase:
    def test_creates_active_phase(self, db_session):
        phase = create_new_phase(
            db_session,
            name="Phase B",
            phase_label="phase_b",
            initial_balance=1000.0,
        )
        assert phase.is_active is True
        assert phase.phase_label == "phase_b"
        assert phase.initial_balance == 1000.0

    def test_rejects_duplicate_active_phase(self, db_session):
        create_new_phase(db_session, name="Phase B", phase_label="phase_b")
        with pytest.raises(ValueError, match="Active phase"):
            create_new_phase(db_session, name="Phase C", phase_label="phase_c")

    def test_normalized_baseline_stored(self, db_session):
        phase = create_new_phase(
            db_session,
            name="Phase B",
            phase_label="phase_b",
            normalized_baseline=1000.0,
            broker_balance_at_start=20000.0,
        )
        assert phase.normalized_baseline == 1000.0
        assert phase.broker_balance_at_start == 20000.0


class TestTransitionToNewPhase:
    def test_archives_and_creates(self, db_session):
        bot = _make_bot(db_session, "bot001")
        _make_trade(db_session, bot, 40.0)
        db_session.commit()

        archived, new = transition_to_new_phase(
            db_session,
            new_phase_name="Phase B",
            new_phase_label="phase_b",
            archive_name="Phase A",
            archive_label="phase_a",
            archive_initial_balance=1000.0,
        )

        assert archived.is_active is False
        assert new.is_active is True
        assert new.phase_label == "phase_b"

    def test_phases_are_separate(self, db_session):
        bot1 = _make_bot(db_session, "bot001")
        _make_trade(db_session, bot1, 10.0)
        db_session.commit()

        archived, new = transition_to_new_phase(
            db_session,
            new_phase_name="Phase B",
            new_phase_label="phase_b",
        )

        # After transition, a new bot should belong to phase B
        bot2 = _make_bot(db_session, "bot002")
        bot2.phase_id = new.id
        db_session.commit()

        db_session.refresh(bot1)
        db_session.refresh(bot2)
        assert bot1.phase_id == archived.id
        assert bot2.phase_id == new.id


class TestPhaseQueries:
    def test_get_active_phase_returns_none_when_empty(self, db_session):
        assert get_active_phase(db_session) is None

    def test_list_phases_ordered_newest_first(self, db_session):
        archived, new = transition_to_new_phase(
            db_session,
            new_phase_name="Phase B",
            new_phase_label="phase_b",
            archive_name="Phase A",
            archive_label="phase_a",
        )
        phases = list_phases(db_session)
        # Newest (Phase B) should be first
        assert phases[0].is_active is True
        assert phases[-1].is_active is False

    def test_get_phase_by_id(self, db_session):
        phase = create_new_phase(db_session, name="Phase B", phase_label="phase_b")
        fetched = get_phase(db_session, phase.id)
        assert fetched is not None
        assert fetched.name == "Phase B"

    def test_get_phase_returns_none_for_missing_id(self, db_session):
        assert get_phase(db_session, 99999) is None


# ── API Tests ─────────────────────────────────────────────────


def test_list_phases_endpoint(api_client, auth_headers):
    """GET /paper/phases returns empty list with no phases."""
    resp = api_client.get("/api/v1/paper/phases", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_active_phase_endpoint_returns_null_when_empty(api_client, auth_headers):
    """GET /paper/phases/active returns null when no phase exists."""
    resp = api_client.get("/api/v1/paper/phases/active", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() is None


def test_phase_transition_endpoint(api_client, auth_headers):
    """POST /paper/phases/transition archives current + creates new phase."""
    payload = {
        "archive_name": "Phase A — Test",
        "archive_label": "phase_a",
        "archive_initial_balance": 1000.0,
        "new_phase_name": "Phase B — Forward",
        "new_phase_label": "phase_b",
        "new_initial_balance": 1000.0,
        "new_normalized_baseline": 1000.0,
        "stop_active_bots": False,
        "reset_account": True,
    }
    resp = api_client.post(
        "/api/v1/paper/phases/transition",
        json=payload,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["archived_phase"]["is_active"] is False
    assert data["new_phase"]["is_active"] is True
    assert data["new_phase"]["phase_label"] == "phase_b"


def test_phase_transition_sequential_succeeds(api_client, auth_headers):
    """Two consecutive transitions both succeed: each archives the current active phase.

    The 409 guard on create_new_phase only fires when called directly without
    going through the archive step first.  The transition endpoint always archives
    whatever is active before creating the new phase, so back-to-back transitions
    are valid and produce one active phase + N archived phases.
    """
    payload_ab = {
        "archive_name": "Phase A",
        "archive_label": "phase_a",
        "new_phase_name": "Phase B",
        "new_phase_label": "phase_b",
        "stop_active_bots": False,
        "reset_account": False,
    }
    payload_bc = {
        "archive_name": "Phase B",
        "archive_label": "phase_b",
        "new_phase_name": "Phase C",
        "new_phase_label": "phase_c",
        "stop_active_bots": False,
        "reset_account": False,
    }

    r1 = api_client.post("/api/v1/paper/phases/transition", json=payload_ab, headers=auth_headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["archived_phase"]["is_active"] is False
    assert r1.json()["new_phase"]["is_active"] is True

    r2 = api_client.post("/api/v1/paper/phases/transition", json=payload_bc, headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["archived_phase"]["is_active"] is False
    assert r2.json()["new_phase"]["phase_label"] == "phase_c"
    assert r2.json()["new_phase"]["is_active"] is True

    # Three phases total: Phase A (archived), Phase B (archived), Phase C (active)
    phases = api_client.get("/api/v1/paper/phases", headers=auth_headers).json()
    assert len(phases) == 3
    active = [p for p in phases if p["is_active"]]
    archived = [p for p in phases if not p["is_active"]]
    assert len(active) == 1
    assert len(archived) == 2


def test_get_specific_phase_endpoint(api_client, auth_headers):
    """GET /paper/phases/{id} returns phase data."""
    payload = {
        "archive_name": "Phase A",
        "archive_label": "phase_a",
        "new_phase_name": "Phase B",
        "new_phase_label": "phase_b",
        "stop_active_bots": False,
        "reset_account": False,
    }
    trans = api_client.post(
        "/api/v1/paper/phases/transition",
        json=payload,
        headers=auth_headers,
    ).json()
    phase_id = trans["new_phase"]["id"]

    resp = api_client.get(f"/api/v1/paper/phases/{phase_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == phase_id
    assert resp.json()["is_active"] is True


def test_export_phase_trades_endpoint(api_client, auth_headers):
    """GET /paper/phases/{id}/export returns an xlsx response."""
    payload = {
        "archive_name": "Phase A",
        "archive_label": "phase_a",
        "new_phase_name": "Phase B",
        "new_phase_label": "phase_b",
        "stop_active_bots": False,
        "reset_account": False,
    }
    trans = api_client.post(
        "/api/v1/paper/phases/transition",
        json=payload,
        headers=auth_headers,
    ).json()
    archived_id = trans["archived_phase"]["id"]

    resp = api_client.get(
        f"/api/v1/paper/phases/{archived_id}/export",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers.get("content-type", "")


def test_export_all_trades_endpoint(api_client, auth_headers):
    """GET /paper/trades/export returns an xlsx response."""
    resp = api_client.get("/api/v1/paper/trades/export", headers=auth_headers)
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers.get("content-type", "")
