"""CHART: backtest trades over the price chart, and operator drawings.

Two things the chart workstation needs that ``routers/markets.py`` does not
serve:

``GET /api/markets/backtests/{symbol}``
    The backtest and walk-forward-fold runs the research ledger holds for a
    symbol (and timeframe): id, strategy, when, the deterministic gate verdict
    filed against it, trade counts, whether it is from the current engine.
``GET /api/markets/backtests/{symbol}/{run_id}/trades``
    That run's trades inside the chart's window, each with its entry, exit,
    direction, size, exit reason, net P&L in the account currency and the stop
    and targets the ENGINE recorded (``core.contracts.Trade.initial_stop`` and
    friends, stamped at the ledger-writing boundary since 2026-10-18). A run
    filed before that has no stops; each trade then says so.
``GET / PUT / DELETE /api/markets/drawings/{symbol}``
    The signed-in operator's drawings for a symbol (``api/drawings.py``), with
    optimistic versioning. Writes need the operator role, CSRF and an
    allow-listed Origin, and are audited; viewers read.

The research ledger is opened READ-ONLY (``mode=ro``): ``ResearchStore()``
runs DDL on open, and a chart read must not write to the ledger. Nothing here
computes a trade, a P&L, a stop or a verdict; every number is the stored one.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AnyPrincipal, AuditDep, SettingsDep
from fiboki.api.drawings import (
    MAX_DRAWINGS,
    Drawing,
    DrawingConflict,
    DrawingStore,
    StoredSet,
)
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure, charged_cost_components
from fiboki.api.provenance import figure as make_figure
from fiboki.api.security import Principal, Role, require_operator
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Provenance, Timeframe
from fiboki.core.paths import resolve_paths

router = APIRouter(prefix="/api/markets", tags=["markets"])

OperatorPrincipal = Annotated[Principal, Depends(require_operator)]

#: Trades served per request. A run with more trades in the window is cut to
#: the latest this many and says so.
MAX_TRADES = 2_000
SUPERSEDED_TAG = "superseded_by_engine_version"


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


def _known_symbol(symbol: str) -> str:
    if not instrument_registry.exists(symbol):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_instrument",
            f"{symbol!r} is not in the instrument registry.",
        )
    return instrument_registry.get(symbol).symbol


def _timeframe(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return Timeframe(value).value
    except ValueError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "unknown_timeframe",
            f"{value!r} is not a recognised timeframe.",
        ) from exc


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# ================================================================ backtests


class BacktestRunView(BaseModel):
    """One stored backtest (or walk-forward fold) run, as the ledger holds it."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    strategy_id: str
    #: ``backtest`` or ``walkforward_fold`` (from the record's label, which the
    #: research worker writes as ``walkforward_fold_<n>`` for a fold).
    kind: str
    label: str
    created_at: datetime | None
    timeframe: str
    instruments: list[str]
    dataset_version_ids: list[str]
    engine_version: str
    #: False when the run predates the current engine generation
    #: (``backtest/version.py``): its numbers are not comparable with a new run.
    engine_current: bool
    #: True when a supersession note points at this run.
    superseded: bool
    #: The latest deterministic validation verdict filed against THIS run
    #: (``pass`` / ``fail`` / ``inconclusive``), or ``not_evaluated``.
    gate_verdict: str
    gate_report_id: str | None
    trade_count: Figure
    trades_on_symbol: Figure
    #: Trades on this symbol whose stop the engine recorded.
    trades_with_stops: Figure
    provenance: Provenance


class BacktestTradeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    strategy_id: str
    direction: str = Field(pattern="^(long|short)$")
    size: Figure
    entry_time: datetime
    exit_time: datetime
    entry_price: Figure
    exit_price: Figure
    exit_reason: str
    net_pnl: Figure
    gross_pnl: Figure
    costs: Figure
    bars_held: Figure
    #: The protective stop at entry, as the engine placed it.
    stop_loss: Figure
    #: The stop when the trade closed (moved by breakeven or a trail when
    #: ``stop_moved_by`` says so). The engine does not record WHEN it moved.
    final_stop: Figure
    #: The first planned take-profit; ``take_profit_targets`` holds all legs.
    take_profit: Figure
    take_profit_targets: list[Figure]
    stop_moved_by: str | None
    provenance: Provenance


class BacktestTradesView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str | None
    window_from: datetime | None
    window_to: datetime | None
    run: BacktestRunView
    trades: list[BacktestTradeView]
    #: Trades on this symbol in the run, inside and outside the window.
    total_on_symbol: Figure
    outside_window: Figure
    #: Why stops or targets are missing, when they are.
    stops_detail: str
    account_ccy: str | None


def _ledger_path(settings: Any) -> Path | None:
    path = getattr(settings, "experiment_db", None)
    return Path(path) if path is not None else None


def _unprovisioned() -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "experiment_ledger_not_configured",
        "No experiment ledger is configured (FIBOKI_EXPERIMENT_DB), so no stored "
        "backtest can be read. This is a missing source, not a symbol with no runs.",
    )


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "experiment_ledger_unavailable",
            f"The experiment ledger at {path} does not exist.",
        )
    try:
        return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "experiment_ledger_unavailable",
            "The experiment ledger could not be opened read-only.",
            context={"error_class": type(exc).__name__},
        ) from exc


def _has_artefacts(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_artefact'"
        ).fetchone()
        is not None
    )


def _verdicts(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """``backtest_id -> (verdict, report_id)``, the latest report per run."""
    out: dict[str, tuple[str, str]] = {}
    rows = conn.execute(
        "SELECT id, json_extract(payload_json, '$.backtest_id'), "
        "json_extract(payload_json, '$.verdict') FROM research_artefact "
        "WHERE collection = 'validation_reports' ORDER BY created_at, id"
    ).fetchall()
    for report_id, backtest_id, verdict in rows:
        if backtest_id:
            out[str(backtest_id)] = (str(verdict or "inconclusive"), str(report_id))
    return out


def _superseded(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT json_extract(payload_json, '$.supersedes') FROM research_artefact a "
        "WHERE collection = 'notes' AND json_extract(payload_json, '$.supersedes') IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM json_each(a.payload_json, '$.tags') WHERE value = ?)",
        (SUPERSEDED_TAG,),
    ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


_RUN_COLUMNS = (
    "a.id, a.created_at, a.strategy_id, "
    "json_extract(a.payload_json, '$.label'), "
    "json_extract(a.payload_json, '$.timeframe'), "
    "json_extract(a.payload_json, '$.instruments'), "
    "json_extract(a.payload_json, '$.dataset_version_ids'), "
    "json_extract(a.payload_json, '$.engine_version'), "
    "json_array_length(a.payload_json, '$.trades'), "
    "(SELECT count(*) FROM json_each(a.payload_json, '$.trades') t "
    " WHERE upper(json_extract(t.value, '$.instrument')) = :sym), "
    "(SELECT count(*) FROM json_each(a.payload_json, '$.trades') t "
    " WHERE upper(json_extract(t.value, '$.instrument')) = :sym "
    " AND json_type(t.value, '$.initial_stop') IN ('real', 'integer')), "
    "(SELECT group_concat(DISTINCT json_extract(t.value, '$.provenance')) "
    " FROM json_each(a.payload_json, '$.trades') t)"
)


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return _utc(parsed)


def _run_view(
    row: tuple[Any, ...],
    *,
    verdicts: dict[str, tuple[str, str]],
    superseded: set[str],
    engine_version: str,
) -> BacktestRunView:
    (run_id, created_at, strategy_id, label, timeframe, instruments, datasets, engine,
     n_trades, on_symbol, with_stops, provenances) = row
    label = str(label or "")
    kinds = {p for p in str(provenances or "").split(",") if p}
    provenance = Provenance.BACKTEST
    if len(kinds) == 1:
        try:
            provenance = Provenance(next(iter(kinds)))
        except ValueError:
            provenance = Provenance.BACKTEST
    verdict = verdicts.get(str(run_id))
    count = lambda n: Figure(  # noqa: E731 - three uses, one shape
        value=float(n or 0), provenance=provenance, unit="count"
    )
    return BacktestRunView(
        run_id=str(run_id),
        strategy_id=str(strategy_id or ""),
        kind="walkforward_fold" if label.startswith("walkforward_fold") else "backtest",
        label=label,
        created_at=_parse_time(created_at),
        timeframe=str(timeframe or ""),
        instruments=[str(i) for i in json.loads(instruments or "[]")],
        dataset_version_ids=[str(d) for d in json.loads(datasets or "[]")],
        engine_version=str(engine or ""),
        engine_current=bool(engine) and str(engine) == engine_version,
        superseded=str(run_id) in superseded,
        gate_verdict=verdict[0] if verdict else "not_evaluated",
        gate_report_id=verdict[1] if verdict else None,
        trade_count=count(n_trades),
        trades_on_symbol=count(on_symbol),
        trades_with_stops=count(with_stops),
        provenance=provenance,
    )


def _run_caveats(runs: list[BacktestRunView]) -> list[Caveat]:
    caveats: list[Caveat] = []
    stale = [r.run_id for r in runs if not r.engine_current or r.superseded]
    if stale:
        caveats.append(
            Caveat(
                code="backtest_engine_stale",
                severity="warning",
                message=f"{len(stale)} run(s) are from an older engine generation or "
                "superseded; their trades are drawn as stored and are not comparable "
                "with a current run.",
                affects="backtest_trades",
                direction="unknown",
            )
        )
    no_stops = [r.run_id for r in runs if (r.trades_with_stops.value or 0) == 0
                and (r.trades_on_symbol.value or 0) > 0]
    if no_stops:
        caveats.append(
            Caveat(
                code="backtest_stops_not_recorded",
                severity="info",
                message=f"{len(no_stops)} run(s) were filed before the engine recorded "
                "per-trade stops and targets; their trades are drawn without them.",
                affects="stop_loss",
                direction="unknown",
            )
        )
    return caveats


@router.get("/backtests/{symbol}", response_model=Page[BacktestRunView])
def backtest_runs(
    symbol: str,
    settings: SettingsDep,
    timeframe: str | None = Query(None),
    strategy_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1_000),
) -> Page[BacktestRunView]:
    """Stored runs that traded ``symbol``, newest first. Read-only."""
    from fiboki.backtest.version import ENGINE_VERSION

    sym = _known_symbol(symbol)
    tf = _timeframe(timeframe)
    path = _ledger_path(settings)
    if path is None:
        return Page(
            items=[],
            total=0,
            limit=limit,
            source=_note(
                "absent",
                "No experiment ledger is configured (FIBOKI_EXPERIMENT_DB). This is an "
                "EMPTY SOURCE, not a symbol nobody has backtested.",
            ),
            caveats=(
                Caveat(
                    code="source_not_configured",
                    severity="warning",
                    message="The research ledger is not attached to this deployment.",
                    direction="unknown",
                ),
            ),
        )
    conn = _connect_ro(path)
    try:
        if not _has_artefacts(conn):
            return Page(
                items=[],
                total=0,
                limit=limit,
                source=_note("live", f"Research ledger {path}: no artefact table yet."),
            )
        clauses = ["a.collection = 'backtests'",
                   "EXISTS (SELECT 1 FROM json_each(a.payload_json, '$.instruments') i "
                   "WHERE upper(i.value) = :sym)"]
        params: dict[str, Any] = {"sym": sym.upper(), "limit": limit}
        if tf is not None:
            clauses.append("upper(json_extract(a.payload_json, '$.timeframe')) = :tf")
            params["tf"] = tf
        if strategy_id:
            clauses.append("a.strategy_id = :strategy")
            params["strategy"] = strategy_id
        where = " AND ".join(clauses)
        total = conn.execute(
            f"SELECT count(*) FROM research_artefact a WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM research_artefact a WHERE {where} "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT :limit",
            params,
        ).fetchall()
        verdicts = _verdicts(conn)
        superseded = _superseded(conn)
    except sqlite3.Error as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "experiment_ledger_unavailable",
            "The research ledger could not be read. Not 'no runs'.",
            context={"error_class": type(exc).__name__},
        ) from exc
    finally:
        conn.close()
    items = [
        _run_view(r, verdicts=verdicts, superseded=superseded, engine_version=ENGINE_VERSION)
        for r in rows
    ]
    return Page(
        items=items,
        total=int(total),
        limit=limit,
        source=_note(
            "live",
            f"Research ledger (append-only), read-only: stored backtest records for {sym}"
            + (f" {tf}" if tf else "")
            + f". Current engine {ENGINE_VERSION}.",
        ),
        caveats=tuple(_run_caveats(items)),
    )


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _trade_view(
    row: dict[str, Any],
    *,
    provenance: Provenance,
    settings: Any,
    run_stops_note: str,
) -> BacktestTradeView | None:
    entry = _parse_time(row.get("entry_time"))
    exit_ = _parse_time(row.get("exit_time"))
    direction = str(row.get("direction") or "")
    if entry is None or exit_ is None or direction not in ("long", "short"):
        return None
    try:
        p = Provenance(str(row.get("provenance") or provenance.value))
    except ValueError:
        p = provenance
    ccy = str(row.get("account_ccy") or "")
    charged = charged_cost_components(row)

    def price(key: str, missing: str) -> Figure:
        value = _num(row.get(key))
        return (
            Figure(value=value, provenance=p)
            if value is not None
            else Figure.missing(p, reason=missing)
        )

    costs_parts = [_num(row.get(k)) for k in
                   ("spread_cost", "commission", "slippage_cost", "financing_cost")]
    known_costs = [c for c in costs_parts if c is not None]
    targets = [t for t in (row.get("take_profit_targets") or []) if _num(t) is not None]
    has_target_key = "take_profit_targets" in row
    target_missing = (
        "The engine planned no take-profit for this trade."
        if has_target_key
        else run_stops_note
    )
    moved = row.get("stop_moved_by")
    return BacktestTradeView(
        trade_id=str(row.get("trade_id") or ""),
        strategy_id=str(row.get("strategy_id") or ""),
        direction=direction,
        size=Figure(value=_num(row.get("size")), provenance=p, unit="lots"),
        entry_time=entry,
        exit_time=exit_,
        entry_price=price("entry_price", "No entry price was stored."),
        exit_price=price("exit_price", "No exit price was stored."),
        exit_reason=str(row.get("exit_reason") or ""),
        net_pnl=make_figure(
            _num(row.get("net_pnl")), p, settings=settings, unit=ccy,
            affects="net_pnl", charged=charged,
        ),
        gross_pnl=Figure(value=_num(row.get("gross_pnl")), provenance=p, unit=ccy),
        costs=(
            Figure(value=sum(known_costs), provenance=p, unit=ccy)
            if known_costs
            else Figure.missing(p, unit=ccy, reason="No cost components were stored.")
        ),
        bars_held=(
            Figure(value=float(row["bars_held"]), provenance=p, unit="bars")
            if isinstance(row.get("bars_held"), int)
            else Figure.missing(p, unit="bars", reason="Bars held was not stored.")
        ),
        stop_loss=price("initial_stop", run_stops_note),
        final_stop=price("final_stop", run_stops_note),
        take_profit=(
            Figure(value=float(targets[0]), provenance=p)
            if targets
            else Figure.missing(p, reason=target_missing)
        ),
        take_profit_targets=[Figure(value=float(t), provenance=p) for t in targets],
        stop_moved_by=str(moved) if moved else None,
        provenance=p,
    )


@router.get("/backtests/{symbol}/{run_id}/trades", response_model=Envelope[BacktestTradesView])
def backtest_trades(
    symbol: str,
    run_id: str,
    settings: SettingsDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    timeframe: str | None = Query(None),
) -> Envelope[BacktestTradesView]:
    """One run's trades on ``symbol`` whose life overlaps ``[from, to]``.

    ``from``/``to`` are the chart's first and last bar; omitted, every trade on
    the symbol is served (up to :data:`MAX_TRADES`, the latest kept).
    """
    from fiboki.backtest.version import ENGINE_VERSION

    sym = _known_symbol(symbol)
    tf = _timeframe(timeframe)
    lo, hi = _utc(from_), _utc(to)
    if lo is not None and hi is not None and lo > hi:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_window", "'from' is after 'to'.")
    path = _ledger_path(settings)
    if path is None:
        raise _unprovisioned()
    conn = _connect_ro(path)
    try:
        if not _has_artefacts(conn):
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "unknown_backtest", f"No backtest {run_id!r}."
            )
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS}, a.payload_json FROM research_artefact a "
            "WHERE a.collection = 'backtests' AND a.id = :id",
            {"sym": sym.upper(), "id": run_id},
        ).fetchone()
        if row is None:
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                "unknown_backtest",
                f"The research ledger holds no backtest {run_id!r}.",
            )
        verdicts = _verdicts(conn)
        superseded = _superseded(conn)
    except sqlite3.Error as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "experiment_ledger_unavailable",
            "The research ledger could not be read.",
            context={"error_class": type(exc).__name__},
        ) from exc
    finally:
        conn.close()

    run = _run_view(
        row[:-1], verdicts=verdicts, superseded=superseded, engine_version=ENGINE_VERSION
    )
    if sym.upper() not in {i.upper() for i in run.instruments}:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "backtest_not_on_symbol",
            f"Backtest {run_id} did not trade {sym} (it traded {', '.join(run.instruments)}).",
        )
    record = json.loads(row[-1])
    rows = [
        t for t in record.get("trades") or []
        if isinstance(t, dict) and str(t.get("instrument") or "").upper() == sym.upper()
    ]
    stamped = any("initial_stop" in t for t in rows)
    stops_note = (
        "The engine recorded no stop for this trade."
        if stamped
        else f"Run {run.run_id} was filed before the engine recorded per-trade stops and "
        "targets (2026-10-18); re-run it to see them. Nothing is estimated."
    )
    views = [
        v
        for v in (
            _trade_view(t, provenance=run.provenance, settings=settings, run_stops_note=stops_note)
            for t in rows
        )
        if v is not None
    ]
    unreadable = len(rows) - len(views)
    inside = [
        v for v in views
        if (hi is None or v.entry_time <= hi) and (lo is None or v.exit_time >= lo)
    ]
    inside.sort(key=lambda v: (v.entry_time, v.trade_id))
    caveats = _run_caveats([run])
    if len(inside) > MAX_TRADES:
        caveats.append(
            Caveat(
                code="backtest_trades_truncated",
                severity="info",
                message=f"{len(inside)} trades overlap the window; the latest {MAX_TRADES} "
                "are served.",
                affects="backtest_trades",
                direction="unknown",
            )
        )
        inside = inside[-MAX_TRADES:]
    if unreadable:
        caveats.append(
            Caveat(
                code="backtest_trades_unreadable",
                severity="warning",
                message=f"{unreadable} stored trade row(s) lacked a time or direction and "
                "are not drawn.",
                affects="backtest_trades",
                direction="unknown",
            )
        )
    if tf is not None and run.timeframe and run.timeframe.upper() != tf:
        caveats.append(
            Caveat(
                code="backtest_timeframe_mismatch",
                severity="info",
                message=f"The run executed on {run.timeframe} bars; it is drawn on {tf} bars, "
                "so an entry is placed on the bar open at its time.",
                affects="backtest_trades",
                direction="unknown",
            )
        )
    ccys = {v.net_pnl.unit for v in views if v.net_pnl.unit}
    count = lambda n: Figure(  # noqa: E731
        value=float(n), provenance=run.provenance, unit="count"
    )
    view = BacktestTradesView(
        symbol=sym,
        timeframe=tf,
        window_from=lo,
        window_to=hi,
        run=run,
        trades=inside,
        total_on_symbol=count(len(views)),
        outside_window=count(len(views) - len(inside)),
        stops_detail=(
            "Stops and targets as the engine placed them (initial) and held them at the "
            "close (final). When a stop moved, the engine records by which rule, not "
            "when, so no stop-move path is drawn."
            if stamped
            else stops_note
        ),
        account_ccy=next(iter(ccys)) if len(ccys) == 1 else None,
    )
    datasets = ", ".join(run.dataset_version_ids) or "not recorded"
    return Envelope(
        data=view,
        source=_note(
            "live",
            f"Research ledger backtest {run.run_id} ({run.strategy_id}, engine "
            f"{run.engine_version or 'unstamped'}), dataset {datasets}; read-only.",
        ),
        caveats=tuple(caveats),
    )


# ================================================================= drawings


class OtherDrawingSet(BaseModel):
    """Another operator's drawings on this symbol: shown, never editable here."""

    model_config = ConfigDict(frozen=True)

    owner: str
    version: int
    updated_at: datetime | None
    drawings: list[Drawing]


class DrawingSetView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    owner: str
    version: int
    updated_at: datetime | None
    updated_by: str
    drawings: list[Drawing]
    #: Whether the signed-in role may write (operator or admin). The server
    #: refuses a viewer's write whatever this says.
    can_edit: bool
    others: list[OtherDrawingSet]
    max_drawings: int


class DrawingSetWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The version this set was edited from. A stale version is refused (409).
    base_version: int = Field(ge=0)
    drawings: list[Drawing] = Field(max_length=MAX_DRAWINGS)


def _store(settings: Any) -> DrawingStore:
    # One path rule (core.paths): the API's state dir, resolved like the CLI's.
    return DrawingStore(resolve_paths({"FIBOKI_STATE_DIR": str(settings.state_dir)}).drawings_db)


def _set_view(stored: StoredSet, principal: Principal, others: list[StoredSet]) -> DrawingSetView:
    return DrawingSetView(
        symbol=stored.symbol,
        owner=stored.owner,
        version=stored.version,
        updated_at=stored.updated_at,
        updated_by=stored.updated_by,
        drawings=list(stored.drawings),
        can_edit=principal.role.satisfies(Role.OPERATOR),
        others=[
            OtherDrawingSet(
                owner=o.owner, version=o.version, updated_at=o.updated_at, drawings=list(o.drawings)
            )
            for o in others
        ],
        max_drawings=MAX_DRAWINGS,
    )


def _envelope(view: DrawingSetView, store: DrawingStore) -> Envelope[DrawingSetView]:
    return Envelope(
        data=view,
        source=_note(
            "live",
            f"Operator drawings at {store.path}: annotations placed by hand, never read "
            "by a strategy, the risk engine or a backtest.",
        ),
    )


@router.get("/drawings/{symbol}", response_model=Envelope[DrawingSetView])
def get_drawings(
    symbol: str, settings: SettingsDep, principal: AnyPrincipal
) -> Envelope[DrawingSetView]:
    sym = _known_symbol(symbol)
    store = _store(settings)
    try:
        mine = store.get(principal.user_id, sym)
        others = store.others(principal.user_id, sym)
    except (sqlite3.Error, ValueError) as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "drawings_unavailable",
            "The drawing store could not be read. Your drawings are not lost; they "
            "could not be loaded.",
            context={"error_class": type(exc).__name__},
        ) from exc
    return _envelope(_set_view(mine, principal, others), store)


def _write(
    *,
    action: str,
    symbol: str,
    base_version: int,
    drawings: list[Drawing],
    request: Request,
    settings: Any,
    audit: Any,
    principal: Principal,
) -> Envelope[DrawingSetView]:
    sym = _known_symbol(symbol)
    store = _store(settings)

    def record(outcome: str, reason: str, detail: dict[str, Any]) -> None:
        audit.record(
            f"chart.drawings.{action}",
            actor=principal.user_id,
            actor_role=principal.role.value,
            outcome=outcome,
            reason=reason,
            target=sym,
            execution_mode=settings.execution_mode.value,
            correlation_id=current_correlation_id(),
            source_ip=request.client.host if request.client else "",
            detail={"symbol": sym, "base_version": base_version, **detail},
        )

    try:
        stored = store.put(
            principal.user_id,
            sym,
            base_version=base_version,
            drawings=drawings,
            actor=principal.user_id,
            action=action,
        )
    except DrawingConflict as conflict:
        record(
            "refused",
            "stale base version",
            {"current_version": conflict.current.version},
        )
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "drawings_conflict",
            f"Your drawings for {sym} changed elsewhere (now version "
            f"{conflict.current.version}, you edited version {base_version}). Nothing was "
            "overwritten; reload to see the newer set.",
            context={"current_version": conflict.current.version, "base_version": base_version},
        ) from conflict
    except ValueError as exc:
        record("refused", str(exc), {})
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_drawings", str(exc)) from exc
    except sqlite3.Error as exc:
        record("failed", type(exc).__name__, {})
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "drawings_unavailable",
            "The drawing store could not be written. Nothing was saved.",
            context={"error_class": type(exc).__name__},
        ) from exc
    record(
        "allowed",
        f"{len(drawings)} drawing(s)",
        {"version": stored.version, "count": len(drawings)},
    )
    return _envelope(_set_view(stored, principal, store.others(principal.user_id, sym)), store)


@router.put("/drawings/{symbol}", response_model=Envelope[DrawingSetView])
def put_drawings(
    symbol: str,
    body: DrawingSetWrite,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
) -> Envelope[DrawingSetView]:
    """Replace the operator's whole set for this symbol. Audited; versioned."""
    return _write(
        action="put",
        symbol=symbol,
        base_version=body.base_version,
        drawings=list(body.drawings),
        request=request,
        settings=settings,
        audit=audit,
        principal=principal,
    )


@router.delete("/drawings/{symbol}", response_model=Envelope[DrawingSetView])
def delete_drawings(
    symbol: str,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
    base_version: int = Query(..., ge=0),
) -> Envelope[DrawingSetView]:
    """Clear the operator's set (a new, empty version; the history keeps the old)."""
    return _write(
        action="delete",
        symbol=symbol,
        base_version=base_version,
        drawings=[],
        request=request,
        settings=settings,
        audit=audit,
        principal=principal,
    )
