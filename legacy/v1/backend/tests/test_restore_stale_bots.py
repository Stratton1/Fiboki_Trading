"""Tests for POST /paper/bots/restore-stale.

Calls the route function directly with a temp DB session (no auth/TestClient
needed). Verifies: errored bots are restored (state→monitoring, error cleared);
monitoring-but-stale bots are reported under needs_attention; healthy bots are
left untouched.
"""

from datetime import datetime, timedelta, timezone

import pytest

from fibokei.api.routes.paper import restore_stale_bots
from fibokei.db.database import get_engine, get_session_factory, init_db
from fibokei.db.repository import get_paper_bot, save_paper_bot


@pytest.fixture()
def session(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/bots.db")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        yield s


def _bot(session, bot_id, *, state="monitoring", last_eval=None, error=None):
    save_paper_bot(session, {
        "bot_id": bot_id, "strategy_id": "bot04_chikou_momentum",
        "instrument": "EURUSD", "timeframe": "H1", "risk_pct": 1.0,
        "source_type": "manual", "state": state,
    })
    b = get_paper_bot(session, bot_id)
    if last_eval is not None:
        b.last_evaluated_bar = last_eval
    if error is not None:
        b.error_message = error
    session.commit()
    return b


def test_restore_classifies_and_recovers(session):
    now = datetime.now(timezone.utc)
    _bot(session, "errbot", state="monitoring", error="Recovery failed: boom")
    _bot(session, "stalebot", state="monitoring", last_eval=now - timedelta(hours=48))
    _bot(session, "healthybot", state="monitoring", last_eval=now - timedelta(hours=1))

    resp = restore_stale_bots(user=None, db=session)

    # Errored bot is restored + cleared.
    assert resp.count == 1
    assert resp.restored[0]["bot_id"] == "errbot"
    restored_bot = get_paper_bot(session, "errbot")
    assert restored_bot.state == "monitoring"
    assert restored_bot.error_message == ""

    # Stale-but-monitoring bot is flagged, not auto-restarted.
    attention_ids = {x["bot_id"] for x in resp.needs_attention}
    assert "stalebot" in attention_ids
    assert "healthybot" not in attention_ids
    assert "errbot" not in attention_ids


def test_no_stale_bots_is_noop(session):
    now = datetime.now(timezone.utc)
    _bot(session, "ok", state="monitoring", last_eval=now - timedelta(minutes=30))
    resp = restore_stale_bots(user=None, db=session)
    assert resp.count == 0
    assert resp.needs_attention == []
