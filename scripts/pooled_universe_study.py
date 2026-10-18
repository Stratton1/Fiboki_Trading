#!/usr/bin/env python
"""E-4: pooled-universe candidates, measured (P0) and then run (main stage).

Pre-registration: ``research/preregistration/pooled_universe_e4.json``. Read it
first. This script is the instrument that file describes; nothing it prints may
be read as a result unless the file is FILED and the run is the one it names.

A pooled candidate is ONE strategy document evaluated on every series of its
universe (K5's sixteen OANDA MID series) by the existing engine path, one
equal-capital sleeve per instrument, P&L summed, with every count-based gate
reading the effective trade count ``N / DEFF``
(:mod:`fiboki.validation.pooled`). The gates are the production set
(``v2.1.0-calibrated``) unchanged.

Subcommands
-----------
``plan``
    The main stage's candidate list and trial accounting, from the documents
    alone (no bars). ``--check`` compares them with the filing.

``p0`` (process P0, runs FIRST)
    Size and power of the pooled procedure. Per replicate one seed document's
    real default-binding trades on every sleeve of its universe (research window
    only, fresh engine runs) are resampled by a JOINT stationary bootstrap of
    calendar days: every sleeve copies the trades of the SAME source day, so the
    cross-instrument dependence the effective count must correct for is kept.
    Each sleeve is demeaned (no edge anywhere) and the pooled per-trade Sharpe
    ``sr`` injected. The pooled candidate goes through the measuring ladder,
    judged under the production set (primary) and ``v2.0.0-audit`` (reference).

``main`` (main stage, only after P0)
    Every pooled candidate (document x timeframe), through the fail-fast ladder
    under the production set, rungs 0 to 5. Rung 6 is DEFERRED: nothing here
    claims or reads a holdout. Every report is written, pass or fail.

``holdout``
    The one look, for the single candidate the filing's rule chooses (a SEED
    document that cleared rungs 0 to 5, highest deflated Sharpe), only if P0 for
    its timeframe met the size bound and the calibration check. It refuses
    otherwise, and refuses if any constituent's holdout was already spent.

``merge``
    Shards of ``p0`` or ``main`` into one file; refuses gaps, overlaps,
    partial checkpoints and mismatched identity (as ``gate_power_study.py``).

Shards and resume work as in ``scripts/gate_power_study.py``: ``--replicate-range``
(``p0``) or ``--candidate-range`` (``main``) runs a shard, ``--resume`` continues
from the checkpoint at ``--out`` (written at most once a minute), and ``merge``
joins shards that tile the range exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_power_study as gps

from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    WindowEvaluation,
    params_key,
)
from fiboki.validation.gates import (
    GATE_SET_V2,
    PRODUCTION_GATE_SET,
    GateSet,
    sanity_trade_floor,
)
from fiboki.validation.holdout import DEFAULT_HOLDOUT_FRACTION, HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder, default_rungs
from fiboki.validation.pooled import (
    PooledEvaluator,
    PooledHoldoutRegistry,
    PooledSleeve,
    rungs_with_deferred_holdout,
)

ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION = "research/preregistration/pooled_universe_e4.json"
STUDY = "E-4"
GENERATED_DIR = ROOT / "research" / "generated"
GENERATED_MANIFEST = "MANIFEST.json"
#: Every trial run on these sixteen series before E-4: K5's true trial count,
#: which contains K4's 8,496, K3's 7,376 and the 4,026 before them
#: (research/reports/RESEARCH_LEDGER.md, K5 result).
PRIOR_TRIALS = 20_904
TIMEFRAMES = ("H4", "D1")
SR_GRID = (0.0, 0.03, 0.05, 0.08, 0.12)
#: The filed base seed (data_rules); an evidence run uses no other.
BASE_SEED = 20_261_010
#: The filed replicate counts: 400, or 800 once under the filing's replicate_rule
#: (size within 2 Monte Carlo SE of 0.05 doubles that timeframe's replicates once).
FILED_REPLICATES = (400, 800)
#: The K5 cell shape: grid capped at 8 points, 2 values per axis.
MAX_GRID_POINTS = 8
MAX_VALUES_PER_AXIS = 2
#: The production set, judged as the primary verdict; the audited set beside it.
JUDGED: dict[str, GateSet] = {
    PRODUCTION_GATE_SET.version: PRODUCTION_GATE_SET,
    GATE_SET_V2.version: GATE_SET_V2,
}
NS_PER_DAY = 86_400 * 1_000_000_000
CHECKPOINT_SECONDS = 60.0
#: A main-stage candidate needs this many sleeves to be a pool at all.
MIN_SLEEVES = 2


# --------------------------------------------------------------------------
# Documents and the plan
# --------------------------------------------------------------------------


def generated_documents(path: Path = GENERATED_DIR) -> tuple[list[Any], str]:
    """K5's generated roster, ordered by id, and its manifest sha256."""
    from fiboki.strategy.dsl import StrategyDocument

    docs = sorted(
        (
            StrategyDocument.from_json(p.read_text(encoding="utf-8"))
            for p in sorted(path.glob("*.json"))
            if p.name != GENERATED_MANIFEST
        ),
        key=lambda d: d.strategy_id,
    )
    manifest = path / GENERATED_MANIFEST
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else "absent"
    return docs, digest


def _root_id(document: Any) -> str:
    """As ``discovery.campaign._root_id``: the seed a document descends from."""
    parents = tuple(getattr(document, "parent_strategy_ids", ()) or ())
    return parents[0] if parents else document.strategy_id


def sweep_axes_for(document: Any, axes: Mapping[str, tuple[str, ...]]) -> tuple[str, ...] | None:
    """The axes K5 swept for this document (``run_cell``'s lookup)."""
    return axes.get(document.strategy_id) or axes.get(_root_id(document))


@dataclass(frozen=True, slots=True)
class PlannedCandidate:
    index: int
    tier: str  # "seed" or "generated"
    document: Any = field(repr=False)
    timeframe: str
    instruments: tuple[str, ...]
    grid_points: int

    @property
    def key(self) -> str:
        return f"{self.document.strategy_id}@{self.timeframe}"

    def describe(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "key": self.key,
            "tier": self.tier,
            "strategy_id": self.document.strategy_id,
            "timeframe": self.timeframe,
            "instruments": list(self.instruments),
            "grid_points": self.grid_points,
            "default_binding_content_hash": self.document.bind_defaults().content_hash(),
        }


def build_plan(
    universe: Sequence[str] = gps.K5_UNIVERSE,
    *,
    seed_ids: Sequence[str] | None = None,
    include_generated: bool = True,
    generated_dir: Path = GENERATED_DIR,
) -> tuple[list[PlannedCandidate], dict[str, Any]]:
    """The main stage's candidates, in the filed order: seeds first, then generated;
    within a tier by strategy id, H4 before D1. A document x timeframe whose
    universe holds fewer than two of the series is not a pool and is not planned."""
    from fiboki.core.enums import Timeframe

    axes = gps.campaign_sweep_axes()
    seeds = gps.load_documents(seed_ids)
    tiers: list[tuple[str, list[Any]]] = [("seed", seeds)]
    manifest = None
    if include_generated:
        gen, manifest = generated_documents(generated_dir)
        tiers.append(("generated", gen))
    plan: list[PlannedCandidate] = []
    not_pools: list[dict[str, Any]] = []
    for tier, docs in tiers:
        for doc in docs:
            for tf_name in TIMEFRAMES:
                tf = Timeframe(tf_name)
                if tf not in doc.timeframes:
                    continue
                inst = tuple(s for s in sorted(universe) if s in doc.universe)
                if len(inst) < MIN_SLEEVES:
                    not_pools.append({"key": f"{doc.strategy_id}@{tf_name}", "instruments": list(inst)})
                    continue
                grid = Candidate.from_document(
                    doc, max_points=MAX_GRID_POINTS, max_values_per_axis=MAX_VALUES_PER_AXIS,
                    include=sweep_axes_for(doc, axes),
                ).grid
                plan.append(PlannedCandidate(len(plan), tier, doc, tf_name, inst, len(grid.points())))
    planned = sum(c.grid_points for c in plan)
    meta = {
        "universe": list(universe),
        "timeframes": list(TIMEFRAMES),
        "n_candidates": len(plan),
        "by_tier_and_timeframe": {
            f"{t}:{tf}": sum(1 for c in plan if c.tier == t and c.timeframe == tf)
            for t in ("seed", "generated") for tf in TIMEFRAMES
        },
        "planned_trials": planned,
        "prior_trials": PRIOR_TRIALS,
        "true_trial_count": PRIOR_TRIALS + planned,
        "sleeves_by_timeframe": {
            tf: sum(len(c.instruments) for c in plan if c.timeframe == tf) for tf in TIMEFRAMES
        },
        "generated_manifest_sha256": manifest,
        "not_pools": not_pools,
    }
    return plan, meta


def external_trials(planned_total: int, own_grid_points: int) -> int:
    """Every trial on these bars except the candidate's own grid: K5's true N plus
    every pooled trial E-4 plans, less this candidate's."""
    return PRIOR_TRIALS + int(planned_total) - int(own_grid_points)


# --------------------------------------------------------------------------
# P0: the joint calendar-day bootstrap of real sleeve trades
# --------------------------------------------------------------------------

#: Per-trade record columns (fractions of the sleeve's equity at entry).
COST_FIELDS = ("spread", "slippage", "financing", "commission")


@dataclass(slots=True)
class SleeveSource:
    """One sleeve's real default-binding trades on the common research window."""

    instrument: str
    entry_day: np.ndarray = field(repr=False)
    intraday_ns: np.ndarray = field(repr=False)
    hold_ns: np.ndarray = field(repr=False)
    ret: np.ndarray = field(repr=False)
    costs: dict[str, np.ndarray] = field(repr=False)
    long: np.ndarray = field(repr=False)
    exit_reason: np.ndarray = field(repr=False)
    by_day_start: np.ndarray = field(repr=False)
    by_day_count: np.ndarray = field(repr=False)
    cell: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.ret.size)

    @property
    def mean(self) -> float:
        return float(self.ret.mean()) if self.n else 0.0


@dataclass(slots=True)
class PooledSource:
    """One seed document's sleeves, ready to be resampled jointly by day."""

    strategy_id: str
    sleeves: list[SleeveSource]
    src_start_ns: int
    n_src_days: int
    block_length: dict[str, Any]
    sd_pooled: float
    digest: str

    def describe(self) -> dict[str, Any]:
        n = sum(s.n for s in self.sleeves)
        return {
            "strategy_id": self.strategy_id,
            "n_sleeves": len(self.sleeves),
            "n_trades": n,
            "n_source_days": self.n_src_days,
            "block_length_days": self.block_length,
            "sd_pooled_demeaned": self.sd_pooled,
            "source_sha256": self.digest,
            "sleeves": [
                {**s.cell, "n_trades": s.n, "mean_ret": s.mean} for s in self.sleeves
            ],
        }


def _sleeve_source(
    document: Any, series: Any, window: DateWindow, calendar: Any, src_start_ns: int
) -> SleeveSource:
    """Fresh engine run of the default binding over the COMMON research window."""
    engine = gps._evaluator_for(
        document, series, series.frame, series.dataset_version_id, calendar, series.price_lineage
    )
    ev = engine(document.default_values(), window)
    bars = gps._window_bars(series.frame.index, window)
    if len(bars) != ev.returns.size:
        raise RuntimeError(f"{document.strategy_id} {series.instrument}: returns do not align with bars")
    opening = float(ev.meta.get("opening_equity", gps.NOTIONAL))
    equity_after = opening + np.cumsum(ev.returns)
    trades = sorted(ev.trades, key=lambda t: (t.entry_time, t.exit_time))
    entry_ns = np.array([t.entry_time.value for t in trades], dtype=np.int64)
    rows: dict[str, list[float]] = {"ret": [], **{c: [] for c in COST_FIELDS}}
    for t in trades:
        pos = int(bars.searchsorted(t.entry_time, side="left")) - 1
        equity = opening if pos < 0 else float(equity_after[pos])
        if equity <= 0:
            raise RuntimeError(f"{document.strategy_id} {series.instrument}: non-positive equity")
        rows["ret"].append(float(t.net_pnl) / equity)
        rows["spread"].append(float(t.spread_cost) / equity)
        rows["slippage"].append(float(t.slippage_cost) / equity)
        rows["financing"].append(float(t.financing_cost) / equity)
        rows["commission"].append(float(t.commission) / equity)
    rel = entry_ns - src_start_ns
    day = (rel // NS_PER_DAY).astype(np.int64)
    n_days = int(-(-(window.end.value - src_start_ns) // NS_PER_DAY))
    count = np.bincount(day, minlength=n_days).astype(np.int64) if day.size else np.zeros(n_days, np.int64)
    start = np.concatenate([[0], np.cumsum(count)[:-1]]).astype(np.int64)
    return SleeveSource(
        instrument=series.instrument,
        entry_day=day,
        intraday_ns=(rel - day * NS_PER_DAY).astype(np.int64),
        hold_ns=np.array([(t.exit_time - t.entry_time).value for t in trades], dtype=np.int64),
        ret=np.asarray(rows["ret"], dtype=float),
        costs={c: np.asarray(rows[c], dtype=float) for c in COST_FIELDS},
        long=np.array([t.direction.value == "long" for t in trades], dtype=bool),
        exit_reason=np.array([t.exit_reason.value for t in trades], dtype=object),
        by_day_start=start,
        by_day_count=count,
        cell={
            "instrument": series.instrument,
            "dataset_version_id": series.dataset_version_id,
            "strategy_content_hash": engine.bind(document.default_values()).content_hash(),
            "engine_config_hash": engine.engine_config_hash(),
            "ledger_sha256": ev.meta.get("ledger_sha256"),
            "window": window.to_dict(),
        },
    )


def build_pooled_source(document: Any, series: Sequence[Any], calendar: Any) -> PooledSource:
    """Every sleeve's real trades on the research window COMMON to all of them."""
    start = max(s.research_window.start for s in series)
    end = min(s.research_window.end for s in series)
    window = DateWindow("e4_p0_source", start, end)
    src_start = int(start.value // NS_PER_DAY * NS_PER_DAY)
    sleeves = [_sleeve_source(document, s, window, calendar, src_start) for s in series]
    n_days = int(sleeves[0].by_day_count.size)
    daily = np.zeros(n_days, dtype=float)
    for s in sleeves:
        np.add.at(daily, s.entry_day, s.ret - s.mean)
    demeaned = np.concatenate([s.ret - s.mean for s in sleeves]) if sleeves else np.zeros(0)
    if demeaned.size < 8 or float(demeaned.std()) == 0.0:
        raise ValueError(f"{document.strategy_id}: {demeaned.size} pooled trades; a bootstrap needs 8 that move")
    digest = hashlib.sha256(document.strategy_id.encode("utf-8"))
    for s in sleeves:
        digest.update(s.instrument.encode("utf-8"))
        for arr in (s.entry_day, s.intraday_ns, s.hold_ns, s.ret, *s.costs.values()):
            digest.update(np.ascontiguousarray(arr).tobytes())
    return PooledSource(
        strategy_id=document.strategy_id,
        sleeves=sleeves,
        src_start_ns=src_start,
        n_src_days=n_days,
        block_length=gps.block_length_for(daily),
        sd_pooled=float(demeaned.std(ddof=0)),
        digest=digest.hexdigest(),
    )


@dataclass(slots=True)
class PooledBootstrapReplicate:
    """One simulated dataset: a day sequence shared by EVERY sleeve, per grid point.

    Simulated day ``k`` copies source day ``D[k]`` for every sleeve at once, so
    trades that coincided in real time coincide in the simulation and the
    cross-sleeve dependence is the data's own. Grid point ``j`` takes the common
    day with probability ``sqrt(CROSS_GRID_CORRELATION)`` and its own stationary
    draw otherwise (per day, for all sleeves together): two grid points then
    correlate at about ``CROSS_GRID_CORRELATION`` as in E-1 process 1.
    """

    source: PooledSource
    seed: int
    sim_start_ns: int
    sim_end_ns: int
    step_ns: int
    _days: dict[int, np.ndarray] = field(init=False, default_factory=dict, repr=False)
    _cols: dict[tuple[int, int], dict[str, np.ndarray]] = field(init=False, default_factory=dict, repr=False)

    @property
    def n_sim_days(self) -> int:
        return int(-(-(self.sim_end_ns - self.sim_start_ns) // NS_PER_DAY))

    def _stream(self, *label: object) -> Any:
        return gps.StationaryStream(
            self.source.n_src_days,
            float(self.source.block_length["stationary"]),
            np.random.default_rng(gps._seed(self.seed, *label)),
        )

    def days(self, j: int) -> np.ndarray:
        if j not in self._days:
            if -1 not in self._days:
                self._days[-1] = self._stream("common").draw(self.n_sim_days)
            if j == -1:
                return self._days[-1]
            own = self._stream("idio", j).draw(self.n_sim_days)
            mix = np.random.default_rng(gps._seed(self.seed, "mix", j)).random(self.n_sim_days)
            self._days[j] = np.where(mix < math.sqrt(gps.CROSS_GRID_CORRELATION), self._days[-1], own)
        return self._days[j]

    def column(self, j: int, i: int) -> dict[str, np.ndarray]:
        """Grid point ``j``'s simulated trades on sleeve ``i``, by entry time."""
        key = (j, i)
        if key not in self._cols:
            s = self.source.sleeves[i]
            d = self.days(j)
            counts = s.by_day_count[d]
            total = int(counts.sum())
            sim_day = np.repeat(np.arange(d.size, dtype=np.int64), counts)
            first = np.repeat(s.by_day_start[d], counts)
            within = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(counts) - counts, counts)
            idx = (first + within).astype(np.int64)
            entry = self.sim_start_ns + sim_day * NS_PER_DAY + s.intraday_ns[idx]
            keep = entry < self.sim_end_ns
            idx, entry = idx[keep], entry[keep]
            self._cols[key] = {"idx": idx, "entry_ns": entry, "exit_ns": entry + s.hold_ns[idx]}
        return self._cols[key]


@dataclass(slots=True)
class BootstrapSleeveEvaluator:
    """One sleeve of a :class:`PooledBootstrapReplicate`, with the edge injected.

    Per-trade return = resampled return - the SLEEVE's mean (no edge anywhere)
    + ``plateau_factor(d) * sr * sd_pooled``, so the POOLED per-trade Sharpe at
    the defaults is ``sr``. P&L on a non-compounding GBP 10,000 sleeve, spread
    evenly over the bars the trade is held (a linear mark-to-market: the
    engine's per-bar equity changes over the holding period too, and the
    effective count is measured from per-period co-movement). Approximation,
    stated: the intrabar and intratrade path is unknown and linear here. A trade
    still open at the window's end is closed inside it with its whole return.
    """

    replicate: PooledBootstrapReplicate
    sleeve: int
    sr: float
    grid_index: dict[str, int]
    distances: dict[str, int]
    bar_index: pd.DatetimeIndex = field(repr=False)
    dataset_version_id: str = ""

    def __call__(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        from fiboki.core.contracts import Trade
        from fiboki.core.enums import Direction, ExitReason

        rep = self.replicate
        src = rep.source
        s = src.sleeves[self.sleeve]
        key = params_key(params)
        col = rep.column(self.grid_index[key], self.sleeve)
        shift = self.sr * gps.plateau_factor(self.distances[key]) * src.sd_pooled
        bars = self.bar_index.asi8
        lo = int(np.searchsorted(bars, window.start.value, side="left"))
        hi = int(np.searchsorted(bars, window.end.value, side="left"))
        n_bars = hi - lo
        if n_bars < 1:
            raise ValueError(f"window {window} holds no simulated bars")
        a = int(np.searchsorted(col["entry_ns"], window.start.value, side="left"))
        b = int(np.searchsorted(col["entry_ns"], window.end.value, side="left"))
        idx = col["idx"][a:b]
        entry = col["entry_ns"][a:b]
        exit_ = np.minimum(col["exit_ns"][a:b], window.end.value - 1)
        ret = s.ret[idx] - s.mean + shift
        pnl = ret * gps.NOTIONAL
        eb = np.searchsorted(bars, entry, side="right") - 1 - lo
        xb = np.searchsorted(bars, exit_, side="right") - 1 - lo
        eb = np.clip(eb, 0, n_bars - 1)
        xb = np.clip(np.maximum(xb, eb), 0, n_bars - 1)
        per = pnl / (xb - eb + 1)
        diff = np.zeros(n_bars + 1, dtype=float)
        np.add.at(diff, eb, per)
        np.add.at(diff, xb + 1, -per)
        returns = np.cumsum(diff[:-1])
        trades = []
        for k in range(idx.size):
            r = int(idx[k])
            costs = {c: float(s.costs[c][r]) * gps.NOTIONAL for c in COST_FIELDS}
            net = float(pnl[k])
            trades.append(Trade(
                instrument=s.instrument,
                direction=Direction.LONG if bool(s.long[r]) else Direction.SHORT,
                size=1.0, entry_price=1.0, exit_price=1.0 + float(ret[k]),
                entry_time=pd.Timestamp(int(entry[k]), tz="UTC"),
                exit_time=pd.Timestamp(int(max(entry[k], exit_[k])), tz="UTC"),
                exit_reason=ExitReason(str(s.exit_reason[r])),
                gross_pnl=net + sum(costs.values()), spread_cost=costs["spread"],
                commission=costs["commission"], slippage_cost=costs["slippage"],
                financing_cost=costs["financing"], net_pnl=net, account_ccy="GBP",
                strategy_id="e4_p0", bars_held=int(xb[k] - eb[k] + 1),
            ))
        return WindowEvaluation(
            window=window, params=dict(params), n_trades=len(trades),
            net_profit=float(pnl.sum()), returns=returns, returns_basis="period",
            trades=tuple(trades),
            meta={
                "opening_equity": gps.NOTIONAL,
                "dataset_version_id": self.dataset_version_id,
                "strategy_content_hash": "e4_p0",
            },
        )


def _ladder_config(args: argparse.Namespace, external: int) -> LadderConfig:
    return LadderConfig(
        min_trades=sanity_trade_floor(PRODUCTION_GATE_SET),
        walk_forward_folds=args.folds,
        spa_bootstraps=args.spa_bootstraps,
        stress_samples=args.stress_samples,
        external_trial_count=int(external),
        seed=args.seed,
    )


def _rung0(measured: Sequence[Any]) -> dict[str, Any]:
    rung = next((r for r in measured if r.index == 0), None)
    return dict(rung.metrics) if rung is not None else {}


def pooled_diagnostics(rung0: Mapping[str, Any]) -> dict[str, Any]:
    """The defaults' pooled count, effective count, design effect and z-statistics."""
    basis = rung0.get("min_trl_basis") or {}
    n_raw = rung0.get("n_trades")
    n_eff = rung0.get("n_trades_effective", n_raw)
    sr_hat = basis.get("per_trade_sharpe")
    deff = rung0.get("design_effect") or {}
    min_trl = rung0.get("min_trl_95")
    out: dict[str, Any] = {
        "n_trades_raw": n_raw,
        "n_trades_effective": n_eff,
        "deff": deff.get("deff"),
        "deff_basis": deff.get("basis"),
        "per_trade_sharpe": sr_hat,
        "min_trl_95": min_trl,
    }
    if sr_hat is not None and n_raw and n_eff:
        out["z_effective"] = float(sr_hat) * math.sqrt(float(n_eff))
        out["z_naive"] = float(sr_hat) * math.sqrt(float(n_raw))
    if min_trl is not None and n_raw:
        floor = max(float(min_trl), 150.0)
        out["n_over_min_trl_naive"] = float(n_raw) / floor
        out["n_over_min_trl_effective"] = float(n_eff) / floor
    return out


def run_measuring_row(
    *, candidate: Candidate, evaluator: Any, registry: Any, dataset_version_id: str,
    config: LadderConfig, notes: str, sr: float, replicate: int,
) -> dict[str, Any]:
    """One pooled candidate through the MEASURING ladder; the row schema of
    ``gate_power_study._run_ladder`` (so its summaries and ``judge_rows`` apply)
    plus the pooled diagnostics."""
    recorder = gps.RecordingEvaluator(evaluator)
    measured: list[Any] = []
    reports = ValidationLadder(config=config, gate_set=PRODUCTION_GATE_SET).run_measuring(
        candidate, recorder, registry=registry, dataset_version_id=dataset_version_id,
        gate_sets=JUDGED, actor="scripts/pooled_universe_study.py", notes=notes,
        measured_rungs=measured,
    )
    primary = reports[PRODUCTION_GATE_SET.version]
    row: dict[str, Any] = {"sr": sr, "replicate": replicate, "ladder_mode": "measuring"}
    row.update(gps._verdict_fields(primary, all_values=True))
    row["rung_outcomes"] = {str(r.index): r.outcome.value for r in measured}
    row["rung_labels"] = {str(r.index): r.label for r in measured}
    row["metrics"] = {
        g.gate.metric: (None if g.value is None or not math.isfinite(g.value) else g.value)
        for report in reports.values() for g in report.gate_results
    }
    row["not_applicable"] = sorted({
        g.gate.name for report in reports.values() for g in report.gate_results
        if g.status.value == "not_applicable"
    })
    row["under"] = {name: gps._verdict_fields(reports[name], all_values=True) for name in sorted(JUDGED)}
    row["evaluations"] = recorder.calls
    row["sanity_n_trades"] = recorder.first_n_trades
    row["trade_counts"] = {k: v for k, v in sorted(recorder.trade_counts.items())}
    row["pooled"] = pooled_diagnostics(_rung0(measured))
    return row


@dataclass(slots=True)
class P0Study:
    """Process P0: everything built once and recorded."""

    timeframe: str
    sources: list[PooledSource]
    series: list[Any]
    skipped: list[dict[str, Any]]
    source_meta: dict[str, Any]
    calendar: Any
    config: LadderConfig
    base_seed: int

    @classmethod
    def build(cls, args: argparse.Namespace, config: LadderConfig) -> P0Study:
        from fiboki.core.enums import Timeframe
        from fiboki.marketstate.calendar import load_research_calendar

        tf = Timeframe(args.timeframe)
        instruments = args.instruments or (gps.STARTER_INSTRUMENTS if args.starter else gps.K5_UNIVERSE)
        bars_from = args.bars_from if args.bars_from is not None else (None if args.starter else gps.K5_BARS_FROM)
        series, skipped, meta = gps.load_series(
            starter=args.starter, data_root=args.data_root, instruments=instruments,
            timeframe=tf, bars_from=bars_from,
        )
        calendar = load_research_calendar()
        sources: list[PooledSource] = []
        for doc in gps.load_documents(args.documents):
            if tf not in doc.timeframes:
                skipped.append({"strategy_id": doc.strategy_id, "reason": f"no {tf.value} in the document"})
                continue
            sleeves = [s for s in series if s.instrument in doc.universe]
            if len(sleeves) < MIN_SLEEVES:
                skipped.append({"strategy_id": doc.strategy_id, "reason": f"{len(sleeves)} sleeve(s): not a pool"})
                continue
            try:
                sources.append(build_pooled_source(doc, sleeves, calendar))
            except ValueError as exc:
                skipped.append({"strategy_id": doc.strategy_id, "reason": str(exc)})
        if not sources:
            raise RuntimeError(f"no seed document forms a pool on these bars: {skipped}")
        return cls(tf.value, sources, series, skipped, meta, calendar, config, int(args.seed))

    def span(self, source: PooledSource) -> tuple[int, int, int]:
        """The simulated calendar: the sleeves' common full span (research + holdout)."""
        from fiboki.core.enums import Timeframe

        names = {s.instrument for s in source.sleeves}
        frames = [s.frame for s in self.series if s.instrument in names]
        step = int(Timeframe(self.timeframe).minutes) * 60 * 1_000_000_000
        start = max(int(f.index[0].value) for f in frames)
        end = min(int(f.index[-1].value) for f in frames) + step
        return start, end, step

    def describe(self) -> dict[str, Any]:
        from fiboki.validation.engine_evaluator import blackout_fingerprint

        return {
            **self.source_meta,
            "timeframe": self.timeframe,
            "series": [s.describe() for s in self.series],
            "skipped": self.skipped,
            "documents": [src.describe() for src in self.sources],
            "calendar": blackout_fingerprint(self.calendar),
            "engine": "engine_v3_realism via EngineEvaluator (GBP 10,000 per sleeve, IG_REALISTIC, "
                      "fixed_fractional_v2, research construction), default binding, research window "
                      "common to the document's sleeves",
            "resampling": "joint stationary bootstrap of calendar days, one day sequence for every "
                          "sleeve; Politis-White block length on the pooled daily demeaned return",
            "grid": {
                "axes": {k: list(v) for k, v in gps.BOOTSTRAP_AXES.items()},
                "defaults": gps.BOOTSTRAP_DEFAULTS,
                "shift_by_distance": {d: gps.plateau_factor(d) for d in range(4)},
                "cross_grid_correlation": gps.CROSS_GRID_CORRELATION,
            },
            "rotation": "replicate r uses documents[r % n_documents]",
        }

    def replicate_rows(self, replicate: int, srs: Sequence[float]) -> list[dict[str, Any]]:
        source = self.sources[replicate % len(self.sources)]
        seed = gps._seed(self.base_seed, "e4_p0", self.timeframe, "replicate", replicate)
        start, end, step = self.span(source)
        rep = PooledBootstrapReplicate(source, seed, start, end, step)
        bar_index = pd.DatetimeIndex(np.arange(start, end, step, dtype=np.int64), tz="UTC")
        grid, index, distance = gps.bootstrap_grid()
        rows = []
        for sr in srs:
            registry = HoldoutRegistry.in_memory()
            sleeves = []
            for i, s in enumerate(source.sleeves):
                vid = f"e4_p0::{self.timeframe}::{s.instrument}::{source.digest[:12]}::r{replicate}"
                registry.define(vid, data_start=pd.Timestamp(start, tz="UTC"),
                                data_end=pd.Timestamp(end - step, tz="UTC"))
                sleeves.append(PooledSleeve(
                    s.instrument,
                    BootstrapSleeveEvaluator(rep, i, sr, index, distance, bar_index, vid),
                    bar_index, vid,
                ))
            pooled = PooledEvaluator(tuple(sleeves))
            ledger = PooledHoldoutRegistry(registry, [p.dataset_version_id for p in sleeves])
            candidate = Candidate(
                strategy_id=f"e4_p0_{source.strategy_id}",
                content_hash=hashlib.sha256(
                    f"e4|p0|{self.timeframe}|{source.strategy_id}|{sr}|{replicate}".encode()
                ).hexdigest(),
                default_params=dict(gps.BOOTSTRAP_DEFAULTS), grid=grid,
            )
            row = run_measuring_row(
                candidate=candidate, evaluator=pooled, registry=ledger,
                dataset_version_id=pooled.dataset_version_id, config=self.config,
                notes=f"E-4 P0 pooled_block_bootstrap {self.timeframe}", sr=sr, replicate=replicate,
            )
            row["document"] = source.strategy_id
            row["sleeves"] = [s.instrument for s in source.sleeves]
            rows.append(row)
        return rows


def _var_with_se(values: Sequence[float]) -> dict[str, Any]:
    arr = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if arr.size < 3:
        return {"n": int(arr.size)}
    var = float(arr.var(ddof=1))
    return {"n": int(arr.size), "variance": var, "se": var * math.sqrt(2.0 / (arr.size - 1)),
            "mean": float(arr.mean())}


def p0_diagnostics(rows: Sequence[Mapping[str, Any]], n_candidates: int) -> dict[str, Any]:
    """The filed P0 metrics beyond the shared size/power summary."""
    at0 = [r for r in rows if float(r["sr"]) == 0.0]
    p0 = sum(1 for r in at0 if r["promoted"]) / len(at0) if at0 else None
    by_sr: dict[str, Any] = {}
    for sr in sorted({float(r["sr"]) for r in rows}):
        group = [r for r in rows if float(r["sr"]) == sr]
        deffs = [r["pooled"].get("deff") for r in group if r["pooled"].get("deff") is not None]
        neff = [r["pooled"].get("n_trades_effective") for r in group
                if r["pooled"].get("n_trades_effective") is not None]
        nraw = [r["pooled"].get("n_trades_raw") for r in group if r["pooled"].get("n_trades_raw") is not None]
        by_sr[f"{sr:g}"] = {
            "deff": gps._quantiles(deffs),
            "n_trades_raw": gps._quantiles(nraw),
            "n_trades_effective": gps._quantiles(neff),
        }
    naive_pass = [r for r in at0 if (r["pooled"].get("n_over_min_trl_naive") or 0.0) >= 1.0]
    eff_pass = [r for r in at0 if (r["pooled"].get("n_over_min_trl_effective") or 0.0) >= 1.0]
    return {
        "size_per_candidate": p0,
        "size_family_wise_union_bound": (
            None if p0 is None else 1.0 - (1.0 - p0) ** int(n_candidates)
        ),
        "family_size_candidates": int(n_candidates),
        "z_calibration_at_sr_0": {
            "effective": _var_with_se([r["pooled"].get("z_effective") for r in at0]),
            "naive": _var_with_se([r["pooled"].get("z_naive") for r in at0]),
            "reading": "variance of the defaults' pooled per-trade z across sr = 0 replicates; "
                       "1 when the count used is the true information, > 1 when it overstates it",
        },
        "min_trl_gate_pass_at_sr_0": {
            "effective": len(eff_pass) / len(at0) if at0 else None,
            "naive_raw_count": len(naive_pass) / len(at0) if at0 else None,
        },
        "by_sr": by_sr,
    }


# --------------------------------------------------------------------------
# Writing, resuming and merging (shared by p0 and main)
# --------------------------------------------------------------------------


def shard_indices(shard: Mapping[str, Any]) -> set[int]:
    """The replicate or candidate indices a shard declares: a range, or a residue."""
    if "k" in shard:
        return set(range(int(shard["k"]), int(shard["of"]), int(shard["n"])))
    return set(range(int(shard["start"]), int(shard["end"])))


def _write(out: Path, doc: Mapping[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, out)


def _identity(doc: Mapping[str, Any]) -> dict[str, Any]:
    ident = {k: doc.get(k) for k in ("study", "stage", "preregistration", "evidence",
                                     "gate_set_version", "gate_set_fingerprint",
                                     "judged_gate_sets", "config", "timeframe", "plan")}
    source = doc.get("source")
    if source is not None:
        ident["source"] = {k: v for k, v in source.items() if k != "data_root"}
    return json.loads(json.dumps(ident, sort_keys=True))


def p0_result(header: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], *,
              partial: bool, wall: float, build: float) -> dict[str, Any]:
    order = {sr: k for k, sr in enumerate(header["config"]["sr_grid"])}
    rows = sorted(rows, key=lambda r: (order[float(r["sr"])], int(r["replicate"])))
    return {
        **header,
        "partial": partial,
        "summary": gps.summarise(rows),
        "secondary": gps.secondary(rows),
        "summary_by_gate_set": {n: gps.summarise(gps.rows_under(rows, n)) for n in sorted(JUDGED)},
        "e4": p0_diagnostics(rows, header["config"]["family_size_candidates"]),
        "runs": list(rows),
        "wall_seconds": round(wall, 3),
        "wall_seconds_source_build": round(build, 3),
    }


def main_result(header: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], *,
                partial: bool, wall: float) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: int(r["index"]))
    run = [r for r in rows if r.get("status") == "run"]
    cleared = [r for r in run if r.get("cleared_rungs_0_to_5")]
    binding: dict[str, int] = {}
    for r in run:
        binding[str(r.get("binding"))] = binding.get(str(r.get("binding")), 0) + 1
    return {
        **header,
        "partial": partial,
        "summary": {
            "candidates_in_range": len(rows),
            "run": len(run),
            "errors": sum(1 for r in rows if r.get("status") == "error"),
            "cleared_rungs_0_to_5": [r["key"] for r in cleared],
            "binding_counts": dict(sorted(binding.items())),
            "holdout": "not read: rung 6 deferred in the main stage",
        },
        "runs": list(rows),
        "wall_seconds": round(wall, 3),
    }


def _resume(out: Path, header: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    if not out.exists():
        return []
    prior = json.loads(out.read_text(encoding="utf-8"))
    if _identity(prior) != _identity(header):
        raise ValueError(f"{out} was produced by a different run; refusing to resume from it")
    if prior.get("shard") != header.get("shard"):
        raise ValueError(f"{out} covers {prior.get('shard')}, not {header.get('shard')}")
    rows = list(prior["runs"])
    if key == "replicate":
        srs = {float(s) for s in header["config"]["sr_grid"]}
        seen: dict[int, set[float]] = {}
        for r in rows:
            seen.setdefault(int(r["replicate"]), set()).add(float(r["sr"]))
        bad = sorted(k for k, v in seen.items() if v != srs)
        if bad:
            raise ValueError(f"{out} holds incomplete replicates {bad}; not resumable")
    return rows


def merge(paths: Sequence[Path]) -> dict[str, Any]:
    if not paths:
        raise ValueError("nothing to merge")
    docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    first = docs[0]
    stage = first.get("stage")
    key, total = (
        ("replicate", int(first["config"]["replicates"])) if stage == "p0"
        else ("index", int(first["plan"]["n_candidates"]))
    )
    ident = _identity(first)
    covered: dict[int, Path] = {}
    rows: list[dict[str, Any]] = []
    wall = build = 0.0
    for path, doc in zip(paths, docs, strict=True):
        if _identity(doc) != ident:
            raise ValueError(f"{path} is not the same study as {paths[0]} (identity differs)")
        if doc.get("partial"):
            raise ValueError(f"{path} is a partial checkpoint; finish or resume it before merging")
        want = shard_indices(doc["shard"])
        for k in sorted(want):
            if k in covered:
                raise ValueError(f"{key} {k} is in both {covered[k]} and {path}")
            covered[k] = Path(path)
        got = {int(r[key]) for r in doc["runs"]}
        if got != want:
            raise ValueError(f"{path} declares {key}s {sorted(want)[:5]}... but holds {sorted(got)[:5]}...")
        rows.extend(doc["runs"])
        wall += float(doc.get("wall_seconds", 0.0))
        build += float(doc.get("wall_seconds_source_build", 0.0))
    missing = sorted(set(range(total)) - set(covered))
    if missing:
        raise ValueError(f"{key}s not covered by any shard: {missing[:20]}")
    header = {k: v for k, v in first.items()
              if k not in ("runs", "summary", "secondary", "summary_by_gate_set", "e4", "shard",
                           "partial", "wall_seconds", "wall_seconds_source_build")}
    header["shard"] = {"start": 0, "end": total, "of": total}
    header["merged_from"] = [str(p) for p in paths]
    if stage == "p0":
        return p0_result(header, rows, partial=False, wall=wall, build=build)
    return main_result(header, rows, partial=False, wall=wall)


# --------------------------------------------------------------------------
# Main stage
# --------------------------------------------------------------------------


@dataclass(slots=True)
class MainStage:
    plan: list[PlannedCandidate]
    plan_meta: dict[str, Any]
    args: argparse.Namespace
    calendar: Any
    _series: dict[str, tuple[list[Any], list[dict[str, Any]], dict[str, Any]]] = field(default_factory=dict)

    def series_for(self, tf_name: str) -> tuple[list[Any], list[dict[str, Any]], dict[str, Any]]:
        from fiboki.core.enums import Timeframe

        if tf_name not in self._series:
            instruments = self.args.instruments or (
                gps.STARTER_INSTRUMENTS if self.args.starter else gps.K5_UNIVERSE
            )
            bars_from = self.args.bars_from if self.args.bars_from is not None else (
                None if self.args.starter else gps.K5_BARS_FROM
            )
            self._series[tf_name] = gps.load_series(
                starter=self.args.starter, data_root=self.args.data_root,
                instruments=instruments, timeframe=Timeframe(tf_name), bars_from=bars_from,
            )
        return self._series[tf_name]

    def build_candidate(
        self, planned: PlannedCandidate
    ) -> tuple[Candidate, PooledEvaluator, PooledHoldoutRegistry, list[Any], HoldoutRegistry] | str:
        from fiboki.validation.engine_evaluator import EvaluationCache

        series, _, _ = self.series_for(planned.timeframe)
        sleeves_series = [s for s in series if s.instrument in planned.instruments]
        if len(sleeves_series) < MIN_SLEEVES:
            return f"{len(sleeves_series)} sleeve(s) with bars: not a pool"
        # One in-memory cache per candidate, as run_validation has: rung 0's
        # defaults and rung 3's selected full-window run are grid points rung 1
        # already evaluated. Never on disk: an H4 evaluation is about 1 MB.
        cache = EvaluationCache()
        engines = [
            gps._evaluator_for(planned.document, s, s.frame, s.dataset_version_id, self.calendar,
                               s.price_lineage, cache=cache)
            for s in sleeves_series
        ]
        registry = HoldoutRegistry.in_memory()
        for s in sleeves_series:
            # Exactly run_validation's definition: first bar, last bar, 20%.
            registry.define(s.dataset_version_id, data_start=s.frame.index[0],
                            data_end=s.frame.index[-1], holdout_fraction=DEFAULT_HOLDOUT_FRACTION,
                            label=f"{s.instrument} {planned.timeframe}")
        pooled = PooledEvaluator(tuple(
            PooledSleeve(s.instrument, e, s.frame.index, s.dataset_version_id)
            for s, e in zip(sleeves_series, engines, strict=True)
        ))
        ledger = PooledHoldoutRegistry(registry, [s.dataset_version_id for s in sleeves_series])
        axes = gps.campaign_sweep_axes()
        candidate = engines[0].candidate(
            max_points=MAX_GRID_POINTS, max_values_per_axis=MAX_VALUES_PER_AXIS,
            include=sweep_axes_for(planned.document, axes),
        )
        if len(candidate.grid.points()) != planned.grid_points:
            raise RuntimeError(f"{planned.key}: grid {len(candidate.grid.points())} != planned {planned.grid_points}")
        return candidate, pooled, ledger, engines, registry

    def run_one(self, planned: PlannedCandidate, reports_dir: Path | None) -> dict[str, Any]:
        t0 = time.monotonic()
        row: dict[str, Any] = planned.describe()
        built = self.build_candidate(planned)
        if isinstance(built, str):
            row.update(status="skipped", reason=built)
            return row
        candidate, pooled, ledger, engines, _ = built
        external = external_trials(self.plan_meta["planned_trials"], planned.grid_points)
        config = LadderConfig(external_trial_count=external, walk_forward_folds=self.args.folds,
                              spa_bootstraps=self.args.spa_bootstraps,
                              stress_samples=self.args.stress_samples)
        ladder = ValidationLadder(config=config, gate_set=PRODUCTION_GATE_SET,
                                  rungs=rungs_with_deferred_holdout())
        report = ladder.run(
            candidate, pooled, registry=ledger, dataset_version_id=pooled.dataset_version_id,
            engine_config={"pooled": True, "sleeves": {
                s.instrument: e.engine_fingerprint() for s, e in zip(pooled.sleeves, engines, strict=True)
            }},
            broker_profile=engines[0].config.profile.fingerprint(),
            experiment_id="e4_pooled_universe", actor="scripts/pooled_universe_study.py",
            notes=f"E-4 main stage {planned.key}; holdout deferred; external trials {external}",
        )
        rung0 = next((r.metrics for r in report.rungs if r.index == 0), {})
        cleared = all(r.passed for r in report.rungs if r.index <= 5) and not any(
            g.status.blocks_promotion for g in report.gate_results if g.gate.rung <= 5
        )
        gate_values = {
            g.gate.name: (None if g.value is None or not math.isfinite(g.value) else g.value)
            for g in report.gate_results
        }
        row.update(
            status="run",
            external_trial_count=external,
            pooled_dataset_version_id=pooled.dataset_version_id,
            constituents=list(ledger.constituents),
            verdict=report.verdict.value,
            binding_kind=report.binding_constraint.kind,
            binding=report.binding_constraint.name,
            cleared_rungs_0_to_5=bool(cleared),
            rung_outcomes={str(r.index): r.outcome.value for r in report.rungs},
            gate_values=gate_values,
            gate_status={g.gate.name: g.status.value for g in report.gate_results},
            pooled=pooled_diagnostics(rung0),
            trial_count_n=report.trial_count_n,
            engine_runs=int(sum(getattr(e, "n_engine_runs", 0) for e in engines)),
            wall_seconds_candidate=round(time.monotonic() - t0, 3),
        )
        if reports_dir is not None:
            path = reports_dir / planned.timeframe / f"{planned.document.strategy_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            text = report.to_json()
            path.write_text(text, encoding="utf-8")
            row["report"] = {"path": str(path), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        return row


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _common(ap: argparse.ArgumentParser) -> None:
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--data-root", type=Path, default=None, help="a marked Fiboki data root")
    src.add_argument("--starter", action="store_true", help="data/starter/histdata: a harness check, never evidence")
    ap.add_argument("--instruments", nargs="+", default=None)
    ap.add_argument("--bars-from", default=None)
    ap.add_argument("--folds", type=int, default=LadderConfig().walk_forward_folds)
    ap.add_argument("--spa-bootstraps", type=int, default=LadderConfig().spa_bootstraps)
    ap.add_argument("--stress-samples", type=int, default=LadderConfig().stress_samples)
    ap.add_argument("--seed", type=int, default=BASE_SEED)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--resume", action="store_true")


def _is_evidence(args: argparse.Namespace) -> bool:
    return not getattr(args, "starter", False) and getattr(args, "data_root", None) is not None


def require_filed_configuration(args: argparse.Namespace, stage: str) -> None:
    """An EVIDENCE run (``--data-root``) may use no setting the filing does not fix.

    The filing fixes the universe and trim (K5's), the ladder configuration
    (``LadderConfig`` defaults), the base seed, the injected-Sharpe grid and the
    replicate count. Every one of them is an argparse option, because the harness
    checks on starter data need them; an evidence run that moved one would carry
    ``evidence: true`` into a file the admission and holdout rules then read.
    Refused here, before any bar is read. A starter run is untouched.
    """
    if not _is_evidence(args):
        return
    defaults = LadderConfig()
    wrong: list[str] = []
    if getattr(args, "instruments", None):
        wrong.append("--instruments (the filed universe is K5's sixteen series)")
    if getattr(args, "bars_from", None) is not None:
        wrong.append(f"--bars-from (the filed trim is {gps.K5_BARS_FROM})")
    if args.folds != defaults.walk_forward_folds:
        wrong.append(f"--folds {args.folds} (filed: {defaults.walk_forward_folds})")
    if args.spa_bootstraps != defaults.spa_bootstraps:
        wrong.append(f"--spa-bootstraps {args.spa_bootstraps} (filed: {defaults.spa_bootstraps})")
    if args.stress_samples != defaults.stress_samples:
        wrong.append(f"--stress-samples {args.stress_samples} (filed: {defaults.stress_samples})")
    if int(args.seed) != BASE_SEED:
        wrong.append(f"--seed {args.seed} (filed: {BASE_SEED})")
    if stage == "p0":
        if [float(s) for s in args.sr] != [float(s) for s in SR_GRID]:
            wrong.append(f"--sr {list(args.sr)} (filed: {list(SR_GRID)})")
        if int(args.replicates) not in FILED_REPLICATES:
            wrong.append(f"--replicates {args.replicates} (filed: {FILED_REPLICATES[0]}, or "
                         f"{FILED_REPLICATES[1]} once under the replicate rule)")
    if wrong:
        raise SystemExit(
            f"an evidence run of {stage} must use the filed configuration ({PREREGISTRATION}); "
            "these settings differ: " + "; ".join(wrong) + ". Use --starter for a harness check."
        )


def _header(stage: str, args: argparse.Namespace, plan_meta: Mapping[str, Any]) -> dict[str, Any]:
    evidence = not args.starter and args.data_root is not None
    return {
        "study": STUDY,
        "stage": stage,
        "preregistration": PREREGISTRATION,
        "evidence": evidence,
        "note": ("harness check on starter data; NOT evidence" if not evidence else
                 "E-4 run on the data root named in source; read only under the filed decision rule"),
        "gate_set_version": PRODUCTION_GATE_SET.version,
        "gate_set_fingerprint": PRODUCTION_GATE_SET.fingerprint(),
        "judged_gate_sets": {n: gs.fingerprint() for n, gs in sorted(JUDGED.items())},
        "plan": {k: plan_meta[k] for k in ("n_candidates", "planned_trials", "prior_trials",
                                            "true_trial_count", "generated_manifest_sha256")},
    }


def cmd_plan(args: argparse.Namespace) -> int:
    plan, meta = build_plan(seed_ids=args.documents, include_generated=not args.seeds_only)
    out = {**meta, "candidates": [c.describe() for c in plan]}
    if args.check:
        filed = json.loads((ROOT / PREREGISTRATION).read_text(encoding="utf-8"))["candidate_set"]
        for k in ("n_candidates", "planned_trials", "true_trial_count"):
            if filed[k] != meta[k]:
                print(f"PLAN DIFFERS FROM FILING: {k} filed {filed[k]} computed {meta[k]}", file=sys.stderr)
                return 3
    text = json.dumps(out, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in meta.items() if k != "not_pools"}, indent=2, sort_keys=True))
    return 0


def cmd_p0(args: argparse.Namespace) -> int:
    if not (args.starter or args.data_root is not None):
        raise SystemExit("p0 reads real bars: pass --data-root (evidence) or --starter (harness check)")
    require_filed_configuration(args, "p0")
    plan, plan_meta = build_plan()
    gps._check_grid_against_filing()
    start, end = (0, args.replicates) if args.replicate_range is None else tuple(args.replicate_range)
    if not 0 <= start < end <= args.replicates:
        raise SystemExit("--replicate-range must satisfy 0 <= START < END <= --replicates")
    external = external_trials(plan_meta["planned_trials"], MAX_GRID_POINTS)
    config = _ladder_config(args, external)
    started = time.monotonic()
    study = P0Study.build(args, config)
    build = time.monotonic() - started
    header = _header("p0", args, plan_meta)
    header.update(
        process="pooled_block_bootstrap",
        timeframe=study.timeframe,
        config={
            "min_trades": config.min_trades, "walk_forward_folds": config.walk_forward_folds,
            "spa_bootstraps": config.spa_bootstraps, "stress_samples": config.stress_samples,
            "external_trial_count": external, "seed": args.seed, "replicates": args.replicates,
            "sr_grid": list(args.sr), "family_size_candidates": plan_meta["n_candidates"],
        },
        shard={"start": start, "end": end, "of": args.replicates},
        source=study.describe(),
    )
    rows = _resume(args.out, header, "replicate") if args.resume else []
    done = {int(r["replicate"]) for r in rows}
    todo = [i for i in range(start, end) if i not in done]
    last = time.monotonic()
    for k, i in enumerate(todo):
        t0 = time.monotonic()
        rows.extend(study.replicate_rows(i, args.sr))
        if k == 0:
            per = time.monotonic() - t0
            print(f"E-4 P0 {study.timeframe}: source built in {build:.1f}s; first replicate took "
                  f"{per:.1f}s; estimate {(build + per * len(todo)) / 3600:.2f}h for {len(todo)}",
                  file=sys.stderr, flush=True)
        if k + 1 < len(todo) and time.monotonic() - last >= CHECKPOINT_SECONDS:
            _write(args.out, p0_result(header, rows, partial=True, wall=time.monotonic() - started, build=build))
            last = time.monotonic()
    result = p0_result(header, rows, partial=False, wall=time.monotonic() - started, build=build)
    _write(args.out, result)
    print(json.dumps({"summary": result["summary"], "e4": result["e4"]["z_calibration_at_sr_0"]},
                     indent=2, sort_keys=True))
    return 0


def cmd_main(args: argparse.Namespace) -> int:
    from fiboki.marketstate.calendar import load_research_calendar

    if not (args.starter or args.data_root is not None):
        raise SystemExit("main reads real bars: pass --data-root (evidence) or --starter (harness check)")
    if args.data_root is not None and (args.documents or args.seeds_only):
        # A subset plan would charge each candidate only the subset's trials: an
        # honest-looking, flattering external trial count. The filed plan only.
        raise SystemExit("--documents/--seeds-only are for harness checks; an evidence run uses the filed plan")
    require_filed_configuration(args, "main")
    plan, plan_meta = build_plan(seed_ids=args.documents, include_generated=not args.seeds_only)
    n = len(plan)
    if args.candidate_range is not None and args.shard is not None:
        raise SystemExit("--candidate-range and --shard are alternatives")
    if args.shard is not None:
        k, of = args.shard
        if not 0 <= k < of:
            raise SystemExit("--shard K N needs 0 <= K < N")
        shard: dict[str, Any] = {"k": k, "n": of, "of": n}
    else:
        start, end = (0, n) if args.candidate_range is None else tuple(args.candidate_range)
        if not 0 <= start < end <= n:
            raise SystemExit(f"--candidate-range must satisfy 0 <= START < END <= {n}")
        shard = {"start": start, "end": end, "of": n}
    wanted = shard_indices(shard)
    admitted = admitted_timeframes(args)
    started = time.monotonic()
    header = _header("main", args, plan_meta)
    header.update(
        config={"walk_forward_folds": args.folds, "spa_bootstraps": args.spa_bootstraps,
                "stress_samples": args.stress_samples, "rungs": "0-5; rung 6 deferred",
                "max_grid_points": MAX_GRID_POINTS, "max_values_per_axis": MAX_VALUES_PER_AXIS,
                "timeframes_admitted_by_p0": admitted},
        shard=shard,
        source={"instruments": list(args.instruments or (gps.STARTER_INSTRUMENTS if args.starter
                                                         else gps.K5_UNIVERSE)),
                "bars_from": args.bars_from if args.bars_from is not None else (
                    None if args.starter else gps.K5_BARS_FROM),
                "data_root": str(args.data_root) if args.data_root else "starter"},
    )
    stage = MainStage(plan, plan_meta, args, load_research_calendar())
    rows = _resume(args.out, header, "index") if args.resume else []
    done = {int(r["index"]) for r in rows}
    last = time.monotonic()
    for planned in plan:
        if planned.index not in wanted or planned.index in done:
            continue
        verdict_p0 = admitted[planned.timeframe]
        if not verdict_p0["admitted"]:
            rows.append({**planned.describe(), "status": "not_admitted", "reason": verdict_p0["why"]})
            continue
        try:
            row = stage.run_one(planned, args.reports_dir)
        except Exception as exc:  # recorded, never silently skipped
            row = {**planned.describe(), "status": "error", "error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
        print(f"E-4 main [{planned.index}] {planned.key}: {row.get('status')} "
              f"{row.get('verdict', '')} {row.get('binding', '')} ({row.get('wall_seconds_candidate', 0)}s)",
              file=sys.stderr, flush=True)
        if time.monotonic() - last >= CHECKPOINT_SECONDS:
            _write(args.out, main_result(header, rows, partial=True, wall=time.monotonic() - started))
            last = time.monotonic()
    result = main_result(header, rows, partial=False, wall=time.monotonic() - started)
    _write(args.out, result)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


def admitted_timeframes(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    """Which timeframes P0 admits (filed decision rule 2). An evidence run needs a
    complete P0 file per timeframe it runs; a starter harness check without
    ``--p0`` runs everything and says so."""
    if args.p0 is None and args.starter:
        return {tf: {"admitted": True, "why": "harness check: no P0 required"} for tf in TIMEFRAMES}
    filing = json.loads((ROOT / PREREGISTRATION).read_text(encoding="utf-8"))
    docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.p0 or [])]
    out = {}
    for tf in TIMEFRAMES:
        found = [d for d in docs if d.get("timeframe") == tf]
        if not found:
            out[tf] = {"admitted": False, "why": f"no P0 file for {tf}"}
            continue
        if len(found) > 1:
            # Two files for one timeframe (a 400 and an 800 under the replicate
            # rule, say): which one decides is not a choice this code may make.
            raise SystemExit(f"{len(found)} P0 files for {tf}; pass exactly one per timeframe")
        ok, why = p0_admits(found[0], tf, filing)
        out[tf] = {"admitted": ok, "why": why,
                   "p0_size": found[0]["summary"].get("0", {}).get("rate"),
                   "p0_power_0_08": found[0]["summary"].get("0.08", {}).get("rate")}
    return out


def choose_holdout_candidate(main_doc: Mapping[str, Any]) -> dict[str, Any] | None:
    """The filed rule: among SEED candidates that cleared rungs 0 to 5, the one with
    the highest deflated Sharpe (ties: the lower index). Generated documents are
    hypotheses and never get the look here."""
    if main_doc.get("partial") or main_doc.get("stage") != "main":
        raise ValueError("the holdout choice reads a complete, merged main-stage file only")
    if shard_indices(main_doc["shard"]) != set(range(int(main_doc["plan"]["n_candidates"]))):
        raise ValueError("the main-stage file does not cover every planned candidate")
    pool = [r for r in main_doc["runs"] if r.get("cleared_rungs_0_to_5") and r.get("tier") == "seed"]
    if not pool:
        return None
    return max(pool, key=lambda r: (float(r["gate_values"].get("deflated_sharpe") or -1.0), -int(r["index"])))


def p0_admits(p0_doc: Mapping[str, Any], timeframe: str, filing: Mapping[str, Any]) -> tuple[bool, str]:
    """Does P0 for ``timeframe`` admit the main stage's verdicts (filed rule)?"""
    if p0_doc.get("partial") or p0_doc.get("stage") != "p0" or p0_doc.get("timeframe") != timeframe:
        return False, "not a complete P0 file for this timeframe"
    if not p0_doc.get("evidence"):
        return False, "P0 was not run on evidence data"
    rule = filing["p0"]["decision_rule_parameters"]
    config = p0_doc["config"]
    replicates = int(config["replicates"])
    if replicates < int(rule["min_replicates"]):
        return False, "P0 ran fewer replicates than filed"
    # The file must BE the filed run, whole: every replicate (a completed shard
    # is not partial, but covers a fraction), the filed edge grid, the filed seed,
    # the filed gate set and the filed trial count. Anything else is a different
    # study, whatever its header says.
    if shard_indices(p0_doc["shard"]) != set(range(replicates)):
        return False, f"P0 file covers {p0_doc['shard']}, not all {replicates} replicates (merge the shards)"
    at_zero = [r for r in p0_doc["runs"] if float(r["sr"]) == 0.0]
    if len(at_zero) != replicates or len(p0_doc["runs"]) != replicates * len(config["sr_grid"]):
        return False, f"P0 file holds {len(at_zero)} sr = 0 rows for {replicates} replicates"
    if [float(s) for s in config["sr_grid"]] != [float(s) for s in filing["p0"]["injected_sharpe_grid"]]:
        return False, f"P0 edge grid {config['sr_grid']} is not the filed {filing['p0']['injected_sharpe_grid']}"
    if int(config.get("seed", -1)) != BASE_SEED:
        return False, f"P0 seed {config.get('seed')} is not the filed {BASE_SEED}"
    if p0_doc.get("gate_set_fingerprint") != PRODUCTION_GATE_SET.fingerprint() or (
        p0_doc.get("gate_set_fingerprint") not in filing["code"]["gate_set"]
    ):
        return False, "P0 was judged under a gate set other than the filed production set"
    true_n = int(filing["candidate_set"]["true_trial_count"])
    if int(p0_doc["plan"]["true_trial_count"]) != true_n or (
        int(config["external_trial_count"]) != true_n - MAX_GRID_POINTS
    ):
        return False, (f"P0 trial accounting ({p0_doc['plan'].get('true_trial_count')}, "
                       f"{config.get('external_trial_count')}) is not the filed ({true_n}, {true_n - MAX_GRID_POINTS})")
    size = p0_doc["summary"].get("0", {}).get("rate")
    if size is None or size > float(rule["size_max"]):
        return False, f"P0 size {size} exceeds {rule['size_max']}"
    z = p0_doc["e4"]["z_calibration_at_sr_0"]["effective"]
    if z.get("variance") is None or z["variance"] > float(rule["z_variance_max"]):
        return False, f"P0 z-variance {z.get('variance')} exceeds {rule['z_variance_max']}"
    return True, "P0 admits"


def cmd_holdout(args: argparse.Namespace) -> int:
    from fiboki.marketstate.calendar import load_research_calendar

    filing = json.loads((ROOT / PREREGISTRATION).read_text(encoding="utf-8"))
    if not str(filing.get("status", "")).startswith("FILED"):
        raise SystemExit("the pre-registration is not FILED")
    if not _is_evidence(args):
        raise SystemExit("the holdout look is spent on evidence data only: pass --data-root")
    require_filed_configuration(args, "holdout")
    main_doc = json.loads(args.main.read_text(encoding="utf-8"))
    if not main_doc.get("evidence"):
        raise SystemExit("refused: the main-stage file was not run on evidence data")
    if main_doc.get("gate_set_fingerprint") != PRODUCTION_GATE_SET.fingerprint():
        raise SystemExit("refused: the main-stage file was judged under a gate set other than the filed one")
    if int(main_doc["plan"]["true_trial_count"]) != int(filing["candidate_set"]["true_trial_count"]):
        raise SystemExit("refused: the main-stage file's trial accounting is not the filed one")
    chosen = choose_holdout_candidate(main_doc)
    if chosen is None:
        print("no seed candidate cleared rungs 0 to 5: no holdout is spent", flush=True)
        return 0
    p0_doc = json.loads(args.p0.read_text(encoding="utf-8"))
    ok, why = p0_admits(p0_doc, chosen["timeframe"], filing)
    if not ok:
        raise SystemExit(f"refused: {why}")
    plan, plan_meta = build_plan()
    planned = plan[int(chosen["index"])]
    if planned.key != chosen["key"]:
        raise SystemExit("the plan no longer matches the main-stage file")
    stage = MainStage(plan, plan_meta, args, load_research_calendar())
    built = stage.build_candidate(planned)
    if isinstance(built, str):
        raise SystemExit(built)
    candidate, pooled, _, engines, _ = built
    persistent = HoldoutRegistry(args.registry)
    series, _, _ = stage.series_for(planned.timeframe)
    for s in series:
        if s.instrument in planned.instruments:
            persistent.define(s.dataset_version_id, data_start=s.frame.index[0],
                              data_end=s.frame.index[-1], holdout_fraction=DEFAULT_HOLDOUT_FRACTION,
                              label=f"{s.instrument} {planned.timeframe}")
    ledger = PooledHoldoutRegistry(persistent, [sl.dataset_version_id for sl in pooled.sleeves])
    for prior in args.prior_registry or []:
        reg = HoldoutRegistry(prior)
        for dvid in ledger.constituents:
            if reg.is_consumed(dvid, candidate.content_hash):
                raise SystemExit(f"refused: {prior} records a look by this strategy on {dvid}")
    external = int(chosen["external_trial_count"])
    config = LadderConfig(external_trial_count=external, walk_forward_folds=args.folds,
                          spa_bootstraps=args.spa_bootstraps, stress_samples=args.stress_samples)
    report = ValidationLadder(config=config, gate_set=PRODUCTION_GATE_SET, rungs=default_rungs()).run(
        candidate, pooled, registry=ledger, dataset_version_id=pooled.dataset_version_id,
        engine_config={"pooled": True}, broker_profile=engines[0].config.profile.fingerprint(),
        experiment_id="e4_pooled_universe_holdout", actor="scripts/pooled_universe_study.py",
        notes=f"E-4 holdout look for {planned.key} (filed rule: best seed survivor by DSR)",
    )
    _write(args.out, {"study": STUDY, "stage": "holdout", "candidate": chosen["key"],
                      "report": json.loads(report.to_json())})
    print(report.summary())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or STUDY).splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="the main stage's candidates and trial accounting")
    p.add_argument("--documents", nargs="+", default=None)
    p.add_argument("--seeds-only", action="store_true")
    p.add_argument("--check", action="store_true", help="exit 3 unless the plan equals the filing")
    p.add_argument("--out", type=Path, default=None)

    p = sub.add_parser("p0", help="process P0: size and power of the pooled procedure")
    _common(p)
    p.add_argument("--timeframe", choices=TIMEFRAMES, required=True)
    p.add_argument("--documents", nargs="+", default=None, help="seed ids (default: all seeds)")
    p.add_argument("--sr", type=float, nargs="+", default=list(SR_GRID))
    p.add_argument("--replicates", type=int, default=400)
    p.add_argument("--replicate-range", type=int, nargs=2, default=None, metavar=("START", "END"))

    p = sub.add_parser("main", help="the main stage: every pooled candidate, rungs 0-5")
    _common(p)
    p.add_argument("--documents", nargs="+", default=None, help="restrict seeds (tests only)")
    p.add_argument("--seeds-only", action="store_true", help="tests only: no generated documents")
    p.add_argument("--candidate-range", type=int, nargs=2, default=None, metavar=("START", "END"))
    p.add_argument("--reports-dir", type=Path, default=None)
    p.add_argument("--p0", type=Path, nargs="+", default=None,
                   help="the merged P0 files (one per timeframe); a timeframe P0 does not admit is not run")
    p.add_argument("--shard", type=int, nargs=2, default=None, metavar=("K", "N"),
                   help="run candidates with index %% N == K (costs are uneven, so shards interleave)")

    p = sub.add_parser("holdout", help="the one look, for the filed rule's single candidate")
    _common(p)
    p.add_argument("--main", type=Path, required=True, help="the merged main-stage file")
    p.add_argument("--p0", type=Path, required=True, help="the merged P0 file for the chosen timeframe")
    p.add_argument("--registry", type=Path, required=True, help="the persistent holdout registry")
    p.add_argument("--prior-registry", type=Path, nargs="*", default=None)

    p = sub.add_parser("merge", help="merge p0 or main shards")
    p.add_argument("shards", type=Path, nargs="+")
    p.add_argument("--out", type=Path, required=True)

    args = ap.parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "p0":
        return cmd_p0(args)
    if args.cmd == "main":
        return cmd_main(args)
    if args.cmd == "holdout":
        return cmd_holdout(args)
    merged = merge(args.shards)
    _write(args.out, merged)
    print(json.dumps(merged["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

