"""Strategy Factory (Phase 20): typed strategy definitions compiled into
the common Strategy interface.

- ``spec``        — versioned, serialisable StrategySpec models
- ``primitives``  — composable rule blocks evaluated on closed candles
- ``compiler``    — StrategySpec → Strategy instance (deterministic)
"""

from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.factory.primitives import PRIMITIVES, primitive_names
from fibokei.strategies.factory.spec import (
    RuleSpec,
    StopSpec,
    StrategySpec,
    TargetSpec,
    TrailingSpec,
)

__all__ = [
    "PRIMITIVES",
    "RuleSpec",
    "StopSpec",
    "StrategySpec",
    "TargetSpec",
    "TrailingSpec",
    "compile_spec",
    "primitive_names",
]
