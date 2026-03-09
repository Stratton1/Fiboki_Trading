"""Tests for reconciliation between Fiboki and IG broker state."""

from unittest.mock import MagicMock

import pytest

from fibokei.execution.ig_adapter import IGExecutionAdapter
from fibokei.execution.ig_client import IGClient, IGSession
from fibokei.execution.reconciliation import reconcile_positions, PositionMismatch
import time


def _mock_adapter(broker_positions: list[dict]) -> IGExecutionAdapter:
    """Create an adapter with mocked broker positions."""
    client = MagicMock(spec=IGClient)
    client.authenticate.return_value = IGSession(
        cst="test", x_security_token="test",
        account_id="TEST", created_at=time.time(),
    )
    # The adapter calls client.get_positions() and maps results
    # For reconciliation, we mock the adapter's get_positions directly
    adapter = IGExecutionAdapter(client=client)
    adapter.get_positions = MagicMock(return_value=broker_positions)
    return adapter


class TestReconciliation:
    def test_clean_reconciliation(self):
        """All positions match."""
        fiboki = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
            {"deal_id": "D2", "instrument": "GBPUSD", "direction": "SELL", "size": 2.0},
        ]
        broker = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
            {"deal_id": "D2", "instrument": "GBPUSD", "direction": "SELL", "size": 2.0},
        ]
        adapter = _mock_adapter(broker)
        result = reconcile_positions(fiboki, adapter)
        assert result.is_clean
        assert result.matched == 2
        assert result.fiboki_position_count == 2
        assert result.broker_position_count == 2

    def test_missing_at_broker(self):
        """Fiboki tracks a position that broker doesn't have."""
        fiboki = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
        ]
        broker = []
        adapter = _mock_adapter(broker)
        result = reconcile_positions(fiboki, adapter)
        assert not result.is_clean
        assert len(result.mismatches) == 1
        assert result.mismatches[0].type == "missing_at_broker"
        assert result.mismatches[0].fiboki_deal_id == "D1"

    def test_missing_in_fiboki(self):
        """Broker has a position Fiboki doesn't track."""
        fiboki = []
        broker = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
        ]
        adapter = _mock_adapter(broker)
        result = reconcile_positions(fiboki, adapter)
        assert not result.is_clean
        assert len(result.mismatches) == 1
        assert result.mismatches[0].type == "missing_in_fiboki"
        assert result.mismatches[0].broker_deal_id == "D1"

    def test_direction_mismatch(self):
        """Same deal ID but different directions."""
        fiboki = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
        ]
        broker = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "SELL", "size": 1.0},
        ]
        adapter = _mock_adapter(broker)
        result = reconcile_positions(fiboki, adapter)
        assert not result.is_clean
        assert result.mismatches[0].type == "direction_mismatch"

    def test_size_mismatch(self):
        """Same deal ID but different sizes."""
        fiboki = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
        ]
        broker = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 2.0},
        ]
        adapter = _mock_adapter(broker)
        result = reconcile_positions(fiboki, adapter)
        assert not result.is_clean
        assert result.mismatches[0].type == "size_mismatch"

    def test_empty_both_sides(self):
        """No positions on either side = clean."""
        adapter = _mock_adapter([])
        result = reconcile_positions([], adapter)
        assert result.is_clean
        assert result.matched == 0

    def test_multiple_mismatches(self):
        """Multiple issues detected in one reconciliation."""
        fiboki = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
            {"deal_id": "D2", "instrument": "GBPUSD", "direction": "SELL", "size": 3.0},
        ]
        broker = [
            {"deal_id": "D1", "instrument": "EURUSD", "direction": "BUY", "size": 1.0},
            {"deal_id": "D3", "instrument": "USDJPY", "direction": "BUY", "size": 1.0},
        ]
        adapter = _mock_adapter(broker)
        result = reconcile_positions(fiboki, adapter)
        assert not result.is_clean
        assert result.matched == 1
        assert len(result.mismatches) == 2
        types = {m.type for m in result.mismatches}
        assert "missing_at_broker" in types  # D2 not at broker
        assert "missing_in_fiboki" in types  # D3 not in Fiboki
