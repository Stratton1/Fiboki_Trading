"""Tests for the IG epic-resolution reject classifier and worker stale auto-heal.

Both are offline/pure where possible. The IG reject classifier is keyed on the
real reason observed on demo account Z5ZAV: "Failed to retrieve price
information for this currency".
"""

from datetime import datetime, timedelta, timezone

import pytest

from fibokei.core.models import Timeframe
from fibokei.db.database import get_engine, get_session_factory, init_db
from fibokei.execution.ig_adapter import _is_epic_resolution_reject
from fibokei.paper.bot import BotState
from fibokei.worker import PaperWorker


def test_reject_classifier_matches_real_reason():
    # The exact IG demo reject reason from the activity history.
    assert _is_epic_resolution_reject(
        "Failed to retrieve price information for this currency"
    )
    assert _is_epic_resolution_reject("")        # empty → retry
    assert _is_epic_resolution_reject("UNKNOWN") # IG's unhelpful default → retry
    assert _is_epic_resolution_reject("no access to the relevant exchange")
    # Genuine order problems should NOT trigger an epic remap.
    assert not _is_epic_resolution_reject("INSUFFICIENT_FUNDS")
    assert not _is_epic_resolution_reject("ATTACHED_ORDER_LEVEL_ERROR")


class _StubBot:
    def __init__(self, bot_id, state, last_eval, tf=Timeframe.H1):
        self.bot_id = bot_id
        self.state = state
        self._last_evaluated_bar = last_eval
        self.timeframe = tf
        self.instrument = "EURUSD"
        self.bars_seen = 200


@pytest.fixture()
def worker(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/heal.db")
    init_db(engine)
    return PaperWorker(get_session_factory(engine), dry_run=True)


def test_auto_heal_rewarms_stale_monitoring_bot(worker):
    now = datetime.now(timezone.utc)
    stale = _StubBot("stale1", BotState.MONITORING, now - timedelta(hours=48))
    fresh = _StubBot("fresh1", BotState.MONITORING, now - timedelta(hours=1))
    worker.bots = {"stale1": stale, "fresh1": fresh}

    worker._auto_heal_stale_bots()

    assert stale._last_evaluated_bar is None   # re-warm forced
    assert stale.bars_seen == 0
    assert fresh._last_evaluated_bar is not None  # untouched


def test_auto_heal_skips_open_positions(worker):
    now = datetime.now(timezone.utc)
    holding = _StubBot("pos1", BotState.POSITION_OPEN, now - timedelta(hours=48))
    worker.bots = {"pos1": holding}

    worker._auto_heal_stale_bots()

    # Never disturb a bot holding a live position.
    assert holding._last_evaluated_bar is not None
