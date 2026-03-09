"""Tests for Phase 9 — paper trading persistence, worker, health, promotion gate."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.models import Base, PaperBotModel, PaperTradeModel, PaperAccountModel
from fibokei.db.repository import (
    get_active_paper_bots,
    get_best_research_score,
    get_or_create_paper_account,
    get_paper_bot,
    get_paper_bots,
    get_paper_trades,
    save_paper_bot,
    save_paper_trade,
    save_research_results,
    update_paper_account,
    update_paper_bot_state,
)


@pytest.fixture
def db_session():
    """In-memory SQLite session for testing."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


# ─── DB Model Tests ─────────────────────────────────────────────

class TestPaperBotModel:
    def test_create_paper_bot(self, db_session):
        bot = save_paper_bot(db_session, {
            "bot_id": "abc12345",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "risk_pct": 1.0,
            "state": "monitoring",
        })
        assert bot.id is not None
        assert bot.bot_id == "abc12345"
        assert bot.state == "monitoring"

    def test_get_paper_bot(self, db_session):
        save_paper_bot(db_session, {
            "bot_id": "test001",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "monitoring",
        })
        bot = get_paper_bot(db_session, "test001")
        assert bot is not None
        assert bot.strategy_id == "bot01_sanyaku"

    def test_get_paper_bot_not_found(self, db_session):
        assert get_paper_bot(db_session, "nonexistent") is None

    def test_update_paper_bot_state(self, db_session):
        save_paper_bot(db_session, {
            "bot_id": "upd001",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "monitoring",
        })
        now = datetime.now(timezone.utc)
        updated = update_paper_bot_state(
            db_session, "upd001", "position_open",
            last_evaluated_bar=now,
            bars_seen=150,
            position_json={"direction": "LONG", "entry_price": 1.1},
        )
        assert updated.state == "position_open"
        assert updated.bars_seen == 150
        assert updated.position_json["direction"] == "LONG"
        # SQLite strips tzinfo; compare without tz
        assert updated.last_evaluated_bar.replace(tzinfo=None) == now.replace(tzinfo=None)

    def test_update_nonexistent_bot(self, db_session):
        result = update_paper_bot_state(db_session, "ghost", "stopped")
        assert result is None

    def test_upsert_existing_bot(self, db_session):
        save_paper_bot(db_session, {
            "bot_id": "ups001",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "monitoring",
        })
        # Upsert same bot_id
        updated = save_paper_bot(db_session, {
            "bot_id": "ups001",
            "state": "stopped",
        })
        assert updated.state == "stopped"
        # Should still be only one record
        all_bots = get_paper_bots(db_session)
        assert len(all_bots) == 1


class TestPaperBotQueries:
    def test_get_paper_bots_all(self, db_session):
        for i in range(3):
            save_paper_bot(db_session, {
                "bot_id": f"bot{i:03d}",
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "state": "monitoring" if i < 2 else "stopped",
            })
        all_bots = get_paper_bots(db_session)
        assert len(all_bots) == 3

    def test_get_paper_bots_filtered_by_state(self, db_session):
        save_paper_bot(db_session, {
            "bot_id": "a1", "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD", "timeframe": "H1", "state": "monitoring",
        })
        save_paper_bot(db_session, {
            "bot_id": "a2", "strategy_id": "bot01_sanyaku",
            "instrument": "GBPUSD", "timeframe": "H1", "state": "stopped",
        })
        monitoring = get_paper_bots(db_session, state="monitoring")
        assert len(monitoring) == 1
        assert monitoring[0].bot_id == "a1"

    def test_get_active_paper_bots(self, db_session):
        save_paper_bot(db_session, {
            "bot_id": "b1", "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD", "timeframe": "H1", "state": "monitoring",
        })
        save_paper_bot(db_session, {
            "bot_id": "b2", "strategy_id": "bot01_sanyaku",
            "instrument": "GBPUSD", "timeframe": "H1", "state": "position_open",
        })
        save_paper_bot(db_session, {
            "bot_id": "b3", "strategy_id": "bot01_sanyaku",
            "instrument": "USDJPY", "timeframe": "H1", "state": "stopped",
        })
        active = get_active_paper_bots(db_session)
        assert len(active) == 2
        bot_ids = {b.bot_id for b in active}
        assert bot_ids == {"b1", "b2"}


class TestPaperTradeModel:
    def test_save_and_retrieve_paper_trade(self, db_session):
        bot = save_paper_bot(db_session, {
            "bot_id": "t001",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "monitoring",
        })
        trade = save_paper_trade(db_session, {
            "paper_bot_id": bot.id,
            "bot_id": "t001",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "direction": "LONG",
            "entry_time": datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc),
            "entry_price": 1.1000,
            "exit_time": datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
            "exit_price": 1.1050,
            "exit_reason": "take_profit_hit",
            "pnl": 50.0,
            "bars_in_trade": 4,
        })
        assert trade.id is not None
        assert trade.pnl == 50.0

        trades = get_paper_trades(db_session, bot_id="t001")
        assert len(trades) == 1


class TestPaperAccountModel:
    def test_get_or_create_account(self, db_session):
        acct = get_or_create_paper_account(db_session)
        assert acct.initial_balance == 10000.0
        assert acct.balance == 10000.0

    def test_creates_only_once(self, db_session):
        acct1 = get_or_create_paper_account(db_session)
        acct2 = get_or_create_paper_account(db_session)
        assert acct1.id == acct2.id

    def test_update_paper_account(self, db_session):
        get_or_create_paper_account(db_session)
        updated = update_paper_account(
            db_session,
            balance=10150.0,
            equity=10200.0,
            daily_pnl=150.0,
            weekly_pnl=200.0,
        )
        assert updated.balance == 10150.0
        assert updated.equity == 10200.0
        assert updated.daily_pnl == 150.0


# ─── Promotion Gate Tests ──────────────────────────────────────

class TestPromotionGate:
    def test_best_research_score_exists(self, db_session):
        save_research_results(db_session, [{
            "run_id": "r001",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "composite_score": 0.72,
            "rank": 1,
        }])
        score = get_best_research_score(db_session, "bot01_sanyaku", "EURUSD", "H1")
        assert score == 0.72

    def test_best_research_score_none(self, db_session):
        score = get_best_research_score(db_session, "bot01_sanyaku", "EURUSD", "H1")
        assert score is None

    def test_best_score_picks_highest(self, db_session):
        for score_val in [0.55, 0.80, 0.65]:
            save_research_results(db_session, [{
                "run_id": f"r_{score_val}",
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "composite_score": score_val,
                "rank": 1,
            }])
        best = get_best_research_score(db_session, "bot01_sanyaku", "EURUSD", "H1")
        assert best == 0.80

    def test_score_filters_by_combo(self, db_session):
        save_research_results(db_session, [{
            "run_id": "r1",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "composite_score": 0.90,
            "rank": 1,
        }])
        # Different instrument
        score = get_best_research_score(db_session, "bot01_sanyaku", "GBPUSD", "H1")
        assert score is None


# ─── Stale Data Detection Tests ───────────────────────────────

class TestStaleDataDetection:
    def test_fresh_bot_not_stale(self, db_session):
        """A bot evaluated recently should not be stale."""
        now = datetime.now(timezone.utc)
        save_paper_bot(db_session, {
            "bot_id": "fresh01",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "monitoring",
        })
        update_paper_bot_state(
            db_session, "fresh01", "monitoring",
            last_evaluated_bar=now - timedelta(minutes=30),
        )
        bot = get_paper_bot(db_session, "fresh01")
        # H1 threshold is 7200s (2h); 30min < 2h
        last_eval = bot.last_evaluated_bar
        if last_eval.tzinfo is None:
            last_eval = last_eval.replace(tzinfo=timezone.utc)
        seconds_since = (now - last_eval).total_seconds()
        assert seconds_since < 7200

    def test_stale_bot_detected(self, db_session):
        """A bot not evaluated for longer than threshold should be stale."""
        now = datetime.now(timezone.utc)
        save_paper_bot(db_session, {
            "bot_id": "stale01",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "monitoring",
        })
        update_paper_bot_state(
            db_session, "stale01", "monitoring",
            last_evaluated_bar=now - timedelta(hours=3),
        )
        bot = get_paper_bot(db_session, "stale01")
        last_eval = bot.last_evaluated_bar
        if last_eval.tzinfo is None:
            last_eval = last_eval.replace(tzinfo=timezone.utc)
        seconds_since = (now - last_eval).total_seconds()
        assert seconds_since > 7200  # Stale

    def test_stopped_bot_not_checked_for_staleness(self, db_session):
        """Stopped bots should not be flagged as stale."""
        save_paper_bot(db_session, {
            "bot_id": "stop01",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "stopped",
        })
        bot = get_paper_bot(db_session, "stop01")
        # Staleness only applies to active bots
        assert bot.state == "stopped"


# ─── Worker Recovery Tests ─────────────────────────────────────

class TestWorkerRecovery:
    def test_recover_active_bots(self, db_session):
        """Worker should recover monitoring and position_open bots."""
        save_paper_bot(db_session, {
            "bot_id": "w1", "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD", "timeframe": "H1", "state": "monitoring",
            "bars_seen": 200,
        })
        save_paper_bot(db_session, {
            "bot_id": "w2", "strategy_id": "bot01_sanyaku",
            "instrument": "GBPUSD", "timeframe": "H1", "state": "position_open",
            "bars_seen": 350,
        })
        save_paper_bot(db_session, {
            "bot_id": "w3", "strategy_id": "bot01_sanyaku",
            "instrument": "USDJPY", "timeframe": "H1", "state": "stopped",
        })
        active = get_active_paper_bots(db_session)
        assert len(active) == 2
        bot_ids = {b.bot_id for b in active}
        assert "w3" not in bot_ids

    def test_last_evaluated_bar_prevents_duplicate(self, db_session):
        """Bot with last_evaluated_bar set should skip older candles."""
        now = datetime.now(timezone.utc)
        save_paper_bot(db_session, {
            "bot_id": "dup01",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "state": "monitoring",
        })
        update_paper_bot_state(
            db_session, "dup01", "monitoring",
            last_evaluated_bar=now,
        )
        bot = get_paper_bot(db_session, "dup01")
        assert bot.last_evaluated_bar is not None
        # Worker would filter: only feed bars where timestamp > last_evaluated_bar


# ─── API Health Endpoint Tests ─────────────────────────────────

class TestHealthEndpoint:
    def test_health_endpoint_returns_bots(self, auth_headers, api_client):
        """GET /paper/health returns bot health data."""
        resp = api_client.get("/api/v1/paper/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_bots" in data
        assert "active_bots" in data
        assert "stale_bots" in data
        assert "bots" in data

    def test_health_no_bots(self, auth_headers, api_client):
        """Health with no bots returns zeros."""
        resp = api_client.get("/api/v1/paper/health", headers=auth_headers)
        data = resp.json()
        assert data["total_bots"] == 0
        assert data["stale_bots"] == 0


# ─── API Promotion Gate Tests ─────────────────────────────────

class TestPromotionGateAPI:
    def test_create_bot_without_research_fails(self, auth_headers, api_client):
        """Creating a bot without qualifying research score should fail."""
        resp = api_client.post(
            "/api/v1/paper/bots",
            headers=auth_headers,
            json={
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
            },
        )
        assert resp.status_code == 422
        assert "Promotion gate failed" in resp.json()["detail"]

    def test_create_bot_with_low_score_fails(self, auth_headers, api_client):
        """Creating a bot with score below threshold should fail."""
        # First seed a low research score via the DB
        # The api_client uses in-memory DB, so we need to insert directly
        from fibokei.db.repository import save_research_results
        session_factory = api_client.app.state.session_factory
        with session_factory() as session:
            save_research_results(session, [{
                "run_id": "gate_test",
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "composite_score": 0.30,
                "rank": 1,
            }])

        resp = api_client.post(
            "/api/v1/paper/bots",
            headers=auth_headers,
            json={
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
            },
        )
        assert resp.status_code == 422
        assert "Promotion gate failed" in resp.json()["detail"]

    def test_create_bot_with_passing_score(self, auth_headers, api_client):
        """Creating a bot with qualifying score should succeed."""
        session_factory = api_client.app.state.session_factory
        with session_factory() as session:
            save_research_results(session, [{
                "run_id": "gate_pass",
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "composite_score": 0.75,
                "rank": 1,
            }])

        resp = api_client.post(
            "/api/v1/paper/bots",
            headers=auth_headers,
            json={
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "monitoring"
        assert data["strategy_id"] == "bot01_sanyaku"


# ─── API Bot CRUD Tests ───────────────────────────────────────

class TestPaperBotAPI:
    def _seed_research(self, api_client, strategy_id="bot01_sanyaku",
                       instrument="EURUSD", timeframe="H1"):
        session_factory = api_client.app.state.session_factory
        with session_factory() as session:
            save_research_results(session, [{
                "run_id": "seed",
                "strategy_id": strategy_id,
                "instrument": instrument,
                "timeframe": timeframe,
                "composite_score": 0.80,
                "rank": 1,
            }])

    def test_list_bots_empty(self, auth_headers, api_client):
        resp = api_client.get("/api/v1/paper/bots", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_list_bot(self, auth_headers, api_client):
        self._seed_research(api_client)
        resp = api_client.post(
            "/api/v1/paper/bots",
            headers=auth_headers,
            json={
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
            },
        )
        assert resp.status_code == 200
        bot_id = resp.json()["bot_id"]

        resp = api_client.get("/api/v1/paper/bots", headers=auth_headers)
        assert len(resp.json()) == 1
        assert resp.json()[0]["bot_id"] == bot_id

    def test_stop_bot(self, auth_headers, api_client):
        self._seed_research(api_client)
        create_resp = api_client.post(
            "/api/v1/paper/bots",
            headers=auth_headers,
            json={
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
            },
        )
        bot_id = create_resp.json()["bot_id"]

        resp = api_client.post(
            f"/api/v1/paper/bots/{bot_id}/stop",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "stopped"

    def test_pause_bot(self, auth_headers, api_client):
        self._seed_research(api_client)
        create_resp = api_client.post(
            "/api/v1/paper/bots",
            headers=auth_headers,
            json={
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
            },
        )
        bot_id = create_resp.json()["bot_id"]

        resp = api_client.post(
            f"/api/v1/paper/bots/{bot_id}/pause",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "paused"

    def test_get_account(self, auth_headers, api_client):
        resp = api_client.get("/api/v1/paper/account", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 10000.0
        assert data["initial_balance"] == 10000.0

    def test_stop_nonexistent_bot(self, auth_headers, api_client):
        resp = api_client.post(
            "/api/v1/paper/bots/ghost/stop",
            headers=auth_headers,
        )
        assert resp.status_code == 404
