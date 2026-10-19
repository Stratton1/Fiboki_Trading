"""Per-bot control ledger (ops/controls.py): transitions, reasons, replay, restart."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fiboki.ops.controls import (
    RUNTIME_ACTOR,
    BotControlLedger,
    BotState,
    ControlRefused,
)

REASON = "news week on EURUSD, reviewing"


def _ledger(tmp_path) -> BotControlLedger:
    return BotControlLedger(tmp_path / "paper_controls.jsonl")


def test_an_uncontrolled_bot_is_active_and_trades(tmp_path):
    state = _ledger(tmp_path).state("forward-v1")
    assert state.state is BotState.ACTIVE and not state.blocks_entries
    assert state.last is None and state.records == 0
    assert state.allowed_actions() == ["pause", "drain"]


def test_pause_then_resume_records_operator_reason_and_time(tmp_path):
    ledger = _ledger(tmp_path)
    at = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    rec = ledger.request("forward-v1", "pause", actor="joe", actor_role="admin", reason=REASON, at=at)
    assert (rec.state_before, rec.state_after) == (BotState.ACTIVE, BotState.PAUSED)
    state = ledger.state("forward-v1")
    assert state.blocks_entries and state.last.actor == "joe" and state.last.reason == REASON
    assert state.last.at == at
    ledger.request("forward-v1", "resume", actor="tom", actor_role="admin", reason="review done, resume")
    assert ledger.state("forward-v1").state is BotState.ACTIVE
    assert [r.action.value for r in ledger.records("forward-v1")] == ["pause", "resume"]


@pytest.mark.parametrize("reason", ["", "short", "x" * 501])
def test_a_reason_is_required_and_bounded(tmp_path, reason):
    with pytest.raises(ValueError, match="reason"):
        _ledger(tmp_path).request("forward-v1", "pause", actor="joe", actor_role="admin", reason=reason)
    assert _ledger(tmp_path).records() == []


def test_refused_transitions_say_why_and_write_nothing(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ControlRefused, match="active"):
        ledger.request("forward-v1", "resume", actor="joe", actor_role="admin", reason=REASON)
    ledger.request("forward-v1", "pause", actor="joe", actor_role="admin", reason=REASON)
    with pytest.raises(ControlRefused):
        ledger.request("forward-v1", "pause", actor="joe", actor_role="admin", reason=REASON)
    assert len(ledger.records()) == 1


def test_drain_completes_only_from_draining_and_only_by_the_runtime(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ControlRefused):
        ledger.complete_drain("forward-v1")
    ledger.request("forward-v1", "drain", actor="joe", actor_role="admin", reason=REASON)
    with pytest.raises(ControlRefused, match="runtime"):
        ledger.request("forward-v1", "drain_complete", actor="joe", actor_role="admin", reason=REASON)
    rec = ledger.complete_drain("forward-v1")
    assert rec.actor == RUNTIME_ACTOR and rec.state_after is BotState.STOPPED
    stopped = ledger.state("forward-v1")
    assert stopped.blocks_entries and stopped.allowed_actions() == ["resume"]
    ledger.request("forward-v1", "resume", actor="joe", actor_role="admin", reason="resume after drain")
    assert ledger.state("forward-v1").state is BotState.ACTIVE


def test_bots_are_independent_and_state_survives_a_new_reader(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.request("forward-a", "pause", actor="joe", actor_role="admin", reason=REASON)
    ledger.request("forward-b", "drain", actor="joe", actor_role="admin", reason=REASON)
    again = BotControlLedger(ledger.path)  # a restarted process
    assert again.state("forward-a").state is BotState.PAUSED
    assert again.state("forward-b").state is BotState.DRAINING
    assert again.state("forward-c").state is BotState.ACTIVE
    assert set(again.states()) == {"forward-a", "forward-b"}
