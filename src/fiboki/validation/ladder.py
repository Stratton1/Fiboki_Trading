"""The validation ladder: seven ordered rungs, fail-fast, each able to reject.

    RUNG 0  SANITY          enough trades, positive expectancy, real numbers
    RUNG 1  IN-SAMPLE       a SCREEN. Never evidence. Produces the trial family.
    RUNG 2  WALK-FORWARD    fit on train, evaluate THAT fit on test
    RUNG 3  PURGED CV       a distribution of OOS Sharpe across backtest paths
    RUNG 4  ROBUSTNESS      costs, delay, deletion, dates, parameter plateau
    RUNG 5  DEFLATION       DSR with a real N, PBO, SPA / StepM membership
    RUNG 6  HOLDOUT         one evaluation, on data nothing earlier touched

Ordered by how cheaply each one kills a bad idea, so the expensive rungs are
only ever paid for by candidates that have already survived the cheap ones.

The two rungs that did not exist in V1, in any form
---------------------------------------------------
**RUNG 2 actually fits.** V1's "walk-forward" evaluated ONE FIXED parameter set
on a train window and then on a test window. Nothing was selected, so nothing
about selection was measured -- the procedure could not detect overfitting
because it never overfitted anything. Here each fold sweeps the DOMAINS THE
STRATEGY DOCUMENT DECLARES on the train window, selects a parameterisation, and
evaluates *that* parameterisation on the test window. The difference is the
entire point of walk-forward, and ``tests/unit/test_walk_forward_transfers.py``
constructs a strategy that passes the V1 procedure and fails this one.

**RUNG 6 can only happen once.** The holdout registry records the consumption
before the evaluation runs, so there is no path -- crash, retry, rename, or
re-registration -- by which a strategy sees the holdout twice.

A rejection is a research result
--------------------------------
Every rung records WHY it rejected, in numbers, and the report keeps rejected
candidates. A rejection tells the next search where not to go; throwing it away
is how V1 ended up re-discovering the same dead end repeatedly.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fiboki.core.enums import Provenance
from fiboki.stats.cv import CombinatorialPurgedCV, LabelSpans
from fiboki.stats.multiple_testing import effective_trials_by_clustering
from fiboki.stats.pbo import combinatorially_symmetric_cv
from fiboki.stats.sharpe import deflated_sharpe_ratio, sharpe_moments
from fiboki.stats.spa import step_m, superior_predictive_ability
from fiboki.stats.stability import analyse_parameter_stability
from fiboki.stats.stress import (
    end_date_stress,
    execution_delay_stress,
    net_profit,
    random_deletion_stress,
    slippage_stress,
    spread_multiplier_stress,
    start_date_stress,
)
from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    Evaluator,
    WindowEvaluation,
    params_key,
)
from fiboki.validation.gates import GATE_SET_V2, GateSet
from fiboki.validation.holdout import (
    HoldoutAlreadyConsumed,
    HoldoutRegistry,
    HoldoutSegment,
)
from fiboki.validation.report import RungOutcome, RungResult, ValidationReport

__all__ = [
    "DeflationRung",
    "HoldoutRung",
    "InSampleScreenRung",
    "LadderConfig",
    "LadderContext",
    "PurgedCVRung",
    "RobustnessRung",
    "Rung",
    "SanityRung",
    "ValidationLadder",
    "WalkForwardRung",
    "default_rungs",
]

#: Multipliers the audit names for the spread stress. 2.0 is the gated one.
SPREAD_MULTIPLIERS = (1.0, 1.5, 2.0, 3.0)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LadderConfig:
    """Everything the ladder needs that is not a threshold.

    Thresholds live in :mod:`fiboki.validation.gates` because they are the
    promotion contract; these are procedure settings. The split matters: a
    reader asking "what did it have to beat?" reads the gate set, and a reader
    asking "how was it measured?" reads this.
    """

    min_trades: int = 400
    """Rung 0 floor. V1 used 80, which at search scale is statistically vacuous.

    Also the value the ``min_trades`` GATE checks; they are kept equal by
    :meth:`ValidationLadder.__post_init__` so the ladder cannot admit a candidate
    the gate would reject, or vice versa.
    """

    selection_metric: str = "sharpe"
    """What a sweep selects on. ``sharpe``, ``net_profit``, ``expectancy``."""

    # -- walk-forward
    walk_forward_scheme: str = "anchored"
    """``anchored`` (train window grows) or ``rolling`` (fixed-length train)."""
    walk_forward_folds: int = 5

    # -- purged CV
    cv_groups: int = 6
    cv_k: int = 2
    embargo_pct: float = 0.01
    min_path_positive_fraction: float = 0.5
    """Procedure check, NOT one of the audit gates. Recorded as such."""

    # -- robustness
    stress_samples: int = 100
    stability_radius: int = 1

    # -- deflation
    cscv_splits: int = 8
    spa_bootstraps: int = 500
    corr_threshold: float = 0.7
    external_trial_count: int = 0
    """Trials run OUTSIDE this candidate's own parameter sweep.

    The ladder can see how many parameterisations of THIS strategy were tried.
    It cannot see that the same operator also searched 11 other strategies over
    60 instruments and 7 timeframes -- and that search is the one that produced
    V1's 23,040-cell leaderboard. Whoever runs the campaign must supply its size
    here; it is ADDED to the clustered effective count before deflation. Leaving
    it at 0 deflates against the parameter sweep alone, which is a floor, not
    the truth, and the report records the value used.
    """

    # -- holdout
    holdout_min_trades: int = 30

    seed: int = 20260919

    def __post_init__(self) -> None:
        if self.walk_forward_scheme not in ("anchored", "rolling"):
            raise ValueError("walk_forward_scheme must be 'anchored' or 'rolling'")
        if self.walk_forward_folds < 2:
            raise ValueError("walk_forward_folds must be >= 2")
        if self.min_trades < 1:
            raise ValueError("min_trades must be >= 1")
        if self.cscv_splits % 2 != 0 or self.cscv_splits < 2:
            raise ValueError("cscv_splits must be an even number >= 2")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_trades": self.min_trades,
            "selection_metric": self.selection_metric,
            "walk_forward_scheme": self.walk_forward_scheme,
            "walk_forward_folds": self.walk_forward_folds,
            "cv_groups": self.cv_groups,
            "cv_k": self.cv_k,
            "embargo_pct": self.embargo_pct,
            "min_path_positive_fraction": self.min_path_positive_fraction,
            "stress_samples": self.stress_samples,
            "stability_radius": self.stability_radius,
            "cscv_splits": self.cscv_splits,
            "spa_bootstraps": self.spa_bootstraps,
            "corr_threshold": self.corr_threshold,
            "external_trial_count": self.external_trial_count,
            "holdout_min_trades": self.holdout_min_trades,
            "seed": self.seed,
        }


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------


@dataclass(slots=True)
class LadderContext:
    """Mutable state threaded through the rungs.

    Everything a later rung needs from an earlier one passes through here, and
    every gate input a rung produces is written to :attr:`gate_values`. Nothing
    else is shared, so a rung cannot quietly depend on another rung's internals.
    """

    candidate: Candidate
    evaluator: Evaluator
    segment: HoldoutSegment
    config: LadderConfig
    registry: HoldoutRegistry
    experiment_id: str = ""
    actor: str = ""

    gate_values: dict[str, float | None] = field(default_factory=dict)
    not_applicable: set[str] = field(default_factory=set)
    provenance_labels: dict[str, str] = field(default_factory=dict)
    scratch: dict[str, Any] = field(default_factory=dict)
    n_evaluations: int = 0

    @property
    def research_window(self) -> DateWindow:
        return self.segment.research_window

    @property
    def holdout_window(self) -> DateWindow:
        return self.segment.window

    def evaluate(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        self.n_evaluations += 1
        return self.evaluator(params, window)

    def sweep(self, window: DateWindow) -> list[WindowEvaluation]:
        """Evaluate every declared grid point on one window, in grid order."""
        return [self.evaluate(p, window) for p in self.candidate.grid.points()]

    def select(self, evaluations: Sequence[WindowEvaluation]) -> int:
        """Index of the best evaluation. Ties go to the FIRST grid point.

        Deterministic tie-breaking is not pedantry: a sweep whose winner depends
        on iteration order produces a different "selected" parameterisation on a
        re-run, and then the holdout registry is keyed on a hash that changes.
        """
        metric = self.config.selection_metric
        return max(
            range(len(evaluations)),
            key=lambda i: (evaluations[i].metric(metric), -i),
        )

    def label(self, metric: str, provenance: Provenance) -> None:
        self.provenance_labels[metric] = provenance.value


# --------------------------------------------------------------------------
# Rungs
# --------------------------------------------------------------------------


class Rung:
    """One step of the ladder."""

    index: int = -1
    name: str = "UNNAMED"
    provenance: Provenance = Provenance.BACKTEST
    is_evidence: bool = True

    def run(self, ctx: LadderContext) -> RungResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers shared by the concrete rungs

    def _pass(self, metrics: Mapping[str, Any]) -> RungResult:
        return RungResult(
            index=self.index,
            name=self.name,
            outcome=RungOutcome.PASS,
            metrics=dict(metrics),
            provenance=self.provenance,
            is_evidence=self.is_evidence,
        )

    def _fail(self, reason: str, metrics: Mapping[str, Any] | None = None) -> RungResult:
        return RungResult(
            index=self.index,
            name=self.name,
            outcome=RungOutcome.FAIL,
            reason=reason,
            metrics=dict(metrics or {}),
            provenance=self.provenance,
            is_evidence=self.is_evidence,
        )

    def _error(self, reason: str, metrics: Mapping[str, Any] | None = None) -> RungResult:
        return RungResult(
            index=self.index,
            name=self.name,
            outcome=RungOutcome.ERROR,
            reason=reason,
            metrics=dict(metrics or {}),
            provenance=self.provenance,
            is_evidence=self.is_evidence,
        )

    def not_reached(self) -> RungResult:
        return RungResult(
            index=self.index,
            name=self.name,
            outcome=RungOutcome.NOT_REACHED,
            provenance=self.provenance,
            is_evidence=self.is_evidence,
        )


class SanityRung(Rung):
    """RUNG 0. Is there anything here at all?

    Cheapest rung, so it runs first: one evaluation of the strategy's own
    declared defaults over the research window.
    """

    index = 0
    name = "SANITY"

    def run(self, ctx: LadderContext) -> RungResult:
        baseline = ctx.evaluate(ctx.candidate.default_params, ctx.research_window)
        ctx.scratch["baseline"] = baseline
        ctx.gate_values["n_trades"] = float(baseline.n_trades)
        ctx.label("n_trades", Provenance.BACKTEST)
        metrics = {
            "baseline": baseline.summary(),
            "n_trades": baseline.n_trades,
            "expectancy": baseline.expectancy,
            "net_profit": baseline.net_profit,
            "min_trades_required": ctx.config.min_trades,
        }
        degeneracies = baseline.degeneracies()
        if degeneracies:
            return self._fail(
                "degenerate metrics at defaults: "
                + ", ".join(degeneracies)
                + ". A statistic computed from these numbers would be decoration.",
                metrics,
            )
        if baseline.n_trades < ctx.config.min_trades:
            return self._fail(
                f"{baseline.n_trades} trades at declared defaults, below the "
                f"{ctx.config.min_trades} minimum. V1 promoted on 80 trades; at "
                "this search scale that cannot separate an edge from the best of "
                "many coin flips.",
                metrics,
            )
        if baseline.expectancy <= 0.0:
            return self._fail(
                f"expectancy {baseline.expectancy:.6g} at declared defaults is not "
                "positive; there is no edge to validate.",
                metrics,
            )
        return self._pass(metrics)


class InSampleScreenRung(Rung):
    """RUNG 1. A SCREEN, explicitly. Never evidence.

    Its job is to produce the family of trials -- every parameterisation the
    document declares -- because that family is what rungs 4 and 5 need. The
    in-sample numbers it produces are recorded with ``is_evidence=False`` so no
    downstream reader can mistake the best cell of a sweep for a finding.
    """

    index = 1
    name = "IN_SAMPLE_SCREEN"
    is_evidence = False

    def run(self, ctx: LadderContext) -> RungResult:
        window = ctx.research_window
        sweep = ctx.sweep(window)
        ctx.scratch["sweep"] = sweep
        best = ctx.select(sweep)
        ctx.scratch["is_best_index"] = best

        sharpes = np.array([e.sharpe for e in sweep], dtype=float)
        ctx.scratch["trial_sharpes"] = sharpes
        variance = float(sharpes.var(ddof=1)) if sharpes.size > 1 else 0.0
        ctx.scratch["cross_trial_sharpe_variance"] = variance

        matrix, matrix_note = _trials_matrix(sweep)
        ctx.scratch["trials_matrix"] = matrix
        ctx.scratch["trials_matrix_note"] = matrix_note

        metrics = {
            "SCREEN_NOT_EVIDENCE": (
                "In-sample results select; they do not demonstrate. Nothing on "
                "this rung may be quoted as performance."
            ),
            "n_trials": len(sweep),
            "grid": ctx.candidate.grid.to_dict(),
            "best_index": best,
            "best_params": dict(sweep[best].params),
            "best_in_sample": sweep[best].summary(),
            "cross_trial_sharpe_variance": variance,
            "trials_matrix": matrix_note,
        }
        if sweep[best].metric(ctx.config.selection_metric) <= 0.0:
            return self._fail(
                "no parameterisation inside the domains the document declares is "
                f"positive in sample on {ctx.config.selection_metric}; there is "
                "nothing for the later rungs to test.",
                metrics,
            )
        return self._pass(metrics)


class WalkForwardRung(Rung):
    """RUNG 2. Walk-forward that ACTUALLY FITS.

    Per fold: sweep the declared domains on the train window, select, then
    evaluate THE SELECTED PARAMETERISATION on the test window. The fixed-
    parameter evaluation V1 called walk-forward is also computed, purely as a
    contrast, and recorded so the difference is visible in the report rather
    than asserted in a docstring.
    """

    index = 2
    name = "WALK_FORWARD"
    provenance = Provenance.WALKFORWARD

    def run(self, ctx: LadderContext) -> RungResult:
        cfg = ctx.config
        slices = ctx.research_window.slices(cfg.walk_forward_folds + 1, prefix="wf")
        folds: list[dict[str, Any]] = []
        for i in range(cfg.walk_forward_folds):
            if cfg.walk_forward_scheme == "anchored":
                train = DateWindow(f"wf_train_{i}", slices[0].start, slices[i].end)
            else:
                train = DateWindow(f"wf_train_{i}", slices[i].start, slices[i].end)
            test = DateWindow(f"wf_test_{i}", slices[i + 1].start, slices[i + 1].end)

            trained = ctx.sweep(train)
            chosen = ctx.select(trained)
            selected_params = dict(trained[chosen].params)

            # THE TRANSFER. The parameters that won the train window are the ones
            # the test window sees. V1 skipped this line and measured nothing.
            oos = ctx.evaluate(selected_params, test)
            fixed = ctx.evaluate(ctx.candidate.default_params, test)

            folds.append(
                {
                    "fold": i,
                    "train": train.to_dict(),
                    "test": test.to_dict(),
                    "selected_params": selected_params,
                    "is_rate": trained[chosen].profit_per_day,
                    "is_net_profit": trained[chosen].net_profit,
                    "oos_rate": oos.profit_per_day,
                    "oos_net_profit": oos.net_profit,
                    "oos_trades": oos.n_trades,
                    "oos_sharpe": oos.sharpe,
                    "fixed_param_oos_rate": fixed.profit_per_day,
                    "fixed_param_oos_net_profit": fixed.net_profit,
                }
            )

        is_rates = np.array([f["is_rate"] for f in folds], dtype=float)
        oos_rates = np.array([f["oos_rate"] for f in folds], dtype=float)
        fixed_rates = np.array([f["fixed_param_oos_rate"] for f in folds], dtype=float)

        wfe = _efficiency(is_rates, oos_rates)
        hit = float(np.mean([f["oos_net_profit"] > 0.0 for f in folds]))
        fixed_hit = float(np.mean([f["fixed_param_oos_net_profit"] > 0.0 for f in folds]))

        selected = _modal_params([f["selected_params"] for f in folds])
        ctx.scratch["selected_params"] = selected
        ctx.scratch["walk_forward_folds"] = folds

        ctx.gate_values["walk_forward_efficiency"] = wfe
        ctx.gate_values["oos_profitable_fraction"] = hit
        ctx.label("walk_forward_efficiency", Provenance.WALKFORWARD)
        ctx.label("oos_profitable_fraction", Provenance.WALKFORWARD)

        metrics = {
            "scheme": cfg.walk_forward_scheme,
            "n_folds": cfg.walk_forward_folds,
            "folds": folds,
            "walk_forward_efficiency": wfe,
            "oos_profitable_fraction": hit,
            "mean_is_rate": float(is_rates.mean()),
            "mean_oos_rate": float(oos_rates.mean()),
            "selected_params": selected,
            "parameter_stability_across_folds": _param_agreement(
                [f["selected_params"] for f in folds]
            ),
            "CONTRAST_fixed_parameter_oos_rate": float(fixed_rates.mean()),
            "CONTRAST_fixed_parameter_hit_rate": fixed_hit,
            "CONTRAST_note": (
                "The CONTRAST_* figures re-run the DEFAULT parameters on the same "
                "test windows -- what V1 called walk-forward. They are diagnostics "
                "only: a candidate whose contrast figures are healthy while its "
                "true walk-forward figures are not was being selected on noise."
            ),
        }
        if not np.isfinite(wfe):
            return self._error(
                "walk-forward efficiency is not finite; the in-sample profit rate "
                "was zero or non-finite, so the ratio has no meaning.",
                metrics,
            )
        return self._pass(metrics)


class PurgedCVRung(Rung):
    """RUNG 3. Purged, embargoed, combinatorial CV -> a DISTRIBUTION.

    A single out-of-sample number is one draw. What matters is the spread across
    reassembled backtest paths, and the variance of that spread is what rung 5
    deflates against.

    **The selection is repeated inside every split.** This is the part that is
    easy to get wrong and worthless to get wrong: if the same fixed return series
    is simply re-sliced, every path reassembles the identical sample and the
    "distribution" is a single number repeated ``n_paths`` times. So for each
    split the best parameterisation is chosen using the TRAIN rows only, and that
    column's TEST rows become the path's out-of-sample segment. Different splits
    choose differently -- which is exactly the variation being measured.
    """

    index = 3
    name = "PURGED_CV"
    provenance = Provenance.OUT_OF_SAMPLE

    def run(self, ctx: LadderContext) -> RungResult:
        cfg = ctx.config
        selected = ctx.scratch.get("selected_params", ctx.candidate.default_params)
        full = ctx.evaluate(selected, ctx.research_window)
        ctx.scratch["selected_full"] = full

        matrix: np.ndarray | None = ctx.scratch.get("trials_matrix")
        if matrix is None:
            # Without aligned columns there is no selection to repeat, so the
            # single selected series is all there is. Say so; do not dress a
            # repeated number up as a distribution.
            if not full.returns.size:
                return self._error(
                    "the selected parameterisation produced no returns over the "
                    "research window; there is nothing to cross-validate.",
                    {"selected_params": dict(selected)},
                )
            matrix = full.returns.astype(float).reshape(-1, 1)
            selection_repeated = False
            basis = (
                "single column: "
                + str(ctx.scratch.get("trials_matrix_note", "no aligned trials"))
                + " -- paths differ only in purging, not in selection"
            )
        else:
            selection_repeated = matrix.shape[1] > 1
            basis = f"{matrix.shape[0]} periods x {matrix.shape[1]} trials, re-selected per split"

        n_rows = int(matrix.shape[0])
        if n_rows < cfg.cv_groups * 2:
            return self._error(
                f"{n_rows} samples cannot be split into {cfg.cv_groups} purged "
                "groups with anything left to estimate a Sharpe from.",
                {"n_samples": n_rows, "cv_groups": cfg.cv_groups},
            )

        spans, span_basis = _spans_for(full, n_rows)
        cv = CombinatorialPurgedCV(
            cfg.cv_groups, cfg.cv_k, spans=spans, embargo_pct=cfg.embargo_pct
        )
        splits = list(cv.split())
        chosen_per_split = [
            _select_column(matrix[s.train, :], cfg.selection_metric) for s in splits
        ]

        path_sharpes: list[float] = []
        path_columns: list[list[int]] = []
        for path in cv.path_map():
            segments = []
            columns = []
            for split_index, group in path:
                col = chosen_per_split[split_index]
                columns.append(int(col))
                segments.append(matrix[cv.group_indices(group), col])
            joined = np.concatenate(segments) if segments else np.zeros(0)
            path_columns.append(columns)
            path_sharpes.append(
                float(sharpe_moments(joined).sr_hat) if joined.size > 1 else 0.0
            )

        arr = np.array(path_sharpes, dtype=float)
        positive = float(np.mean(arr > 0.0)) if arr.size else 0.0
        variance = float(arr.var(ddof=1)) if arr.size > 1 else 0.0
        ctx.scratch["path_sharpes"] = arr
        ctx.scratch["path_sharpe_variance"] = variance

        metrics = {
            "basis": basis,
            "span_basis": span_basis,
            "selection_repeated_per_split": selection_repeated,
            "n_paths": int(arr.size),
            "n_samples": n_rows,
            "n_groups": cfg.cv_groups,
            "k": cfg.cv_k,
            "embargo_pct": cfg.embargo_pct,
            "n_purged": [int(s.n_purged) for s in splits],
            "n_embargoed": [int(s.n_embargoed) for s in splits],
            "columns_selected_per_split": [int(c) for c in chosen_per_split],
            "n_distinct_column_selections": len(set(chosen_per_split)),
            "path_columns": path_columns,
            "path_sharpes": [float(x) for x in arr],
            "mean_path_sharpe": float(arr.mean()) if arr.size else 0.0,
            "min_path_sharpe": float(arr.min()) if arr.size else 0.0,
            "path_sharpe_variance": variance,
            "positive_path_fraction": positive,
            "min_positive_path_fraction_required": cfg.min_path_positive_fraction,
            "NOTE": (
                "min_positive_path_fraction is a LADDER CONFIG check, not one of "
                "the versioned promotion gates."
            ),
        }
        if positive < cfg.min_path_positive_fraction:
            return self._fail(
                f"only {positive:.0%} of {arr.size} backtest paths had a positive "
                f"Sharpe, below the {cfg.min_path_positive_fraction:.0%} the ladder "
                "requires. The result depends on which path the sample happened to be.",
                metrics,
            )
        return self._pass(metrics)


class RobustnessRung(Rung):
    """RUNG 4. Does the edge survive the assumptions moving?

    Two independent questions, both of which killed V1 strategies that nobody
    checked: does the edge survive costs and execution reality, and does the
    selected parameterisation sit on a plateau or on a spike?
    """

    index = 4
    name = "ROBUSTNESS"

    def run(self, ctx: LadderContext) -> RungResult:
        cfg = ctx.config
        full: WindowEvaluation | None = ctx.scratch.get("selected_full")
        metrics: dict[str, Any] = {}

        if full is None or not full.trades:
            ctx.gate_values["net_profit_at_2x_spread"] = None
            return self._error(
                "the evaluator returned no Trade objects, so cost, delay and "
                "deletion stresses cannot be applied. The 2x-spread gate fails "
                "CLOSED rather than being assumed.",
                {"note": "supply trades in WindowEvaluation.trades to run rung 4"},
            )

        trades = list(full.trades)
        rng = np.random.default_rng(cfg.seed)
        spread = spread_multiplier_stress(trades, multipliers=SPREAD_MULTIPLIERS)
        two_x = float(spread.median[SPREAD_MULTIPLIERS.index(2.0)])
        curves = {
            "spread_multiplier": spread,
            "slippage": slippage_stress(trades),
            "execution_delay_capture": execution_delay_stress(trades, mode="capture"),
            "execution_delay_adverse": execution_delay_stress(trades, mode="adverse"),
            "random_deletion": random_deletion_stress(
                trades, n_samples=cfg.stress_samples, rng=rng
            ),
            "start_date": start_date_stress(trades),
            "end_date": end_date_stress(trades),
        }
        metrics["baseline_net_profit"] = float(net_profit(trades))
        metrics["stress"] = {
            name: {
                "levels": [float(x) for x in c.levels],
                "median": [float(x) for x in c.median],
                "retention": [float(x) for x in c.retention],
                "breaking_level": c.breaking_level,
            }
            for name, c in curves.items()
        }
        metrics["net_profit_at_2x_spread"] = two_x
        ctx.gate_values["net_profit_at_2x_spread"] = two_x
        ctx.label("net_profit_at_2x_spread", Provenance.BACKTEST)

        # -- parameter plateau
        plateau = self._plateau(ctx)
        metrics["plateau"] = plateau
        ratio = plateau.get("point_plateau_ratio")
        ctx.gate_values["point_plateau_ratio"] = ratio
        ctx.label("point_plateau_ratio", Provenance.BACKTEST)
        if plateau.get("not_applicable"):
            ctx.not_applicable.add("parameter_plateau")

        if plateau.get("is_isolated_peak"):
            return self._fail(
                "the selected parameterisation is an ISOLATED PEAK: its "
                f"neighbourhood averages {plateau['plateau_mean_excluding']:.4g} "
                f"against its own {plateau['score']:.4g}. A point that good only "
                "at itself is a fit to this sample.",
                metrics,
            )
        return self._pass(metrics)

    @staticmethod
    def _plateau(ctx: LadderContext) -> dict[str, Any]:
        grid = ctx.candidate.grid
        sweep: list[WindowEvaluation] = ctx.scratch.get("sweep", [])
        numeric = grid.numeric_names
        if not sweep or not numeric or set(numeric) != set(grid.names):
            return {
                "not_applicable": True,
                "reason": (
                    "parameter plateau needs a fully numeric declared grid; this "
                    f"candidate declares axes {list(grid.names)} of which "
                    f"{list(numeric)} are numeric."
                ),
                "point_plateau_ratio": None,
            }
        if grid.full_size() < 3:
            return {
                "not_applicable": True,
                "reason": (
                    f"a {grid.full_size()}-point grid has no neighbourhood to be a "
                    "plateau in."
                ),
                "point_plateau_ratio": None,
            }
        points = [dict(e.params) for e in sweep]
        scores = [e.metric(ctx.config.selection_metric) for e in sweep]
        report = analyse_parameter_stability(
            points, scores, radius=ctx.config.stability_radius
        )
        selected = ctx.scratch.get("selected_params") or dict(
            sweep[ctx.scratch.get("is_best_index", 0)].params
        )
        target = {k: float(v) for k, v in selected.items()}
        chosen = next(
            (p for p in report.points if p.params == target), report.best_by_score
        )
        return {
            "not_applicable": False,
            "params": chosen.params,
            "score": chosen.score,
            "plateau_mean": chosen.plateau_mean,
            "plateau_mean_excluding": chosen.plateau_mean_excluding,
            "plateau_std": chosen.plateau_std,
            "point_plateau_ratio": chosen.point_plateau_ratio,
            "plateau_quality": chosen.plateau_quality,
            "is_isolated_peak": chosen.is_isolated_peak,
            "n_isolated_peaks_in_grid": len(report.isolated_peaks),
            "best_by_plateau_params": report.best_by_plateau.params,
        }


class DeflationRung(Rung):
    """RUNG 5. Deflate the result by the size of the search that produced it.

    ``N`` is the number of INDEPENDENT trials, obtained by clustering the trial
    return series on correlation distance, plus whatever the campaign ran outside
    this candidate. The variance term is the spread of the rung-3 backtest-path
    Sharpes when that distribution exists, and the cross-section of trial Sharpes
    otherwise; the report records which was used, because the two answer slightly
    different questions and a reader must not have to guess.
    """

    index = 5
    name = "DEFLATION"
    provenance = Provenance.OUT_OF_SAMPLE

    def run(self, ctx: LadderContext) -> RungResult:
        cfg = ctx.config
        matrix: np.ndarray | None = ctx.scratch.get("trials_matrix")
        sweep: list[WindowEvaluation] = ctx.scratch.get("sweep", [])
        trial_sharpes: np.ndarray = ctx.scratch.get("trial_sharpes", np.zeros(0))
        metrics: dict[str, Any] = {
            "n_trials_swept": len(sweep),
            "external_trial_count": cfg.external_trial_count,
        }

        if matrix is None:
            for name in ("deflated_sharpe_ratio", "pbo", "spa_p_consistent", "stepm_member"):
                ctx.gate_values[name] = None
            return self._error(
                "no aligned trials matrix: "
                + str(ctx.scratch.get("trials_matrix_note", "unavailable"))
                + ". PBO, SPA and StepM all require one column per trial on a "
                "shared calendar, so deflation fails CLOSED. Supply period-basis "
                "returns from the evaluator.",
                metrics,
            )

        n_obs, n_cols = matrix.shape
        best = int(ctx.scratch.get("is_best_index", int(np.argmax(trial_sharpes))))

        # -- N: how many independent bets was this search?
        if n_cols >= 2:
            eff = effective_trials_by_clustering(matrix, corr_threshold=cfg.corr_threshold)
            n_effective = int(eff.n_effective)
            metrics["clustering"] = {
                "n_trials": eff.n_trials,
                "n_effective": eff.n_effective,
                "redundancy": eff.redundancy,
                "mean_abs_correlation": eff.mean_abs_correlation,
            }
        else:
            n_effective = 1
            metrics["clustering"] = {
                "note": "a single-point grid is not a search; N comes from the campaign"
            }
        n_for_deflation = max(2, n_effective + int(cfg.external_trial_count))
        ctx.scratch["n_effective_trials"] = n_effective
        ctx.scratch["n_trials_for_deflation"] = n_for_deflation
        metrics["n_effective_trials"] = n_effective
        metrics["n_trials_used_for_deflation"] = n_for_deflation

        # -- the variance term
        # The LARGER of the two dispersions, and the report says which won.
        #
        # Bailey & Lopez de Prado define V as the variance of the trial Sharpes
        # across the search. The rung-3 backtest-path distribution answers a
        # narrower question -- how much this ONE selected strategy moves between
        # reassembled paths -- and is usually the smaller of the two, because the
        # paths share a return-generating process while the trials do not. Taking
        # the maximum means the path distribution can only ever make the
        # deflation MORE demanding, never less, so feeding it in cannot
        # accidentally wave something through.
        path_var = float(ctx.scratch.get("path_sharpe_variance", 0.0) or 0.0)
        cross_var = float(ctx.scratch.get("cross_trial_sharpe_variance", 0.0) or 0.0)
        sr_variance = max(path_var, cross_var)
        if sr_variance <= 0.0:
            source = "degenerate_zero_variance"
        elif path_var >= cross_var:
            source = "max(cpcv_path_sharpes, cross_trial_sharpes) -> cpcv_path_sharpes"
        else:
            source = "max(cpcv_path_sharpes, cross_trial_sharpes) -> cross_trial_sharpes"
        ctx.scratch["dsr_variance_source"] = source
        ctx.scratch["dsr_variance"] = sr_variance
        metrics["sr_variance"] = sr_variance
        metrics["sr_variance_source"] = source
        metrics["sr_variance_cpcv_paths"] = path_var
        metrics["sr_variance_cross_trial"] = cross_var

        moments = sharpe_moments(matrix[:, best])
        dsr = deflated_sharpe_ratio(
            moments.sr_hat,
            moments.n_obs,
            n_trials=n_for_deflation,
            sr_variance=sr_variance,
            skew=moments.skew,
            kurtosis=moments.kurtosis,
        )
        ctx.gate_values["deflated_sharpe_ratio"] = float(dsr)
        ctx.label("deflated_sharpe_ratio", Provenance.OUT_OF_SAMPLE)
        metrics["selected_sharpe"] = moments.sr_hat
        metrics["deflated_sharpe_ratio"] = float(dsr)

        # -- PBO / SPA / StepM need a FAMILY; with one trial there is no selection
        if n_cols < 2:
            for name, gate in (
                ("pbo", "pbo"),
                ("spa_p_consistent", "spa_consistent_p"),
                ("stepm_member", "stepm_survivor"),
            ):
                ctx.gate_values[name] = None
                ctx.not_applicable.add(gate)
            metrics["family_tests"] = (
                "not applicable: the document declares a single parameterisation, "
                "so no selection took place. The CAMPAIGN-level multiple-testing "
                "correction still applies and must come from external_trial_count."
            )
            return self._pass(metrics)

        splits = min(cfg.cscv_splits, (n_obs // 4) * 2)
        if splits < 2:
            ctx.gate_values["pbo"] = None
            return self._error(
                f"{n_obs} observations cannot support a CSCV with at least two "
                "even splits; PBO fails CLOSED.",
                metrics,
            )
        pbo = combinatorially_symmetric_cv(matrix, n_splits=splits)
        ctx.gate_values["pbo"] = float(pbo.pbo)
        ctx.label("pbo", Provenance.OUT_OF_SAMPLE)
        metrics["pbo"] = {
            "pbo": pbo.pbo,
            "n_splits": splits,
            "n_combinations": pbo.n_combinations,
            "degradation_slope": pbo.degradation_slope,
            "probability_of_loss": pbo.probability_of_loss,
        }

        spa = superior_predictive_ability(
            matrix, n_boot=cfg.spa_bootstraps, rng=cfg.seed
        )
        ctx.gate_values["spa_p_consistent"] = float(spa.p_consistent)
        ctx.label("spa_p_consistent", Provenance.OUT_OF_SAMPLE)
        metrics["spa"] = {
            "p_consistent": spa.p_consistent,
            "p_lower": spa.p_lower,
            "p_upper": spa.p_upper,
            "best_index": spa.best_index,
            "n_strategies": spa.n_strategies,
        }

        stepm = step_m(matrix, alpha=0.05, n_boot=cfg.spa_bootstraps, rng=cfg.seed)
        member = best in set(stepm.rejected)
        ctx.gate_values["stepm_member"] = 1.0 if member else 0.0
        ctx.label("stepm_member", Provenance.OUT_OF_SAMPLE)
        metrics["stepm"] = {
            "selected_index": best,
            "n_rejected": stepm.n_rejected,
            "rejected": list(stepm.rejected),
            "selected_is_survivor": member,
        }
        return self._pass(metrics)


class HoldoutRung(Rung):
    """RUNG 6. The one look.

    The consumption is recorded BEFORE the evaluation runs. If this process dies
    halfway through, the look is still spent -- which is the correct outcome, and
    the one V1's arrangement could not produce.
    """

    index = 6
    name = "HOLDOUT"
    provenance = Provenance.HOLDOUT

    def run(self, ctx: LadderContext) -> RungResult:
        candidate = ctx.candidate
        selected = ctx.scratch.get("selected_params", candidate.default_params)
        metrics: dict[str, Any] = {
            "window": ctx.holdout_window.to_dict(),
            "params_evaluated": dict(selected),
        }
        try:
            token = ctx.registry.claim(
                ctx.segment.dataset_version_id,
                candidate.content_hash,
                strategy_id=candidate.strategy_id,
                experiment_id=ctx.experiment_id,
                actor=ctx.actor,
                note=f"ladder rung {self.index}",
            )
        except HoldoutAlreadyConsumed as exc:
            metrics["previous_consumption"] = exc.consumption.to_dict()
            return self._error(str(exc), metrics)

        result = ctx.evaluate(selected, ctx.holdout_window)
        ctx.scratch["holdout"] = result
        summary = result.summary()
        ctx.registry.record_outcome(token, summary)
        metrics["holdout"] = summary
        metrics["token"] = token.token
        ctx.label("holdout_net_profit", Provenance.HOLDOUT)

        if result.n_trades < ctx.config.holdout_min_trades:
            return self._fail(
                f"{result.n_trades} holdout trades, below the "
                f"{ctx.config.holdout_min_trades} the ladder requires for the "
                "result to mean anything. The look is spent regardless.",
                metrics,
            )
        if result.net_profit <= 0.0:
            return self._fail(
                f"holdout net profit {result.net_profit:.6g} is not positive. "
                "This is the only untouched evidence there is, and it says no.",
                metrics,
            )
        return self._pass(metrics)


def default_rungs() -> tuple[Rung, ...]:
    return (
        SanityRung(),
        InSampleScreenRung(),
        WalkForwardRung(),
        PurgedCVRung(),
        RobustnessRung(),
        DeflationRung(),
        HoldoutRung(),
    )


# --------------------------------------------------------------------------
# The ladder
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ValidationLadder:
    """Runs the rungs in order, stops at the first rejection, writes a report."""

    config: LadderConfig = field(default_factory=LadderConfig)
    gate_set: GateSet = GATE_SET_V2
    rungs: tuple[Rung, ...] = field(default_factory=default_rungs)

    def __post_init__(self) -> None:
        gate_min = self.gate_set.by_name("min_trades").threshold
        if float(self.config.min_trades) != float(gate_min):
            raise ValueError(
                f"LadderConfig.min_trades ({self.config.min_trades}) disagrees with "
                f"the {self.gate_set.version} min_trades gate ({gate_min:g}). One of "
                "them would then be decorative. Change the gate set with "
                "GateSet.with_overrides(version=..., min_trades=...) so the report "
                "records which bar was actually in force."
            )

    def run(
        self,
        candidate: Candidate,
        evaluator: Evaluator,
        *,
        registry: HoldoutRegistry,
        dataset_version_id: str,
        engine_config: Mapping[str, Any] | None = None,
        broker_profile: Mapping[str, Any] | None = None,
        experiment_id: str = "",
        actor: str = "",
        notes: str = "",
    ) -> ValidationReport:
        segment = registry.segment(dataset_version_id)
        # Structural, before anything runs: no rung before 6 may see the holdout.
        registry.assert_untouched(dataset_version_id, segment.research_window)

        ctx = LadderContext(
            candidate=candidate,
            evaluator=evaluator,
            segment=segment,
            config=self.config,
            registry=registry,
            experiment_id=experiment_id,
            actor=actor,
        )

        results: list[RungResult] = []
        stopped = False
        for rung in self.rungs:
            if stopped:
                results.append(rung.not_reached())
                continue
            result = rung.run(ctx)
            if result.passed:
                blocking = self._blocking_gate_for(ctx, rung.index)
                if blocking is not None:
                    result = RungResult(
                        index=result.index,
                        name=result.name,
                        outcome=RungOutcome.FAIL,
                        reason=f"promotion gate not met -- {blocking}",
                        metrics=result.metrics,
                        provenance=result.provenance,
                        is_evidence=result.is_evidence,
                    )
            results.append(result)
            if not result.passed:
                stopped = True

        gate_results = self.gate_set.evaluate(
            ctx.gate_values, not_applicable=frozenset(ctx.not_applicable)
        )
        return ValidationReport.build(
            strategy_id=candidate.strategy_id,
            strategy_content_hash=candidate.content_hash,
            dataset_version_id=dataset_version_id,
            gate_set=self.gate_set,
            gate_results=gate_results,
            rungs=results,
            engine_config=dict(engine_config or {}),
            broker_profile=dict(broker_profile or {}),
            ladder_config=self.config.to_dict(),
            windows={
                "research": segment.research_window.to_dict(),
                "holdout": segment.window.to_dict(),
                "holdout_fraction": segment.holdout_fraction,
                "n_evaluations": ctx.n_evaluations,
            },
            trial_count_n=int(ctx.scratch.get("n_trials_for_deflation", 0) or 0),
            raw_trial_count=len(ctx.scratch.get("sweep", [])),
            cross_trial_sharpe_variance=float(
                ctx.scratch.get("cross_trial_sharpe_variance", 0.0) or 0.0
            ),
            dsr_variance_source=str(ctx.scratch.get("dsr_variance_source", "")),
            provenance_labels=dict(ctx.provenance_labels),
            experiment_id=experiment_id,
            actor=actor,
            notes=notes,
        )

    def _blocking_gate_for(self, ctx: LadderContext, rung_index: int) -> str | None:
        """Describe the first gate this rung produced that does not pass."""
        for gate in self.gate_set.gates:
            if gate.rung != rung_index:
                continue
            result = gate.evaluate(
                ctx.gate_values.get(gate.metric),
                applicable=gate.name not in ctx.not_applicable,
            )
            if result.status.blocks_promotion:
                return result.describe()
        return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _spans_for(evaluation: WindowEvaluation, n_rows: int) -> tuple[LabelSpans, str]:
    """Label spans for the CV rows.

    Trade spans (entry -> exit) are used when the evaluation supplies exactly one
    trade per row, because a trade's label is genuinely determined over its whole
    holding period and purging has to know that. Otherwise the rows are periods
    and the spans are unit-length -- correct for a per-bar return, and stated in
    the report so nobody has to guess which was used.
    """
    if evaluation.trades and len(evaluation.trades) == n_rows:
        return (
            LabelSpans.from_trades(evaluation.trades),
            "trade spans (entry -> exit), one trade per row",
        )
    return (
        LabelSpans(starts=np.arange(float(n_rows)), ends=np.arange(float(n_rows)) + 1.0),
        "unit-length period spans",
    )


def _select_column(block: np.ndarray, metric: str) -> int:
    """Best column of a returns block under the ladder's selection metric.

    Ties go to the lowest column index, matching ``LadderContext.select``.
    """
    if block.size == 0 or block.shape[1] == 0:
        return 0
    if metric == "net_profit":
        scores = block.sum(axis=0)
    elif metric in ("expectancy", "profit_per_day"):
        scores = block.mean(axis=0)
    else:
        scores = np.array(
            [
                sharpe_moments(block[:, j]).sr_hat if block.shape[0] > 1 else 0.0
                for j in range(block.shape[1])
            ],
            dtype=float,
        )
    scores = np.nan_to_num(scores, nan=-np.inf, posinf=-np.inf, neginf=-np.inf)
    return int(np.argmax(scores))


def _trials_matrix(sweep: Sequence[WindowEvaluation]) -> tuple[np.ndarray | None, str]:
    """``T x N`` matrix of trial returns, or an honest refusal.

    Columns must be ALIGNED for PBO, SPA and correlation clustering to mean
    anything. Trade-basis returns are not aligned -- two parameterisations take
    different trades at different times -- so this refuses rather than padding,
    truncating or otherwise inventing a correspondence that does not exist.
    """
    if not sweep:
        return None, "the sweep was empty"
    bases = {e.returns_basis for e in sweep}
    if bases != {"period"}:
        return None, (
            f"returns_basis is {sorted(bases)}; only 'period' returns share a "
            "calendar across parameterisations"
        )
    lengths = {int(e.returns.size) for e in sweep}
    if len(lengths) != 1:
        return None, f"period returns have differing lengths {sorted(lengths)}"
    if lengths == {0}:
        return None, "period returns are empty"
    if len(sweep) == 1:
        return np.asarray([sweep[0].returns], dtype=float).T, "single-column (no search)"
    return np.column_stack([e.returns for e in sweep]).astype(float), (
        f"{lengths.pop()} periods x {len(sweep)} trials"
    )


def _efficiency(is_rates: np.ndarray, oos_rates: np.ndarray) -> float:
    """Walk-forward efficiency as a percentage.

    Defined on PROFIT PER DAY rather than on the selection metric so that folds
    of unequal length are comparable and so the number keeps its usual meaning
    whatever a candidate was selected on. A non-positive in-sample rate makes the
    ratio meaningless, and returns ``nan`` rather than a flattering number.
    """
    mean_is = float(np.mean(is_rates))
    mean_oos = float(np.mean(oos_rates))
    if not np.isfinite(mean_is) or mean_is <= 0.0:
        return float("nan")
    return 100.0 * mean_oos / mean_is


def _modal_params(per_fold: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The parameterisation walk-forward chose most often.

    Ties break towards the EARLIEST fold, so the answer does not depend on dict
    ordering. Picking the modal set rather than the last fold's avoids handing
    the holdout a parameterisation that won exactly once.
    """
    if not per_fold:
        return {}
    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    store: dict[str, dict[str, Any]] = {}
    for i, params in enumerate(per_fold):
        key = params_key(params)
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, i)
        store.setdefault(key, dict(params))
    best = min(counts, key=lambda k: (-counts[k], first[k]))
    return store[best]


def _param_agreement(per_fold: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How much the folds agreed. Disagreement is itself a finding."""
    keys = [params_key(p) for p in per_fold]
    distinct = len(set(keys))
    modal = _modal_params(per_fold)
    modal_key = params_key(modal)
    return {
        "n_folds": len(per_fold),
        "n_distinct_selections": distinct,
        "modal_share": keys.count(modal_key) / len(keys) if keys else 0.0,
        "note": (
            "n_distinct_selections equal to n_folds means every fold chose "
            "differently: the surface has no stable optimum and the selection "
            "step is fitting noise."
        ),
    }
