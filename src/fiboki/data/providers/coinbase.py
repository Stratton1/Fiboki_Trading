"""Coinbase Exchange PUBLIC candles: spot crypto bars, no credentials.

The modelled crypto venue is Coinbase UK (``docs/v2/research/CRYPTO_VENUES_2026-10.md``),
and its public market-data REST API serves the same GBP books the operator
would trade. This module is the bar provider for them; ``data/crypto_backfill.py``
writes history into the store through it and the crypto paper-forward wiring
polls it for live bars.

The endpoint
------------
``GET https://api.exchange.coinbase.com/products/{product_id}/candles``
with ``granularity`` in seconds (60, 300, 900, 3600, 21600 or 86400; nothing
else is accepted) and optional ``start``/``end`` (ISO 8601, both inclusive in
practice). At most 300 candles per request. No key. Public endpoints are rate
limited to 10 requests per second per IP (burst 15); this module never asks
for more than the caller's limiter allows. Each candle is an array::

    [time, low, high, open, close, volume]

``time`` is the bar's START in Unix seconds, UTC. ``volume`` is traded BASE
quantity (BTC for BTC-GBP), a real exchange volume. The response is newest
first.

Five things this provider refuses to be casual about
----------------------------------------------------
* **There is no "complete" flag.** The newest candle may still be forming. A
  candle is kept only if its END (start plus the bar) is at or before the
  provider's clock; every other one is dropped and counted. This is the one
  place the clock decides finality, and it is stated: Coinbase gives nothing
  else to decide it with. The feed polls a few seconds after the boundary, so
  the forming candle is never the one at stake.
* **Missing candles are absence.** Coinbase publishes no candle for an interval
  with no trades. Nothing is filled; the integrity layer reports the gap.
* **Order is checked, not assumed.** The page must be strictly monotonic (it
  arrives descending); a page that is not, or that repeats a timestamp, is
  refused rather than sorted.
* **The prices are TRADE PRINTS (``LAST``).** Fiboki's fill model takes mid
  bars and adds half a spread each side. On a central limit order book every
  print is at the bid or the ask, so open and close sit within half a spread of
  mid, the high is (at most) half a spread above the mid's high and the low
  half a spread below the mid's low. The bars are stored as
  ``price_basis = last_as_mid`` with that bound recorded as an adjustment. The
  error is symmetric in sign but not in effect: a long's stop (bid low) is hit
  SLIGHTLY MORE often than on true mids, and its target (bid high) slightly
  more often too. At the recorded median spreads (0.8 to 7 bps of mid) it is
  one to two orders of magnitude below the 60 bps fee per leg the cost profile
  charges, and it is stated here rather than hidden.
* **H4 is not a Coinbase granularity.** H4 bars are built from H1 by
  :func:`fiboki.data.resample.resample` (epoch-anchored UTC, empty buckets
  dropped) and an H4 bucket is kept only when all four of its hours have
  closed. The backfill records the resample as its own lineage step.

Volume
------
The canonical ``volume`` column is an integer. Base quantities are fractional
(0.0859 BTC), so ``volume`` is stored as the base quantity in units of the
instrument's ``size_step`` (Coinbase's ``base_increment``: satoshi for BTC,
0.01 ADA for ADA), rounded to the nearest unit. Lossless at the venue's own
precision; every volume indicator Fiboki has is a ratio, so the unit cancels.
Recorded as an adjustment. There is no ``tick_volume`` column (the OANDA
backfill likewise stores no ``volume`` column, the one it lacks).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.providers.base import (
    BarBatch,
    BarProvider,
    ProviderCapabilities,
    ProviderError,
    UnsupportedRequest,
)
from fiboki.data.resample import resample
from fiboki.data.schema import (
    Adjustment,
    DatasetKind,
    PriceBasis,
    canonical_frame,
    describe_frame,
)

__all__ = [
    "COINBASE_EXCHANGE_BASE_URL",
    "COINBASE_EXCHANGE_HOST",
    "COINBASE_MAX_CANDLES",
    "COINBASE_SOURCE",
    "NATIVE_GRANULARITY",
    "CoinbaseCandlesProvider",
    "assert_coinbase_host",
    "frame_from_candles",
    "from_coinbase_product",
    "h1_to_h4",
    "page_rows",
    "to_coinbase_product",
]

COINBASE_EXCHANGE_HOST = "api.exchange.coinbase.com"
COINBASE_EXCHANGE_BASE_URL = f"https://{COINBASE_EXCHANGE_HOST}"
#: The ``source`` stamped on every dataset version this provider's data makes.
COINBASE_SOURCE = "coinbase_exchange_public"
#: Documented per-request ceiling.
COINBASE_MAX_CANDLES = 300
#: Timeframes Coinbase serves natively, in seconds. H4 is derived from H1.
NATIVE_GRANULARITY: dict[Timeframe, int] = {Timeframe.H1: 3600, Timeframe.D1: 86400}
#: Derived timeframe -> (native source, bars per derived bar).
DERIVED: dict[Timeframe, tuple[Timeframe, int]] = {Timeframe.H4: (Timeframe.H1, 4)}


#: Declared on every batch and every stored version.
PRICE_ADJUSTMENTS: tuple[Adjustment, ...] = (
    Adjustment(
        kind="price_basis_declaration",
        description=(
            "Coinbase candles are trade prints (LAST). Stored as last_as_mid: each print "
            "is at the bid or the ask, so open/close are within half a spread of mid and "
            "high/low overstate the mid's range by at most half a spread. Bound: the "
            "instrument's spread / 2 in bps of mid (recorded median 0.8-7 bps)."
        ),
        parameters={"native": PriceBasis.LAST.value, "stored": PriceBasis.LAST_AS_MID.value},
    ),
    Adjustment(
        kind="volume_semantics",
        description=(
            "volume = traded BASE quantity / the instrument's size_step (Coinbase "
            "base_increment), rounded to an integer. There is no tick_volume column: "
            "an exchange's traded volume is the real thing."
        ),
        parameters={"volume": "base_quantity_in_size_steps", "tick_volume": "not_stored"},
    ),
    Adjustment(
        kind="candle_finality",
        description=(
            "Coinbase sends no completeness flag; a candle is kept only when its end "
            "(start + bar) is at or before the fetch clock."
        ),
        parameters={"rule": "start + bar <= clock"},
    ),
)


def assert_coinbase_host(base_url: str) -> str:
    """The parsed hostname must be the public Exchange API host, over https."""
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host != COINBASE_EXCHANGE_HOST:
        raise ProviderError(
            f"{base_url!r} parses to {parts.scheme}://{host}; only "
            f"{COINBASE_EXCHANGE_BASE_URL} is accepted (compared on the parsed hostname)"
        )
    return COINBASE_EXCHANGE_BASE_URL


def to_coinbase_product(symbol: str) -> str:
    """``BTCGBP`` -> ``BTC-GBP``. A product id is accepted unchanged."""
    try:
        if "-" in symbol:
            instrument_registry.symbol_for_coinbase_product(symbol)
            return symbol
        return instrument_registry.coinbase_product_for(symbol)
    except KeyError as exc:
        raise ProviderError(str(exc.args[0])) from exc


def from_coinbase_product(product_id: str) -> str:
    try:
        return instrument_registry.symbol_for_coinbase_product(product_id)
    except KeyError as exc:
        raise ProviderError(str(exc.args[0])) from exc


def _bar(tf: Timeframe) -> pd.Timedelta:
    return pd.Timedelta(minutes=tf.minutes)


def _utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def page_rows(payload: Any, *, label: str) -> list[tuple[int, float, float, float, float, float]]:
    """Validate one candles response and return its rows OLDEST FIRST.

    Refuses: a non-list body (Coinbase errors are ``{"message": ...}``), a row
    that is not six numbers, and a page that is not strictly monotonic in time.
    """
    if isinstance(payload, Mapping) and set(payload) == {"_body"}:
        # ``HttpxTransport`` wraps a non-object JSON body as {"_body": ...}.
        payload = payload["_body"]
    if isinstance(payload, Mapping):
        raise ProviderError(f"{label}: Coinbase answered {dict(payload)!r}"[:300])
    if not isinstance(payload, list):
        raise ProviderError(f"{label}: candles response is not a list")
    rows: list[tuple[int, float, float, float, float, float]] = []
    for raw in payload:
        if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 6:
            raise ProviderError(f"{label}: malformed candle {raw!r}"[:200])
        try:
            t = int(raw[0])
            low, high, opn, close, vol = (float(x) for x in raw[1:])
        except (TypeError, ValueError) as exc:
            raise ProviderError(f"{label}: non-numeric candle {raw!r}"[:200]) from exc
        rows.append((t, low, high, opn, close, vol))
    if len(rows) >= 2:
        times = [r[0] for r in rows]
        desc = all(a > b for a, b in pairwise(times))
        asc = all(a < b for a, b in pairwise(times))
        if not (desc or asc):
            raise ProviderError(
                f"{label}: candle times are not strictly monotonic (or repeat); "
                "refusing rather than sorting"
            )
        if desc:
            rows.reverse()
    return rows


def frame_from_candles(
    rows: Sequence[tuple[int, float, float, float, float, float]],
    *,
    instrument: str,
    timeframe: Timeframe,
    now: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Canonical ``last_as_mid`` bars from oldest-first rows; forming candles dropped.

    Raises on an empty result: an empty frame would read as "no history".
    """
    spec = instrument_registry.get(instrument)
    bar = _bar(timeframe)
    clock = _utc(now)
    kept: list[tuple[int, float, float, float, float, float]] = []
    forming = 0
    for row in rows:
        start = pd.Timestamp(row[0], unit="s", tz="UTC")
        if start + bar > clock:
            forming += 1
            continue
        kept.append(row)
    report = {"candles_received": len(rows), "candles_kept": len(kept), "incomplete_dropped": forming}
    if not kept:
        raise ProviderError(
            f"{instrument} {timeframe.value}: no closed candles in this response; an empty "
            "frame would be indistinguishable from an instrument with no history"
        )
    arr = np.asarray(kept, dtype=np.float64)
    index = pd.DatetimeIndex(pd.to_datetime(arr[:, 0].astype(np.int64), unit="s", utc=True), name="timestamp")
    step = float(spec.size_step)
    frame = pd.DataFrame(
        {
            "open": arr[:, 3],
            "high": arr[:, 2],
            "low": arr[:, 1],
            "close": arr[:, 4],
            "volume": np.rint(arr[:, 5] / step).astype(np.int64),
        },
        index=index,
    )
    frame = canonical_frame(
        frame, instrument=spec.symbol, timeframe=timeframe, price_basis=PriceBasis.LAST_AS_MID
    )
    return frame, report


def h1_to_h4(frame_h1: pd.DataFrame, *, now: pd.Timestamp) -> tuple[pd.DataFrame, int]:
    """Epoch-anchored H4 from H1; a bucket whose four hours have not all closed is dropped.

    Returns ``(frame, dropped)``. An H1 bar missing because nothing traded
    leaves its H4 bucket built from the hours that did trade, which is the
    venue's own answer for that interval; nothing is filled.
    """
    out = resample(frame_h1, Timeframe.H4, source=Timeframe.H1)
    clock = _utc(now)
    complete = (out.index + _bar(Timeframe.H4)) <= clock
    dropped = int((~complete).sum())
    return out.loc[complete], dropped


class CoinbaseCandlesProvider(BarProvider):
    """Coinbase Exchange public candles. Implements ``BarProvider`` (and the feed's ``count``)."""

    def __init__(
        self,
        *,
        http_client: Any = None,
        base_url: str = COINBASE_EXCHANGE_BASE_URL,
        clock: Callable[[], pd.Timestamp] | None = None,
        limiter: Any = None,
    ) -> None:
        self.base_url = assert_coinbase_host(base_url)
        self.http_client = http_client
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        #: Anything with ``acquire()``; called before EVERY request.
        self.limiter = limiter
        self.requests = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=COINBASE_SOURCE,
            native_price_basis=PriceBasis.LAST,
            native_timezone="UTC",
            supports_bid_ask=False,
            supports_tick_volume=False,
            supports_real_volume=True,
            timeframes=(Timeframe.H1, Timeframe.H4, Timeframe.D1),
            requires_credentials=False,
            can_emit_incomplete_bars=True,
            max_bars_per_request=COINBASE_MAX_CANDLES,
            notes=(
                "Exchange trade prints stored as last_as_mid (bound: half the spread). "
                "No completeness flag: forming candles are dropped by the clock. H4 is "
                "resampled from H1. No candle is published for an interval without trades."
            ),
        )

    def available(self) -> list[tuple[str, Timeframe]]:
        return [
            (sym, tf)
            for sym in sorted(instrument_registry.CRYPTO_REGISTERED)
            for tf in self.capabilities.timeframes
        ]

    # -- requests --------------------------------------------------------

    def request_params(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> tuple[str, dict[str, Any]]:
        """The exact URL and query for ONE native page ``[start, end]`` (both inclusive)."""
        if timeframe not in NATIVE_GRANULARITY:
            raise UnsupportedRequest(
                f"Coinbase serves {[t.value for t in NATIVE_GRANULARITY]} natively; "
                f"{timeframe.value} is derived, not requested"
            )
        start, end = _utc(start), _utc(end)
        if end < start:
            raise ProviderError("end before start")
        bars = int((end - start) / _bar(timeframe)) + 1
        if bars > COINBASE_MAX_CANDLES:
            raise ProviderError(
                f"a page of {bars} {timeframe.value} candles exceeds Coinbase's "
                f"{COINBASE_MAX_CANDLES}-candle limit"
            )
        product = to_coinbase_product(instrument)
        url = f"{self.base_url}/products/{product}/candles"
        params = {
            "granularity": NATIVE_GRANULARITY[timeframe],
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        }
        return url, params

    def fetch_page(
        self, instrument: str, timeframe: Timeframe, start: pd.Timestamp, end: pd.Timestamp
    ) -> list[tuple[int, float, float, float, float, float]]:
        """One request; rows oldest first. A non-2xx raises with its status attached."""
        if self.http_client is None:
            raise ProviderError("no HTTP client configured for Coinbase candles")
        url, params = self.request_params(instrument, timeframe, start=start, end=end)
        if self.limiter is not None:
            self.limiter.acquire()
        self.requests += 1
        response = self.http_client.get(url, params=params, headers={"Accept": "application/json"})
        response.raise_for_status()
        return page_rows(response.json(), label=f"{instrument} {timeframe.value} {params['start']}")

    def fetch_native(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp,
        end: pd.Timestamp,
        runner: Callable[..., Any] | None = None,
        on_page: Callable[[pd.Timestamp, int], None] | None = None,
    ) -> list[tuple[int, float, float, float, float, float]]:
        """All native rows with start in ``[start, end)``, oldest first, paged at 300.

        ``runner(fn, *args)`` wraps each page request (the backfill passes its
        retry runner's ``run``); ``on_page(page_end, rows)`` is told after each.
        Pages that come back EMPTY are not the end of history (Coinbase sends
        nothing for an interval with no trades); paging stops at ``end``.
        """
        bar = _bar(timeframe)
        cursor = _utc(start).ceil(f"{timeframe.minutes}min")
        stop = _utc(end)
        out: dict[int, tuple[int, float, float, float, float, float]] = {}
        while cursor < stop:
            page_end = min(cursor + bar * (COINBASE_MAX_CANDLES - 1), stop - bar)
            if page_end < cursor:
                break
            page = (
                runner(self.fetch_page, instrument, timeframe, cursor, page_end)
                if runner is not None
                else self.fetch_page(instrument, timeframe, cursor, page_end)
            )
            if on_page is not None:
                on_page(page_end, len(page))
            for row in page:
                t = pd.Timestamp(row[0], unit="s", tz="UTC")
                if t < cursor or t > page_end:
                    continue  # Coinbase may include a candle just outside the window
                if row[0] in out and out[row[0]] != row:
                    raise ProviderError(
                        f"{instrument} {timeframe.value}: candle {t.isoformat()} arrived twice "
                        "with different values"
                    )
                out[row[0]] = row
            cursor = page_end + bar
        return [out[k] for k in sorted(out)]

    def fetch_bars(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        count: int | None = None,
    ) -> BarBatch:
        """Closed bars for ``instrument``: the newest ``count``, or ``[start, end)``.

        H4 is fetched as H1 (four per bar plus one bar of slack) and resampled.
        Raises :class:`ProviderError` when nothing closed is returned.
        """
        self.capabilities.assert_timeframe(timeframe)
        now = _utc(self.clock())
        native, per = DERIVED.get(timeframe, (timeframe, 1))
        boundary = now.floor(f"{timeframe.minutes}min")
        if count is not None:
            if start is not None:
                raise ProviderError("pass count or start, not both")
            stop = boundary
            first = stop - _bar(timeframe) * int(count)
        else:
            if start is None:
                raise ProviderError("pass start (and optionally end) or count")
            first = _utc(start)
            stop = min(_utc(end), boundary) if end is not None else boundary
        rows = self.fetch_native(instrument, native, start=first, end=stop)
        frame, counts = frame_from_candles(rows, instrument=instrument, timeframe=native, now=now)
        report: dict[str, Any] = dict(counts)
        if per > 1:
            frame, dropped = h1_to_h4(frame, now=now)
            report = {**report, "derived_from": native.value, "derived_incomplete_dropped": dropped}
            if frame.empty:
                raise ProviderError(f"{instrument} {timeframe.value}: no closed derived bars")
        return self.batch_for(frame, report, source_identifier=f"{self.base_url}/products/"
                              f"{to_coinbase_product(instrument)}/candles granularity="
                              f"{NATIVE_GRANULARITY[native]}")

    def batch_for(
        self, frame: pd.DataFrame, report: Mapping[str, Any], *, source_identifier: str
    ) -> BarBatch:
        warnings: list[str] = []
        if report.get("incomplete_dropped"):
            warnings.append(
                f"dropped {report['incomplete_dropped']} forming candle(s); signals are "
                "evaluated on closed candles only"
            )
        metadata = describe_frame(
            frame,
            source=COINBASE_SOURCE,
            source_identifier=source_identifier,
            timezone_of_origin="UTC",
            quality=DataQuality.RAW,
            kind=DatasetKind.RAW,
            adjustments=PRICE_ADJUSTMENTS,
            notes="; ".join(warnings),
            extra={"parse_report": dict(report)},
        )
        return BarBatch(
            frame=frame,
            metadata=metadata,
            adjustments=PRICE_ADJUSTMENTS,
            warnings=tuple(warnings),
            extra=dict(report),
        )
