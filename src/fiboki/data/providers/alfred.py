"""ALFRED: FRED series with their vintages (St. Louis Fed).

A first-party client written from the FRED API documentation
(``fred/series_observations`` and "Real-Time Periods"); no code copied.

Point-in-time semantics
-----------------------
``GET /fred/series/observations`` with ``realtime_start=1776-07-04`` and
``realtime_end=9999-12-31`` returns every vintage of every observation: one
row per ``(date, realtime_start, realtime_end, value)``. FRED documents the
real-time period as a CLOSED interval of dates: the value was the current one
from ``realtime_start`` through ``realtime_end`` inclusive.

Those are DATES without a time or a timezone. The release behind a vintage is
usually a morning US release, but FRED does not say when on the day, so this
module stamps conservatively:

* ``available_at``  = 00:00 America/Chicago on ``realtime_start + 1 day``
  (the end of the vintage's first day in the St. Louis Fed's own timezone);
* ``superseded_at`` = 00:00 America/Chicago on ``realtime_end + 2 days``
  (the successor vintage, dated ``realtime_end + 1``, becomes available at the
  end of ITS first day), or null when ``realtime_end`` is ``9999-12-31``.

The windows therefore tile without a gap or an overlap, and a vintage is
never visible before the end of the day it was published. The cost is up to
a day of delay against the true release instant; a consumer that needs the
intraday instant must join to the official economic calendar, not loosen this.

A FRED missing value (``"."``) is kept as an explicit NaN record.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import date, time, timedelta
from typing import Any

import pandas as pd

from fiboki.data.providers.base import AuthenticationRequired, ProviderError
from fiboki.data.providers.macro_base import (
    CHICAGO,
    AvailabilityBasis,
    MacroDataset,
    MacroProviderDescriptor,
    as_of,
    http_get_text,
    local_instant,
    macro_frame,
)

__all__ = ["ALFRED_HOST", "AlfredProvider", "parse_observations", "vintage_window"]

ALFRED_HOST = "https://api.stlouisfed.org"
ENV_FRED_API_KEY = "FIBOKI_FRED_API_KEY"
PARSER_VERSION = "fiboki.data.providers.alfred/1"
REALTIME_EARLIEST = "1776-07-04"
REALTIME_LATEST = "9999-12-31"
PAGE_LIMIT = 100_000

FRED_ATTRIBUTION = (
    "This product uses the FRED® API but is not endorsed or certified by the "
    "Federal Reserve Bank of St. Louis."
)

DESCRIPTOR = MacroProviderDescriptor(
    name="alfred",
    source="Federal Reserve Bank of St. Louis, FRED/ALFRED API (api.stlouisfed.org)",
    licence=(
        "FRED API Terms of Use; series marked 'Copyright' in their notes belong to "
        "third parties and need the owner's permission for anything beyond personal use"
    ),
    licence_url="https://fred.stlouisfed.org/docs/api/terms_of_use.html",
    attribution=FRED_ATTRIBUTION,
    point_in_time=(
        "VINTAGE: each value is valid over FRED's closed real-time period "
        "[realtime_start, realtime_end]; available_at = 00:00 America/Chicago on "
        "realtime_start+1, superseded_at = 00:00 America/Chicago on realtime_end+2."
    ),
    availability_bases=(AvailabilityBasis.VINTAGE,),
    requires_credentials=True,
    credential_env=ENV_FRED_API_KEY,
    endpoints=(f"{ALFRED_HOST}/fred/series/observations",),
    known_limitations=(
        "Real-time periods are dates: up to one day of delay against the true intraday release.",
        "ALFRED vintage history begins when ALFRED started tracking a series; values "
        "before the first recorded vintage are only as good as that first vintage.",
        "Third-party copyrighted series (noted 'Copyright' by FRED) are not cleared for "
        "redistribution; check series notes before any use beyond personal research.",
        "No recorded response exists in this repository: the fixtures are constructed "
        "from the documented response shape because no API key was available.",
    ),
)


def vintage_window(realtime_start: str, realtime_end: str) -> tuple[pd.Timestamp, pd.Timestamp | None]:
    """``(available_at, superseded_at)`` for one FRED real-time period."""
    start = date.fromisoformat(realtime_start)
    available = local_instant(start + timedelta(days=1), time(0, 0), CHICAGO)
    if realtime_end == REALTIME_LATEST:
        return available, None
    end = date.fromisoformat(realtime_end)
    if end < start:
        raise ProviderError(f"real-time period ends before it starts: {realtime_start}..{realtime_end}")
    return available, local_instant(end + timedelta(days=2), time(0, 0), CHICAGO)


def parse_observations(payload: Mapping[str, Any], series_id: str) -> list[dict[str, Any]]:
    """Rows for :func:`macro_frame` from one ``series/observations`` page."""
    obs = payload.get("observations")
    if obs is None:
        raise ProviderError("FRED response has no 'observations' key")
    rows: list[dict[str, Any]] = []
    for o in obs:
        try:
            rs, re_, d, raw = o["realtime_start"], o["realtime_end"], o["date"], o["value"]
        except KeyError as exc:
            raise ProviderError(
                f"FRED observation without {exc}; ask for realtime_start/realtime_end so "
                "each row carries its vintage"
            ) from None
        available, superseded = vintage_window(rs, re_)
        value = None if str(raw).strip() == "." else float(raw)
        rows.append(
            {
                "series_id": series_id,
                "period": d,
                "period_start": pd.Timestamp(d, tz="UTC"),
                "value": value,
                "available_at": available,
                "superseded_at": superseded,
                "availability_basis": AvailabilityBasis.VINTAGE,
                "vintage": rs,
                "attributes": {"realtime_start": rs, "realtime_end": re_},
            }
        )
    return rows


class AlfredProvider:
    """FRED series observations with every vintage. Needs ``FIBOKI_FRED_API_KEY``."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_client: Any | None = None,
        host: str = ALFRED_HOST,
    ) -> None:
        self.api_key = api_key
        self.http_client = http_client
        self.host = host

    @classmethod
    def from_env(
        cls, http_client: Any | None = None, *, env: Mapping[str, str] | None = None
    ) -> AlfredProvider:
        environ = os.environ if env is None else env
        return cls(api_key=(environ.get(ENV_FRED_API_KEY) or "").strip() or None,
                   http_client=http_client)

    @property
    def descriptor(self) -> MacroProviderDescriptor:
        return DESCRIPTOR

    def describe(self) -> str:
        return DESCRIPTOR.render()

    def request_params(self, series_id: str, *, offset: int = 0) -> tuple[str, dict[str, Any]]:
        """URL and query WITHOUT the key (the key is added at send time only)."""
        return f"{self.host}/fred/series/observations", {
            "series_id": series_id,
            "realtime_start": REALTIME_EARLIEST,
            "realtime_end": REALTIME_LATEST,
            "file_type": "json",
            "output_type": 1,
            "limit": PAGE_LIMIT,
            "offset": offset,
            "sort_order": "asc",
        }

    def fetch(self, series_id: str, *, now: pd.Timestamp | None = None) -> MacroDataset:
        """Every vintage of ``series_id``, paging until FRED's ``count`` is reached."""
        if not self.api_key:
            raise AuthenticationRequired(
                f"ALFRED needs {ENV_FRED_API_KEY}. No key is configured, and this raises "
                "rather than returning nothing: a missing key is not a series with no history."
            )
        fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        rows: list[dict[str, Any]] = []
        offset = 0
        pages = 0
        while True:
            url, params = self.request_params(series_id, offset=offset)
            text = http_get_text(
                self.http_client, url, params={**params, "api_key": self.api_key},
                secrets=(self.api_key,), what=f"ALFRED {series_id}",
            )
            payload = json.loads(text)
            if "error_code" in payload:
                raise ProviderError(f"ALFRED {series_id}: {payload.get('error_message')}")
            page = parse_observations(payload, series_id)
            rows.extend(page)
            pages += 1
            count = int(payload.get("count", len(page)))
            offset += len(page)
            if not page or offset >= count:
                break
        if not rows:
            raise ProviderError(f"ALFRED returned no observations for {series_id}")
        frame = macro_frame(rows)
        _, params = self.request_params(series_id)
        params.pop("offset")
        return MacroDataset(
            provider="alfred",
            dataset_key=series_id,
            frame=frame,
            descriptor=DESCRIPTOR,
            request=params,
            parser_version=PARSER_VERSION,
            fetched_at=fetched_at,
            report={"pages": pages, "rows": len(frame),
                    "missing_values": int(frame["value"].isna().sum()),
                    "attribution": FRED_ATTRIBUTION},
        )

    def series_as_of(
        self,
        series_id: str,
        when: pd.Timestamp | str,
        *,
        dataset: MacroDataset | None = None,
    ) -> pd.Series:
        """The vintage of ``series_id`` that was current at ``when``.

        Indexed by observation date (UTC). Observations whose first vintage
        was published after ``when`` are absent, and so are revisions made
        after it. Pass ``dataset`` to query a stored fetch instead of the API.
        """
        ds = dataset if dataset is not None else self.fetch(series_id)
        view = as_of(ds.frame, when, series_ids=[series_id])
        series = pd.Series(
            view["value"].to_numpy(), index=pd.DatetimeIndex(view["period_start"]), name=series_id
        )
        series.attrs["vintage_by_date"] = dict(zip(view["period"], view["vintage"], strict=True))
        series.attrs["as_of"] = pd.Timestamp(when).isoformat()
        series.attrs["attribution"] = FRED_ATTRIBUTION
        return series
