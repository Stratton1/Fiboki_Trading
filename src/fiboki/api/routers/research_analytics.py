"""RESEARCH analytics: tearsheets, comparison, Monte Carlo, surfaces, folds, jobs.

Every figure on the research analytics screens is computed HERE, from stored
results, by :mod:`fiboki.research.tearsheet`; the workstation draws what it is
sent. Routes (all ``GET``, all read-only):

``/api/research/runs``
    Stored backtest runs across every symbol, newest first, with headline metrics.
``/api/research/runs/{run_id}/tearsheet``
    One run: key stats with definitions and the gate check beside each one,
    equity with a benchmark, underwater with the five deepest drawdowns,
    monthly returns, the trade-return histogram, MAE/MFE per trade, the rolling
    Sharpe, a per-instrument breakdown, the run's gate verdicts and the full
    provenance block.
``/api/research/runs/{run_id}/monte-carlo``
    Stationary-bootstrap resampling of the trade sequence (seeded, shown).
``/api/research/compare?runs=a,b``
    Two to six runs: rebased equity, side-by-side stats, daily-return correlation.
``/api/research/strategies/{strategy_id}/overview``
    One strategy: its document, runs, lifecycle state, ladder history and gate reports.
``/api/research/strategies/{strategy_id}/walk-forward``
    The ladder's walk-forward rung: IS/OOS windows per fold and their stability.
``/api/research/parameter-lab/{strategy_id}/surface``
    A 2D parameter surface over stored runs with the ladder's plateau definition.
``/api/research/jobs``
    The research worker's job ledger and the study shards on disk, with ETAs.
``/api/research/correlations``
    The universe's trailing-window return correlation matrix.

The research ledger and the job ledger are opened READ-ONLY (``mode=ro``).
Nothing here runs a backtest, sizes a trade or decides a verdict: a verdict
shown is the one the deterministic worker or the ladder filed.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict

from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure, Series, SeriesPoint
from fiboki.api.routers.chart import (
    BacktestRunView,
    _connect_ro,
    _has_artefacts,
    _ledger_path,
    _run_view,
    _superseded,
    _verdicts,
)
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Provenance, Timeframe
from fiboki.research import tearsheet as ts

router = APIRouter(prefix="/api/research", tags=["research"])

MAX_COMPARE = 6
#: Shard files live under ``research/reports/<study>/shards`` (E-4, K6, E-1).
REPORTS_ROOT = Path("research/reports")


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


def _fig(value: Any, provenance: Provenance, unit: str = "", *, reason: str = "",
         sample: int | None = None, estimated: bool = False) -> Figure:
    v = ts._num(value)
    if v is None:
        return Figure.missing(provenance, unit=unit, reason=reason or "Not recorded for this run.")
    return Figure(value=v, provenance=provenance, unit=unit, sample_size=sample, estimated=estimated)


def _series(name: str, provenance: Provenance, unit: str, data: Any) -> Series:
    return Series(
        name=name,
        provenance=provenance,
        unit=unit,
        points=tuple(SeriesPoint(t=t.to_pydatetime(), v=v) for t, v in ts.points(data)),
    )


def _contract_sizes(instruments: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym in instruments:
        if instrument_registry.exists(sym):
            out[sym.upper()] = float(instrument_registry.get(sym).contract_size)
    return out


# ================================================================ views


class DefinitionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    definition: str
    formula: str


class GateCheckView(BaseModel):
    """One deterministic gate the worker or the ladder evaluated."""

    model_config = ConfigDict(frozen=True)

    name: str
    metric: str
    #: ``pass`` / ``fail`` / ``not_evaluated`` / ``not_applicable``.
    status: str
    passed: bool
    #: The value the gate judged, labelled with the provenance of the report.
    value: Figure
    #: The gate's declared threshold: configuration, not a measurement.
    threshold: float | None
    comparison: str
    rung: int | None
    detail: str


class GateReportView(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    backtest_id: str
    created_at: datetime | None
    verdict: str
    kind: str
    gate_set_version: str
    gate_set_fingerprint: str
    checks: list[GateCheckView]
    caveats: list[str]
    #: The report's own Sharpe-probability diagnostics, as filed.
    psr: Figure
    deflated_sharpe: Figure
    trial_count: Figure
    trial_count_known: bool


class StatView(BaseModel):
    """A headline figure, its definition, and the gate on the same metric (if any)."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    figure: Figure
    definition: str
    formula: str
    check: GateCheckView | None = None
    #: Plain-language source of the number (``engine metrics``, ``gate report``...).
    source: str


class DrawdownPeriodView(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    peak_at: datetime
    trough_at: datetime
    recovered_at: datetime | None
    depth_pct: Figure
    peak_equity: Figure
    trough_equity: Figure
    decline_days: Figure
    recovery_days: Figure
    length_days: Figure


class MonthlyCellView(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    month: int
    return_pct: Figure


class YearReturnView(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    return_pct: Figure


class HistogramView(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: ``r_multiple`` or ``net_pnl`` (in ``unit``).
    basis: str
    provenance: Provenance
    unit: str
    #: Bin edges and per-bin trade counts, in ``unit``, labelled by ``provenance``.
    edges: list[float]
    counts: list[int]
    n: int
    mean: Figure
    median: Figure


class ExcursionTable(BaseModel):
    """Per-trade result and excursions, as labelled COLUMNS (one entry per trade).

    Columnar, like :class:`Series`: the provenance and the units are declared
    once for the table rather than repeated on thousands of cells. ``None`` in
    a column is an absent value (no stop, no excursion key), never zero.
    """

    model_config = ConfigDict(frozen=True)

    provenance: Provenance
    #: Unit of ``net_pnl``, ``mae`` and ``mfe`` (the account currency).
    pnl_unit: str
    #: Unit of ``r_multiple``, ``mae_r`` and ``mfe_r``.
    r_unit: str
    trade_id: list[str]
    instrument: list[str]
    direction: list[str]
    entry_time: list[datetime | None]
    exit_time: list[datetime | None]
    exit_reason: list[str]
    bars_held: list[int | None]
    net_pnl: list[float | None]
    r_multiple: list[float | None]
    mae: list[float | None]
    mfe: list[float | None]
    mae_r: list[float | None]
    mfe_r: list[float | None]


class InstrumentBreakdownView(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument: str
    trades: Figure
    net_pnl: Figure
    win_rate_pct: Figure
    profit_factor: Figure
    expectancy: Figure
    expectancy_r: Figure


class ProvenanceBlock(BaseModel):
    """Everything needed to reproduce, or to distrust, a stored result."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    strategy_id: str
    strategy_hash: str
    parameters: dict[str, Any]
    #: Where ``parameters`` came from (job payload, defaults, unknown).
    parameters_source: str
    dataset_version_ids: list[str]
    data_fingerprint: dict[str, Any]
    timeframe: str
    instruments: list[str]
    account_ccy: str
    initial_balance: float | None
    cost_profile: str
    spread_model: str
    slippage_model: str
    commission_model: str
    financing_model: str
    sizing: dict[str, Any]
    exit_policy: dict[str, Any]
    intrabar_policy: str
    calendar_version: str
    calendar_note: str
    gate_set_version: str
    gate_set_fingerprint: str
    engine_version: str
    engine_current: bool
    ledger_sha256: str
    job_id: str
    run_created_at: datetime | None
    tearsheet_version: str


class TearsheetView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: BacktestRunView
    account_ccy: str
    stats: list[StatView]
    equity: Series
    benchmark: Series | None
    #: ``buy_and_hold`` or ``flat``.
    benchmark_kind: str
    benchmark_note: str
    underwater: Series
    drawdowns: list[DrawdownPeriodView]
    monthly: list[MonthlyCellView]
    yearly: list[YearReturnView]
    trade_histogram: HistogramView
    excursions: ExcursionTable
    excursions_note: str
    rolling_sharpe: Series
    rolling_window_days: int
    by_instrument: list[InstrumentBreakdownView]
    pooled: bool
    gate_report: GateReportView | None
    provenance: ProvenanceBlock
    definitions: dict[str, DefinitionView]
    chart_links: dict[str, str]
    strategy_link: str


class RunSummaryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: BacktestRunView
    total_return_pct: Figure
    sharpe: Figure
    max_drawdown_pct: Figure
    profit_factor: Figure
    tearsheet: str


class ComparedRunView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: BacktestRunView
    rebased: Series
    stats: list[StatView]
    daily_returns: int


class CorrelationCellView(BaseModel):
    model_config = ConfigDict(frozen=True)

    a: str
    b: str
    rho: Figure
    #: Common trading days the correlation was measured on (its sample size).
    n_common: int


class ComparisonView(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: list[ComparedRunView]
    correlations: list[CorrelationCellView]
    min_common_days: int
    definitions: dict[str, DefinitionView]


class MonteCarloView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    #: ``r_multiple`` (sizing re-simulated) or ``trade_return``.
    basis: str
    seed: int
    n_paths: int
    n_trades: int
    block_length: Figure
    #: Configuration: the fraction of running equity risked per resampled trade.
    risk_fraction: float
    initial_equity: Figure
    #: Configuration: ruin is a fall to this fraction of the running peak.
    ruin_fraction: float
    ruin_probability: Figure
    provenance: Provenance
    fan_steps: list[int]
    #: Percentile paths in the account currency (``p5`` ... ``p95``), simulated.
    fan: dict[str, list[float]]
    final_equity_percentiles: dict[str, Figure]
    drawdown_percentiles: dict[str, Figure]
    realised_max_drawdown_pct: Figure
    realised_final_equity: Figure
    account_ccy: str
    method: str
    notes: list[str]


class RungView(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    name: str
    outcome: str
    reason: str


class LadderEntryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: ``report_file`` or ``experiment_ledger``.
    source: str
    experiment_id: str | None
    created_at: datetime | None
    verdict: str
    gate_set_version: str
    report_version: str
    dataset_version_id: str
    strategy_content_hash: str
    binding_constraint: str
    trial_count: Figure
    rungs: list[RungView]
    has_walk_forward: bool


class LifecycleBrief(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_hash: str
    state: str
    degraded: bool
    link: str


class StrategyOverviewView(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    name: str
    family: str
    hypothesis: str
    content_hash: str
    timeframes: list[str]
    universe: list[str]
    parameters: dict[str, Any]
    runs: list[RunSummaryView]
    runs_total: int
    gate_reports: list[GateReportView]
    ladder_history: list[LadderEntryView]
    lifecycle: list[LifecycleBrief]
    lifecycle_note: str
    links: dict[str, str]


class FoldView(BaseModel):
    model_config = ConfigDict(frozen=True)

    fold: int
    train_start: datetime | None
    train_end: datetime | None
    test_start: datetime | None
    test_end: datetime | None
    is_rate: Figure
    oos_rate: Figure
    is_net_profit: Figure
    oos_net_profit: Figure
    oos_trades: Figure
    oos_sharpe: Figure
    efficiency: Figure
    fixed_param_oos_rate: Figure
    selected_params: dict[str, Any]


class WalkForwardView(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    source: str
    source_ref: str
    scheme: str
    outcome: str
    reason: str
    folds: list[FoldView]
    n_folds: int
    walk_forward_efficiency: Figure
    oos_profitable_fraction: Figure
    mean_is_rate: Figure
    mean_oos_rate: Figure
    oos_rate_sd: Figure
    oos_rate_cv: Figure
    parameter_repeats: int
    parameter_stability_across_folds: Any
    gates: list[GateCheckView]
    gate_set_version: str
    rate_unit: str
    fold_runs: list[BacktestRunView]
    available: list[str]
    definitions: dict[str, DefinitionView]


class SurfaceCellView(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: The two parameter values: coordinates on the declared grid, not measurements.
    x: float
    y: float
    score: Figure
    n_runs: int
    run_ids: list[str]
    point_plateau_ratio: Figure
    neighbourhood_median_ratio: Figure
    neighbourhood_min: Figure
    on_plateau: bool
    isolated_peak: bool
    plateau_checks: dict[str, bool]


class PlateauGateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    metric: str
    comparison: str
    threshold: float
    rationale: str


class ParameterSurfaceView(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    parameters: list[str]
    x: str
    y: str
    metric: str
    metrics_available: list[str]
    fixed: dict[str, Any]
    x_values: list[float]
    y_values: list[float]
    cells: list[SurfaceCellView]
    radius: int
    plateau_gates: list[PlateauGateView]
    gate_set_version: str
    runs_considered: int
    runs_used: int
    runs_other_params: int
    runs_unknown_params: int
    plateau_definition: str
    notes: list[str]


class JobView(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    job_type: str
    queue: str
    status: str
    submitted_at: datetime | None
    started_at: datetime | None
    next_attempt_at: datetime | None
    attempts: int
    max_attempts: int
    error: str
    strategy_id: str
    instrument: str
    elapsed_seconds: Figure
    eta_seconds: Figure
    outputs: dict[str, str]


class ShardView(BaseModel):
    model_config = ConfigDict(frozen=True)

    study: str
    stage: str
    path: str
    unit: str
    done: int
    total: int | None
    partial: bool
    progress: Figure
    seconds_per_unit: Figure
    eta_seconds: Figure
    updated_at: datetime | None
    age_seconds: Figure
    error: str


class JobQueueView(BaseModel):
    model_config = ConfigDict(frozen=True)

    counts: dict[str, int]
    jobs: list[JobView]
    shards: list[ShardView]
    job_ledger: str
    shards_root: str
    eta_method: str


class CorrelationMatrixView(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeframe: str
    #: The matrix is computed from validated bars; values are labelled by this.
    provenance: Provenance
    window: int
    windows: list[int]
    timeframes: list[str]
    instruments: list[str]
    matrix: list[list[float | None]]
    bars_used: int
    first_common: datetime | None
    last_common: datetime | None
    dataset_version_ids: dict[str, str]
    excluded: dict[str, str]
    method: str


# ================================================================ ledger reads


def _ledger(settings: Any) -> Path:
    path = _ledger_path(settings)
    if path is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "experiment_ledger_not_configured",
            "No experiment ledger is configured (FIBOKI_EXPERIMENT_DB), so no stored run "
            "can be read. This is a missing source, not a strategy with no runs.",
        )
    return path


_SUMMARY_COLUMNS = (
    "a.id, a.created_at, a.strategy_id, "
    "json_extract(a.payload_json, '$.label'), "
    "json_extract(a.payload_json, '$.timeframe'), "
    "json_extract(a.payload_json, '$.instruments'), "
    "json_extract(a.payload_json, '$.dataset_version_ids'), "
    "json_extract(a.payload_json, '$.engine_version'), "
    "json_array_length(a.payload_json, '$.trades'), "
    "json_array_length(a.payload_json, '$.trades'), "
    "(SELECT count(*) FROM json_each(a.payload_json, '$.trades') t "
    " WHERE json_type(t.value, '$.initial_stop') IN ('real', 'integer')), "
    "(SELECT group_concat(DISTINCT json_extract(t.value, '$.provenance')) "
    " FROM json_each(a.payload_json, '$.trades') t), "
    "json_extract(a.payload_json, '$.metrics')"
)


def _sqlite_error(exc: Exception) -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "experiment_ledger_unavailable",
        "The research ledger could not be read. This is not 'no runs'.",
        context={"error_class": type(exc).__name__},
    )


def _summary(row: tuple[Any, ...], verdicts: Any, superseded: Any) -> RunSummaryView:
    from fiboki.backtest.version import ENGINE_VERSION

    run = _run_view(row[:12], verdicts=verdicts, superseded=superseded,
                    engine_version=ENGINE_VERSION)
    metrics = json.loads(row[12] or "{}")
    p = run.provenance
    return RunSummaryView(
        run=run,
        total_return_pct=_fig(metrics.get("total_return_pct"), p, "pct"),
        sharpe=_fig(metrics.get("sharpe"), p, "ratio"),
        max_drawdown_pct=_fig(metrics.get("max_drawdown_pct"), p, "pct"),
        profit_factor=_fig(metrics.get("profit_factor"), p, "ratio",
                           reason="Undefined: no losing trade, or no trades."),
        tearsheet=f"/research/runs/{run.run_id}",
    )


def _list_runs(settings: Any, *, strategy_id: str | None, limit: int,
               offset: int = 0) -> tuple[list[RunSummaryView], int]:
    path = _ledger(settings)
    conn = _connect_ro(path)
    try:
        if not _has_artefacts(conn):
            return [], 0
        where = "a.collection = 'backtests'"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if strategy_id:
            where += " AND a.strategy_id = :strategy"
            params["strategy"] = strategy_id
        total = conn.execute(
            f"SELECT count(*) FROM research_artefact a WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLUMNS} FROM research_artefact a WHERE {where} "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        verdicts = _verdicts(conn)
        superseded = _superseded(conn)
    except sqlite3.Error as exc:
        raise _sqlite_error(exc) from exc
    finally:
        conn.close()
    return [_summary(r, verdicts, superseded) for r in rows], int(total)


def _load_run(settings: Any, run_id: str) -> tuple[BacktestRunView, dict[str, Any],
                                                     dict[str, Any] | None]:
    """``(run view, stored record, latest gate report payload or None)``."""
    from fiboki.backtest.version import ENGINE_VERSION

    conn = _connect_ro(_ledger(settings))
    try:
        if not _has_artefacts(conn):
            raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_backtest",
                           f"The research ledger holds no backtest {run_id!r}.")
        row = conn.execute(
            f"SELECT {_SUMMARY_COLUMNS}, a.payload_json FROM research_artefact a "
            "WHERE a.collection = 'backtests' AND a.id = :id",
            {"id": run_id},
        ).fetchone()
        if row is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_backtest",
                           f"The research ledger holds no backtest {run_id!r}.")
        report = conn.execute(
            "SELECT payload_json FROM research_artefact WHERE collection = 'validation_reports' "
            "AND json_extract(payload_json, '$.backtest_id') = :id "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            {"id": run_id},
        ).fetchone()
        verdicts = _verdicts(conn)
        superseded = _superseded(conn)
    except sqlite3.Error as exc:
        raise _sqlite_error(exc) from exc
    finally:
        conn.close()
    run = _run_view(row[:12], verdicts=verdicts, superseded=superseded,
                    engine_version=ENGINE_VERSION)
    return run, json.loads(row[-1]), (json.loads(report[0]) if report else None)


def _gate_reports_for_strategy(settings: Any, strategy_id: str, limit: int = 50) -> list[GateReportView]:
    conn = _connect_ro(_ledger(settings))
    try:
        if not _has_artefacts(conn):
            return []
        rows = conn.execute(
            "SELECT payload_json FROM research_artefact WHERE collection = 'validation_reports' "
            "AND strategy_id = :s ORDER BY created_at DESC, id DESC LIMIT :limit",
            {"s": strategy_id, "limit": limit},
        ).fetchall()
    except sqlite3.Error as exc:
        raise _sqlite_error(exc) from exc
    finally:
        conn.close()
    return [_gate_report_view(json.loads(r[0])) for r in rows]


def _check_view(raw: dict[str, Any], provenance: Provenance = Provenance.BACKTEST) -> GateCheckView:
    status_ = str(raw.get("status") or ("pass" if raw.get("passed") else "fail"))
    gate = raw.get("gate") if isinstance(raw.get("gate"), dict) else None
    return GateCheckView(
        name=str(raw.get("name") or (gate or {}).get("name") or ""),
        metric=str(raw.get("metric") or (gate or {}).get("metric") or ""),
        status=status_,
        passed=bool(raw.get("passed")),
        value=_fig(raw.get("value"), provenance, reason="The gate was not evaluated."),
        threshold=ts._num(raw.get("threshold", (gate or {}).get("threshold"))),
        comparison=str(raw.get("comparison") or (gate or {}).get("comparison") or ""),
        rung=(int(raw["rung"]) if isinstance(raw.get("rung"), int)
              else (int(gate["rung"]) if gate and isinstance(gate.get("rung"), int) else None)),
        detail=str(raw.get("detail") or raw.get("description") or ""),
    )


def _parse_dt(value: Any) -> datetime | None:
    t = ts._ts(value)
    return t.to_pydatetime() if t is not None else None


def _gate_report_view(raw: dict[str, Any]) -> GateReportView:
    metrics = raw.get("metrics") or {}
    trials = metrics.get("trial_count") or {}
    return GateReportView(
        report_id=str(raw.get("report_id") or ""),
        backtest_id=str(raw.get("backtest_id") or ""),
        created_at=_parse_dt(raw.get("created_at")),
        verdict=str(raw.get("verdict") or "inconclusive"),
        kind=str(raw.get("kind") or ""),
        gate_set_version=str(metrics.get("gate_set_version") or ""),
        gate_set_fingerprint=str(metrics.get("gate_set_fingerprint") or ""),
        checks=[_check_view(c, Provenance.BACKTEST) for c in raw.get("checks") or []
                if isinstance(c, dict)],
        caveats=[str(c) for c in raw.get("caveats") or []],
        psr=_fig(metrics.get("psr"), Provenance.BACKTEST, "ratio",
                 reason="The gate job could not compute a PSR for this run."),
        deflated_sharpe=_fig(metrics.get("deflated_sharpe_ratio"), Provenance.BACKTEST, "ratio",
                             reason="NOT_EVALUATED by the gate job (see its notes)."),
        trial_count=_fig(trials.get("n_trials") if isinstance(trials, dict) and trials.get("known")
                         else None, Provenance.BACKTEST, "count",
                         reason="The experiment ledger held no trial count for this scope."),
        trial_count_known=bool(isinstance(trials, dict) and trials.get("known")),
    )


# ================================================================ job ledger


def _jobs_path(settings: Any) -> Path:
    return Path(settings.state_dir) / "jobs.sqlite"


def _job_payload(settings: Any, job_id: str) -> dict[str, Any] | None:
    path = _jobs_path(settings)
    if not job_id or not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT spec_json FROM job WHERE job_id = ?", (job_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return dict(json.loads(row[0]).get("payload") or {})
    except (ValueError, AttributeError):
        return None


def _job_payloads(settings: Any, job_ids: set[str]) -> dict[str, dict[str, Any]]:
    path = _jobs_path(settings)
    if not job_ids or not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {}
    try:
        for job_id, spec in conn.execute("SELECT job_id, spec_json FROM job").fetchall():
            if job_id in job_ids:
                try:
                    out[str(job_id)] = dict(json.loads(spec).get("payload") or {})
                except (ValueError, AttributeError):
                    continue
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return out


def _run_parameters(platform: Any, strategy_id: str, payload: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    """The parameters a run executed with: declared defaults overlaid by the job's."""
    doc = platform.strategy(strategy_id)
    defaults: dict[str, Any] = {}
    if doc is not None and callable(getattr(doc, "default_values", None)):
        try:
            defaults = dict(doc.default_values())
        except Exception:
            defaults = {}
    if payload is None:
        return ({}, "unknown: the run's job is not in the job ledger, so the binding it "
                "executed with cannot be read back")
    overrides = payload.get("parameters") or {}
    if not isinstance(overrides, dict):
        overrides = {}
    if not defaults and not overrides:
        return {}, "none: the strategy declares no parameters"
    source = ("job payload over declared defaults" if overrides
              else "declared defaults (the job named no parameters)")
    return {**defaults, **overrides}, source


# ================================================================ tearsheet


STAT_ORDER = (
    ("net_profit", "account"),
    ("total_return_pct", "pct"),
    ("cagr_pct", "pct"),
    ("sharpe", "ratio"),
    ("sharpe_lo_adjusted", "ratio"),
    ("psr", "ratio"),
    ("deflated_sharpe", "ratio"),
    ("max_drawdown_pct", "pct"),
    ("n_trades", "count"),
    ("win_rate_pct", "pct"),
    ("expectancy", "account"),
    ("expectancy_r", "R"),
    ("profit_factor", "ratio"),
    ("exposure_pct", "pct"),
    ("turnover", "x"),
    ("costs_paid", "account"),
    ("cost_drag_pct", "pct"),
    ("sortino", "ratio"),
    ("calmar", "ratio"),
    ("avg_bars_held", "bars"),
)

#: Stat key -> gate metric names that judge the same quantity.
STAT_GATES: dict[str, tuple[str, ...]] = {
    "n_trades": ("n_trades", "n_trades_over_min_trl"),
    "deflated_sharpe": ("deflated_sharpe_ratio",),
    "max_drawdown_pct": ("max_drawdown_pct_abs", "max_drawdown_pct"),
    "net_profit": ("net_profit_at_2x_spread",),
    "psr": ("psr",),
}


def _calendar() -> tuple[str, str]:
    try:
        from fiboki.marketstate.calendar import PINNED_RESEARCH_CALENDAR_VERSION
    except Exception:  # pragma: no cover - defensive
        return "", "The calendar version could not be read."
    return (
        PINNED_RESEARCH_CALENDAR_VERSION,
        "Research is pinned to this official calendar version; the run record does not "
        "stamp a calendar version of its own, so this is the pin in force, not a "
        "recorded property of the run.",
    )


def _closes(settings: Any, symbol: str, timeframe: str, start: Any, end: Any) -> tuple[Any, str]:
    if settings.data_root is None or not Path(settings.data_root).exists():
        return None, "No market-data root is mounted, so buy-and-hold cannot be drawn."
    try:
        from fiboki.data.store import DataStore

        with DataStore(settings.data_root) as store:
            frame, version = store.read_latest(
                instrument=symbol, timeframe=timeframe, start=start, end_inclusive=end
            )
    except Exception as exc:
        return None, f"The bars for buy-and-hold could not be read ({type(exc).__name__})."
    return frame["close"].astype(float), getattr(version, "version_id", "")


def _stat(key: str, unit: str, value: Any, p: Provenance, *, source: str,
          reason: str = "", sample: int | None = None,
          checks: dict[str, GateCheckView] | None = None) -> StatView:
    d = ts.DEFINITIONS[key]
    check = None
    for metric in STAT_GATES.get(key, ()):
        if checks and metric in checks:
            check = checks[metric]
            break
    return StatView(
        key=key, label=d.label, figure=_fig(value, p, unit, reason=reason, sample=sample),
        definition=d.definition, formula=d.formula, check=check, source=source,
    )


def _defs(keys: list[str]) -> dict[str, DefinitionView]:
    return {k: DefinitionView(label=v.label, definition=v.definition, formula=v.formula)
            for k, v in ts.definitions(keys).items()}


def _run_stats(record: dict[str, Any], run: BacktestRunView, report: GateReportView | None,
               ccy: str) -> list[StatView]:
    p = run.provenance
    metrics = record.get("metrics") or {}
    trades = [t for t in record.get("trades") or [] if isinstance(t, dict)]
    sizes = _contract_sizes([str(t.get("instrument") or "") for t in trades] + list(run.instruments))
    initial = ts._num(metrics.get("initial_equity")) or (
        ts._num((record.get("equity_curve") or [{}])[0].get("equity")) if record.get("equity_curve") else None
    )
    final = ts._num(metrics.get("final_equity"))
    equity = ts.equity_series(record.get("equity_curve") or [])
    cost = ts.costs(trades)
    tstats = ts.trade_stats(trades, sizes)
    years = ts._num(metrics.get("years"))
    mean_eq = float(equity.mean()) if len(equity) else None
    turnover, _ = ts.turnover(trades, sizes, mean_eq, years)
    checks = {c.metric: c for c in report.checks} if report else {}
    if report is not None and report.psr.value is not None:
        psr_value, psr_source, psr_reason, psr_n = (
            report.psr.value, "gate report " + report.report_id, "", None)
    else:
        computed = ts.per_trade_psr(trades, initial or 1.0)
        psr_value, psr_source, psr_reason, psr_n = (
            computed.psr, "computed here, per-trade returns on running equity (the gate job's basis)",
            computed.reason, computed.n_obs,
        )
    if report is not None and report.deflated_sharpe.value is not None:
        dsr_value = report.deflated_sharpe.value
        dsr_reason = ""
        n_trials = report.trial_count.value
        dsr_source = (f"gate report {report.report_id}, N = "
                      f"{int(n_trials) if n_trials is not None else '?'} trial(s) from the experiment ledger")
    else:
        dsr_value = None
        dsr_source = "gate report"
        if report is None:
            dsr_reason = ("No gate report is filed against this run, so the trial count N that "
                          "deflation needs is not known here. Run the validation job.")
        elif not report.trial_count_known:
            dsr_reason = ("The gate report could not read a trial count from the experiment "
                          "ledger; the deflated Sharpe is NOT_EVALUATED, which blocks.")
        else:
            dsr_reason = "The gate report left the deflated Sharpe NOT_EVALUATED."
    net = (final - initial) if (final is not None and initial is not None) else None
    engine = "engine metrics (backtest/metrics.py), as stored"
    here = "computed here from the stored trades"
    cost_drag = metrics.get("cost_drag_pct_of_gross")
    rows: list[StatView] = []
    for key, unit in STAT_ORDER:
        u = ccy if unit == "account" else unit
        if key == "net_profit":
            rows.append(_stat(key, u, net, p, source=engine, checks=checks))
        elif key == "psr":
            rows.append(_stat(key, u, psr_value, p, source=psr_source, reason=psr_reason,
                              sample=psr_n, checks=checks))
        elif key == "deflated_sharpe":
            rows.append(_stat(key, u, dsr_value, p, source=dsr_source, reason=dsr_reason, checks=checks))
        elif key == "expectancy_r":
            rows.append(_stat(key, u, tstats.expectancy_r, p, source=here, sample=tstats.n_with_r,
                              reason="No trade in this run has a recorded stop (filed before "
                              "2026-10-18), so no R multiple exists."))
        elif key == "exposure_pct":
            rows.append(_stat(key, u, metrics.get("time_in_market_pct"), p, source=engine))
        elif key == "turnover":
            rows.append(_stat(key, u, turnover, p, source=here + " (notional at the stored FX rate)",
                              reason="Turnover needs a measured span and a positive mean equity."))
        elif key == "costs_paid":
            rows.append(_stat(key, u, cost.total if trades else metrics.get("total_costs"), p,
                              source=here if trades else engine))
        elif key == "cost_drag_pct":
            rows.append(_stat(key, u, cost_drag, p, source=engine,
                              reason="Undefined when gross P&L is zero."))
        elif key == "n_trades":
            rows.append(_stat(key, u, metrics.get("n_trades", len(trades)), p, source=engine, checks=checks))
        elif key == "profit_factor":
            rows.append(_stat(key, u, metrics.get("profit_factor"), p, source=engine,
                              reason="Undefined: no losing trade, or no trades."))
        else:
            rows.append(_stat(key, u, metrics.get(key), p, source=engine, checks=checks,
                              reason="Degenerate or not recorded for this run (see the engine's "
                              "degenerate list)."))
    return rows


def _provenance_block(platform: Any, settings: Any, run: BacktestRunView, record: dict[str, Any],
                      report: GateReportView | None) -> ProvenanceBlock:
    from fiboki.validation.gates import PRODUCTION_GATE_SET

    cfg = record.get("config_fingerprint") or {}
    profile = cfg.get("profile") if isinstance(cfg.get("profile"), dict) else {}
    params, source = _run_parameters(platform, run.strategy_id,
                                     _job_payload(settings, str(record.get("job_id") or "")))
    calendar, calendar_note = _calendar()
    return ProvenanceBlock(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        strategy_hash=str(record.get("content_hash") or ""),
        parameters=params,
        parameters_source=source,
        dataset_version_ids=list(run.dataset_version_ids),
        data_fingerprint=dict(record.get("data_fingerprint") or {}),
        timeframe=run.timeframe,
        instruments=list(run.instruments),
        account_ccy=str(cfg.get("account_ccy") or ""),
        initial_balance=ts._num(cfg.get("initial_balance")),
        cost_profile=str(profile.get("name") or ""),
        spread_model=str(profile.get("spread") or ""),
        slippage_model=str(profile.get("slippage") or ""),
        commission_model=str(profile.get("commission") or ""),
        financing_model=str(profile.get("financing") or ""),
        sizing=dict(cfg.get("sizing") or {}) if isinstance(cfg.get("sizing"), dict) else {},
        exit_policy=dict(record.get("exit_policy_fingerprint") or {}),
        intrabar_policy=str(cfg.get("intrabar_policy") or ""),
        calendar_version=calendar,
        calendar_note=calendar_note,
        gate_set_version=(report.gate_set_version if report else "")
        or f"not evaluated (production set is {PRODUCTION_GATE_SET.version})",
        gate_set_fingerprint=report.gate_set_fingerprint if report else "",
        engine_version=run.engine_version or "unstamped (predates the engine stamp)",
        engine_current=run.engine_current,
        ledger_sha256=str(record.get("ledger_sha256") or ""),
        job_id=str(record.get("job_id") or ""),
        run_created_at=run.created_at,
        tearsheet_version=ts.TEARSHEET_VERSION,
    )


@router.get("/runs", response_model=Page[RunSummaryView])
def runs(
    settings: SettingsDep,
    strategy_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page[RunSummaryView]:
    """Every stored backtest run (any symbol), newest first. Read-only."""
    if _ledger_path(settings) is None:
        return Page(
            items=[], total=0, limit=limit,
            source=_note("absent", "No experiment ledger is configured (FIBOKI_EXPERIMENT_DB). "
                         "This is an EMPTY SOURCE, not a strategy nobody has backtested."),
            caveats=(Caveat(code="source_not_configured", severity="warning",
                            message="The research ledger is not attached to this deployment.",
                            direction="unknown"),),
        )
    items, total = _list_runs(settings, strategy_id=strategy_id, limit=limit, offset=offset)
    return Page(items=items, total=total, offset=offset, limit=limit,
                source=_note("live", "Research ledger (append-only), read-only."))


@router.get("/runs/{run_id}/tearsheet", response_model=Envelope[TearsheetView])
def tearsheet(run_id: str, platform: PlatformDep, settings: SettingsDep) -> Envelope[TearsheetView]:
    run, record, report_raw = _load_run(settings, run_id)
    report = _gate_report_view(report_raw) if report_raw else None
    p = run.provenance
    metrics = record.get("metrics") or {}
    trades = [t for t in record.get("trades") or [] if isinstance(t, dict)]
    ccys = {str(t.get("account_ccy") or "") for t in trades} - {""}
    cfg = record.get("config_fingerprint") or {}
    ccy = next(iter(ccys)) if len(ccys) == 1 else str(cfg.get("account_ccy") or "")
    initial = ts._num(metrics.get("initial_equity")) or ts._num(cfg.get("initial_balance"))
    equity = ts.equity_series(record.get("equity_curve") or [])
    caveats: list[Caveat] = []
    if not run.engine_current or run.superseded:
        caveats.append(Caveat(code="backtest_engine_stale", severity="warning",
                              message="This run is from an older engine generation or superseded: "
                              "its numbers are not comparable with a current run.",
                              affects="tearsheet", direction="unknown"))
    if equity.empty:
        caveats.append(Caveat(code="equity_curve_missing", severity="warning",
                              message="The record holds no equity curve; curve-based panels are empty.",
                              affects="equity", direction="unknown"))

    # Benchmark: buy-and-hold for a single instrument with bars, else flat cash.
    benchmark = None
    benchmark_kind = "flat"
    benchmark_note = ("Flat: the initial equity held in cash (no interest). Buy-and-hold is "
                      "defined only for a single-instrument run with bars mounted.")
    if len(run.instruments) == 1 and not equity.empty and initial:
        closes, info = _closes(settings, run.instruments[0], run.timeframe,
                               equity.index[0], equity.index[-1])
        if closes is not None:
            bh = ts.buy_and_hold(equity, closes, initial)
            if not bh.empty:
                benchmark = _series(f"Buy and hold {run.instruments[0]}", p, ccy, bh)
                benchmark_kind = "buy_and_hold"
                benchmark_note = (f"Buy and hold {run.instruments[0]}: the initial equity held long "
                                  f"in the instrument's own price (dataset {info}), without costs, "
                                  "financing or currency conversion.")
        else:
            benchmark_note = info + " Flat cash is drawn instead."
    if benchmark is None and not equity.empty and initial:
        import pandas as pd

        flat = pd.Series(float(initial), index=equity.index)
        benchmark = _series("Flat (initial equity)", p, ccy, flat)

    uw = ts.underwater(equity)
    periods = ts.drawdown_periods(equity)
    monthly = ts.monthly_returns(equity, initial)
    sizes = _contract_sizes([str(t.get("instrument") or "") for t in trades] + list(run.instruments))
    exc = ts.excursions(trades, sizes)
    r_values = [e.r_multiple for e in exc if e.r_multiple is not None]
    if trades and len(r_values) == len(trades):
        hist = ts.histogram(r_values)
        hist_view = HistogramView(basis="r_multiple", provenance=p, unit="R", edges=hist.edges,
                                  counts=hist.counts, n=hist.n, mean=_fig(hist.mean, p, "R"),
                                  median=_fig(hist.median, p, "R"))
    else:
        pnls = [e.net_pnl for e in exc if e.net_pnl is not None]
        hist = ts.histogram(pnls)
        hist_view = HistogramView(basis="net_pnl", provenance=p, unit=ccy, edges=hist.edges,
                                  counts=hist.counts, n=hist.n, mean=_fig(hist.mean, p, ccy),
                                  median=_fig(hist.median, p, ccy))
    has_exc = any(e.mae is not None for e in exc)
    exc_note = (
        "MAE and MFE are the engine's own per-bar excursions, recorded at the ledger boundary "
        "(backtest/position.py) in the account currency at the closing FX rate; in R when the "
        "trade's stop was recorded."
        if has_exc else
        "This run's trade rows carry no excursion keys, so no MAE or MFE is shown; nothing is "
        "estimated. Re-run it to record them."
    )
    rets = ts.daily_returns(equity, initial)
    dpy = ts._num(metrics.get("days_per_year")) or ts.measured_days_per_year(rets)
    rolling = ts.rolling_sharpe(rets, days_per_year=dpy)
    breakdown = [
        InstrumentBreakdownView(
            instrument=sym,
            trades=_fig(s.n, p, "count"),
            net_pnl=_fig(s.net, p, ccy),
            win_rate_pct=_fig(s.win_rate_pct, p, "pct"),
            profit_factor=_fig(s.profit_factor, p, "ratio", reason="Undefined: no losing trade."),
            expectancy=_fig(s.expectancy, p, ccy),
            expectancy_r=_fig(s.expectancy_r, p, "R", sample=s.n_with_r,
                              reason="No trade on this instrument has a recorded stop."),
        )
        for sym, s in ts.by_instrument(trades, sizes).items()
    ]
    view = TearsheetView(
        run=run,
        account_ccy=ccy,
        stats=_run_stats(record, run, report, ccy),
        equity=_series("Equity", p, ccy, equity),
        benchmark=benchmark,
        benchmark_kind=benchmark_kind,
        benchmark_note=benchmark_note,
        underwater=_series("Underwater", p, "pct", uw),
        drawdowns=[
            DrawdownPeriodView(
                rank=d.rank, peak_at=d.peak_at.to_pydatetime(), trough_at=d.trough_at.to_pydatetime(),
                recovered_at=d.recovered_at.to_pydatetime() if d.recovered_at is not None else None,
                depth_pct=_fig(d.depth_pct, p, "pct"), peak_equity=_fig(d.peak_equity, p, ccy),
                trough_equity=_fig(d.trough_equity, p, ccy), decline_days=_fig(d.decline_days, p, "days"),
                recovery_days=_fig(d.recovery_days, p, "days",
                                   reason="Not recovered by the end of the run."),
                length_days=_fig(d.length_days, p, "days"),
            )
            for d in periods
        ],
        monthly=[MonthlyCellView(year=c.year, month=c.month,
                                 return_pct=_fig(c.return_pct, p, "pct",
                                                 reason="The previous month ended at no equity."))
                 for c in monthly.cells],
        yearly=[YearReturnView(year=y, return_pct=_fig(v, p, "pct"))
                for y, v in monthly.years.items()],
        trade_histogram=hist_view,
        excursions=ExcursionTable(
            provenance=p, pnl_unit=ccy, r_unit="R",
            trade_id=[e.trade_id for e in exc], instrument=[e.instrument for e in exc],
            direction=[e.direction for e in exc],
            entry_time=[e.entry_time.to_pydatetime() if e.entry_time is not None else None for e in exc],
            exit_time=[e.exit_time.to_pydatetime() if e.exit_time is not None else None for e in exc],
            exit_reason=[e.exit_reason for e in exc], bars_held=[e.bars_held for e in exc],
            net_pnl=[e.net_pnl for e in exc], r_multiple=[e.r_multiple for e in exc],
            mae=[e.mae for e in exc], mfe=[e.mfe for e in exc],
            mae_r=[e.mae_r for e in exc], mfe_r=[e.mfe_r for e in exc],
        ),
        excursions_note=exc_note,
        rolling_sharpe=_series(f"Rolling Sharpe ({ts.ROLLING_SHARPE_DAYS}d)", p, "ratio", rolling),
        rolling_window_days=ts.ROLLING_SHARPE_DAYS,
        by_instrument=breakdown,
        pooled=len(run.instruments) > 1,
        gate_report=report,
        provenance=_provenance_block(platform, settings, run, record, report),
        definitions=_defs(list(ts.DEFINITIONS)),
        chart_links={sym: f"/markets/{sym}?run={run.run_id}" for sym in run.instruments},
        strategy_link=f"/research/strategies/{run.strategy_id}",
    )
    datasets = ", ".join(run.dataset_version_ids) or "not recorded"
    return Envelope(
        data=view,
        source=_note("live", f"Research ledger backtest {run.run_id} ({run.strategy_id}, engine "
                     f"{run.engine_version or 'unstamped'}), dataset {datasets}; read-only. "
                     f"Analytics: research/tearsheet.py {ts.TEARSHEET_VERSION}."),
        caveats=tuple(caveats),
    )


@router.get("/runs/{run_id}/monte-carlo", response_model=Envelope[MonteCarloView])
def monte_carlo(
    run_id: str,
    settings: SettingsDep,
    paths: int = Query(1_000, ge=100, le=5_000),
    seed: int = Query(ts.DEFAULT_MC_SEED, ge=0, le=2**32 - 1),
    ruin_fraction: float = Query(0.5, gt=0.0, lt=1.0),
) -> Envelope[MonteCarloView]:
    run, record, _report = _load_run(settings, run_id)
    trades = [t for t in record.get("trades") or [] if isinstance(t, dict)]
    metrics = record.get("metrics") or {}
    cfg = record.get("config_fingerprint") or {}
    initial = ts._num(metrics.get("initial_equity")) or ts._num(cfg.get("initial_balance")) or 10_000.0
    sizing = cfg.get("sizing") if isinstance(cfg.get("sizing"), dict) else {}
    risk_fraction = ts._num((sizing or {}).get("risk_fraction"))
    sizes = _contract_sizes([str(t.get("instrument") or "") for t in trades] + list(run.instruments))
    rs = [ts.r_multiple(t, sizes.get(str(t.get("instrument") or "").upper(), 1.0)) for t in trades]
    r_ok = bool(trades) and all(r is not None for r in rs) and risk_fraction is not None
    try:
        result = ts.monte_carlo(
            r_multiples=[float(r) for r in rs if r is not None] if r_ok else None,
            returns=list(ts.trade_returns(trades, initial)),
            initial_equity=initial,
            risk_fraction=float(risk_fraction or 1.0),
            n_paths=paths,
            seed=seed,
            ruin_fraction=ruin_fraction,
        )
    except ValueError as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "monte_carlo_not_possible", str(exc)) from exc
    p = run.provenance
    ccys = {str(t.get("account_ccy") or "") for t in trades} - {""}
    ccy = next(iter(ccys)) if len(ccys) == 1 else str(cfg.get("account_ccy") or "")
    view = MonteCarloView(
        run_id=run.run_id,
        basis=result.basis,
        seed=result.seed,
        n_paths=result.n_paths,
        n_trades=len(trades),
        block_length=Figure(value=result.block_length, provenance=p, unit="trades", estimated=True),
        risk_fraction=result.risk_fraction,
        initial_equity=Figure(value=result.initial_equity, provenance=p, unit=ccy),
        ruin_fraction=result.ruin_fraction,
        provenance=p,
        ruin_probability=Figure(value=result.ruin_probability * 100.0, provenance=p, unit="pct",
                                sample_size=result.n_paths, estimated=True),
        fan_steps=result.fan_steps,
        fan={f"p{int(k)}": v for k, v in result.fan.items()},
        final_equity_percentiles={
            f"p{int(k)}": Figure(value=v, provenance=p, unit=ccy, sample_size=result.n_paths,
                                 estimated=True)
            for k, v in result.final_equity_percentiles.items()
        },
        drawdown_percentiles={
            f"p{int(k)}": Figure(value=v, provenance=p, unit="pct", sample_size=result.n_paths,
                                 estimated=True)
            for k, v in result.drawdown_percentiles.items()
        },
        realised_max_drawdown_pct=_fig(result.realised_max_drawdown_pct, p, "pct"),
        realised_final_equity=_fig(result.realised_final_equity, p, ccy),
        account_ccy=ccy,
        method=(
            "Politis-Romano stationary bootstrap of the trade sequence (stats/bootstrap.py "
            "resample_with_compounding), block length from Politis and White (2004) "
            f"({result.block_length:.2f} trades), {result.n_paths} paths, seed {result.seed}. "
            + ("Each resampled R multiple is re-sized off running equity at the run's risk "
               f"fraction ({result.risk_fraction:g})." if result.basis == "r_multiple" else
               "Per-trade returns on running equity are resampled (risk fraction 1).")
            + f" Ruin: a drawdown of {100 * (1 - result.ruin_fraction):.0f}% from the running peak."
        ),
        notes=[*list(result.notes), "Resampling assumes the future draws from the same trade distribution; it cannot " "show a regime the backtest never saw."],
    )
    return Envelope(data=view, source=_note("live", f"Research ledger backtest {run.run_id}; "
                                            "simulated, not observed."))


@router.get("/compare", response_model=Envelope[ComparisonView])
def compare(
    settings: SettingsDep,
    runs_param: str = Query(..., alias="runs", description="Comma-separated run ids, 2 to 6."),
) -> Envelope[ComparisonView]:
    ids = [r.strip() for r in runs_param.split(",") if r.strip()]
    ids = list(dict.fromkeys(ids))
    if not 2 <= len(ids) <= MAX_COMPARE:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_comparison",
                       f"Compare takes 2 to {MAX_COMPARE} distinct run ids; got {len(ids)}.")
    compared: list[ComparedRunView] = []
    returns: dict[str, Any] = {}
    prov_of: dict[str, Provenance] = {}
    caveats: list[Caveat] = []
    for run_id in ids:
        run, record, report_raw = _load_run(settings, run_id)
        report = _gate_report_view(report_raw) if report_raw else None
        metrics = record.get("metrics") or {}
        cfg = record.get("config_fingerprint") or {}
        initial = ts._num(metrics.get("initial_equity")) or ts._num(cfg.get("initial_balance"))
        equity = ts.equity_series(record.get("equity_curve") or [])
        trades = [t for t in record.get("trades") or [] if isinstance(t, dict)]
        ccys = {str(t.get("account_ccy") or "") for t in trades} - {""}
        ccy = next(iter(ccys)) if len(ccys) == 1 else str(cfg.get("account_ccy") or "")
        daily = ts.daily_returns(equity, initial)
        returns[run.run_id] = daily
        prov_of[run.run_id] = run.provenance
        if not run.engine_current or run.superseded:
            caveats.append(Caveat(code="backtest_engine_stale", severity="warning",
                                  message=f"{run.run_id} is from an older engine generation or "
                                  "superseded; it is not comparable with a current run.",
                                  affects=run.run_id, direction="unknown"))
        compared.append(ComparedRunView(
            run=run,
            rebased=_series(run.run_id, run.provenance, "index", ts.rebased(equity, initial)),
            stats=_run_stats(record, run, report, ccy),
            daily_returns=int(len(daily)),
        ))
    ccy_set = {c.stats[0].figure.unit for c in compared}
    if len(ccy_set) > 1:
        caveats.append(Caveat(code="mixed_account_currency", severity="info",
                              message="The runs are in different account currencies: compare the "
                              "rebased curves and ratios, not the currency figures.",
                              affects="comparison", direction="unknown"))
    cells = ts.return_correlations(returns, min_common=20)
    view = ComparisonView(
        runs=compared,
        correlations=[
            CorrelationCellView(
                a=c.a, b=c.b, n_common=c.n_common,
                rho=(Figure(value=c.rho, provenance=prov_of[c.a], unit="ratio", sample_size=c.n_common)
                     if c.rho is not None else Figure.missing(
                         prov_of[c.a], unit="ratio",
                         reason=f"Fewer than 20 common days ({c.n_common}) or no dispersion.")),
            )
            for c in cells
        ],
        min_common_days=20,
        definitions=_defs(["return_correlation", *[k for k, _ in STAT_ORDER]]),
    )
    return Envelope(data=view, source=_note("live", "Research ledger, read-only; rebased to 100 "
                                            "at each run's initial equity."), caveats=tuple(caveats))


# ================================================================ strategy


def _ladder_entry(raw: dict[str, Any], *, source: str, experiment_id: str | None,
                  created_at: datetime | None) -> LadderEntryView:
    binding = raw.get("binding_constraint")
    if isinstance(binding, dict):
        binding_text = str(binding.get("description") or binding.get("name") or "")
    else:
        binding_text = str(binding or "")
    rungs = [
        RungView(index=int(r.get("index", i)), name=str(r.get("name") or ""),
                 outcome=str(r.get("outcome") or ""), reason=str(r.get("reason") or ""))
        for i, r in enumerate(raw.get("rungs") or []) if isinstance(r, dict)
    ]
    return LadderEntryView(
        source=source,
        experiment_id=experiment_id,
        created_at=created_at or _parse_dt(raw.get("created_at")),
        verdict=str(raw.get("verdict") or ""),
        gate_set_version=str(raw.get("gate_set_version") or ""),
        report_version=str(raw.get("report_version") or ""),
        dataset_version_id=str(raw.get("dataset_version_id") or ""),
        strategy_content_hash=str(raw.get("strategy_content_hash") or ""),
        binding_constraint=binding_text,
        trial_count=_fig(raw.get("trial_count_n"), Provenance.OUT_OF_SAMPLE, "count",
                         reason="The report records no trial count."),
        rungs=rungs,
        has_walk_forward=any(r.name == "WALK_FORWARD" and r.outcome != "not_reached" for r in rungs),
    )


def _ladder_reports(settings: Any, strategy_id: str) -> list[tuple[LadderEntryView, dict[str, Any], str]]:
    """``(entry, raw report, ref)`` from the report file and the experiment ledger, newest first."""
    out: list[tuple[LadderEntryView, dict[str, Any], str]] = []
    path = REPORTS_ROOT / f"{strategy_id}.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            out.append((_ladder_entry(raw, source="report_file", experiment_id=None, created_at=None),
                        raw, str(path)))
        except (OSError, ValueError):
            pass
    if settings.experiment_db is not None and Path(settings.experiment_db).exists():
        try:
            from fiboki.research.experiment import ExperimentLedger

            with ExperimentLedger(settings.experiment_db) as ledger:
                rows = ledger.list(strategy_id=strategy_id, allow_stale_keys=True)
        except Exception:
            rows = []
        for exp in rows:
            raw = exp.validation_report_json
            if not raw:
                continue
            out.append((_ladder_entry(raw, source="experiment_ledger", experiment_id=exp.id,
                                      created_at=exp.created_at), dict(raw), exp.id))
    out.sort(key=lambda x: (x[0].created_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    return out


@router.get("/strategies/{strategy_id}/overview", response_model=Envelope[StrategyOverviewView])
def strategy_overview(strategy_id: str, platform: PlatformDep, settings: SettingsDep
                      ) -> Envelope[StrategyOverviewView]:
    doc = platform.strategy(strategy_id)
    if doc is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    caveats: list[Caveat] = []
    runs_list: list[RunSummaryView] = []
    total = 0
    reports: list[GateReportView] = []
    if _ledger_path(settings) is not None:
        runs_list, total = _list_runs(settings, strategy_id=strategy_id, limit=200)
        reports = _gate_reports_for_strategy(settings, strategy_id)
    else:
        caveats.append(Caveat(code="source_not_configured", severity="warning",
                              message="No experiment ledger is configured: runs and gate reports "
                              "are unknown, not absent.", direction="unknown"))
    lifecycle: list[LifecycleBrief] = []
    lifecycle_note = ""
    try:
        for s in platform.lifecycle.statuses():
            if str(getattr(s, "strategy_id", "")) == strategy_id:
                h = str(getattr(s, "strategy_content_hash", "") or getattr(s, "content_hash", ""))
                state = getattr(s, "lifecycle", "")
                lifecycle.append(LifecycleBrief(
                    content_hash=h, state=str(getattr(state, "value", state)),
                    degraded=bool(getattr(s, "degraded", False)), link=f"/lifecycle/{h}",
                ))
        if not lifecycle:
            lifecycle_note = "The lifecycle store holds no entry for this strategy: it has never been registered for paper."
    except Exception as exc:
        lifecycle_note = f"The lifecycle store could not be read ({type(exc).__name__})."
    params: dict[str, Any] = {}
    for name, spec in sorted((getattr(doc, "parameters", {}) or {}).items()):
        params[name] = {
            "kind": getattr(spec, "kind", ""), "default": getattr(spec, "default", None),
            "min": getattr(spec, "min_value", None), "max": getattr(spec, "max_value", None),
            "step": getattr(spec, "step", None),
        }
    full_hash = doc.content_hash()
    view = StrategyOverviewView(
        strategy_id=strategy_id,
        name=str(doc.name),
        family=str(getattr(doc.family, "value", doc.family)),
        hypothesis=str(getattr(doc, "hypothesis", "") or ""),
        content_hash=full_hash,
        timeframes=[str(getattr(t, "value", t)) for t in getattr(doc, "timeframes", [])],
        universe=list(getattr(doc, "universe", [])),
        parameters=params,
        runs=runs_list,
        runs_total=total,
        gate_reports=reports,
        ladder_history=[e for e, _, _ in _ladder_reports(settings, strategy_id)],
        lifecycle=lifecycle,
        lifecycle_note=lifecycle_note,
        links={
            "walk_forward": f"/research/walk-forward?strategy={strategy_id}",
            "parameter_lab": f"/research/parameter-lab?strategy={strategy_id}",
            "validation": "/research/validation",
            "lifecycle": f"/lifecycle/{full_hash[:12]}",
            "chart": f"/markets/{(list(getattr(doc, 'universe', [])) or ['EURUSD'])[0]}",
        },
    )
    return Envelope(data=view, source=_note("mixed", "Strategy registry, research ledger, ladder "
                                            "reports and the lifecycle store."), caveats=tuple(caveats))


@router.get("/strategies/{strategy_id}/walk-forward", response_model=Envelope[WalkForwardView])
def walk_forward(strategy_id: str, platform: PlatformDep, settings: SettingsDep,
                 source_ref: str | None = Query(None, alias="source")) -> Envelope[WalkForwardView]:
    if platform.strategy(strategy_id) is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    reports = [r for r in _ladder_reports(settings, strategy_id) if r[0].has_walk_forward]
    if source_ref:
        reports = [r for r in reports if r[2] == source_ref or r[0].experiment_id == source_ref]
    if not reports:
        raise ApiError(status.HTTP_404_NOT_FOUND, "walk_forward_not_run",
                       "No ladder report for this strategy reached the walk-forward rung. "
                       "Nothing is shown rather than an empty fold table.")
    entry, raw, ref = reports[0]
    rung = next(r for r in raw.get("rungs") or [] if isinstance(r, dict) and r.get("name") == "WALK_FORWARD")
    m = rung.get("metrics") or {}
    folds_raw = [f for f in m.get("folds") or [] if isinstance(f, dict)]
    stab = ts.fold_stability(folds_raw)
    p = Provenance.WALKFORWARD
    engine = raw.get("engine_config") if isinstance(raw.get("engine_config"), dict) else {}
    ccy = str((engine or {}).get("account_ccy") or "")
    folds: list[FoldView] = []
    for f, eff in zip(folds_raw, stab.fold_efficiency, strict=True):
        train, test = f.get("train") or {}, f.get("test") or {}
        folds.append(FoldView(
            fold=int(f.get("fold", len(folds))),
            train_start=_parse_dt(train.get("start")), train_end=_parse_dt(train.get("end")),
            test_start=_parse_dt(test.get("start")), test_end=_parse_dt(test.get("end")),
            is_rate=_fig(f.get("is_rate"), Provenance.BACKTEST, "rate"),
            oos_rate=_fig(f.get("oos_rate"), p, "rate"),
            is_net_profit=_fig(f.get("is_net_profit"), Provenance.BACKTEST, ccy),
            oos_net_profit=_fig(f.get("oos_net_profit"), p, ccy),
            oos_trades=_fig(f.get("oos_trades"), p, "count"),
            oos_sharpe=_fig(f.get("oos_sharpe"), p, "ratio"),
            efficiency=_fig(eff, p, "ratio", reason="Undefined when the in-sample rate is zero."),
            fixed_param_oos_rate=_fig(f.get("fixed_param_oos_rate"), p, "rate"),
            selected_params=dict(f.get("selected_params") or {}),
        ))
    gates = [_check_view(g, p) for g in raw.get("gate_results") or []
             if isinstance(g, dict) and isinstance(g.get("gate"), dict) and g["gate"].get("rung") == 2]
    fold_runs: list[BacktestRunView] = []
    if _ledger_path(settings) is not None:
        fold_runs = [s.run for s in _list_runs(settings, strategy_id=strategy_id, limit=200)[0]
                     if s.run.kind == "walkforward_fold"]
    view = WalkForwardView(
        strategy_id=strategy_id,
        source=entry.source,
        source_ref=ref,
        scheme=str(m.get("scheme") or ""),
        outcome=str(rung.get("outcome") or ""),
        reason=str(rung.get("reason") or ""),
        folds=folds,
        n_folds=stab.n_folds,
        walk_forward_efficiency=_fig(m.get("walk_forward_efficiency"), p, "pct"),
        oos_profitable_fraction=_fig(m.get("oos_profitable_fraction", stab.oos_profitable_fraction), p, "ratio"),
        mean_is_rate=_fig(m.get("mean_is_rate", stab.mean_is_rate), p, "rate"),
        mean_oos_rate=_fig(m.get("mean_oos_rate", stab.mean_oos_rate), p, "rate"),
        oos_rate_sd=_fig(stab.oos_rate_sd, p, "rate", reason="Needs two or more folds."),
        oos_rate_cv=_fig(stab.oos_rate_cv, p, "ratio", reason="Undefined when the mean OOS rate is zero."),
        parameter_repeats=stab.parameter_repeats,
        parameter_stability_across_folds=m.get("parameter_stability_across_folds"),
        gates=gates,
        gate_set_version=entry.gate_set_version,
        rate_unit="profit rate (net profit per unit of capital per year, as the ladder measures it)",
        fold_runs=fold_runs,
        available=[r[2] for r in reports],
        definitions={
            "walk_forward_efficiency": DefinitionView(
                label="Walk-forward efficiency",
                definition="Mean out-of-sample profit rate as a percentage of the mean in-sample "
                "profit rate of the SAME selected parameters (the ladder's rung-2 metric).",
                formula="mean(OOS rate) / mean(IS rate) x 100"),
            "efficiency": DefinitionView(
                label="Fold efficiency", definition="One fold's OOS rate over its IS rate.",
                formula="OOS rate_k / IS rate_k"),
            "oos_rate_cv": DefinitionView(
                label="OOS dispersion", definition="Standard deviation of the OOS rate across "
                "folds over the absolute mean: the larger, the less stable.",
                formula="sd(OOS rate) / |mean(OOS rate)|"),
        },
    )
    return Envelope(data=view, source=_note("live", f"Validation ladder report ({entry.source}: {ref}), "
                                            "rung 2 WALK_FORWARD, as filed."))


# ================================================================ parameter surface


SURFACE_METRICS = ("sharpe", "total_return_pct", "cagr_pct", "profit_factor", "expectancy",
                   "win_rate_pct", "max_drawdown_pct", "sortino", "calmar")


def _axis_values(spec: Any) -> list[float]:
    try:
        domain = spec.domain()
    except Exception:
        return []
    values = [float(v) for v in domain if isinstance(v, int | float) and not isinstance(v, bool)]
    return values if len(values) <= 60 else []


@router.get("/parameter-lab/{strategy_id}/surface", response_model=Envelope[ParameterSurfaceView])
def parameter_surface(
    strategy_id: str,
    platform: PlatformDep,
    settings: SettingsDep,
    x: str | None = Query(None),
    y: str | None = Query(None),
    metric: str = Query("sharpe"),
    fixed: str | None = Query(None, description="JSON object of the other parameters' values."),
) -> Envelope[ParameterSurfaceView]:
    from fiboki.validation.gates import PRODUCTION_GATE_SET
    from fiboki.validation.ladder import LadderConfig

    doc = platform.strategy(strategy_id)
    if doc is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    specs = {n: s for n, s in sorted((getattr(doc, "parameters", {}) or {}).items())
             if getattr(s, "kind", "") in ("int", "float")}
    names = list(specs)
    if len(names) < 2:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "surface_needs_two_parameters",
                       f"{strategy_id} declares {len(names)} numeric parameter(s); a surface needs two.")
    x = x or names[0]
    y = y or names[1]
    if x not in specs or y not in specs or x == y:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_axes",
                       f"x and y must be two different numeric parameters of {strategy_id}: {names}.")
    if metric not in SURFACE_METRICS:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_metric",
                       f"metric must be one of {list(SURFACE_METRICS)}.")
    try:
        fixed_req = json.loads(fixed) if fixed else {}
        if not isinstance(fixed_req, dict):
            raise ValueError
    except ValueError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_fixed", "fixed must be a JSON object.") from exc
    defaults = dict(doc.default_values()) if callable(getattr(doc, "default_values", None)) else {}
    others = [n for n in names if n not in (x, y)]
    fixed_values = {n: fixed_req.get(n, defaults.get(n)) for n in others}

    runs_considered = runs_unknown = runs_other = 0
    observations: list[tuple[float, float, float | None, str]] = []
    notes: list[str] = []
    if _ledger_path(settings) is None:
        notes.append("No experiment ledger is configured: there are no stored runs to place.")
    else:
        conn = _connect_ro(_ledger(settings))
        try:
            rows = conn.execute(
                "SELECT id, json_extract(payload_json, '$.job_id'), json_extract(payload_json, '$.metrics'), "
                "json_extract(payload_json, '$.label') FROM research_artefact "
                "WHERE collection = 'backtests' AND strategy_id = :s ORDER BY created_at, id",
                {"s": strategy_id},
            ).fetchall() if _has_artefacts(conn) else []
        except sqlite3.Error as exc:
            raise _sqlite_error(exc) from exc
        finally:
            conn.close()
        rows = [r for r in rows if not str(r[3] or "").startswith("walkforward_fold")]
        payloads = _job_payloads(settings, {str(r[1]) for r in rows if r[1]})
        for run_id, job_id, metrics_json, _label in rows:
            runs_considered += 1
            payload = payloads.get(str(job_id or ""))
            if payload is None:
                runs_unknown += 1
                continue
            params, _ = _run_parameters(platform, strategy_id, payload)
            if any(_num_eq(params.get(n), v) is False for n, v in fixed_values.items()):
                runs_other += 1
                continue
            px, py = ts._num(params.get(x)), ts._num(params.get(y))
            if px is None or py is None:
                runs_unknown += 1
                continue
            score = ts._num((json.loads(metrics_json or "{}") or {}).get(metric))
            observations.append((px, py, score, str(run_id)))
    xs = sorted(set(_axis_values(specs[x])) | {o[0] for o in observations})
    ys = sorted(set(_axis_values(specs[y])) | {o[1] for o in observations})
    radius = int(LadderConfig().stability_radius)
    plateau_metrics = {"point_plateau_ratio", "plateau_neighbourhood_median_ratio", "plateau_neighbourhood_min"}
    gates = [g for g in PRODUCTION_GATE_SET.gates if g.metric in plateau_metrics]
    cells = ts.parameter_surface(observations, x_values=xs, y_values=ys, radius=radius, plateau_gates=gates)
    if runs_unknown:
        notes.append(f"{runs_unknown} run(s) could not be placed: the job that produced them is not in "
                     "the job ledger, so the parameters they executed with are unknown.")
    if runs_other:
        notes.append(f"{runs_other} run(s) used other values of the fixed parameters and are not on this slice.")
    if not observations:
        notes.append("No stored run lies on this slice: the grid shows the declared domain only.")
    def _cell(v: float | None, unit: str, n: int | None = None) -> Figure:
        # Compact on purpose (no reason caveat): a surface can hold thousands of cells.
        if v is None:
            return Figure(value=None, provenance=Provenance.BACKTEST, unit=unit)
        return Figure(value=v, provenance=Provenance.BACKTEST, unit=unit, sample_size=n)

    gate_text = "; ".join(f"{g.metric} {g.comparison.symbol} {g.threshold:g}" for g in gates)
    view = ParameterSurfaceView(
        strategy_id=strategy_id,
        parameters=names,
        x=x, y=y, metric=metric, metrics_available=list(SURFACE_METRICS),
        fixed=fixed_values,
        x_values=xs, y_values=ys,
        cells=[SurfaceCellView(
            x=c.x, y=c.y, n_runs=c.n_runs, run_ids=list(c.run_ids),
            score=_cell(c.score, "", c.n_runs),
            point_plateau_ratio=_cell(c.point_plateau_ratio, "ratio"),
            neighbourhood_median_ratio=_cell(c.neighbourhood_median_ratio, "ratio"),
            neighbourhood_min=_cell(c.neighbourhood_min, ""),
            on_plateau=c.on_plateau, isolated_peak=c.isolated_peak,
            plateau_checks=c.plateau_checks) for c in cells],
        radius=radius,
        plateau_gates=[PlateauGateView(name=g.name, metric=g.metric, comparison=g.comparison.symbol,
                                       threshold=float(g.threshold), rationale=g.rationale) for g in gates],
        gate_set_version=PRODUCTION_GATE_SET.version,
        runs_considered=runs_considered,
        runs_used=len(observations),
        runs_other_params=runs_other,
        runs_unknown_params=runs_unknown,
        plateau_definition=(
            f"A cell is on the plateau when, judged as if it were the selected point, every rung-4 "
            f"plateau gate of {PRODUCTION_GATE_SET.version} passes ({gate_text}) over its neighbours "
            f"within {radius} grid step(s) (Chebyshev, stats/stability.py), and its own score is "
            "positive. Neighbours are the evaluated cells on this slice only."
        ),
        notes=notes,
    )
    return Envelope(data=view, source=_note("live" if observations else "mixed",
                                            "Stored runs in the research ledger, parameters from the job ledger."))


def _num_eq(a: Any, b: Any) -> bool | None:
    fa, fb = ts._num(a), ts._num(b)
    if fa is None or fb is None:
        return None if (a is None or b is None) else (a == b)
    return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-12)


# ================================================================ jobs


def _job_rows(settings: Any, limit: int) -> tuple[list[tuple[Any, ...]], str]:
    path = _jobs_path(settings)
    if not path.exists():
        return [], f"No job ledger at {path}: the research worker has not run on this deployment."
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = conn.execute(
                "SELECT job_id, queue, job_type, spec_json, status, attempts_json, result_json, error, "
                "submitted_at, next_attempt_at, claimed_at FROM job ORDER BY queue_seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "job_ledger_unavailable",
                       "The research job ledger could not be read.",
                       context={"error_class": type(exc).__name__}) from exc
    return rows, f"Job ledger {path} (read-only)."


def _eta_by_type(rows: list[tuple[Any, ...]]) -> dict[str, float]:
    durations: dict[str, list[float]] = {}
    for row in rows:
        if row[4] != "succeeded":
            continue
        try:
            attempts = json.loads(row[5] or "[]")
        except ValueError:
            continue
        for a in attempts:
            s, f = _parse_dt(a.get("started_at")), _parse_dt(a.get("finished_at"))
            if s and f and a.get("status") == "succeeded":
                durations.setdefault(str(row[2]), []).append((f - s).total_seconds())
    import statistics

    return {k: float(statistics.median(v)) for k, v in durations.items() if v}


def _outputs(result: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    bt = result.get("backtest_id")
    if bt:
        out["tearsheet"] = f"/research/runs/{bt}"
    for i, b in enumerate(result.get("backtest_ids") or []):
        out[f"fold {i}"] = f"/research/runs/{b}"
    if result.get("report_id") and result.get("backtest_id"):
        out["gate report"] = f"/research/runs/{result['backtest_id']}#gates"
    if result.get("strategy_id"):
        out["strategy"] = f"/research/strategies/{result['strategy_id']}"
    return out


def _shard_views(root: Path, now: datetime) -> list[ShardView]:
    out: list[ShardView] = []
    if not root.exists():
        return out
    files = sorted(list(root.glob("*/shards/**/*.json")) + list(root.glob("*/shards/**/*.jsonl")))
    for path in files[:200]:
        study = path.relative_to(root).parts[0]
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        age = (now - mtime).total_seconds()
        view = _shard_view(path, study, root, mtime, age)
        out.append(view)
    return out


def _shard_total(shard: Any) -> int | None:
    if not isinstance(shard, dict):
        return None
    try:
        if "k" in shard and "n" in shard:
            return len(range(int(shard["k"]), int(shard["of"]), int(shard["n"])))
        if "start" in shard and "end" in shard:
            return int(shard["end"]) - int(shard["start"])
    except (TypeError, ValueError):
        return None
    return None


def _shard_view(path: Path, study: str, root: Path, mtime: datetime, age: float) -> ShardView:
    p = Provenance.BACKTEST
    rel = str(path.relative_to(root.parent)) if root.parent != path else str(path)
    stage = unit = error = ""
    done = 0
    total: int | None = None
    partial = True
    per_unit: float | None = None
    try:
        if path.suffix == ".jsonl":
            lines = path.read_text(encoding="utf-8").splitlines()
            header = json.loads(lines[0]) if lines else {}
            rows = []
            for line in lines[1:]:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    break
            stage = str(header.get("stage") or "")
            unit = "documents"
            done = len({int(r["index"]) for r in rows if isinstance(r.get("index"), int)})
            indices = header.get("indices")
            total = len(indices) if isinstance(indices, list) else None
            secs = [float(r["seconds"]) for r in rows if isinstance(r.get("seconds"), int | float)]
            per_unit = (sum(secs) / len(secs)) if secs else None
            partial = total is None or done < total
        else:
            doc = json.loads(path.read_text(encoding="utf-8"))
            stage = str(doc.get("stage") or "")
            runs_ = doc.get("runs") or []
            sr_grid = (doc.get("config") or {}).get("sr_grid") if isinstance(doc.get("config"), dict) else None
            if stage == "p0" and isinstance(sr_grid, list) and sr_grid:
                unit = "replicates"
                done = len({r.get("replicate") for r in runs_ if isinstance(r, dict)})
            else:
                unit = "candidates" if stage == "main" else "rows"
                done = len(runs_)
            total = _shard_total(doc.get("shard"))
            partial = bool(doc.get("partial")) or (total is not None and done < total)
            wall = ts._num(doc.get("wall_seconds"))
            per_unit = (wall / done) if (wall and done) else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error = f"unreadable ({type(exc).__name__})"
    remaining = (total - done) if (total is not None) else None
    eta = (remaining * per_unit) if (per_unit is not None and remaining is not None and partial) else None
    return ShardView(
        study=study, stage=stage, path=rel, unit=unit, done=done, total=total, partial=partial,
        progress=(Figure(value=done / total, provenance=p, unit="ratio", sample_size=total)
                  if total else Figure.missing(p, unit="ratio", reason="The shard does not declare its size.")),
        seconds_per_unit=_fig(per_unit, p, "s", reason="No per-unit timing recorded yet."),
        eta_seconds=(Figure(value=eta, provenance=p, unit="s", estimated=True)
                     if eta is not None else Figure.missing(
                         p, unit="s", reason="Done." if not partial else "No per-unit timing recorded yet.")),
        updated_at=mtime, age_seconds=Figure(value=age, provenance=p, unit="s"), error=error,
    )


@router.get("/jobs", response_model=Envelope[JobQueueView])
def jobs(settings: SettingsDep, limit: int = Query(200, ge=1, le=1_000)) -> Envelope[JobQueueView]:
    now = datetime.now(tz=UTC)
    rows, ledger_note = _job_rows(settings, limit)
    eta = _eta_by_type(rows)
    from fiboki.agents.orchestrator import JobStatus

    counts: dict[str, int] = {s.value: 0 for s in JobStatus}
    views: list[JobView] = []
    p = Provenance.BACKTEST
    for (job_id, queue, job_type, spec, status_, attempts_json, result_json, error,
         submitted, next_at, claimed) in rows:
        counts[status_] = counts.get(status_, 0) + 1
        try:
            payload = dict(json.loads(spec).get("payload") or {})
        except (ValueError, AttributeError):
            payload = {}
        try:
            attempts = json.loads(attempts_json or "[]")
            spec_body = json.loads(spec)
        except ValueError:
            attempts, spec_body = [], {}
        try:
            result = json.loads(result_json) if result_json else {}
        except ValueError:
            result = {}
        started = _parse_dt(claimed) if status_ == "running" else (
            _parse_dt(attempts[-1].get("started_at")) if attempts else None)
        elapsed = (now - started).total_seconds() if (status_ == "running" and started) else None
        median = eta.get(str(job_type))
        if status_ == "running" and median is not None and elapsed is not None:
            eta_fig = Figure(value=max(0.0, median - elapsed), provenance=p, unit="s", estimated=True,
                             sample_size=None)
        elif status_ == "pending" and median is not None:
            eta_fig = Figure(value=median, provenance=p, unit="s", estimated=True)
        else:
            eta_fig = Figure.missing(p, unit="s", reason=(
                "No completed job of this type to time against." if status_ in ("running", "pending")
                else "Not queued or running."))
        views.append(JobView(
            job_id=str(job_id), job_type=str(job_type), queue=str(queue), status=str(status_),
            submitted_at=_parse_dt(submitted), started_at=started, next_attempt_at=_parse_dt(next_at),
            attempts=len(attempts), max_attempts=int(spec_body.get("max_attempts", 1) or 1),
            error=str(error or ""), strategy_id=str(payload.get("strategy_id") or ""),
            instrument=str(payload.get("instrument") or ""),
            elapsed_seconds=(Figure(value=elapsed, provenance=p, unit="s") if elapsed is not None
                             else Figure.missing(p, unit="s", reason="Not running.")),
            eta_seconds=eta_fig,
            outputs=_outputs(result if isinstance(result, dict) else {}),
        ))
    order = {"running": 0, "pending": 1, "failed": 2, "dead_letter": 3, "succeeded": 4}
    views.sort(key=lambda v: (order.get(v.status, 9), -(v.submitted_at.timestamp() if v.submitted_at else 0)))
    shards = _shard_views(REPORTS_ROOT, now)
    view = JobQueueView(
        counts=counts, jobs=views, shards=shards, job_ledger=ledger_note,
        shards_root=str(REPORTS_ROOT / "*" / "shards"),
        eta_method=("Job ETA: the median duration of this job type's succeeded attempts in the ledger, "
                    "less the elapsed time (an estimate). Shard ETA: remaining units times the "
                    "shard's own mean seconds per unit."),
    )
    kind = "live" if (rows or shards) else "absent"
    return Envelope(data=view, source=_note(kind, ledger_note + f" Shards under {view.shards_root}."))


# ================================================================ correlations


CORRELATION_WINDOWS = (20, 50, 100, 250, 500)
CORRELATION_TIMEFRAMES = ("H1", "H4", "D1")
MAX_CORRELATION_INSTRUMENTS = 40


@router.get("/correlations", response_model=Envelope[CorrelationMatrixView])
def correlations(
    settings: SettingsDep,
    timeframe: str = Query("D1"),
    window: int = Query(100),
    instruments: str | None = Query(None, description="Comma-separated; default every instrument with data."),
) -> Envelope[CorrelationMatrixView]:
    if timeframe not in CORRELATION_TIMEFRAMES:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "unknown_timeframe",
                       f"timeframe must be one of {list(CORRELATION_TIMEFRAMES)}.")
    if window not in CORRELATION_WINDOWS:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_window",
                       f"window must be one of {list(CORRELATION_WINDOWS)} bars.")
    if settings.data_root is None or not Path(settings.data_root).exists():
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "market_data_not_mounted",
                       "No market-data root is mounted, so no correlation can be estimated. An empty "
                       "matrix is NOT an uncorrelated book.")
    import numpy as np
    import pandas as pd

    from fiboki.data.store import DataStore
    from fiboki.marketstate.cross_asset import CrossAssetError, align_panel

    tf = Timeframe(timeframe)
    excluded: dict[str, str] = {}
    versions: dict[str, str] = {}
    frames: dict[str, Any] = {}
    try:
        with DataStore(settings.data_root) as store:
            inv = store.inventory()
            if instruments:
                wanted = [s.strip().upper() for s in instruments.split(",") if s.strip()]
            else:
                if inv.empty:
                    wanted = []
                else:
                    sel = inv[(inv["timeframe"] == timeframe) & (inv["kind"] == "validated")]
                    wanted = sorted({str(s).upper() for s in sel["instrument"]})
            if len(wanted) > MAX_CORRELATION_INSTRUMENTS:
                for s in wanted[MAX_CORRELATION_INSTRUMENTS:]:
                    excluded[s] = f"over the {MAX_CORRELATION_INSTRUMENTS}-instrument cap"
                wanted = wanted[:MAX_CORRELATION_INSTRUMENTS]
            span = pd.Timedelta(minutes=tf.minutes * window * 4 + 14 * 24 * 60)
            for sym in wanted:
                try:
                    latest = store.catalogue.latest(sym, timeframe)
                    if latest is None:
                        excluded[sym] = "no validated dataset for this timeframe"
                        continue
                    last = pd.Timestamp(latest.last_timestamp)
                    last = last.tz_localize("UTC") if last.tzinfo is None else last
                    frame, version = store.read_latest(instrument=sym, timeframe=timeframe, start=last - span)
                except Exception as exc:
                    excluded[sym] = f"bars unreadable ({type(exc).__name__})"
                    continue
                if len(frame) < window + 1:
                    excluded[sym] = f"only {len(frame)} bars in the read window"
                    continue
                frames[sym] = frame
                versions[sym] = getattr(version, "version_id", "")
    except Exception as exc:
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "bars_unavailable",
                       "The bar store could not be read.", context={"error_class": type(exc).__name__}) from exc
    if len(frames) < 2:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "correlation_needs_two_series",
                       "Fewer than two instruments have enough bars for this window.",
                       context={"excluded": excluded})
    try:
        panel = align_panel(frames)
    except CrossAssetError as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "correlation_not_aligned", str(exc)) from exc
    corr, n = ts.window_correlation(panel.log_returns, window)
    names = list(corr.columns)
    matrix = [[(None if not np.isfinite(v) else float(v)) for v in corr.loc[a, names].to_numpy()] for a in names]
    tail = panel.log_returns.dropna(how="any").tail(window)
    caveats: list[Caveat] = []
    if n < window:
        caveats.append(Caveat(code="correlation_short_window", severity="warning",
                              message=f"Only {n} common bars were available of the {window} requested: the "
                              "series overlap less than the window.", affects="correlation", direction="unknown"))
    view = CorrelationMatrixView(
        timeframe=timeframe, provenance=Provenance.BACKTEST, window=window,
        windows=list(CORRELATION_WINDOWS),
        timeframes=list(CORRELATION_TIMEFRAMES), instruments=names, matrix=matrix, bars_used=n,
        first_common=tail.index[0].to_pydatetime() if n else None,
        last_common=tail.index[-1].to_pydatetime() if n else None,
        dataset_version_ids=versions, excluded=excluded,
        method=("Pearson correlation of log close-to-close returns over the last N bars the "
                "instruments have IN COMMON (inner join, no forward fill: marketstate/cross_asset.py "
                "align_panel), trailing and causal."),
    )
    return Envelope(data=view, source=_note("live", "Canonical bar store, validated datasets."),
                    caveats=tuple(caveats))
