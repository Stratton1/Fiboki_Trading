"""MARKETS: instruments, bars, regimes, correlations, data quality.

The instrument universe is real (:mod:`fiboki.core.instruments`). Bars, regimes
and correlations require a mounted market-data root; when there is none this
router returns an explicit unavailable state with a 503 or an empty page whose
``source.kind`` is ``absent``. It never synthesises a chart that looks like a
measurement.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict

from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure
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
    return Envelope(
        data={
            "symbol": symbol,
            "timeframe": timeframe,
            "dataset_version_id": getattr(version, "version_id", ""),
            "bars": [
                {
                    "t": index.isoformat(),
                    "o": float(row.open),
                    "h": float(row.high),
                    "l": float(row.low),
                    "c": float(row.close),
                }
                for index, row in tail.iterrows()
            ],
        },
        source=_note("live", "Canonical bar store."),
    )


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
