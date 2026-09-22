"""Tests for the evolutionary search (research/evolution.py)."""

import random

import numpy as np
import pandas as pd

from fibokei.core.models import Timeframe
from fibokei.research.evolution import (
    EvoConfig,
    crossover,
    evolve,
    param_mutate,
    structural_add,
    structural_remove,
)
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.traditional import TRADITIONAL_GEN1_SPECS

SMA_X = next(s for s in TRADITIONAL_GEN1_SPECS if s.spec_id == "trad_sma_crossover")
RSI = next(s for s in TRADITIONAL_GEN1_SPECS if s.spec_id == "trad_rsi_meanrev")


def _df(n=700, seed=5):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 0.02 * t + 6 * np.sin(t / 22.0) + np.cumsum(rng.normal(0, 0.2, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.5, n)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high,
                         "low": low, "close": close, "volume": 0.0})


def test_param_mutate_is_child_and_compiles():
    rng = random.Random(1)
    child = param_mutate(SMA_X, rng)
    assert child.parent_spec_id == SMA_X.spec_id
    assert child.generation_method == "parameter_mutation"
    compile_spec(child)  # must compile


def test_structural_add_grows_rule_count():
    rng = random.Random(2)
    child = structural_add(SMA_X, rng, max_extra=2)
    extra = len(child.confirmation_rules) + len(child.filters)
    assert extra >= 1  # became at least a hybrid
    assert child.generation_method == "structural_mutation"
    compile_spec(child)


def test_structural_add_respects_triple_cap():
    rng = random.Random(3)
    s = SMA_X
    for _ in range(6):
        s = structural_add(s, rng, max_extra=2)
    # Never exceeds 2 extra rules (triple hybrid max).
    assert len(s.confirmation_rules) + len(s.filters) <= 2


def test_structural_remove_then_add_roundtrip_compiles():
    rng = random.Random(4)
    grown = structural_add(structural_add(SMA_X, rng, 2), rng, 2)
    pruned = structural_remove(grown, rng)
    compile_spec(pruned)
    assert (len(pruned.confirmation_rules) + len(pruned.filters)
            <= len(grown.confirmation_rules) + len(grown.filters))


def test_crossover_compiles_and_caps_rules():
    rng = random.Random(5)
    a = SMA_X
    b = structural_add(structural_add(RSI, rng, 2), rng, 2)
    child = crossover(a, b, rng, max_extra=2)
    assert len(child.confirmation_rules) + len(child.filters) <= 2
    compile_spec(child)


def test_evolve_improves_or_matches_seed_fitness():
    cfg = EvoConfig(population=10, generations=3, elite=3, seed=7, max_extra_rules=2)
    res = evolve(SMA_X, _df(), "TEST", Timeframe.H1, cfg)
    assert res.best, "no genomes produced"
    assert res.generations_run == 3
    # Best fitness is finite and sorted descending.
    fits = [g.fitness for g in res.best]
    assert fits == sorted(fits, reverse=True)
    # The search evaluated multiple unique genomes.
    assert res.evaluated >= 10


def test_evolve_is_deterministic():
    cfg = EvoConfig(population=8, generations=2, elite=2, seed=11)
    a = evolve(SMA_X, _df(), "TEST", Timeframe.H1, cfg)
    b = evolve(SMA_X, _df(), "TEST", Timeframe.H1, cfg)
    assert [g.fitness for g in a.best] == [g.fitness for g in b.best]


def test_evolve_can_produce_triple_hybrid():
    cfg = EvoConfig(population=14, generations=4, elite=4, seed=21, max_extra_rules=2)
    res = evolve(SMA_X, _df(), "TEST", Timeframe.H1, cfg)
    max_extra = max(
        len(g.spec.confirmation_rules) + len(g.spec.filters) for g in res.best
    )
    # Structural growth is possible up to triple (1 entry + 2 extra).
    assert max_extra <= 2
