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
