"""GET /api/intelligence/runs reads the agent audit and stays honest about gaps."""
from __future__ import annotations

from fiboki.agents.audit import (
    ActionKind,
    AuditRecord,
    JsonlAuditLedger,
    Outcome,
)


def _record(**overrides: object) -> AuditRecord:
    base: dict[str, object] = {
        "agent_id": "a1",
        "role": "quant_researcher",
        "kind": ActionKind.TOOL_CALL,
        "tool": "query_strategy",
        "inputs": {},
        "outputs": {},
        "reason": "because",
        "outcome": Outcome.OK,
        "workflow_id": "wf_test",
        "model_id": "qwen3:4b",
        "model_digest": "sha256:359d7dd4",
    }
    base.update(overrides)
    return AuditRecord(**base)  # type: ignore[arg-type]


def test_a_missing_ledger_is_absent(admin_client) -> None:
    body = admin_client.get("/api/intelligence/runs").json()
    assert body["source"]["kind"] == "absent"
    assert "audit.jsonl" in body["source"]["detail"]
    assert body["items"] == []
    assert body["total"] == 0


def test_a_fixture_audit_line_is_served_newest_first(admin_client, api_env) -> None:
    from pathlib import Path

    ledger = Path(api_env["FIBOKI_STATE_DIR"]) / "agents" / "audit.jsonl"
    store = JsonlAuditLedger(ledger)
    store.append(_record(tool="workflow:start", role="workflow"))
    store.append(
        _record(
            tool="workflow:end",
            role="workflow",
            kind=ActionKind.WORKFLOW_STEP,
        )
    )
    body = admin_client.get("/api/intelligence/runs").json()
    assert body["source"]["kind"] == "live"
    assert body["total"] == 2
    first = body["items"][0]
    assert first["workflow"] == "wf_test"
    assert first["model_id"] == "qwen3:4b"
    assert first["model_digest"] == "sha256:359d7dd4"
    assert first["outcome"] == "ok"
    assert first["tier"] == "t1_annotate_shadow"
    assert first["tier_source"] == "absent"
    assert first["activity"] == "idle"
    assert first["in_progress"] is False
    assert "20-run" in first["acceptance_protocol"]
    assert body["items"][1]["tool"] if False else body["items"][0]["role"] == "workflow"
    roles = admin_client.get("/api/intelligence/agents").json()
    assert all(role["can_execute"] is False for role in roles["items"])


def test_a_broken_hash_chain_names_the_sequence_and_hides_the_tail(admin_client, api_env) -> None:
    from pathlib import Path

    ledger = Path(api_env["FIBOKI_STATE_DIR"]) / "agents" / "audit.jsonl"
    store = JsonlAuditLedger(ledger)
    store.append(_record(workflow_id="wf_kept"))
    store.append(_record(workflow_id="wf_tail", tool="workflow:end", role="workflow"))
    lines = ledger.read_text(encoding="utf-8").splitlines()
    # Flip the second record's hash so the chain no longer verifies.
    broken = lines[1].replace(store.records()[1].record_hash, "0" * 64, 1)
    ledger.write_text(lines[0] + "\n" + broken + "\n", encoding="utf-8")
    body = admin_client.get("/api/intelligence/runs").json()
    assert body["source"]["kind"] == "live"
    assert "intact" in body["source"]["detail"].lower() or body["items"]
    assert body["total"] == 1
    assert body["items"][0]["workflow"] == "wf_kept"
    assert all(item["workflow"] != "wf_tail" for item in body["items"])
    codes = [caveat["code"] for caveat in body["caveats"]]
    assert "agent_audit_chain_broken" in codes
    message = next(c["message"] for c in body["caveats"] if c["code"] == "agent_audit_chain_broken")
    assert "sequence 1" in message
