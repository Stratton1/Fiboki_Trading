"""Tests for the triple-hybrid Gen-1 family."""

import numpy as np
import pandas as pd

from fibokei.core.models import Timeframe
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.registry import classify_strategy, strategy_registry
from fibokei.strategies.traditional import (
    TRIPLE_HYBRID_GEN1_SPECS,
    TRIPLE_HYBRID_GEN1_STRATEGY_CLASSES,
)


def _df(n=400, seed=9):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 0.02 * t + 5 * np.sin(t / 18.0) + np.cumsum(rng.normal(0, 0.15, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.5, n)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high,
                         "low": low, "close": close, "volume": 0.0})


def test_each_is_three_indicators():
    assert len(TRIPLE_HYBRID_GEN1_SPECS) == len(TRIPLE_HYBRID_GEN1_STRATEGY_CLASSES)
    assert TRIPLE_HYBRID_GEN1_SPECS
    for s in TRIPLE_HYBRID_GEN1_SPECS:
        extra = len(s.confirmation_rules) + len(s.filters)
        assert extra == 2, f"{s.spec_id} must have exactly 2 confirm/filter rules"
        assert s.entry_rules
        assert s.family == "triple_hybrid_gen1"
        assert s.spec_id.startswith("tri_")


def test_registered_as_triple_hybrid_tier():
    listed = {i["id"]: i for i in strategy_registry.list_available()}
    for s in TRIPLE_HYBRID_GEN1_SPECS:
        sid = f"factory_{s.spec_id}_v{s.version}"
        assert sid in listed, f"{sid} not registered"
        assert listed[sid]["tier"] == "triple_hybrid_gen1"
        assert classify_strategy(sid) == "triple_hybrid_gen1"


def test_health_counts_include_triple():
    h = strategy_registry.registry_health()
    assert h["tier_counts"]["triple_hybrid_gen1"] == len(TRIPLE_HYBRID_GEN1_SPECS)
    assert h["registered_count"] == sum(h["tier_counts"].values())


def test_triples_compile_and_signal_smoke():
    df = _df()
    for s in TRIPLE_HYBRID_GEN1_SPECS:
        strat = compile_spec(s)
        d = strat.compute_indicators(df.copy())
        ctx = {"instrument": "TEST", "timeframe": Timeframe.H1}
        # Must not raise across the warmed-up range.
        for idx in range(200, len(d)):
            sig = strat.generate_signal(d, idx, ctx)
            if sig is not None:
                assert sig.stop_loss != sig.proposed_entry
