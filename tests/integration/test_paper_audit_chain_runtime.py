"""The paper runtime writes the audit chain: orders, fills, closes, controls, risk decisions.

Whole composition (``compose_runtime``) on the restore suite's synthetic EURUSD
H1 series. After a forward run with a pause and a restart in it, the chain
verifies from genesis, holds every kind of record it promises, and a single
edited row breaks verification at that row.
"""
from __future__ import annotations

import json
from pathlib import Path

from fiboki.ops.audit_chain import CHAIN_FILE, PaperAuditChain, canonical_json
from fiboki.ops.controls import BotControlLedger
from tests.integration import test_paper_forward_restore as harness


def test_the_runtime_chain_is_complete_survives_a_restart_and_detects_tampering(tmp_path: Path) -> None:
    frame = harness._frame()
    acct = harness.Account(tmp_path, frame)
    acct.start()
    for _ in range(60):
        acct.next_bar()
    BotControlLedger(acct.settings.paths.bot_controls).request(
        "forward-restore_test", "pause", actor="joe", actor_role="admin", reason="test: chain the pause"
    )
    for _ in range(5):
        acct.next_bar()
    acct.start()  # restart: the chain continues from its tail
    for _ in range(10):
        acct.next_bar()
    acct.close()

    chain = PaperAuditChain(acct.ledger / CHAIN_FILE, session_id="forward-restore_test")
    result = chain.verify()
    assert result.present and result.intact, result.reason
    for kind in (
        "session_start", "risk_decision", "order_intent", "venue_fill", "position_opened",
        "trade_closed", "venue_instruction", "control_applied", "control_state_at_start",
    ):
        assert result.kinds.get(kind, 0) > 0, f"no {kind} row in the chain: {result.kinds}"
    # Every closed trade in the ledger of record is in the chain.
    trades = acct.trades()
    chained = [r for r in chain.rows() if r["kind"] == "trade_closed"]
    assert {t["trade_id"] for t in trades} == {r["payload"]["trade_id"] for r in chained}

    # Tamper with one fill's price: verification names that row.
    lines = chain.path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    target = next(i for i, r in enumerate(rows) if r["kind"] == "venue_fill")
    rows[target]["payload"]["price"] = float(rows[target]["payload"]["price"]) + 0.0001
    chain.path.write_text("".join(canonical_json(r) + "\n" for r in rows), encoding="utf-8")
    broken = PaperAuditChain(chain.path, session_id="forward-restore_test").verify()
    assert not broken.intact and broken.first_broken_seq == target + 1
