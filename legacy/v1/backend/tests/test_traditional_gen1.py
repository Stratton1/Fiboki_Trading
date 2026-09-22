"""Phase 4 — traditional Gen-1 strategy family.

Locks the 25 declarative families: they register as ``traditional_gen1``,
compile deterministically, evaluate only on closed candles (no look-ahead),
and produce valid signals on synthetic data without indicator KeyErrors.
Pure/offline — no network, no DB.
"""

import numpy as np
import pandas as pd
import pytest

from fibokei.core.models import Timeframe
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.registry import classify_strategy, strategy_registry
from fibokei.strategies.traditional import (
    GEN1_STRATEGY_CLASSES,
    TRADITIONAL_GEN1_SPECS,
)


def _synthetic_ohlcv(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """Deterministic trending+mean-reverting OHLCV with non-zero volume."""
    rng = np.random.default_rng(seed)
    # Drift + sine cycle + noise so every family has something to react to.
    t = np.arange(n)
    base = 100 + 0.02 * t + 5 * np.sin(t / 18.0) + np.cumsum(rng.normal(0, 0.15, n))
    close = base
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.5, n)
    vol = rng.uniform(800, 1200, n)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"timestamp": ts, "open": open_, "high": high, "low": low,
         "close": close, "volume": vol}
    )


# ── Spec hygiene ─────────────────────────────────────────────────────

def test_twenty_five_families():
    assert len(TRADITIONAL_GEN1_SPECS) == 25
    assert len(GEN1_STRATEGY_CLASSES) == 25


def test_spec_ids_unique_and_prefixed():
    ids = [s.spec_id for s in TRADITIONAL_GEN1_SPECS]
    assert len(set(ids)) == 25
    assert all(i.startswith("trad_") for i in ids)
    assert all(s.family == "traditional_gen1" for s in TRADITIONAL_GEN1_SPECS)


def test_every_spec_has_mandatory_stop():
    # Stops are non-negotiable: a positive stop multiple on every family.
    assert all(s.stop.multiple > 0 for s in TRADITIONAL_GEN1_SPECS)
    assert all(s.entry_rules for s in TRADITIONAL_GEN1_SPECS)


# ── Registry integration ─────────────────────────────────────────────

def test_all_registered_as_traditional_gen1():
    listed = {i["id"]: i for i in strategy_registry.list_available()}
    for spec in TRADITIONAL_GEN1_SPECS:
        sid = f"factory_{spec.spec_id}_v{spec.version}"
        assert sid in listed, f"{sid} not registered"
        assert listed[sid]["tier"] == "traditional_gen1"
        assert classify_strategy(sid) == "traditional_gen1"


def test_registry_health_reports_gen1():
    h = strategy_registry.registry_health()
    assert h["traditional_gen1_count"] == 25
    assert h["registered_count"] == sum(h["tier_counts"].values())


# ── Compile + deterministic content hash ─────────────────────────────

def test_specs_compile_and_hash_is_stable():
    for spec in TRADITIONAL_GEN1_SPECS:
        strat = compile_spec(spec)
        assert strat.strategy_id == f"factory_{spec.spec_id}_v{spec.version}"
        # Identical spec -> identical content hash (the determinism gate).
        assert compile_spec(spec).spec.content_hash == spec.content_hash


# ── Backtest smoke: signals generate without indicator KeyErrors ──────

@pytest.mark.parametrize("spec", TRADITIONAL_GEN1_SPECS,
                         ids=[s.spec_id for s in TRADITIONAL_GEN1_SPECS])
def test_signal_generation_smoke(spec):
    df = _synthetic_ohlcv()
    strat = compile_spec(spec)
    df = strat.compute_indicators(df)
    ctx = {"instrument": "TEST", "timeframe": Timeframe.H1}
    # Iterate the back half (indicators warmed up) — must not raise.
    fired = 0
    for idx in range(200, len(df)):
        sig = strat.generate_signal(df, idx, ctx)
        if sig is not None:
            assert sig.stop_loss != sig.proposed_entry
            assert sig.take_profit_primary is not None
            fired += 1
    # Determinism: a second pass yields the same count.
    fired2 = sum(
        strat.generate_signal(df, idx, ctx) is not None
        for idx in range(200, len(df))
    )
    assert fired == fired2


def test_at_least_some_families_fire():
    """The family set as a whole must produce signals on trending data."""
    df = _synthetic_ohlcv()
    total = 0
    for spec in TRADITIONAL_GEN1_SPECS:
        strat = compile_spec(spec)
        d = strat.compute_indicators(df.copy())
        ctx = {"instrument": "TEST", "timeframe": Timeframe.H1}
        total += sum(
            strat.generate_signal(d, idx, ctx) is not None
            for idx in range(200, len(d))
        )
    assert total > 0


def test_no_lookahead_signals_unchanged_by_future_bars():
    """Mutating bars after idx must not change the signal at idx."""
    df = _synthetic_ohlcv()
    # Use a crossover family (reads idx-1..idx) as a representative.
    spec = next(s for s in TRADITIONAL_GEN1_SPECS if s.spec_id == "trad_ema_crossover")
    strat = compile_spec(spec)
    d = strat.compute_indicators(df)
    ctx = {"instrument": "TEST", "timeframe": Timeframe.H1}
    idx = 250
    before = strat.generate_signal(d, idx, ctx)
    # Indicators are recomputed on a future-corrupted copy; values at <= idx
    # must be identical, so the decision at idx is unchanged.
    corrupt = df.copy()
    corrupt.loc[corrupt.index[idx + 1:], ["open", "high", "low", "close"]] *= 1.5
    d2 = strat.compute_indicators(corrupt)
    after = strat.generate_signal(d2, idx, ctx)
    assert (before is None) == (after is None)
    if before is not None:
        assert before.direction == after.direction
        assert before.proposed_entry == pytest.approx(after.proposed_entry)
