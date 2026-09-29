"""OANDA v20 position book and order book snapshots (read-only market data).

``GET {host}/v3/instruments/{name}/positionBook`` and ``.../orderBook`` with
``Authorization: Bearer <token>``. Response (v20 release notes 3.0.25,
2018-09-28, "Added orderBook and PositionBook endpoints"; shape as documented
by the v20 reference at the time)::

    {"positionBook": {"instrument": "EUR_USD", "time": "2017-06-28T10:00:00Z",
                      "price": "1.13609", "bucketWidth": "0.00050",
                      "buckets": [{"price": "1.12800", "longCountPercent": "0.2627",
                                   "shortCountPercent": "0.2670"}, ...]}}

``orderBook`` has the same shape. Percentages are decimal strings.

**Availability is not established.** On 2026-09-29 the current v20
instrument reference no longer documents either endpoint, and a third-party
report (Dekalog blog, 2024-09) says OANDA stopped serving them through v20 as
"a business decision". The client is therefore built and tested against a
CONSTRUCTED fixture only; the first live poll decides whether it returns data
or a 4xx, which the recorder logs as an error per instrument, not as an empty
book.

Safety: this module is data, not execution. It issues GETs to exactly two
path templates and nothing else; the host is checked by PARSED hostname
against the two v20 REST hosts (practice ``api-fxpractice.oanda.com``, live
``api-fxtrade.oanda.com``); the live host is reachable only with
``FIBOKI_OANDA_BOOKS_ENVIRONMENT=live`` because books are served for the
account's own environment. No order, account or trade path can be built.

Terms: OANDA Terms of Use (https://www.oanda.com/site/terms-of-use, linked
from the v20 development guide); book data is served to the account holder
and is recorded here for personal research only (``personal_only``).
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fiboki.data.positioning.store import PositioningSnapshot
from fiboki.data.providers.base import AuthenticationRequired, ProviderError
from fiboki.data.providers.macro_base import http_get_text

__all__ = [
    "BOOK_KINDS",
    "DEFAULT_BOOK_INSTRUMENTS",
    "ENV_OANDA_BOOKS_ENVIRONMENT",
    "ENV_OANDA_BOOKS_TOKEN",
    "OANDA_BOOKS_SOURCE",
    "OandaBooksClient",
    "parse_book",
]

ENV_OANDA_BOOKS_TOKEN = "FIBOKI_OANDA_BOOKS_TOKEN"
ENV_OANDA_BOOKS_ENVIRONMENT = "FIBOKI_OANDA_BOOKS_ENVIRONMENT"
OANDA_BOOKS_SOURCE = "oanda_books"
HOSTS: dict[str, str] = {
    "practice": "api-fxpractice.oanda.com",
    "live": "api-fxtrade.oanda.com",
}
BOOK_KINDS: dict[str, str] = {"position_book": "positionBook", "order_book": "orderBook"}
_INSTRUMENT = re.compile(r"^[A-Z0-9]{2,8}_[A-Z0-9]{2,8}$")

#: Majors and gold; v20 books were historically served for a subset of FX
#: pairs and metals only, not for index CFDs.
DEFAULT_BOOK_INSTRUMENTS: tuple[str, ...] = (
    "EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD",
    "EUR_JPY", "GBP_JPY", "EUR_GBP", "XAU_USD",
)


def _parse_time(raw: Any) -> datetime:
    text = str(raw or "").strip()
    if not text:
        raise ProviderError("book has no 'time'")
    # v20 may send nanoseconds (".000000000Z"); keep microseconds.
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z$", text)
    if not m:
        raise ProviderError(f"book time {text!r} is not an RFC 3339 UTC instant")
    frac = (m.group(2) or ".0")[1:7].ljust(6, "0")
    return datetime.strptime(f"{m.group(1)}.{frac}", "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)


def parse_book(payload: Mapping[str, Any], kind: str) -> PositioningSnapshot:
    """One v20 book response to a snapshot. ``kind``: position_book | order_book."""
    field = BOOK_KINDS[kind]
    book = payload.get(field)
    if not isinstance(book, Mapping):
        raise ProviderError(f"v20 response has no {field!r} object")
    buckets_raw = book.get("buckets")
    if not isinstance(buckets_raw, list) or not buckets_raw:
        raise ProviderError(f"{field} has no buckets; an empty book is not recorded as flat")
    buckets = [
        {"price": float(b["price"]), "long_pct": float(b["longCountPercent"]),
         "short_pct": float(b["shortCountPercent"])}
        for b in buckets_raw
    ]
    price = float(book["price"])
    long_total = sum(b["long_pct"] for b in buckets)
    short_total = sum(b["short_pct"] for b in buckets)
    payload_out = {
        "instrument": str(book.get("instrument")),
        "price": price,
        "bucket_width": float(book["bucketWidth"]),
        "buckets": buckets,
        # Descriptive totals only (the book's own percentages summed); no signal.
        "long_pct_total": round(long_total, 6),
        "short_pct_total": round(short_total, 6),
        "long_pct_below_price": round(sum(b["long_pct"] for b in buckets if b["price"] < price), 6),
        "short_pct_above_price": round(sum(b["short_pct"] for b in buckets if b["price"] > price), 6),
    }
    return PositioningSnapshot(source=OANDA_BOOKS_SOURCE, instrument=str(book.get("instrument")),
                               kind=kind, vendor_time=_parse_time(book.get("time")),
                               payload=payload_out)


class OandaBooksClient:
    """Hourly v20 book snapshots for a fixed instrument list. Read-only."""

    def __init__(self, *, token: str | None, http_client: Any, environment: str = "practice",
                 instruments: Iterable[str] = DEFAULT_BOOK_INSTRUMENTS,
                 kinds: Iterable[str] = tuple(BOOK_KINDS), min_interval_s: float = 3600.0) -> None:
        if environment not in HOSTS:
            raise ValueError(f"environment must be one of {sorted(HOSTS)}, not {environment!r}")
        self.token = token
        self.http_client = http_client
        self.environment = environment
        self.base_url = f"https://{HOSTS[environment]}"
        host = urlsplit(self.base_url).hostname
        if host not in HOSTS.values():  # parsed, never a string prefix test
            raise ValueError(f"refusing host {host!r}")
        self.instruments = tuple(instruments)
        for name in self.instruments:
            if not _INSTRUMENT.match(name):
                raise ValueError(f"{name!r} is not a v20 instrument name like EUR_USD")
        self.kinds = tuple(kinds)
        for k in self.kinds:
            if k not in BOOK_KINDS:
                raise ValueError(f"unknown book kind {k!r}")
        self.key = f"oanda_books_{environment}"
        self.min_interval_s = float(min_interval_s)
        self.last_report: dict[str, Any] = {}

    @classmethod
    def from_env(cls, http_client: Any, *, env: Mapping[str, str] | None = None,
                 **kwargs: Any) -> OandaBooksClient:
        environ = os.environ if env is None else env
        return cls(token=(environ.get(ENV_OANDA_BOOKS_TOKEN) or "").strip() or None,
                   http_client=http_client,
                   environment=(environ.get(ENV_OANDA_BOOKS_ENVIRONMENT) or "practice").strip().lower(),
                   **kwargs)

    def url(self, instrument: str, kind: str) -> str:
        if not _INSTRUMENT.match(instrument) or kind not in BOOK_KINDS:
            raise ValueError("only the two book paths may be built")
        return f"{self.base_url}/v3/instruments/{instrument}/{BOOK_KINDS[kind]}"

    def fetch(self) -> list[PositioningSnapshot]:
        """Every configured (instrument, kind). A failing one is reported, the rest carry on;
        if every one fails, raise with the first error so the poll records a failure."""
        if not self.token:
            raise AuthenticationRequired(f"OANDA books need {ENV_OANDA_BOOKS_TOKEN}")
        out: list[PositioningSnapshot] = []
        errors: dict[str, str] = {}
        for instrument in self.instruments:
            for kind in self.kinds:
                try:
                    text = http_get_text(
                        self.http_client, self.url(instrument, kind),
                        headers={"Authorization": f"Bearer {self.token}",
                                 "Accept-Datetime-Format": "RFC3339"},
                        secrets=(self.token,), what=f"OANDA {kind} {instrument}",
                    )
                    out.append(parse_book(json.loads(text), kind))
                except (ProviderError, ValueError, KeyError) as exc:
                    errors[f"{instrument}/{kind}"] = f"{type(exc).__name__}: {exc}"
        self.last_report = {"errors": errors} if errors else {}
        if not out and errors:
            first = next(iter(errors.values()))
            raise ProviderError(f"every OANDA book request failed ({len(errors)}); first: {first}")
        return out
