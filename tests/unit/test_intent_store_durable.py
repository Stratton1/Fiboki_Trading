"""The intent store survives the crash it exists for (audit F P1-6).

Before: ``JsonlIntentStore._replay`` called ``json.loads`` on every line, so a
line torn by a crash mid-write made the store raise at startup and the worker
could not start until someone hand-edited the ledger.
"""
from __future__ import annotations

import json

import pandas as pd

from fiboki.broker.execution_service import IntentState, JsonlIntentStore, OrderIntent
from fiboki.core.durable import set_torn_tail_hook
from fiboki.core.enums import Direction, ExecutionMode

NOW = pd.Timestamp("2024-06-03 10:00", tz="UTC")


def _intent(ref: str, state: IntentState = IntentState.PENDING) -> OrderIntent:
    return OrderIntent(
        client_ref=ref, plan_id=f"p-{ref}", signal_id="s", strategy_id="ichimoku_a",
        instrument="EURUSD", direction=Direction.LONG, size=1000.0, mode=ExecutionMode.PAPER,
        state=state, created_at=NOW, updated_at=NOW,
    )


def test_a_torn_pending_intent_is_quarantined_and_the_store_opens(tmp_path) -> None:
    path = tmp_path / "intents.jsonl"
    store = JsonlIntentStore(path)
    store.write(_intent("a"))
    store.write(_intent("a", IntentState.FILLED))
    with path.open("ab") as fh:
        fh.write(_intent("b").to_json().encode()[:40])  # died mid-write of b
    seen: list = []
    set_torn_tail_hook("t", seen.append)
    try:
        reopened = JsonlIntentStore(path)
    finally:
        set_torn_tail_hook("t", None)
    assert reopened.get("a").state is IntentState.FILLED
    assert reopened.get("b") is None  # its dispatch never started: write() had not returned
    assert len(seen) == 1 and seen[0].quarantine_path.exists()
    reopened.write(_intent("c"))
    assert [i.client_ref for i in JsonlIntentStore(path).history()] == ["a", "a", "c"]


def test_a_legacy_unframed_intent_ledger_still_replays(tmp_path) -> None:
    path = tmp_path / "intents.jsonl"
    path.write_text(_intent("old").to_json() + "\n")
    store = JsonlIntentStore(path)
    assert store.get("old") is not None
    store.write(_intent("new"))
    lines = path.read_text().splitlines()
    assert not lines[0].startswith('{"_crc32"') and lines[1].startswith('{"_crc32"')
    assert json.loads(lines[1])["client_ref"] == "new"
    assert {i.client_ref for i in JsonlIntentStore(path).all()} == {"old", "new"}
