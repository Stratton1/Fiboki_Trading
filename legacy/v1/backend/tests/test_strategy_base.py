"""Tests for Strategy ABC and StrategyRegistry."""

import pandas as pd
import pytest

from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan
from fibokei.strategies.base import Strategy
from fibokei.strategies.registry import StrategyRegistry


class DummyStrategy(Strategy):
    """Minimal concrete strategy for testing."""

    @property
    def strategy_id(self) -> str:
        return "dummy_test"

    @property
    def strategy_name(self) -> str:
        return "Dummy Test Strategy"

    @property
    def strategy_family(self) -> str:
        return "test"

    def get_required_indicators(self) -> list[str]:
        return ["ichimoku_cloud", "atr"]

    def prepare_data(self, df):
        return df

    def compute_indicators(self, df):
        return df

    def detect_market_regime(self, df, idx):
        return "unknown"

    def detect_setup(self, df, idx, context):
        return False

    def generate_signal(self, df, idx, context):
        return None

    def validate_signal(self, signal, context):
        return signal

    def build_trade_plan(self, signal, context):
        return TradePlan(
            entry_price=1.10,
            stop_loss=1.09,
            take_profit_targets=[1.12],
        )

    def manage_position(self, position, df, idx, context):
        return position

    def generate_exit(self, position, df, idx, context):
        return None

    def score_confidence(self, signal, context):
        return 0.5

    def explain_decision(self, context):
        return "Test decision"


class TestStrategyABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Strategy()

    def test_concrete_subclass_instantiates(self):
        s = DummyStrategy()
        assert s.strategy_id == "dummy_test"
        assert s.strategy_name == "Dummy Test Strategy"
        assert s.strategy_family == "test"

    def test_identity_defaults(self):
        s = DummyStrategy()
        assert s.supports_long is True
        assert s.supports_short is True
        assert s.requires_mtfa is False
        assert s.requires_fibonacci is False
        assert s.complexity_level == "standard"

    def test_get_required_indicators(self):
        s = DummyStrategy()
        assert s.get_required_indicators() == ["ichimoku_cloud", "atr"]

    def test_run_preparation(self):
        s = DummyStrategy()
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        result = s.run_preparation(df)
        assert len(result) == 3


class TestStrategyRegistry:
    def test_register_and_get(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy)
        s = reg.get("dummy_test")
        assert isinstance(s, DummyStrategy)

    def test_get_unknown_raises(self):
        reg = StrategyRegistry()
        with pytest.raises(KeyError, match="Unknown strategy"):
            reg.get("nonexistent")

    def test_list_available(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy)
        available = reg.list_available()
        assert len(available) == 1
        assert available[0]["id"] == "dummy_test"
        assert available[0]["name"] == "Dummy Test Strategy"
        assert available[0]["family"] == "test"
        assert available[0]["complexity"] == "standard"
