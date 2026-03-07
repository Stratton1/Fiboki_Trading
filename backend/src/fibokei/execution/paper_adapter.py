"""Paper trading execution adapter."""

from fibokei.execution.adapter import ExecutionAdapter
from fibokei.paper.account import PaperAccount


class PaperExecutionAdapter(ExecutionAdapter):
    """Execution adapter that routes orders through the paper trading engine."""

    def __init__(self, account: PaperAccount | None = None):
        self._account = account or PaperAccount()

    def place_order(self, order: dict) -> dict:
        """Simulate order placement (instant fill)."""
        return {"status": "filled", "order": order}

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
        return {"status": "closed", "position_id": position_id}

    def partial_close(self, position_id: str, pct: float) -> dict:
        """Partially close a paper position."""
        return {"status": "partial_closed", "position_id": position_id, "pct": pct}
