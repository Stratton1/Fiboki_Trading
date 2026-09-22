"""Tests for Phase 2 execution_accounts + bot_execution_targets.

Covers DB seeding, repository CRUD, and the API endpoints. Verifies the
backwards-compatibility rule — bots created without targets still work and,
under ``db_targets`` mode, fall through to the seeded Paper account.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.repository import (
    create_bot_execution_target,
    create_execution_account,
    get_default_execution_account,
    list_bot_execution_targets,
    list_execution_accounts,
    list_targets_with_accounts,
    update_bot_execution_target,
    update_execution_account,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


# ── Seed and account CRUD ──────────────────────────────────────


class TestSeedAndAccounts:
    def test_default_paper_account_seeded(self, db_session):
        """init_db must seed exactly one default Paper execution account."""
        accounts = list_execution_accounts(db_session)
        assert len(accounts) == 1
        paper = accounts[0]
        assert paper.broker == "paper"
        assert paper.environment == "paper"
        assert paper.is_default is True
        assert paper.is_enabled is True
        assert paper.live_allowed is False

    def test_seed_is_idempotent(self, db_session):
        """Running init_db twice must not create a duplicate Paper row."""
        # init_db has already run via the fixture. Run it again on the same engine.
        engine = db_session.get_bind()
        init_db(engine)
        accounts = list_execution_accounts(db_session)
        assert len(accounts) == 1

    def test_get_default_returns_paper(self, db_session):
        default = get_default_execution_account(db_session)
        assert default is not None
        assert default.name == "Paper"

    def test_create_ig_demo_account(self, db_session):
        ig = create_execution_account(
            db_session,
            {
                "name": "IG Demo Main",
                "broker": "ig",
                "environment": "demo",
                "allocated_capital": 1500.0,
                "risk_per_trade_pct": 0.5,
            },
        )
        assert ig.id != get_default_execution_account(db_session).id
        assert ig.broker == "ig"
        assert ig.environment == "demo"
        # Default-true on is_enabled
        assert ig.is_enabled is True
        # Live remains hard-blocked at the column level
        assert ig.live_allowed is False

    def test_update_account_disable(self, db_session):
        accounts = list_execution_accounts(db_session)
        paper = accounts[0]
        updated = update_execution_account(db_session, paper.id, {"is_enabled": False})
        assert updated.is_enabled is False
        # Filtered list excludes disabled
        enabled = list_execution_accounts(db_session, enabled_only=True)
        assert paper.id not in [a.id for a in enabled]


# ── Targets CRUD ───────────────────────────────────────────────


class TestTargetsCRUD:
    def test_create_target_for_bot(self, db_session):
        paper = get_default_execution_account(db_session)
        target = create_bot_execution_target(
            db_session,
            {"bot_id": "bot01", "execution_account_id": paper.id},
        )
        assert target.bot_id == "bot01"
        assert target.execution_account_id == paper.id
        assert target.is_enabled is True

    def test_unique_constraint_on_bot_account(self, db_session):
        paper = get_default_execution_account(db_session)
        create_bot_execution_target(
            db_session, {"bot_id": "bot02", "execution_account_id": paper.id}
        )
        with pytest.raises(Exception):
            create_bot_execution_target(
                db_session, {"bot_id": "bot02", "execution_account_id": paper.id}
            )

    def test_list_targets_filters_by_bot(self, db_session):
        paper = get_default_execution_account(db_session)
        ig = create_execution_account(
            db_session,
            {"name": "IG Demo", "broker": "ig", "environment": "demo"},
        )
        create_bot_execution_target(
            db_session, {"bot_id": "botA", "execution_account_id": paper.id}
        )
        create_bot_execution_target(
            db_session, {"bot_id": "botA", "execution_account_id": ig.id}
        )
        create_bot_execution_target(
            db_session, {"bot_id": "botB", "execution_account_id": paper.id}
        )
        a_targets = list_bot_execution_targets(db_session, bot_id="botA")
        assert len(a_targets) == 2
        b_targets = list_bot_execution_targets(db_session, bot_id="botB")
        assert len(b_targets) == 1

    def test_target_overrides(self, db_session):
        paper = get_default_execution_account(db_session)
        target = create_bot_execution_target(
            db_session,
            {
                "bot_id": "bot03",
                "execution_account_id": paper.id,
                "allocation_override": 2500.0,
                "risk_per_trade_pct_override": 0.5,
            },
        )
        assert target.allocation_override == 2500.0
        assert target.risk_per_trade_pct_override == 0.5

    def test_disable_target(self, db_session):
        paper = get_default_execution_account(db_session)
        target = create_bot_execution_target(
            db_session,
            {"bot_id": "bot04", "execution_account_id": paper.id},
        )
        updated = update_bot_execution_target(
            db_session, target.id, {"is_enabled": False}
        )
        assert updated.is_enabled is False

    def test_list_targets_with_accounts_excludes_disabled(self, db_session):
        paper = get_default_execution_account(db_session)
        ig = create_execution_account(
            db_session,
            {"name": "IG Demo", "broker": "ig", "environment": "demo"},
        )
        create_bot_execution_target(
            db_session, {"bot_id": "botX", "execution_account_id": paper.id}
        )
        ig_target = create_bot_execution_target(
            db_session, {"bot_id": "botX", "execution_account_id": ig.id}
        )

        # Both enabled
        pairs = list_targets_with_accounts(db_session, bot_id="botX")
        assert len(pairs) == 2

        # Disable the IG target
        update_bot_execution_target(db_session, ig_target.id, {"is_enabled": False})
        pairs = list_targets_with_accounts(db_session, bot_id="botX")
        assert len(pairs) == 1
        assert pairs[0][1].broker == "paper"

        # Re-enable IG target but disable the IG account itself
        update_bot_execution_target(db_session, ig_target.id, {"is_enabled": True})
        update_execution_account(db_session, ig.id, {"is_enabled": False})
        pairs = list_targets_with_accounts(db_session, bot_id="botX")
        assert len(pairs) == 1
        assert pairs[0][1].broker == "paper"


# ── API endpoints ──────────────────────────────────────────────


class TestExecutionAccountAPI:
    def test_list_accounts_returns_paper(self, api_client, auth_headers):
        resp = api_client.get("/api/v1/execution/accounts", headers=auth_headers)
        assert resp.status_code == 200
        accounts = resp.json()
        assert len(accounts) == 1
        assert accounts[0]["broker"] == "paper"
        assert accounts[0]["is_default"] is True

    def test_create_account_then_get(self, api_client, auth_headers):
        resp = api_client.post(
            "/api/v1/execution/accounts",
            headers=auth_headers,
            json={
                "name": "IG Demo Main",
                "broker": "ig",
                "environment": "demo",
                "allocated_capital": 1500.0,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        account_id = body["id"]
        assert body["broker"] == "ig"
        assert body["environment"] == "demo"
        # GET it back
        resp2 = api_client.get(
            f"/api/v1/execution/accounts/{account_id}", headers=auth_headers
        )
        assert resp2.status_code == 200
        assert resp2.json()["allocated_capital"] == 1500.0

    def test_duplicate_name_rejected(self, api_client, auth_headers):
        resp = api_client.post(
            "/api/v1/execution/accounts",
            headers=auth_headers,
            json={"name": "Paper", "broker": "paper", "environment": "paper"},
        )
        assert resp.status_code == 409

    def test_unknown_broker_rejected(self, api_client, auth_headers):
        resp = api_client.post(
            "/api/v1/execution/accounts",
            headers=auth_headers,
            json={"name": "Bogus", "broker": "junk", "environment": "demo"},
        )
        assert resp.status_code == 400

    def test_patch_account(self, api_client, auth_headers):
        resp = api_client.post(
            "/api/v1/execution/accounts",
            headers=auth_headers,
            json={"name": "TV Demo", "broker": "tradovate", "environment": "demo"},
        )
        account_id = resp.json()["id"]
        resp2 = api_client.patch(
            f"/api/v1/execution/accounts/{account_id}",
            headers=auth_headers,
            json={"allocated_capital": 7500.0, "risk_per_trade_pct": 0.5},
        )
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["allocated_capital"] == 7500.0
        assert body["risk_per_trade_pct"] == 0.5

    def test_account_status_endpoint(self, api_client, auth_headers):
        # Paper account always reports configured=True
        resp = api_client.get("/api/v1/execution/accounts", headers=auth_headers)
        paper_id = resp.json()[0]["id"]
        resp2 = api_client.get(
            f"/api/v1/execution/accounts/{paper_id}/status", headers=auth_headers
        )
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["broker"] == "paper"
        assert body["configured"] is True


class TestBotTargetsAPI:
    def _create_bot(self, api_client, auth_headers):
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
        return resp.json()["bot_id"]

    def test_bot_with_no_targets_lists_empty(self, api_client, auth_headers):
        bot_id = self._create_bot(api_client, auth_headers)
        resp = api_client.get(
            f"/api/v1/paper/bots/{bot_id}/targets", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_attach_paper_target_to_bot(self, api_client, auth_headers):
        bot_id = self._create_bot(api_client, auth_headers)
        accounts = api_client.get(
            "/api/v1/execution/accounts", headers=auth_headers
        ).json()
        paper_id = accounts[0]["id"]
        resp = api_client.post(
            f"/api/v1/paper/bots/{bot_id}/targets",
            headers=auth_headers,
            json={"execution_account_id": paper_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bot_id"] == bot_id
        assert body["execution_account_id"] == paper_id
        assert body["account"]["broker"] == "paper"

    def test_duplicate_target_rejected(self, api_client, auth_headers):
        bot_id = self._create_bot(api_client, auth_headers)
        accounts = api_client.get(
            "/api/v1/execution/accounts", headers=auth_headers
        ).json()
        paper_id = accounts[0]["id"]
        api_client.post(
            f"/api/v1/paper/bots/{bot_id}/targets",
            headers=auth_headers,
            json={"execution_account_id": paper_id},
        )
        resp = api_client.post(
            f"/api/v1/paper/bots/{bot_id}/targets",
            headers=auth_headers,
            json={"execution_account_id": paper_id},
        )
        assert resp.status_code == 409

    def test_patch_target(self, api_client, auth_headers):
        bot_id = self._create_bot(api_client, auth_headers)
        accounts = api_client.get(
            "/api/v1/execution/accounts", headers=auth_headers
        ).json()
        paper_id = accounts[0]["id"]
        created = api_client.post(
            f"/api/v1/paper/bots/{bot_id}/targets",
            headers=auth_headers,
            json={"execution_account_id": paper_id},
        ).json()
        resp = api_client.patch(
            f"/api/v1/paper/bots/{bot_id}/targets/{created['id']}",
            headers=auth_headers,
            json={"allocation_override": 2500.0, "is_enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["allocation_override"] == 2500.0
        assert resp.json()["is_enabled"] is False

    def test_delete_target(self, api_client, auth_headers):
        bot_id = self._create_bot(api_client, auth_headers)
        accounts = api_client.get(
            "/api/v1/execution/accounts", headers=auth_headers
        ).json()
        paper_id = accounts[0]["id"]
        created = api_client.post(
            f"/api/v1/paper/bots/{bot_id}/targets",
            headers=auth_headers,
            json={"execution_account_id": paper_id},
        ).json()
        resp = api_client.delete(
            f"/api/v1/paper/bots/{bot_id}/targets/{created['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # Subsequent list returns empty
        listed = api_client.get(
            f"/api/v1/paper/bots/{bot_id}/targets", headers=auth_headers
        ).json()
        assert listed == []

    def test_create_bot_with_inline_targets(self, api_client, auth_headers):
        """POST /paper/bots with execution_targets attaches them in one request."""
        accounts = api_client.get(
            "/api/v1/execution/accounts", headers=auth_headers
        ).json()
        paper_id = accounts[0]["id"]
        # Add an IG demo account to attach as a second target
        ig = api_client.post(
            "/api/v1/execution/accounts",
            headers=auth_headers,
            json={"name": "IG Demo", "broker": "ig", "environment": "demo"},
        ).json()

        resp = api_client.post(
            "/api/v1/paper/bots",
            headers=auth_headers,
            json={
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "execution_targets": [
                    {"execution_account_id": paper_id},
                    {"execution_account_id": ig["id"], "allocation_override": 500.0},
                ],
            },
        )
        assert resp.status_code == 200
        bot = resp.json()
        assert len(bot["execution_targets"]) == 2
        # Verify the targets really landed
        listed = api_client.get(
            f"/api/v1/paper/bots/{bot['bot_id']}/targets", headers=auth_headers
        ).json()
        assert len(listed) == 2
        ig_target = next(t for t in listed if t["account"]["broker"] == "ig")
        assert ig_target["allocation_override"] == 500.0
