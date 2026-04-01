"""Strategy registry for centralized strategy management."""

import os

from fibokei.strategies.base import Strategy
from fibokei.strategies.bot01_sanyaku import PureSanyakuConfluence
from fibokei.strategies.bot02_kijun_pullback import KijunPullback
from fibokei.strategies.bot03_flat_senkou_b import FlatSenkouBBounce
from fibokei.strategies.bot04_chikou_momentum import ChikouMomentum
from fibokei.strategies.bot05_mtfa_sanyaku import MTFASanyaku
from fibokei.strategies.bot06_nwave import NWaveStructural
from fibokei.strategies.bot07_kumo_twist import KumoTwistAnticipator
from fibokei.strategies.bot08_kihon_suchi import KihonSuchiCycle
from fibokei.strategies.bot09_golden_cloud import GoldenCloudConfluence
from fibokei.strategies.bot10_kijun_fib import KijunFibContinuation
from fibokei.strategies.bot11_sanyaku_fib_ext import SanyakuFibExtension
from fibokei.strategies.bot12_kumo_fib_tz import KumoFibTimeZone
from fibokei.strategies.bot13_golden_zone import GoldenZonePullback
from fibokei.strategies.bot14_fractal_golden_pocket import FractalGoldenPocketScalper


class StrategyRegistry:
    """Registry for strategy classes."""

    def __init__(self):
        self._strategies: dict[str, type[Strategy]] = {}

    def register(self, strategy_class: type[Strategy]) -> None:
        """Register a strategy class by its strategy_id."""
        instance = strategy_class()
        self._strategies[instance.strategy_id] = strategy_class

    def get(self, strategy_id: str, **kwargs) -> Strategy:
        """Get a strategy instance by ID."""
        if strategy_id not in self._strategies:
            raise KeyError(f"Unknown strategy: {strategy_id}")
        return self._strategies[strategy_id](**kwargs)

    def list_available(self) -> list[dict]:
        """List registered strategies, filtered by FIBOKEI_VISIBLE_STRATEGIES if set."""
        visible = os.environ.get("FIBOKEI_VISIBLE_STRATEGIES", "")
        visible_ids = {s.strip() for s in visible.split(",") if s.strip()} if visible else None

        result = []
        for sid, cls in sorted(self._strategies.items()):
            if visible_ids and sid not in visible_ids:
                continue
            inst = cls()
            result.append({
                "id": inst.strategy_id,
                "name": inst.strategy_name,
                "family": inst.strategy_family,
                "complexity": inst.complexity_level,
            })
        return result


# Global registry — strategies register themselves on import
strategy_registry = StrategyRegistry()
strategy_registry.register(PureSanyakuConfluence)
strategy_registry.register(KijunPullback)
strategy_registry.register(FlatSenkouBBounce)
strategy_registry.register(ChikouMomentum)
strategy_registry.register(MTFASanyaku)
strategy_registry.register(NWaveStructural)
strategy_registry.register(KumoTwistAnticipator)
strategy_registry.register(KihonSuchiCycle)
strategy_registry.register(GoldenCloudConfluence)
strategy_registry.register(KijunFibContinuation)
strategy_registry.register(SanyakuFibExtension)
strategy_registry.register(KumoFibTimeZone)
strategy_registry.register(GoldenZonePullback)
strategy_registry.register(FractalGoldenPocketScalper)
