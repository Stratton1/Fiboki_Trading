"""The kill switch is re-read before every decision and must be durable (audit F P0-1).

Before: ``KillSwitch`` replayed its journal once, in ``__init__``, so a process
that built its gateway before an activation kept trading until restarted; and
``RiskGateway()`` silently held an in-memory journal no operator could reach.
"""
from __future__ import annotations

import json

import pytest

from fiboki.core.durable import set_torn_tail_hook
from fiboki.core.enums import ExecutionMode
from fiboki.core.paths import resolve_paths
from fiboki.risk.gateway import InMemoryAttemptRecorder, RiskGateway
from fiboki.risk.killswitch import (
    FileKillSwitchJournal,
    KillSwitch,
    KillSwitchMode,
    RequestKind,
)
from tests.exec_fixtures import NOW, make_context


def test_an_activation_by_another_instance_binds_the_very_next_decision(tmp_path) -> None:
    path = tmp_path / "killswitch.jsonl"
    worker_side = KillSwitch.at_path(path)
    assert worker_side.allows(RequestKind.OPEN)[0]
    KillSwitch.at_path(path).activate(KillSwitchMode.PAUSE, operator="joe", reason="spreads")
    ok, why = worker_side.allows(RequestKind.OPEN)
    assert not ok and why == "kill_switch_pause_blocks_new_risk"
    assert worker_side.state.operator == "joe"  # state refreshes too (the API page)


def test_a_deactivation_elsewhere_is_seen_and_a_downgrade_is_still_refused(tmp_path) -> None:
    path = tmp_path / "killswitch.jsonl"
    a, b = KillSwitch.at_path(path), KillSwitch.at_path(path)
    a.activate(KillSwitchMode.FLATTEN, operator="tom", reason="incident")
    # b has never looked since construction; it must not downgrade FLATTEN.
    with pytest.raises(ValueError, match="downgrade"):
        b.activate(KillSwitchMode.PAUSE, operator="joe", reason="calmer")
    b.deactivate(operator="joe", reason="resolved")
    assert not a.active


def test_refresh_is_a_stat_not_a_replay_when_nothing_changed(tmp_path, monkeypatch) -> None:
    switch = KillSwitch.at_path(tmp_path / "k.jsonl")
    calls = {"n": 0}
    real = switch._replay

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(switch, "_replay", counting)
    for _ in range(5):
        switch.allows(RequestKind.OPEN)
    assert calls["n"] == 0


def test_a_torn_journal_still_loads_and_alerts(tmp_path) -> None:
    path = tmp_path / "killswitch.jsonl"
    KillSwitch.at_path(path).activate(KillSwitchMode.PAUSE, operator="joe", reason="x")
    with path.open("ab") as fh:
        fh.write(b'{"_crc32":"12345678","action":"deac')
    seen: list = []
    set_torn_tail_hook("t", seen.append)
    try:
        switch = KillSwitch.at_path(path)
    finally:
        set_torn_tail_hook("t", None)
    assert switch.active and switch.mode is KillSwitchMode.PAUSE
    assert len(seen) == 1


def test_a_legacy_unframed_journal_is_still_read(tmp_path) -> None:
    path = tmp_path / "killswitch.jsonl"
    path.write_text(
        json.dumps(
            {
                "action": "activate", "mode": "flatten", "operator": "joe",
                "reason": "legacy", "at": NOW.isoformat(), "positions_open": 0, "extra": {},
            }
        )
        + "\n"
    )
    switch = KillSwitch.at_path(path)
    assert switch.mode is KillSwitchMode.FLATTEN
    switch.deactivate(operator="joe", reason="done")  # a framed line after a legacy one
    assert not KillSwitch.at_path(path).active


def test_from_paths_uses_the_resolved_journal(tmp_path) -> None:
    paths = resolve_paths({"FIBOKI_STATE_DIR": str(tmp_path)})
    switch = KillSwitch.from_paths(paths)
    assert switch.durable and switch.journal.path == tmp_path / "killswitch.jsonl"


# ----------------------------------------------------------------- gateway


@pytest.mark.parametrize(
    "mode", [ExecutionMode.PAPER, ExecutionMode.SHADOW, ExecutionMode.DEMO, ExecutionMode.LIVE]
)
def test_the_gateway_refuses_an_in_memory_journal_outside_backtest(mode) -> None:
    with pytest.raises(ValueError, match="in-memory kill-switch journal"):
        RiskGateway(mode=mode)
    with pytest.raises(ValueError, match="in-memory"):
        RiskGateway(mode=mode, kill_switch=KillSwitch())


def test_backtest_may_use_memory_and_a_declared_gateway_accepts_a_file(tmp_path) -> None:
    assert not RiskGateway(mode=ExecutionMode.BACKTEST).kill_switch.durable
    gw = RiskGateway(mode=ExecutionMode.DEMO, kill_switch=KillSwitch.at_path(tmp_path / "k.jsonl"))
    assert gw.kill_switch.durable and isinstance(gw.kill_switch.journal, FileKillSwitchJournal)


@pytest.mark.parametrize("mode", [ExecutionMode.SHADOW, ExecutionMode.DEMO, ExecutionMode.LIVE])
def test_an_undeclared_gateway_blocks_broker_opens_without_a_durable_switch(mode) -> None:
    recorder = InMemoryAttemptRecorder()
    decision = RiskGateway(recorder=recorder).evaluate(make_context(mode=mode))
    assert not decision.allowed
    assert f"kill_switch_journal_not_durable:{mode.value}" in decision.reasons
    assert recorder.attempts[-1].extra["kill_switch_journal"] == "in_memory"


def test_an_in_memory_paper_decision_is_stamped_not_hidden() -> None:
    recorder = InMemoryAttemptRecorder()
    decision = RiskGateway(recorder=recorder).evaluate(make_context(mode=ExecutionMode.PAPER))
    assert decision.allowed, decision.reasons
    assert recorder.attempts[-1].extra["kill_switch_journal"] == "in_memory"


def test_a_gateway_built_before_the_activation_blocks_the_next_evaluate(tmp_path) -> None:
    path = tmp_path / "killswitch.jsonl"
    gw = RiskGateway(
        mode=ExecutionMode.PAPER,
        kill_switch=KillSwitch.at_path(path),
        recorder=InMemoryAttemptRecorder(),
    )
    assert gw.evaluate(make_context()).allowed
    KillSwitch.at_path(path).activate(KillSwitchMode.PAUSE, operator="joe", reason="now")
    decision = gw.evaluate(make_context())
    assert not decision.allowed
    assert "kill_switch_pause_blocks_new_risk" in decision.reasons
