"""The paper audit chain (ops/audit_chain.py): canonical hashing, linkage, tamper evidence."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd

from fiboki.ops.audit_chain import GENESIS, PaperAuditChain, canonical_json, row_hash


def _chain(tmp_path) -> PaperAuditChain:
    chain = PaperAuditChain(tmp_path / "audit_chain.jsonl", session_id="forward-v1")
    at = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    chain.append("order_intent", {"client_ref": "FBK-1", "size": 1000.0, "state": "pending"}, at=at)
    chain.append("venue_fill", {"price": 1.10123, "nan": float("nan")}, at=pd.Timestamp(at))
    chain.append("risk_decision", {"allowed": False, "reasons": ["market_closed"]}, at=at)
    chain.append("trade_closed", {"net_pnl": -12.5}, at=at)
    return chain


def _rewrite(path, mutate) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    mutate(rows)
    path.write_text("".join(canonical_json(r) + "\n" for r in rows), encoding="utf-8")


def test_an_untouched_chain_verifies_from_genesis_and_is_deterministic(tmp_path):
    chain = _chain(tmp_path)
    result = chain.verify()
    assert result.present and result.intact and result.rows == 4 and result.first_broken_seq is None
    assert result.kinds == {"order_intent": 1, "venue_fill": 1, "risk_decision": 1, "trade_closed": 1}
    rows = chain.rows()
    assert rows[0]["prev_hash"] == GENESIS
    assert [r["prev_hash"] for r in rows[1:]] == [r["hash"] for r in rows[:-1]]
    assert all(row_hash(r) == r["hash"] for r in rows)
    assert rows[1]["payload"]["nan"] is None  # NaN is stored and hashed as null
    other = _chain(tmp_path / "again")
    assert [r["hash"] for r in other.rows()] == [r["hash"] for r in rows]
    assert result.head_hash == rows[-1]["hash"] == chain.head


def test_editing_a_row_breaks_verification_at_that_row(tmp_path):
    chain = _chain(tmp_path)

    def edit(rows):
        rows[1]["payload"]["price"] = 1.20123

    _rewrite(chain.path, edit)
    result = PaperAuditChain(chain.path, session_id="forward-v1").verify()
    assert not result.intact and result.first_broken_seq == 2 and "edited" in result.reason


def test_rehashing_an_edited_row_breaks_the_next_link(tmp_path):
    chain = _chain(tmp_path)

    def edit_and_rehash(rows):
        rows[1]["payload"]["price"] = 1.20123
        rows[1]["hash"] = row_hash(rows[1])

    _rewrite(chain.path, edit_and_rehash)
    result = PaperAuditChain(chain.path, session_id="forward-v1").verify()
    assert not result.intact and result.first_broken_seq == 3 and "prev_hash" in result.reason


def test_deleting_or_reordering_a_row_is_named(tmp_path):
    chain = _chain(tmp_path)
    _rewrite(chain.path, lambda rows: rows.pop(1))
    deleted = PaperAuditChain(chain.path, session_id="forward-v1").verify()
    assert not deleted.intact and deleted.first_broken_seq == 2

    chain2 = _chain(tmp_path / "b")
    _rewrite(chain2.path, lambda rows: rows.reverse())
    swapped = PaperAuditChain(chain2.path, session_id="forward-v1").verify()
    assert not swapped.intact and swapped.first_broken_seq == 1


def test_a_new_writer_continues_the_chain_from_the_tail(tmp_path):
    chain = _chain(tmp_path)
    again = PaperAuditChain(chain.path, session_id="forward-v1")
    again.append("control_applied", {"action": "pause"}, at=datetime(2026, 10, 19, 10, tzinfo=UTC))
    result = again.verify()
    assert result.intact and result.rows == 5


def test_an_absent_chain_is_reported_absent_not_intact_evidence(tmp_path):
    result = PaperAuditChain(tmp_path / "none.jsonl", session_id="x").verify()
    assert not result.present and result.rows == 0 and "no audit chain" in result.reason
