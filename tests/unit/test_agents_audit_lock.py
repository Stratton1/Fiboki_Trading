"""JsonlAuditLedger inter-process safety.

Two processes (the API and the research worker) may hold a ledger on the same
file.  Each caches the chain it has seen; without a lock and a tail check they
would each seal a record on the same tail and fork the chain.  These tests pin
the three behaviours that prevent it: catch-up under the lock, refusal of a
stale view, and a single valid chain from concurrent writers.
"""
from __future__ import annotations

import json
import multiprocessing
from itertools import pairwise
from pathlib import Path

import pytest

from fiboki.agents.audit import (
    ActionKind,
    AuditChainForkError,
    AuditRecord,
    ChainError,
    JsonlAuditLedger,
    Outcome,
)

N_WRITERS = 4
PER_WRITER = 25


def _record(tool: str, agent_id: str = "a1") -> AuditRecord:
    return AuditRecord(
        agent_id=agent_id,
        role="quant_researcher",
        kind=ActionKind.TOOL_CALL,
        tool=tool,
        inputs={"x": 1},
        outputs={"ok": True},
        reason="lock test",
        outcome=Outcome.OK,
    )


def _disk_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _disk_chain_is_valid(path: Path) -> bool:
    # A fresh instance runs the full verification on load.
    return JsonlAuditLedger(path).verify_chain()


# ----------------------------------------------------------- two instances


def test_two_instances_appending_alternately_stay_one_valid_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    a = JsonlAuditLedger(path)
    b = JsonlAuditLedger(path)
    for i in range(6):
        (a if i % 2 == 0 else b).append(_record(f"tool_{i}"))

    rows = _disk_records(path)
    assert [r["sequence"] for r in rows] == list(range(6))
    assert [r["tool"] for r in rows] == [f"tool_{i}" for i in range(6)]
    for prev, cur in pairwise(rows):
        assert cur["previous_hash"] == prev["record_hash"]
    assert _disk_chain_is_valid(path)
    # Each instance adopted the other's records while catching up.
    assert len(a) == 5 and len(b) == 6
    assert a.verify_chain() and b.verify_chain()


def test_lock_file_is_a_sidecar_and_is_never_written(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    ledger.append(_record("a"))
    lock = tmp_path / "audit.jsonl.lock"
    assert lock.exists()
    assert lock.stat().st_size == 0


def test_first_append_creates_the_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    assert not path.exists()
    ledger.append(_record("a"))
    assert path.exists()
    assert _disk_chain_is_valid(path)


# ------------------------------------------------------------ stale views


def test_stale_instance_whose_tail_no_longer_matches_disk_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    stale = JsonlAuditLedger(path)
    stale.append(_record("original_0"))
    stale.append(_record("original_1"))

    # Another, independent chain of the same length replaces the file.
    other_path = tmp_path / "other.jsonl"
    other = JsonlAuditLedger(other_path)
    other.append(_record("impostor_0"))
    other.append(_record("impostor_1"))
    path.write_bytes(other_path.read_bytes())
    before = path.read_bytes()

    with pytest.raises(AuditChainForkError, match="no longer holds the chain"):
        stale.append(_record("would_fork"))
    assert path.read_bytes() == before, "a refused append must not touch the file"
    assert len(stale) == 2


def test_refused_when_the_file_was_truncated(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    for i in range(3):
        ledger.append(_record(f"t{i}"))
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(lines[:1]))

    with pytest.raises(AuditChainForkError, match="truncated or replaced"):
        ledger.append(_record("after_truncation"))


def test_refused_when_another_writer_appended_a_record_that_does_not_continue_the_chain(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    ledger.append(_record("t0"))

    # A record sealed as if it were the genesis record, appended behind our back.
    rogue = _record("rogue").sealed(sequence=0, previous_hash="0" * 64)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(rogue.payload(), sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(AuditChainForkError, match="does not continue this chain"):
        ledger.append(_record("t1"))


def test_fork_error_is_a_chain_error() -> None:
    assert issubclass(AuditChainForkError, ChainError)


def test_a_ledger_loaded_with_a_broken_chain_refuses_to_append(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = JsonlAuditLedger(path)
    for i in range(3):
        ledger.append(_record(f"t{i}"))
    lines = path.read_text().splitlines()
    doctored = json.loads(lines[1])
    doctored["outputs"] = {"ok": False}
    lines[1] = json.dumps(doctored, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    reloaded = JsonlAuditLedger(path)
    assert not reloaded.verify_chain()
    with pytest.raises(ChainError, match="failed verification"):
        reloaded.append(_record("on_top_of_a_break"))


# ------------------------------------------------------- concurrent processes


def _writer(path_text: str, writer: int, count: int) -> None:
    ledger = JsonlAuditLedger(path_text)
    for i in range(count):
        ledger.append(_record(f"w{writer}_{i}", agent_id=f"writer_{writer}"))


def test_concurrent_processes_produce_one_valid_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_writer, args=(str(path), w, PER_WRITER)) for w in range(N_WRITERS)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=120)
    assert all(proc.exitcode == 0 for proc in procs), [p.exitcode for p in procs]

    rows = _disk_records(path)
    assert len(rows) == N_WRITERS * PER_WRITER
    assert [r["sequence"] for r in rows] == list(range(N_WRITERS * PER_WRITER))
    assert len({r["action_id"] for r in rows}) == N_WRITERS * PER_WRITER
    for w in range(N_WRITERS):
        mine = [r["tool"] for r in rows if r["agent_id"] == f"writer_{w}"]
        assert mine == [f"w{w}_{i}" for i in range(PER_WRITER)]
    assert _disk_chain_is_valid(path)
