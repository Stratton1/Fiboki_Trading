"""PAUSE and FLATTEN mean different things, and both are journalled.

V1's kill switch blocked new orders and abandoned open positions, and nobody
had written down which of those two things it was meant to do.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.risk.killswitch import (
    FileKillSwitchJournal,
    InMemoryKillSwitchJournal,
    KillSwitch,
    KillSwitchMode,
    RequestKind,
)
from tests.exec_fixtures import NOW, make_position


def test_a_new_switch_is_inactive_and_permits_everything() -> None:
    ks = KillSwitch()
    assert not ks.active
    for kind in RequestKind:
        assert ks.allows(kind)[0]


# ------------------------------------------------------------ PAUSE


def test_pause_blocks_new_risk_but_not_risk_reduction() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="wide spreads", at=NOW)

    assert ks.allows(RequestKind.OPEN) == (False, "kill_switch_pause_blocks_new_risk")
    assert ks.allows(RequestKind.INCREASE)[0] is False
    # Getting flat must stay possible. A pause that traps you in is not a control.
    assert ks.allows(RequestKind.CLOSE)[0] is True
    assert ks.allows(RequestKind.REDUCE)[0] is True
    assert ks.allows(RequestKind.PROTECTIVE_AMEND)[0] is True


def test_pause_leaves_existing_positions_alone() -> None:
    """PAUSE explicitly does NOT enumerate closing intents."""
    ks = KillSwitch()
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="pause", at=NOW)
    positions = [make_position(), make_position(instrument="GBPUSD")]
    assert ks.flatten_orders(positions) == ()
    assert not ks.state.requires_flatten


# ---------------------------------------------------------- FLATTEN


def test_flatten_permits_only_closing_orders() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.FLATTEN, operator="tom", reason="incident", at=NOW)
    assert ks.allows(RequestKind.CLOSE)[0] is True
    assert ks.allows(RequestKind.REDUCE)[0] is True
    assert ks.allows(RequestKind.OPEN)[0] is False
    assert ks.allows(RequestKind.INCREASE)[0] is False
    assert ks.allows(RequestKind.PROTECTIVE_AMEND)[0] is False


def test_flatten_enumerates_every_open_position_deterministically() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.FLATTEN, operator="tom", reason="incident", at=NOW)
    positions = [
        make_position(instrument="GBPUSD", venue_ref="B"),
        make_position(instrument="EURUSD", venue_ref="A"),
        make_position(instrument="USDJPY", venue_ref="C"),
    ]
    intents = ks.flatten_orders(positions)
    assert [i.instrument for i in intents] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert [i.venue_ref for i in intents] == ["A", "B", "C"]
    # order is stable whatever order they arrived in
    assert ks.flatten_orders(list(reversed(positions))) == intents


def test_flatten_intents_carry_the_broker_reference() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.FLATTEN, operator="tom", reason="x", at=NOW)
    intent = ks.flatten_orders([make_position(venue_ref="DEAL-1")])[0]
    assert intent.venue_ref == "DEAL-1"
    assert "kill_switch_flatten" in intent.reason


def test_flatten_orders_is_empty_when_the_switch_is_off() -> None:
    assert KillSwitch().flatten_orders([make_position()]) == ()


# --------------------------------------------------- explicit semantics


def test_activation_requires_an_explicit_mode() -> None:
    ks = KillSwitch()
    with pytest.raises(TypeError):
        ks.activate("pause", operator="joe", reason="x")  # type: ignore[arg-type]


def test_activation_requires_an_operator_and_a_reason() -> None:
    ks = KillSwitch()
    with pytest.raises(ValueError):
        ks.activate(KillSwitchMode.PAUSE, operator="", reason="x")
    with pytest.raises(ValueError):
        ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="")


def test_pause_may_escalate_to_flatten() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="pause", at=NOW)
    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="worse", at=NOW)
    assert ks.mode is KillSwitchMode.FLATTEN


def test_flatten_may_NOT_be_quietly_downgraded_to_pause() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="incident", at=NOW)
    with pytest.raises(ValueError, match="downgrade"):
        ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="calmer now", at=NOW)
    assert ks.mode is KillSwitchMode.FLATTEN


def test_reversal_requires_an_explicit_operator_action() -> None:
    ks = KillSwitch()
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="x", at=NOW)
    with pytest.raises(ValueError):
        ks.deactivate(operator="", reason="y")
    with pytest.raises(ValueError):
        ks.deactivate(operator="joe", reason="")
    ks.deactivate(operator="tom", reason="spreads normalised", at=NOW)
    assert not ks.active
    assert ks.allows(RequestKind.OPEN)[0]


def test_deactivating_an_inactive_switch_raises() -> None:
    with pytest.raises(ValueError):
        KillSwitch().deactivate(operator="joe", reason="x")


def test_there_is_no_automatic_expiry() -> None:
    """A switch that turns itself off is not a kill switch."""
    ks = KillSwitch()
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="x", at=NOW)
    far_future = NOW + pd.Timedelta(days=365)
    replayed = KillSwitch(ks.journal)
    assert replayed.active
    assert replayed.state.since < far_future


# ------------------------------------------------------------- journal


def test_activation_and_deactivation_are_journalled() -> None:
    journal = InMemoryKillSwitchJournal()
    ks = KillSwitch(journal)
    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="incident 42",
                at=NOW, positions_open=3)
    ks.deactivate(operator="tom", reason="resolved", at=NOW + pd.Timedelta(hours=1))

    events = journal.events()
    assert [e.action for e in events] == ["activate", "deactivate"]
    assert events[0].mode is KillSwitchMode.FLATTEN
    assert events[0].operator == "joe"
    assert events[0].positions_open == 3
    assert events[1].operator == "tom"


def test_the_journal_is_append_only() -> None:
    journal = InMemoryKillSwitchJournal()
    ks = KillSwitch(journal)
    for i in range(4):
        ks.activate(KillSwitchMode.PAUSE, operator="joe", reason=f"r{i}", at=NOW)
        ks.deactivate(operator="joe", reason=f"d{i}", at=NOW)
    assert len(journal.events()) == 8


def test_state_survives_a_restart_via_the_file_journal(tmp_path) -> None:
    path = tmp_path / "killswitch.jsonl"
    ks = KillSwitch(FileKillSwitchJournal(path))
    ks.activate(KillSwitchMode.FLATTEN, operator="joe", reason="crash test", at=NOW)

    # a fresh process
    recovered = KillSwitch(FileKillSwitchJournal(path))
    assert recovered.active
    assert recovered.mode is KillSwitchMode.FLATTEN
    assert recovered.state.operator == "joe"
    assert recovered.state.reason == "crash test"
    assert recovered.allows(RequestKind.OPEN)[0] is False


def test_a_deactivation_also_survives_a_restart(tmp_path) -> None:
    path = tmp_path / "ks.jsonl"
    ks = KillSwitch(FileKillSwitchJournal(path))
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="x", at=NOW)
    ks.deactivate(operator="joe", reason="y", at=NOW)
    assert not KillSwitch(FileKillSwitchJournal(path)).active


def test_journal_rows_round_trip_exactly(tmp_path) -> None:
    path = tmp_path / "ks.jsonl"
    ks = KillSwitch(FileKillSwitchJournal(path))
    ks.activate(KillSwitchMode.PAUSE, operator="joe", reason="x", at=NOW,
                extra={"incident": "INC-1"})
    event = FileKillSwitchJournal(path).events()[0]
    assert event.extra == {"incident": "INC-1"}
    assert event.at == NOW
