"""Regression: worker reloads the paper account when the evaluation phase
changes, so a phase reset of daily/weekly PnL (and balance) is not clobbered
by the worker's stale in-memory copy. Offline, temp SQLite.
"""

import pytest

from fibokei.db.database import get_engine, get_session_factory, init_db
from fibokei.db.repository import (
    create_new_phase,
    get_or_create_paper_account,
    transition_to_new_phase,
)
from fibokei.worker import PaperWorker


@pytest.fixture()
def factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/phase.db")
    init_db(engine)
    return get_session_factory(engine)


def _set_account(factory, *, balance, daily, weekly):
    with factory() as s:
        acct = get_or_create_paper_account(s)
        acct.balance = balance
        acct.equity = balance
        acct.daily_pnl = daily
        acct.weekly_pnl = weekly
        s.commit()


def test_phase_change_reloads_account(factory):
    # Phase A active; worker is mid-phase with a negative daily PnL in memory.
    with factory() as s:
        phase_a = create_new_phase(s, "Phase A", "phase_a")
        phase_a_id = phase_a.id
    _set_account(factory, balance=806.54, daily=-193.46, weekly=-226.19)

    worker = PaperWorker(factory, dry_run=True)
    worker._active_phase_id = phase_a_id
    worker.account.balance = 806.54
    worker.account.daily_pnl = -193.46
    worker.account.weekly_pnl = -226.19

    # Operator transitions to Phase C and the endpoint zeroes the counters.
    with factory() as s:
        transition_to_new_phase(
            s, new_phase_name="Phase C", new_phase_label="phase_c",
            archive_name="Phase B", archive_label="phase_b",
        )
    _set_account(factory, balance=1000.0, daily=0.0, weekly=0.0)

    # Worker detects the phase change and reloads — stale negatives gone.
    worker._reload_account_if_phase_changed()
    assert worker.account.daily_pnl == 0.0
    assert worker.account.weekly_pnl == 0.0
    assert worker.account.balance == 1000.0
    assert worker._active_phase_id != phase_a_id


def test_first_call_just_caches_phase(factory):
    with factory() as s:
        create_new_phase(s, "Phase A", "phase_a")
    _set_account(factory, balance=500.0, daily=-50.0, weekly=-50.0)

    worker = PaperWorker(factory, dry_run=True)
    worker.account.daily_pnl = -50.0
    assert worker._active_phase_id is None
    worker._reload_account_if_phase_changed()  # first call: cache only
    assert worker._active_phase_id is not None
    assert worker.account.daily_pnl == -50.0  # unchanged on first call
