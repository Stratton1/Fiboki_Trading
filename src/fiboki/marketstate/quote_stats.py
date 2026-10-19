"""Monitor statistics over RECORDED executable quotes (watchlist, Market Pulse).

Pure, deterministic functions of a list of :class:`~fiboki.data.recorder.
QuoteRecord` and explicit times: the same quotes and the same ``now`` give the
same numbers, so the API can be tested exactly and the workstation computes
none of this (the terminal programme's rule: the frontend renders).

What these numbers are, and are not
-----------------------------------
They describe what the RECORDER saw: OANDA practice top of book polled every
``FIBOKI_QUOTES_INTERVAL`` seconds (default 30), one record per changed quote.
Moves between polls are not seen; a window with no record for an instrument is
reported as missing, never filled. They are monitoring figures, not research
inputs: research reads closed bars from versioned datasets, and nothing here is
imported by ``strategy/``, ``backtest/``, ``risk/`` or ``portfolio/``.

* ``change_pct``: last mid in the window over the FIRST mid at or after the
  window start, minus one, in percent. ``None`` with fewer than two quotes.
* ``range_pct``: (max mid - min mid) / last mid, in percent.
* ``realised_vol_pct``: the standard deviation of log returns between
  consecutive bucket closes (last mid per bucket; empty buckets skipped),
  scaled by sqrt(number of returns) to the window, in percent. Not annualised.
  ``None`` with fewer than three non-empty buckets.
* currency strength: for each currency, the mean over the FX pairs it is in of
  ``+change`` (as base) or ``-change`` (as quote). A simple, stated
  definition; it is not a trade-weighted index.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from fiboki.data.recorder import QuoteRecord

__all__ = [
    "CurrencyScore",
    "InstrumentStats",
    "bucket_closes",
    "currency_strength",
    "instrument_stats",
]


@dataclass(frozen=True, slots=True)
class InstrumentStats:
    n_quotes: int
    first_mid: float | None
    last_mid: float | None
    change_pct: float | None
    range_pct: float | None
    realised_vol_pct: float | None
    #: Bucket closes (last mid per bucket), ``None`` for a bucket with no quote.
    spark: tuple[float | None, ...]
    bucket_starts: tuple[datetime, ...]


@dataclass(frozen=True, slots=True)
class CurrencyScore:
    currency: str
    score_pct: float
    pairs: int


def bucket_closes(
    quotes: Sequence[QuoteRecord], start: datetime, end: datetime, buckets: int
) -> tuple[tuple[datetime, ...], tuple[float | None, ...]]:
    """Last mid per equal bucket of ``[start, end)``; ``None`` where nothing was recorded."""
    if buckets < 1 or end <= start:
        raise ValueError("need at least one bucket and end after start")
    width = (end - start) / buckets
    starts = tuple(start + width * i for i in range(buckets))
    closes: list[float | None] = [None] * buckets
    for q in quotes:
        if q.timestamp < start or q.timestamp >= end:
            continue
        i = min(buckets - 1, int((q.timestamp - start) / width))
        closes[i] = q.mid  # quotes arrive oldest first: the last one wins
    return starts, tuple(closes)


def instrument_stats(
    quotes: Sequence[QuoteRecord], start: datetime, end: datetime, *, buckets: int = 48
) -> InstrumentStats:
    inside = [q for q in quotes if start <= q.timestamp <= end]
    starts, spark = bucket_closes(inside, start, end + timedelta(microseconds=1), buckets)
    if not inside:
        return InstrumentStats(0, None, None, None, None, None, spark, starts)
    mids = [q.mid for q in inside]
    first, last = mids[0], mids[-1]
    change = None if len(mids) < 2 or first <= 0 else (last / first - 1.0) * 100.0
    rng = None if len(mids) < 2 or last <= 0 else (max(mids) - min(mids)) / last * 100.0
    closes = [c for c in spark if c is not None and c > 0]
    vol = None
    if len(closes) >= 3:
        rets = [math.log(b / a) for a, b in pairwise(closes)]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
        vol = math.sqrt(var) * math.sqrt(len(rets)) * 100.0
    return InstrumentStats(len(inside), first, last, change, rng, vol, spark, starts)


def currency_strength(
    changes: Mapping[str, float | None], pairs: Mapping[str, tuple[str, str]]
) -> list[CurrencyScore]:
    """Mean signed change per currency over the FX pairs it appears in, strongest first.

    ``pairs`` maps a symbol to ``(base, quote)``; symbols not in it (metals,
    indices, crypto) and missing changes are left out, never counted as zero.
    Ties sort by currency code so the order is deterministic.
    """
    acc: dict[str, list[float]] = {}
    for symbol, change in sorted(changes.items()):
        legs = pairs.get(symbol)
        if change is None or legs is None:
            continue
        base, quote = legs
        acc.setdefault(base, []).append(change)
        acc.setdefault(quote, []).append(-change)
    out = [CurrencyScore(c, sum(v) / len(v), len(v)) for c, v in acc.items()]
    out.sort(key=lambda s: (-s.score_pct, s.currency))
    return out


def ranked(values: Iterable[tuple[str, float | None]], *, descending: bool) -> list[tuple[str, float]]:
    """Known values only, sorted by value then symbol (deterministic)."""
    known = [(s, v) for s, v in values if v is not None]
    known.sort(key=lambda sv: ((-sv[1]) if descending else sv[1], sv[0]))
    return known
