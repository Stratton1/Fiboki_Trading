#!/usr/bin/env python
"""E-1: the size and power of the promotion gates, measured.

Pre-registration: ``research/preregistration/gate_calibration_e1.json``. Read it
first; this script is the instrument that file describes, and nothing it prints
may be used to move a gate threshold until the file is FILED (committed, with
its commit recorded) and the full-scale study has been run under it.

What it does
------------
For each injected per-trade Sharpe ``sr`` in the grid and each replicate:

1. build a candidate from one of the pre-registered data-generating processes,
   with a known injected edge of per-trade Sharpe ``sr``;
2. run the FULL :class:`~fiboki.validation.ladder.ValidationLadder` against
   :data:`~fiboki.validation.gates.GATE_SET_V2` (unchanged), with a fresh
   in-memory holdout registry and the campaign's external trial count;
3. record whether it was promoted, which gate bound, every gate's status and
   the trade count of every window the ladder asked about.

``size`` is the promotion rate at ``sr = 0``; ``power(sr)`` the rate above it.
Both are PER-CANDIDATE rates (one simulated candidate per replicate); the
pre-registration's H0 is family-wise over a campaign, see ``SIZE_BASIS_NOTE``.

The three processes
-------------------
``synthetic_gaussian_plateau``
    Gaussian per-period returns on a known plateau (:class:`PlateauEvaluator`).
    Exercises the harness; NOT evidence for or against any threshold.

``block_bootstrap_real_returns`` (evidence)
    Per-trade net returns of the seed documents, from fresh
    ``engine_v3_realism`` runs on real bars (GBP account, IG_REALISTIC, the
    research construction policy, the official calendar), holdout excluded;
    each seed stationary-block-bootstrapped separately, seeds weighted equally
    across replicates, demeaned by the seed's mean and shifted by ``sr * sd``.
    See :class:`TradePool` and :class:`BootstrapReplicate`.

``perturbed_price_paths`` (evidence)
    A real OHLC path whose bar returns are stationary-block-bootstrapped from
    the research window, re-run through the real engine, with a deterministic
    per-entry mark-up overlay calibrated so the realised per-trade Sharpe is
    ``sr``. See :func:`perturb_path` and :class:`OverlayEvaluator`.

The two evidence processes read bars from a marked DataStore (``--data-root``)
or, for tests and pilots, from ``data/starter/histdata`` (``--starter``). A
starter-data run is a pilot: two years of five HistData FX pairs are not the
validated universe the pre-registration names, and its output says
``"evidence": false``.

Usage (tiny scale, as the tests run it)::

    python scripts/gate_power_study.py --replicates 2 --sr 0 0.5 --folds 3 \
        --spa-bootstraps 40 --stress-samples 5 --out e1.json
    python scripts/gate_power_study.py --process block_bootstrap_real_returns \
        --starter --replicates 2 --sr 0 0.5 --out e1_bb.json
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import hashlib
import json
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    ParameterGrid,
    WindowEvaluation,
    params_key,
)
from fiboki.validation.gates import GATE_SET_V2
from fiboki.validation.holdout import DEFAULT_HOLDOUT_FRACTION, HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder

ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION = "research/preregistration/gate_calibration_e1.json"
DATA_START = pd.Timestamp("2014-01-01", tz="UTC")
DATA_END = pd.Timestamp("2024-01-01", tz="UTC")
DATASET_VERSION = "e1_synthetic_v1"
FAST = (5, 10, 15, 20, 25)
SLOW = (20, 30, 40, 50, 60)
DEFAULTS = {"fast": 15, "slow": 40}
PROCESSES = ("synthetic_gaussian_plateau", "block_bootstrap_real_returns", "perturbed_price_paths")
REAL_PROCESSES = ("block_bootstrap_real_returns", "perturbed_price_paths")

SIZE_BASIS_NOTE = (
    "size and power are PER-CANDIDATE promotion rates: each replicate simulates one "
    "candidate whose deflation is charged external_trial_count for the rest of the "
    "campaign. The pre-registration's H0_size is FAMILY-WISE (P(any of "
    "campaign_candidates promoted)); for positively dependent candidates it lies "
    "between the per-candidate rate and 1 - (1 - rate) ** campaign_candidates, and a "
    "per-candidate rate estimated from 400 replicates cannot resolve the family-wise "
    "bound at 106 candidates on its own."
)

# --------------------------------------------------------------------------
# Real-data sources
# --------------------------------------------------------------------------

STARTER_DIR = ROOT / "data" / "starter" / "histdata"
SEED_DIR = ROOT / "research" / "strategies"
CAMPAIGN_SCRIPT = ROOT / "research" / "run_discovery_campaign.py"
STARTER_INSTRUMENTS = ("AUDUSD", "EURUSD", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")
#: K5's universe (research/reports/RESEARCH_LEDGER.md, K5 pre-registration), the
#: default for ``--data-root``: E-1 is run on the series the largest campaign used.
K5_UNIVERSE = (
    "AUDJPY", "AUDUSD", "DE40", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD",
    "NZDUSD", "UK100", "US500", "USDCAD", "USDCHF", "USDJPY", "XAGUSD", "XAUUSD",
)
#: K5's bars_from trim (same ledger entry), the default for ``--data-root``.
K5_BARS_FROM = "2006-01-04"
#: The simulated account: GBP 10,000, the research default (EvaluatorConfig).
NOTIONAL = 10_000.0

#: block_bootstrap_real_returns: an abstract 2 x 2 x 2 grid, the SHAPE of a K5
#: cell (``discovery.campaign.CampaignSpec``: max_grid_points 8, max_values_per_axis
#: 2, three swept axes). Values are grid-step indices; the defaults are a corner so
#: the grid holds points 0, 1, 2 and 3 steps away. Its size is checked against the
#: pre-registration's filed ``grid_points_per_candidate``.
BOOTSTRAP_AXES: dict[str, tuple[int, ...]] = {"a": (0, 1), "b": (0, 1), "c": (0, 1)}
BOOTSTRAP_DEFAULTS = {"a": 0, "b": 0, "c": 0}
#: The same shift within one grid step of the defaults, then a linear decay of
#: this fraction of it per further step (the synthetic process's decay).
PLATEAU_DECAY = 0.15
#: Correlation between two grid points' per-trade returns, the synthetic
#: process's ``common_fraction``. The bootstrap cannot measure the real
#: cross-binding correlation without running every binding; it is declared.
CROSS_GRID_CORRELATION = 0.7
#: perturbed_price_paths: the fraction of entries that carry the mark-up.
OVERLAY_FRACTION = 0.5


def _seed(*parts: object) -> int:
    blob = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big") % (2**63)


def _unit_hash(*parts: object) -> float:
    """A deterministic uniform draw in [0, 1) keyed on ``parts``."""
    return _seed(*parts) / float(2**63)


@dataclass(slots=True)
class PlateauEvaluator:
    """Per-period returns ``mu(params) + sd * noise`` with a shared window factor.

    ``mu`` is ``sr * sd`` at the defaults and falls by ``decay * sr * sd`` per
    grid step (Manhattan distance), floored at 0: a real, parameter-stable edge
    of known size. Deterministic in ``(seed, params, window)``.
    """

    sr: float
    seed: int
    sd: float = 1.0
    decay: float = 0.15
    common_fraction: float = 0.7
    periods_per_day: float = 0.45
    spread_cost: float = 0.02
    calls: int = field(default=0)

    def _mu(self, params: Mapping[str, Any]) -> float:
        d = abs(FAST.index(int(params["fast"])) - FAST.index(DEFAULTS["fast"])) + abs(
            SLOW.index(int(params["slow"])) - SLOW.index(DEFAULTS["slow"])
        )
        return max(0.0, self.sr * self.sd * (1.0 - self.decay * d))

    def __call__(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        from fiboki.core.contracts import Trade
        from fiboki.core.enums import Direction, ExitReason

        self.calls += 1
        n = max(2, int(window.days * self.periods_per_day))
        span = (window.start.isoformat(), window.end.isoformat())
        common = np.random.default_rng(_seed(self.seed, "common", *span)).standard_normal(n)
        idio = np.random.default_rng(
            _seed(self.seed, "idio", params_key(params), *span)
        ).standard_normal(n)
        c = self.common_fraction
        returns = self._mu(params) + self.sd * (np.sqrt(c) * common + np.sqrt(1 - c) * idio)
        step = window.duration / n
        trades = tuple(
            Trade(
                instrument="EURUSD", direction=Direction.LONG, size=1.0,
                entry_price=1.1, exit_price=1.1 + float(r) / 100_000.0,
                entry_time=window.start + step * i,
                exit_time=window.start + step * i + step * 0.9,
                exit_reason=ExitReason.TAKE_PROFIT if r >= 0 else ExitReason.STOP_LOSS,
                gross_pnl=float(r) + self.spread_cost, spread_cost=self.spread_cost,
                commission=0.0, slippage_cost=0.0, financing_cost=0.0,
                net_pnl=float(r), account_ccy="GBP", strategy_id="e1", bars_held=1,
            )
            for i, r in enumerate(returns)
        )
        return WindowEvaluation(
            window=window, params=dict(params), n_trades=n,
            net_profit=float(returns.sum()), returns=returns,
            returns_basis="period", trades=trades,
        )


class RealDataRequired(ValueError):
    """An evidence process was asked for without the real bars it is built from."""


def make_evaluator(process: str, sr: float, seed: int) -> PlateauEvaluator:
    """The evaluator of a process that needs no data: only the synthetic one.

    The two evidence processes are built per replicate from real bars
    (:class:`RealStudy`), so asking for one here is refused rather than
    answered with a synthetic stand-in.
    """
    if process == "synthetic_gaussian_plateau":
        return PlateauEvaluator(sr=sr, seed=seed)
    if process in REAL_PROCESSES:
        raise RealDataRequired(
            f"{process} is built from real bars and engine_v3_realism backtests; "
            "run it with --starter (pilot, not evidence) or --data-root "
            f"(see {PREREGISTRATION})"
        )
    raise ValueError(f"unknown data-generating process {process!r}; known {PROCESSES}")


# --------------------------------------------------------------------------
# Recording what the ladder asked for
# --------------------------------------------------------------------------


def _window_kind(window: DateWindow) -> str:
    name = window.name.split("::", 1)[0]
    if name.startswith("wf_train"):
        return "wf_train"
    if name.startswith("wf_test"):
        return "wf_test"
    return name  # "research" or "holdout"


@dataclass(slots=True)
class RecordingEvaluator:
    """Passes every call through and records the trade count per window kind.

    The ladder never sees this wrapper's records; they are the secondary metric
    "per-window trade count distribution", which matters because K3 to K5 died
    on ``min_trades`` before any statistical gate ran.
    """

    inner: Callable[[Mapping[str, Any], DateWindow], WindowEvaluation]
    calls: int = 0
    trade_counts: dict[str, list[int]] = field(default_factory=dict)
    first_n_trades: int | None = None

    def __call__(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        evaluation = self.inner(params, window)
        self.calls += 1
        if self.first_n_trades is None:
            self.first_n_trades = int(evaluation.n_trades)
        self.trade_counts.setdefault(_window_kind(window), []).append(int(evaluation.n_trades))
        return evaluation


#: Gate inputs whose distribution at sr = 0 is a pre-registered secondary metric.
DISTRIBUTION_GATES = ("deflated_sharpe", "pbo", "spa_consistent_p")


def _run_ladder(
    *,
    candidate: Candidate,
    evaluator: Callable[[Mapping[str, Any], DateWindow], WindowEvaluation],
    registry: HoldoutRegistry,
    dataset_version_id: str,
    config: LadderConfig,
    notes: str,
    sr: float,
    replicate: int,
) -> dict[str, Any]:
    recorder = RecordingEvaluator(evaluator)
    report = ValidationLadder(config=config, gate_set=GATE_SET_V2).run(
        candidate, recorder, registry=registry, dataset_version_id=dataset_version_id,
        actor="scripts/gate_power_study.py", notes=notes,
    )
    binding = report.binding_constraint
    return {
        "sr": sr,
        "replicate": replicate,
        "promoted": bool(report.verdict.promotable),
        "verdict": report.verdict.value,
        "binding_kind": binding.kind,
        "binding": binding.name,
        "evaluations": recorder.calls,
        "gate_status": {g.gate.name: g.status.value for g in report.gate_results},
        "gate_values": {
            g.gate.name: (None if g.value is None or not math.isfinite(g.value) else g.value)
            for g in report.gate_results
            if g.gate.name in DISTRIBUTION_GATES
        },
        "sanity_n_trades": recorder.first_n_trades,
        "trade_counts": {k: v for k, v in sorted(recorder.trade_counts.items())},
    }


def run_one(
    *, process: str, sr: float, replicate: int, config: LadderConfig, base_seed: int
) -> dict[str, Any]:
    """One synthetic-process ladder run (the evidence processes use :class:`RealStudy`)."""
    seed = _seed(base_seed, process, sr, replicate)
    evaluator = make_evaluator(process, sr, seed)
    registry = HoldoutRegistry.in_memory()
    registry.define(DATASET_VERSION, data_start=DATA_START, data_end=DATA_END)
    candidate = Candidate(
        strategy_id="e1_candidate",
        content_hash=hashlib.sha256(f"e1|{process}|{sr}|{replicate}".encode()).hexdigest(),
        default_params=dict(DEFAULTS),
        grid=ParameterGrid.from_axes({"fast": FAST, "slow": SLOW}),
    )
    return _run_ladder(
        candidate=candidate, evaluator=evaluator, registry=registry,
        dataset_version_id=DATASET_VERSION, config=config, notes="E-1 synthetic",
        sr=sr, replicate=replicate,
    )


# --------------------------------------------------------------------------
# The stationary bootstrap (Politis-Romano 1994)
# --------------------------------------------------------------------------


class StationaryStream:
    """Stationary-bootstrap indices into one series, drawn in consecutive chunks.

    Politis, D. and Romano, J. (1994), "The Stationary Bootstrap", JASA 89,
    1303-1313: a block starts at a uniformly chosen observation, each following
    draw continues the block (wrapping circularly) with probability
    ``1 - 1/b``, so block lengths are geometric with mean ``b`` and every
    observation is equally likely at every position (the resample is
    stationary, and its marginal distribution is the series' own).

    ``fiboki.stats.bootstrap.stationary_bootstrap_indices`` is the same scheme
    with the output length fixed to the source length; it is not reused
    because both evidence processes need a different output length (a trade
    calendar filled until a date; a price path as long as the full series while
    drawing only from its research window). Deterministic: the draws depend
    only on the generator and on the sequence of chunk sizes requested.
    """

    def __init__(self, n_obs: int, block_length: float, rng: np.random.Generator) -> None:
        if n_obs < 1:
            raise ValueError("the series needs at least one observation")
        if block_length < 1.0:
            raise ValueError("block_length must be >= 1")
        self.n_obs = int(n_obs)
        self.p_new = 1.0 / float(block_length)
        self.rng = rng
        self._pos = -1

    def draw(self, n: int) -> np.ndarray:
        """The next ``n`` indices."""
        new = self.rng.random(n) < self.p_new
        start = self.rng.integers(0, self.n_obs, size=n)
        out = np.empty(n, dtype=np.int64)
        p = self._pos
        for t in range(n):
            p = int(start[t]) if (p < 0 or new[t]) else (p + 1) % self.n_obs
            out[t] = p
        self._pos = p
        return out


def block_length_for(series: np.ndarray) -> dict[str, Any]:
    """Politis-White (2004) block length with the Patton-Politis-White (2009) correction.

    Politis, D. and White, H. (2004), "Automatic Block-Length Selection for the
    Dependent Bootstrap", Econometric Reviews 23(1), 53-70; Patton, A., Politis,
    D. and White, H. (2009), "Correction to ...", Econometric Reviews 28(4),
    372-375. The estimator is the repository's own
    :func:`fiboki.stats.bootstrap.optimal_block_length` (flat-top kernel,
    ``D_SB = 2 g(0)^2``); the STATIONARY-bootstrap length is the one used.
    """
    from fiboki.stats.bootstrap import optimal_block_length

    est = optimal_block_length(np.asarray(series, dtype=float))
    return {
        "stationary": float(est.stationary),
        "circular": float(est.circular),
        "m_hat": int(est.m_hat),
        "bandwidth": int(est.bandwidth),
        "capped": bool(est.capped),
        "method": "Politis-White (2004) with Patton-Politis-White (2009) correction",
    }


# --------------------------------------------------------------------------
# Bars
# --------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


class StarterStore:
    """``read_latest`` over ``data/starter/histdata``: H1 BID bars only.

    The HistData files are on the fixed EST clock labelled UTC; the provider's
    +5h correction is applied (as ``tests/unit/test_tsmom_seed.py`` does) after
    checking the evidence for that convention, so H4 and D1 buckets are true-UTC
    buckets. Only H1 is served: :func:`build_research_fx_source` then reduces it
    to one daily rate, and says so in its label.
    """

    def __init__(self, directory: Path = STARTER_DIR) -> None:
        self.directory = directory
        self._cache: dict[str, tuple[pd.DataFrame, str]] = {}

    def path(self, instrument: str) -> Path:
        low = instrument.lower()
        return self.directory / low / f"{low}_h1.parquet"

    def h1(self, instrument: str) -> tuple[pd.DataFrame, str]:
        from fiboki.core.enums import Timeframe
        from fiboki.data.providers.histdata import (
            convert_histdata_index,
            detect_timestamp_convention,
        )
        from fiboki.data.schema import PriceBasis, canonical_frame

        sym = instrument.upper()
        if sym not in self._cache:
            path = self.path(sym)
            if not path.exists():
                raise FileNotFoundError(f"no starter file for {sym}: {_rel(path)}")
            raw = pd.read_parquet(path)
            evidence = detect_timestamp_convention(raw.index)
            if not evidence.looks_fixed_offset:
                raise ValueError(f"{_rel(path)}: {evidence.summary()}")
            corrected = raw.set_axis(
                convert_histdata_index(raw.index, already_mislabelled_utc=True), axis=0
            )
            frame = canonical_frame(
                corrected, instrument=sym, timeframe=Timeframe.H1, price_basis=PriceBasis.BID
            )
            self._cache[sym] = (frame, f"starter:{sym}:H1:sha256={_sha256_file(path)}")
        return self._cache[sym]

    def read_latest(self, instrument: str, timeframe: Any, *, kind: Any = None) -> tuple[pd.DataFrame, str]:
        from fiboki.core.enums import Timeframe

        tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
        if tf is not Timeframe.H1:
            raise LookupError(f"the starter store holds H1 only, not {tf.value}")
        return self.h1(instrument)

    def bars(self, instrument: str, timeframe: Any) -> tuple[pd.DataFrame, str]:
        from fiboki.core.enums import Timeframe
        from fiboki.data.resample import resample

        tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
        h1, ref = self.h1(instrument)
        if tf is Timeframe.H1:
            return h1, ref
        return resample(h1, tf, source=Timeframe.H1), f"{ref}->resampled:{tf.value}"


@dataclass(slots=True)
class BarSeries:
    """One instrument's bars as research would trade them, with its holdout fixed."""

    instrument: str
    timeframe: Any
    frame: pd.DataFrame = field(repr=False)
    dataset_version_id: str
    source_ref: str
    price_lineage: dict[str, Any]
    fx: Any = field(repr=False)
    fx_label: str
    research_window: DateWindow
    holdout_window: DateWindow

    def describe(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "timeframe": self.timeframe.value,
            "dataset_version_id": self.dataset_version_id,
            "source_ref": self.source_ref,
            "bars": len(self.frame),
            "data_start": self.frame.index[0].isoformat(),
            "data_end": self.frame.index[-1].isoformat(),
            "research_window": self.research_window.to_dict(),
            "holdout_window_excluded": self.holdout_window.to_dict(),
            "price_lineage": dict(self.price_lineage),
            "fx_label": self.fx_label,
        }


def _series_from_frame(
    *, instrument: str, timeframe: Any, frame: pd.DataFrame, dataset_version_id: str,
    source_ref: str, fx_store: Any,
) -> BarSeries:
    """Mid bars, the GBP rate source, and the holdout exactly as research defines it.

    The holdout segment is ``HoldoutRegistry.define(data_start=first bar,
    data_end=last bar, DEFAULT_HOLDOUT_FRACTION)``, the call
    :func:`fiboki.validation.run.run_validation` makes; everything from its
    ``holdout_start`` on is excluded from every E-1 source.
    """
    from fiboki.core.instruments import get as get_instrument
    from fiboki.core.money import IdentityFxSource
    from fiboki.validation.run import (
        RESEARCH_ACCOUNT_CCY,
        build_research_fx_source,
        research_mid_frame,
    )

    sym = instrument.upper()
    mid, lineage = research_mid_frame(frame, sym)
    columns = [c for c in ("open", "high", "low", "close", "price_basis") if c in mid.columns]
    mid = mid[columns]
    quote = get_instrument(sym).quote.upper()
    if quote == RESEARCH_ACCOUNT_CCY:
        fx: Any = IdentityFxSource()
        fx_label = f"identity({quote}=={RESEARCH_ACCOUNT_CCY})"
    else:
        fx, fx_label = build_research_fx_source(fx_store, quote_currencies=(quote,))
    registry = HoldoutRegistry.in_memory()
    segment = registry.define(
        dataset_version_id, data_start=mid.index[0], data_end=mid.index[-1],
        holdout_fraction=DEFAULT_HOLDOUT_FRACTION,
    )
    return BarSeries(
        instrument=sym, timeframe=timeframe, frame=mid,
        dataset_version_id=dataset_version_id, source_ref=source_ref,
        price_lineage=dict(lineage), fx=fx, fx_label=fx_label,
        research_window=segment.research_window, holdout_window=segment.window,
    )


def load_series(
    *, starter: bool, data_root: Path | None, instruments: Sequence[str],
    timeframe: Any, bars_from: str | None,
) -> tuple[list[BarSeries], list[dict[str, str]], dict[str, Any]]:
    """Every requested series that can be traded in GBP, and why the rest cannot."""
    from fiboki.validation.run import FxSourceUnavailable

    series: list[BarSeries] = []
    skipped: list[dict[str, str]] = []
    trim = pd.Timestamp(bars_from, tz="UTC") if bars_from else None
    store: Any
    if starter:
        store = StarterStore()
        meta: dict[str, Any] = {"mode": "starter", "directory": _rel(STARTER_DIR)}
    else:
        from fiboki.data.store import DataStore

        assert data_root is not None
        store = DataStore(data_root)
        meta = {"mode": "data_root", "data_root": str(data_root)}
    meta["bars_from"] = trim.isoformat() if trim is not None else None
    try:
        for sym in sorted({s.upper() for s in instruments}):
            try:
                if starter:
                    frame, ref = store.bars(sym, timeframe)
                    vid = ref
                else:
                    from fiboki.data.schema import DatasetKind

                    frame, version = store.read_latest(sym, timeframe, kind=DatasetKind.VALIDATED)
                    vid = str(version.version_id)
                    ref = f"store:{vid}"
            except Exception as exc:  # absent bars are recorded, never invented
                skipped.append({"instrument": sym, "reason": f"no bars: {exc}"})
                continue
            if trim is not None:
                frame = frame.loc[frame.index >= trim]
            if len(frame) < 50:
                skipped.append({"instrument": sym, "reason": f"{len(frame)} bars after trim"})
                continue
            try:
                series.append(_series_from_frame(
                    instrument=sym, timeframe=timeframe, frame=frame,
                    dataset_version_id=vid, source_ref=ref, fx_store=store,
                ))
            except FxSourceUnavailable as exc:
                skipped.append({"instrument": sym, "reason": f"no GBP conversion: {exc}"})
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    return series, skipped, meta


def load_documents(ids: Sequence[str] | None) -> list[Any]:
    from fiboki.discovery.campaign import seed_documents

    docs = list(seed_documents(SEED_DIR))
    if ids:
        known = {d.strategy_id: d for d in docs}
        unknown = sorted(set(ids) - set(known))
        if unknown:
            raise ValueError(f"unknown seed documents {unknown}; known {sorted(known)}")
        docs = [known[i] for i in sorted(set(ids))]
    return docs


def campaign_sweep_axes() -> dict[str, tuple[str, ...]]:
    """``SWEEP_AXES`` from research/run_discovery_campaign.py, read without importing it."""
    tree = ast.parse(CAMPAIGN_SCRIPT.read_text(encoding="utf-8"))
    target: ast.expr
    value: ast.expr | None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == "SWEEP_AXES" and value is not None:
            raw = ast.literal_eval(value)
            return {str(k): tuple(str(a) for a in v) for k, v in raw.items()}
    raise LookupError(f"SWEEP_AXES not found in {_rel(CAMPAIGN_SCRIPT)}")


def campaign_grid_shape() -> tuple[int, int]:
    """``(max_grid_points, max_values_per_axis)``: the campaign defaults K5 ran with."""
    from fiboki.discovery.campaign import CampaignSpec

    defaults: dict[str, Any] = {f.name: f.default for f in dataclasses.fields(CampaignSpec)}
    return int(defaults["max_grid_points"]), int(defaults["max_values_per_axis"])


def _evaluator_for(document: Any, series: BarSeries, frame: pd.DataFrame, vid: str,
                   calendar: Any, lineage: Mapping[str, Any], cache: Any = None) -> Any:
    """The research engine path: engine_v3_realism, GBP, IG_REALISTIC, construction_v2."""
    from fiboki.validation.engine_evaluator import EngineEvaluator, EvaluatorConfig

    return EngineEvaluator(
        document=document, frame=frame, dataset_version_id=vid,
        config=EvaluatorConfig(instrument=series.instrument, timeframe=series.timeframe),
        fx=series.fx, fx_label=series.fx_label, cache=cache, blackout=calendar,
        price_basis_lineage=dict(lineage),
    )


def _window_bars(index: pd.DatetimeIndex, window: DateWindow) -> pd.DatetimeIndex:
    lo = int(index.searchsorted(window.start, side="left"))
    hi = int(index.searchsorted(window.end, side="left"))
    return index[lo:hi]


# --------------------------------------------------------------------------
# Process 1: block_bootstrap_real_returns
# --------------------------------------------------------------------------

#: Per-trade record columns, every one a fraction of the equity at entry except
#: the times (seconds) and the labels.
RECORD_FIELDS = ("ret", "spread", "slippage", "financing", "commission", "gap_s", "hold_s")


def seed_trade_records(document: Any, series: BarSeries, calendar: Any) -> dict[str, Any]:
    """Per-trade net returns of ``document``'s default binding on the research window.

    A fresh engine run (never a stored result, so no engine_v2 record can
    enter), through the evaluator research uses, over the RESEARCH window only:
    the holdout is never read. A trade's return is its net P&L divided by the
    account equity at the bar before its entry, so the pool is in fractions of
    equity and seeds of different sizes are comparable; its costs are recorded
    in the same unit so the rung-4 cost stresses see real cost shares.
    """
    evaluator = _evaluator_for(
        document, series, series.frame, series.dataset_version_id, calendar, series.price_lineage
    )
    evaluation = evaluator(document.default_values(), series.research_window)
    bars = _window_bars(series.frame.index, series.research_window)
    if len(bars) != evaluation.returns.size:
        raise RuntimeError(
            f"{document.strategy_id} {series.instrument}: {evaluation.returns.size} period "
            f"returns for {len(bars)} research bars; cannot place trades on the equity curve"
        )
    opening = float(evaluation.meta.get("opening_equity", NOTIONAL))
    equity_after = opening + np.cumsum(evaluation.returns)
    trades = sorted(evaluation.trades, key=lambda t: (t.entry_time, t.exit_time))
    cols: dict[str, list[Any]] = {k: [] for k in (*RECORD_FIELDS, "long", "instrument", "exit_reason")}
    previous = series.research_window.start
    for t in trades:
        pos = int(bars.searchsorted(t.entry_time, side="left")) - 1
        equity = opening if pos < 0 else float(equity_after[pos])
        if equity <= 0:
            raise RuntimeError(f"{document.strategy_id}: non-positive equity before a trade")
        cols["ret"].append(float(t.net_pnl) / equity)
        cols["spread"].append(float(t.spread_cost) / equity)
        cols["slippage"].append(float(t.slippage_cost) / equity)
        cols["financing"].append(float(t.financing_cost) / equity)
        cols["commission"].append(float(t.commission) / equity)
        cols["gap_s"].append(max(0.0, (t.entry_time - previous).total_seconds()))
        cols["hold_s"].append(max(0.0, (t.exit_time - t.entry_time).total_seconds()))
        cols["long"].append(t.direction.value == "long")
        cols["instrument"].append(t.instrument)
        cols["exit_reason"].append(t.exit_reason.value)
        previous = t.entry_time
    return {
        "columns": cols,
        "cell": {
            "instrument": series.instrument,
            "n_trades": len(trades),
            "strategy_content_hash": evaluator.bind(document.default_values()).content_hash(),
            "engine_config_hash": evaluator.engine_config_hash(),
            "ledger_sha256": evaluation.meta.get("ledger_sha256"),
            "research_window": series.research_window.to_dict(),
        },
    }


@dataclass(slots=True)
class SeedSeries:
    """One seed document's per-trade records over its cells, in instrument then time order."""

    strategy_id: str
    data: dict[str, np.ndarray] = field(repr=False)
    block_length: dict[str, Any]
    cells: list[dict[str, Any]]

    @property
    def n(self) -> int:
        return int(self.data["ret"].size)

    @property
    def mean(self) -> float:
        """The mean of this seed's bootstrap distribution: its trades' mean."""
        return float(self.data["ret"].mean())

    @property
    def sd(self) -> float:
        """The sd of this seed's bootstrap distribution (population, ddof = 0)."""
        return float(self.data["ret"].std(ddof=0))


@dataclass(slots=True)
class TradePool:
    """The seeds' per-trade records, one series per seed, weighted equally.

    Each seed is bootstrapped SEPARATELY and replicate ``r`` draws from seed
    ``r % n_seeds``, so over a run every seed contributes the same number of
    simulated candidates whatever its trade count, and a simulated candidate
    carries ONE real seed's joint (return, cost, entry gap, holding time)
    structure: its real fat tails and its real trade frequency. (Mixing seeds
    inside one resample would make the simulated trade rate the reciprocal of
    the seeds' AVERAGE entry gap, which a sparse seed dominates.)
    """

    seeds: list[SeedSeries]
    excluded: list[dict[str, str]]
    digest: str

    def describe(self) -> dict[str, Any]:
        return {
            "weighting": "equal per seed document: replicate r draws from seeds[r % n_seeds]",
            "return_unit": "net P&L / account equity at the bar before entry",
            "demeaned_by": "the seed's own trade mean (its bootstrap distribution's mean)",
            "shift": "sr * the seed's trade sd (ddof 0), scaled by the grid-distance factor",
            "pool_sha256": self.digest,
            "excluded_seeds": self.excluded,
            "seeds": [
                {
                    "strategy_id": s.strategy_id,
                    "n_trades": s.n,
                    "mean": s.mean,
                    "sd": s.sd,
                    "per_trade_sharpe_before_demeaning": s.mean / s.sd if s.sd > 0 else None,
                    "block_length": s.block_length,
                    "cells": s.cells,
                }
                for s in self.seeds
            ],
        }


def build_pool(records: Mapping[str, list[dict[str, Any]]]) -> TradePool:
    """Concatenate each seed's cells (instrument order) and estimate its block length.

    A seed with fewer than 8 trades in total cannot have a block length
    estimated (the estimator's floor) and is excluded, by name, in the output.
    """
    seeds: list[SeedSeries] = []
    excluded: list[dict[str, str]] = []
    digest = hashlib.sha256()
    for sid in sorted(records):
        parts = records[sid]
        data: dict[str, np.ndarray] = {}
        for key in (*RECORD_FIELDS, "long", "instrument", "exit_reason"):
            values = [v for p in parts for v in p["columns"][key]]
            data[key] = np.asarray(values, dtype=float if key in RECORD_FIELDS else object)
        data["long"] = data["long"].astype(bool)
        n = int(data["ret"].size)
        if n < 8 or float(data["ret"].std()) == 0.0:
            excluded.append({
                "strategy_id": sid,
                "reason": f"{n} trades; a block length needs 8 with non-zero dispersion",
            })
            continue
        digest.update(sid.encode("utf-8"))
        for key in RECORD_FIELDS:
            digest.update(np.ascontiguousarray(data[key]).tobytes())
        seeds.append(SeedSeries(sid, data, block_length_for(data["ret"]), [p["cell"] for p in parts]))
    if not seeds:
        raise RuntimeError(f"no seed document produced 8 trades on these bars: {excluded}")
    return TradePool(seeds, excluded, digest.hexdigest())


def plateau_factor(distance: int) -> float:
    """The injected shift at ``distance`` grid steps, as a fraction of the peak's."""
    if distance <= 1:
        return 1.0
    return max(0.0, 1.0 - PLATEAU_DECAY * (distance - 1))


def _chunked_calendar(
    stream: StationaryStream, gap_s: np.ndarray, span_ns: int
) -> np.ndarray:
    """Draw indices until the cumulative entry gaps pass ``span_ns``."""
    drawn: list[np.ndarray] = []
    elapsed = 0
    while elapsed < span_ns:
        idx = stream.draw(256)
        drawn.append(idx)
        elapsed += int(np.round(gap_s[idx] * 1e9).astype(np.int64).sum())
        if len(drawn) > 100_000:  # pragma: no cover - a series of zero gaps
            raise RuntimeError("the seed's entry gaps never advance the calendar")
    return np.concatenate(drawn)


@dataclass(slots=True)
class BootstrapReplicate:
    """One simulated dataset: a trade calendar and every grid point's resampled trades.

    Everything is drawn from ONE seed's series (``TradePool``). The trade TIMES
    come from one "common" stationary-bootstrap stream: each draw carries the
    real gap since the previous entry and the real holding time of the trade it
    copies, so the simulated trade frequency and holding periods are the
    seed's own, and every grid point shares one trade calendar (which is what
    lets the ladder align the trials matrix, as every binding of an engine
    evaluation shares one bar calendar). Each grid point's per-trade RECORD
    (return and costs) is the common draw with probability
    ``sqrt(CROSS_GRID_CORRELATION)`` and its own independent stationary-
    bootstrap draw otherwise, so two grid points' returns correlate at
    ``CROSS_GRID_CORRELATION`` while each keeps the seed's marginal
    distribution, fat tails included. The simulated span is the longest source
    series' full span (research plus holdout); its holdout is simulated data.
    """

    seed_series: SeedSeries
    seed: int
    start: pd.Timestamp
    end: pd.Timestamp
    step_minutes: int
    entry_ns: np.ndarray = field(init=False, repr=False)
    exit_ns: np.ndarray = field(init=False, repr=False)
    common: np.ndarray = field(init=False, repr=False)
    _columns: dict[int, dict[str, np.ndarray]] = field(init=False, default_factory=dict, repr=False)

    def _stream(self, *label: object) -> StationaryStream:
        s = self.seed_series
        return StationaryStream(
            s.n, s.block_length["stationary"], np.random.default_rng(_seed(self.seed, *label))
        )

    def __post_init__(self) -> None:
        data = self.seed_series.data
        idx = _chunked_calendar(self._stream("common"), data["gap_s"], int((self.end - self.start).value))
        entry = self.start.value + np.cumsum(np.round(data["gap_s"][idx] * 1e9).astype(np.int64))
        keep = entry < self.end.value
        self.entry_ns = entry[keep]
        self.exit_ns = self.entry_ns + np.round(data["hold_s"][idx[keep]] * 1e9).astype(np.int64)
        self.common = idx[keep]

    @property
    def n_trades(self) -> int:
        return int(self.entry_ns.size)

    def column(self, j: int) -> dict[str, np.ndarray]:
        """Grid point ``j``'s per-trade records, built once per replicate."""
        if j not in self._columns:
            n = self.n_trades
            idio = self._stream("idio", j).draw(n) if n else np.zeros(0, dtype=np.int64)
            mix = np.random.default_rng(_seed(self.seed, "mix", j)).random(n)
            chosen = np.where(mix < math.sqrt(CROSS_GRID_CORRELATION), self.common, idio)
            data = self.seed_series.data
            self._columns[j] = {
                key: data[key][chosen] for key in (*RECORD_FIELDS, "long", "instrument", "exit_reason")
            }
        return self._columns[j]


@dataclass(slots=True)
class BootstrapEvaluator:
    """Grid point ``params`` of a :class:`BootstrapReplicate`, with the edge injected.

    Per-trade return = resampled net return - seed mean + ``factor(d) * sr * sd``,
    where ``d`` is the grid-step distance from the defaults
    (:func:`plateau_factor`). Returned on a ``"period"`` basis like the
    production :class:`EngineEvaluator`: one element per bar of a regular
    ``step_minutes`` calendar over the window, zero except at the bar a trade
    exits, where it is the trade's P&L on a non-compounding GBP 10,000 account
    (so the column sums to the window's net profit). Trades are those ENTERED
    in the window; one still open at the window's end is closed inside it with
    its whole return, as ``close_at_end_of_data`` does in the engine.
    ``opening_equity`` is recorded so walk-forward measures log growth, as it
    does for every engine evaluation. Costs are the copied trade's own cost
    shares, so the rung-4 spread stress sees real costs.
    """

    replicate: BootstrapReplicate
    sr: float
    grid_index: dict[str, int]
    distances: dict[str, int]
    dataset_version_id: str

    def __call__(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        from fiboki.core.contracts import Trade
        from fiboki.core.enums import Direction, ExitReason

        rep = self.replicate
        seed_series = rep.seed_series
        key = params_key(params)
        j = self.grid_index[key]
        shift = self.sr * plateau_factor(self.distances[key]) * seed_series.sd
        step = rep.step_minutes * 60 * 1_000_000_000
        lo_bar = -(-(window.start.value - rep.start.value) // step)
        hi_bar = -(-(window.end.value - rep.start.value) // step)
        n_bars = int(hi_bar - lo_bar)
        if n_bars < 1:
            raise ValueError(f"window {window} holds no bars of the simulated calendar")
        a = int(np.searchsorted(rep.entry_ns, window.start.value, side="left"))
        b = int(np.searchsorted(rep.entry_ns, window.end.value, side="left"))
        col = rep.column(j)
        ret = col["ret"][a:b] - seed_series.mean + shift
        exit_bar = np.clip((rep.exit_ns[a:b] - rep.start.value) // step, lo_bar, hi_bar - 1)
        pnl = ret * NOTIONAL
        returns = np.zeros(n_bars, dtype=float)
        np.add.at(returns, (exit_bar - lo_bar).astype(np.int64), pnl)
        trades = []
        for i in range(b - a):
            k = a + i
            costs = {
                c: float(col[c][k]) * NOTIONAL
                for c in ("spread", "slippage", "financing", "commission")
            }
            entry_ns = int(rep.entry_ns[k])
            # A trade still open at the window's end is closed inside it.
            exit_ns = max(entry_ns, min(int(rep.exit_ns[k]), window.end.value - 1))
            entry_bar = (entry_ns - rep.start.value) // step
            net = float(pnl[i])
            trades.append(Trade(
                instrument=str(col["instrument"][k]),
                direction=Direction.LONG if bool(col["long"][k]) else Direction.SHORT,
                size=1.0, entry_price=1.0, exit_price=1.0 + float(ret[i]),
                entry_time=pd.Timestamp(entry_ns, tz="UTC"),
                exit_time=pd.Timestamp(exit_ns, tz="UTC"),
                exit_reason=ExitReason(str(col["exit_reason"][k])),
                gross_pnl=net + sum(costs.values()), spread_cost=costs["spread"],
                commission=costs["commission"], slippage_cost=costs["slippage"],
                financing_cost=costs["financing"], net_pnl=net, account_ccy="GBP",
                strategy_id="e1_bootstrap", bars_held=max(1, int(exit_bar[i]) - int(entry_bar)),
            ))
        return WindowEvaluation(
            window=window, params=dict(params), n_trades=len(trades),
            net_profit=float(pnl.sum()), returns=returns, returns_basis="period",
            trades=tuple(trades),
            meta={"opening_equity": NOTIONAL, "dataset_version_id": self.dataset_version_id},
        )


def bootstrap_grid() -> tuple[ParameterGrid, dict[str, int], dict[str, int]]:
    grid = ParameterGrid.from_axes(BOOTSTRAP_AXES)
    index: dict[str, int] = {}
    distance: dict[str, int] = {}
    for j, point in enumerate(grid.points()):
        key = params_key(point)
        index[key] = j
        distance[key] = sum(abs(int(point[k]) - BOOTSTRAP_DEFAULTS[k]) for k in point)
    return grid, index, distance


# --------------------------------------------------------------------------
# Process 2: perturbed_price_paths
# --------------------------------------------------------------------------


def perturb_path(
    frame: pd.DataFrame, research_window: DateWindow, rng: np.random.Generator
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """A price path with the real start, calendar and bar count, and resampled bar returns.

    Source: every bar ``t`` inside the research window (holdout excluded) with
    a previous bar, described RELATIVE TO THE PREVIOUS CLOSE as the four
    ratios ``open_t/close_{t-1}``, ``high_t/...``, ``low_t/...``,
    ``close_t/...``. A stationary bootstrap (block length from
    :func:`block_length_for` on the log close-to-close returns) draws
    ``len(frame) - 1`` source bars; the path starts with the real first bar and
    each following bar is the previous SIMULATED close times the drawn bar's
    four ratios.

    Approximation, stated: a bar's open, high and low are reconstructed
    PROPORTIONALLY from the resampled close-to-close move (the drawn bar's
    shape relative to its own previous close, rescaled to the new level), so
    intrabar shape travels with the return it came with, including the gap
    from the previous close to the open. The timestamps are the real ones, so a
    weekend-gap return can land mid-week and vice versa, and volatility
    seasonality by hour or weekday is destroyed along with the serial
    structure. Scaling a coherent bar by a positive factor keeps it coherent;
    the output is nonetheless checked and any bar needing ``high``/``low``
    widened to contain ``open``/``close`` is counted in ``coherence_clamped``.
    """
    index = frame.index
    bars = _window_bars(index, research_window)
    lo = max(1, int(index.searchsorted(bars[0])))
    hi = int(index.searchsorted(research_window.end, side="left"))
    o = frame["open"].to_numpy(dtype=float)
    h = frame["high"].to_numpy(dtype=float)
    lw = frame["low"].to_numpy(dtype=float)
    c = frame["close"].to_numpy(dtype=float)
    prev = c[lo - 1:hi - 1]
    ratios = np.column_stack([o[lo:hi], h[lo:hi], lw[lo:hi], c[lo:hi]]) / prev[:, None]
    if not np.isfinite(ratios).all() or (ratios <= 0).any():
        raise ValueError("source bars contain non-positive or non-finite prices")
    log_returns = np.log(ratios[:, 3])
    block = block_length_for(log_returns)
    n_out = len(index) - 1
    draws = StationaryStream(ratios.shape[0], block["stationary"], rng).draw(n_out)
    drawn = ratios[draws]
    # Sequential products, so close_t is bit-for-bit prev_close_t * ratio: the
    # same float operation that builds the bar's open, high and low.
    close = np.cumprod(np.concatenate([[c[0]], drawn[:, 3]]))[1:]
    prev_close = np.concatenate([[c[0]], close[:-1]])
    new = np.empty((len(index), 4), dtype=float)
    new[0] = (o[0], h[0], lw[0], c[0])
    new[1:, 0] = prev_close * drawn[:, 0]
    new[1:, 1] = prev_close * drawn[:, 1]
    new[1:, 2] = prev_close * drawn[:, 2]
    new[1:, 3] = close
    top = np.maximum.reduce([new[:, 0], new[:, 1], new[:, 3]])
    bottom = np.minimum.reduce([new[:, 0], new[:, 2], new[:, 3]])
    clamped = int(((top != new[:, 1]) | (bottom != new[:, 2])).sum())
    new[:, 1], new[:, 2] = top, bottom
    path = pd.DataFrame(new, index=index, columns=["open", "high", "low", "close"])
    if "price_basis" in frame.columns:
        path["price_basis"] = frame["price_basis"].to_numpy()
    info = {
        "block_length": block,
        "n_source_bars": int(ratios.shape[0]),
        "source_window": research_window.to_dict(),
        "n_bars": len(path),
        "coherence_clamped": clamped,
        "path_sha256": hashlib.sha256(np.ascontiguousarray(new).tobytes()).hexdigest(),
    }
    return path, info


def solve_markup(returns: np.ndarray, marked: np.ndarray, sr: float) -> float:
    """The mark-up ``m`` with Sharpe(returns + m * marked) == sr exactly (ddof = 1).

    With ``a, v`` the mean and variance of the returns, ``b, w`` of the 0/1
    mark and ``c`` their covariance (all population moments, ``k = n/(n-1)``
    converting to the sample standard deviation the ladder uses):
    ``(a + m b)^2 = sr^2 k (v + 2 m c + m^2 w)``, a quadratic in ``m``; the
    root with ``a + m b >= 0`` is the one whose Sharpe is ``+sr`` rather than
    ``-sr``. At ``sr = 0`` it is ``-a / b``: the overlay removes the path's own
    expectancy, so the null is zero net expectancy, as in the bootstrap process.
    """
    r = np.asarray(returns, dtype=float)
    x = np.asarray(marked, dtype=float)
    n = r.size
    if n < 2:
        raise ValueError("a per-trade Sharpe needs at least two trades")
    if sr < 0:
        raise ValueError("sr must be non-negative")
    a, b = float(r.mean()), float(x.mean())
    if b <= 0.0:
        raise ValueError("no trade carries the mark-up")
    v = float(((r - a) ** 2).mean())
    w = float(((x - b) ** 2).mean())
    cov = float(((r - a) * (x - b)).mean())
    if sr == 0.0:
        return -a / b
    k = n / (n - 1)
    qa = b * b - sr * sr * k * w
    qb = 2.0 * a * b - 2.0 * sr * sr * k * cov
    qc = a * a - sr * sr * k * v
    if abs(qa) < 1e-15:
        roots = [-qc / qb] if qb != 0.0 else []
    else:
        disc = qb * qb - 4.0 * qa * qc
        if disc < 0.0:
            raise ValueError(f"no mark-up reaches a per-trade Sharpe of {sr}")
        root = math.sqrt(disc)
        roots = sorted({(-qb - root) / (2.0 * qa), (-qb + root) / (2.0 * qa)})
    valid = [m for m in roots if a + m * b >= 0.0]
    if not valid:
        raise ValueError(f"no mark-up reaches a per-trade Sharpe of +{sr}")
    return min(valid, key=abs)


@dataclass(slots=True)
class OverlayEvaluator:
    """The real engine's evaluation, plus a deterministic per-entry mark-up.

    An entry is marked when ``_unit_hash(replicate seed, entry time,
    direction) < OVERLAY_FRACTION``, so the SAME entry made by two bindings is
    marked in both and the edge belongs to the signal, not to a binding. A
    marked trade's net (and gross) P&L gains ``markup`` GBP, booked on the
    period return of the bar it exits on; costs are untouched. ``markup`` is
    calibrated per replicate by :func:`solve_markup` on the defaults' untouched
    trades from INDEPENDENT paths of the same cell
    (:meth:`RealStudy._calibration_sample`), never on the path being evaluated.
    Approximation, stated: the mark-up is added after the run, so it does not
    feed back into the equity that sizes later trades or into the construction
    policy's realised volatility.
    """

    inner: Any
    markup: float
    seed: int
    frame_index: pd.DatetimeIndex = field(repr=False)

    def marks(self, trades: Sequence[Any]) -> np.ndarray:
        return np.array(
            [
                _unit_hash(self.seed, "overlay", t.entry_time.isoformat(), t.direction.value)
                < OVERLAY_FRACTION
                for t in trades
            ],
            dtype=bool,
        )

    def __call__(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        ev = self.inner(params, window)
        if not ev.trades or self.markup == 0.0:
            return ev
        marked = self.marks(ev.trades)
        bars = _window_bars(self.frame_index, window)
        if len(bars) != ev.returns.size:
            raise RuntimeError("period returns do not align with the window's bars")
        returns = ev.returns.copy()
        trades = []
        for t, m in zip(ev.trades, marked, strict=True):
            if m:
                pos = min(max(int(bars.searchsorted(t.exit_time, side="right")) - 1, 0), len(bars) - 1)
                returns[pos] += self.markup
                t = dataclasses.replace(
                    t, net_pnl=t.net_pnl + self.markup, gross_pnl=t.gross_pnl + self.markup
                )
            trades.append(t)
        meta = dict(ev.meta)
        meta["e1_overlay"] = {"markup_gbp": self.markup, "n_marked": int(marked.sum())}
        return WindowEvaluation(
            window=ev.window, params=dict(ev.params), n_trades=ev.n_trades,
            net_profit=float(ev.net_profit + self.markup * int(marked.sum())),
            returns=returns, returns_basis=ev.returns_basis, trades=tuple(trades),
            notes=ev.notes, meta=meta,
        )


# --------------------------------------------------------------------------
# The real-data study
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RealStudy:
    """Everything an evidence process needs, built once and recorded."""

    process: str
    series: list[BarSeries]
    skipped: list[dict[str, str]]
    source_meta: dict[str, Any]
    documents: list[Any]
    calendar: Any
    config: LadderConfig
    base_seed: int
    pool: TradePool | None = None
    cells: list[tuple[Any, BarSeries]] = field(default_factory=list)
    sweep_axes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    grid_shape: tuple[int, int] = (8, 2)
    calibration_paths: int = 4

    @classmethod
    def build(cls, process: str, args: argparse.Namespace, config: LadderConfig) -> RealStudy:
        from fiboki.core.enums import Timeframe
        from fiboki.marketstate.calendar import load_official_calendar

        tf = Timeframe(args.timeframe)
        instruments = args.instruments or (STARTER_INSTRUMENTS if args.starter else K5_UNIVERSE)
        bars_from = args.bars_from if args.bars_from is not None else (
            None if args.starter else K5_BARS_FROM
        )
        series, skipped, meta = load_series(
            starter=args.starter, data_root=args.data_root, instruments=instruments,
            timeframe=tf, bars_from=bars_from,
        )
        if not series:
            raise RuntimeError(f"no usable series: {skipped}")
        documents = load_documents(args.documents)
        study = cls(
            process=process, series=series, skipped=skipped, source_meta=meta,
            documents=documents, calendar=load_official_calendar(), config=config,
            base_seed=args.seed, sweep_axes=campaign_sweep_axes(), grid_shape=campaign_grid_shape(),
            calibration_paths=int(args.calibration_paths),
        )
        for doc in documents:
            for s in series:
                if s.instrument not in doc.universe or tf not in doc.timeframes:
                    study.skipped.append({
                        "instrument": s.instrument, "strategy_id": doc.strategy_id,
                        "reason": "outside the document's universe or timeframes",
                    })
                    continue
                study.cells.append((doc, s))
        if not study.cells:
            raise RuntimeError("no (document, series) cell is inside a document's universe")
        if process == "block_bootstrap_real_returns":
            records: dict[str, list[dict[str, Any]]] = {}
            for doc, s in study.cells:
                records.setdefault(doc.strategy_id, []).append(
                    seed_trade_records(doc, s, study.calendar)
                )
            study.pool = build_pool(records)
        return study

    def describe(self) -> dict[str, Any]:
        from fiboki.validation.engine_evaluator import blackout_fingerprint

        out: dict[str, Any] = {
            **self.source_meta,
            "series": [s.describe() for s in self.series],
            "skipped": self.skipped,
            "documents": [
                {"strategy_id": d.strategy_id, "binding": "declared defaults",
                 "default_binding_content_hash": d.bind_defaults().content_hash()}
                for d in self.documents
            ],
            "calendar": blackout_fingerprint(self.calendar),
            "engine": "engine_v3_realism via fiboki.validation.engine_evaluator.EngineEvaluator "
                      "(GBP 10,000, IG_REALISTIC, fixed_fractional_v2, research construction)",
            "holdout": f"HoldoutRegistry.define(first bar, last bar, {DEFAULT_HOLDOUT_FRACTION}); "
                       "the holdout segment is excluded from every source",
        }
        if self.pool is not None:
            out["pool"] = self.pool.describe()
            out["grid"] = {
                "axes": {k: list(v) for k, v in BOOTSTRAP_AXES.items()},
                "defaults": BOOTSTRAP_DEFAULTS,
                "shift_by_distance": {d: plateau_factor(d) for d in range(4)},
                "cross_grid_correlation": CROSS_GRID_CORRELATION,
            }
        else:
            out["cells"] = [f"{d.strategy_id}@{s.instrument}" for d, s in self.cells]
            out["cell_rotation"] = "replicate r runs cells[r % len(cells)]"
            out["grid"] = {
                "max_grid_points": self.grid_shape[0],
                "max_values_per_axis": self.grid_shape[1],
                "sweep_axes": "research/run_discovery_campaign.py SWEEP_AXES",
            }
            out["overlay_fraction"] = OVERLAY_FRACTION
            out["calibration_paths"] = self.calibration_paths
        return out

    def _span(self) -> tuple[pd.Timestamp, pd.Timestamp, int]:
        longest = max(self.series, key=lambda s: (s.frame.index[-1] - s.frame.index[0], s.instrument))
        return longest.frame.index[0], longest.frame.index[-1], int(longest.timeframe.minutes)

    def replicate_rows(self, replicate: int, srs: Sequence[float]) -> list[dict[str, Any]]:
        """Every sr for one replicate, on ONE simulated dataset (common random numbers).

        The resample depends on the replicate only; ``sr`` changes only the
        injected shift (bootstrap) or mark-up (paths), so differences between
        sr cells are not Monte Carlo noise between datasets.
        """
        seed = _seed(self.base_seed, self.process, "replicate", replicate)
        if self.process == "block_bootstrap_real_returns":
            return self._bootstrap_rows(replicate, seed, srs)
        return self._path_rows(replicate, seed, srs)

    def _bootstrap_rows(self, replicate: int, seed: int, srs: Sequence[float]) -> list[dict[str, Any]]:
        assert self.pool is not None
        start, end, minutes = self._span()
        seed_series = self.pool.seeds[replicate % len(self.pool.seeds)]
        rep = BootstrapReplicate(seed_series, seed, start, end, minutes)
        grid, index, distance = bootstrap_grid()
        vid = f"e1_bootstrap::{self.pool.digest[:16]}::r{replicate}"
        rows = []
        for sr in srs:
            registry = HoldoutRegistry.in_memory()
            registry.define(vid, data_start=start, data_end=end)
            candidate = Candidate(
                strategy_id="e1_bootstrap_candidate",
                content_hash=hashlib.sha256(f"e1|{self.process}|{sr}|{replicate}".encode()).hexdigest(),
                default_params=dict(BOOTSTRAP_DEFAULTS), grid=grid,
            )
            row = _run_ladder(
                candidate=candidate,
                evaluator=BootstrapEvaluator(rep, sr, index, distance, vid),
                registry=registry, dataset_version_id=vid, config=self.config,
                notes="E-1 block_bootstrap_real_returns", sr=sr, replicate=replicate,
            )
            row["seed_document"] = seed_series.strategy_id
            row["simulated_trades"] = rep.n_trades
            rows.append(row)
        return rows

    def _calibration_sample(
        self, doc: Any, series: BarSeries, base: Candidate, seed: int
    ) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """Pooled untouched trades of the defaults on ``calibration_paths`` INDEPENDENT paths.

        Independent of the evaluated path on purpose. Calibrating on the path the
        ladder then evaluates would force the defaults' research-window mean to
        exactly ``sr * sd`` -- rung 0 would see zero expectancy at ``sr = 0`` on
        every replicate and the sampling noise the gates exist to judge would be
        gone. Drawn from the same source, cell and block length, so the mark-up
        targets the process's EXPECTED per-trade Sharpe; the calibration's own
        noise adds a factor of about ``1 + 1/K`` to the variance of the defaults'
        mean, which can only make size look WORSE, never better.
        """
        pnl: list[float] = []
        marks: list[bool] = []
        hashes: list[str] = []
        probe = OverlayEvaluator(None, 0.0, seed, series.frame.index)
        for k in range(self.calibration_paths):
            path, info = perturb_path(
                series.frame, series.research_window,
                np.random.default_rng(_seed(seed, "calibration", k)),
            )
            vid = f"e1_paths_calibration::{info['path_sha256'][:16]}"
            engine = _evaluator_for(doc, series, path, vid, self.calendar, series.price_lineage)
            segment = HoldoutRegistry.in_memory().define(
                vid, data_start=path.index[0], data_end=path.index[-1]
            )
            untouched = engine(base.default_params, segment.research_window)
            pnl.extend(float(t.net_pnl) for t in untouched.trades)
            marks.extend(bool(x) for x in probe.marks(untouched.trades))
            hashes.append(info["path_sha256"])
        return np.asarray(pnl, dtype=float), np.asarray(marks, dtype=bool), hashes

    def _path_rows(self, replicate: int, seed: int, srs: Sequence[float]) -> list[dict[str, Any]]:
        from fiboki.validation.engine_evaluator import EvaluationCache

        doc, series = self.cells[replicate % len(self.cells)]
        path, info = perturb_path(
            series.frame, series.research_window, np.random.default_rng(_seed(seed, "path"))
        )
        vid = f"e1_paths::{series.dataset_version_id}::{info['path_sha256'][:16]}"
        lineage = {**series.price_lineage, "e1_perturbation": "stationary bootstrap of research-window bars"}
        # One in-memory cache per replicate: every sr re-uses the same engine runs,
        # and the overlay is applied after the cache.
        engine = _evaluator_for(doc, series, path, vid, self.calendar, lineage, cache=EvaluationCache())
        base = engine.candidate(
            max_points=self.grid_shape[0], max_values_per_axis=self.grid_shape[1],
            include=self.sweep_axes.get(doc.strategy_id),
        )
        segment = HoldoutRegistry.in_memory().define(
            vid, data_start=path.index[0], data_end=path.index[-1]
        )
        untouched = engine(base.default_params, segment.research_window)
        eval_pnl = np.array([t.net_pnl for t in untouched.trades], dtype=float)
        cal_pnl, cal_marks, cal_hashes = self._calibration_sample(doc, series, base, seed)
        rows = []
        for sr in srs:
            overlay = OverlayEvaluator(engine, 0.0, seed, path.index)
            try:
                markup = solve_markup(cal_pnl, cal_marks, sr)
                status = "calibrated"
            except ValueError as exc:
                # Only a candidate that rung 0 rejects on its trade count whatever
                # the mark-up may run uncalibrated; anything else would be a row
                # labelled sr that did not have sr injected.
                if (cal_pnl.size >= 2 and cal_marks.any()) or eval_pnl.size >= self.config.min_trades:
                    raise ValueError(
                        f"perturbed_price_paths cannot inject sr={sr} "
                        f"({doc.strategy_id}@{series.instrument}, replicate {replicate}): {exc}"
                    ) from exc
                markup = 0.0
                status = (
                    f"not calibrated ({exc}); the defaults trade {eval_pnl.size} times, "
                    f"below min_trades {self.config.min_trades}, so rung 0 rejects regardless"
                )
            overlay.markup = markup
            cal_adj = cal_pnl + markup * cal_marks
            eval_adj = eval_pnl + markup * overlay.marks(untouched.trades)

            def _sharpe(x: np.ndarray) -> float | None:
                return float(x.mean() / x.std(ddof=1)) if x.size > 1 and x.std(ddof=1) > 0 else None

            calibration = {
                "status": status,
                "on": (
                    f"the defaults' untouched research-window trades on {self.calibration_paths} "
                    "independent perturbed paths of the same cell"
                ),
                "calibration_path_sha256": cal_hashes,
                "markup_gbp": markup,
                "overlay_fraction": OVERLAY_FRACTION,
                "calibration_n_trades": int(cal_pnl.size),
                "calibration_n_marked": int(cal_marks.sum()),
                "calibration_per_trade_sharpe": _sharpe(cal_adj),
                "evaluated_path_defaults_n_trades": int(eval_pnl.size),
                "evaluated_path_defaults_per_trade_sharpe": _sharpe(eval_adj),
            }
            registry = HoldoutRegistry.in_memory()
            registry.define(vid, data_start=path.index[0], data_end=path.index[-1])
            candidate = Candidate(
                strategy_id=base.strategy_id,
                content_hash=hashlib.sha256(
                    f"{base.content_hash}|e1|{sr}|{replicate}".encode()
                ).hexdigest(),
                default_params=base.default_params, grid=base.grid, document=base.document,
            )
            row = _run_ladder(
                candidate=candidate, evaluator=overlay, registry=registry,
                dataset_version_id=vid, config=self.config,
                notes="E-1 perturbed_price_paths", sr=sr, replicate=replicate,
            )
            row["cell"] = f"{doc.strategy_id}@{series.instrument}"
            row["path"] = {k: info[k] for k in ("block_length", "n_bars", "coherence_clamped", "path_sha256")}
            row["calibration"] = calibration
            rows.append(row)
        return rows


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_sr: dict[float, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_sr.setdefault(float(r["sr"]), []).append(r)
    out: dict[str, Any] = {}
    for sr, group in sorted(by_sr.items()):
        n = len(group)
        k = sum(1 for g in group if g["promoted"])
        p = k / n
        out[f"{sr:g}"] = {
            "n": n,
            "promoted": k,
            "rate": p,
            "monte_carlo_se": float(np.sqrt(p * (1 - p) / n)) if n else None,
            "role": "size" if sr == 0.0 else "power",
        }
    return out


def _quantiles(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=float)
    q = np.quantile(arr, [0.0, 0.1, 0.5, 0.9, 1.0])
    return {"n": int(arr.size), "min": float(q[0]), "p10": float(q[1]), "median": float(q[2]),
            "p90": float(q[3]), "max": float(q[4]), "mean": float(arr.mean())}


def secondary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per sr: which gate bound, every gate's status counts, trade-count and gate-value distributions."""
    by_sr: dict[float, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_sr.setdefault(float(r["sr"]), []).append(r)
    out: dict[str, Any] = {}
    for sr, group in sorted(by_sr.items()):
        n = len(group)
        binding: dict[str, int] = {}
        status: dict[str, dict[str, int]] = {}
        counts: dict[str, list[int]] = {}
        values: dict[str, list[float]] = {}
        for g in group:
            name = f"{g['binding_kind']}:{g['binding']}" if g["binding_kind"] != "none" else "none"
            binding[name] = binding.get(name, 0) + 1
            for gate, st in g["gate_status"].items():
                status.setdefault(gate, {})
                status[gate][st] = status[gate].get(st, 0) + 1
            for kind, c in g["trade_counts"].items():
                counts.setdefault(kind, []).extend(c)
            if g.get("sanity_n_trades") is not None:
                counts.setdefault("sanity_defaults", []).append(int(g["sanity_n_trades"]))
            for gate, v in g["gate_values"].items():
                if v is not None:
                    values.setdefault(gate, []).append(float(v))
        out[f"{sr:g}"] = {
            "binding_rate": {k: v / n for k, v in sorted(binding.items())},
            "gate_rejection_rate": {
                gate: (st.get("fail", 0) + st.get("not_evaluated", 0)) / n
                for gate, st in sorted(status.items())
            },
            "gate_status_counts": {k: dict(sorted(v.items())) for k, v in sorted(status.items())},
            "trade_counts_per_window": {k: _quantiles(v) for k, v in sorted(counts.items())},
            "gate_value_distribution": {k: _quantiles(v) for k, v in sorted(values.items())},
        }
    return out


def filed_search_size() -> dict[str, Any]:
    doc = json.loads((ROOT / PREREGISTRATION).read_text(encoding="utf-8"))
    return dict(doc["search_size"])


def _check_grid_against_filing() -> None:
    filed = filed_search_size().get("grid_points_per_candidate")
    size = ParameterGrid.from_axes(BOOTSTRAP_AXES).full_size()
    if isinstance(filed, int) and filed != size:
        raise ValueError(
            f"the pre-registration files {filed} grid points per candidate but the "
            f"bootstrap grid has {size}; the study must use the filed search size"
        )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "E-1").splitlines()[0])
    ap.add_argument("--process", default="synthetic_gaussian_plateau", choices=PROCESSES)
    ap.add_argument("--sr", type=float, nargs="+", default=[0.0, 0.03, 0.05, 0.08, 0.12])
    ap.add_argument("--replicates", type=int, default=400)
    ap.add_argument("--external-trials", type=int, default=0,
                    help="campaign trials outside this candidate (pre-registration search_size)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--spa-bootstraps", type=int, default=500)
    ap.add_argument("--stress-samples", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260929)
    ap.add_argument("--out", type=Path, required=True)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--data-root", type=Path, default=None,
                     help="a marked Fiboki data root (evidence processes)")
    src.add_argument("--starter", action="store_true",
                     help="data/starter/histdata, resampled: a pilot, never evidence")
    ap.add_argument("--timeframe", default="H4")
    ap.add_argument("--instruments", nargs="+", default=None,
                    help=f"default: K5's universe with --data-root, {STARTER_INSTRUMENTS} with --starter")
    ap.add_argument("--documents", nargs="+", default=None, help="seed ids (default: all)")
    ap.add_argument("--calibration-paths", type=int, default=4,
                    help="perturbed_price_paths: independent paths the mark-up is calibrated on")
    ap.add_argument("--bars-from", default=None,
                    help=f"UTC trim (default {K5_BARS_FROM} with --data-root, none with --starter)")
    args = ap.parse_args(argv)

    real = args.process in REAL_PROCESSES
    if real and not (args.starter or args.data_root is not None):
        make_evaluator(args.process, 0.0, 0)  # raises RealDataRequired, the message says why
    if not real and (args.starter or args.data_root is not None):
        ap.error("the synthetic process reads no bars; --starter/--data-root apply to evidence processes")
    if real and any(sr < 0 for sr in args.sr):
        ap.error("injected Sharpe values must be non-negative")

    # min_trades is the gate set's own threshold: this study measures the gates
    # as they stand and changes none of them.
    min_trades = int(GATE_SET_V2.by_name("min_trades").threshold)
    config = LadderConfig(
        min_trades=min_trades,
        walk_forward_folds=args.folds,
        spa_bootstraps=args.spa_bootstraps,
        stress_samples=args.stress_samples,
        external_trial_count=args.external_trials,
        seed=args.seed,
    )
    started = time.monotonic()
    source: dict[str, Any] | None = None
    build_seconds = 0.0
    if real:
        _check_grid_against_filing()
        study = RealStudy.build(args.process, args, config)
        build_seconds = time.monotonic() - started
        source = study.describe()
        rows: list[dict[str, Any]] = []
        for i in range(args.replicates):
            t0 = time.monotonic()
            rows.extend(study.replicate_rows(i, args.sr))
            if i == 0:
                per = time.monotonic() - t0
                print(
                    f"E-1 {args.process}: source built in {build_seconds:.1f}s; first replicate "
                    f"({len(args.sr)} sr values) took {per:.1f}s; estimated total for "
                    f"{args.replicates} replicates: {build_seconds + per * args.replicates:.0f}s "
                    f"(~{(build_seconds + per * args.replicates) / 3600:.2f}h)",
                    file=sys.stderr, flush=True,
                )
        order = {sr: k for k, sr in enumerate(args.sr)}
        rows.sort(key=lambda r: (order[r["sr"]], r["replicate"]))
    else:
        rows = [
            run_one(process=args.process, sr=sr, replicate=i, config=config, base_seed=args.seed)
            for sr in args.sr
            for i in range(args.replicates)
        ]
    elapsed = time.monotonic() - started
    evidence = real and not args.starter
    result: dict[str, Any] = {
        "study": "E-1",
        "preregistration": PREREGISTRATION,
        "evidence": evidence,
        "note": (
            "synthetic_gaussian_plateau exercises the harness only; it is not "
            "evidence for or against any gate threshold"
            if not real else (
                "starter-data pilot: two years of HistData FX pairs are not the validated "
                "universe the pre-registration names; NOT evidence for or against any threshold"
                if args.starter else
                "evidence process on the data root named in source; valid as E-1 evidence only "
                "once the pre-registration is filed and the full replicate count has run"
            )
        ),
        "size_basis_note": SIZE_BASIS_NOTE,
        "gate_set_version": GATE_SET_V2.version,
        "process": args.process,
        "config": {
            "min_trades": min_trades, "walk_forward_folds": args.folds,
            "spa_bootstraps": args.spa_bootstraps, "stress_samples": args.stress_samples,
            "external_trial_count": args.external_trials, "seed": args.seed,
            "replicates": args.replicates, "sr_grid": list(args.sr),
        },
        "summary": summarise(rows),
        "secondary": secondary(rows),
        "runs": rows,
        "wall_seconds": round(elapsed, 3),
        "wall_seconds_source_build": round(build_seconds, 3),
        "wall_seconds_per_ladder_run": round((elapsed - build_seconds) / max(1, len(rows)), 3),
    }
    if source is not None:
        result["source"] = source
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
