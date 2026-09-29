"""MARKETS: instruments, bars, regimes, correlations, data quality.

The instrument universe is real (:mod:`fiboki.core.instruments`). Bars, regimes
and correlations require a mounted market-data root; when there is none this
router returns an explicit unavailable state with a 503 or an empty page whose
``source.kind`` is ``absent``. It never synthesises a chart that looks like a
measurement.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure, SeriesPoint
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import AssetClass, Provenance

router = APIRouter(prefix="/api/markets", tags=["markets"])


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


class InstrumentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    asset_class: str
    base: str
    quote: str
    trading_hours: str
    pip_size: Figure
    contract_size: Figure
    min_size: Figure
    size_step: Figure
    typical_spread_pips: Figure
    retail_leverage: Figure
    annual_financing_bps: Figure


class DataQualityRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument: str
    available: bool
    quality: str
    detail: str
    bars: Figure
    gaps: Figure
    stale_runs: Figure


class RegimeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument: str
    available: bool
    detail: str
    volatility: str | None = None
    direction: str | None = None
    liquidity: str | None = None
    stress: str | None = None
    persistence: str | None = None


@router.get("/instruments", response_model=Page[InstrumentView])
def instruments(
    asset_class: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
) -> Page[InstrumentView]:
    symbols = instrument_registry.all_symbols()
    if asset_class:
        try:
            cls = AssetClass(asset_class)
        except ValueError as exc:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST,
                "unknown_asset_class",
                f"{asset_class!r} is not a recognised asset class.",
            ) from exc
        symbols = [i.symbol for i in instrument_registry.by_asset_class(cls)]

    p = Provenance.BACKTEST
    items = []
    for symbol in symbols[:limit]:
        inst = instrument_registry.get(symbol)
        items.append(
            InstrumentView(
                symbol=inst.symbol,
                asset_class=inst.asset_class.value,
                base=inst.base,
                quote=inst.quote,
                trading_hours=inst.trading_hours,
                pip_size=Figure(value=inst.pip_size, provenance=p),
                contract_size=Figure(value=inst.contract_size, provenance=p, unit="units"),
                min_size=Figure(value=inst.min_size, provenance=p, unit="lots"),
                size_step=Figure(value=inst.size_step, provenance=p, unit="lots"),
                typical_spread_pips=Figure(
                    value=inst.typical_spread_pips,
                    provenance=p,
                    unit="pips",
                    estimated=True,
                    caveats=(
                        Caveat(
                            code="static_spread",
                            severity="warning",
                            message="A single typical value held constant; real "
                            "spreads widen at news and session edges.",
                            affects="spread",
                            direction="optimistic",
                        ),
                    ),
                ),
                retail_leverage=Figure(value=inst.retail_leverage, provenance=p, unit="x"),
                annual_financing_bps=Figure(
                    value=inst.annual_financing_bps, provenance=p, unit="bps"
                ),
            )
        )
    return Page(
        items=items,
        total=len(symbols),
        limit=limit,
        source=_note(
            "live",
            f"{len(symbols)} registered instruments from fiboki.core.instruments.",
        ),
    )


@router.get("/bars/{symbol}", response_model=Envelope[dict])
def bars(
    symbol: str,
    platform: PlatformDep,
    settings: SettingsDep,
    timeframe: str = Query("H1"),
    limit: int = Query(500, ge=1, le=5000),
) -> Envelope[dict]:
    if not instrument_registry.exists(symbol):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_instrument",
            f"{symbol!r} is not in the instrument registry.",
        )
    if settings.data_root is None or not settings.data_root.exists():
        # An explicit 503 rather than an empty candle array. An empty chart and
        # an unmounted data store must not look the same.
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "market_data_not_mounted",
            "No market-data root is mounted on this deployment, so no bars can "
            "be served. This is a missing data source, not a quiet market.",
            context={"symbol": symbol, "timeframe": timeframe},
        )
    from fiboki.data.store import DataStore

    try:
        store = DataStore(settings.data_root)
        frame, version = store.read_latest(instrument=symbol, timeframe=timeframe)
    except Exception as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "bars_unavailable",
            "The bar dataset could not be read.",
            context={"error_class": type(exc).__name__},
        ) from exc
    tail = frame.tail(limit)
    volume_kind = _volume_column(tail)
    bars_out: list[dict[str, Any]] = []
    for index, row in tail.iterrows():
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
        bars_out.append(bar)
    return Envelope(
        data={
            "symbol": symbol,
            "timeframe": timeframe,
            "dataset_version_id": getattr(version, "version_id", ""),
            # ``volume`` is exchange volume, ``tick_volume`` a count of price
            # updates; null means the dataset carries neither (or only zeros,
            # which for FX is an absence, not a quiet market).
            "volume_kind": volume_kind,
            "bars": bars_out,
        },
        source=_note("live", "Canonical bar store."),
    )


def _volume_column(frame: Any) -> str | None:
    """The volume column worth serving, or ``None``.

    A column that is entirely zero or missing is reported as absent: V1 fed
    identically-zero FX volume to OBV, and a chart pane of zeros would repeat
    that lie visually.
    """
    for column in ("volume", "tick_volume"):
        if column not in frame.columns:
            continue
        values = frame[column].astype(float)
        if values.notna().any() and (values.fillna(0.0) != 0.0).any():
            return column
    return None


@router.get("/regimes", response_model=Page[RegimeView])
def regimes(platform: PlatformDep, settings: SettingsDep) -> Page[RegimeView]:
    if settings.data_root is None or not settings.data_root.exists():
        items = [
            RegimeView(
                instrument=symbol,
                available=False,
                detail="Regime classification requires bar history. No market-data "
                "root is mounted, so the regime is UNKNOWN — not 'ranging'.",
            )
            for symbol in instrument_registry.all_symbols()[:24]
        ]
        return Page(
            items=items,
            total=len(items),
            source=_note("absent", "No market-data root mounted."),
            caveats=(
                Caveat(
                    code="regime_unknown",
                    severity="warning",
                    message="No regime can be computed without bars. Any strategy "
                    "gated on regime cannot be evaluated in this deployment.",
                    direction="unknown",
                ),
            ),
        )
    raise ApiError(
        status.HTTP_501_NOT_IMPLEMENTED,
        "regime_engine_not_wired",
        "A market-data root is mounted but this API has no wiring to the "
        "market-state engine yet. Reporting that plainly rather than shipping a "
        "placeholder regime.",
    )


@router.get("/correlations", response_model=Envelope[dict])
def correlations(platform: PlatformDep, settings: SettingsDep) -> Envelope[dict]:
    if settings.data_root is None or not settings.data_root.exists():
        return Envelope(
            data={"instruments": [], "matrix": [], "window_bars": None, "available": False},
            source=_note(
                "absent",
                "Correlation requires aligned bar history. Nothing is mounted, so "
                "the matrix is unavailable. An empty matrix is NOT an "
                "uncorrelated book.",
            ),
            caveats=(
                Caveat(
                    code="correlation_unknown",
                    severity="critical",
                    message="Correlated-exposure limits cannot be enforced "
                    "meaningfully without a correlation estimate.",
                    affects="correlated_exposure",
                    direction="optimistic",
                ),
            ),
        )
    raise ApiError(
        status.HTTP_501_NOT_IMPLEMENTED,
        "correlation_engine_not_wired",
        "A market-data root is mounted but the cross-asset engine is not wired "
        "into this API yet.",
    )


@router.get("/data-quality", response_model=Page[DataQualityRow])
def data_quality(
    platform: PlatformDep,
    settings: SettingsDep,
    limit: int = Query(50, ge=1, le=200),
) -> Page[DataQualityRow]:
    p = Provenance.BACKTEST
    mounted = settings.data_root is not None and settings.data_root.exists()
    items = [
        DataQualityRow(
            instrument=symbol,
            available=mounted,
            quality="unknown" if not mounted else "pending",
            detail=(
                "No dataset is mounted for this instrument, so integrity has "
                "never been validated. Treat every figure derived from it as "
                "unverified."
                if not mounted
                else "Mounted; integrity report not yet wired into this API."
            ),
            bars=Figure.missing(p, unit="count"),
            gaps=Figure.missing(p, unit="count"),
            stale_runs=Figure.missing(p, unit="count"),
        )
        for symbol in instrument_registry.all_symbols()[:limit]
    ]
    return Page(
        items=items,
        total=len(items),
        limit=limit,
        source=_note(
            "live" if mounted else "absent",
            "Per-instrument data integrity status.",
        ),
    )


# =========================================================== chart overlays
#
# ``GET /api/markets/overlays/{symbol}`` is everything the chart workstation
# draws over candles, computed here so the frontend computes nothing
# (FRONTEND_OVERHAUL_PLAN.md §6, report E §7.2). Indicator series come ONLY
# from :mod:`fiboki.indicators`; regimes ONLY from :mod:`fiboki.marketstate`;
# trades, positions and signals ONLY from the paper journal the trading routes
# read. Items produced by an execution or a simulation carry that provenance;
# indicator series, regimes, calendar events and headlines are market or
# reference data, which is not a provenance class, so their ``provenance`` is
# null and ``source`` says what they are. Every item carries the
# ``dataset_version_id`` it was computed from, or null with the reason in
# ``sections``.

#: Indicators drawn on the price pane (their outputs are prices).
_PRICE_PANE_KEYS = frozenset(
    {"ichimoku", "fibonacci", "sma", "ema", "wma", "bollinger", "donchian", "keltner",
     "psar", "swing", "vwap"}
)
#: Output columns of price-pane indicators that are states or ratios, not prices.
_STATE_SUFFIXES = (
    "_dir", "_range", "_price_vs_cloud", "_chikou_above_price", "_pctb", "_width",
)
#: Always computed, whatever strategies are selected.
BASELINE_INDICATORS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("ichimoku", {}),
    ("fibonacci", {}),
    ("atr", {"period": 14}),
)
#: Bars of history before the window used to warm indicators and regimes. An
#: expanding percentile over a capped history is a rolling one, so the cap is
#: reported whenever it binds.
OVERLAY_HISTORY_BARS = 20_000
OVERLAY_MAX_BARS = 5_000


class OverlaySource(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    detail: str = ""


class SignalOverlay(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "signal"
    t: datetime
    side: str = Field(pattern="^(long|short|unknown)$")
    strategy_id: str
    outcome: str = Field(pattern="^(accepted|blocked)$")
    reason: str
    signal_id: str
    session_id: str
    timeframe: str
    requested_price: Figure
    provenance: Provenance
    dataset_version_id: str | None
    source: OverlaySource


class FillOverlay(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "fill"
    t: datetime
    role: str = Field(pattern="^(entry|exit)$")
    side: str = Field(pattern="^(buy|sell)$")
    price: Figure
    trade_id: str
    strategy_id: str
    session_id: str
    exit_reason: str | None = None
    net_pnl: Figure | None = None
    provenance: Provenance
    dataset_version_id: str | None
    source: OverlaySource


class LevelOverlay(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: str = "level"
    role: str = Field(pattern="^(entry|stop|target)$")
    price: Figure
    from_: datetime = Field(alias="from")
    to: datetime | None
    position_id: str
    strategy_id: str
    provenance: Provenance
    dataset_version_id: str | None
    source: OverlaySource


class RegimeOverlay(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: str = "regime"
    from_: datetime = Field(alias="from")
    to: datetime
    label: str = Field(pattern="^(trend|range|stress|unknown)$")
    regime_key: str
    axes: dict[str, str]
    provenance: Provenance | None = None
    dataset_version_id: str | None
    classifier_fingerprint: str
    source: OverlaySource


class SeriesOverlay(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "series"
    pane: str
    name: str
    indicator_id: str
    indicator_key: str
    params: dict[str, Any]
    #: True for chart-only series that are NOT causal (the displayed chikou
    #: span). Never fed to a strategy; drawn for convention only.
    display_only: bool = False
    points: tuple[SeriesPoint, ...]
    provenance: Provenance | None = None
    dataset_version_id: str | None
    source: OverlaySource


class EventOverlay(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "event"
    t: datetime
    window_end: datetime | None
    time_known: bool
    currency: str
    name: str
    impact: str
    event_id: str
    source_url: str
    provenance: Provenance | None = None
    dataset_version_id: str | None
    source: OverlaySource


class HeadlineOverlay(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "headline"
    #: ``observed_at``: when OUR recorder first saw it, the only honest time.
    t: datetime
    vendor_published_at: datetime | None
    news_source: str
    currency: str | None
    title: str
    url: str
    provenance: Provenance | None = None
    dataset_version_id: str | None
    source: OverlaySource


class SectionStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    detail: str


class OverlayView(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    symbol: str
    timeframe: str
    window_from: datetime | None
    window_to: datetime | None
    bars_dataset_version_id: str | None
    signals: list[SignalOverlay]
    fills: list[FillOverlay]
    levels: list[LevelOverlay]
    regimes: list[RegimeOverlay]
    series: list[SeriesOverlay]
    events: list[EventOverlay]
    headlines: list[HeadlineOverlay]
    #: Per section: available, and why not when it is not.
    sections: dict[str, SectionStatus]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _in(t: datetime, lo: datetime | None, hi: datetime | None) -> bool:
    return (lo is None or t >= lo) and (hi is None or t <= hi)


def _registry_key(indicator: Any) -> str:
    from fiboki.indicators import INDICATORS

    for key, cls in INDICATORS.items():
        if type(indicator) is cls:
            return key
    return type(indicator).__name__.lower()


def _pane_for(key: str, indicator: Any, column: str) -> str:
    if key in _PRICE_PANE_KEYS:
        return f"state:{indicator.name}" if column.endswith(_STATE_SUFFIXES) else "price"
    return indicator.name


def _overlay_indicators(
    platform: Any, strategy_ids: list[str] | None
) -> tuple[list[tuple[str, Any]], list[str]]:
    """Baseline plus the indicators the selected seed strategies compile to."""
    from fiboki.indicators import create
    from fiboki.strategy.compiler import compile_strategy

    chosen: dict[str, tuple[str, Any]] = {}
    notes: list[str] = []
    for key, params in BASELINE_INDICATORS:
        ind = create(key, params)
        chosen.setdefault(ind.name, (key, ind))
    for doc in platform.strategy_documents():
        if strategy_ids is not None and doc.strategy_id not in strategy_ids:
            continue
        try:
            compiled = compile_strategy(doc.bind_defaults())
        except Exception as exc:
            notes.append(f"{doc.strategy_id}: not compiled ({type(exc).__name__})")
            continue
        for ind in compiled.indicators:
            chosen.setdefault(ind.name, (_registry_key(ind), ind))
    return [chosen[name] for name in sorted(chosen)], notes


def _points(series: Any) -> tuple[SeriesPoint, ...]:
    out: list[SeriesPoint] = []
    for stamp, value in series.items():
        v = None if value is None or value != value else float(value)
        out.append(SeriesPoint(t=stamp.to_pydatetime(), v=v))
    return tuple(out)


def _regime_label(vector: Any) -> str:
    """Coarse ribbon label from the five-axis vector. The axes travel too.

    ``unknown`` whenever direction or volatility is unknown (the vector's own
    ``is_known``), ``stress`` when the stress axis says stressed, ``trend`` for
    a trending persistence or a strong direction, ``range`` otherwise.
    """
    if not vector.is_known:
        return "unknown"
    if vector.stress.value == "stressed":
        return "stress"
    if vector.persistence.value == "trending" or vector.direction.value in (
        "strong_up",
        "strong_down",
    ):
        return "trend"
    return "range"


def _file_digest(path: Any) -> str | None:
    import hashlib

    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:24]
    except OSError:
        return None


def _headlines(
    path: Any, lo: datetime, hi: datetime, currencies: set[str], limit: int = 200
) -> list[dict[str, Any]]:
    """Headlines OBSERVED in ``[lo, hi]``, newest ``limit``, read-only.

    Opens the store with ``mode=ro``: ``HeadlineStore()`` runs DDL on open and
    this route must not write. Central-bank sources are narrowed to the
    instrument's currencies; vendor sources pass through, as
    ``HeadlineStore.query`` does with ``currencies_hint``.
    """
    import sqlite3

    from fiboki.data.news.store import CURRENCY_BY_SOURCE, to_utc_text

    banks_for = {s.value: c for s, c in CURRENCY_BY_SOURCE.items()}
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0) as conn:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='headline'"
        ).fetchone()
        if present is None:
            return []
        rows = conn.execute(
            "SELECT source, url, title, vendor_published_at, observed_at FROM headline "
            "WHERE observed_at >= ? AND observed_at <= ? ORDER BY observed_at DESC, id DESC",
            (to_utc_text(lo), to_utc_text(hi)),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for source, url, title, vendor, observed in rows:
        bank_ccy = banks_for.get(source)
        if bank_ccy is not None and bank_ccy not in currencies:
            continue
        out.append(
            {
                "source": source,
                "url": url,
                "title": title,
                "currency": bank_ccy,
                "vendor_published_at": vendor,
                "observed_at": observed,
            }
        )
        if len(out) >= limit:
            break
    return out[::-1]


def _parse_news_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


@router.get("/overlays/{symbol}", response_model=Envelope[OverlayView])
def overlays(
    symbol: str,
    platform: PlatformDep,
    settings: SettingsDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    strategy_id: Annotated[list[str] | None, Query()] = None,
    timeframe: str = Query("H1"),
    limit: int = Query(500, ge=1, le=OVERLAY_MAX_BARS),
    include_indicators: bool = Query(True),
) -> Envelope[OverlayView]:
    """Signals, fills, levels, regimes, indicator series, events and headlines.

    The window is the last ``limit`` bars at or before ``to`` and at or after
    ``from``. With no bar store mounted the bar-derived sections are reported
    unavailable and the journal, calendar and headline sections use
    ``[from, to]`` as given.
    """
    import pandas as pd

    from fiboki.core.enums import Timeframe

    if not instrument_registry.exists(symbol):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_instrument",
            f"{symbol!r} is not in the instrument registry.",
        )
    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "unknown_timeframe",
            f"{timeframe!r} is not a recognised timeframe.",
        ) from exc
    known = set(platform.strategy_ids)
    if strategy_id:
        missing = sorted(set(strategy_id) - known)
        if missing:
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                "unknown_strategy",
                f"No seed strategy {missing} is registered.",
            )
    lo, hi = _utc(from_), _utc(to)
    if lo is not None and hi is not None and lo > hi:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, "invalid_window", "'from' is after 'to'."
        )

    sections: dict[str, SectionStatus] = {}
    caveats: list[Caveat] = []
    signals: list[SignalOverlay] = []
    fills: list[FillOverlay] = []
    levels: list[LevelOverlay] = []
    regimes_out: list[RegimeOverlay] = []
    series_out: list[SeriesOverlay] = []
    events_out: list[EventOverlay] = []
    headlines_out: list[HeadlineOverlay] = []

    # ---- bars: the window and everything computed from it -------------
    bars_version: str | None = None
    history = None
    window = None
    mounted = settings.data_root is not None and settings.data_root.exists()
    if not mounted:
        detail = "No market-data root is mounted; nothing bar-derived can be computed."
        for name in ("series", "regimes"):
            sections[name] = SectionStatus(available=False, detail=detail)
    else:
        from fiboki.data.store import DataStore

        try:
            with DataStore(settings.data_root) as store:
                frame, version = store.read_latest(
                    instrument=symbol, timeframe=tf, end=pd.Timestamp(hi) if hi else None
                )
            bars_version = getattr(version, "version_id", None)
        except Exception as exc:
            detail = f"The bar dataset could not be read ({type(exc).__name__})."
            for name in ("series", "regimes"):
                sections[name] = SectionStatus(available=False, detail=detail)
        else:
            sel = frame if lo is None else frame[frame.index >= pd.Timestamp(lo)]
            window = sel.tail(limit)
            if window.empty:
                detail = "The dataset has no bars in the requested window."
                for name in ("series", "regimes"):
                    sections[name] = SectionStatus(available=False, detail=detail)
                window = None
            else:
                upto = frame[frame.index <= window.index[-1]]
                history = upto.tail(len(window) + OVERLAY_HISTORY_BARS)
                if len(history) < len(upto):
                    caveats.append(
                        Caveat(
                            code="overlay_history_capped",
                            severity="info",
                            message=(
                                f"Indicators and regimes were computed over the last "
                                f"{len(history)} bars, not the full {len(upto)}. "
                                "Recursive indicators converge; expanding-percentile "
                                "regime inputs become rolling ones when capped."
                            ),
                            affects="series",
                            direction="unknown",
                        )
                    )

    win_lo = window.index[0].to_pydatetime() if window is not None else lo
    win_hi = window.index[-1].to_pydatetime() if window is not None else hi
    if win_lo is None and win_hi is None:
        win_hi = datetime.now(tz=UTC)
        win_lo = win_hi - pd.Timedelta(days=30).to_pytimedelta()
        caveats.append(
            Caveat(
                code="overlay_default_window",
                severity="info",
                message="No bars and no from/to: the last 30 days were used.",
                affects="window",
                direction="unknown",
            )
        )

    bar_source = OverlaySource(
        kind="bar_store",
        detail=f"Canonical bar store, dataset {bars_version}; computed by fiboki.indicators.",
    )
    if history is not None and window is not None:
        from fiboki.indicators import VolumeUnavailableError, chikou_span_display

        if include_indicators:
            chosen, notes = _overlay_indicators(platform, strategy_id)
            skipped: list[str] = list(notes)
            for key, ind in chosen:
                try:
                    computed = ind.compute(history)
                except VolumeUnavailableError:
                    skipped.append(f"{ind.name}: no volume in this dataset")
                    continue
                except Exception as exc:
                    skipped.append(f"{ind.name}: {type(exc).__name__}")
                    continue
                visible = computed.loc[window.index]
                for column in ind.output_columns:
                    series_out.append(
                        SeriesOverlay(
                            pane=_pane_for(key, ind, column),
                            name=column,
                            indicator_id=ind.name,
                            indicator_key=key,
                            params=ind.params(),
                            points=_points(visible[column]),
                            dataset_version_id=bars_version,
                            source=bar_source,
                        )
                    )
                if key == "ichimoku":
                    chikou = chikou_span_display(history, ind.chikou_shift).loc[window.index]
                    series_out.append(
                        SeriesOverlay(
                            pane="price",
                            name=f"{ind.name}_chikou_span_display",
                            indicator_id=ind.name,
                            indicator_key=key,
                            params=ind.params(),
                            display_only=True,
                            points=_points(chikou),
                            dataset_version_id=bars_version,
                            source=OverlaySource(
                                kind="bar_store",
                                detail="Chart-only lagging span (close shifted back); "
                                "NOT causal, never read by a strategy.",
                            ),
                        )
                    )
            sections["series"] = SectionStatus(
                available=True,
                detail=f"{len(series_out)} series from {len(chosen)} indicator(s)"
                + (f"; skipped: {', '.join(skipped)}" if skipped else "")
                + ". The forward-projected cloud is not served: it has no bar times.",
            )
        else:
            sections["series"] = SectionStatus(available=False, detail="Not requested.")

        try:
            from fiboki.marketstate import FeatureEngine
            from fiboki.marketstate.regime import RegimeClassifier, RegimeVector

            ohlc = history[[c for c in ("open", "high", "low", "close", "volume") if c in history]]
            engine = FeatureEngine(timeframe=tf, instrument=symbol)
            classifier = RegimeClassifier()
            if len(ohlc) <= engine.warmup:
                raise ValueError(
                    f"{len(ohlc)} bars is below the feature engine's {engine.warmup}"
                )
            regime = classifier.classify(engine.compute(ohlc))
            keys = regime.frame["regime_key"].reindex(window.index)
            run_start = None
            run_key = None
            stamps = list(window.index)
            for i, stamp in enumerate(stamps):
                key_now = str(keys.iloc[i])
                if run_key is None:
                    run_start, run_key = stamp, key_now
                    continue
                if key_now != run_key:
                    vector = RegimeVector.from_key(run_key)
                    regimes_out.append(
                        RegimeOverlay(
                            from_=run_start.to_pydatetime(),
                            to=stamp.to_pydatetime(),
                            label=_regime_label(vector),
                            regime_key=run_key,
                            axes=vector.to_dict(),
                            dataset_version_id=bars_version,
                            classifier_fingerprint=regime.fingerprint,
                            source=OverlaySource(kind="marketstate", detail="RegimeClassifier"),
                        )
                    )
                    run_start, run_key = stamp, key_now
            if run_key is not None and run_start is not None:
                vector = RegimeVector.from_key(run_key)
                regimes_out.append(
                    RegimeOverlay(
                        from_=run_start.to_pydatetime(),
                        to=stamps[-1].to_pydatetime(),
                        label=_regime_label(vector),
                        regime_key=run_key,
                        axes=vector.to_dict(),
                        dataset_version_id=bars_version,
                        classifier_fingerprint=regime.fingerprint,
                        source=OverlaySource(kind="marketstate", detail="RegimeClassifier"),
                    )
                )
            sections["regimes"] = SectionStatus(
                available=True,
                detail=f"{len(regimes_out)} segment(s); classifier warm-up "
                f"{regime.warmup} bars. 'unknown' is drawn hatched, never as range.",
            )
        except Exception as exc:
            sections["regimes"] = SectionStatus(
                available=False,
                detail=f"Regime classification unavailable: {type(exc).__name__}: {exc}",
            )

    # ---- the paper journal: fills, levels, signals ------------------------
    journal = platform.journal
    if journal is None or not journal.sessions:
        detail = (
            f"No paper journal at {platform.paper_root}; no fills, levels or signals "
            "exist. The seed fixture is never drawn on a chart."
            if journal is None
            else "A paper journal exists but none of its sessions could be read."
        )
        for name in ("fills", "levels", "signals"):
            sections[name] = SectionStatus(available=False, detail=detail)
    else:
        from fiboki.api.provenance import figure as make_figure

        sessions = [s for s in journal.sessions if s.instrument.upper() == symbol.upper()]
        for session in sessions:
            summary = platform.session_summary(session.session_id) or {}
            dataset_id = summary.get("dataset_version_id")
            charged = platform.session_charged_costs(session.session_id)
            src = OverlaySource(
                kind="paper_journal",
                detail=f"session {session.session_id} ({session.timeframe}), "
                f"replayed dataset {dataset_id}",
            )
            p = session.provenance
            for trade in session.trades:
                buy_first = getattr(trade.direction, "value", trade.direction) == "long"
                if _in(trade.entry_time, win_lo, win_hi):
                    fills.append(
                        FillOverlay(
                            t=trade.entry_time,
                            role="entry",
                            side="buy" if buy_first else "sell",
                            price=Figure(value=trade.entry_price, provenance=p, as_of=trade.as_of),
                            trade_id=trade.trade_id,
                            strategy_id=trade.strategy_id,
                            session_id=session.session_id,
                            provenance=p,
                            dataset_version_id=dataset_id,
                            source=src,
                        )
                    )
                if _in(trade.exit_time, win_lo, win_hi):
                    fills.append(
                        FillOverlay(
                            t=trade.exit_time,
                            role="exit",
                            side="sell" if buy_first else "buy",
                            price=Figure(value=trade.exit_price, provenance=p, as_of=trade.as_of),
                            trade_id=trade.trade_id,
                            strategy_id=trade.strategy_id,
                            session_id=session.session_id,
                            exit_reason=getattr(trade.exit_reason, "value", trade.exit_reason),
                            net_pnl=make_figure(
                                trade.net_pnl,
                                p,
                                settings=settings,
                                unit=trade.account_ccy,
                                as_of=trade.as_of,
                                affects="net_pnl",
                                charged=charged,
                            ),
                            provenance=p,
                            dataset_version_id=dataset_id,
                            source=src,
                        )
                    )
            for pos in session.positions:
                if win_hi is not None and pos.entry_time > win_hi:
                    continue
                buy = getattr(pos.direction, "value", pos.direction) == "long"
                if _in(pos.entry_time, win_lo, win_hi):
                    fills.append(
                        FillOverlay(
                            t=pos.entry_time,
                            role="entry",
                            side="buy" if buy else "sell",
                            price=Figure(value=pos.entry_price, provenance=p, as_of=pos.as_of),
                            trade_id=pos.position_id,
                            strategy_id=pos.strategy_id,
                            session_id=session.session_id,
                            provenance=p,
                            dataset_version_id=dataset_id,
                            source=src,
                        )
                    )
                for role, price in (
                    ("entry", pos.entry_price),
                    ("stop", pos.stop_loss),
                    ("target", pos.take_profit),
                ):
                    if price is None:
                        continue
                    levels.append(
                        LevelOverlay(
                            role=role,
                            price=Figure(value=price, provenance=p, as_of=pos.as_of),
                            from_=pos.entry_time,
                            to=None,
                            position_id=pos.position_id,
                            strategy_id=pos.strategy_id,
                            provenance=p,
                            dataset_version_id=dataset_id,
                            source=src,
                        )
                    )
            candidates = [
                (t.entry_time, t.size, getattr(t.direction, "value", t.direction))
                for t in session.trades
            ] + [
                (q.entry_time, q.size, getattr(q.direction, "value", q.direction))
                for q in session.positions
            ]
            for row in platform.session_telemetry(session.session_id):
                decided = _utc(_parse_iso(row.get("decided_at")))
                if decided is None or not _in(decided, win_lo, win_hi):
                    continue
                if str(row.get("instrument", symbol)).upper() != symbol.upper():
                    continue
                decision = row.get("decision") or {}
                extra = row.get("extra") or {}
                size = row.get("requested_size")
                side = "unknown"
                matches = sorted(
                    (c for c in candidates if c[0] >= decided and size is not None
                     and abs(c[1] - float(size)) < 1e-9),
                    key=lambda c: c[0],
                )
                if matches and extra.get("request_kind", "open") == "open":
                    side = str(matches[0][2])
                allowed = bool(decision.get("allowed"))
                reasons = [str(r) for r in decision.get("reasons") or []]
                requested = extra.get("requested_price")
                signals.append(
                    SignalOverlay(
                        t=decided,
                        side=side if side in ("long", "short") else "unknown",
                        strategy_id=str(row.get("strategy_id") or session.strategy_id),
                        outcome="accepted" if allowed else "blocked",
                        reason=", ".join(reasons)
                        if reasons
                        else f"{extra.get('request_kind', 'open')} accepted by the gateway",
                        signal_id=str(row.get("signal_id") or ""),
                        session_id=session.session_id,
                        timeframe=session.timeframe,
                        requested_price=Figure(value=float(requested), provenance=p)
                        if isinstance(requested, int | float)
                        else Figure.missing(p, reason="No requested price was recorded."),
                        provenance=p,
                        dataset_version_id=dataset_id,
                        source=src,
                    )
                )
        count = len(sessions)
        sections["fills"] = SectionStatus(
            available=True,
            detail=f"{len(fills)} fill(s) from {count} paper session(s) on {symbol}.",
        )
        sections["levels"] = SectionStatus(
            available=True,
            detail="Entry, stop and target of positions open at the end of each session.",
        )
        sections["signals"] = SectionStatus(
            available=True,
            detail="Gateway attempts from each session's telemetry.jsonl; side is matched "
            "to the fill it produced and is 'unknown' when none was.",
        )
    sections["backtest_fills"] = SectionStatus(
        available=False,
        detail="The research ledger records experiment metrics, not per-trade fills, so "
        "no backtest trade can be drawn.",
    )
    sections["stop_moves"] = SectionStatus(
        available=False,
        detail="The paper journal persists each open position's current stop but not "
        "when it moved; a stop-move history would have to invent times.",
    )

    # ---- calendar ------------------------------------------------------------
    try:
        from fiboki.marketstate.calendar import (
            OFFICIAL_EVENTS_FIXTURE,
            instrument_currencies,
            load_official_calendar,
        )

        currencies = list(instrument_currencies(symbol))
        calendar = load_official_calendar()
        cal_version = _file_digest(OFFICIAL_EVENTS_FIXTURE)
        if win_lo is not None and win_hi is not None:
            for event in calendar.events_between(
                pd.Timestamp(win_lo), pd.Timestamp(win_hi), currencies=currencies
            ):
                events_out.append(
                    EventOverlay(
                        t=event.event_time.to_pydatetime(),
                        window_end=event.window_end.to_pydatetime()
                        if event.window_end is not None
                        else None,
                        time_known=event.time_known,
                        currency=event.currency,
                        name=event.name,
                        impact=event.impact.value,
                        event_id=event.event_id,
                        source_url=event.source_url,
                        dataset_version_id=cal_version,
                        source=OverlaySource(
                            kind="official_calendar", detail=f"{event.source}"
                        ),
                    )
                )
        coverage = calendar.coverage()
        uncovered = sorted(set(currencies) - set(coverage.currencies))
        detail = f"{len(events_out)} scheduled event(s) for {', '.join(currencies)}."
        if uncovered:
            detail += f" No official events are recorded for {', '.join(uncovered)}."
        sections["events"] = SectionStatus(available=True, detail=detail)
    except Exception as exc:
        sections["events"] = SectionStatus(
            available=False, detail=f"Calendar unavailable: {type(exc).__name__}"
        )

    # ---- headlines -----------------------------------------------------------
    from fiboki.data.news import default_store_path

    news_path = default_store_path(settings.state_dir)
    if not news_path.exists():
        sections["headlines"] = SectionStatus(
            available=False,
            detail="No headline store has been recorded on this deployment.",
        )
    elif win_lo is None or win_hi is None:
        sections["headlines"] = SectionStatus(available=False, detail="No window.")
    else:
        try:
            from fiboki.marketstate.calendar import instrument_currencies

            rows = _headlines(news_path, win_lo, win_hi, set(instrument_currencies(symbol)))
            for row in rows:
                observed = _parse_news_time(row["observed_at"])
                if observed is None:
                    continue
                headlines_out.append(
                    HeadlineOverlay(
                        t=observed,
                        vendor_published_at=_parse_news_time(row["vendor_published_at"]),
                        news_source=row["source"],
                        currency=row["currency"],
                        title=row["title"],
                        url=row["url"],
                        dataset_version_id=None,
                        source=OverlaySource(
                            kind="news_store",
                            detail="Append-only headline store, read-only; the store "
                            "has no dataset version, the point-in-time key is observed_at.",
                        ),
                    )
                )
            sections["headlines"] = SectionStatus(
                available=True, detail=f"{len(headlines_out)} headline(s) observed in window."
            )
        except Exception as exc:
            sections["headlines"] = SectionStatus(
                available=False, detail=f"Headline store unreadable: {type(exc).__name__}"
            )

    signals.sort(key=lambda s: (s.t, s.signal_id))
    fills.sort(key=lambda f: (f.t, f.trade_id, f.role))
    view = OverlayView(
        symbol=symbol,
        timeframe=tf.value,
        window_from=win_lo,
        window_to=win_hi,
        bars_dataset_version_id=bars_version,
        signals=signals,
        fills=fills,
        levels=levels,
        regimes=regimes_out,
        series=series_out,
        events=events_out,
        headlines=headlines_out,
        sections=sections,
    )
    return Envelope(
        data=view,
        source=_note(
            "mixed",
            "Bar store (series, regimes), paper journal (signals, fills, levels), "
            "official calendar and headline store; computed now.",
        ),
        caveats=tuple(caveats),
    )


def _parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
