"""Paper trading execution adapter.

In Phase 1 of the multi-broker fan-out architecture this adapter now returns
a synthetic ``deal_id`` so the router can track per-target open positions
even when paper is just one of several active execution targets. Existing
single-broker test assertions (``status == "filled"``, ``order`` echoed
back) continue to pass.
"""

from __future__ import annotations

import uuid

from fibokei.execution.adapter import ExecutionAdapter
from fibokei.paper.account import PaperAccount


class PaperExecutionAdapter(ExecutionAdapter):
    """Execution adapter that simulates instant fills against a paper account."""

    def __init__(self, account: PaperAccount | None = None):
        self._account = account or PaperAccount()

    def place_order(self, order: dict) -> dict:
        """Simulate order placement (instant fill) and emit a synthetic deal id."""
        deal_id = f"PAPER-{uuid.uuid4().hex[:12]}"
        size = order.get("size")
        return {
            "status": "filled",
            "deal_id": deal_id,
            "order_id": deal_id,
            "order": order,
            "size": size,
            "filled_size": size,
            "filled_price": order.get("requested_price"),
            "level": order.get("requested_price"),
        }

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a paper order. Always succeeds."""
        return True

    def modify_order(self, order_id: str, changes: dict) -> dict:
        """Modify a paper order."""
        return {"status": "modified", "order_id": order_id, "changes": changes}

    def get_positions(self) -> list[dict]:
        """Return open positions from paper account."""
        return list(self._account.open_positions)

    def get_account_info(self) -> dict:
        """Return paper account status."""
        return self._account.get_status()

    def close_position(self, position_id: str) -> dict:
        """Close a paper position."""
        return {"status": "closed", "position_id": position_id, "deal_id": position_id}

    def partial_close(self, position_id: str, pct: float) -> dict:
        """Partially close a paper position."""
        return {
            "status": "partial_closed",
            "position_id": position_id,
            "pct": pct,
        }
