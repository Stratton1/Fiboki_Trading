"""Strategy Factory output families: traditional_gen1 + hybrid_gen1."""

from fibokei.strategies.traditional.gen1 import (
    GEN1_STRATEGY_CLASSES,
    TRADITIONAL_GEN1_SPECS,
    register_gen1,
)
from fibokei.strategies.traditional.hybrid_gen1 import (
    HYBRID_GEN1_SPECS,
    HYBRID_GEN1_STRATEGY_CLASSES,
    register_hybrid_gen1,
)
from fibokei.strategies.traditional.triple_hybrid_gen1 import (
    TRIPLE_HYBRID_GEN1_SPECS,
    TRIPLE_HYBRID_GEN1_STRATEGY_CLASSES,
    register_triple_hybrid_gen1,
)

__all__ = [
    "GEN1_STRATEGY_CLASSES",
    "TRADITIONAL_GEN1_SPECS",
    "register_gen1",
    "HYBRID_GEN1_SPECS",
    "HYBRID_GEN1_STRATEGY_CLASSES",
    "register_hybrid_gen1",
    "TRIPLE_HYBRID_GEN1_SPECS",
    "TRIPLE_HYBRID_GEN1_STRATEGY_CLASSES",
    "register_triple_hybrid_gen1",
]
