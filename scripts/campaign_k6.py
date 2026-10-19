#!/usr/bin/env python
"""K6: 100,000 generated documents, pooled, screened, then laddered. The instrument.

Pre-registration (DRAFT): ``research/preregistration/campaign_k6.json``. Read it
first. It is DRAFT until E-4's process P0 has been run and read; until it is
FILED this script refuses an evidence run (``--data-root``) and runs only the
harness (``--starter`` bars or, in tests, injected series). Nothing it prints
before the filing may be read as a result.

What a K6 candidate is
----------------------
One generated document (``research/generate_families_k6.py``; the 100,000 cells
are in ``research/generated_k6/``) on one admitted timeframe, evaluated as ONE
pooled candidate over a named universe (one equal-capital sleeve per series,
E-4's :class:`~fiboki.validation.pooled.PooledEvaluator`, every count-based
gate on ``N_eff = N / DEFF``), under the production gates
(``v2.1.0-calibrated``), unchanged.

Stages
------
``plan``
    The trial accounting and the deflation bar it implies, from the manifest and
    the universe alone (no bars). ``--check`` compares with the pre-registration.

``screen`` (stage S0; every document, one trial each per timeframe)
    S0a, a BOUND: the pooled signal count over the research window, from the
    signal kernel without the engine. Trades never exceed signals and
    ``N_eff <= N``, so a document whose pooled signal count is below rung 0's
    trade floor (150) cannot pass rung 0, and its engine runs are skipped. This
    removes nothing rung 0 would keep.
    S0b: rung 0 of the production ladder, alone (``SanityRung`` plus the
    rung-0 gate block, the MinTRL gate on ``N_eff``): the default binding, once
    per sleeve, over the research window. A document passes the screen exactly
    when the fail-fast ladder would pass rung 0, so the screen is rung 0 run
    first for every document, not an extra selection.

``ladder`` (stage S1; screen survivors, at most the filed cap per timeframe)
    The fail-fast ladder, rungs 0 to 5, holdout DEFERRED (E-4's
    ``rungs_with_deferred_holdout``), with ``external_trial_count`` set to the
    filed campaign N less the candidate's own grid. K6 reads no holdout at all:
    a generated survivor is a hypothesis, to be re-authored as a seed under its
    own filing, which is where its one look is spent.

``merge``, ``status``
    Shard files are JSON lines (one header line, then one row per document,
    flushed per row and fsynced at most once a minute), so ``--resume`` loses at
    most the document in progress, a torn last line is dropped and redone, and
    ``merge`` refuses gaps, overlaps and shards from a different run.

Universes
---------
``--universe`` is a NAME from :data:`UNIVERSES`; anything else is refused.
``crypto_majors_v1`` is declared (another branch adds its instruments and bars)
and refused here until its instrument list is defined and its own P0 exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research"))

import gate_power_study as gps  # noqa: E402
import generate_families_k6 as k6gen  # noqa: E402

from fiboki.validation.evaluation import DateWindow  # noqa: E402
from fiboki.validation.gates import PRODUCTION_GATE_SET, sanity_trade_floor  # noqa: E402
from fiboki.validation.holdout import DEFAULT_HOLDOUT_FRACTION, HoldoutRegistry  # noqa: E402
from fiboki.validation.ladder import LadderConfig, SanityRung, ValidationLadder  # noqa: E402
from fiboki.validation.pooled import (  # noqa: E402
    PooledEvaluator,
    PooledHoldoutRegistry,
    PooledSleeve,
    rungs_with_deferred_holdout,
)

PREREGISTRATION = "research/preregistration/campaign_k6.json"
E4_PREREGISTRATION = "research/preregistration/pooled_universe_e4.json"
STUDY = "K6"
TIMEFRAMES = ("H4", "D1")
#: E-4's true N: every trial on these bars before K6 (ledger, "E-4 filed").
PRIOR_TRIALS = 21_968
MAX_GRID_POINTS = 8
MAX_VALUES_PER_AXIS = 2
MIN_SLEEVES = 2
#: Survivors laddered per (universe, timeframe), in generation-index order.
LADDER_CAP = 5_000
#: Universes the declared N charges for, whether or not both are ever run: the
#: FX pool and crypto_majors_v1. Declaring both up front means adding the crypto
#: universe later cannot raise the bar the FX candidates were judged at.
N_UNIVERSES_DECLARED = 2
CHECKPOINT_SECONDS = 60.0
#: Filed stop rule: more than 1% error rows after 1,000 documents stops a stage.
ERROR_STOP_AFTER = 1_000
ERROR_STOP_RATE = 0.01
RUNG0_FLOOR = sanity_trade_floor(PRODUCTION_GATE_SET)

#: Universe name -> definition. ``instruments`` None means declared, not defined here.
UNIVERSES: dict[str, dict[str, Any]] = {
    "fx_pooled_k5": {
        "instruments": tuple(gps.K5_UNIVERSE),
        "bars_from": gps.K5_BARS_FROM,
        "note": "K5's sixteen OANDA MID series, as E-4 pools them",
        "p0": "E-4 process P0 (pooled_universe_e4.json), per timeframe",
    },
    "crypto_majors_v1": {
        "instruments": None,
        "bars_from": None,
        "note": ("declared for K6; its instruments, bars and calendar are added on "
                 "wave/crypto and are not defined on this branch"),
        "p0": "its own P0 (an E-4-style size/power study on that universe), not yet filed",
    },
}


class UniverseUnavailable(SystemExit):
    pass


def _named_elsewhere(name: str) -> dict[str, Any]:
    """A universe defined in ``fiboki.research.universes`` (added on wave/crypto), if present.

    Resolving the NAME is all this does. A crypto universe is long only, uses its
    own cost profile and calendar, and needs its own P0: :func:`admitted` refuses
    every evidence run on it, and the filing lists what must be amended first.
    """
    try:
        from fiboki.research.universes import get_universe  # type: ignore[import-not-found]

        universe = get_universe(name)
    except Exception:  # module absent on this branch, or the name unknown there
        return {}
    return {"instruments": tuple(universe.symbols), "long_only": bool(universe.long_only),
            "profile": universe.profile, "note": universe.description}


def resolve_universe(name: str) -> dict[str, Any]:
    """The universe's definition, or a refusal. Unknown names are never guessed."""
    if name not in UNIVERSES:
        raise UniverseUnavailable(
            f"unknown universe {name!r}; K6 declares {sorted(UNIVERSES)}. A universe is a "
            "pre-registered name, not a list typed on the command line."
        )
    spec = dict(UNIVERSES[name])
    if spec["instruments"] is None:
        spec.update(_named_elsewhere(name))
    if spec["instruments"] is None:
        raise UniverseUnavailable(
            f"universe {name!r} is declared but not defined on this branch ({spec['note']}). "
            "Define its instrument list here, record its hash in the filing, and run its own "
            f"P0 ({spec['p0']}) before any K6 stage reads it."
        )
    spec["name"] = name
    spec["instruments"] = tuple(sorted(spec["instruments"]))
    spec["sha256"] = hashlib.sha256(
        json.dumps({"name": name, "instruments": spec["instruments"],
                    "bars_from": spec["bars_from"]}, sort_keys=True).encode()
    ).hexdigest()
    return spec


# --------------------------------------------------------------------------
# Trial accounting and the deflation bar
# --------------------------------------------------------------------------


def screen_trials(cells: Sequence[tuple[int, Any]], timeframes: Sequence[str]) -> int:
    """One trial per (document, admitted timeframe it declares)."""
    total = 0
    for _, cell in cells:
        total += sum(1 for tf in timeframes if tf in declared_timeframes(cell))
    return total


def declared_trials(cells: Sequence[tuple[int, Any]], timeframes: Sequence[str] = TIMEFRAMES,
                    n_universes: int = N_UNIVERSES_DECLARED) -> dict[str, int]:
    """The campaign's N, fixed BEFORE any result: every screened document counts,
    and every survivor the cap allows is charged its whole grid (less the default
    point the screen already counted). Running fewer never lowers it."""
    screened = screen_trials(cells, timeframes) * n_universes
    ladder = LADDER_CAP * (MAX_GRID_POINTS - 1) * len(timeframes) * n_universes
    return {
        "prior_trials": PRIOR_TRIALS,
        "screen_trials": screened,
        "ladder_trials_max": ladder,
        "campaign_trials": screened + ladder,
        "declared_true_n": PRIOR_TRIALS + screened + ladder,
    }


def external_trials(declared_true_n: int, own_grid_points: int) -> int:
    return int(declared_true_n) - int(own_grid_points)


def expected_max_z(n: int) -> float:
    """``E[max of n iid N(0,1)]`` as the platform's deflation computes it (V = 1)."""
    from fiboki.stats.sharpe import expected_max_sharpe

    return expected_max_sharpe(int(n), 1.0)


def deflation_table(ns: Iterable[int], trade_counts: Iterable[int] = (425, 1000, 2000, 4000),
                    confidence: float = 0.95) -> list[dict[str, Any]]:
    """What a trial count does to the bar, in per-trade Sharpe, Gaussian approximation.

    Under the null each candidate's per-trade Sharpe estimate has variance about
    ``1/T`` (T effective trades), so the deflation threshold is
    ``SR0 ~ E[max Z_N] / sqrt(T)`` and a DSR of ``confidence`` needs roughly
    ``SR >= (E[max Z_N] + z_conf) / sqrt(T)``. The ladder's own DSR is computed on
    per-period returns with the trials' observed variance and moments; this
    table is the plain-words version, not the gate.
    """
    from scipy.stats import norm

    z = float(norm.ppf(confidence))
    out = []
    for n in ns:
        emax = expected_max_z(n)
        row: dict[str, Any] = {"n_trials": int(n), "expected_max_z": round(emax, 3),
                               "sqrt_2_ln_n": round(math.sqrt(2.0 * math.log(n)), 3)}
        for t in trade_counts:
            row[f"sr0_T{t}"] = round(emax / math.sqrt(t), 4)
            row[f"certifiable_T{t}"] = round((emax + z) / math.sqrt(t), 4)
        out.append(row)
    return out


# --------------------------------------------------------------------------
# Building a pooled candidate
# --------------------------------------------------------------------------

EvaluatorFactory = Callable[[Any, Any, Any], Any]


def default_evaluator_factory(calendar: Any) -> EvaluatorFactory:
    """The research engine path (engine_v4_time_units, GBP, IG_REALISTIC, construction_v2)."""

    def make(document: Any, series: Any, cache: Any) -> Any:
        return gps._evaluator_for(document, series, series.frame, series.dataset_version_id,
                                  calendar, series.price_lineage, cache=cache)

    return make


@dataclass(slots=True)
class Pool:
    candidate: Any
    evaluator: PooledEvaluator
    ledger: PooledHoldoutRegistry
    engines: list[Any]
    research_windows: dict[str, DateWindow]


def build_pool(document: Any, series: Sequence[Any], make: EvaluatorFactory,
               *, cache: bool) -> Pool | str:
    """E-4's main-stage construction (``pooled_universe_study.MainStage.build_candidate``)."""
    from fiboki.validation.engine_evaluator import EvaluationCache

    sleeves = [s for s in series if s.instrument in document.universe]
    if len(sleeves) < MIN_SLEEVES:
        return f"{len(sleeves)} sleeve(s) with bars: not a pool"
    shared = EvaluationCache() if cache else None
    engines = [make(document, s, shared) for s in sleeves]
    registry = HoldoutRegistry.in_memory()
    windows: dict[str, DateWindow] = {}
    for s in sleeves:
        seg = registry.define(s.dataset_version_id, data_start=s.frame.index[0],
                              data_end=s.frame.index[-1],
                              holdout_fraction=DEFAULT_HOLDOUT_FRACTION,
                              label=f"{s.instrument} {s.timeframe.value}")
        windows[s.instrument] = seg.research_window
    pooled = PooledEvaluator(tuple(
        PooledSleeve(s.instrument, e, s.frame.index, s.dataset_version_id)
        for s, e in zip(sleeves, engines, strict=True)
    ))
    ledger = PooledHoldoutRegistry(registry, [s.dataset_version_id for s in sleeves])
    candidate = engines[0].candidate(max_points=MAX_GRID_POINTS,
                                     max_values_per_axis=MAX_VALUES_PER_AXIS)
    return Pool(candidate, pooled, ledger, engines, windows)


def cost_share(trades: Sequence[Any]) -> dict[str, Any]:
    """Modelled costs as a share of gross P&L magnitude: is the edge eaten by costs?"""
    spread = sum(float(t.spread_cost) for t in trades)
    slippage = sum(float(t.slippage_cost) for t in trades)
    financing = sum(float(t.financing_cost) for t in trades)
    commission = sum(float(t.commission) for t in trades)
    total = spread + slippage + financing + commission
    gross = sum(abs(float(t.net_pnl) + float(t.spread_cost) + float(t.slippage_cost)
                    + float(t.financing_cost) + float(t.commission)) for t in trades)
    return {"spread": spread, "slippage": slippage, "financing": financing,
            "commission": commission, "total": total,
            "share_of_gross": (total / gross) if gross > 0 else None}


def _rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def screen_one(index: int, document: Any, series: Sequence[Any], make: EvaluatorFactory,
               *, use_bound: bool = True) -> dict[str, Any]:
    """S0 for one (document, timeframe): the bound, then rung 0 alone."""
    t0 = time.monotonic()
    row: dict[str, Any] = {"index": index, "strategy_id": document.strategy_id}
    built = build_pool(document, series, make, cache=True)
    if isinstance(built, str):
        row.update(status="skipped", reason=built, passed=False)
        return row
    pool = built
    row["default_binding_content_hash"] = pool.candidate.content_hash
    research = pool.ledger.segment(pool.evaluator.dataset_version_id).research_window
    if use_bound:
        defaults = pool.candidate.default_params
        counts = {s.instrument: int(s.evaluator.signal_count(defaults, research))
                  for s in pool.evaluator.sleeves}
        bound = sum(counts.values())
        row["signal_bound"] = bound
        if bound < RUNG0_FLOOR:
            row.update(status="run", stage="S0a", passed=False,
                       reason=f"pooled signal count {bound} < rung-0 floor {RUNG0_FLOOR}",
                       seconds=round(time.monotonic() - t0, 3))
            return row
    ladder = ValidationLadder(config=LadderConfig(), gate_set=PRODUCTION_GATE_SET,
                              rungs=(SanityRung(),))
    report = ladder.run(pool.candidate, pool.evaluator, registry=pool.ledger,
                        dataset_version_id=pool.evaluator.dataset_version_id,
                        experiment_id="k6_screen", actor="scripts/campaign_k6.py",
                        notes=f"K6 S0 document {index}")
    rung0 = report.rungs[0]
    metrics = dict(rung0.metrics)
    # The rung's own baseline, served from the per-candidate cache (no new
    # engine run): its trades carry the cost split the screen records.
    baseline = pool.evaluator(pool.candidate.default_params, research)
    costs = cost_share(baseline.trades)
    row.update(
        status="run",
        stage="S0b",
        passed=bool(rung0.passed),
        rung0_outcome=rung0.outcome.value,
        reason=rung0.reason if not rung0.passed else "",
        n_trades=metrics.get("n_trades"),
        n_trades_effective=metrics.get("n_trades_effective"),
        design_effect=(metrics.get("design_effect") or {}).get("deff")
        if isinstance(metrics.get("design_effect"), Mapping) else None,
        expectancy=metrics.get("expectancy"),
        min_trl_95=metrics.get("min_trl_95"),
        n_trades_over_min_trl=metrics.get("n_trades_over_min_trl"),
        costs=costs,
        engine_runs=int(sum(getattr(e, "n_engine_runs", 0) for e in pool.engines)),
        seconds=round(time.monotonic() - t0, 3),
    )
    return row


def ladder_one(index: int, document: Any, series: Sequence[Any], make: EvaluatorFactory,
               declared_true_n: int, reports_dir: Path | None = None) -> dict[str, Any]:
    """S1 for one survivor: rungs 0 to 5, holdout deferred, deflated at the filed N."""
    t0 = time.monotonic()
    row: dict[str, Any] = {"index": index, "strategy_id": document.strategy_id}
    built = build_pool(document, series, make, cache=True)
    if isinstance(built, str):
        row.update(status="skipped", reason=built)
        return row
    pool = built
    grid = len(pool.candidate.grid.points())
    external = external_trials(declared_true_n, grid)
    config = LadderConfig(external_trial_count=external)
    ladder = ValidationLadder(config=config, gate_set=PRODUCTION_GATE_SET,
                              rungs=rungs_with_deferred_holdout())
    report = ladder.run(
        pool.candidate, pool.evaluator, registry=pool.ledger,
        dataset_version_id=pool.evaluator.dataset_version_id,
        engine_config={"pooled": True, "sleeves": {
            s.instrument: e.engine_fingerprint()
            for s, e in zip(pool.evaluator.sleeves, pool.engines, strict=True)}},
        broker_profile=pool.engines[0].config.profile.fingerprint(),
        experiment_id="k6_campaign", actor="scripts/campaign_k6.py",
        notes=f"K6 S1 document {index}; holdout deferred; external trials {external}",
    )
    cleared = all(r.passed for r in report.rungs if r.index <= 5) and not any(
        g.status.blocks_promotion for g in report.gate_results if g.gate.rung <= 5)
    row.update(
        status="run",
        grid_points=grid,
        external_trial_count=external,
        verdict=report.verdict.value,
        binding=report.binding_constraint.name,
        cleared_rungs_0_to_5=bool(cleared),
        rung_outcomes={str(r.index): r.outcome.value for r in report.rungs},
        gate_values={g.gate.name: (None if g.value is None or not math.isfinite(g.value)
                                   else g.value) for g in report.gate_results},
        trial_count_n=report.trial_count_n,
        seconds=round(time.monotonic() - t0, 3),
    )
    if reports_dir is not None:
        path = reports_dir / f"{document.strategy_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = report.to_json()
        path.write_text(text, encoding="utf-8")
        row["report_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return row


# --------------------------------------------------------------------------
# Shards: JSON lines, resumable
# --------------------------------------------------------------------------


def shard_indices(total: int, k: int, n: int) -> list[int]:
    """Interleaved: shard k of n takes k, k+n, k+2n, ... so a partial run is a
    random subsample of the generation order, not a family block."""
    if not 0 <= k < n:
        raise ValueError("shard must satisfy 0 <= K < N")
    return list(range(k, total, n))


def _identity(header: Mapping[str, Any]) -> str:
    keep = {k: header.get(k) for k in ("study", "stage", "universe", "timeframe",
                                       "manifest", "gate_set_fingerprint", "declared",
                                       "evidence", "shard", "use_bound")}
    return json.dumps(keep, sort_keys=True)


class ShardFile:
    """Append-only JSON lines with a header; resumable; a torn tail is dropped."""

    def __init__(self, path: Path, header: Mapping[str, Any], *, resume: bool) -> None:
        self.path = path
        self.rows: dict[int, dict[str, Any]] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not resume:
                raise SystemExit(f"{path} exists; pass --resume to continue it")
            self._load(header)
            self.fh = path.open("a", encoding="utf-8")
        else:
            self.fh = path.open("w", encoding="utf-8")
            self.fh.write(json.dumps({"kind": "header", **header}, sort_keys=True) + "\n")
            self._sync()
        self._last_sync = time.monotonic()

    def _load(self, header: Mapping[str, Any]) -> None:
        raw = self.path.read_bytes()
        good = raw.rfind(b"\n") + 1
        if good < len(raw):  # torn last line: drop it, redo that document
            with self.path.open("r+b") as fh:
                fh.truncate(good)
            raw = raw[:good]
        lines = raw.decode("utf-8").splitlines()
        if not lines:
            raise SystemExit(f"{self.path} has no header")
        first = json.loads(lines[0])
        if first.get("kind") != "header" or _identity(first) != _identity(header):
            raise SystemExit(f"{self.path} was produced by a different run; refusing to resume")
        for line in lines[1:]:
            row = json.loads(line)
            self.rows[int(row["index"])] = row

    def _sync(self) -> None:
        self.fh.flush()
        os.fsync(self.fh.fileno())

    def append(self, row: Mapping[str, Any]) -> None:
        self.fh.write(json.dumps({"kind": "row", **row}, sort_keys=True, default=str) + "\n")
        self.fh.flush()
        if time.monotonic() - self._last_sync >= CHECKPOINT_SECONDS:
            self._sync()
            self._last_sync = time.monotonic()
        self.rows[int(row["index"])] = dict(row)

    def close(self) -> None:
        self._sync()
        self.fh.close()


def read_shard(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    rows = []
    for line in lines[1:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:  # a torn tail: the shard is not finished
            break
    return header, rows


def merge(paths: Sequence[Path]) -> dict[str, Any]:
    """Shards of one stage into one result; refuses gaps, overlaps and strangers."""
    if not paths:
        raise ValueError("nothing to merge")
    loaded = [read_shard(Path(p)) for p in paths]
    first = loaded[0][0]
    ident = {k: v for k, v in json.loads(_identity(first)).items() if k != "shard"}
    planned_count = int(first["n_documents"])
    covered: dict[int, str] = {}
    rows: list[dict[str, Any]] = []
    for path, (header, shard_rows) in zip(paths, loaded, strict=True):
        other = {k: v for k, v in json.loads(_identity(header)).items() if k != "shard"}
        if other != ident:
            raise ValueError(f"{path} is not the same run as {paths[0]}")
        want = set(header["indices"])
        got = {int(r["index"]) for r in shard_rows}
        if got != want:
            missing = sorted(want - got)
            raise ValueError(f"{path} is unfinished or holds strangers: missing {missing[:5]}")
        for i in want:
            if i in covered:
                raise ValueError(f"document {i} is in both {covered[i]} and {path}")
            covered[i] = str(path)
        rows.extend(shard_rows)
    if "planned_indices_total" in first:
        gaps = sorted({int(i) for i in first["planned_indices_total"]} - set(covered))
    else:
        gaps = [] if len(covered) == planned_count else [planned_count - len(covered)]
    if gaps or len(covered) != planned_count:
        raise ValueError(f"shards cover {len(covered)} of {planned_count} planned documents; "
                         f"missing {gaps[:20]}")
    rows.sort(key=lambda r: int(r["index"]))
    run = [r for r in rows if r.get("status") == "run"]
    out = {k: v for k, v in first.items() if k not in ("kind", "shard", "indices")}
    out["merged_from"] = [str(p) for p in paths]
    out["summary"] = summarise(first["stage"], rows)
    out["runs"] = rows
    out["n_run"] = len(run)
    return out


def summarise(stage: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if stage == "screen":
        passed = [int(r["index"]) for r in rows if r.get("passed")]
        by_stage: dict[str, int] = {}
        for r in rows:
            key = f"{r.get('stage', r.get('status'))}:{'pass' if r.get('passed') else 'fail'}"
            by_stage[key] = by_stage.get(key, 0) + 1
        return {"documents": len(rows), "survivors": len(passed),
                "survivor_indices": passed, "by_stage": dict(sorted(by_stage.items()))}
    cleared = [int(r["index"]) for r in rows if r.get("cleared_rungs_0_to_5")]
    binding: dict[str, int] = {}
    for r in rows:
        if r.get("status") == "run":
            binding[str(r.get("binding"))] = binding.get(str(r.get("binding")), 0) + 1
    return {"candidates": len(rows), "cleared_rungs_0_to_5": cleared,
            "binding_counts": dict(sorted(binding.items())),
            "holdout": "not read: K6 reads no holdout (survivors are hypotheses)"}


# --------------------------------------------------------------------------
# Admission, filing and the run loop
# --------------------------------------------------------------------------


def filing() -> dict[str, Any]:
    return json.loads((ROOT / PREREGISTRATION).read_text(encoding="utf-8"))


def require_filed(evidence: bool) -> None:
    status = str(filing().get("status", ""))
    if evidence and not status.startswith("FILED"):
        raise SystemExit(
            f"{PREREGISTRATION} is {status!r}: K6 is filed only after E-4's P0 has been run and "
            "read. An evidence run before the filing would be a result nobody pre-registered. "
            "Use --starter for a harness check."
        )


def admitted(timeframe: str, universe: str, p0_path: Path | None, evidence: bool) -> tuple[bool, str]:
    """K6 runs a (universe, timeframe) only where that universe's P0 admits it."""
    if not evidence:
        return True, "harness check: no P0 required"
    if universe != "fx_pooled_k5":
        return False, f"no filed P0 procedure for {universe}"
    if p0_path is None:
        return False, f"no E-4 P0 file for {timeframe}"
    sys.path.insert(0, str(ROOT / "scripts"))
    import pooled_universe_study as pus

    e4 = json.loads((ROOT / E4_PREREGISTRATION).read_text(encoding="utf-8"))
    doc = json.loads(p0_path.read_text(encoding="utf-8"))
    return pus.p0_admits(doc, timeframe, e4)


def _header(args: argparse.Namespace, stage: str, universe: Mapping[str, Any],
            manifest: Mapping[str, Any], declared: Mapping[str, int], indices: Sequence[int],
            n_documents: int, evidence: bool) -> dict[str, Any]:
    return {
        "study": STUDY,
        "stage": stage,
        "preregistration": PREREGISTRATION,
        "evidence": evidence,
        "note": "harness check; NOT evidence" if not evidence else "K6 evidence run",
        "universe": {"name": universe["name"], "sha256": universe["sha256"],
                     "instruments": list(universe["instruments"])},
        "timeframe": args.timeframe,
        "manifest": {"cells_sha256": manifest["cells_sha256"],
                     "hash_chain_sha256": manifest["hash_chain_sha256"],
                     "rng_seed": manifest["rng_seed"], "size": manifest["size"]},
        "gate_set_fingerprint": PRODUCTION_GATE_SET.fingerprint(),
        "declared": dict(declared),
        "use_bound": not getattr(args, "no_bound", False),
        "shard": {"k": args.shard[0], "of": args.shard[1]},
        "indices": list(indices),
        "n_documents": n_documents,
    }


def run_stage(stage: str, *, documents: Sequence[tuple[int, Any]], series: Sequence[Any],
              make: EvaluatorFactory, out: ShardFile, declared_true_n: int,
              max_rss_mb: float | None = None, max_documents: int | None = None,
              reports_dir: Path | None = None, log: Callable[[str], None] = print) -> str:
    """Run every document not yet in ``out``. Returns ``done``, ``memory``, ``errors`` or ``limit``."""
    todo = [(i, d) for i, d in documents if i not in out.rows]
    started = time.monotonic()
    for k, (index, document) in enumerate(todo):
        if max_documents is not None and k >= max_documents:
            return "limit"
        try:
            if stage == "screen":
                row = screen_one(index, document, series, make)
            else:
                row = ladder_one(index, document, series, make, declared_true_n, reports_dir)
        except Exception as exc:  # recorded, never skipped silently
            row = {"index": index, "strategy_id": document.strategy_id, "status": "error",
                   "error": f"{type(exc).__name__}: {exc}", "passed": False}
        row["rss_mb"] = round(_rss_mb(), 1)
        out.append(row)
        errors = sum(1 for r in out.rows.values() if r.get("status") == "error")
        if len(out.rows) >= ERROR_STOP_AFTER and errors > ERROR_STOP_RATE * len(out.rows):
            log(f"K6 {stage}: {errors} errors in {len(out.rows)} rows (above "
                f"{ERROR_STOP_RATE:.0%}); stopping: an instrument bug, not a market result.")
            return "errors"
        if k == 0 or (k + 1) % 100 == 0:
            per = (time.monotonic() - started) / (k + 1)
            log(f"K6 {stage}: {k + 1}/{len(todo)} documents, {per:.2f} s each, "
                f"about {per * (len(todo) - k - 1) / 3600:.1f} h left, rss {row['rss_mb']} MB")
        if max_rss_mb is not None and row["rss_mb"] > max_rss_mb:
            log(f"K6 {stage}: resident size {row['rss_mb']} MB above {max_rss_mb}; stopping. "
                "Resume with --resume (nothing is lost).")
            return "memory"
    return "done"


def _load(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], list[tuple[int, Any]]]:
    universe = resolve_universe(args.universe)
    manifest = json.loads((args.generated / k6gen.MANIFEST_NAME).read_text(encoding="utf-8"))
    cells = k6gen.read_cells(args.generated)
    return universe, manifest, cells


def cmd_plan(args: argparse.Namespace) -> int:
    universe, manifest, cells = _load(args)
    tfs = tuple(args.timeframes)
    declared = declared_trials(cells)
    table = deflation_table([PRIOR_TRIALS, declared["declared_true_n"]])
    out = {"universe": universe["name"], "universe_sha256": universe["sha256"],
           "timeframes": list(tfs), "documents": len(cells),
           "screen_trials_this_universe_and_timeframes": screen_trials(cells, tfs),
           "manifest": {k: manifest[k] for k in ("cells_sha256", "hash_chain_sha256",
                                                  "rng_seed", "size", "per_family")},
           "ladder_cap_per_timeframe": LADDER_CAP, "rung0_floor": RUNG0_FLOOR,
           **declared, "deflation": table}
    if args.check:
        filed = filing()
        want = filed["trial_accounting"]["declared"]
        diff = [k for k in ("screen_trials", "ladder_trials_max", "declared_true_n")
                if int(want[k]) != int(declared[k])]
        if diff:
            print(f"PLAN DIFFERS FROM THE PRE-REGISTRATION in {diff}", file=sys.stderr)
            return 3
        if filed["generator"]["cells_sha256"] != manifest["cells_sha256"]:
            print("MANIFEST DIFFERS FROM THE PRE-REGISTRATION", file=sys.stderr)
            return 3
    text = json.dumps(out, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _series(args: argparse.Namespace, universe: Mapping[str, Any]) -> list[Any]:
    from fiboki.core.enums import Timeframe

    instruments = gps.STARTER_INSTRUMENTS if args.starter else universe["instruments"]
    bars_from = None if args.starter else universe["bars_from"]
    series, skipped, _ = gps.load_series(
        starter=args.starter, data_root=args.data_root, instruments=instruments,
        timeframe=Timeframe(args.timeframe), bars_from=bars_from)
    if skipped:
        print(f"K6: series skipped (recorded, not invented): {skipped}", file=sys.stderr)
    return series


def declared_timeframes(cell: Any) -> tuple[str, ...]:
    return ("H4",) if cell.filter == "london_ny" else ("H4", "D1")


def _stage_documents(cells: Sequence[tuple[int, Any]], indices: Sequence[int],
                     instruments: Sequence[str]) -> list[tuple[int, Any]]:
    by_index = dict(cells)
    return [(i, k6gen.materialise(i, by_index[i], instruments)) for i in indices]


def cmd_stage(args: argparse.Namespace) -> int:
    evidence = args.data_root is not None and not args.starter
    if not (args.starter or args.data_root is not None):
        raise SystemExit("pass --data-root (evidence, only once FILED) or --starter (harness)")
    require_filed(evidence)
    universe, manifest, cells = _load(args)
    if evidence:
        filed = filing()
        if filed["generator"]["cells_sha256"] != manifest["cells_sha256"]:
            raise SystemExit("the generated cells are not the filed ones; run generate --check")
        filed_universe = filed["universes"].get(universe["name"], {})
        if filed_universe.get("definition_sha256") != universe["sha256"]:
            raise SystemExit(f"universe {universe['name']} is not the filed definition")
    ok, why = admitted(args.timeframe, universe["name"], args.p0, evidence)
    if not ok:
        raise SystemExit(f"K6 {args.timeframe} on {universe['name']} is not admitted: {why}")
    declared = declared_trials(cells)
    if args.stage == "screen":
        pool = [i for i, cell in cells if args.timeframe in declared_timeframes(cell)]
    else:
        screen = json.loads(args.screen.read_text(encoding="utf-8"))
        if screen.get("stage") != "screen" or screen["timeframe"] != args.timeframe:
            raise SystemExit("--screen must be the merged screen file for this timeframe")
        if screen["manifest"]["cells_sha256"] != manifest["cells_sha256"]:
            raise SystemExit("--screen was run on a different document set")
        pool = sorted(screen["summary"]["survivor_indices"])[:LADDER_CAP]
    indices = [pool[j] for j in shard_indices(len(pool), *args.shard)]
    if args.limit is not None:
        indices = indices[: args.limit]
    series = _series(args, universe)
    instruments = universe["instruments"] if evidence else tuple(s.instrument for s in series)
    header = _header(args, args.stage, universe, manifest, declared, indices, len(pool), evidence)
    if args.stage == "ladder":
        header["planned_indices_total"] = pool
    else:
        header["planned_screen_timeframe_filter"] = args.timeframe
    out = ShardFile(args.out, header, resume=args.resume)
    try:
        docs = _stage_documents(cells, indices, instruments)
        from fiboki.marketstate.calendar import load_research_calendar

        make = default_evaluator_factory(load_research_calendar())
        state = run_stage(args.stage, documents=docs, series=series, make=make, out=out,
                          declared_true_n=declared["declared_true_n"],
                          max_rss_mb=args.max_rss_mb, reports_dir=args.reports_dir,
                          log=lambda m: print(m, file=sys.stderr, flush=True))
    finally:
        out.close()
    print(json.dumps({"stage": args.stage, "state": state, "rows": len(out.rows),
                      "of": len(indices)}))
    return 0 if state == "done" else 75


def cmd_merge(args: argparse.Namespace) -> int:
    merged = merge(args.paths)
    args.out.write_text(json.dumps(merged, indent=1, sort_keys=True, default=str) + "\n",
                        encoding="utf-8")
    print(json.dumps(merged["summary"] if len(json.dumps(merged["summary"])) < 4000 else
                     {k: v for k, v in merged["summary"].items() if k != "survivor_indices"},
                     indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    for path in sorted(args.paths):
        header, rows = read_shard(path)
        n = len(header["indices"])
        secs = [float(r.get("seconds", 0.0)) for r in rows if r.get("status") == "run"]
        per = sum(secs) / len(secs) if secs else float("nan")
        rss = max((float(r.get("rss_mb", 0)) for r in rows), default=0.0)
        print(f"{path.name}: {len(rows)}/{n} rows, {per:.2f} s/doc, "
              f"about {per * (n - len(rows)) / 3600:.1f} h left, peak rss {rss:.0f} MB, "
              f"passed {sum(1 for r in rows if r.get('passed'))}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--universe", default="fx_pooled_k5")
    p.add_argument("--timeframes", nargs="+", default=list(TIMEFRAMES), choices=TIMEFRAMES)
    p.add_argument("--generated", type=Path, default=k6gen.OUT)
    p.add_argument("--check", action="store_true")
    p.add_argument("--out", type=Path, default=None)
    for name in ("screen", "ladder"):
        s = sub.add_parser(name)
        s.set_defaults(stage=name)
        s.add_argument("--universe", default="fx_pooled_k5")
        s.add_argument("--timeframe", required=True, choices=TIMEFRAMES)
        s.add_argument("--generated", type=Path, default=k6gen.OUT)
        src = s.add_mutually_exclusive_group()
        src.add_argument("--data-root", type=Path, default=None)
        src.add_argument("--starter", action="store_true")
        s.add_argument("--p0", type=Path, default=None,
                       help="the merged E-4 P0 file for this timeframe (evidence runs)")
        s.add_argument("--shard", type=int, nargs=2, default=(0, 1), metavar=("K", "N"))
        s.add_argument("--limit", type=int, default=None, help="harness: at most this many")
        s.add_argument("--max-rss-mb", type=float, default=1500.0)
        s.add_argument("--out", type=Path, required=True)
        s.add_argument("--resume", action="store_true")
        s.add_argument("--reports-dir", type=Path, default=None)
        if name == "ladder":
            s.add_argument("--screen", type=Path, required=True)
    m = sub.add_parser("merge")
    m.add_argument("paths", type=Path, nargs="+")
    m.add_argument("--out", type=Path, required=True)
    st = sub.add_parser("status")
    st.add_argument("paths", type=Path, nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd in ("screen", "ladder"):
        return cmd_stage(args)
    if args.cmd == "merge":
        return cmd_merge(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
