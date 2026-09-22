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
from fibokei.strategies.bot13_chikou_session import ChikouSessionGuard
from fibokei.strategies.bot15_momentum_continuation import MomentumContinuation
from fibokei.strategies.bot16_golden_momentum import GoldenMomentumConfluence
from fibokei.strategies.bot17_gartley_harmonic import GartleyHarmonicReversal
from fibokei.strategies.bot18_fib_ma_confluence import FibMAConfluence
from fibokei.strategies.bot19_fib_bb_exhaustion import FibBBExhaustion
from fibokei.strategies.bot20_golden_pocket_divergence import GoldenPocketDivergence
from fibokei.strategies.bot21_fib_arc_breakout import FibArcBreakout
from fibokei.strategies.bot22_fib_volume_confluence import FibVolumeConfluence

# Architectural minimum number of hand-coded strategies expected to be
# registered. Used by /system/status to flag a registry that's failed to
# load adequately (e.g. import error wiped most of the registry) without
# the dashboard hardcoding a magic number against a growing registry.
EXPECTED_MIN_STRATEGIES = 12

# The 12 hand-coded blueprint strategies. Everything else registered is an
# extended/experimental strategy. Tier is declared explicitly here rather than
# inferred from filenames so the classification never drifts silently.
CANONICAL_STRATEGY_IDS = frozenset({
    "bot01_sanyaku",
    "bot02_kijun_pullback",
    "bot03_flat_senkou_b",
    "bot04_chikou_momentum",
    "bot05_mtfa_sanyaku",
    "bot06_nwave",
    "bot07_kumo_twist",
    "bot08_kihon_suchi",
    "bot09_golden_cloud",
    "bot10_kijun_fib",
    "bot11_sanyaku_fib_ext",
    "bot12_kumo_fib_tz",
})


# Strategy Factory tiers are identified by the compiled id prefix
# (``factory_{spec_id}_v{n}``) so classification never drifts from the spec.
TIER_CANONICAL = "canonical"
TIER_TRADITIONAL_GEN1 = "traditional_gen1"
TIER_HYBRID_GEN1 = "hybrid_gen1"
TIER_TRIPLE_HYBRID_GEN1 = "triple_hybrid_gen1"
TIER_EXPERIMENTAL = "experimental"

# All non-canonical tiers a strategy can fall into, in display order.
NON_CANONICAL_TIERS = (
    TIER_TRADITIONAL_GEN1,
    TIER_HYBRID_GEN1,
    TIER_TRIPLE_HYBRID_GEN1,
    TIER_EXPERIMENTAL,
)

# Single source of truth for how each tier is presented in the UI — label,
# short badge, one-line description and display order. The frontend renders
# from this (via /strategies/grouped) so tier semantics never drift between
# backend classification and UI grouping/badges.
TIER_METADATA: dict[str, dict] = {
    TIER_CANONICAL: {
        "label": "Canonical (blueprint)",
        "badge": "Core",
        "description": "The 12 hand-coded Ichimoku/Fibonacci blueprint bots.",
        "order": 0,
    },
    TIER_TRADITIONAL_GEN1: {
        "label": "Traditional (Gen-1)",
        "badge": "Research",
        "description": "Classic single-indicator factory families — research-tier.",
        "order": 1,
    },
    TIER_HYBRID_GEN1: {
        "label": "Hybrid (Gen-1)",
        "badge": "Research",
        "description": "Two-indicator combinations (primary + confirm) — research-tier.",
        "order": 2,
    },
    TIER_TRIPLE_HYBRID_GEN1: {
        "label": "Triple Hybrid (Gen-1)",
        "badge": "Research",
        "description": "Three-indicator combinations (primary + 2 confirm/filter) — research-tier.",
        "order": 3,
    },
    TIER_EXPERIMENTAL: {
        "label": "Extended / experimental",
        "badge": "Experimental",
        "description": "Extended bots and anything not yet promoted to a tier.",
        "order": 4,
    },
}

# Tiers in canonical display order (canonical first, experimental last).
TIER_ORDER = tuple(
    t for t, _ in sorted(TIER_METADATA.items(), key=lambda kv: kv[1]["order"])
)


def classify_strategy(strategy_id: str) -> str:
    """Return the tier for a strategy id.

    Tiers: 'canonical' (the 12 hand-coded blueprints), 'traditional_gen1' and
    'hybrid_gen1' (Strategy Factory specs, identified by id prefix), and
    'experimental' (everything else, e.g. the extended bot13/15-22 set).
    """
    if strategy_id in CANONICAL_STRATEGY_IDS:
        return TIER_CANONICAL
    if strategy_id.startswith("factory_trad_"):
        return TIER_TRADITIONAL_GEN1
    if strategy_id.startswith("factory_tri_"):
        return TIER_TRIPLE_HYBRID_GEN1
    if strategy_id.startswith("factory_hyb_"):
        return TIER_HYBRID_GEN1
    return TIER_EXPERIMENTAL


class StrategyRegistry:
    """Registry for strategy classes."""

    def __init__(self):
        self._strategies: dict[str, type[Strategy]] = {}

    def register(self, strategy_class: type[Strategy]) -> None:
        """Register a strategy class by its strategy_id."""
        instance = strategy_class()
        self._strategies[instance.strategy_id] = strategy_class

    @property
    def loaded_count(self) -> int:
        """Total number of registered strategy classes.

        This is the canonical "what's loaded in the running process" count,
        independent of the FIBOKEI_VISIBLE_STRATEGIES operator-visibility
        filter applied by :meth:`list_available`. Use this for system-status
        observability so the dashboard never reports a misleadingly-low
        number when the visibility filter is set.
        """
        return len(self._strategies)

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
                "tier": classify_strategy(inst.strategy_id),
            })
        return result

    def list_grouped(self) -> list[dict]:
        """Strategies grouped by tier in display order, for grouped UI.

        Returns one entry per non-empty tier (canonical first), each with its
        display metadata and the strategies it contains. Honours the same
        FIBOKEI_VISIBLE_STRATEGIES filter as :meth:`list_available`.
        """
        items = self.list_available()
        groups: dict[str, list[dict]] = {}
        for item in items:
            groups.setdefault(item["tier"], []).append(item)

        ordered: list[dict] = []
        for tier in TIER_ORDER:
            members = groups.get(tier, [])
            if not members:
                continue
            meta = TIER_METADATA[tier]
            ordered.append({
                "tier": tier,
                "label": meta["label"],
                "badge": meta["badge"],
                "description": meta["description"],
                "count": len(members),
                "strategies": members,
            })
        return ordered

    def registry_health(self) -> dict:
        """Operator-facing truth about the strategy registry.

        Compares strategies registered in-process against the strategy files
        on disk so the count can never drift silently again. ``unregistered_files``
        lists ``botNN`` prefixes present on disk but missing from the registry.
        """
        import os as _os

        registered_ids = sorted(self._strategies.keys())
        registered_prefixes = {sid.split("_", 1)[0] for sid in registered_ids}

        strat_dir = _os.path.dirname(__file__)
        file_prefixes: set[str] = set()
        try:
            for fn in _os.listdir(strat_dir):
                if fn.startswith("bot") and fn.endswith(".py"):
                    file_prefixes.add(fn.split("_", 1)[0])
        except OSError:
            file_prefixes = set()

        by_tier: dict[str, list[str]] = {TIER_CANONICAL: [], TIER_EXPERIMENTAL: []}
        for tier in NON_CANONICAL_TIERS:
            by_tier.setdefault(tier, [])
        for sid in registered_ids:
            by_tier.setdefault(classify_strategy(sid), []).append(sid)

        canonical = by_tier[TIER_CANONICAL]
        experimental = by_tier[TIER_EXPERIMENTAL]
        unregistered = sorted(file_prefixes - registered_prefixes)

        return {
            "registered_count": len(registered_ids),
            "file_count": len(file_prefixes),
            "canonical_count": len(canonical),
            "experimental_count": len(experimental),
            "traditional_gen1_count": len(by_tier[TIER_TRADITIONAL_GEN1]),
            "hybrid_gen1_count": len(by_tier[TIER_HYBRID_GEN1]),
            "tier_counts": {t: len(ids) for t, ids in by_tier.items()},
            "expected_min": EXPECTED_MIN_STRATEGIES,
            "by_tier": by_tier,
            "unregistered_files": unregistered,
            "healthy": (
                len(registered_ids) >= EXPECTED_MIN_STRATEGIES
                and not unregistered
            ),
        }


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
strategy_registry.register(ChikouSessionGuard)
strategy_registry.register(MomentumContinuation)
strategy_registry.register(GoldenMomentumConfluence)
strategy_registry.register(GartleyHarmonicReversal)
strategy_registry.register(FibMAConfluence)
strategy_registry.register(FibBBExhaustion)
strategy_registry.register(GoldenPocketDivergence)
strategy_registry.register(FibArcBreakout)
strategy_registry.register(FibVolumeConfluence)

# Strategy Factory — traditional Gen-1 families (25 declarative specs compiled
# into the common Strategy interface). Research-tier: registered + backtestable,
# never auto-promoted. Imported here (not at top) to avoid a circular import
# (traditional.gen1 imports the factory compiler, which imports strategies.base).
from fibokei.strategies.traditional import (  # noqa: E402
    register_gen1,
    register_hybrid_gen1,
    register_triple_hybrid_gen1,
)

register_gen1(strategy_registry)
register_hybrid_gen1(strategy_registry)
register_triple_hybrid_gen1(strategy_registry)
