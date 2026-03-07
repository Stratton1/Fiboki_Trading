"""Indicator registry for centralized indicator management."""

from fibokei.indicators.atr import ATR
from fibokei.indicators.base import Indicator
from fibokei.indicators.candles import CandlestickPatterns
from fibokei.indicators.fibonacci import (
    FibonacciExtension,
    FibonacciRetracement,
    FibonacciTimeZones,
)
from fibokei.indicators.ichimoku import IchimokuCloud
from fibokei.indicators.regime import MarketRegime
from fibokei.indicators.swing import SwingDetector
from fibokei.indicators.volatility import RollingVolatility


class IndicatorRegistry:
    """Registry for indicator classes."""

    def __init__(self):
        self._indicators: dict[str, type[Indicator]] = {}

    def register(self, indicator_class: type[Indicator]) -> None:
        """Register an indicator class by its name."""
        instance = indicator_class()
        self._indicators[instance.name] = indicator_class

    def get(self, name: str, **kwargs) -> Indicator:
        """Get an indicator instance by name."""
        if name not in self._indicators:
            raise KeyError(f"Unknown indicator: {name}")
        return self._indicators[name](**kwargs)

    def list_available(self) -> list[str]:
        """List all registered indicator names."""
        return sorted(self._indicators.keys())


# Global registry with pre-registered indicators
registry = IndicatorRegistry()
registry.register(IchimokuCloud)
registry.register(ATR)
registry.register(SwingDetector)
registry.register(CandlestickPatterns)
registry.register(MarketRegime)
registry.register(FibonacciRetracement)
registry.register(FibonacciExtension)
registry.register(FibonacciTimeZones)
registry.register(RollingVolatility)
