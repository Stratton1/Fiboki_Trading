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

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.models import Envelope, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.routers import chart as chart_router
from fiboki.api.routers import markets as markets_router
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Provenance, Timeframe

router = APIRouter(tags=["chart-workstation"])

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
