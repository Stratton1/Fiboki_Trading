"""RESEARCH robustness and integrity diagnostics (Wave 3R): advisory, beside the verdict.

Twelve diagnostics the best algorithmic research platforms carry (Freqtrade's
recursive and look-ahead analyses, StrategyQuant X and Build Alpha's robustness
tests, pyfolio and QuantStats breakdowns, System Parameter Permutation). Every
number is computed by :mod:`fiboki.research.robustness`; the workstation draws
it. NONE of them is read by the ladder, a gate, a threshold or a filed
pre-registration: a robustness figure is reported, never wired into promotion.

Routes under ``/api/research``:

Ledger diagnostics, computed on request from the stored run (read-only):
``GET /runs/{run_id}/robustness/bands``         5/50/95% block-bootstrap bands
``GET /runs/{run_id}/robustness/breakdowns``    exit reason, direction, calendar
``GET /runs/{run_id}/robustness/consistency``   K sub-periods, every instrument
``GET /runs/{run_id}/robustness/fills``         intra-candle fill rule per trade
``GET /strategies/{id}/optimisation-profile``   median versus peak over the grid
``GET /rankings``                               selectable penalised objectives

Re-run diagnostics, computed by the research WORKER as ``robustness`` jobs and
read back from their cache (``<state>/research/robustness.sqlite``):
``GET  /runs/{run_id}/robustness``              status of every re-run kind
``GET  /runs/{run_id}/robustness/{kind}``       one kind's result or progress
``POST /runs/{run_id}/robustness/{kind}``       queue it (admin, audited)

The holdout is never read: ledger diagnostics exclude everything at or after
the holdout boundary, and re-run jobs load bars strictly before it
(:mod:`fiboki.research.robustness.boundary`).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.routers.chart import BacktestRunView, _connect_ro, _has_artefacts
from fiboki.api.routers.research_analytics import (
    _contract_sizes,
    _fig,
    _job_payload,
    _job_payloads,
    _jobs_path,
    _ledger,
    _ledger_path,
    _load_run,
    _run_parameters,
    _sqlite_error,
)
from fiboki.api.security import Principal, require_admin
from fiboki.core.enums import Provenance
from fiboki.research import tearsheet as ts
from fiboki.research.robustness import ledger as rl
from fiboki.research.robustness.boundary import (
    HoldoutBoundary,
    ResearchSlice,
    research_slice,
    resolve_boundary,
    run_span,
)
from fiboki.research.robustness.engine import RunBinding
from fiboki.research.robustness.job import RERUN_KINDS, idempotency_key
from fiboki.research.robustness.rerun import options_of
from fiboki.research.robustness.store import cache_key, read_entry

router = APIRouter(prefix="/api/research", tags=["research"])
AdminPrincipal = Annotated[Principal, Depends(require_admin)]

P = Provenance.BACKTEST
ADVISORY = ("Advisory diagnostic: reported beside the ladder verdict, never read by a gate, a "
            "threshold or a pre-registration.")
KIND_TITLES = {
    "warmup": "Warm-up stability (recursive indicators)",
    "lookahead": "Look-ahead detector",
    "execution": "Execution-realism stress",
    "perturbation": "Parameter-perturbation Monte Carlo",
    "random": "Versus random entries",
    "noise": "Price-noise test",
}


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


def _count(v: Any, reason: str = "") -> Figure:
    return _fig(v, P, "count", reason=reason)


# ================================================================ shared views


class HoldoutBoundaryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime
    #: ``registry`` (the segment the ladder defined) or ``provisional``.
    basis: str
    detail: str
    trades_excluded: Figure
    points_excluded: Figure
    note: str


class DistributionView(BaseModel):
    """Quantiles of one figure across many re-runs (simulated, so ``estimated``)."""

    model_config = ConfigDict(frozen=True)

    n: Figure
    p5: Figure
    p25: Figure
    p50: Figure
    p75: Figure
    p95: Figure
    mean: Figure


class RunMetricsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    n_trades: Figure
    net_profit: Figure
    total_return_pct: Figure
    cagr_pct: Figure
    sharpe: Figure
    sortino: Figure
    calmar: Figure
    max_drawdown_pct: Figure
    profit_factor: Figure
    win_rate_pct: Figure
    expectancy: Figure
    total_costs: Figure


class RerunBriefView(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: str
    metrics: RunMetricsView
    ledger_sha256: str
    rejections: dict[str, int]
    signals_emitted: Figure


_METRIC_UNITS = {
    "n_trades": "count", "net_profit": "account", "total_return_pct": "pct", "cagr_pct": "pct",
    "sharpe": "ratio", "sortino": "ratio", "calmar": "ratio", "max_drawdown_pct": "pct",
    "profit_factor": "ratio", "win_rate_pct": "pct", "expectancy": "account", "total_costs": "account",
}


def _metrics(raw: dict[str, Any] | None, ccy: str, *, estimated: bool = False) -> RunMetricsView:
    raw = raw or {}
    out = {}
    for key, unit in _METRIC_UNITS.items():
        u = ccy if unit == "account" else unit
        v = ts._num(raw.get(key))
        out[key] = (Figure(value=v, provenance=P, unit=u, estimated=estimated) if v is not None
                    else Figure.missing(P, unit=u, reason="Undefined for this re-run (no trades, no "
                                        "losing trade, or a degenerate curve)."))
    return RunMetricsView(**out)


def _dist(raw: dict[str, Any] | None, unit: str) -> DistributionView:
    raw = raw or {}
    n = int(raw.get("n") or 0)

    def q(k: str) -> Figure:
        v = ts._num(raw.get(k))
        return (Figure(value=v, provenance=P, unit=unit, sample_size=n, estimated=True) if v is not None
                else Figure.missing(P, unit=unit, reason="No defined value in any re-run."))

    return DistributionView(n=_count(n), p5=q("p5"), p25=q("p25"), p50=q("p50"), p75=q("p75"),
                            p95=q("p95"), mean=q("mean"))


def _brief(raw: dict[str, Any] | None, ccy: str) -> RerunBriefView:
    raw = raw or {}
    return RerunBriefView(profile=str(raw.get("profile") or ""), metrics=_metrics(raw.get("metrics"), ccy),
                          ledger_sha256=str(raw.get("ledger_sha256") or ""),
                          rejections={str(k): int(v) for k, v in (raw.get("rejections") or {}).items()},
                          signals_emitted=_count(raw.get("signals_emitted")))


def _ratio(v: Any, reason: str, unit: str = "ratio") -> Figure:
    x = ts._num(v)
    return Figure(value=x, provenance=P, unit=unit, estimated=True) if x is not None else Figure.missing(
        P, unit=unit, reason=reason)


# ================================================================ run context


def _ccy(record: dict[str, Any]) -> str:
    trades = [t for t in record.get("trades") or [] if isinstance(t, dict)]
    ccys = {str(t.get("account_ccy") or "") for t in trades} - {""}
    cfg = record.get("config_fingerprint") or {}
    return next(iter(ccys)) if len(ccys) == 1 else str(cfg.get("account_ccy") or "")


def _initial(record: dict[str, Any]) -> float | None:
    metrics = record.get("metrics") or {}
    cfg = record.get("config_fingerprint") or {}
    return ts._num(metrics.get("initial_equity")) or ts._num(cfg.get("initial_balance"))


def _boundary(settings: Any, run: BacktestRunView, record: dict[str, Any]) -> HoldoutBoundary:
    first, last = run_span(record)
    try:
        return resolve_boundary(dataset_version_ids=list(run.dataset_version_ids) or list(
            record.get("dataset_version_ids") or []), first=first, last=last,
            registry_path=settings.paths.holdout_db)
    except ValueError as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "holdout_boundary_unknown", str(exc)) from exc


def _boundary_view(s: ResearchSlice) -> HoldoutBoundaryView:
    return HoldoutBoundaryView(
        start=s.boundary.start.to_pydatetime(), basis=s.boundary.basis, detail=s.boundary.detail,
        trades_excluded=_count(s.trades_excluded), points_excluded=_count(s.points_excluded), note=s.note,
    )


def _run_context(settings: Any, run_id: str) -> tuple[BacktestRunView, dict[str, Any], ResearchSlice]:
    run, record, _ = _load_run(settings, run_id)
    boundary = _boundary(settings, run, record)
    return run, record, research_slice(record, boundary)


def _caveats(run: BacktestRunView, s: ResearchSlice) -> tuple[Caveat, ...]:
    out = [Caveat(code="advisory_diagnostic", severity="info", message=ADVISORY, affects="robustness",
                  direction="unknown")]
    if s.trades_excluded or s.points_excluded:
        out.append(Caveat(code="holdout_excluded", severity="info", message=s.note, affects="robustness",
                          direction="unknown"))
    if not run.engine_current or run.superseded:
        out.append(Caveat(code="backtest_engine_stale", severity="warning",
                          message="This run is from an older engine generation or superseded; its "
                          "ledger diagnostics describe that engine's trades.", affects="robustness",
                          direction="unknown"))
    return tuple(out)


# ================================================================ 3. bands


class BandView(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    point: Figure
    p5: Figure
    p50: Figure
    p95: Figure
    #: ``daily returns`` or ``trade net P&L``: what was resampled.
    basis: str
    observations: Figure
    block_length: Figure


class ConfidenceBandsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    seed: int
    n_boot: int
    bands: list[BandView]
    boundary: HoldoutBoundaryView
    method: str


@router.get("/runs/{run_id}/robustness/bands", response_model=Envelope[ConfidenceBandsView])
def bands(run_id: str, settings: SettingsDep, seed: int = Query(ts.DEFAULT_MC_SEED, ge=0, le=2**32 - 1),
          n_boot: int = Query(1000, ge=200, le=5000)) -> Envelope[ConfidenceBandsView]:
    run, record, s = _run_context(settings, run_id)
    out = rl.confidence_bands(s, initial=_initial(record), seed=seed, n_boot=n_boot)
    units = {"pct": "pct", "ratio": "ratio"}
    views = []
    for b in out["bands"]:
        u = units.get(b.unit, b.unit)

        def fig(v: float | None, b: Any = b, u: str = u) -> Figure:
            return (Figure(value=v, provenance=run.provenance, unit=u, sample_size=b.n_obs, estimated=True)
                    if v is not None else Figure.missing(run.provenance, unit=u, reason=b.reason or "Undefined."))

        point = (Figure(value=b.point, provenance=run.provenance, unit=u, sample_size=b.n_obs)
                 if b.point is not None else Figure.missing(run.provenance, unit=u, reason=b.reason or "Undefined."))
        views.append(BandView(key=b.key, label=b.label, point=point, p5=fig(b.p5), p50=fig(b.p50), p95=fig(b.p95),
                              basis=b.basis, observations=_count(b.n_obs),
                              block_length=_fig(b.block_length, run.provenance, "observations",
                                                reason=b.reason or "Not resampled.", estimated=True)))
    view = ConfidenceBandsView(
        run_id=run.run_id, seed=seed, n_boot=n_boot, bands=views, boundary=_boundary_view(s),
        method=(f"Politis-Romano stationary bootstrap (stats/bootstrap.py bootstrap_confidence_interval), "
                f"{n_boot} resamples, block length by Politis-White (2004) per series; metric i draws from "
                f"default_rng([{seed}, i]). CAGR, drawdown and Sharpe resample the {out['n_daily_returns']} "
                f"daily (17:00 New York) returns of the research slice; profit factor and win rate its "
                f"{out['n_trades']} trades. The bands describe sampling uncertainty of THIS sample, not the "
                "selection the search made: they are not deflated."),
    )
    return Envelope(data=view, source=_note("live", f"Research ledger backtest {run.run_id}; resampled."),
                    caveats=_caveats(run, s))


# ================================================================ 5. breakdowns


class BucketView(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    trades: Figure
    net_pnl: Figure
    win_rate_pct: Figure
    expectancy: Figure
    profit_factor: Figure
    avg_bars_held: Figure


class BreakdownsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    account_ccy: str
    #: ``exit_reason``, ``direction``, ``year``, ``month``, ``weekday``, ``hour``, ``session``.
    groups: dict[str, list[BucketView]]
    entry_reason_note: str
    calendar_note: str
    boundary: HoldoutBoundaryView


@router.get("/runs/{run_id}/robustness/breakdowns", response_model=Envelope[BreakdownsView])
def breakdowns(run_id: str, settings: SettingsDep) -> Envelope[BreakdownsView]:
    run, record, s = _run_context(settings, run_id)
    ccy = _ccy(record)
    p = run.provenance
    groups = {
        name: [BucketView(
            key=b.key, trades=_count(b.n), net_pnl=_fig(b.net_pnl, p, ccy),
            win_rate_pct=_fig(b.win_rate_pct, p, "pct"), expectancy=_fig(b.expectancy, p, ccy),
            profit_factor=_fig(b.profit_factor, p, "ratio", reason="Undefined: no losing trade."),
            avg_bars_held=_fig(b.avg_bars_held, p, "bars", reason="No holding time recorded."),
        ) for b in buckets]
        for name, buckets in rl.breakdowns(s).items()
    }
    view = BreakdownsView(
        run_id=run.run_id, account_ccy=ccy, groups=groups,
        entry_reason_note=("The trade ledger records no entry rule (a signal's rationale is not stored), "
                           "so entries are broken down by DIRECTION, named as such; exit reasons are the "
                           "engine's own."),
        calendar_note=("Year, month, weekday, hour and session are of the ENTRY bar, in UTC; sessions are "
                       "marketstate/features.py session_flags (Tokyo 09-15, London 08-16:30, New York "
                       "08-17 local, overlaps named)."),
        boundary=_boundary_view(s),
    )
    return Envelope(data=view, source=_note("live", f"Research ledger backtest {run.run_id}, trade ledger."),
                    caveats=_caveats(run, s))


# ================================================================ 10. consistency


class SplitView(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    start: datetime | None
    end: datetime | None
    trades: Figure
    net_profit: Figure
    total_return_pct: Figure
    sharpe: Figure
    win_rate_pct: Figure
    profit_factor: Figure
    #: ``None`` for a split with no trade: it has no sign.
    positive: bool | None


class ConsistencyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    k: int
    periods: list[SplitView]
    instruments: list[SplitView]
    period_positive_share: Figure
    period_empty: Figure
    instrument_positive_share: Figure
    pooled: bool
    boundary: HoldoutBoundaryView
    method: str


def _split(sp: Any, p: Provenance, ccy: str) -> SplitView:
    m = sp.metrics
    return SplitView(
        key=sp.key, start=sp.start.to_pydatetime() if sp.start is not None else None,
        end=sp.end.to_pydatetime() if sp.end is not None else None,
        trades=_count(m.get("n_trades")), net_profit=_fig(m.get("net_profit"), p, ccy),
        total_return_pct=_fig(m.get("total_return_pct"), p, "pct", reason="Not computed for an instrument split."),
        sharpe=_fig(m.get("sharpe"), p, "ratio", reason="Undefined over this split's curve."),
        win_rate_pct=_fig(m.get("win_rate_pct"), p, "pct", reason="No trade in this split."),
        profit_factor=_fig(m.get("profit_factor"), p, "ratio", reason="Undefined: no losing trade."),
        positive=sp.positive,
    )


@router.get("/runs/{run_id}/robustness/consistency", response_model=Envelope[ConsistencyView])
def consistency(run_id: str, settings: SettingsDep, k: int = Query(4, ge=2, le=12)) -> Envelope[ConsistencyView]:
    run, record, s = _run_context(settings, run_id)
    ccy = _ccy(record)
    out = rl.consistency(s, k=k, initial=_initial(record))
    p = run.provenance
    view = ConsistencyView(
        run_id=run.run_id, k=k,
        periods=[_split(sp, p, ccy) for sp in out["periods"]],
        instruments=[_split(sp, p, ccy) for sp in out["instruments"]],
        period_positive_share=_fig(out["period_positive_share"], p, "ratio", sample=out["period_signed"],
                                   reason="No sub-period holds a trade."),
        period_empty=_count(out["period_empty"]),
        instrument_positive_share=_fig(out["instrument_positive_share"], p, "ratio",
                                       sample=out["instrument_signed"], reason="No instrument traded."),
        pooled=len(run.instruments) > 1, boundary=_boundary_view(s),
        method=(f"The research slice (holdout excluded) cut into {k} equal spans of time; a trade counts in "
                "the span it EXITED in. Instruments: every instrument the run traded (one for a single-"
                "instrument run, every sleeve of a pooled one). The share of positive splits ignores empty ones."),
    )
    return Envelope(data=view, source=_note("live", f"Research ledger backtest {run.run_id}."),
                    caveats=_caveats(run, s))


# ================================================================ 12. fills


class FillTable(BaseModel):
    """Per-trade fill rules and cost decomposition, as labelled COLUMNS."""

    model_config = ConfigDict(frozen=True)

    provenance: Provenance
    unit: str
    trade_id: list[str]
    instrument: list[str]
    direction: list[str]
    entry_time: list[datetime | None]
    exit_time: list[datetime | None]
    exit_reason: list[str]
    #: False: the row predates the engine's fill flags, so no rule is shown.
    recorded: list[bool]
    rules: list[list[str]]
    gross_pnl: list[float | None]
    spread_cost: list[float | None]
    slippage_cost: list[float | None]
    commission: list[float | None]
    financing_cost: list[float | None]
    net_pnl: list[float | None]


class FillDetailView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    intrabar_policy: str
    rule_counts: dict[str, Figure]
    rule_definitions: dict[str, str]
    trades_recorded: Figure
    trades_total: Figure
    table: FillTable
    boundary: HoldoutBoundaryView
    note: str


@router.get("/runs/{run_id}/robustness/fills", response_model=Envelope[FillDetailView])
def fills(run_id: str, settings: SettingsDep) -> Envelope[FillDetailView]:
    run, record, s = _run_context(settings, run_id)
    cfg = record.get("config_fingerprint") or {}
    policy = str(cfg.get("intrabar_policy") or "")
    out = rl.fill_detail(s, intrabar_policy=policy)
    rows = out["rows"]
    ccy = _ccy(record)

    def col(k: str) -> list[Any]:
        return [r[k] for r in rows]

    table = FillTable(
        provenance=run.provenance, unit=ccy, trade_id=col("trade_id"), instrument=col("instrument"),
        direction=col("direction"),
        entry_time=[t.to_pydatetime() if t is not None else None for t in col("entry_time")],
        exit_time=[t.to_pydatetime() if t is not None else None for t in col("exit_time")],
        exit_reason=col("exit_reason"), recorded=col("recorded"), rules=col("rules"),
        gross_pnl=col("gross_pnl"), spread_cost=col("spread_cost"), slippage_cost=col("slippage_cost"),
        commission=col("commission"), financing_cost=col("financing_cost"), net_pnl=col("net_pnl"),
    )
    n = len(rows)
    note = ("Fill rules are the flags the engine recorded at each fill (backtest/position.py): "
            f"intrabar policy {policy or 'not recorded'} decides a bar where the stop and a target were both "
            "touched. Costs are the ledger's own decomposition, in the account currency.")
    if out["recorded"] < n:
        note += (f" {n - out['recorded']} trade(s) were filed before the engine recorded fill flags: they show "
                 "their costs and no rule. Re-run to record them; nothing is re-simulated here.")
    view = FillDetailView(
        run_id=run.run_id, intrabar_policy=policy,
        rule_counts={k: _count(v) for k, v in out["counts"].items()}, rule_definitions=out["rules"],
        trades_recorded=_count(out["recorded"]), trades_total=_count(n), table=table,
        boundary=_boundary_view(s), note=note,
    )
    return Envelope(data=view, source=_note("live", f"Research ledger backtest {run.run_id}, trade ledger."),
                    caveats=_caveats(run, s))


# ================================================================ 9. rankings


class ObjectiveDefView(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    unit: str
    definition: str
    neutral: Figure


class RankingRowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    run: BacktestRunView
    trades: Figure
    raw: Figure
    score: Figure
    penalty_factor: Figure
    penalised: bool
    basis: str
    holdout_excluded: Figure


class RankingView(BaseModel):
    model_config = ConfigDict(frozen=True)

    objective: str
    objectives: list[ObjectiveDefView]
    min_trades: int
    rows: list[RankingRowView]
    unranked: list[str]
    penalty: str
    note: str


def _strategy_records(settings: Any, strategy_id: str | None, limit: int) -> list[tuple[BacktestRunView, dict[str, Any]]]:
    from fiboki.api.routers.chart import _run_view, _superseded, _verdicts
    from fiboki.api.routers.research_analytics import _SUMMARY_COLUMNS
    from fiboki.backtest.version import ENGINE_VERSION

    conn = _connect_ro(_ledger(settings))
    try:
        if not _has_artefacts(conn):
            return []
        where = "a.collection = 'backtests'"
        params: dict[str, Any] = {"limit": limit}
        if strategy_id:
            where += " AND a.strategy_id = :s"
            params["s"] = strategy_id
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLUMNS}, a.payload_json FROM research_artefact a WHERE {where} "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT :limit", params).fetchall()
        verdicts, superseded = _verdicts(conn), _superseded(conn)
    except sqlite3.Error as exc:
        raise _sqlite_error(exc) from exc
    finally:
        conn.close()
    return [(_run_view(r[:12], verdicts=verdicts, superseded=superseded, engine_version=ENGINE_VERSION),
             json.loads(r[-1])) for r in rows]


def _objectives_of(settings: Any, run: BacktestRunView, record: dict[str, Any]) -> tuple[dict[str, Any], ResearchSlice]:
    s = research_slice(record, _boundary(settings, run, record))
    sizes = _contract_sizes([str(t.get("instrument") or "") for t in s.trades] + list(run.instruments))
    return rl.objectives(s.trades, ts.equity_series(s.equity_curve), _initial(record), sizes), s


@router.get("/rankings", response_model=Envelope[RankingView])
def rankings(settings: SettingsDep, objective: str = Query("sharpe"), strategy_id: str | None = Query(None),
             limit: int = Query(100, ge=1, le=300)) -> Envelope[RankingView]:
    if objective not in rl.OBJECTIVES:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_objective",
                       f"objective must be one of {list(rl.OBJECTIVES)}.")
    spec = rl.OBJECTIVES[objective]
    defs = [ObjectiveDefView(key=o.key, label=o.label, unit=o.unit, definition=o.definition,
                             neutral=Figure(value=o.neutral, provenance=P, unit=o.unit))
            for o in rl.OBJECTIVES.values()]
    if _ledger_path(settings) is None:
        return Envelope(data=RankingView(objective=objective, objectives=defs, min_trades=rl.RANKING_MIN_TRADES,
                                         rows=[], unranked=[], penalty="", note="No experiment ledger."),
                        source=_note("absent", "No experiment ledger is configured (FIBOKI_EXPERIMENT_DB)."))
    scored: list[tuple[float, BacktestRunView, dict[str, Any], ResearchSlice, str, str]] = []
    unranked: list[str] = []
    for run, record in _strategy_records(settings, strategy_id, limit):
        try:
            objs, s = _objectives_of(settings, run, record)
        except ApiError:
            unranked.append(run.run_id)
            continue
        o = objs[objective]
        if o["score"] is None:
            unranked.append(run.run_id)
            continue
        scored.append((float(o["score"]), run, o, s, run.run_id, _ccy(record)))
    scored.sort(key=lambda x: (-x[0], x[4]))
    rows = []
    for i, (_score, run, o, s, _id, ccy) in enumerate(scored):
        unit = spec.unit if spec.unit != "account" else ccy
        n = len(s.trades)
        rows.append(RankingRowView(
            rank=i + 1, run=run, trades=_count(n),
            raw=_fig(o["raw"], run.provenance, unit, sample=n), score=_fig(o["score"], run.provenance, unit, sample=n),
            penalty_factor=_fig(o["penalty_factor"], run.provenance, "ratio"),
            penalised=n < rl.RANKING_MIN_TRADES and o["raw"] is not None and o["score"] != o["raw"],
            basis=str(o.get("basis") or ""), holdout_excluded=_count(s.trades_excluded),
        ))
    view = RankingView(
        objective=objective, objectives=defs, min_trades=rl.RANKING_MIN_TRADES, rows=rows, unranked=unranked,
        penalty=(f"Below {rl.RANKING_MIN_TRADES} trades the favourable part of every objective is shrunk "
                 f"towards its neutral value by n / {rl.RANKING_MIN_TRADES} (profit factor towards 1, the rest "
                 "towards 0); an unfavourable value is never shrunk, so the penalty cannot flatter a loser."),
        note=("Display and ranking only. The ladder ranks and promotes on its own versioned gate set "
              "(validation/gates.py); this ordering changes no verdict. Every objective is computed on the "
              "research slice of each run (holdout excluded), so it can differ from the stored headline."),
    )
    return Envelope(data=view, source=_note("live", "Research ledger, read-only."),
                    caveats=(Caveat(code="advisory_diagnostic", severity="info", message=ADVISORY,
                                    affects="ranking", direction="unknown"),))


# ================================================================ 8. optimisation profile


class GridPointView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    params: dict[str, Any]
    objective: Figure
    trades: Figure


class OptimisationProfileView(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    objective: str
    parameters: list[str]
    points: list[GridPointView]
    n_points: Figure
    n_undefined: Figure
    runs_unknown_params: Figure
    peak: Figure
    median: Figure
    p25: Figure
    p75: Figure
    profitable_share: Figure
    median_over_peak: Figure
    peak_params: dict[str, Any]
    method: str


@router.get("/strategies/{strategy_id}/optimisation-profile", response_model=Envelope[OptimisationProfileView])
def optimisation_profile(strategy_id: str, platform: PlatformDep, settings: SettingsDep,
                         objective: str = Query("net_profit")) -> Envelope[OptimisationProfileView]:
    doc = platform.strategy(strategy_id)
    if doc is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    if objective not in rl.OBJECTIVES:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_objective",
                       f"objective must be one of {list(rl.OBJECTIVES)}.")
    names = sorted(n for n, s in (getattr(doc, "parameters", {}) or {}).items()
                   if getattr(s, "kind", "") in ("int", "float"))
    records = _strategy_records(settings, strategy_id, 500) if _ledger_path(settings) is not None else []
    records = [(r, rec) for r, rec in records if r.kind != "walkforward_fold"]
    payloads = _job_payloads(settings, {str(rec.get("job_id") or "") for _, rec in records if rec.get("job_id")})
    seen: dict[str, tuple[BacktestRunView, dict[str, Any], dict[str, Any]]] = {}
    unknown = 0
    for run, rec in records:  # newest first: the first run of a parameter set is the latest
        payload = payloads.get(str(rec.get("job_id") or ""))
        if payload is None:
            unknown += 1
            continue
        params, _ = _run_parameters(platform, strategy_id, payload)
        key = json.dumps({n: params.get(n) for n in names}, sort_keys=True, default=str)
        seen.setdefault(key, (run, rec, {n: params.get(n) for n in names}))
    unit = rl.OBJECTIVES[objective].unit
    points: list[GridPointView] = []
    triples: list[tuple[dict[str, Any], float | None, int]] = []
    for run, rec, params in seen.values():
        try:
            objs, s = _objectives_of(settings, run, rec)
            value = objs[objective]["raw"]
        except ApiError:
            value, s = None, None
        n = len(s.trades) if s is not None else 0
        triples.append((params, value, n))
        points.append(GridPointView(run_id=run.run_id, params=params,
                                    objective=_fig(value, run.provenance, unit, sample=n, reason="Undefined."),
                                    trades=_count(n)))
    prof = rl.optimisation_profile(triples)
    # Best objective first, undefined last, run id as the tie-break: the order is the API's.
    points.sort(key=lambda g: (g.objective.value is None, -(g.objective.value or 0.0), g.run_id))

    def f(k: str, u: str = unit, reason: str = "No grid point has a defined objective.") -> Figure:
        return _fig(prof.get(k), P, u, sample=prof["n_defined"], reason=reason)

    view = OptimisationProfileView(
        strategy_id=strategy_id, objective=objective, parameters=names, points=points,
        n_points=_count(prof["n_points"]), n_undefined=_count(prof["n_undefined"]),
        runs_unknown_params=_count(unknown), peak=f("peak"), median=f("median"), p25=f("p25"), p75=f("p75"),
        profitable_share=f("profitable_share", "ratio"),
        median_over_peak=f("median_over_peak", "ratio", "Undefined: the peak is not positive."),
        peak_params=dict(prof.get("peak_params") or {}),
        method=("System Parameter Permutation (Walton 2014): every distinct parameter set with a stored run (the "
                "latest run of each), its objective on the research slice (holdout excluded). The MEDIAN is the "
                "expected performance of the system; the peak is what the search maximised. Runs whose job is "
                "not in the job ledger cannot be placed and are counted."),
    )
    return Envelope(data=view, source=_note("live" if points else "mixed",
                                            "Stored runs in the research ledger, parameters from the job ledger."),
                    caveats=(Caveat(code="advisory_diagnostic", severity="info", message=ADVISORY,
                                    affects="optimisation_profile", direction="unknown"),))


# ================================================================ re-run jobs


class RerunStatusView(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    title: str
    #: ``done``, ``pending``, ``running``, ``failed``, ``dead_letter`` or ``not_run``.
    status: str
    cache_key: str
    job_id: str
    error: str
    progress: Figure
    stage: str
    updated_at: datetime | None
    computed_at: datetime | None
    options: dict[str, Any]


class RobustnessIndexView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    rerunnable: bool
    reason: str
    binding: dict[str, Any]
    kinds: list[RerunStatusView]
    can_queue_note: str


class RerunProvenanceView(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_start: datetime | None
    window_end: datetime | None
    window_bars: Figure
    boundary: dict[str, Any]
    dataset_version_id: str
    engine: dict[str, Any]
    engine_runs: Figure
    covers_stored_run: bool
    #: ``None`` when the window does not cover the whole stored run (comparison impossible).
    reproduces_stored: bool | None


def _binding(settings: Any, platform: Any, run: BacktestRunView, record: dict[str, Any]) -> tuple[RunBinding | None, str]:
    if len(run.instruments) != 1:
        return None, ("Re-run diagnostics re-execute ONE binding on ONE instrument; this is a pooled run of "
                      f"{len(run.instruments)} instruments. Not supported: run them on a sleeve's own run.")
    payload = _job_payload(settings, str(record.get("job_id") or ""))
    if payload is None:
        return None, ("The run's job is not in the job ledger, so the parameter binding it executed with "
                      "cannot be read back; a re-run of a guessed binding would describe another strategy.")
    params, _ = _run_parameters(platform, run.strategy_id, payload)
    cfg = record.get("config_fingerprint") or {}
    profile = cfg.get("profile") if isinstance(cfg.get("profile"), dict) else {}
    sizing = cfg.get("sizing") if isinstance(cfg.get("sizing"), dict) else {}
    first, last = run_span(record)
    if first is None or last is None:
        return None, "The run records no bar span, so its research window is unknown."
    boundary = _boundary(settings, run, record)
    end = min(boundary.start, last + pd.Timedelta(microseconds=1))
    if end <= first:
        return None, "The run lies entirely inside the holdout: there is no research window to diagnose."
    construction = cfg.get("construction")
    return RunBinding(
        run_id=run.run_id, strategy_id=run.strategy_id, instrument=run.instruments[0], timeframe=run.timeframe,
        window_start=first.isoformat(), window_end=end.isoformat(),
        initial_balance=float(cfg.get("initial_balance") or _initial(record) or 10_000.0),
        risk_fraction=float(sizing.get("risk_fraction") or payload.get("risk_fraction") or 0.01),
        account_ccy=str(cfg.get("account_ccy") or payload.get("account_ccy") or "GBP"),
        profile_name=str(profile.get("name") or payload.get("profile") or "IG_REALISTIC").split("~")[0],
        parameters=params, construction="none" if construction in (None, "none") else "research",
        sizing_policy=str(sizing.get("policy_id") or "fixed_fractional_v2"),
        content_hash=str(record.get("content_hash") or ""), ledger_sha256=str(record.get("ledger_sha256") or ""),
        dataset_version_id=(list(run.dataset_version_ids) or [""])[0], run_last=last.isoformat(),
        boundary=boundary.to_dict(),
    ), ""


def _job_by_key(settings: Any, key: str) -> dict[str, Any] | None:
    path = _jobs_path(settings)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT job_id, status, error FROM job WHERE idempotency_key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return {"job_id": row[0], "status": row[1], "error": row[2] or ""} if row else None


def _parse(v: Any) -> datetime | None:
    t = ts._ts(v)
    return t.to_pydatetime() if t is not None else None


def _status(settings: Any, kind: str, binding: RunBinding | None, options: dict[str, Any]) -> tuple[RerunStatusView, dict[str, Any]]:
    opts = options_of(kind, options)
    if binding is None:
        return RerunStatusView(kind=kind, title=KIND_TITLES[kind], status="not_run", cache_key="", job_id="",
                               error="", progress=Figure.missing(P, unit="ratio", reason="Not re-runnable."),
                               stage="", updated_at=None, computed_at=None, options=opts), {}
    key = cache_key(kind, binding.to_payload(), opts)
    entry = read_entry(settings.paths.robustness_db, key)
    job = _job_by_key(settings, idempotency_key(key)) or {}
    prog = entry.get("progress") or {}
    if entry.get("result") is not None:
        state = "done"
    else:
        state = str(job.get("status") or "not_run")
        if state == "succeeded":  # the job ran but its result is not in this store
            state = "failed"
    total = int(prog.get("total") or 0)
    progress = (Figure(value=(1.0 if state == "done" else (prog.get("done", 0) / total)), provenance=P,
                       unit="ratio", sample_size=total or None)
                if (state == "done" or total) else Figure.missing(P, unit="ratio", reason="No progress reported yet."))
    return RerunStatusView(
        kind=kind, title=KIND_TITLES[kind], status=state, cache_key=key,
        job_id=str(job.get("job_id") or entry.get("job_id") or prog.get("job_id") or ""),
        error=str(job.get("error") or ""), progress=progress, stage=str(prog.get("stage") or ""),
        updated_at=_parse(prog.get("updated_at")), computed_at=_parse(entry.get("created_at")), options=opts,
    ), entry


def _can_queue_note() -> str:
    return ("Queueing a re-run diagnostic is admin-only and audited (POST). The research worker executes it; "
            "the API never runs the engine. Results are cached by the engine fingerprint, so the same "
            "diagnostic of the same run under the same engine is computed once.")


@router.get("/runs/{run_id}/robustness", response_model=Envelope[RobustnessIndexView])
def robustness_index(run_id: str, platform: PlatformDep, settings: SettingsDep) -> Envelope[RobustnessIndexView]:
    run, record, _ = _load_run(settings, run_id)
    binding, reason = _binding(settings, platform, run, record)
    kinds = [_status(settings, k, binding, {})[0] for k in RERUN_KINDS]
    view = RobustnessIndexView(run_id=run.run_id, rerunnable=binding is not None, reason=reason,
                               binding=binding.to_payload() if binding else {}, kinds=kinds,
                               can_queue_note=_can_queue_note())
    return Envelope(data=view, source=_note("live", "Robustness cache and job ledger, read-only."),
                    caveats=(Caveat(code="advisory_diagnostic", severity="info", message=ADVISORY,
                                    affects="robustness", direction="unknown"),))


class QueueRequest(BaseModel):
    reason: str = Field(min_length=4, max_length=500)
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/runs/{run_id}/robustness/{kind}", response_model=Envelope[RerunStatusView],
             status_code=status.HTTP_202_ACCEPTED)
def queue_robustness(run_id: str, kind: str, body: QueueRequest, platform: PlatformDep, settings: SettingsDep,
                     audit: AuditDep, principal: AdminPrincipal, request: Request) -> Envelope[RerunStatusView]:
    """Queue one re-run diagnostic as a research job. Admin-gated and audited: it consumes compute."""
    from fiboki.agents.orchestrator import JobSpec, JobType, Orchestrator
    from fiboki.research.robustness.job import robustness_handler

    if kind not in RERUN_KINDS:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_diagnostic", f"kind must be one of {list(RERUN_KINDS)}.")
    run, record, _ = _load_run(settings, run_id)
    binding, reason = _binding(settings, platform, run, record)
    if binding is None:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "not_rerunnable", reason)
    try:
        opts = options_of(kind, body.options)
    except ValueError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_options", str(exc)) from exc
    key = cache_key(kind, binding.to_payload(), opts)
    audit.record(
        "research.robustness.queue", actor=principal.user_id, actor_role=principal.role.value,
        outcome="allowed", reason=body.reason, target=f"{run.run_id}:{kind}",
        execution_mode=settings.execution_mode.value, correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "", detail={"cache_key": key, "options": opts},
    )
    if read_entry(settings.paths.robustness_db, key).get("result") is None:
        orch = Orchestrator(path=_jobs_path(settings))
        try:
            # Registered ONLY so the durable ledger accepts the job; this process never
            # claims or runs one (it calls no run_next). The research worker executes it.
            orch.register_handler(JobType.ROBUSTNESS, robustness_handler)
            orch.submit(JobSpec(
                job_type=JobType.ROBUSTNESS, queue="research", idempotency_key=idempotency_key(key),
                submitted_by=principal.user_id, role="operator", trigger="operator", max_attempts=2,
                payload={"kind": kind, "binding": binding.to_payload(), "options": opts, "cache_key": key,
                         "strategy_id": run.strategy_id, "instrument": binding.instrument},
            ))
        finally:
            orch.close()
    view, _ = _status(settings, kind, binding, opts)
    return Envelope(data=view, source=_note("live", "Job ledger (queued) and robustness cache."))


# -- per-kind result views -----------------------------------------------------------------


class IndicatorDriftView(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    max_abs_diff: Figure
    max_rel_diff: Figure
    bars_differing: Figure
    last_differing_bar: datetime | None


class WarmupRunView(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: ``extended`` (N earlier bars prepended) or ``late_start`` (the short run starts N bars later).
    mode: str
    compared_from: datetime | None
    extra_bars: Figure
    signals_compared: Figure
    signals_changed_after_warmup: Figure
    first_changed_signal: datetime | None
    signals_changed_in_cold_region: Figure
    indicator_columns: list[IndicatorDriftView]
    trades_short: Figure
    trades_long: Figure
    trades_matched: Figure
    trades_only_short: Figure
    trades_only_long: Figure
    trades_changed: Figure
    metrics: RunMetricsView
    indicators_converged: bool
    stable: bool


class WarmupView(BaseModel):
    model_config = ConfigDict(frozen=True)

    warmup_period: Figure
    prologue_available: Figure
    stable: bool
    baseline: RerunBriefView
    runs: list[WarmupRunView]


class LookaheadFlagView(BaseModel):
    model_config = ConfigDict(frozen=True)

    bar: datetime
    signal_full: str | None
    signal_truncated: str | None
    signal_changed: bool
    columns_changed: list[str]


class LookaheadView(BaseModel):
    model_config = ConfigDict(frozen=True)

    samples: Figure
    seed: int
    signals_at_samples: Figure
    columns_checked: list[str]
    flags: list[LookaheadFlagView]
    biased: bool
    signal_flags: Figure


class StressRowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    stress: str
    level: str
    runs: Figure
    profile: str
    net_profit: DistributionView
    sharpe: DistributionView
    max_drawdown_pct: DistributionView
    trades: DistributionView
    retention: Figure
    profitable_share: Figure


class StressCellView(BaseModel):
    model_config = ConfigDict(frozen=True)

    spread_x: str
    slippage_x: str
    profile: str
    metrics: RunMetricsView
    retention: Figure


class ExecutionStressView(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline: RerunBriefView
    rows: list[StressRowView]
    grid: list[StressCellView]
    multipliers: list[str]
    seed: int
    notes: list[str]


class PerturbationDrawView(BaseModel):
    model_config = ConfigDict(frozen=True)

    params: dict[str, Any]
    valid: bool
    error: str
    net_profit: Figure
    sharpe: Figure
    trades: Figure


class PerturbationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    jitter_pct: Figure
    seed: int
    parameters: list[str]
    baseline: RerunBriefView
    draws: list[PerturbationDrawView]
    invalid: Figure
    net_profit: DistributionView
    sharpe: DistributionView
    profitable_share: Figure
    baseline_percentile: Figure


class RandomBenchmarkView(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: Figure
    seed: int
    trades: Figure
    strategy_total_return: Figure
    strategy_mean_return: Figure
    random_total_return: DistributionView
    percentile: Figure
    holding_bars: DistributionView
    long_share: Figure
    baseline: RerunBriefView


class NoiseRowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    atr_fraction: str
    runs: Figure
    net_profit: DistributionView
    sharpe: DistributionView
    trades: DistributionView
    degradation: Figure
    profitable_share: Figure


class NoiseView(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline: RerunBriefView
    rows: list[NoiseRowView]
    seed: int
    atr_period: Figure


class RerunResultView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    status: RerunStatusView
    provenance: RerunProvenanceView | None
    method: str
    warmup: WarmupView | None = None
    lookahead: LookaheadView | None = None
    execution: ExecutionStressView | None = None
    perturbation: PerturbationView | None = None
    random: RandomBenchmarkView | None = None
    noise: NoiseView | None = None


METHODS = {
    "warmup": ("The binding re-run through the engine (validation/engine_evaluator.py) with N bars more and N "
               "bars less history: N earlier bars prepended when they exist (extended), otherwise a second run "
               "starting cold N bars later (late start). Signals, indicator columns and trades are compared "
               "over the shorter run after its own declared warm-up; a change there means a recursive "
               "indicator has not converged by the declared warm-up (Freqtrade recursive-analysis)."),
    "lookahead": ("Signals and indicator columns at seeded sampled bars, recomputed on data truncated at that "
                  "bar, compared with the full-data values. Any difference is look-ahead (Freqtrade "
                  "lookahead-analysis)."),
    "execution": ("Every cell is a real engine run under a stressed copy of the run's own cost profile "
                  "(sim/stress_profiles.py): broker rejects at the skip rate under several seeds, latency_bars "
                  "for an entry delay, and every spread and slippage parameter scaled. Sizing prices the "
                  "stressed costs too. Retention: median net profit over the baseline's."),
    "perturbation": ("Every numeric parameter multiplied by a seeded uniform factor in [1 - k%, 1 + k%], "
                     "clamped to its declared domain, re-run through the engine. Objective: net profit."),
    "random": ("Random-entry systems with the strategy's trade count, holding-time distribution and direction "
               "mix; every trade (the strategy's too) measured as the mid-to-mid move less the profile's "
               "expected round-trip friction. Sizing is left out of both sides."),
    "noise": ("OHLC perturbed by seeded uniform noise of +/- x ATR(14) of the previous bar, bars made valid "
              "again (high >= max(open, close), low <= min(open, close)), re-run through the engine."),
}


def _result_view(kind: str, result: dict[str, Any], ccy: str) -> dict[str, Any]:
    out = result.get("output") or {}
    base = _brief(out.get("baseline"), ccy)
    if kind == "warmup":
        runs = [WarmupRunView(
            mode=str(r.get("mode") or ""), compared_from=_parse(r.get("compared_from")),
            extra_bars=_count(r.get("extra_bars")), signals_compared=_count(r.get("signals_compared")),
            signals_changed_after_warmup=_count(r.get("signals_changed_after_warmup")),
            first_changed_signal=_parse(r.get("first_changed_signal")),
            signals_changed_in_cold_region=_count(r.get("signals_changed_in_cold_region")),
            indicator_columns=[IndicatorDriftView(
                column=str(c["column"]), max_abs_diff=_fig(c.get("max_abs_diff"), P, "price"),
                max_rel_diff=_fig(c.get("max_rel_diff"), P, "ratio"), bars_differing=_count(c.get("bars_differing")),
                last_differing_bar=_parse(c.get("last_differing_bar"))) for c in r.get("indicator_columns") or []],
            trades_short=_count(r.get("trades_short")), trades_long=_count(r.get("trades_long")),
            trades_matched=_count(r.get("trades_matched")), trades_only_short=_count(r.get("trades_only_short")),
            trades_only_long=_count(r.get("trades_only_long")), trades_changed=_count(r.get("trades_changed")),
            metrics=_metrics(r.get("metrics"), ccy), indicators_converged=bool(r.get("indicators_converged")),
            stable=bool(r.get("stable")),
        ) for r in out.get("runs") or []]
        return {"warmup": WarmupView(warmup_period=_fig(out.get("warmup_period"), P, "bars"),
                                     prologue_available=_fig(out.get("prologue_available"), P, "bars"),
                                     stable=bool(out.get("stable")), baseline=base, runs=runs)}
    if kind == "lookahead":
        return {"lookahead": LookaheadView(
            samples=_count(out.get("samples")), seed=int(out.get("seed") or 0),
            signals_at_samples=_count(out.get("signals_at_samples")),
            columns_checked=[str(c) for c in out.get("columns_checked") or []],
            flags=[LookaheadFlagView(bar=_parse(f["bar"]) or datetime.now(tz=UTC), signal_full=f.get("signal_full"),
                                     signal_truncated=f.get("signal_truncated"),
                                     signal_changed=bool(f.get("signal_changed")),
                                     columns_changed=[str(c) for c in f.get("columns_changed") or []])
                   for f in out.get("flags") or []],
            biased=bool(out.get("biased")), signal_flags=_count(out.get("signal_flags")))}
    if kind == "execution":
        rows = [StressRowView(
            stress=str(r["stress"]),
            level=(f"{float(r['level']):.0%}" if r["stress"] == "trade_skip" else f"{int(r['level'])} bar(s)"),
            runs=_count(r.get("runs")), profile=str(r.get("profile") or ""),
            net_profit=_dist(r.get("net_profit"), ccy), sharpe=_dist(r.get("sharpe"), "ratio"),
            max_drawdown_pct=_dist(r.get("max_drawdown_pct"), "pct"), trades=_dist(r.get("n_trades"), "count"),
            retention=_ratio(r.get("retention"), "Undefined: the baseline's net profit is zero."),
            profitable_share=_ratio(r.get("profitable_share"), "No runs."),
        ) for r in out.get("rows") or []]
        grid = [StressCellView(spread_x=f"{float(c['spread_x']):g}x", slippage_x=f"{float(c['slippage_x']):g}x",
                               profile=str(c.get("profile") or ""), metrics=_metrics(c.get("metrics"), ccy, estimated=True),
                               retention=_ratio(c.get("retention"), "Undefined: the baseline's net profit is zero."))
                for c in out.get("grid") or []]
        return {"execution": ExecutionStressView(
            baseline=base, rows=rows, grid=grid, multipliers=[f"{float(m):g}x" for m in out.get("multipliers") or []],
            seed=int(out.get("seed") or 0), notes=[str(n) for n in out.get("notes") or []])}
    if kind == "perturbation":
        return {"perturbation": PerturbationView(
            jitter_pct=_fig(out.get("pct"), P, "pct"), seed=int(out.get("seed") or 0),
            parameters=[str(p) for p in out.get("parameters") or []], baseline=base,
            draws=[PerturbationDrawView(params=dict(d.get("params") or {}), valid=bool(d.get("valid")),
                                        error=str(d.get("error") or ""),
                                        net_profit=_fig(d.get("net_profit"), P, ccy, reason="Invalid draw.", estimated=True),
                                        sharpe=_fig(d.get("sharpe"), P, "ratio", reason="Undefined.", estimated=True),
                                        trades=_count(d.get("n_trades"), "Invalid draw."))
                   for d in out.get("draws") or []],
            invalid=_count(out.get("invalid")), net_profit=_dist(out.get("net_profit"), ccy),
            sharpe=_dist(out.get("sharpe"), "ratio"),
            profitable_share=_ratio(out.get("profitable_share"), "No valid draw."),
            baseline_percentile=_ratio(out.get("baseline_percentile"), "No valid draw.", "pct"))}
    if kind == "random":
        return {"random": RandomBenchmarkView(
            runs=_count(out.get("n_runs")), seed=int(out.get("seed") or 0), trades=_count(out.get("n_trades")),
            strategy_total_return=_fig(out.get("strategy_total_return"), P, "fraction"),
            strategy_mean_return=_fig(out.get("strategy_mean_return"), P, "fraction"),
            random_total_return=_dist(out.get("random"), "fraction"),
            percentile=_ratio(out.get("percentile"), "No random runs.", "pct"),
            holding_bars=_dist(out.get("holding_bars"), "bars"),
            long_share=_fig(out.get("long_share"), P, "ratio"), baseline=base)}
    if kind == "noise":
        return {"noise": NoiseView(baseline=base, seed=int(out.get("seed") or 0),
                                   atr_period=_fig(out.get("atr_period"), P, "bars"),
                                   rows=[NoiseRowView(
                                       atr_fraction=f"{float(r['atr_fraction']):g} ATR", runs=_count(r.get("runs")),
                                       net_profit=_dist(r.get("net_profit"), ccy), sharpe=_dist(r.get("sharpe"), "ratio"),
                                       trades=_dist(r.get("n_trades"), "count"),
                                       degradation=_ratio(r.get("degradation"), "Undefined: the baseline's net profit is zero."),
                                       profitable_share=_ratio(r.get("profitable_share"), "No runs."))
                                       for r in out.get("rows") or []])}
    raise AssertionError(kind)  # pragma: no cover


@router.get("/runs/{run_id}/robustness/{kind}", response_model=Envelope[RerunResultView])
def robustness_result(run_id: str, kind: str, platform: PlatformDep, settings: SettingsDep,
                      options: str | None = Query(None, description="JSON options, as queued.")) -> Envelope[RerunResultView]:
    if kind not in RERUN_KINDS:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_diagnostic", f"kind must be one of {list(RERUN_KINDS)}.")
    try:
        opts = json.loads(options) if options else {}
        if not isinstance(opts, dict):
            raise ValueError("options must be a JSON object")
        options_of(kind, opts)
    except ValueError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_options", str(exc)) from exc
    run, record, _ = _load_run(settings, run_id)
    binding, reason = _binding(settings, platform, run, record)
    st, entry = _status(settings, kind, binding, opts)
    result = entry.get("result")
    prov = None
    extra: dict[str, Any] = {}
    if result is not None:
        w = result.get("window") or {}
        prov = RerunProvenanceView(
            window_start=_parse(w.get("start")), window_end=_parse(w.get("end")),
            window_bars=_fig(w.get("bars"), P, "bars"), boundary=dict(result.get("boundary") or {}),
            dataset_version_id=str(result.get("dataset_version_id") or ""), engine=dict(result.get("engine") or {}),
            engine_runs=_count(result.get("n_engine_runs")), covers_stored_run=bool(result.get("covers_stored_run")),
            reproduces_stored=result.get("reproduces_stored"),
        )
        extra = _result_view(kind, result, _ccy(record))
    caveats = [Caveat(code="advisory_diagnostic", severity="info", message=ADVISORY, affects="robustness",
                      direction="unknown")]
    if binding is None:
        caveats.append(Caveat(code="not_rerunnable", severity="warning", message=reason, affects="robustness",
                              direction="unknown"))
    if prov is not None and prov.reproduces_stored is False:
        caveats.append(Caveat(code="baseline_does_not_reproduce", severity="warning",
                              message="The baseline re-run's ledger hash differs from the stored run's: the "
                              "engine or data moved since the run was filed. Deltas are against the baseline.",
                              affects="robustness", direction="unknown"))
    view = RerunResultView(run_id=run.run_id, status=st, provenance=prov, method=METHODS[kind], **extra)
    kind_note = "live" if result is not None else "absent"
    return Envelope(data=view, source=_note(kind_note, "Robustness cache (written by the research worker), "
                                            "read-only." if result is not None else
                                            "Not computed yet: queue it, and the research worker runs it."),
                    caveats=tuple(caveats))

