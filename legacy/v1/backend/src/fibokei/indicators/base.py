"""Base class for all technical indicators."""

from abc import ABC, abstractmethod

import pandas as pd


class Indicator(ABC):
    """Abstract base class for technical indicators."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this indicator."""

    @property
    def required_columns(self) -> list[str]:
        """Columns required in the input DataFrame."""
        return ["open", "high", "low", "close"]

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """Minimum bars needed before indicator values are valid."""

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute indicator and add columns to DataFrame.

        Args:
            df: OHLCV DataFrame with at least the required_columns.

        Returns:
            Same DataFrame with new indicator columns added.
        """

    def validate_input(self, df: pd.DataFrame) -> None:
        """Check that required columns exist."""
        missing = [c for c in self.required_columns if c not in df.columns]
        if missing:
            raise ValueError(f"{self.name} requires columns {missing}")
