#!/usr/bin/env python
"""E-1 skeleton: the size and power of the promotion gates, measured.

Pre-registration: ``research/preregistration/gate_calibration_e1.json``. Read it
first; this script is the instrument that file describes, and nothing it prints
may be used to move a gate threshold until the file is FILED (committed, with
its commit recorded) and the full-scale study has been run under it.

What it does
------------
For each injected per-trade Sharpe ``sr`` in the grid and each replicate:

1. build a candidate whose declared parameter grid sits on a plateau with its
   peak at the defaults, where the peak's per-period mean is ``sr * sd``;
2. run the FULL :class:`~fiboki.validation.ladder.ValidationLadder` against
   :data:`~fiboki.validation.gates.GATE_SET_V2` (unchanged), with a fresh
   in-memory holdout registry and the campaign's external trial count;
3. record whether it was promoted and which gate bound.

``size`` is the promotion rate at ``sr = 0``; ``power(sr)`` the rate above it.

What it does NOT do yet (and says so)
-------------------------------------
Only the ``synthetic_gaussian_plateau`` data-generating process is implemented.
The two processes that constitute EVIDENCE in the pre-registration -- block-
bootstrapped real returns and perturbed real price paths -- raise
``NotImplementedError`` until the validated universe is ingested. A result
from the synthetic process exercises the harness; it is not evidence for or
against any threshold.

Usage (tiny scale, as the test runs it)::

    python scripts/gate_power_study.py --replicates 2 --sr 0 0.5 --folds 3 \
        --spa-bootstraps 40 --stress-samples 5 --out e1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping, Sequence
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
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder

PREREGISTRATION = "research/preregistration/gate_calibration_e1.json"
DATA_START = pd.Timestamp("2014-01-01", tz="UTC")
DATA_END = pd.Timestamp("2024-01-01", tz="UTC")
DATASET_VERSION = "e1_synthetic_v1"
FAST = (5, 10, 15, 20, 25)
SLOW = (20, 30, 40, 50, 60)
DEFAULTS = {"fast": 15, "slow": 40}
PROCESSES = ("synthetic_gaussian_plateau", "block_bootstrap_real_returns", "perturbed_price_paths")


def _seed(*parts: object) -> int:
    blob = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big") % (2**63)


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


def make_evaluator(process: str, sr: float, seed: int) -> PlateauEvaluator:
    if process == "synthetic_gaussian_plateau":
        return PlateauEvaluator(sr=sr, seed=seed)
    if process in PROCESSES:
        raise NotImplementedError(
            f"{process} is pre-registered but not implemented in this skeleton; "
            "it needs the validated HistData universe and engine_v3_realism "
            f"backtests (see {PREREGISTRATION})"
        )
    raise ValueError(f"unknown data-generating process {process!r}; known {PROCESSES}")


def run_one(
    *, process: str, sr: float, replicate: int, config: LadderConfig, base_seed: int
) -> dict[str, Any]:
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
    report = ValidationLadder(config=config, gate_set=GATE_SET_V2).run(
        candidate, evaluator, registry=registry, dataset_version_id=DATASET_VERSION,
        actor="scripts/gate_power_study.py", notes="E-1 synthetic",
    )
    binding = report.binding_constraint
    return {
        "sr": sr,
        "replicate": replicate,
        "promoted": bool(report.verdict.promotable),
        "verdict": report.verdict.value,
        "binding_kind": binding.kind,
        "binding": binding.name,
        "evaluations": evaluator.calls,
    }


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


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    args = ap.parse_args(argv)

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
    rows = [
        run_one(process=args.process, sr=sr, replicate=i, config=config, base_seed=args.seed)
        for sr in args.sr
        for i in range(args.replicates)
    ]
    result = {
        "study": "E-1",
        "preregistration": PREREGISTRATION,
        "evidence": args.process != "synthetic_gaussian_plateau",
        "note": (
            "synthetic_gaussian_plateau exercises the harness only; it is not "
            "evidence for or against any gate threshold"
        ),
        "gate_set_version": GATE_SET_V2.version,
        "process": args.process,
        "config": {
            "min_trades": min_trades, "walk_forward_folds": args.folds,
            "spa_bootstraps": args.spa_bootstraps, "stress_samples": args.stress_samples,
            "external_trial_count": args.external_trials, "seed": args.seed,
            "replicates": args.replicates, "sr_grid": list(args.sr),
        },
        "summary": summarise(rows),
        "runs": rows,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
