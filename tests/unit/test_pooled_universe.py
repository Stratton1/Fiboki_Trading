"""E-4: pooled-universe candidates and the effective sample size.

What is pinned here, before any E-4 number exists
(``research/preregistration/pooled_universe_e4.json``):

* the design effect is exact at both ends (independent sleeves: N_eff = N;
  identical sleeves: N_eff = N / m), matches a hand-computed case, is never
  below 1, and is calibrated: across many simulated datasets the z-statistic
  scaled by N_eff has unit variance where the raw count's does not;
* the pooled evaluator keeps each instrument's own costs, sums equal-capital
  sleeves on the union calendar, refuses anything it cannot align, and is
  deterministic whatever order the sleeves arrive in;
* the ladder reads N_eff, and only N_eff, at its three count sites (rung 0's
  floor and MinTRL gate, rung 2's OOS-fold floor, rung 6's holdout floor), and a
  single-series evaluation is untouched;
* the composite holdout never lets a rung touch any constituent's holdout, spends
  the look on EVERY constituent, refuses a second look, and the main stage's
  deferred rung 6 claims nothing and can never promote;
* the trial accounting and the plan equal the filing.
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason
from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    ParameterGrid,
    WindowEvaluation,
    params_key,
)
from fiboki.validation.gates import (
    GATE_SET_V2,
    PRODUCTION_GATE_SET,
    Comparison,
    Gate,
    GateSet,
)
from fiboki.validation.holdout import (
    HoldoutAlreadyConsumed,
    HoldoutLeak,
    HoldoutRegistry,
)
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.pooled import (
    DeferredHoldoutRung,
    PooledEvaluator,
    PooledHoldoutRegistry,
    PooledSleeve,
    design_effect,
    pooled_dataset_version_id,
    rungs_with_deferred_holdout,
)
from fiboki.validation.report import RungOutcome, Verdict

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "research" / "preregistration" / "pooled_universe_e4.json"
SCRIPT = ROOT / "scripts" / "pooled_universe_study.py"
NS_DAY = 86_400 * 1_000_000_000
H4 = 4 * 3_600 * 1_000_000_000


def _script():
    name = "pooled_universe_study"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# A synthetic sleeve with a known co-movement
# --------------------------------------------------------------------------


def _seed(*parts: object) -> int:
    import hashlib

    blob = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big") % (2**63)


class SleeveEval:
    """Per-bar P&L ``mu + sqrt(rho) * common + sqrt(1 - rho) * own`` on a regular H4
    calendar; one trade per ``bars_per_trade`` bars whose net P&L is the sum of
    its bars. ``common`` is keyed on the window only, so sleeves of one pool share
    it; ``own`` on the sleeve's seed, the binding and the window."""

    def __init__(self, instrument: str, index: pd.DatetimeIndex, *, seed: int, rho: float,
                 mu: float = 0.0, spread: float = 0.02, bars_per_trade: int = 6,
                 decay: float = 0.15) -> None:
        self.instrument, self.index, self.seed, self.rho = instrument, index, seed, rho
        self.mu, self.spread, self.k, self.decay = mu, spread, bars_per_trade, decay

    def __call__(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        lo = int(self.index.searchsorted(window.start, side="left"))
        hi = int(self.index.searchsorted(window.end, side="left"))
        bars = self.index[lo:hi]
        n = len(bars)
        span = (window.start.isoformat(), window.end.isoformat())
        common = np.random.default_rng(_seed("common", *span)).standard_normal(n)
        own = np.random.default_rng(_seed(self.seed, params_key(params), *span)).standard_normal(n)
        d = abs(int(params["a"])) + abs(int(params["b"]))
        drift = self.mu * max(0.0, 1.0 - self.decay * d)
        pnl = drift + math.sqrt(self.rho) * common + math.sqrt(1.0 - self.rho) * own
        n_trades = n // self.k
        pnl[n_trades * self.k:] = 0.0
        trades = []
        for i in range(n_trades):
            net = float(pnl[i * self.k:(i + 1) * self.k].sum())
            entry = bars[i * self.k]
            trades.append(Trade(
                instrument=self.instrument, direction=Direction.LONG, size=1.0,
                entry_price=1.0, exit_price=1.0 + net / 1e5, entry_time=entry,
                exit_time=entry + pd.Timedelta(hours=4 * self.k - 1),
                exit_reason=ExitReason.TAKE_PROFIT if net >= 0 else ExitReason.STOP_LOSS,
                gross_pnl=net + self.spread, spread_cost=self.spread, commission=0.0,
                slippage_cost=0.0, financing_cost=0.0, net_pnl=net, account_ccy="GBP",
                strategy_id="pooled_test", bars_held=self.k,
            ))
        return WindowEvaluation(
            window=window, params=dict(params), n_trades=n_trades, net_profit=float(pnl.sum()),
            returns=pnl, returns_basis="period", trades=tuple(trades),
            meta={"opening_equity": 10_000.0, "strategy_content_hash": "doc", "engine_config_hash": self.instrument},
        )


GRID = ParameterGrid.from_axes({"a": (0, 1), "b": (0, 1)})


def _pool(m: int, *, rho: float, days: int = 900, mu: float = 0.0, identical: bool = False,
          start: str = "2015-01-05", spreads: list[float] | None = None):
    index = pd.date_range(start, periods=days * 6, freq="4h", tz="UTC")
    sleeves = []
    for i in range(m):
        inst = f"S{i:02d}"
        ev = SleeveEval(inst, index, seed=0 if identical else 100 + i, rho=1.0 if identical else rho,
                        mu=mu, spread=(spreads[i] if spreads else 0.02))
        sleeves.append(PooledSleeve(inst, ev, index, f"ds_{inst}"))
    return PooledEvaluator(tuple(sleeves)), index


def _registry(pooled: PooledEvaluator, index: pd.DatetimeIndex, path: Path | None = None):
    inner = HoldoutRegistry(path) if path is not None else HoldoutRegistry.in_memory()
    for s in pooled.sleeves:
        inner.define(s.dataset_version_id, data_start=index[0], data_end=index[-1])
    return inner, PooledHoldoutRegistry(inner, [s.dataset_version_id for s in pooled.sleeves])


def _candidate(name: str = "pooled_test") -> Candidate:
    return Candidate(strategy_id=name, content_hash=f"hash_{name}", default_params={"a": 0, "b": 0}, grid=GRID)


# --------------------------------------------------------------------------
# The design effect
# --------------------------------------------------------------------------


def _calendar(n_bars: int) -> np.ndarray:
    return pd.Timestamp("2010-01-04", tz="UTC").value + np.arange(n_bars, dtype=np.int64) * H4


def test_independent_sleeves_give_deff_one_so_n_eff_equals_n() -> None:
    """Exactly 1 in expectation; the estimate carries sampling noise of about
    (m - 1) / sqrt(pairs * weeks), here about 0.04, and is floored at 1."""
    rng = np.random.default_rng(7)
    ts = _calendar(6 * 5000)
    deff = design_effect(rng.standard_normal((ts.size, 10)), ts)
    assert 1.0 <= deff.deff < 1.12
    assert deff.effective(4000) == pytest.approx(4000 / deff.deff)
    assert deff.effective(4000) > 0.89 * 4000


def test_identical_sleeves_give_deff_m_so_n_eff_is_n_over_m() -> None:
    rng = np.random.default_rng(8)
    ts = _calendar(6 * 1000)
    x = np.repeat(rng.standard_normal((ts.size, 1)), 6, axis=1)
    deff = design_effect(x, ts)
    assert deff.deff == pytest.approx(6.0, abs=1e-9)
    assert deff.effective(1200) == pytest.approx(200.0)


def test_equicorrelated_sleeves_give_one_plus_m_minus_one_rho() -> None:
    rng = np.random.default_rng(9)
    ts = _calendar(6 * 3000)
    m, rho = 8, 0.4
    x = math.sqrt(rho) * rng.standard_normal((ts.size, 1)) + math.sqrt(1 - rho) * rng.standard_normal((ts.size, m))
    assert design_effect(x, ts).deff == pytest.approx(1 + (m - 1) * rho, rel=0.06)


def test_a_hand_computed_case() -> None:
    """Two sleeves, one bar per Monday, 40 weeks: daily and weekly sums are the bars.

    x = [1, -1, 1, -1, ...], y = [1, -1, -1, 1, 1, -1, -1, 1, ...] (period 4).
    var(x) = var(y) = 40/39 (ddof 1, mean 0); cov(x, y) = sum(x*y)/39 with
    x*y = [1, 1, -1, -1, ...] summing to 0, so DEFF = 1 on that pair. With
    y = x instead, cov = var and DEFF = (v + v + 2v) / (v + v) = 2. With
    y = 0.5 x + 0.5 z (z orthogonal to x, unit entries): var(y) = 0.5 * 40/39,
    cov = 0.5 * 40/39, so DEFF = (1 + 0.5 + 1) / (1 + 0.5) = 5/3.
    """
    weeks = 40
    ts = pd.Timestamp("2018-01-01", tz="UTC").value + np.arange(weeks, dtype=np.int64) * 7 * NS_DAY
    x = np.array([1.0 if i % 2 == 0 else -1.0 for i in range(weeks)])
    y = np.array([1.0, -1.0, -1.0, 1.0] * (weeks // 4))
    assert design_effect(np.column_stack([x, y]), ts).by_frequency["weekly"]["deff_raw"] == pytest.approx(1.0)
    assert design_effect(np.column_stack([x, x]), ts).deff == pytest.approx(2.0)
    z = y  # orthogonal to x with unit entries
    assert design_effect(np.column_stack([x, 0.5 * x + 0.5 * z]), ts).by_frequency["weekly"]["deff_raw"] == pytest.approx(5.0 / 3.0)


def test_negative_correlation_never_buys_a_larger_sample() -> None:
    rng = np.random.default_rng(10)
    ts = _calendar(6 * 600)
    a = rng.standard_normal(ts.size)
    deff = design_effect(np.column_stack([a, -a]), ts)
    assert deff.by_frequency["daily"]["deff_raw"] < 0.01
    assert deff.deff == 1.0


def test_asynchronous_closes_are_caught_by_the_weekly_sum() -> None:
    """Sleeve B books sleeve A's daily P&L one day later (Epps): the daily
    correlation is about zero, the weekly sums are strongly correlated, and the
    larger, weekly, design effect is the one used."""
    rng = np.random.default_rng(11)
    days = 3000
    ts = pd.Timestamp("2010-01-04", tz="UTC").value + np.arange(days, dtype=np.int64) * NS_DAY
    a = rng.standard_normal(days)
    b = np.concatenate([[0.0], a[:-1]])
    deff = design_effect(np.column_stack([a, b]), ts)
    assert deff.by_frequency["daily"]["deff"] < 1.08
    assert deff.by_frequency["weekly"]["deff"] > 1.6
    assert deff.deff == deff.by_frequency["weekly"]["deff"]
    assert "weekly" in deff.basis


def test_one_active_sleeve_is_deff_one_and_too_few_periods_is_m() -> None:
    rng = np.random.default_rng(12)
    ts = _calendar(6 * 400)
    x = np.zeros((ts.size, 3))
    x[:, 0] = rng.standard_normal(ts.size)
    assert design_effect(x, ts).deff == 1.0
    short = _calendar(6 * 20)  # 20 days, 3 weeks: neither frequency is usable
    y = rng.standard_normal((short.size, 4))
    d = design_effect(y, short)
    assert d.deff == 4.0 and "conservative" in d.basis


def test_n_eff_is_calibrated_where_the_raw_count_is_not() -> None:
    """Across 300 simulated datasets of 8 sleeves correlated at 0.5, the per-trade
    mean's z-statistic scaled by sqrt(N) has variance near 1 + 7 * 0.5 = 4.5; scaled
    by sqrt(N_eff) it has variance near 1. This is the property P0's z_variance
    measures on real dependence."""
    rng = np.random.default_rng(13)
    m, rho, days = 8, 0.5, 400
    ts = pd.Timestamp("2012-01-02", tz="UTC").value + np.arange(days, dtype=np.int64) * NS_DAY
    z_naive, z_eff = [], []
    for _ in range(300):
        x = math.sqrt(rho) * rng.standard_normal((days, 1)) + math.sqrt(1 - rho) * rng.standard_normal((days, m))
        trades = x.ravel()  # one trade per sleeve per day
        sr = trades.mean() / trades.std(ddof=1)
        deff = design_effect(x, ts)
        z_naive.append(sr * math.sqrt(trades.size))
        z_eff.append(sr * math.sqrt(deff.effective(trades.size)))
    assert 3.5 < np.var(z_naive, ddof=1) < 5.5
    assert 0.8 < np.var(z_eff, ddof=1) < 1.25


# --------------------------------------------------------------------------
# The pooled evaluator
# --------------------------------------------------------------------------


def test_costs_stay_per_instrument_and_sleeves_sum_with_equal_capital() -> None:
    pooled, index = _pool(3, rho=0.3, days=200, spreads=[0.01, 0.05, 0.30])
    w = DateWindow("w", index[10], index[900])
    ev = pooled({"a": 0, "b": 0}, w)
    singles = [s.evaluator({"a": 0, "b": 0}, w) for s in pooled.sleeves]
    assert ev.n_trades == sum(s.n_trades for s in singles)
    assert ev.net_profit == pytest.approx(sum(s.net_profit for s in singles))
    by_inst = {sl.instrument: {t.spread_cost for t in ev1.trades} for sl, ev1 in zip(pooled.sleeves, singles, strict=True)}
    for t in ev.trades:
        assert {t.spread_cost} == by_inst[t.instrument]
    assert sorted(ev.trades, key=lambda t: (t.entry_time, t.exit_time, t.instrument)) == list(ev.trades)
    assert ev.meta["opening_equity"] == pytest.approx(30_000.0)
    assert ev.returns.sum() == pytest.approx(sum(s.returns.sum() for s in singles))
    assert ev.is_pooled and ev.n_trades_effective <= ev.n_trades
    assert ev.meta["n_trades_effective"] == pytest.approx(ev.n_trades / ev.meta["design_effect"]["deff"])


def test_sleeves_on_different_calendars_sum_on_the_union() -> None:
    a_idx = pd.date_range("2020-01-06", periods=600, freq="4h", tz="UTC")
    b_idx = a_idx[a_idx.hour != 8]  # B has no 08:00 bar (a session break)
    a = SleeveEval("AAA", a_idx, seed=1, rho=0.2)
    b = SleeveEval("BBB", b_idx, seed=2, rho=0.2)
    pooled = PooledEvaluator((PooledSleeve("BBB", b, b_idx, "ds_b"), PooledSleeve("AAA", a, a_idx, "ds_a")))
    w = DateWindow("w", a_idx[0], a_idx[-1])
    ev = pooled({"a": 0, "b": 0}, w)
    ea, eb = a({"a": 0, "b": 0}, w), b({"a": 0, "b": 0}, w)
    union = a_idx[(a_idx >= w.start) & (a_idx < w.end)]
    assert ev.returns.size == len(union) == ev.meta["n_periods"]
    eight = np.flatnonzero(union.hour == 8)
    np.testing.assert_allclose(ev.returns[eight], ea.returns[eight])
    assert ev.returns.sum() == pytest.approx(ea.returns.sum() + eb.returns.sum())


def _ledger(ev: WindowEvaluation) -> list[tuple[Any, ...]]:
    """The trades without their random ids (as BacktestResult.LEDGER_COLUMNS)."""
    return [(t.instrument, t.entry_time, t.exit_time, t.net_pnl, t.spread_cost) for t in ev.trades]


def test_the_pool_is_deterministic_and_order_free() -> None:
    pooled, index = _pool(4, rho=0.3, days=200)
    w = DateWindow("w", index[0], index[1000])
    reordered = PooledEvaluator(tuple(reversed(pooled.sleeves)))
    one, two, three = pooled({"a": 1, "b": 0}, w), pooled({"a": 1, "b": 0}, w), reordered({"a": 1, "b": 0}, w)
    for other in (two, three):
        np.testing.assert_array_equal(one.returns, other.returns)
        assert _ledger(one) == _ledger(other)
        assert one.meta == other.meta
    assert pooled.dataset_version_id == reordered.dataset_version_id == pooled_dataset_version_id(
        ["ds_S03", "ds_S00", "ds_S02", "ds_S01"]
    )


def test_the_pool_refuses_what_it_cannot_align() -> None:
    index = pd.date_range("2020-01-06", periods=300, freq="4h", tz="UTC")
    good = SleeveEval("AAA", index, seed=1, rho=0.0)
    with pytest.raises(ValueError, match="at least two"):
        PooledEvaluator((PooledSleeve("AAA", good, index, "a"),))
    with pytest.raises(ValueError, match="duplicate"):
        PooledEvaluator((PooledSleeve("AAA", good, index, "a"), PooledSleeve("AAA", good, index, "b")))
    wrong_calendar = PooledEvaluator((
        PooledSleeve("AAA", good, index, "a"),
        PooledSleeve("BBB", SleeveEval("BBB", index, seed=2, rho=0.0), index[::2], "b"),
    ))
    with pytest.raises(ValueError, match="calendar"):
        wrong_calendar({"a": 0, "b": 0}, DateWindow("w", index[0], index[-1]))

    class OtherDoc(SleeveEval):
        def __call__(self, params, window):
            ev = super().__call__(params, window)
            return WindowEvaluation(window=ev.window, params=ev.params, n_trades=ev.n_trades,
                                    net_profit=ev.net_profit, returns=ev.returns, returns_basis="period",
                                    trades=ev.trades, meta={**ev.meta, "strategy_content_hash": "other"})

    mixed = PooledEvaluator((PooledSleeve("AAA", good, index, "a"),
                             PooledSleeve("BBB", OtherDoc("BBB", index, seed=3, rho=0.0), index, "b")))
    with pytest.raises(ValueError, match="different strategies"):
        mixed({"a": 0, "b": 0}, DateWindow("w", index[0], index[-1]))


def test_a_single_series_evaluation_is_untouched() -> None:
    index = pd.date_range("2020-01-06", periods=300, freq="4h", tz="UTC")
    ev = SleeveEval("AAA", index, seed=1, rho=0.0)({"a": 0, "b": 0}, DateWindow("w", index[0], index[-1]))
    assert not ev.is_pooled
    assert ev.n_trades_effective == float(ev.n_trades)


# --------------------------------------------------------------------------
# The ladder reads N_eff at its three count sites, and only there
# --------------------------------------------------------------------------


def _measure(pooled: PooledEvaluator, index: pd.DatetimeIndex, **config: Any):
    _, ledger = _registry(pooled, index)
    measured: list[Any] = []
    cfg = {"min_trades": 150, "walk_forward_folds": 3, "spa_bootstraps": 40, "stress_samples": 5,
           "external_trial_count": 100, **config}
    reports = ValidationLadder(config=LadderConfig(**cfg), gate_set=PRODUCTION_GATE_SET).run_measuring(
        _candidate(), pooled, registry=ledger, dataset_version_id=pooled.dataset_version_id,
        gate_sets={GATE_SET_V2.version: GATE_SET_V2}, measured_rungs=measured,
    )
    return reports, {r.index: r for r in measured}


def test_rung_zero_and_the_min_trl_gate_read_the_effective_count() -> None:
    pooled, index = _pool(6, rho=0.5, days=500, mu=0.15)
    reports, rungs = _measure(pooled, index)
    r0 = rungs[0].metrics
    n_raw, n_eff = r0["n_trades"], r0["n_trades_effective"]
    assert r0["design_effect"]["deff"] > 2.0 and n_eff == pytest.approx(n_raw / r0["design_effect"]["deff"])
    prod = {g.gate.name: g for g in reports[PRODUCTION_GATE_SET.version].gate_results}
    assert prod["min_track_record"].value == pytest.approx(n_eff / max(150.0, r0["min_trl_95"]))
    audited = {g.gate.name: g for g in reports[GATE_SET_V2.version].gate_results}
    assert audited["min_trades"].value == pytest.approx(n_eff)
    # rung 2: the OOS-fold floor is the smallest fold's EFFECTIVE count
    folds = rungs[2].metrics["folds"]
    assert all("oos_trades_effective" in f and f["oos_trades_effective"] < f["oos_trades"] for f in folds)
    assert prod["walk_forward_min_oos_trades"].value == pytest.approx(min(f["oos_trades_effective"] for f in folds))
    # where N_eff does NOT enter: expectancy uses every trade, and deflation counts periods
    assert r0["expectancy"] == pytest.approx(r0["net_profit"] / n_raw)
    assert r0["min_trl_basis"]["n_trades"] == n_raw


def test_correlated_trades_cannot_clear_the_floor_on_their_number_alone() -> None:
    """Four IDENTICAL sleeves of about 100 research-window trades each: 400 pooled
    trades, which are one sleeve's information (DEFF = 4). Rung 0 rejects on the
    150-trade floor; the same number of independent trades clears it."""
    days = 125
    identical, index = _pool(4, rho=1.0, days=days, mu=0.5, identical=True)
    _, rungs = _measure(identical, index)
    r0 = rungs[0]
    assert r0.metrics["n_trades"] >= 300
    assert r0.metrics["n_trades_effective"] == pytest.approx(r0.metrics["n_trades"] / 4.0)
    assert r0.outcome is RungOutcome.FAIL and "effective trades" in r0.reason

    independent, index2 = _pool(4, rho=0.0, days=days, mu=0.5)
    _, rungs2 = _measure(independent, index2)
    assert rungs2[0].metrics["n_trades_effective"] >= 150
    assert "effective trades" not in rungs2[0].reason


# --------------------------------------------------------------------------
# The holdout across constituents
# --------------------------------------------------------------------------


def _staggered_registry(path: Path | None = None):
    inner = HoldoutRegistry(path) if path is not None else HoldoutRegistry.in_memory()
    inner.define("ds_a", data_start="2010-01-01", data_end="2020-01-01")
    inner.define("ds_b", data_start="2011-01-01", data_end="2020-01-01")
    inner.define("ds_c", data_start="2010-06-01", data_end="2019-06-01")
    return inner, PooledHoldoutRegistry(inner, ["ds_c", "ds_a", "ds_b"])


def test_the_composite_segment_reads_no_constituent_holdout() -> None:
    inner, ledger = _staggered_registry()
    segs = {c: inner.segment(c) for c in ("ds_a", "ds_b", "ds_c")}
    seg = ledger.segment(ledger.dataset_version_id)
    assert seg.research_window.start == max(s.data_start for s in segs.values())
    assert seg.research_window.end == min(s.holdout_start for s in segs.values())
    assert seg.window.start == max(s.holdout_start for s in segs.values())
    assert seg.window.end == min(s.data_end for s in segs.values())
    for s in segs.values():
        assert not seg.research_window.overlaps(s.window)
        assert seg.window.start >= s.holdout_start
    ledger.assert_untouched(ledger.dataset_version_id, seg.research_window)
    # A window past the EARLIEST constituent holdout start leaks into that one.
    leaky = DateWindow("x", seg.research_window.start, seg.research_window.end + pd.Timedelta(days=1))
    with pytest.raises(HoldoutLeak):
        ledger.assert_untouched(ledger.dataset_version_id, leaky)


def test_a_pooled_look_is_spent_on_every_constituent_and_only_once(tmp_path: Path) -> None:
    inner, ledger = _staggered_registry(tmp_path / "holdout.sqlite")
    token = ledger.claim(ledger.dataset_version_id, "doc_hash", strategy_id="doc")
    assert ledger.is_consumed("doc_hash") == {"ds_a": True, "ds_b": True, "ds_c": True}
    ledger.record_outcome(token, {"net_profit": 1.0})
    assert all(c.outcome is not None for c in inner.consumptions())
    with pytest.raises(HoldoutAlreadyConsumed):
        ledger.claim(ledger.dataset_version_id, "doc_hash")
    # A single-series look at one constituent forbids the pooled look, and the
    # refusal writes nothing on the others.
    inner.claim("ds_b", "other_hash")
    with pytest.raises(HoldoutAlreadyConsumed):
        ledger.claim(ledger.dataset_version_id, "other_hash")
    assert ledger.is_consumed("other_hash") == {"ds_a": False, "ds_b": True, "ds_c": False}


def test_the_pooled_ledger_is_in_memory_exactly_when_its_registry_is(tmp_path: Path) -> None:
    _, mem = _staggered_registry()
    _, disk = _staggered_registry(tmp_path / "h.sqlite")
    assert mem.db_path is None and disk.db_path == tmp_path / "h.sqlite"


#: A permissive WIRING bar: one trade gate, so a strong synthetic edge can clear
#: rungs 0 to 5 and the holdout path is exercised. Never a promotion bar.
WIRING = GateSet(
    version="test-pooled-wiring-only",
    gates=(Gate("min_trades", "n_trades", Comparison.GTE, 20.0, 0),),
)


def _wiring_ladder(rungs=None) -> ValidationLadder:
    kwargs = {"rungs": rungs} if rungs is not None else {}
    return ValidationLadder(
        config=LadderConfig(min_trades=20, walk_forward_folds=3, spa_bootstraps=40, stress_samples=5),
        gate_set=WIRING, **kwargs,
    )


def test_the_main_stage_ladder_claims_nothing_and_cannot_promote(tmp_path: Path) -> None:
    pooled, index = _pool(3, rho=0.3, days=400, mu=0.6)
    inner, ledger = _registry(pooled, index, tmp_path / "h.sqlite")
    report = _wiring_ladder(rungs_with_deferred_holdout()).run(
        _candidate(), pooled, registry=ledger, dataset_version_id=pooled.dataset_version_id,
    )
    assert [r.passed for r in report.rungs if r.index <= 5] == [True] * 6
    assert report.verdict is Verdict.INCOMPLETE and report.binding_constraint.rung_index == 6
    rung6 = next(r for r in report.rungs if r.index == 6)
    assert rung6.outcome is RungOutcome.NOT_REACHED and "deferred" in rung6.reason
    assert isinstance(rungs_with_deferred_holdout()[-1], DeferredHoldoutRung)
    assert inner.consumptions() == []


def test_the_full_ladder_spends_every_constituents_look(tmp_path: Path) -> None:
    pooled, index = _pool(3, rho=0.3, days=400, mu=0.6)
    inner, ledger = _registry(pooled, index, tmp_path / "h.sqlite")
    report = _wiring_ladder().run(_candidate(), pooled, registry=ledger,
                                  dataset_version_id=pooled.dataset_version_id)
    rung6 = next(r for r in report.rungs if r.index == 6)
    assert rung6.outcome in (RungOutcome.PASS, RungOutcome.FAIL)
    spent = inner.consumptions()
    assert sorted(c.dataset_version_id for c in spent) == sorted(s.dataset_version_id for s in pooled.sleeves)
    assert {c.strategy_content_hash for c in spent} == {"hash_pooled_test"}
    again = _wiring_ladder().run(_candidate(), pooled, registry=ledger,
                                 dataset_version_id=pooled.dataset_version_id)
    assert next(r for r in again.rungs if r.index == 6).outcome is RungOutcome.ERROR


# --------------------------------------------------------------------------
# Trial accounting, the plan and the filing
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plan():
    return _script().build_plan()


def test_the_plan_and_the_trial_accounting_equal_the_filing(plan) -> None:
    e4 = _script()
    candidates, meta = plan
    filed = json.loads(PREREG.read_text(encoding="utf-8"))
    cs = filed["candidate_set"]
    assert meta["n_candidates"] == cs["n_candidates"] == 172
    assert meta["by_tier_and_timeframe"] == {
        "seed:H4": 6, "seed:D1": 5, "generated:H4": 100, "generated:D1": 61,
    } == {k: cs["by_tier_and_timeframe"][k] for k in meta["by_tier_and_timeframe"]}
    assert meta["sleeves_by_timeframe"] == {"H4": cs["sleeves"]["H4"], "D1": cs["sleeves"]["D1"]}
    assert meta["planned_trials"] == cs["planned_trials"] == 1064
    assert meta["prior_trials"] == 20_904
    assert meta["true_trial_count"] == cs["true_trial_count"] == 20_904 + 1064
    assert meta["generated_manifest_sha256"] in cs["rule"]
    # each pooled grid point is ONE trial; every candidate is charged the rest
    for c in candidates:
        assert e4.external_trials(meta["planned_trials"], c.grid_points) == 20_904 + 1064 - c.grid_points
    assert e4.external_trials(1064, 8) == 21_960
    assert [c.tier for c in candidates[:11]] == ["seed"] * 11
    assert all(c.tier == "generated" for c in candidates[11:])
    assert all(len(c.instruments) >= 2 for c in candidates)


def test_the_filing_names_what_the_code_runs() -> None:
    e4 = _script()
    doc = json.loads(PREREG.read_text(encoding="utf-8"))
    assert doc["schema"] == "fiboki-preregistration:1" and doc["id"] == "pooled_universe_e4"
    assert doc["status"].startswith("FILED 2026-10-10")
    assert doc["universe"]["series"] == list(e4.gps.K5_UNIVERSE)
    assert doc["universe"]["timeframes"] == list(e4.TIMEFRAMES)
    assert PRODUCTION_GATE_SET.fingerprint() in doc["code"]["gate_set"]
    assert GATE_SET_V2.fingerprint() in doc["code"]["reference_gate_set"]
    assert doc["p0"]["injected_sharpe_grid"] == list(e4.SR_GRID)
    assert doc["p0"]["replicates"] == 400
    assert doc["p0"]["decision_rule_parameters"] == {
        "size_max": 0.05, "z_variance_max": 1.25, "min_replicates": 400, "power_target_at_0_08": 0.5,
    }
    assert len(doc["effective_sample_size"]["where_N_eff_replaces_N"]) == 3
    assert doc["filed_at"].startswith("2026-10-10")


def test_the_filed_commit_carries_the_filing() -> None:
    """Recorded in the commit after the filing, as E-1 and E-2 did; once set, the
    named commit must exist and must already hold this file with FILED status."""
    doc = json.loads(PREREG.read_text(encoding="utf-8"))
    sha = doc["filed_commit"]
    if sha is None:
        pytest.skip("filed_commit is recorded in the commit after the filing")
    shown = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{sha}:research/preregistration/pooled_universe_e4.json"],
        capture_output=True, text=True, check=False,
    )
    if shown.returncode != 0 and "not a git repository" in shown.stderr:
        pytest.skip("not a git checkout")
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["status"].startswith("FILED 2026-10-10")


# --------------------------------------------------------------------------
# P0's joint bootstrap, on a synthetic source (no bars)
# --------------------------------------------------------------------------


def _synthetic_source(e4, m: int = 4, days: int = 900, k: int = 160):
    rng = np.random.default_rng(21)
    start = pd.Timestamp("2010-01-04", tz="UTC").value
    common = rng.standard_normal(days)
    sleeves = []
    for i in range(m):
        day = np.sort(rng.integers(0, days, k))
        cnt = np.bincount(day, minlength=days)
        first = np.concatenate([[0], np.cumsum(cnt)[:-1]])
        ret = 0.01 * (0.6 * common[day] + rng.standard_normal(k)) + 0.002 * i
        sleeves.append(e4.SleeveSource(
            f"S{i}", day, rng.integers(0, NS_DAY // 2, k), rng.integers(NS_DAY // 6, 3 * NS_DAY, k), ret,
            {c: np.full(k, 1e-4 * (i + 1)) for c in e4.COST_FIELDS}, rng.random(k) < 0.5,
            np.array(["take_profit"] * k, dtype=object), first, cnt,
        ))
    demeaned = np.concatenate([s.ret - s.mean for s in sleeves])
    daily = np.zeros(days)
    for s in sleeves:
        np.add.at(daily, s.entry_day, s.ret - s.mean)
    return e4.PooledSource("synthetic", sleeves, start, days, e4.gps.block_length_for(daily),
                           float(demeaned.std()), "f" * 64), start


def test_the_joint_bootstrap_moves_every_sleeve_by_the_same_day(monkeypatch) -> None:
    e4 = _script()
    src, start = _synthetic_source(e4)
    rep = e4.PooledBootstrapReplicate(src, 5, start, start + 1100 * NS_DAY, H4)
    days = rep.days(0)
    for i, s in enumerate(src.sleeves):
        col = rep.column(0, i)
        sim_day = (col["entry_ns"] - start) // NS_DAY
        # each copied trade sits on the simulated day whose source day it came from
        np.testing.assert_array_equal(s.entry_day[col["idx"]], days[sim_day])
    assert not np.array_equal(rep.days(0), rep.days(1))  # grid points draw their own days too
    mixed = float(np.mean(rep.days(1) == rep.days(-1)))
    assert 0.75 < mixed < 0.95  # sqrt(0.7) = 0.837 of days are the common draw


def test_the_bootstrap_demeans_each_sleeve_and_injects_sr_exactly() -> None:
    e4 = _script()
    src, start = _synthetic_source(e4)
    end = start + 1100 * NS_DAY
    rep = e4.PooledBootstrapReplicate(src, 5, start, end, H4)
    bars = pd.DatetimeIndex(np.arange(start, end, H4, dtype=np.int64), tz="UTC")
    grid, index, distance = e4.gps.bootstrap_grid()
    w = DateWindow("w", bars[0], bars[-1])
    for sr in (0.0, 0.08):
        for i, s in enumerate(src.sleeves):
            ev = e4.BootstrapSleeveEvaluator(rep, i, sr, index, distance, bars, "v")(dict(e4.gps.BOOTSTRAP_DEFAULTS), w)
            col = rep.column(index[params_key(e4.gps.BOOTSTRAP_DEFAULTS)], i)
            k = len(ev.trades)
            expect = (s.ret[col["idx"][:k]] - s.mean + sr * src.sd_pooled) * e4.gps.NOTIONAL
            np.testing.assert_allclose([t.net_pnl for t in ev.trades], expect)
            assert {round(t.spread_cost / e4.gps.NOTIONAL, 12) for t in ev.trades} == {round(1e-4 * (i + 1), 12)}
            assert ev.returns.sum() == pytest.approx(ev.net_profit)


def test_p0_rows_are_deterministic_and_carry_the_filed_diagnostics() -> None:
    e4 = _script()
    src, start = _synthetic_source(e4, m=3, days=700, k=220)

    class Study(e4.P0Study):
        def span(self, source):  # the synthetic source has no bars
            return start, start + 800 * NS_DAY, H4

    cfg = LadderConfig(min_trades=150, walk_forward_folds=3, spa_bootstraps=30, stress_samples=5,
                       external_trial_count=21_960)
    study = Study("H4", [src], [], [], {}, None, cfg, 1)
    one = study.replicate_rows(0, [0.0, 0.3])
    two = study.replicate_rows(0, [0.0, 0.3])
    assert one == two
    for row in one:
        p = row["pooled"]
        assert p["n_trades_effective"] == pytest.approx(p["n_trades_raw"] / p["deff"])
        assert set(row["under"]) == {PRODUCTION_GATE_SET.version, GATE_SET_V2.version}
        if p.get("z_effective") is not None:
            assert p["z_effective"] == pytest.approx(p["per_trade_sharpe"] * math.sqrt(p["n_trades_effective"]))
    diag = e4.p0_diagnostics(one, 172)
    assert diag["family_size_candidates"] == 172
    assert "effective" in diag["z_calibration_at_sr_0"]


def test_the_holdout_choice_follows_the_filed_rule() -> None:
    e4 = _script()
    rows = [
        {"index": 0, "tier": "seed", "cleared_rungs_0_to_5": True, "gate_values": {"deflated_sharpe": 0.96}},
        {"index": 1, "tier": "seed", "cleared_rungs_0_to_5": True, "gate_values": {"deflated_sharpe": 0.99}},
        {"index": 2, "tier": "generated", "cleared_rungs_0_to_5": True, "gate_values": {"deflated_sharpe": 0.999}},
        {"index": 3, "tier": "seed", "cleared_rungs_0_to_5": False, "gate_values": {"deflated_sharpe": 1.0}},
    ]
    doc = {"stage": "main", "partial": False, "shard": {"start": 0, "end": 4, "of": 4},
           "plan": {"n_candidates": 4}, "runs": rows}
    assert e4.choose_holdout_candidate(doc)["index"] == 1
    assert e4.choose_holdout_candidate({**doc, "runs": rows[2:]}) is None
    with pytest.raises(ValueError):
        e4.choose_holdout_candidate({**doc, "partial": True})
    with pytest.raises(ValueError):
        e4.choose_holdout_candidate({**doc, "shard": {"start": 0, "end": 3, "of": 4}})


def _filed_p0_doc(e4, replicates: int = 400) -> dict[str, Any]:
    """A merged P0 file exactly as the filed run writes it (headers only)."""
    grid = list(e4.SR_GRID)
    return {
        "stage": "p0", "partial": False, "timeframe": "H4", "evidence": True,
        "gate_set_fingerprint": PRODUCTION_GATE_SET.fingerprint(),
        "plan": {"true_trial_count": 21_968},
        "shard": {"start": 0, "end": replicates, "of": replicates},
        "config": {"replicates": replicates, "sr_grid": grid, "seed": e4.BASE_SEED,
                   "external_trial_count": 21_960},
        "runs": [{"sr": sr, "replicate": r} for r in range(replicates) for sr in grid],
        "summary": {"0": {"rate": 0.0}},
        "e4": {"z_calibration_at_sr_0": {"effective": {"variance": 1.1}}},
    }


def test_p0_admits_the_filed_run_and_only_the_filed_run() -> None:
    """The admission gate is what protects the filing: it must read the WHOLE filed
    run, not a shard, a different edge grid, seed, gate set or trial count."""
    e4 = _script()
    filing = json.loads(PREREG.read_text(encoding="utf-8"))
    p0 = _filed_p0_doc(e4)
    assert e4.p0_admits(p0, "H4", filing) == (True, "P0 admits")
    assert e4.p0_admits(_filed_p0_doc(e4, 800), "H4", filing)[0]  # the replicate rule's doubling
    refused = {
        "timeframe": {**p0, "timeframe": "D1"},
        "size": {**p0, "summary": {"0": {"rate": 0.06}}},
        "z": {**p0, "e4": {"z_calibration_at_sr_0": {"effective": {"variance": 1.3}}}},
        "evidence": {**p0, "evidence": False},
        "partial": {**p0, "partial": True},
        "a completed shard": {**p0, "shard": {"start": 0, "end": 100, "of": 400}},
        "a residue shard": {**p0, "shard": {"k": 0, "n": 4, "of": 400}},
        "missing rows": {**p0, "runs": p0["runs"][:-1]},
        "edge grid": {**p0, "config": {**p0["config"], "sr_grid": [0.0, 0.5]}},
        "seed": {**p0, "config": {**p0["config"], "seed": 7}},
        "replicates": {**p0, "config": {**p0["config"], "replicates": 200},
                       "shard": {"start": 0, "end": 200, "of": 200}},
        "gate set": {**p0, "gate_set_fingerprint": GATE_SET_V2.fingerprint()},
        "trial count": {**p0, "plan": {"true_trial_count": 20_904}},
        "external trials": {**p0, "config": {**p0["config"], "external_trial_count": 1_056}},
    }
    for why, doc in refused.items():
        ok, reason = e4.p0_admits(doc, "H4", filing)
        assert not ok, (why, reason)


def test_an_evidence_run_may_use_no_setting_the_filing_does_not_fix(tmp_path: Path) -> None:
    e4 = _script()
    import argparse

    def ns(stage: str, **over: Any) -> argparse.Namespace:
        base = {"starter": False, "data_root": tmp_path, "instruments": None, "bars_from": None,
                "folds": LadderConfig().walk_forward_folds, "spa_bootstraps": LadderConfig().spa_bootstraps,
                "stress_samples": LadderConfig().stress_samples, "seed": e4.BASE_SEED,
                "sr": list(e4.SR_GRID), "replicates": 400}
        return argparse.Namespace(**{**base, **over})

    for stage in ("p0", "main", "holdout"):
        e4.require_filed_configuration(ns(stage), stage)  # the filed configuration passes
    e4.require_filed_configuration(ns("p0", replicates=800), "p0")  # the replicate rule's doubling
    e4.require_filed_configuration(ns("p0", starter=True, data_root=None, seed=7, folds=2), "p0")  # harness
    for stage, over in (
        ("p0", {"sr": [0.0, 0.5]}), ("p0", {"replicates": 200}), ("p0", {"seed": 7}),
        ("main", {"folds": 2}), ("main", {"spa_bootstraps": 30}), ("main", {"stress_samples": 5}),
        ("main", {"instruments": ["EURUSD", "GBPUSD"]}), ("main", {"bars_from": "2010-01-01"}),
        ("holdout", {"seed": 1}),
    ):
        with pytest.raises(SystemExit, match="filed configuration"):
            e4.require_filed_configuration(ns(stage, **over), stage)
