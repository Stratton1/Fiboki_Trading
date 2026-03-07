"""Tests for execution adapter abstraction."""

import pytest

from fibokei.execution.adapter import ExecutionAdapter
from fibokei.execution.paper_adapter import PaperExecutionAdapter
from fibokei.execution.ig_adapter import IGExecutionAdapter
from fibokei.core.feature_flags import FeatureFlags, get_execution_adapter


def test_cannot_instantiate_base_adapter():
    """ExecutionAdapter is abstract and cannot be instantiated."""
    with pytest.raises(TypeError):
        ExecutionAdapter()


def test_paper_adapter_implements_interface():
    """PaperExecutionAdapter is a valid ExecutionAdapter."""
    adapter = PaperExecutionAdapter()
    assert isinstance(adapter, ExecutionAdapter)


def test_paper_adapter_get_account_info():
    """Paper adapter account info contains balance and equity."""
    adapter = PaperExecutionAdapter()
    info = adapter.get_account_info()
    assert "balance" in info
    assert "equity" in info


def test_paper_adapter_get_positions_empty():
    """Paper adapter returns empty positions list by default."""
    adapter = PaperExecutionAdapter()
    assert adapter.get_positions() == []


def test_paper_adapter_place_order():
    """Paper adapter returns filled status on place_order."""
    adapter = PaperExecutionAdapter()
    order = {"instrument": "EURUSD", "direction": "buy", "size": 1.0}
    result = adapter.place_order(order)
    assert result["status"] == "filled"
    assert result["order"] == order


def test_ig_adapter_raises_not_implemented():
    """IG adapter raises NotImplementedError with V1 message."""
    adapter = IGExecutionAdapter()
    with pytest.raises(NotImplementedError, match="not enabled in V1"):
        adapter.place_order({})


def test_feature_flags_default_paper():
    """Default feature flags disable live execution."""
    flags = FeatureFlags()
    assert flags.live_execution_enabled is False


def test_feature_flags_default_ig_paper_mode():
    """Default feature flags enable IG paper mode."""
    flags = FeatureFlags()
    assert flags.ig_paper_mode is True


def test_get_execution_adapter_returns_paper():
    """Default adapter factory returns PaperExecutionAdapter."""
    adapter = get_execution_adapter()
    assert isinstance(adapter, PaperExecutionAdapter)
