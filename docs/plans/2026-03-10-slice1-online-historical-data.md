# Slice 1: Online Historical Data Foundation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the full canonical dataset (60 instruments × 6 timeframes) accessible in production via a Railway persistent volume, with manifest indexing, paginated market data, LRU caching, observable fallback, and dynamic instrument availability.

**Architecture:** Railway persistent volume mounted at `/data`. Existing `FIBOKEI_DATA_DIR` env var points to it. `load_canonical()` search order unchanged. New manifest.json indexes all datasets. Market data endpoint gains pagination, vectorized serialization, and in-memory DataFrame cache.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pandas, parquet, argparse CLI

---

### Task 1: DataFrame LRU Cache

**Files:**
- Create: `backend/src/fibokei/data/cache.py`
- Test: `backend/tests/test_data_cache.py`

**Step 1: Write the test**

```python
# backend/tests/test_data_cache.py
"""Tests for the DataFrame LRU cache."""

import pandas as pd
import time
from fibokei.data.cache import DataFrameCache


def test_cache_hit():
    cache = DataFrameCache(max_size=10, ttl_seconds=60)
    df = pd.DataFrame({"close": [1.0, 2.0]})
    cache.put("EURUSD", "H1", df)
    result = cache.get("EURUSD", "H1")
    assert result is not None
    assert len(result) == 2


def test_cache_miss():
    cache = DataFrameCache(max_size=10, ttl_seconds=60)
    assert cache.get("EURUSD", "H1") is None


def test_cache_expiry():
    cache = DataFrameCache(max_size=10, ttl_seconds=0.1)
    df = pd.DataFrame({"close": [1.0]})
    cache.put("EURUSD", "H1", df)
    time.sleep(0.2)
    assert cache.get("EURUSD", "H1") is None


def test_cache_eviction():
    cache = DataFrameCache(max_size=2, ttl_seconds=60)
    cache.put("A", "H1", pd.DataFrame({"x": [1]}))
    cache.put("B", "H1", pd.DataFrame({"x": [2]}))
    cache.put("C", "H1", pd.DataFrame({"x": [3]}))
    # A should be evicted (oldest)
    assert cache.get("A", "H1") is None
    assert cache.get("B", "H1") is not None
    assert cache.get("C", "H1") is not None


def test_cache_invalidate():
    cache = DataFrameCache(max_size=10, ttl_seconds=60)
    cache.put("EURUSD", "H1", pd.DataFrame({"x": [1]}))
    cache.invalidate_all()
    assert cache.get("EURUSD", "H1") is None


def test_cache_stats():
    cache = DataFrameCache(max_size=10, ttl_seconds=60)
    cache.put("EURUSD", "H1", pd.DataFrame({"x": [1]}))
    cache.get("EURUSD", "H1")  # hit
    cache.get("GBPUSD", "H1")  # miss
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["size"] == 1
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_data_cache.py -v`
Expected: FAIL — module not found

**Step 3: Implement the cache**

```python
# backend/src/fibokei/data/cache.py
"""In-memory LRU cache for loaded DataFrames."""

import time
from collections import OrderedDict

import pandas as pd


class DataFrameCache:
    """Process-local LRU cache for parquet DataFrames.

    Key: (symbol, timeframe). Entries expire after ttl_seconds.
    Max entries controlled by max_size; oldest evicted on overflow.
    """

    def __init__(self, max_size: int = 50, ttl_seconds: float = 300.0):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._store: OrderedDict[tuple[str, str], tuple[float, pd.DataFrame]] = (
            OrderedDict()
        )
        self._hits = 0
        self._misses = 0

    def get(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        key = (symbol.upper(), timeframe.upper())
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        ts, df = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            self._misses += 1
            return None
        self._store.move_to_end(key)
        self._hits += 1
        return df

    def put(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        key = (symbol.upper(), timeframe.upper())
        self._store[key] = (time.monotonic(), df)
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def invalidate_all(self) -> None:
        self._store.clear()

    def stats(self) -> dict:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._store),
            "max_size": self._max_size,
            "ttl_seconds": self._ttl,
        }
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_data_cache.py -v`
Expected: All 6 PASS

**Step 5: Commit**

```bash
git add backend/src/fibokei/data/cache.py backend/tests/test_data_cache.py
git commit -m "feat: add DataFrame LRU cache for loaded parquet datasets"
```

---

### Task 2: Data Manifest Generator

**Files:**
- Create: `backend/src/fibokei/data/manifest.py`
- Test: `backend/tests/test_manifest.py`

**Step 1: Write the test**

```python
# backend/tests/test_manifest.py
"""Tests for data manifest generation and loading."""

import json
import tempfile
from pathlib import Path

import pandas as pd

from fibokei.data.manifest import generate_manifest, load_manifest


def _create_test_parquet(path: Path, bars: int = 100):
    """Create a minimal parquet file for testing."""
    import numpy as np

    dates = pd.date_range("2024-01-01", periods=bars, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": np.random.uniform(1.0, 1.1, bars),
            "high": np.random.uniform(1.1, 1.2, bars),
            "low": np.random.uniform(0.9, 1.0, bars),
            "close": np.random.uniform(1.0, 1.1, bars),
            "volume": np.zeros(bars),
        },
        index=pd.DatetimeIndex(dates, name="timestamp"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def test_generate_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical"
        _create_test_parquet(
            canonical / "histdata" / "eurusd" / "eurusd_h1.parquet", bars=200
        )
        _create_test_parquet(
            canonical / "histdata" / "gbpusd" / "gbpusd_m15.parquet", bars=500
        )

        manifest = generate_manifest(canonical)

        assert manifest["version"] == 1
        assert len(manifest["datasets"]) == 2

        eurusd = next(d for d in manifest["datasets"] if d["symbol"] == "EURUSD")
        assert eurusd["provider"] == "histdata"
        assert eurusd["timeframe"] == "H1"
        assert eurusd["bars"] == 200
        assert eurusd["path"] == "histdata/eurusd/eurusd_h1.parquet"
        assert "checksum" in eurusd
        assert "size_bytes" in eurusd
        assert "from_date" in eurusd
        assert "to_date" in eurusd


def test_generate_manifest_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical"
        _create_test_parquet(
            canonical / "histdata" / "eurusd" / "eurusd_h1.parquet"
        )

        manifest = generate_manifest(canonical)
        manifest_path = canonical / "manifest.json"
        assert manifest_path.exists()

        loaded = json.loads(manifest_path.read_text())
        assert loaded["version"] == 1
        assert len(loaded["datasets"]) == 1


def test_load_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical"
        _create_test_parquet(
            canonical / "histdata" / "eurusd" / "eurusd_h1.parquet"
        )
        generate_manifest(canonical)

        manifest = load_manifest(canonical)
        assert manifest is not None
        assert len(manifest["datasets"]) == 1


def test_load_manifest_missing():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_manifest(Path(tmp) / "nonexistent") is None
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_manifest.py -v`
Expected: FAIL — module not found

**Step 3: Implement manifest generation**

```python
# backend/src/fibokei/data/manifest.py
"""Data manifest generation and loading.

The manifest indexes all canonical datasets with metadata including
bar counts, date ranges, file sizes, and checksums.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_MANIFEST_VERSION = 1


def generate_manifest(canonical_dir: Path) -> dict:
    """Scan canonical_dir and generate manifest.json.

    Walks the directory tree looking for .parquet and .csv files,
    reads metadata from each, and writes manifest.json to canonical_dir.
    Returns the manifest dict.
    """
    datasets = []

    if not canonical_dir.exists():
        logger.warning("Canonical directory does not exist: %s", canonical_dir)
        manifest = _build_manifest(datasets)
        return manifest

    for provider_dir in sorted(canonical_dir.iterdir()):
        if not provider_dir.is_dir() or provider_dir.name.startswith("."):
            continue
        if provider_dir.name == "manifest.json":
            continue
        provider_name = provider_dir.name

        for symbol_dir in sorted(provider_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            symbol = symbol_dir.name.upper()

            for data_file in sorted(symbol_dir.iterdir()):
                if data_file.suffix not in (".parquet", ".csv"):
                    continue

                entry = _build_dataset_entry(
                    data_file, provider_name, symbol, canonical_dir
                )
                if entry:
                    datasets.append(entry)

    manifest = _build_manifest(datasets)

    # Write to disk
    manifest_path = canonical_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    logger.info(
        "Manifest generated: %d datasets written to %s",
        len(datasets),
        manifest_path,
    )

    return manifest


def _build_manifest(datasets: list[dict]) -> dict:
    return {
        "version": _MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "fibokei manifest generate",
        "datasets": datasets,
    }


def _build_dataset_entry(
    file_path: Path, provider: str, symbol: str, canonical_dir: Path
) -> dict | None:
    """Build a manifest entry for a single data file."""
    # Extract timeframe from filename: eurusd_h1.parquet -> H1
    parts = file_path.stem.split("_")
    if len(parts) < 2:
        return None
    timeframe = parts[-1].upper()

    try:
        if file_path.suffix == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            df = pd.read_csv(file_path, parse_dates=["timestamp"], index_col="timestamp")
    except Exception as e:
        logger.warning("Failed to read %s: %s", file_path, e)
        return None

    # Ensure we have a datetime index
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            idx = pd.DatetimeIndex(df["timestamp"])
        else:
            return None

    # Compute checksum
    file_bytes = file_path.read_bytes()
    checksum = hashlib.sha256(file_bytes).hexdigest()[:16]

    rel_path = file_path.relative_to(canonical_dir)

    return {
        "symbol": symbol,
        "provider": provider,
        "timeframe": timeframe,
        "bars": len(df),
        "from_date": str(idx.min().date()) if len(idx) > 0 else None,
        "to_date": str(idx.max().date()) if len(idx) > 0 else None,
        "size_bytes": file_path.stat().st_size,
        "path": str(rel_path),
        "modified_at": datetime.fromtimestamp(
            file_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "checksum": f"sha256:{checksum}",
    }


def load_manifest(canonical_dir: Path) -> dict | None:
    """Load manifest.json from canonical_dir. Returns None if missing."""
    manifest_path = canonical_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load manifest: %s", e)
        return None
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_manifest.py -v`
Expected: All 4 PASS

**Step 5: Commit**

```bash
git add backend/src/fibokei/data/manifest.py backend/tests/test_manifest.py
git commit -m "feat: add data manifest generator with checksums and metadata"
```

---

### Task 3: CLI Manifest Command

**Files:**
- Modify: `backend/src/fibokei/cli.py`

**Step 1: Add the manifest CLI command**

After the `list_data` function (line 393), add:

```python
def generate_manifest_cmd(_args=None):
    """Generate manifest.json for the canonical data directory."""
    from fibokei.data.manifest import generate_manifest
    from fibokei.data.paths import get_canonical_dir

    canonical = get_canonical_dir()
    print(f"Scanning: {canonical}")

    manifest = generate_manifest(canonical)
    count = len(manifest["datasets"])
    print(f"Generated manifest with {count} datasets")

    if count > 0:
        # Summary by provider
        providers = {}
        for d in manifest["datasets"]:
            p = d["provider"]
            providers[p] = providers.get(p, 0) + 1
        for p, c in sorted(providers.items()):
            print(f"  {p}: {c} files")
```

Add the subparser (after the `list-data` parser at line 564):

```python
    subparsers.add_parser(
        "manifest",
        help="Generate manifest.json for canonical datasets",
    )
```

Add the dispatch case (after `list-data` at line 609):

```python
    elif args.command == "manifest":
        generate_manifest_cmd(args)
```

**Step 2: Test manually**

Run: `cd backend && python -m fibokei manifest`
Expected: "Scanning: .../data/canonical" then "Generated manifest with N datasets"

**Step 3: Commit**

```bash
git add backend/src/fibokei/cli.py
git commit -m "feat: add 'fibokei manifest' CLI command"
```

---

### Task 4: Manifest API Endpoint

**Files:**
- Modify: `backend/src/fibokei/api/routes/data.py`

**Step 1: Add manifest endpoints to the data router**

Add to the imports at the top of `data.py`:

```python
from fibokei.data.manifest import generate_manifest, load_manifest
```

Add a module-level cached manifest:

```python
_cached_manifest: dict | None = None
```

Add these endpoints:

```python
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
```

**Step 2: Test**

Run: `cd backend && python -c "from fibokei.api.routes.data import router; print('import ok')"`
Expected: "import ok"

**Step 3: Commit**

```bash
git add backend/src/fibokei/api/routes/data.py
git commit -m "feat: add GET /data/manifest and POST /data/manifest/refresh endpoints"
```

---

### Task 5: Market Data Pagination & Performance

**Files:**
- Modify: `backend/src/fibokei/api/routes/market_data.py`
- Modify: `backend/src/fibokei/api/schemas/charts.py`
- Modify: `backend/src/fibokei/data/providers/registry.py`

**Step 1: Update MarketDataResponse schema**

In `backend/src/fibokei/api/schemas/charts.py`, replace the `MarketDataResponse` class:

```python
class MarketDataResponse(BaseModel):
    instrument: str
    timeframe: str
    candles: list[CandleBar]
    ichimoku: list[IchimokuSeries]
    total_bars: int = 0
    from_date: str | None = None
    to_date: str | None = None
    source: str | None = None
```

**Step 2: Add cache integration to registry.py**

In `backend/src/fibokei/data/providers/registry.py`, add at the top:

```python
from fibokei.data.cache import DataFrameCache

_df_cache = DataFrameCache(max_size=50, ttl_seconds=300)
```

Add a new function `load_canonical_cached()` that wraps `load_canonical()`:

```python
def load_canonical_cached(
    symbol: str,
    timeframe: str,
    data_dir: str | Path | None = None,
    provider: "ProviderID | str | None" = None,
) -> tuple[pd.DataFrame | None, str]:
    """Load canonical data with caching. Returns (df, source_label)."""
    if data_dir is None and provider is None:
        cached = _df_cache.get(symbol, timeframe)
        if cached is not None:
            return cached, "cache"

    df = load_canonical(symbol, timeframe, data_dir=data_dir, provider=provider)
    if df is not None and data_dir is None and provider is None:
        _df_cache.put(symbol, timeframe, df)

    source = _determine_source(symbol, timeframe)
    return df, source


def _determine_source(symbol: str, timeframe: str) -> str:
    """Determine which data source was used (for observability)."""
    from fibokei.data.paths import get_canonical_dir, get_starter_dir

    canonical = get_canonical_dir()
    starter = get_starter_dir()
    sym_lower = symbol.lower()
    tf_lower = timeframe.lower()

    for pid in ("dukascopy", "histdata"):
        p = canonical / pid / sym_lower / f"{sym_lower}_{tf_lower}.parquet"
        if p.exists():
            return f"canonical/{pid}"
        p = p.with_suffix(".csv")
        if p.exists():
            return f"canonical/{pid}"

    for pid in ("histdata",):
        p = starter / pid / sym_lower / f"{sym_lower}_{tf_lower}.parquet"
        if p.exists():
            return "starter"

    return "fixtures"
```

**Step 3: Add observable fallback logging**

In `load_canonical()`, add logging when falling back. After the canonical search loop (before the starter search), add:

```python
    logger.info("No canonical data for %s/%s, checking starter", symbol, timeframe)
```

Before the fixtures fallback:

```python
    logger.info("No starter data for %s/%s, checking fixtures", symbol, timeframe)
```

**Step 4: Rewrite market_data.py for pagination and vectorized serialization**

Replace the `get_market_data` function in `backend/src/fibokei/api/routes/market_data.py`:

```python
from datetime import datetime

MAX_BARS = 10_000
DEFAULT_BARS = 2_000


@router.get("/market-data/{instrument}/{timeframe}", response_model=MarketDataResponse)
def get_market_data(
    instrument: str,
    timeframe: str,
    user: TokenData = Depends(get_current_user),
    limit: int = DEFAULT_BARS,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
):
    """Return OHLCV candles with precomputed Ichimoku Cloud data."""
    instrument = instrument.replace("_", "").replace("/", "").upper()

    try:
        get_instrument(instrument)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown instrument: {instrument}")

    try:
        tf_enum = Timeframe(timeframe.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    # Enforce server-side cap
    limit = min(max(limit, 1), MAX_BARS)

    # Load data via cached canonical loader
    from fibokei.data.providers.registry import load_canonical_cached

    try:
        df, source = load_canonical_cached(instrument, tf_enum.value)
    except Exception as e:
        logger.error("Failed to load data for %s/%s: %s", instrument, tf_enum.value, e)
        raise HTTPException(
            status_code=500, detail=f"Failed to load data for {instrument}/{tf_enum.value}: {e}"
        )

    if df is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data file for {instrument}/{tf_enum.value}",
        )

    # Reset index so timestamp is a column
    df = df.reset_index()

    total_bars = len(df)

    # Apply date range filtering
    if from_dt is not None:
        df = df[df["timestamp"] >= pd.Timestamp(from_dt, tz="UTC")]
    if to_dt is not None:
        df = df[df["timestamp"] <= pd.Timestamp(to_dt, tz="UTC")]

    # Apply limit (last N bars)
    if len(df) > limit:
        df = df.tail(limit)

    if "volume" not in df.columns:
        df["volume"] = 0.0

    # Vectorized candle serialization (replaces iterrows)
    candles = [
        CandleBar(
            timestamp=int(ts.timestamp() * 1000),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row.get("volume", 0.0),
        )
        for ts, row in zip(df["timestamp"], df.to_dict("records"))
    ]

    # Compute Ichimoku — graceful degradation
    ichimoku = []
    try:
        ich_df = df[["timestamp", "open", "high", "low", "close"]].copy()
        ich_df = ich_df.set_index("timestamp")
        ich = IchimokuCloud()
        ich.compute(ich_df)
        ich_df = ich_df.reset_index()

        ichimoku = [
            IchimokuSeries(
                timestamp=int(row["timestamp"].timestamp() * 1000),
                tenkan=_nan_to_none(row.get("tenkan_sen", float("nan"))),
                kijun=_nan_to_none(row.get("kijun_sen", float("nan"))),
                senkou_a=_nan_to_none(row.get("senkou_span_a", float("nan"))),
                senkou_b=_nan_to_none(row.get("senkou_span_b", float("nan"))),
                chikou=_nan_to_none(row.get("chikou_span", float("nan"))),
            )
            for row in ich_df.to_dict("records")
        ]
    except Exception as e:
        logger.error("Ichimoku computation failed for %s/%s: %s", instrument, tf_enum.value, e)

    # Response metadata
    from_date = str(df["timestamp"].iloc[0]) if len(df) > 0 else None
    to_date = str(df["timestamp"].iloc[-1]) if len(df) > 0 else None

    return MarketDataResponse(
        instrument=instrument,
        timeframe=tf_enum.value,
        candles=candles,
        ichimoku=ichimoku,
        total_bars=total_bars,
        from_date=from_date,
        to_date=to_date,
        source=source,
    )
```

Add `import pandas as pd` and `from datetime import datetime` at the top.

**Step 5: Run existing tests**

Run: `cd backend && python -m pytest -v`
Expected: All pass (no regressions)

**Step 6: Commit**

```bash
git add backend/src/fibokei/api/routes/market_data.py backend/src/fibokei/api/schemas/charts.py backend/src/fibokei/data/providers/registry.py
git commit -m "feat: add market data pagination, LRU caching, and vectorized serialization"
```

---

### Task 6: Dynamic has_canonical_data from Manifest

**Files:**
- Modify: `backend/src/fibokei/api/routes/instruments.py`

**Step 1: Read the current instruments route**

Check `backend/src/fibokei/api/routes/instruments.py` for the `GET /instruments` endpoint.

**Step 2: Add manifest-based availability**

Import the manifest loader and check dataset availability dynamically:

```python
from fibokei.data.manifest import load_manifest
from fibokei.data.paths import get_canonical_dir

def _symbols_with_data() -> set[str]:
    """Return set of symbols that have at least one dataset in the manifest."""
    manifest = load_manifest(get_canonical_dir())
    if manifest is None:
        return set()
    return {d["symbol"].upper() for d in manifest.get("datasets", [])}
```

In the `GET /instruments` endpoint, override `has_canonical_data` dynamically:

```python
    symbols_with_data = _symbols_with_data()
    results = []
    for inst in instruments:
        has_data = inst.symbol in symbols_with_data if symbols_with_data else inst.has_canonical_data
        results.append(InstrumentResponse(
            symbol=inst.symbol,
            name=inst.name,
            asset_class=inst.asset_class.value,
            has_canonical_data=has_data,
        ))
    return results
```

**Step 3: Commit**

```bash
git add backend/src/fibokei/api/routes/instruments.py
git commit -m "feat: derive has_canonical_data dynamically from manifest"
```

---

### Task 7: System Status Data Source Observability

**Files:**
- Modify: `backend/src/fibokei/api/routes/system.py`

**Step 1: Add data_source to system status**

In the `GET /system/status` endpoint, add:

```python
from fibokei.data.paths import get_data_root, get_canonical_dir

data_root = get_data_root()
canonical = get_canonical_dir()
if (canonical / "manifest.json").exists():
    data_source = "volume"
elif any((data_root / "starter").iterdir()) if (data_root / "starter").exists() else False:
    data_source = "starter"
else:
    data_source = "fixtures"
```

Add `"data_source": data_source` to the response dict.

**Step 2: Commit**

```bash
git add backend/src/fibokei/api/routes/system.py
git commit -m "feat: add data_source to system status for observability"
```

---

### Task 8: Remove Dead data_path from Backtest Schema

**Files:**
- Modify: `backend/src/fibokei/api/routes/backtests.py`
- Modify: `backend/src/fibokei/api/schemas/backtests.py` (or wherever BacktestRunRequest is defined)

**Step 1: Find and remove data_path**

Search for `data_path` in the backtest request schema. Remove the field. In the route handler, remove the `if req.data_path:` branch and always use `load_canonical()`.

The backtest route should simplify to:

```python
    df = load_canonical(req.instrument, tf_enum.value)
    if df is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data file for {req.instrument}/{tf_enum.value}",
        )
```

**Step 2: Run tests**

Run: `cd backend && python -m pytest tests/ -v -k backtest`
Expected: All pass

**Step 3: Commit**

```bash
git add backend/src/fibokei/api/routes/backtests.py backend/src/fibokei/api/schemas/
git commit -m "refactor: remove dead data_path from backtest API, always use load_canonical"
```

---

### Task 9: Update Documentation

**Files:**
- Modify: `docs/deployment.md`
- Modify: `docs/api_contracts.md`

**Step 1: Add Railway volume section to deployment.md**

Add a section documenting:
- Volume creation and mounting at `/data`
- `FIBOKEI_DATA_DIR=/data` env var
- Operator workflow for populating the volume
- Manifest generation and verification
- Fallback behavior

**Step 2: Update api_contracts.md**

Add:
- `GET /data/manifest` contract
- `POST /data/manifest/refresh` contract
- Updated `GET /market-data/{instrument}/{timeframe}` with new query params and response metadata
- Updated `GET /system/status` with `data_source` field

**Step 3: Commit**

```bash
git add docs/deployment.md docs/api_contracts.md
git commit -m "docs: Railway volume setup, manifest API, market data pagination"
```

---

### Task 10: Final Verification

**Step 1: Run full test suite**

Run: `cd backend && python -m pytest -v`
Expected: All pass

**Step 2: Test manifest generation locally**

Run: `cd backend && python -m fibokei manifest`
Expected: Lists all canonical datasets with provider counts

**Step 3: Build frontend to verify no regressions**

Run: `cd frontend && npx next build`
Expected: Build succeeds

**Step 4: Manual verification**

Start backend locally and verify:
- `GET /api/v1/data/manifest` returns full dataset listing
- `GET /api/v1/market-data/EURUSD/H1?limit=500` returns 500 bars with metadata
- `GET /api/v1/market-data/EURUSD/M15` returns M15 data (not just H1)
- `GET /api/v1/system/status` includes `data_source`
- `GET /api/v1/instruments` shows correct `has_canonical_data` for all instruments

**Step 5: Final commit**

```bash
git commit -m "chore: Slice 1 complete — online historical data foundation"
```

---

## What This Slice Enables

1. **Full dataset in production** — once Railway volume is populated, all 60 instruments × 6 timeframes are live
2. **Charts work for all instruments and timeframes** — not just 7 majors at H1
3. **Backtests production-usable** — any instrument/timeframe combo works
4. **Research matrix production-usable** — full grid execution possible
5. **Observable** — system status shows data source, manifest shows exact inventory
6. **Performant** — LRU cache prevents repeated parquet reads, vectorized serialization, pagination caps

## What Remains

| Feature | Slice |
|---------|-------|
| Drawing tools UI + persistence | Slice 2 |
| Live IG chart mode | Slice 3 |
| Frontend data availability UI | Slice 4 |
| Research preset builder from manifest | Slice 4 |
| Bulk data sync tooling | Slice 4 |
| Object storage migration (if needed) | Future |
