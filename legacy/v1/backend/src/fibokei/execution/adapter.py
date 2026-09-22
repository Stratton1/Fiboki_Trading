"""Abstract execution adapter interface."""

from abc import ABC, abstractmethod


class ExecutionAdapter(ABC):
    """Base class for all execution adapters."""

    @abstractmethod
    def place_order(self, order: dict) -> dict:
        """Place an order and return execution result."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled."""
        ...

    @abstractmethod
    def modify_order(self, order_id: str, changes: dict) -> dict:
        """Modify an existing order."""
        ...

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return all open positions."""
        ...

    @abstractmethod
    def get_account_info(self) -> dict:
        """Return account information."""
        ...

    @abstractmethod
    def close_position(self, position_id: str) -> dict:
        """Close an open position entirely."""
        ...

    @abstractmethod
    def partial_close(self, position_id: str, pct: float) -> dict:
        """Partially close a position by percentage."""
        ...
