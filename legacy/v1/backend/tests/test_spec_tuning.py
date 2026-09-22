"""Tests for factory spec tuning (research/spec_tuning.py)."""

import numpy as np
import pandas as pd

from fibokei.core.models import Timeframe
from fibokei.research.spec_tuning import mutate_spec, tune_spec
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.traditional import TRADITIONAL_GEN1_SPECS

EMA_X = next(s for s in TRADITIONAL_GEN1_SPECS if s.spec_id == "trad_ema_crossover")
RSI_MR = next(s for s in TRADITIONAL_GEN1_SPECS if s.spec_id == "trad_rsi_meanrev")


def _df(n=600, seed=3):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 0.02 * t + 6 * np.sin(t / 20.0) + np.cumsum(rng.normal(0, 0.2, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.5, n)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high,
                         "low": low, "close": close, "volume": 0.0})


def test_mutate_overrides_params_and_is_child():
    child = mutate_spec(EMA_X, entry_overrides={"fast": 8, "slow": 34},
                        stop_multiple=3.0, target_multiple=1.5, label="t1")
    assert child.entry_rules[0].params["fast"] == 8
    assert child.entry_rules[0].params["slow"] == 34
    assert child.stop.multiple == 3.0
    assert child.target.multiple == 1.5
    assert child.parent_spec_id == EMA_X.spec_id
    assert child.generation_method == "parameter_mutation"
    # Distinct identity from the baseline.
    assert child.spec_id != EMA_X.spec_id
    assert child.content_hash != EMA_X.content_hash


def test_mutated_spec_compiles_and_builds_right_indicator():
    child = mutate_spec(EMA_X, entry_overrides={"fast": 8, "slow": 34}, label="x")
    strat = compile_spec(child)
    df = strat.compute_indicators(_df())
    # Changing EMA fast/slow params builds the matching indicator columns.
    assert "ema_8" in df.columns and "ema_34" in df.columns


def test_baseline_unchanged_by_mutation():
    before = EMA_X.content_hash
    mutate_spec(EMA_X, entry_overrides={"fast": 99}, label="z")
    assert EMA_X.content_hash == before  # original spec is not mutated in place


def test_tune_returns_sorted_with_baseline():
    results = tune_spec(EMA_X, _df(), "TEST", Timeframe.H1,
                        stop_multiples=[2.0, 3.0], target_multiples=[2.0],
                        max_variants=20)
    assert len(results) > 1
    # Sorted by composite score descending.
    scores = [r.composite_score for r in results]
    assert scores == sorted(scores, reverse=True)
    # Exactly one baseline, with the spec's own params.
    baselines = [r for r in results if r.is_baseline]
    assert len(baselines) == 1
    assert baselines[0].entry_params == dict(EMA_X.entry_rules[0].params)


def test_tune_rsi_grid_varies_thresholds():
    results = tune_spec(RSI_MR, _df(), "TEST", Timeframe.H1,
                        stop_multiples=[2.0], target_multiples=[1.5],
                        max_variants=30)
    variant_params = [r.entry_params for r in results if not r.is_baseline]
    assert any("oversold" in p for p in variant_params)


def test_tune_is_deterministic():
    a = tune_spec(EMA_X, _df(), "TEST", Timeframe.H1,
                  stop_multiples=[2.0, 3.0], target_multiples=[2.0], max_variants=15)
    b = tune_spec(EMA_X, _df(), "TEST", Timeframe.H1,
                  stop_multiples=[2.0, 3.0], target_multiples=[2.0], max_variants=15)
    assert [r.label for r in a] == [r.label for r in b]
    assert [r.composite_score for r in a] == [r.composite_score for r in b]
