"""The ledger is append-only, hash-chained, and records refusals as faithfully
as successes. Nothing an agent does may be invisible."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from fiboki.agents.audit import (
    GENESIS_HASH,
    ActionKind,
    AuditedAction,
    AuditLedger,
    AuditRecord,
    ChainError,
    JsonlAuditLedger,
    Outcome,
)


def _record(tool: str = "query_strategy", **overrides: object) -> AuditRecord:
    base: dict[str, object] = {
        "agent_id": "a1",
        "role": "quant_researcher",
        "kind": ActionKind.TOOL_CALL,
        "tool": tool,
        "inputs": {"strategy_id": "x"},
        "outputs": {"ok": True},
        "reason": "because",
        "outcome": Outcome.OK,
    }
    base.update(overrides)
    return AuditRecord(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------ append-only


def test_ledger_has_no_mutation_surface() -> None:
    """The absence of update/delete is the guarantee; enumerate and assert it."""
    public = {
        name
        for name in dir(AuditLedger)
        if not name.startswith("_") and callable(getattr(AuditLedger, name, None))
    }
    forbidden = {"update", "delete", "remove", "truncate", "clear", "pop", "edit", "replace"}
    assert not (public & forbidden), f"mutation surface appeared: {public & forbidden}"
    assert public == set(AuditLedger.PUBLIC_SURFACE)


def test_jsonl_ledger_has_no_mutation_surface() -> None:
    public = {
        name
        for name in dir(JsonlAuditLedger)
        if not name.startswith("_") and callable(getattr(JsonlAuditLedger, name, None))
    }
    assert public == set(JsonlAuditLedger.PUBLIC_SURFACE) - {"path"}


def test_records_are_frozen() -> None:
    ledger = AuditLedger()
    sealed = ledger.append(_record())
    with pytest.raises(FrozenInstanceError):
        sealed.outcome = Outcome.ERROR  # type: ignore[misc]


def test_records_returns_a_copy_not_the_backing_list() -> None:
    ledger = AuditLedger()
    ledger.append(_record())
    snapshot = ledger.records()
    assert isinstance(snapshot, tuple)
    ledger.append(_record())
    assert len(snapshot) == 1
    assert len(ledger.records()) == 2


# -------------------------------------------------------------- chaining


def test_chain_links_each_record_to_the_previous() -> None:
    ledger = AuditLedger()
    first = ledger.append(_record("a"))
    second = ledger.append(_record("b"))
    assert first.previous_hash == GENESIS_HASH
    assert first.sequence == 0
    assert second.previous_hash == first.record_hash
    assert second.sequence == 1
    assert ledger.verify_chain()


def test_tampering_with_a_persisted_record_is_detectable(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    for i in range(4):
        ledger.append(_record(f"tool_{i}"))
    assert ledger.verify_chain()

    lines = path.read_text().strip().split("\n")
    doctored = json.loads(lines[1])
    doctored["outputs"] = {"ok": False, "quietly": "changed"}
    lines[1] = json.dumps(doctored, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    reloaded = JsonlAuditLedger(path)
    assert not reloaded.verify_chain()


def test_deleting_a_persisted_record_is_detectable(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    for i in range(4):
        ledger.append(_record(f"tool_{i}"))
    lines = path.read_text().strip().split("\n")
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert not JsonlAuditLedger(path).verify_chain()


def test_a_persisted_ledger_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    ledger.append(_record("a", reason="first"))
    ledger.append(_record("b", reason="second"))

    reloaded = JsonlAuditLedger(path)
    assert len(reloaded) == 2
    assert reloaded.verify_chain()
    assert [r.reason for r in reloaded.records()] == ["first", "second"]


def test_unreadable_persisted_record_raises(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text("{not json}\n")
    with pytest.raises(ChainError, match="unreadable record"):
        JsonlAuditLedger(path)


# ------------------------------------------------------- full capture


def test_a_record_captures_everything_the_brief_requires() -> None:
    ledger = AuditLedger()
    sealed = ledger.append(
        _record(
            prompt="the actual prompt",
            parent_action_id="act_parent",
            workflow_id="wf_1",
            model="llama3.1:8b-instruct",
            model_version="v1.2.3",
            provider="local",
            prompt_tokens=1200,
            completion_tokens=340,
            cost_usd=0.0021,
            wall_ms=12.5,
            capability="read:strategy",
        )
    )
    payload = sealed.payload()
    for field in (
        "agent_id", "role", "tool", "inputs", "outputs", "reason", "prompt",
        "parent_action_id", "model", "model_version", "prompt_tokens",
        "completion_tokens", "cost_usd", "wall_ms", "outcome", "capability",
    ):
        assert field in payload, field
    assert payload["model_version"] == "v1.2.3"
    assert payload["parent_action_id"] == "act_parent"


def test_audited_action_records_a_raising_call_as_an_error() -> None:
    ledger = AuditLedger()
    with pytest.raises(RuntimeError), AuditedAction(
        ledger, agent_id="a1", role="r", kind=ActionKind.TOOL_CALL,
        tool="explodes", inputs={"x": 1}, reason="test",
    ):
        raise RuntimeError("boom")
    assert len(ledger) == 1
    record = ledger.records()[0]
    assert record.outcome is Outcome.ERROR
    assert "boom" in record.error
    assert record.outputs == {}


def test_audited_action_records_a_denial_as_denied() -> None:
    ledger = AuditLedger()
    with pytest.raises(PermissionError), AuditedAction(
        ledger, agent_id="a1", role="r", kind=ActionKind.TOOL_CALL,
        tool="forbidden", inputs={}, reason="test",
    ):
        raise PermissionError("not for you")
    assert ledger.records()[0].outcome is Outcome.DENIED


def test_audited_action_times_the_call() -> None:
    ledger = AuditLedger()
    with AuditedAction(
        ledger, agent_id="a1", role="r", kind=ActionKind.MODEL_CALL,
        tool="model:echo", inputs={}, reason="test",
    ) as action:
        action.outputs = {"text": "hi"}
        action.cost_usd = 0.5
    record = ledger.records()[0]
    assert record.wall_ms >= 0.0
    assert record.cost_usd == 0.5
    assert record.outcome is Outcome.OK


def test_cost_and_workflow_views() -> None:
    ledger = AuditLedger()
    parent = ledger.append(_record("a", workflow_id="wf_1", cost_usd=0.01))
    ledger.append(_record("b", workflow_id="wf_1", cost_usd=0.02, parent_action_id=parent.action_id))
    ledger.append(_record("c", workflow_id="wf_2", cost_usd=0.04))
    assert len(ledger.by_workflow("wf_1")) == 2
    assert len(ledger.children_of(parent.action_id)) == 1
    assert ledger.total_cost_usd() == pytest.approx(0.07)
    assert len(ledger.tail(2)) == 2
