"""IG broker execution adapter stub."""

from fibokei.execution.adapter import ExecutionAdapter


class IGExecutionAdapter(ExecutionAdapter):
    """IG broker adapter — disabled in V1."""

    def place_order(self, order: dict) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("IG live trading not enabled in V1")

    def modify_order(self, order_id: str, changes: dict) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def get_positions(self) -> list[dict]:
        raise NotImplementedError("IG live trading not enabled in V1")

    def get_account_info(self) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def close_position(self, position_id: str) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")

    def partial_close(self, position_id: str, pct: float) -> dict:
        raise NotImplementedError("IG live trading not enabled in V1")
