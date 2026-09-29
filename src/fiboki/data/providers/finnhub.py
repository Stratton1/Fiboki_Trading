"""Finnhub economic calendar, as a SECONDARY dated-events source.

``GET https://finnhub.io/api/v1/calendar/economic?from=YYYY-MM-DD&to=YYYY-MM-DD``
with the key in the ``X-Finnhub-Token`` header (Finnhub accepts the header or
a ``token`` query parameter; the header keeps the key out of URLs and logs).

What was verified, 2026-09-29, from Finnhub's own swagger schema
(https://finnhub.io/static/swagger.json):

* The endpoint is marked **"Premium Access Required"**; "Historical events and
  surprises are available for Enterprise clients". A free key is expected to
  be refused (HTTP 401/403), which raises
  :class:`~fiboki.data.providers.base.AuthenticationRequired` naming the plan.
* Response: ``{"economicCalendar": [{"actual", "country", "estimate", "event",
  "impact", "prev", "time", "unit"}]}``; ``time`` is ``"YYYY-MM-DD HH:MM:SS"``.
* The schema does not state the zone of ``time``. Its own sample row,
  "Australia - Current Account Balance" at ``2020-06-02 01:30:00``, is an
  ABS release published at 11:30 AEST, i.e. 01:30 UTC, so the field is
  treated as UTC on that evidence. This is an inference from a documentation
  sample and is stated in every file written (``time_basis``); the first
  real response should be checked against a known release.

Rate limit: the free tier's 60 calls per minute is enforced client-side by a
:class:`~fiboki.data.providers.ratelimit.SlidingWindowLimiter`, which should be
the same instance the Finnhub news clients use (the limit is per key).

The output is a dated-events file tagged ``source: "finnhub_calendar"``,
scheduled times only (see :mod:`fiboki.data.providers.calendar_feed`); it is a
comparison source for the official calendar and never replaces it.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.data.providers.base import AuthenticationRequired, ProviderError
from fiboki.data.providers.calendar_feed import (
    IMPACT_ORDER,
    dated_event,
    snapshot_path,
    write_events_file,
)
from fiboki.data.providers.macro_base import http_get_text
from fiboki.data.providers.ratelimit import SlidingWindowLimiter, finnhub_free_tier_limiter

__all__ = [
    "COUNTRY_CURRENCY",
    "ENV_FINNHUB_API_KEY",
    "FINNHUB_CALENDAR_SOURCE",
    "FinnhubCalendarProvider",
    "FinnhubCalendarSnapshot",
    "parse_economic_calendar",
]

ENV_FINNHUB_API_KEY = "FIBOKI_FINNHUB_API_KEY"
FINNHUB_HOST = "https://finnhub.io"
CALENDAR_PATH = "/api/v1/calendar/economic"
FINNHUB_CALENDAR_SOURCE = "finnhub_calendar"
TERMS_URL = "https://finnhub.io/terms-of-service"
TIME_BASIS = (
    "Finnhub 'time' has no declared zone; treated as UTC because the schema's own sample "
    "(AU current account 01:30 for an 11:30 AEST release) is UTC. Inference, verify on first use."
)

#: Finnhub ``country`` (ISO 3166 alpha-2, plus "EU"/"EMU" for the euro area)
#: to the currency whose instruments it moves. Euro-area member states map to
#: EUR. Anything else is counted as unmapped, not guessed.
COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "EU": "EUR", "EMU": "EUR", "EA": "EUR", "DE": "EUR", "FR": "EUR", "IT": "EUR",
    "ES": "EUR", "NL": "EUR", "IE": "EUR", "PT": "EUR", "AT": "EUR", "BE": "EUR", "FI": "EUR",
    "GR": "EUR", "GB": "GBP", "UK": "GBP", "JP": "JPY", "CH": "CHF", "AU": "AUD", "NZ": "NZD",
    "CA": "CAD", "CN": "CNY", "SE": "SEK", "NO": "NOK",
}


@dataclass(frozen=True, slots=True)
class FinnhubCalendarSnapshot:
    events: tuple[dict[str, Any], ...]
    retrieved_at: pd.Timestamp
    request: dict[str, Any]
    report: dict[str, Any] = field(default_factory=dict)

    def write(self, root: str | Path) -> Path:
        """Write under ``<root>/calendar/finnhub_calendar/<stamp>.json`` (write-once)."""
        path = snapshot_path(root, FINNHUB_CALENDAR_SOURCE, self.retrieved_at)
        return write_events_file(
            path, self.events, source=FINNHUB_CALENDAR_SOURCE,
            retrieved_at=self.retrieved_at.isoformat(),
            manifest={"request": self.request, "report": self.report, "time_basis": TIME_BASIS,
                      "terms_url": TERMS_URL, "terms_status": "personal_only",
                      "role": "secondary calendar for comparison; never replaces the official one"},
        )


def parse_economic_calendar(
    payload: Mapping[str, Any], *, retrieved_at: str, source_url: str = f"{FINNHUB_HOST}{CALENDAR_PATH}",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows of ``economicCalendar`` to dated-event dicts; return ``(events, counts)``.

    Counted, never silently dropped: rows with an unmapped country, an impact
    outside low/medium/high, or an unparseable time.
    """
    rows = payload.get("economicCalendar")
    if not isinstance(rows, list):
        raise ProviderError("Finnhub calendar response has no 'economicCalendar' list")
    counts = {"received": len(rows), "kept": 0, "unmapped_country": 0, "bad_impact": 0,
              "bad_time": 0}
    events: list[dict[str, Any]] = []
    for r in rows:
        country = str(r.get("country") or "").strip().upper()
        ccy = COUNTRY_CURRENCY.get(country)
        if ccy is None:
            counts["unmapped_country"] += 1
            continue
        impact = str(r.get("impact") or "").strip().lower()
        if impact not in IMPACT_ORDER:
            counts["bad_impact"] += 1
            continue
        try:
            naive = pd.Timestamp(str(r.get("time") or ""))
        except ValueError:
            counts["bad_time"] += 1
            continue
        if pd.isna(naive) or naive.tzinfo is not None:
            counts["bad_time"] += 1
            continue
        name = " ".join(str(r.get("event") or "").split())
        if not name:
            counts["bad_time"] += 1
            continue
        events.append(dated_event(
            event_time=naive.tz_localize("UTC"), currency=ccy, name=name, impact=impact,
            source=FINNHUB_CALENDAR_SOURCE, source_url=source_url, retrieved_at=retrieved_at,
            tags=(f"country:{country}",),
        ))
        counts["kept"] += 1
    return events, counts


class FinnhubCalendarProvider:
    """Fetch the Finnhub economic calendar for a date range. Needs a PREMIUM key."""

    def __init__(self, *, api_key: str | None, http_client: Any,
                 rate_limiter: SlidingWindowLimiter | None = None) -> None:
        self.api_key = api_key
        self.http_client = http_client
        self.rate_limiter = rate_limiter if rate_limiter is not None else finnhub_free_tier_limiter()

    @classmethod
    def from_env(cls, http_client: Any, *, env: Mapping[str, str] | None = None,
                 rate_limiter: SlidingWindowLimiter | None = None) -> FinnhubCalendarProvider:
        environ = os.environ if env is None else env
        return cls(api_key=(environ.get(ENV_FINNHUB_API_KEY) or "").strip() or None,
                   http_client=http_client, rate_limiter=rate_limiter)

    def fetch(self, start: date, end: date, *, now: pd.Timestamp | None = None) -> FinnhubCalendarSnapshot:
        if not self.api_key:
            raise AuthenticationRequired(f"Finnhub calendar needs {ENV_FINNHUB_API_KEY}")
        if end < start:
            raise ProviderError(f"end {end} is before start {start}")
        retrieved = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        request = {"from": start.isoformat(), "to": end.isoformat()}
        self.rate_limiter.acquire()
        try:
            text = http_get_text(
                self.http_client, f"{FINNHUB_HOST}{CALENDAR_PATH}", params=request,
                headers={"X-Finnhub-Token": self.api_key}, secrets=(self.api_key,),
                what="Finnhub economic calendar",
            )
        except AuthenticationRequired as exc:
            raise AuthenticationRequired(
                f"{exc}. Finnhub marks /calendar/economic 'Premium Access Required'; a free "
                "key is refused here."
            ) from None
        events, counts = parse_economic_calendar(json.loads(text), retrieved_at=retrieved.isoformat())
        return FinnhubCalendarSnapshot(events=tuple(events), retrieved_at=retrieved,
                                       request=request, report=counts)
