"""Tests for the resumable pipeline core helpers (research/pipeline.py).

Covers the deterministic, fast pieces — checkpoint round-trip, diversity prune,
Pareto front, factory-spec lookup. The ladder itself (process_combo) is
integration-exercised by the live pipeline runs, not here.
"""

from pathlib import Path

from fibokei.research.pipeline import (
    ComboResult,
    _factory_specs,
    append_checkpoint,
    diversity_prune,
    load_checkpoint,
    pareto_front,
    trade_overlap,
)


def _c(sid, inst, tf, composite=0.5, sharpe=1.0, max_dd=10.0, oos=0.4,
       trades=100, entries=None):
    return ComboResult(strategy_id=sid, tier="traditional_gen1", instrument=inst,
                       timeframe=tf, composite=composite, sharpe=sharpe,
                       max_dd=max_dd, oos_score=oos, trades=trades,
                       entries=entries or [])


def test_checkpoint_roundtrip(tmp_path: Path):
    p = tmp_path / "checkpoint.jsonl"
    c1 = _c("s1", "EURUSD", "H4")
    c2 = _c("s2", "GBPUSD", "H1")
    append_checkpoint(p, c1)
    append_checkpoint(p, c2)
    done = load_checkpoint(p)
    assert "s1|EURUSD|H4" in done
    assert "s2|GBPUSD|H1" in done
    assert len(done) == 2


def test_checkpoint_resume_skips_completed(tmp_path: Path):
    p = tmp_path / "checkpoint.jsonl"
    append_checkpoint(p, _c("s1", "EURUSD", "H4"))
    grid = [("s1", "EURUSD", "H4"), ("s1", "EURUSD", "H1")]
    done = load_checkpoint(p)
    todo = [g for g in grid if f"{g[0]}|{g[1]}|{g[2]}" not in done]
    assert todo == [("s1", "EURUSD", "H1")]  # finished one skipped


def test_trade_overlap():
    assert trade_overlap(["a", "b", "c"], ["a", "b"]) == 1.0  # subset
    assert trade_overlap(["a", "b"], ["c", "d"]) == 0.0
    assert trade_overlap([], ["a"]) == 0.0


def test_diversity_prune_drops_near_duplicates():
    shared = ["t1", "t2", "t3", "t4"]
    a = _c("s1", "EURUSD", "H4", composite=0.6, entries=shared)
    b = _c("s2", "EURUSD", "H4", composite=0.5, entries=shared)  # dup of a
    c = _c("s3", "EURUSD", "H4", composite=0.4, entries=["x1", "x2", "x3"])
    kept = diversity_prune([a, b, c], overlap_thresh=0.7)
    ids = {k.strategy_id for k in kept}
    assert "s1" in ids and "s3" in ids and "s2" not in ids


def test_pareto_front_excludes_dominated():
    strong = _c("s1", "EURUSD", "H4", sharpe=2.0, max_dd=5.0, oos=0.6, trades=200)
    weak = _c("s2", "EURUSD", "H4", sharpe=1.0, max_dd=20.0, oos=0.2, trades=90)
    front = pareto_front([strong, weak])
    assert strong in front and weak not in front


def test_factory_specs_keys_match_registry():
    from fibokei.strategies.registry import strategy_registry
    specs = _factory_specs()
    registered = {i["id"] for i in strategy_registry.list_available()}
    # Every factory spec id is registered.
    assert specs, "no factory specs found"
    assert all(k in registered for k in specs)
    assert len(specs) == 43  # 25 traditional + 10 hybrid + 8 triple
