"""CHART WORKSTATION (Wave 2B): replay, compare, data window, events, templates, price alerts.

Everything the chart workstation draws beyond the candles and the overlays
(``routers/markets.py``) and the backtest trades and drawings
(``routers/chart.py``). Nothing here computes a signal, a stop, a size or a
P&L: those are the stored ones, or the indicator package's, served as they are.

``GET /api/markets/replay/{symbol}``
    Bar-by-bar replay. Given a ``cursor`` (a UTC instant) it serves only the
    candles CLOSED by then, the indicators and regimes computed over those
    candles alone, and the paper and backtest trades and strategy markers as
    they stood at the cursor: a trade still open at the cursor has no exit, no
    P&L and no final stop. Nothing timed after the cursor is ever served
    (``tests/api/test_chart_replay.py`` proves it by perturbing the future).
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.chart_store import (
    MAX_TEMPLATES,
    ChartTemplate,
    PriceAlert,
    PriceAlertStore,
    StoredTemplate,
    TemplateConflict,
    TemplateStore,
)
from fiboki.api.deps import AnyPrincipal, AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.routers import chart as chart_router
from fiboki.api.routers import markets as markets_router
from fiboki.api.security import Principal, Role, require_operator
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Provenance, Timeframe
from fiboki.core.paths import resolve_paths

router = APIRouter(tags=["chart-workstation"])

OperatorPrincipal = Annotated[Principal, Depends(require_operator)]

#: Closed bars served per replay step.
REPLAY_DEFAULT_BARS = 300
REPLAY_MAX_BARS = 2_000


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _symbol(symbol: str) -> str:
    if not instrument_registry.exists(symbol):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_instrument",
            f"{symbol!r} is not in the instrument registry.",
        )
    return instrument_registry.get(symbol).symbol


def _tf(value: str) -> Timeframe:
    try:
        return Timeframe(value)
    except ValueError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "unknown_timeframe",
            f"{value!r} is not a recognised timeframe.",
        ) from exc


def _mounted(settings: Any, symbol: str, timeframe: str) -> None:
    if settings.data_root is None or not settings.data_root.exists():
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "market_data_not_mounted",
            "No market-data root is mounted on this deployment, so no bars can "
            "be served. This is a missing data source, not a quiet market.",
            context={"symbol": symbol, "timeframe": timeframe},
        )


def _read_frame(settings: Any, symbol: str, tf: Timeframe) -> tuple[Any, str]:
    from fiboki.data.store import DataStore

    try:
        with DataStore(settings.data_root) as store:
            frame, version = store.read_latest(instrument=symbol, timeframe=tf)
    except Exception as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "bars_unavailable",
            "The bar dataset could not be read.",
            context={"error_class": type(exc).__name__},
        ) from exc
    return frame, str(getattr(version, "version_id", "") or "")


def _bar_dicts(frame: Any) -> tuple[list[dict[str, Any]], str | None]:
    volume_kind = markets_router._volume_column(frame)
    out: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        bar: dict[str, Any] = {
            "t": index.isoformat(),
            "o": float(row.open),
            "h": float(row.high),
            "l": float(row.low),
            "c": float(row.close),
        }
        if volume_kind is not None:
            raw = row[volume_kind]
            bar["v"] = None if raw is None or raw != raw else float(raw)
        out.append(bar)
    return out, volume_kind


# ================================================================== replay


class ReplayBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    t: datetime
    o: float
    h: float
    l: float  # noqa: E741 - the bar contract's own name
    c: float
    v: float | None = None


class ReplayTrade(BaseModel):
    """A backtest trade AS IT STOOD at the cursor.

    ``open_at_cursor`` trades have entered but not exited by the cursor: their
    exit, exit reason, P&L, costs and final stop are not known yet and are
    null, never the stored future values.
    """

    model_config = ConfigDict(frozen=True)

    trade_id: str
    strategy_id: str
    direction: str = Field(pattern="^(long|short)$")
    size: Figure
    entry_time: datetime
    entry_price: Figure
    stop_loss: Figure
    take_profit_targets: list[Figure]
    open_at_cursor: bool
    exit_time: datetime | None
    exit_price: Figure | None
    exit_reason: str | None
    net_pnl: Figure | None
    final_stop: Figure | None
    provenance: Provenance


class ReplayView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    #: The replay instant: every item served is timed at or before it.
    cursor: datetime
    dataset_version_id: str
    volume_kind: str | None
    bars: list[ReplayBar]
    #: The close time of the last served bar (``<= cursor``), or null when no
    #: bar has closed by the cursor.
    last_close: datetime | None
    #: The cursor at which one more bar has closed (step forward), or null at
    #: the end of the dataset.
    next_cursor: datetime | None
    #: The cursor one bar earlier (step back), or null at the start.
    prev_cursor: datetime | None
    first_bar: datetime | None
    last_bar: datetime | None
    #: Bars closed by the cursor in the whole dataset (the served ones are the
    #: last ``limit`` of these).
    bars_closed: int
    bars_total: int
    #: The overlay payload restricted to the cursor (see the module docstring).
    overlays: markets_router.OverlayView
    run_id: str | None
    trades: list[ReplayTrade]
    trades_detail: str


def _close_of(open_time: Any, tf: Timeframe) -> Any:
    return open_time + timedelta(minutes=tf.minutes)


def _restrict_overlays(
    view: markets_router.OverlayView, cursor: datetime
) -> tuple[markets_router.OverlayView, list[str]]:
    """Every overlay item timed after ``cursor`` removed; the rest as served."""
    notes: list[str] = []
    levels = []
    dropped_open = 0
    for level in view.levels:
        if level.from_ > cursor:
            continue
        if level.to is None:
            # A position open at the end of the session: its persisted stop
            # and target are TODAY's, not the cursor's. Only its entry stands.
            if level.role != "entry":
                dropped_open += 1
                continue
            levels.append(level)
        elif level.to > cursor:
            # A closed trade's recorded stop or target, set at entry: it stood
            # at the cursor, and when it stopped standing is not yet known.
            levels.append(level.model_copy(update={"to": None}))
        else:
            levels.append(level)
    if dropped_open:
        notes.append(
            f"{dropped_open} stop/target level(s) of positions open now are not drawn: "
            "the journal holds their current values, not the values at the cursor."
        )
    series = [s for s in view.series if not s.display_only]
    if len(series) != len(view.series):
        notes.append("The chart-only lagging span is not served in replay (it reads later closes).")
    restricted = view.model_copy(
        update={
            "signals": [s for s in view.signals if s.t <= cursor],
            "fills": [f for f in view.fills if f.t <= cursor],
            "levels": levels,
            "regimes": [r for r in view.regimes if r.to <= cursor],
            "series": series,
            "events": [e for e in view.events if e.t <= cursor],
            "headlines": [h for h in view.headlines if h.t <= cursor],
            "window_to": min(view.window_to, cursor) if view.window_to else view.window_to,
        }
    )
    return restricted, notes


def _replay_trades(
    symbol: str, run_id: str, settings: Any, tf: str, lo: datetime, cursor: datetime
) -> tuple[list[ReplayTrade], str]:
    env = chart_router.backtest_trades(
        symbol, run_id, settings, from_=lo, to=cursor, timeframe=tf
    )
    out: list[ReplayTrade] = []
    for t in env.data.trades:
        if t.entry_time > cursor:
            continue
        closed = t.exit_time <= cursor
        out.append(
            ReplayTrade(
                trade_id=t.trade_id,
                strategy_id=t.strategy_id,
                direction=t.direction,
                size=t.size,
                entry_time=t.entry_time,
                entry_price=t.entry_price,
                stop_loss=t.stop_loss,
                take_profit_targets=t.take_profit_targets,
                open_at_cursor=not closed,
                exit_time=t.exit_time if closed else None,
                exit_price=t.exit_price if closed else None,
                exit_reason=t.exit_reason if closed else None,
                net_pnl=t.net_pnl if closed else None,
                final_stop=t.final_stop if closed else None,
                provenance=t.provenance,
            )
        )
    detail = (
        f"Backtest {run_id}: {len(out)} trade(s) entered by the cursor, "
        f"{sum(1 for t in out if t.open_at_cursor)} still open at it (exit, P&L and final "
        "stop withheld until their exit time)."
    )
    return out, detail


@router.get("/api/markets/replay/{symbol}", response_model=Envelope[ReplayView])
def replay(
    symbol: str,
    platform: PlatformDep,
    settings: SettingsDep,
    cursor: Annotated[datetime, Query(description="UTC instant; nothing after it is served.")],
    timeframe: str = Query("H1"),
    limit: int = Query(REPLAY_DEFAULT_BARS, ge=1, le=REPLAY_MAX_BARS),
    run: str | None = Query(None, description="A backtest run whose trades to replay."),
    include_indicators: bool = Query(True),
) -> Envelope[ReplayView]:
    """Closed candles, overlays and trades as of ``cursor``; never anything later."""
    import pandas as pd

    sym = _symbol(symbol)
    tf = _tf(timeframe)
    _mounted(settings, sym, tf.value)
    at = _utc(cursor)
    frame, version = _read_frame(settings, sym, tf)
    opens = frame.index
    closes = opens + pd.Timedelta(minutes=tf.minutes)
    closed_mask = closes <= pd.Timestamp(at)
    closed = frame[closed_mask]
    n_closed = int(closed_mask.sum())
    window = closed.tail(limit)
    bars, volume_kind = _bar_dicts(window)

    next_cursor = (
        closes[n_closed].to_pydatetime() if n_closed < len(frame) else None
    )
    prev_cursor = closes[n_closed - 2].to_pydatetime() if n_closed >= 2 else None
    caveats: list[Caveat] = [
        Caveat(
            code="replay_bar_close_rule",
            severity="info",
            message=f"A bar counts as closed at its open plus {tf.minutes} minutes. A bar "
            "whose venue session ended early (a Friday close) is shown one bar late, "
            "never early.",
            affects="bars",
            direction="pessimistic",
        )
    ]

    if window.empty:
        overlay_view = markets_router.OverlayView(
            symbol=sym, timeframe=tf.value, window_from=None, window_to=None,
            bars_dataset_version_id=version or None, signals=[], fills=[], levels=[],
            regimes=[], series=[], events=[], headlines=[],
            sections={"bars": markets_router.SectionStatus(
                available=False, detail="No bar of this dataset had closed by the cursor.")},
        )
        notes: list[str] = []
    else:
        lo = window.index[0].to_pydatetime()
        hi = window.index[-1].to_pydatetime()
        env = markets_router.overlays(
            sym, platform, settings, from_=lo, to=hi, strategy_id=None,
            timeframe=tf.value, limit=limit, include_indicators=include_indicators,
        )
        overlay_view, notes = _restrict_overlays(env.data, at)
        caveats.extend(env.caveats)
        # The overlay route reads its own copy of the latest dataset; if a new
        # version landed between the two reads, say so rather than mix them.
        if overlay_view.bars_dataset_version_id not in (None, version):
            caveats.append(
                Caveat(
                    code="replay_dataset_changed",
                    severity="warning",
                    message="A new dataset version landed during this step; overlays "
                    f"are from {overlay_view.bars_dataset_version_id}, bars from {version}.",
                    affects="series",
                    direction="unknown",
                )
            )

    trades: list[ReplayTrade] = []
    trades_detail = "No backtest run selected."
    if run:
        lo_t = window.index[0].to_pydatetime() if not window.empty else at
        trades, trades_detail = _replay_trades(sym, run, settings, tf.value, lo_t, at)
    for note in notes:
        caveats.append(
            Caveat(code="replay_withheld", severity="info", message=note,
                   affects="overlays", direction="unknown")
        )

    view = ReplayView(
        symbol=sym,
        timeframe=tf.value,
        cursor=at,
        dataset_version_id=version,
        volume_kind=volume_kind,
        bars=[ReplayBar(**b) for b in bars],
        last_close=closes[n_closed - 1].to_pydatetime() if n_closed else None,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        first_bar=opens[0].to_pydatetime() if len(opens) else None,
        last_bar=opens[-1].to_pydatetime() if len(opens) else None,
        bars_closed=n_closed,
        bars_total=len(frame),
        overlays=overlay_view,
        run_id=run,
        trades=trades,
        trades_detail=trades_detail,
    )
    return Envelope(
        data=view,
        source=_note(
            "live",
            f"Replay of dataset {version} as of {at.isoformat()}: closed bars only; "
            "indicators and regimes computed over those bars alone.",
        ),
        caveats=tuple(caveats),
    )


# ================================================================= compare


class ComparePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    t: datetime
    #: Percent change of the compared close since the anchor bar; null where
    #: the compared instrument has no bar at this base bar's time.
    v: float | None


class CompareView(BaseModel):
    model_config = ConfigDict(frozen=True)

    base: str
    symbol: str
    timeframe: str
    unit: str = "percent"
    #: The base bar the series is rebased at (the first base bar the compared
    #: instrument also has a close for): 0% there by definition.
    anchor_time: datetime | None
    anchor_close: Figure
    base_dataset_version_id: str
    dataset_version_id: str
    points: list[ComparePoint]
    #: Base bars with no compared bar at the same time (drawn as gaps).
    unmatched: int


@router.get("/api/markets/compare/{base}", response_model=Envelope[CompareView])
def compare(
    base: str,
    settings: SettingsDep,
    with_: Annotated[str, Query(alias="with", description="The instrument compared against the base.")],
    timeframe: str = Query("H1"),
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    limit: int = Query(500, ge=1, le=5_000),
) -> Envelope[CompareView]:
    """``with``'s close rebased to percent at the first shared bar, on ``base``'s bars.

    Aligned on the base instrument's bar open times exactly: a compared bar at
    a different time is not interpolated, and the gap is counted.
    """
    import pandas as pd

    b = _symbol(base)
    s = _symbol(with_)
    tf = _tf(timeframe)
    _mounted(settings, b, tf.value)
    base_frame, base_version = _read_frame(settings, b, tf)
    other, version = _read_frame(settings, s, tf)
    sel = base_frame
    if from_ is not None:
        sel = sel[sel.index >= pd.Timestamp(_utc(from_))]
    if to is not None:
        sel = sel[sel.index <= pd.Timestamp(_utc(to))]
    window = sel.tail(limit)
    closes = other["close"].reindex(window.index)
    known = closes.dropna()
    p = Provenance.BACKTEST
    caveats: list[Caveat] = []
    if known.empty:
        anchor_time = None
        anchor = Figure.missing(p, reason=f"{s} has no bar at any of these {b} bar times.")
        points = [ComparePoint(t=t.to_pydatetime(), v=None) for t in window.index]
    else:
        anchor_time = known.index[0].to_pydatetime()
        anchor_close = float(known.iloc[0])
        anchor = Figure(value=anchor_close, provenance=p)
        points = []
        for stamp, close in closes.items():
            v = None if close != close else (float(close) / anchor_close - 1.0) * 100.0
            points.append(ComparePoint(t=stamp.to_pydatetime(), v=v))
    unmatched = int(closes.isna().sum())
    if unmatched:
        caveats.append(
            Caveat(
                code="compare_unaligned_bars",
                severity="info",
                message=f"{unmatched} {b} bar(s) have no {s} bar at the same time; they are "
                "gaps, not interpolated values.",
                affects="compare",
                direction="unknown",
            )
        )
    return Envelope(
        data=CompareView(
            base=b,
            symbol=s,
            timeframe=tf.value,
            anchor_time=anchor_time,
            anchor_close=anchor,
            base_dataset_version_id=base_version,
            dataset_version_id=version,
            points=points,
            unmatched=unmatched,
        ),
        source=_note(
            "live",
            f"Canonical bar store: {s} close (dataset {version}) as percent change since "
            f"its close at the first shared {b} bar (dataset {base_version}).",
        ),
        caveats=tuple(caveats),
    )


# ============================================================= data window


class DataWindowValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    indicator_id: str
    name: str
    pane: str
    #: The indicator's value at this bar; null where it has none (warm-up).
    value: float | None
    display_only: bool


class DataWindowView(BaseModel):
    """Everything the platform holds for ONE bar of one chart."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    symbol: str
    timeframe: str
    #: The bar's open time (the bar open at or before the requested ``t``).
    t: datetime
    #: The bar's span ``[t, t_end)``: items timed inside it belong to it.
    t_end: datetime
    dataset_version_id: str
    bar: ReplayBar
    values: list[DataWindowValue]
    regime: markets_router.RegimeOverlay | None
    signals: list[markets_router.SignalOverlay]
    fills: list[markets_router.FillOverlay]
    #: Levels standing during the bar (entry, stop, target).
    levels: list[markets_router.LevelOverlay]
    events: list[markets_router.EventOverlay]
    headlines: list[markets_router.HeadlineOverlay]
    sections: dict[str, markets_router.SectionStatus]


@router.get("/api/markets/datawindow/{symbol}", response_model=Envelope[DataWindowView])
def data_window(
    symbol: str,
    platform: PlatformDep,
    settings: SettingsDep,
    t: Annotated[datetime, Query(description="A UTC instant inside the bar.")],
    timeframe: str = Query("H1"),
) -> Envelope[DataWindowView]:
    """OHLC(V), every indicator output and every overlay item for the bar at ``t``.

    The same computation as the overlays route (indicators over the bar's
    history, closed bars only), for one bar, with nothing filtered by the
    chart's layer toggles: state and ratio columns included.
    """
    import pandas as pd

    sym = _symbol(symbol)
    tf = _tf(timeframe)
    _mounted(settings, sym, tf.value)
    frame, version = _read_frame(settings, sym, tf)
    at = pd.Timestamp(_utc(t))
    upto = frame[frame.index <= at]
    if upto.empty:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "no_bar_at_time",
            f"The dataset has no {sym} {tf.value} bar at or before {at.isoformat()}.",
        )
    bar_open = upto.index[-1]
    span_end = bar_open + pd.Timedelta(minutes=tf.minutes)
    later = frame[frame.index > bar_open]
    hi = later.index[0] if not later.empty else bar_open
    env = markets_router.overlays(
        sym, platform, settings, from_=bar_open.to_pydatetime(), to=hi.to_pydatetime(),
        strategy_id=None, timeframe=tf.value, limit=2, include_indicators=True,
    )
    ov = env.data
    lo_dt, end_dt = bar_open.to_pydatetime(), span_end.to_pydatetime()

    def inside(x: datetime) -> bool:
        return lo_dt <= x < end_dt

    values: list[DataWindowValue] = []
    for s in ov.series:
        point = next((p for p in s.points if pd.Timestamp(p.t) == bar_open), None)
        values.append(
            DataWindowValue(
                indicator_id=s.indicator_id, name=s.name, pane=s.pane,
                value=None if point is None else point.v, display_only=s.display_only,
            )
        )
    regime = next((r for r in ov.regimes if r.from_ <= lo_dt <= r.to), None)
    bars, _ = _bar_dicts(frame.loc[[bar_open]])
    view = DataWindowView(
        symbol=sym,
        timeframe=tf.value,
        t=lo_dt,
        t_end=end_dt,
        dataset_version_id=version,
        bar=ReplayBar(**bars[0]),
        values=values,
        regime=regime,
        signals=[s for s in ov.signals if inside(s.t)],
        fills=[f for f in ov.fills if inside(f.t)],
        levels=[
            lv for lv in ov.levels
            if lv.from_ < end_dt and (lv.to is None or lv.to >= lo_dt)
        ],
        events=[e for e in ov.events if inside(e.t)],
        headlines=[h for h in ov.headlines if inside(h.t)],
        sections=ov.sections,
    )
    return Envelope(
        data=view,
        source=_note(
            "live",
            f"Dataset {version}, bar {bar_open.isoformat()}: indicators from "
            "fiboki.indicators, regime from fiboki.marketstate, journal items from the "
            "paper journal, events from the official calendar.",
        ),
        caveats=env.caveats,
    )


# =============================================================== templates

TEMPLATE_NAME = r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,47}$"


class ChartTemplateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: int
    updated_at: datetime
    template: ChartTemplate


class ChartTemplatesView(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: str
    templates: list[ChartTemplateView]
    #: Whether the signed-in role may save (operator or admin). The server
    #: refuses a viewer's write whatever this says.
    can_edit: bool
    max_templates: int


class ChartTemplateWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The version this template was edited from; 0 to create. Stale: 409.
    base_version: int = Field(ge=0)
    template: ChartTemplate


def _paths(settings: Any) -> Any:
    # One path rule (core.paths): the API's state dir, resolved like the CLI's.
    return resolve_paths({"FIBOKI_STATE_DIR": str(settings.state_dir)})


def _templates_view(store: TemplateStore, principal: Principal) -> Envelope[ChartTemplatesView]:
    try:
        items = store.list(principal.user_id)
    except (sqlite3.Error, ValueError) as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "templates_unavailable",
            "The template store could not be read. Your templates are not lost; they "
            "could not be loaded.",
            context={"error_class": type(exc).__name__},
        ) from exc
    return Envelope(
        data=ChartTemplatesView(
            owner=principal.user_id,
            templates=[_template_view(t) for t in items],
            can_edit=principal.role.satisfies(Role.OPERATOR),
            max_templates=MAX_TEMPLATES,
        ),
        source=_note(
            "live",
            f"Operator chart templates at {store.path}: display choices only (layers, "
            "indicator toggles, strategies), never read by a strategy or the risk engine.",
        ),
    )


def _template_view(t: StoredTemplate) -> ChartTemplateView:
    return ChartTemplateView(name=t.name, version=t.version, updated_at=t.updated_at,
                             template=t.template)


def _audit(audit: Any, request: Request, settings: Any, principal: Principal, action: str,
           outcome: str, reason: str, target: str, detail: dict[str, Any]) -> None:
    audit.record(
        action,
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome=outcome,
        reason=reason,
        target=target,
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
        detail=detail,
    )


@router.get("/api/chart/templates", response_model=Envelope[ChartTemplatesView])
def list_templates(settings: SettingsDep, principal: AnyPrincipal) -> Envelope[ChartTemplatesView]:
    """The signed-in operator's chart templates, by name."""
    return _templates_view(TemplateStore(_paths(settings).chart_templates_db), principal)


@router.put("/api/chart/templates/{name}", response_model=Envelope[ChartTemplatesView])
def put_template(
    name: Annotated[str, Path(pattern=TEMPLATE_NAME)],
    body: ChartTemplateWrite,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
) -> Envelope[ChartTemplatesView]:
    """Create (base_version 0) or replace one template. Audited; versioned."""
    store = TemplateStore(_paths(settings).chart_templates_db)
    detail = {"name": name, "base_version": body.base_version}
    try:
        stored = store.put(principal.user_id, name, body.template, base_version=body.base_version)
    except TemplateConflict as conflict:
        _audit(audit, request, settings, principal, "chart.templates.put", "refused",
               "stale base version", name, {**detail, "current_version": conflict.current_version})
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "template_conflict",
            f"Template {name!r} is at version {conflict.current_version}, not "
            f"{body.base_version}. Nothing was overwritten; reload to see it.",
            context={"current_version": conflict.current_version},
        ) from conflict
    except ValueError as exc:
        _audit(audit, request, settings, principal, "chart.templates.put", "refused",
               str(exc), name, detail)
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_template", str(exc)) from exc
    except sqlite3.Error as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "templates_unavailable",
            "The template store could not be written. Nothing was saved.",
            context={"error_class": type(exc).__name__},
        ) from exc
    _audit(audit, request, settings, principal, "chart.templates.put", "allowed",
           f"version {stored.version}", name, {**detail, "version": stored.version})
    return _templates_view(store, principal)


@router.delete("/api/chart/templates/{name}", response_model=Envelope[ChartTemplatesView])
def delete_template(
    name: Annotated[str, Path(pattern=TEMPLATE_NAME)],
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
    base_version: int = Query(..., ge=1),
) -> Envelope[ChartTemplatesView]:
    store = TemplateStore(_paths(settings).chart_templates_db)
    try:
        store.delete(principal.user_id, name, base_version=base_version)
    except TemplateConflict as conflict:
        _audit(audit, request, settings, principal, "chart.templates.delete", "refused",
               "stale or missing", name, {"name": name, "base_version": base_version})
        raise ApiError(
            status.HTTP_409_CONFLICT if conflict.current_version else status.HTTP_404_NOT_FOUND,
            "template_conflict" if conflict.current_version else "unknown_template",
            f"Template {name!r} is not at version {base_version}; nothing was deleted.",
            context={"current_version": conflict.current_version},
        ) from conflict
    _audit(audit, request, settings, principal, "chart.templates.delete", "allowed",
           "deleted", name, {"name": name, "base_version": base_version})
    return _templates_view(store, principal)


# ============================================================ price alerts


class PriceAlertCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    price: float = Field(gt=0, allow_inf_nan=False)
    timeframe: str = "H1"
    drawing_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    origin: str | None = Field(default=None, pattern="^(level|zone_top|zone_bottom)$")
    note: str = Field(default="", max_length=280)


class PriceAlertsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    alerts: list[PriceAlert]
    #: True when something evaluates these alerts. False on this branch.
    evaluated: bool
    evaluator_detail: str


EVALUATOR_DETAIL = (
    "Stored, not watched: no price-alert evaluator is wired on this deployment yet, so an "
    "alert will not fire. It records the level and the direction the platform decided."
)


def _alerts_envelope(alerts: list[PriceAlert], store: PriceAlertStore) -> Envelope[PriceAlertsView]:
    return Envelope(
        data=PriceAlertsView(alerts=alerts, evaluated=False, evaluator_detail=EVALUATOR_DETAIL),
        source=_note("live", f"Operator price alerts at {store.path} (append-only)."),
        caveats=(
            Caveat(
                code="price_alerts_not_evaluated",
                severity="warning",
                message=EVALUATOR_DETAIL,
                affects="alerts",
                direction="optimistic",
            ),
        ),
    )


@router.get("/api/alerts/price", response_model=Envelope[PriceAlertsView])
def list_price_alerts(
    settings: SettingsDep, principal: AnyPrincipal, symbol: str | None = Query(None)
) -> Envelope[PriceAlertsView]:
    store = PriceAlertStore(_paths(settings).price_alerts_db)
    sym = _symbol(symbol) if symbol else None
    return _alerts_envelope(store.list(principal.user_id, sym), store)


@router.post(
    "/api/alerts/price",
    response_model=Envelope[PriceAlertsView],
    status_code=status.HTTP_201_CREATED,
)
def create_price_alert(
    body: PriceAlertCreate,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
) -> Envelope[PriceAlertsView]:
    """An alert at ``price``; the platform decides cross-above or cross-below.

    The direction is read against the latest CLOSED bar in the store: above
    that close, the alert waits for a cross above; below it, a cross below. A
    level equal to the close is refused (no direction can be decided).
    """
    sym = _symbol(body.symbol)
    tf = _tf(body.timeframe)
    _mounted(settings, sym, tf.value)
    frame, version = _read_frame(settings, sym, tf)
    if frame.empty:
        raise ApiError(status.HTTP_409_CONFLICT, "no_reference_bar",
                       f"No {sym} {tf.value} bar to decide a direction against.")
    import pandas as pd

    now = pd.Timestamp(datetime.now(tz=UTC))
    closes = frame.index + pd.Timedelta(minutes=tf.minutes)
    closed = frame[closes <= now]
    ref = closed if not closed.empty else frame
    ref_time = ref.index[-1].to_pydatetime()
    ref_close = float(ref["close"].iloc[-1])
    if body.price == ref_close:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "alert_at_reference_close",
            f"{body.price} is the latest close; move the level to set a direction.",
        )
    store = PriceAlertStore(_paths(settings).price_alerts_db)
    alert = PriceAlert(
        alert_id=store.new_id(),
        owner=principal.user_id,
        symbol=sym,
        price=body.price,
        condition="cross_above" if body.price > ref_close else "cross_below",
        reference_close=ref_close,
        reference_time=ref_time,
        dataset_version_id=version,
        drawing_id=body.drawing_id,
        origin=body.origin,
        note=body.note,
        created_at=datetime.now(tz=UTC),
    )
    try:
        store.add(alert)
    except sqlite3.Error as exc:
        _audit(audit, request, settings, principal, "alerts.price.create", "failed",
               type(exc).__name__, sym, {"price": body.price})
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "price_alerts_unavailable",
            "The alert store could not be written. No alert was created.",
            context={"error_class": type(exc).__name__},
        ) from exc
    _audit(audit, request, settings, principal, "alerts.price.create", "allowed",
           alert.condition, sym, {"alert_id": alert.alert_id, "price": body.price,
                                  "drawing_id": body.drawing_id})
    return _alerts_envelope(store.list(principal.user_id, sym), store)


# ================================================================== events


class CalendarEventsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    #: The official calendar version served (the newest listed one).
    calendar_version: str
    calendar_digest: str | None
    currencies: list[str]
    window_from: datetime | None
    window_to: datetime | None
    events: list[markets_router.EventOverlay]
    #: The event ``event_id`` named, wherever it is (null when none was asked).
    focus: markets_router.EventOverlay | None
    #: The calendar's declared coverage: no event outside it is known.
    declared_start: datetime | None
    declared_end: datetime | None


def _event_view(event: Any, version: str, digest: str | None) -> markets_router.EventOverlay:
    return markets_router.EventOverlay(
        t=event.event_time.to_pydatetime(),
        window_end=event.window_end.to_pydatetime() if event.window_end is not None else None,
        time_known=event.time_known,
        currency=event.currency,
        name=event.name,
        impact=event.impact.value,
        event_id=event.event_id,
        source_url=event.source_url,
        dataset_version_id=f"official_calendar:{version}" + (f" {digest}" if digest else ""),
        source=markets_router.OverlaySource(
            kind="official_calendar", detail=f"{event.source} (official calendar {version})"
        ),
    )


@router.get("/api/markets/events/{symbol}", response_model=Envelope[CalendarEventsView])
def calendar_events(
    symbol: str,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    event_id: str | None = Query(None, max_length=200),
) -> Envelope[CalendarEventsView]:
    """Scheduled events for the instrument's currencies from the NEWEST official calendar.

    With ``event_id`` the named event is returned as ``focus`` (any currency),
    so a link ``/markets/EURUSD?event=<id>`` can centre the chart on it.
    """
    import pandas as pd

    from fiboki.marketstate.calendar import (
        CalendarError,
        instrument_currencies,
        latest_official_calendar_version,
        load_official_calendar,
        official_calendar_path,
    )

    sym = _symbol(symbol)
    try:
        version = latest_official_calendar_version()
        calendar = load_official_calendar(version=version)
        digest = markets_router._file_digest(official_calendar_path(version))
    except CalendarError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "calendar_unavailable",
            f"The official calendar could not be loaded: {exc}",
        ) from exc
    currencies = list(instrument_currencies(sym))
    lo = _utc(from_) if from_ else None
    hi = _utc(to) if to else None
    events: list[markets_router.EventOverlay] = []
    if lo is not None and hi is not None:
        if lo > hi:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_window", "'from' is after 'to'.")
        events = [
            _event_view(e, version, digest)
            for e in calendar.events_between(pd.Timestamp(lo), pd.Timestamp(hi), currencies=currencies)
        ]
    focus = None
    if event_id:
        match = next((e for e in calendar.all_events() if e.event_id == event_id), None)
        if match is None:
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                "unknown_event",
                f"Official calendar {version} has no event {event_id!r}.",
            )
        focus = _event_view(match, version, digest)
    coverage = calendar.coverage()
    caveats: list[Caveat] = []
    uncovered = sorted(set(currencies) - set(coverage.currencies))
    if uncovered:
        caveats.append(
            Caveat(
                code="calendar_currency_not_covered",
                severity="warning",
                message=f"The official calendar records no events for {', '.join(uncovered)}: "
                "an empty axis there is missing data, not a quiet week.",
                affects="events",
                direction="optimistic",
            )
        )
    start, end = calendar.declared_span()
    return Envelope(
        data=CalendarEventsView(
            symbol=sym,
            calendar_version=version,
            calendar_digest=digest,
            currencies=currencies,
            window_from=lo,
            window_to=hi,
            events=events,
            focus=focus,
            declared_start=start.to_pydatetime() if start is not None else None,
            declared_end=end.to_pydatetime() if end is not None else None,
        ),
        source=_note(
            "live",
            f"Official scheduled-events calendar {version}, fetched from the publishers' "
            "own pages and committed (no network access).",
        ),
        caveats=tuple(caveats),
    )
