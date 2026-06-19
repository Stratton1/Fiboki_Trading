"""Wave 3 — append-only agent/lifecycle/lineage ledger tests.

Proves: round-trip persistence with provenance, event/lane validation, and
the immutability guarantee (the ledger repository exposes no update/delete).
Offline, deterministic — uses a temp SQLite file so multiple sessions share
the same DB.
"""

import pytest

from fibokei.db import ledger_repository as ledger
from fibokei.db.database import get_engine, get_session_factory, init_db


@pytest.fixture()
def session(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/ledger.db")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        yield s


def test_agent_run_roundtrip(session):
    run = ledger.create_agent_run(session, {
        "lane": "builder",
        "agent_type": "fiboki_strategy_author",
        "actor": "agent",
        "prompt_hash": "abc123",
        "code_diff_hash": "def456",
        "summary": "generated bot23 variant",
    })
    assert run.run_id
    rows = ledger.list_agent_runs(session)
    assert len(rows) == 1
    assert rows[0].prompt_hash == "abc123"
    assert rows[0].code_diff_hash == "def456"


def test_lifecycle_event_provenance(session):
    ev = ledger.create_lifecycle_event(session, {
        "event_type": "promoted_to_paper",
        "actor": "agent",
        "bot_id": "bot-xyz",
        "strategy_id": "bot04_chikou_momentum",
        "instrument": "EURUSD",
        "timeframe": "H1",
        "agent_run_id": "run-1",
        "backtest_result_id": "bt-9",
        "approval_status": "approved",
        "risk_decision": "within_limits",
        "reason": "OOS Sharpe 1.2, 142 trades",
    })
    assert ev.event_id
    got = ledger.list_lifecycle_events(session, bot_id="bot-xyz")
    assert len(got) == 1
    assert got[0].event_type == "promoted_to_paper"
    assert got[0].backtest_result_id == "bt-9"
    assert got[0].approval_status == "approved"


def test_strategy_lineage_parent_child(session):
    lin = ledger.create_strategy_lineage(session, {
        "strategy_id": "bot04_v2",
        "parent_strategy_id": "bot04_chikou_momentum",
        "origin": "mutated",
        "actor": "agent",
        "params_json": {"tenkan": 7},
    })
    assert lin.lineage_id
    rows = ledger.list_strategy_lineage(session, strategy_id="bot04_v2")
    assert rows[0].parent_strategy_id == "bot04_chikou_momentum"
    assert rows[0].origin == "mutated"


def test_unknown_event_type_rejected(session):
    with pytest.raises(ValueError):
        ledger.create_lifecycle_event(session, {"event_type": "teleported"})


def test_unknown_lane_rejected(session):
    with pytest.raises(ValueError):
        ledger.create_agent_run(session, {"lane": "rogue_lane"})


def test_ledger_is_append_only():
    """The repository must expose NO update or delete functions."""
    names = [n for n in dir(ledger) if not n.startswith("_")]
    forbidden = [n for n in names if ("update" in n.lower() or "delete" in n.lower())]
    assert forbidden == [], f"ledger must be append-only, found: {forbidden}"
    # And the create/read surface exists.
    for fn in ("create_agent_run", "create_lifecycle_event", "create_strategy_lineage",
               "list_agent_runs", "list_lifecycle_events", "list_strategy_lineage"):
        assert callable(getattr(ledger, fn))


def test_events_accumulate_not_overwrite(session):
    """Appending a second event for the same bot keeps both (write-once)."""
    for et in ("created", "backtested"):
        ledger.create_lifecycle_event(session, {"event_type": et, "bot_id": "b1"})
    rows = ledger.list_lifecycle_events(session, bot_id="b1")
    assert len(rows) == 2
