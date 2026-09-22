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
