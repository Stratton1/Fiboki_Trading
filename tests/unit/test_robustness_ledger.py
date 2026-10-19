"""Ledger robustness diagnostics (research/robustness/ledger.py and boundary.py).

A synthetic stored run is built with a POISONED holdout: every trade that exits
at or after the boundary carries an absurd profit. If any diagnostic read it,
its numbers would show it.
"""
from __future__ import annotations

import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

from fiboki.research.robustness import ledger as rl
from fiboki.research.robustness.boundary import (
    HoldoutBoundary,
    assert_clear,
    load_end,
    research_slice,
    resolve_boundary,
    run_span,
)
from fiboki.validation.holdout import HoldoutLeak, HoldoutRegistry

POISON = 1e9


def _record(n_days: int = 400, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n_days * 6, freq="4h", tz="UTC")
    equity = 10_000 + np.cumsum(rng.normal(1.0, 20.0, size=len(idx)))
    trades = []
    for k, i in enumerate(range(10, len(idx) - 10, 13)):
        pnl = float(rng.normal(5.0, 60.0))
        trades.append({
            "trade_id": f"t{k}", "instrument": "EURUSD" if k % 3 else "GBPUSD",
            "direction": "long" if k % 2 else "short", "size": 1000.0, "entry_price": 1.1,
            "exit_price": 1.1, "entry_time": str(idx[i]), "exit_time": str(idx[i + 5]),
            "exit_reason": ["stop_loss", "take_profit", "time_stop"][k % 3], "gross_pnl": pnl + 2.0,
            "spread_cost": 1.0, "commission": 0.0, "slippage_cost": 0.5, "financing_cost": 0.5, "net_pnl": pnl,
            "account_ccy": "USD", "bars_held": 5, "provenance": "backtest", "fill_flags": ["gapped"] if k % 5 == 0 else [],
        })
    curve = [{"timestamp": str(t), "equity": float(e), "balance": float(e), "open_positions": 0}
             for t, e in zip(idx, equity, strict=True)]
    return {"trades": trades, "equity_curve": curve,
            "data_fingerprint": {"EURUSD": {"first": idx[0].isoformat(), "last": idx[-1].isoformat()}},
            "dataset_version_ids": ["ds1"], "metrics": {"initial_equity": 10_000.0}}


def _boundary(record: dict) -> HoldoutBoundary:
    first, last = run_span(record)
    return resolve_boundary(dataset_version_ids=["ds1"], first=first, last=last, registry_path=None)


def _poisoned() -> tuple[dict, HoldoutBoundary]:
    rec = _record()
    b = _boundary(rec)
    for t in rec["trades"]:
        if pd.Timestamp(t["exit_time"]) >= b.start:
            t["net_pnl"] = POISON
            t["gross_pnl"] = POISON
    for p in rec["equity_curve"]:
        if pd.Timestamp(p["timestamp"]) >= b.start:
            p["equity"] = POISON
    return rec, b


# --------------------------------------------------------------- boundary


def test_provisional_boundary_is_the_final_fifth_of_the_span():
    rec = _record()
    first, last = run_span(rec)
    b = _boundary(rec)
    assert b.basis == "provisional"
    assert (b.start - first) / (last - first) == pytest.approx(0.8)


def test_a_defined_segment_wins_and_is_read_read_only(tmp_path):
    rec = _record()
    first, last = run_span(rec)
    with HoldoutRegistry(tmp_path / "holdout.sqlite") as reg:
        seg = reg.define("ds1", data_start=first, data_end=last, holdout_fraction=0.3)
    b = resolve_boundary(dataset_version_ids=["ds1"], first=first, last=last,
                         registry_path=tmp_path / "holdout.sqlite")
    assert b.basis == "registry" and b.start == seg.holdout_start
    # The read was read-only: the consumption table is still empty.
    conn = sqlite3.connect(tmp_path / "holdout.sqlite")
    assert conn.execute("SELECT count(*) FROM holdout_consumption").fetchone()[0] == 0
    conn.close()


def test_an_unlocatable_holdout_refuses():
    with pytest.raises(ValueError, match="cannot promise"):
        resolve_boundary(dataset_version_ids=[], first=None, last=None, registry_path=None)


def test_assert_clear_and_load_end():
    b = HoldoutBoundary(pd.Timestamp("2024-01-01", tz="UTC"), "registry", ("ds",), "")
    assert_clear(b, start=pd.Timestamp("2023-01-01", tz="UTC"), end=pd.Timestamp("2024-01-01", tz="UTC"))
    with pytest.raises(HoldoutLeak):
        assert_clear(b, start=pd.Timestamp("2023-01-01", tz="UTC"), end=pd.Timestamp("2024-01-01 00:00:01", tz="UTC"))
    assert pd.Timestamp(load_end(b), tz="UTC") < b.start


# --------------------------------------------------------------- holdout exclusion


def test_every_ledger_diagnostic_excludes_the_poisoned_holdout():
    rec, b = _poisoned()
    s = research_slice(rec, b)
    assert s.trades_excluded > 0 and s.points_excluded > 0
    assert all(t["net_pnl"] != POISON for t in s.trades)
    bands = rl.confidence_bands(s, initial=10_000.0, seed=1, n_boot=200)
    for band in bands["bands"]:
        for v in (band.point, band.p5, band.p50, band.p95):
            assert v is None or abs(v) < 1e6, band
    for name, buckets in rl.breakdowns(s).items():
        assert all(abs(bk.net_pnl) < 1e6 for bk in buckets), name
    cons = rl.consistency(s, k=4, initial=10_000.0)
    assert all(abs(sp.metrics["net_profit"]) < 1e6 for sp in cons["periods"] + cons["instruments"])
    fills = rl.fill_detail(s, intrabar_policy="stop_first")
    assert all(r["net_pnl"] != POISON for r in fills["rows"])
    assert all(r["exit_time"] < b.start for r in fills["rows"])
    objs = rl.objectives(s.trades, rl.ts.equity_series(s.equity_curve), 10_000.0, {})
    assert abs(objs["net_profit"]["raw"]) < 1e6


# --------------------------------------------------------------- bands


def test_bands_are_seeded_and_ordered():
    rec = _record()
    s = research_slice(rec, _boundary(rec))
    a = rl.confidence_bands(s, initial=10_000.0, seed=4, n_boot=300)
    b = rl.confidence_bands(s, initial=10_000.0, seed=4, n_boot=300)
    assert a == b
    for band in a["bands"]:
        assert band.p5 <= band.p50 <= band.p95, band.key
        assert band.block_length >= 1.0
    c = rl.confidence_bands(s, initial=10_000.0, seed=5, n_boot=300)
    assert c["bands"] != a["bands"]


def test_bands_with_too_few_observations_say_why():
    rec = _record()
    rec["trades"] = rec["trades"][:3]
    s = research_slice(rec, _boundary(rec))
    out = {b.key: b for b in rl.confidence_bands(s, initial=10_000.0, seed=1, n_boot=200)["bands"]}
    assert out["win_rate_pct"].p50 is None and "at least 8" in out["win_rate_pct"].reason


# --------------------------------------------------------------- breakdowns, consistency


def test_breakdowns_partition_every_trade():
    rec = _record()
    s = research_slice(rec, _boundary(rec))
    total = sum(t["net_pnl"] for t in s.trades)
    for name, buckets in rl.breakdowns(s).items():
        assert sum(b.n for b in buckets) == len(s.trades), name
        assert sum(b.net_pnl for b in buckets) == pytest.approx(total), name
    keys = {b.key for b in rl.breakdowns(s)["session"]}
    assert keys <= {"off_hours", "tokyo", "london", "new_york", "tokyo_london", "london_new_york"}


def test_consistency_assigns_each_trade_to_one_period():
    rec = _record()
    s = research_slice(rec, _boundary(rec))
    out = rl.consistency(s, k=5, initial=10_000.0)
    assert sum(p.metrics["n_trades"] for p in out["periods"]) == len(s.trades)
    assert {i.key for i in out["instruments"]} == {"EURUSD", "GBPUSD"}
    signed = [p.positive for p in out["periods"] if p.positive is not None]
    assert out["period_positive_share"] == pytest.approx(sum(signed) / len(signed))
    with pytest.raises(ValueError):
        rl.consistency(s, k=1, initial=10_000.0)


# --------------------------------------------------------------- objectives (golden arithmetic)


def test_penalty_shrinks_only_the_favourable_side():
    """Golden: below 80 trades, score = neutral + (raw - neutral) * n / 80.

    40 trades, Sharpe 1.2:   0 + 1.2 * 40/80 = 0.6
    40 trades, PF 2.0:       1 + 1.0 * 40/80 = 1.5
    40 trades, Sharpe -0.5:  unchanged (a loss is never made to look smaller)
    80 trades, Sharpe 1.2:   unchanged
    """
    assert rl.penalised(1.2, 40, 0.0) == pytest.approx(0.6)
    assert rl.penalised(2.0, 40, 1.0) == pytest.approx(1.5)
    assert rl.penalised(-0.5, 40, 0.0) == -0.5
    assert rl.penalised(0.8, 40, 1.0) == 0.8
    assert rl.penalised(1.2, 80, 0.0) == 1.2
    assert rl.penalised(None, 10, 0.0) is None


def test_every_objective_is_reported_with_its_penalty():
    rec = _record()
    s = research_slice(rec, _boundary(rec))
    objs = rl.objectives(s.trades, rl.ts.equity_series(s.equity_curve), 10_000.0, {})
    assert set(objs) == set(rl.OBJECTIVES)
    n = len(s.trades)
    pnl = np.array([t["net_pnl"] for t in s.trades])
    assert objs["net_profit"]["raw"] == pytest.approx(pnl.sum())
    assert objs["expectancy"]["raw"] == pytest.approx(pnl.mean())
    assert objs["sqn"]["raw"] == pytest.approx(math.sqrt(n) * pnl.mean() / pnl.std(ddof=1))
    assert objs["sqn"]["basis"] == "net P&L"  # no stops recorded on these rows
    for o in objs.values():
        assert o["penalty_factor"] == pytest.approx(min(1.0, n / 80))


# --------------------------------------------------------------- optimisation profile


def test_optimisation_profile_is_median_not_peak():
    pts = [({"a": i}, v, 100) for i, v in enumerate([-3.0, 1.0, 2.0, 10.0, None])]
    out = rl.optimisation_profile(pts)
    assert out["n_points"] == 5 and out["n_undefined"] == 1
    assert out["peak"] == 10.0 and out["median"] == 1.5
    assert out["profitable_share"] == pytest.approx(0.75)
    assert out["median_over_peak"] == pytest.approx(0.15)
    assert out["peak_params"] == {"a": 3}
    # Profit factor breaks even at 1, not 0: a PF of 0.8 is not "profitable".
    pf = rl.optimisation_profile([({"a": i}, v, 100) for i, v in enumerate([0.8, 0.9, 1.2, 1.5])],
                                 neutral=rl.OBJECTIVES["profit_factor"].neutral)
    assert pf["profitable_share"] == pytest.approx(0.5)
    empty = rl.optimisation_profile([({"a": 1}, None, 0)])
    assert empty["peak"] is None and empty["profitable_share"] is None


# --------------------------------------------------------------- fills


def test_fill_detail_reads_flags_and_never_invents_them():
    rec = _record()
    rec["trades"][1].pop("fill_flags")
    s = research_slice(rec, _boundary(rec))
    out = rl.fill_detail(s, intrabar_policy="stop_first")
    rows = {r["trade_id"]: r for r in out["rows"]}
    assert rows["t1"]["recorded"] is False and rows["t1"]["rules"] == []
    assert rows["t0"]["rules"] == ["gapped"]
    assert rows["t2"]["rules"] == ["plain"]
    assert out["recorded"] == len(out["rows"]) - 1
