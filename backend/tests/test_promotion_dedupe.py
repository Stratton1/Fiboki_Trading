"""Promotion dedupe + promotion-status tests.

Calls the paper route functions directly with a temp DB (no auth/TestClient).
Verifies a second active bot for the same strategy+instrument+timeframe is
refused unless allow_duplicate=true, and that promotion-status reports copies.
"""

import pytest
from fastapi import HTTPException

from fibokei.api.routes.paper import (
    CreateBotRequest,
    create_bot,
    promotion_status,
)
from fibokei.db.database import get_engine, get_session_factory, init_db

STRAT = "bot01_sanyaku"  # a registered canonical strategy


@pytest.fixture()
def session(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/promo.db")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        yield s


def _req(**kw):
    base = dict(strategy_id=STRAT, instrument="EURUSD", timeframe="H1", risk_pct=1.0)
    base.update(kw)
    return CreateBotRequest(**base)


def test_duplicate_promotion_blocked(session):
    first = create_bot(_req(), user=None, db=session)
    assert first.bot_id

    with pytest.raises(HTTPException) as exc:
        create_bot(_req(), user=None, db=session)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "already_promoted"
    assert first.bot_id in exc.value.detail["existing_bot_ids"]


def test_allow_duplicate_clones(session):
    create_bot(_req(), user=None, db=session)
    second = create_bot(_req(allow_duplicate=True), user=None, db=session)
    assert second.bot_id

    status = promotion_status(
        strategy_id=STRAT, instrument="EURUSD", timeframe="h1",
        user=None, db=session,
    )
    assert status.already_promoted is True
    assert status.count == 2


def test_promotion_status_false_when_none(session):
    status = promotion_status(
        strategy_id=STRAT, instrument="GBPUSD", timeframe="H1",
        user=None, db=session,
    )
    assert status.already_promoted is False
    assert status.count == 0
    assert status.bot_ids == []
