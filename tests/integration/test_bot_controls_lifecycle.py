"""Per-bot pause / resume / drain on the WHOLE paper composition (``compose_runtime``).

Driven by the restore suite's harness (``test_paper_forward_restore``): a fake
clock and a transport that serves one synthetic EURUSD H1 series by the clock.
The control is written to the SAME ledger the API writes
(``Settings.paths.bot_controls``), between cycles, as an operator would.

What these prove:

* PAUSE stops new entries and keeps managing what is open: a position open
  when the bot is paused closes on the bar, at the price and for the reason the
  uninterrupted run closes it; no position opens while paused; the signals the
  pause suppressed are journalled; RESUME lets entries through again.
* DRAIN lets the open position close by its own rule, then the runtime records
  ``drain_complete`` and the bot is ``stopped``: nothing opens afterwards.
* A RESTART mid-pause comes back paused (the ledger is the authority, not the
  process), says so in the journal, and still suppresses entries.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fiboki.ops.controls import BotControlLedger, BotState
from tests.integration import test_paper_forward_restore as harness

SID = "forward-restore_test"
H1 = harness.H1
BAR0 = harness.FIRST_POLL - H1  # the bar the first poll closes


def _poll_of(bar_start: pd.Timestamp) -> int:
    return int((pd.Timestamp(bar_start) - BAR0) / H1)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return harness._frame()


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory, frame: pd.DataFrame) -> harness.Account:
    return harness.run_uninterrupted(tmp_path_factory.mktemp("controls_baseline"), frame)


def _held_trade(baseline: harness.Account, *, min_bars: int = 3) -> dict:
    for trade in baseline.trades():
        entry, exit_ = pd.Timestamp(trade["entry_time"]), pd.Timestamp(trade["exit_time"])
        if exit_ - entry >= min_bars * H1 and _poll_of(exit_) + 3 < harness.N_POLLS:
            return trade
    pytest.skip("the synthetic series has no trade held long enough to pause over")


def _ledger(acct: harness.Account) -> BotControlLedger:
    return BotControlLedger(acct.settings.paths.bot_controls)


def _drive(acct: harness.Account, until_poll: int) -> None:
    """Run polls until ``until_poll`` (inclusive) has been processed."""
    while _current_poll(acct) < until_poll:
        acct.next_bar()


def _current_poll(acct: harness.Account) -> int:
    return _poll_of(acct.clock.now.floor("h") - H1)


def _opened_bars(acct: harness.Account) -> list[pd.Timestamp]:
    return [pd.Timestamp(e["bar_time"]) for e in acct.events("position_opened")]


def test_pause_keeps_managing_the_open_position_and_resume_lets_entries_back(
    tmp_path: Path, frame: pd.DataFrame, baseline: harness.Account
) -> None:
    trade = _held_trade(baseline)
    entry, exit_ = pd.Timestamp(trade["entry_time"]), pd.Timestamp(trade["exit_time"])
    acct = harness.Account(tmp_path, frame)
    acct.start()
    _drive(acct, _poll_of(entry))
    assert acct.runtime.manager.open_positions, "the position is open when the bot is paused"
    _ledger(acct).request(SID, "pause", actor="joe", actor_role="admin", reason="test: pause mid-trade")
    paused_from = _current_poll(acct)
    resume_at = min(harness.N_POLLS - 5, _poll_of(exit_) + 30)
    _drive(acct, resume_at)

    # The open position was managed to its own exit, exactly as uninterrupted.
    closed = {pd.Timestamp(t["entry_time"]): t for t in acct.trades()}
    mine = closed[entry]
    assert (mine["exit_time"], mine["exit_price"], mine["exit_reason"], mine["net_pnl"]) == (
        trade["exit_time"], trade["exit_price"], trade["exit_reason"], trade["net_pnl"]
    )
    # Nothing opened while paused; what the pause suppressed is on the record.
    opened_while_paused = [b for b in _opened_bars(acct) if _poll_of(b) > paused_from]
    assert opened_while_paused == []
    applied = acct.events("control_applied")
    assert [a["action"] for a in applied] == ["pause"] and applied[0]["actor"] == "joe"
    suppressed = acct.events("entries_suppressed_by_control")
    baseline_signals_in_window = [
        b for b in baseline.signal_bars if paused_from < _poll_of(b) + 1 <= resume_at
    ]
    assert baseline_signals_in_window, "the scenario must signal while paused to mean anything"
    assert suppressed, "a signal the uninterrupted run took was suppressed and journalled"
    assert all(s["state"] == "paused" for s in suppressed)

    _ledger(acct).request(SID, "resume", actor="joe", actor_role="admin", reason="test: resume trading")
    _drive(acct, harness.N_POLLS - 1)
    assert [a["action"] for a in acct.events("control_applied")] == ["pause", "resume"]
    later = [b for b in _opened_bars(acct) if _poll_of(b) > resume_at]
    later_signals = [b for b in baseline.signal_bars if _poll_of(b) > resume_at + 1]
    assert later_signals and later, "after resume the bot opens positions again"
    summary = acct.runtime.journal.session_dir / "summary.json"
    assert '"control_state": "active"' in summary.read_text()
    acct.close()


def test_drain_closes_by_its_own_rules_then_stops(
    tmp_path: Path, frame: pd.DataFrame, baseline: harness.Account
) -> None:
    trade = _held_trade(baseline)
    entry, exit_ = pd.Timestamp(trade["entry_time"]), pd.Timestamp(trade["exit_time"])
    acct = harness.Account(tmp_path, frame)
    acct.start()
    _drive(acct, _poll_of(entry))
    _ledger(acct).request(SID, "drain", actor="tom", actor_role="admin", reason="test: drain to flat")
    drained_from = _current_poll(acct)
    _drive(acct, _poll_of(exit_) + 1)
    state = _ledger(acct).state(SID)
    assert state.state is BotState.STOPPED, "flat after the exit, so the drain completed"
    assert state.last.actor == "paper-runtime"
    mine = {pd.Timestamp(t["entry_time"]): t for t in acct.trades()}[entry]
    assert (mine["exit_time"], mine["exit_price"]) == (trade["exit_time"], trade["exit_price"])
    actions = [a["action"] for a in acct.events("control_applied")]
    assert actions == ["drain", "drain_complete"]
    _drive(acct, harness.N_POLLS - 1)
    assert [b for b in _opened_bars(acct) if _poll_of(b) > drained_from] == []
    assert not acct.runtime.manager.open_positions
    acct.close()


def test_a_restart_mid_pause_comes_back_paused(
    tmp_path: Path, frame: pd.DataFrame, baseline: harness.Account
) -> None:
    acct = harness.Account(tmp_path, frame)
    acct.start()
    _drive(acct, 5)
    _ledger(acct).request(SID, "pause", actor="joe", actor_role="admin", reason="test: pause then restart")
    _drive(acct, 20)
    acct.start()  # a NEW process on the same state directory
    at_start = acct.events("control_state_at_start")
    assert at_start and at_start[-1]["state"] == "paused" and at_start[-1]["actor"] == "joe"
    assert acct.runtime.controls.blocks_entries
    _drive(acct, harness.N_POLLS - 1)
    assert [b for b in _opened_bars(acct) if _poll_of(b) > 5] == []
    signals_after = [b for b in baseline.signal_bars if _poll_of(b) > 6]
    assert signals_after and acct.events("entries_suppressed_by_control")
    # The snapshot records the state for information; the ledger is the authority.
    import json

    from fiboki.core.durable import read_record

    snap = json.loads(read_record(acct.ledger / "state_snapshot.json"))
    assert snap["control"]["state"] == "paused"
    acct.close()
