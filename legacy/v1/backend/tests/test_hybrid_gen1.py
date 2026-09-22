"""Phase 5 — hybrid Gen-1 strategy family.

Locks the 10 curated hybrids: each pairs a primary trigger with a same-direction
confirm/filter, registers as ``hybrid_gen1``, compiles deterministically, and
generates valid signals on synthetic data without indicator KeyErrors.
Pure/offline — no network, no DB.
"""

import numpy as np
import pandas as pd
import pytest

from fibokei.core.models import Timeframe
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.registry import classify_strategy, strategy_registry
from fibokei.strategies.traditional import (
    HYBRID_GEN1_SPECS,
    HYBRID_GEN1_STRATEGY_CLASSES,
)


def _synthetic_ohlcv(n: int = 400, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 0.02 * t + 5 * np.sin(t / 18.0) + np.cumsum(rng.normal(0, 0.15, n))
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

def test_ten_hybrids():
    assert len(HYBRID_GEN1_SPECS) == 10
    assert len(HYBRID_GEN1_STRATEGY_CLASSES) == 10


def test_hybrid_ids_and_family():
    ids = [s.spec_id for s in HYBRID_GEN1_SPECS]
    assert len(set(ids)) == 10
    assert all(i.startswith("hyb_") for i in ids)
    assert all(s.family == "hybrid_gen1" for s in HYBRID_GEN1_SPECS)


def test_every_hybrid_has_a_secondary_indicator():
    """A hybrid must combine 2+ indicators: entry plus a confirm or filter."""
    for s in HYBRID_GEN1_SPECS:
        assert s.entry_rules, f"{s.spec_id} has no entry"
        assert s.confirmation_rules or s.filters, (
            f"{s.spec_id} is not a hybrid — no confirmation/filter"
        )
        assert s.stop.multiple > 0


# ── Registry integration ─────────────────────────────────────────────

def test_all_registered_as_hybrid_gen1():
    listed = {i["id"]: i for i in strategy_registry.list_available()}
    for spec in HYBRID_GEN1_SPECS:
        sid = f"factory_{spec.spec_id}_v{spec.version}"
        assert sid in listed, f"{sid} not registered"
        assert listed[sid]["tier"] == "hybrid_gen1"
        assert classify_strategy(sid) == "hybrid_gen1"


def test_registry_health_reports_hybrids():
    h = strategy_registry.registry_health()
    assert h["hybrid_gen1_count"] == 10
    assert h["registered_count"] == sum(h["tier_counts"].values())


# ── Compile + determinism + signal smoke ─────────────────────────────

def test_hybrids_compile_with_stable_hash():
    for spec in HYBRID_GEN1_SPECS:
        strat = compile_spec(spec)
        assert strat.strategy_id == f"factory_{spec.spec_id}_v{spec.version}"
        assert compile_spec(spec).spec.content_hash == spec.content_hash


@pytest.mark.parametrize("spec", HYBRID_GEN1_SPECS,
                         ids=[s.spec_id for s in HYBRID_GEN1_SPECS])
def test_hybrid_signal_smoke(spec):
    df = _synthetic_ohlcv()
    strat = compile_spec(spec)
    df = strat.compute_indicators(df)
    ctx = {"instrument": "TEST", "timeframe": Timeframe.H1}
    fired = 0
    for idx in range(200, len(df)):
        sig = strat.generate_signal(df, idx, ctx)
        if sig is not None:
            assert sig.stop_loss != sig.proposed_entry
            fired += 1
    fired2 = sum(
        strat.generate_signal(df, idx, ctx) is not None
        for idx in range(200, len(df))
    )
    assert fired == fired2


def test_confirmation_never_contradicts_primary():
    """When a hybrid fires, entry + confirmation + filters all agree on the
    signalled direction — the factory cannot trade against its confirmation."""
    from fibokei.core.models import Direction
    from fibokei.strategies.factory.primitives import PRIMITIVES

    df = _synthetic_ohlcv()
    ctx = {"instrument": "TEST", "timeframe": Timeframe.H1}
    checked = 0
    for spec in HYBRID_GEN1_SPECS:
        strat = compile_spec(spec)
        d = strat.compute_indicators(df.copy())
        for idx in range(200, len(d)):
            sig = strat.generate_signal(d, idx, ctx)
            if sig is None:
                continue
            direction = "long" if sig.direction == Direction.LONG else "short"
            for rule in (spec.entry_rules + spec.confirmation_rules + spec.filters):
                assert PRIMITIVES[rule.primitive].fn(d, idx, rule.params, direction), (
                    f"{spec.spec_id}: {rule.primitive} did not agree with "
                    f"signalled {direction} at idx {idx}"
                )
            checked += 1
            break  # one confirmed signal per hybrid is enough
    assert checked > 0, "no hybrid produced a signal to verify"
