"""Abstract base class for all trading strategies."""

from abc import ABC, abstractmethod

import pandas as pd

from fibokei.core.models import Timeframe
from fibokei.core.signals import Signal
from fibokei.core.trades import ExitReason, TradePlan


class Strategy(ABC):
    """Base class that all 12 FIBOKEI bots must implement."""

    # --- Identity (override in subclass) ---

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique identifier, e.g. 'bot01_sanyaku'."""

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Human-readable name."""

    @property
    @abstractmethod
    def strategy_family(self) -> str:
        """Family grouping: 'ichimoku', 'fibonacci', or 'hybrid'."""

    @property
    def description(self) -> str:
        return ""

    @property
    def logic_summary(self) -> str:
        return ""

    @property
    def valid_market_regimes(self) -> list[str]:
        return []

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return list(Timeframe)

    @property
    def supports_long(self) -> bool:
        return True

    @property
    def supports_short(self) -> bool:
        return True

    @property
    def requires_mtfa(self) -> bool:
        return False

    @property
    def requires_fibonacci(self) -> bool:
        return False

    @property
    def complexity_level(self) -> str:
        return "standard"

    @property
    def config(self) -> dict:
        """Strategy-specific configuration with defaults."""
        return {}

    # --- Abstract methods ---

    @abstractmethod
    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Any pre-processing before indicator computation."""

    @abstractmethod
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all required indicators on the DataFrame."""

    @abstractmethod
    def detect_market_regime(self, df: pd.DataFrame, idx: int) -> str:
        """Classify market regime at bar idx."""

    @abstractmethod
    def detect_setup(self, df: pd.DataFrame, idx: int, context: dict) -> bool:
        """Check if a valid setup exists at bar idx."""

    @abstractmethod
    def generate_signal(
        self, df: pd.DataFrame, idx: int, context: dict
    ) -> Signal | None:
        """Generate a Signal if setup is valid, else None."""

    @abstractmethod
    def validate_signal(self, signal: Signal, context: dict) -> Signal:
        """Validate and possibly invalidate a signal."""

    @abstractmethod
    def build_trade_plan(self, signal: Signal, context: dict) -> TradePlan:
        """Build a TradePlan from a validated signal."""

    @abstractmethod
    def manage_position(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> dict:
        """Update position management logic (trailing stops, etc.)."""

    @abstractmethod
    def generate_exit(
        self, position: dict, df: pd.DataFrame, idx: int, context: dict
    ) -> ExitReason | None:
        """Check if current position should be exited."""

    @abstractmethod
    def score_confidence(self, signal: Signal, context: dict) -> float:
        """Score the signal confidence from 0.0 to 1.0."""

    @abstractmethod
    def explain_decision(self, context: dict) -> str:
        """Human-readable explanation of the decision."""

    # --- Concrete helpers ---

    def get_required_indicators(self) -> list[str]:
        """Return names of indicators this strategy needs."""
        return []

    def run_preparation(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run prepare_data then compute_indicators."""
        df = self.prepare_data(df)
        df = self.compute_indicators(df)
        return df
