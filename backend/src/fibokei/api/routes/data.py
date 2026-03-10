"""Data management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from fibokei.api.auth import TokenData, get_current_user
from fibokei.data.manifest import generate_manifest, load_manifest
from fibokei.data.paths import get_canonical_dir
from fibokei.data.providers.base import ProviderID
from fibokei.data.providers.registry import get_provider, list_providers, load_canonical
from fibokei.data.providers.symbol_map import list_mapped_symbols, provider_has_symbol

router = APIRouter(tags=["data"])

_CANONICAL_DIR = get_canonical_dir()
_cached_manifest: dict | None = None


class DatasetInfo(BaseModel):
    provider: str
    symbol: str
    timeframe: str
    format: str
    size_bytes: int
    row_count: int | None = None


class ProviderInfo(BaseModel):
    id: str
    symbols: list[str]


class IngestRequest(BaseModel):
    provider: str = Field(..., description="histdata or dukascopy")
    symbols: list[str] = Field(..., min_length=1)


class IngestResult(BaseModel):
    symbol: str
    status: str
    timeframes: int = 0
    total_bars: int = 0
    error: str | None = None


@router.get("/data/providers", response_model=list[ProviderInfo])
def get_providers(user: TokenData = Depends(get_current_user)):
    """List available data providers and their supported symbols."""
    return [
        ProviderInfo(id=pid.value, symbols=list_mapped_symbols(pid))
        for pid in list_providers()
    ]


@router.get("/data/datasets", response_model=list[DatasetInfo])
def get_datasets(
    provider: str | None = Query(None),
    symbol: str | None = Query(None),
    user: TokenData = Depends(get_current_user),
):
    """List available canonical datasets, optionally filtered."""
    if not _CANONICAL_DIR.exists():
        return []

    datasets = []
    for pdir in sorted(_CANONICAL_DIR.iterdir()):
        if not pdir.is_dir():
            continue
        if provider and pdir.name != provider.lower():
            continue
        for sdir in sorted(pdir.iterdir()):
            if not sdir.is_dir():
                continue
            if symbol and sdir.name != symbol.lower():
                continue
            for f in sorted(sdir.iterdir()):
                if f.suffix not in (".parquet", ".csv"):
                    continue
                parts = f.stem.split("_")
                tf = parts[-1].upper() if len(parts) >= 2 else "?"
                datasets.append(DatasetInfo(
                    provider=pdir.name,
                    symbol=sdir.name.upper(),
                    timeframe=tf,
                    format=f.suffix.lstrip("."),
                    size_bytes=f.stat().st_size,
                ))

    return datasets


@router.post("/data/ingest", response_model=list[IngestResult])
def ingest_data(
    req: IngestRequest,
    user: TokenData = Depends(get_current_user),
):
    """Ingest raw data from a provider into the canonical store."""
    try:
        provider_id = ProviderID(req.provider.lower())
    except ValueError:
        raise HTTPException(400, f"Unknown provider: {req.provider}")

    prov = get_provider(provider_id)
    results = []

    for symbol in req.symbols:
        symbol = symbol.upper()
        if not provider_has_symbol(symbol, provider_id):
            results.append(IngestResult(
                symbol=symbol, status="skipped",
                error=f"Not mapped for {req.provider}",
            ))
            continue

        try:
            if hasattr(prov, "ingest_all_timeframes"):
                tfs = prov.ingest_all_timeframes(symbol, data_dir=_CANONICAL_DIR)
                total_bars = sum(m.row_count for _, m in tfs)
                results.append(IngestResult(
                    symbol=symbol, status="ok",
                    timeframes=len(tfs), total_bars=total_bars,
                ))
            else:
                df, meta = prov.ingest(symbol, "M1", data_dir=_CANONICAL_DIR)
                results.append(IngestResult(
                    symbol=symbol, status="ok",
                    timeframes=1, total_bars=meta.row_count,
                ))
        except Exception as e:
            results.append(IngestResult(
                symbol=symbol, status="error", error=str(e),
            ))

    return results


@router.get("/data/check/{symbol}/{timeframe}")
def check_data_availability(
    symbol: str,
    timeframe: str,
    provider: str | None = Query(None),
    user: TokenData = Depends(get_current_user),
):
    """Check if canonical data exists for a symbol/timeframe combo."""
    df = load_canonical(symbol.upper(), timeframe.upper(), provider=provider)
    if df is None:
        return {"available": False, "rows": 0}
    return {
        "available": True,
        "rows": len(df),
        "start": str(df.index.min()),
        "end": str(df.index.max()),
    }


@router.get("/data/manifest")
def get_manifest(user: TokenData = Depends(get_current_user)):
    """Return the data manifest listing all available canonical datasets."""
    global _cached_manifest
    if _cached_manifest is None:
        _cached_manifest = load_manifest(_CANONICAL_DIR)
    if _cached_manifest is None:
        raise HTTPException(status_code=404, detail="No manifest found. Run 'fibokei manifest' to generate.")
    return _cached_manifest


@router.post("/data/manifest/refresh")
def refresh_manifest(user: TokenData = Depends(get_current_user)):
    """Regenerate and reload the manifest from disk."""
    global _cached_manifest
    _cached_manifest = generate_manifest(_CANONICAL_DIR)
    return {"status": "ok", "datasets": len(_cached_manifest.get("datasets", []))}
