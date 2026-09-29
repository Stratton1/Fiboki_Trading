"""Federal Reserve Bank of New York Markets Data API: reference rates and repo operations.

A first-party client written from the documented Markets Data API
(markets.newyorkfed.org); no code copied.

Point-in-time semantics
-----------------------
**Reference rates** (SOFR, BGCR, TGCR, EFFR, OBFR): the rate for business day
D is published on the next business day at about 08:00 ET, and "rate
revisions will only occur ... on the same day as initial publication" at about
14:30 ET (NY Fed, Additional Information about Reference Rates, retrieved
2026-09-28). The API serves the final value, so ``available_at`` =
**15:00 America/New_York on the next business day** after ``effectiveDate``
(``RELEASE_RULE``), with business days taken from a SUPERSET of SIFMA
full-closure days (federal holidays, Good Friday, known ad-hoc closures).

**Repo and reverse repo operations**: each operation carries ``lastUpdated``,
the New York local instant its results were posted. ``available_at`` is that
instant (``SOURCE_TIMESTAMP``); a later correction can only move it later.
"""
from __future__ import annotations

import json
import re
from datetime import date, time
from typing import Any

import pandas as pd

from fiboki.data.providers.base import ProviderError
from fiboki.data.providers.macro_base import (
    NEW_YORK,
    AvailabilityBasis,
    MacroDataset,
    MacroProviderDescriptor,
    http_get_text,
    local_instant,
    macro_frame,
    next_business_day,
    us_bond_market_holidays_superset,
)

__all__ = ["NYFED_HOST", "RATE_PATHS", "NyFedMarketsProvider", "parse_rates", "parse_repo"]

NYFED_HOST = "https://markets.newyorkfed.org"
PARSER_VERSION = "fiboki.data.providers.nyfed/1"
RATE_AVAILABLE_ET = time(15, 0)

#: Rate type -> API path segment ("secured" or "unsecured").
RATE_PATHS: dict[str, str] = {
    "sofr": "secured",
    "bgcr": "secured",
    "tgcr": "secured",
    "effr": "unsecured",
    "obfr": "unsecured",
}
RATE_FIELDS: tuple[str, ...] = (
    "percentRate",
    "percentPercentile1",
    "percentPercentile25",
    "percentPercentile75",
    "percentPercentile99",
    "volumeInBillions",
)
REPO_FIELDS: tuple[str, ...] = ("totalAmtSubmitted", "totalAmtAccepted")

NYFED_NOTICE = (
    "The reference rate and markets data are subject to the Terms of Use posted at "
    "newyorkfed.org. The New York Fed is not responsible for publication of the data by "
    "Fiboki, does not sanction or endorse any particular republication, and has no "
    "liability for your use. Fiboki is not affiliated with the New York Fed."
)

DESCRIPTOR = MacroProviderDescriptor(
    name="nyfed",
    source="Federal Reserve Bank of New York, Markets Data API (markets.newyorkfed.org)",
    licence=(
        "New York Fed Terms of Use: reference-rate data may be used with the prescribed "
        "notice and non-endorsement disclaimer; SOFR/BGCR use licensed DTCC Solutions data"
    ),
    licence_url="https://www.newyorkfed.org/privacy/termsofuse",
    attribution=NYFED_NOTICE,
    point_in_time=(
        "Reference rates: RELEASE_RULE, 15:00 America/New_York on the next business day "
        "after effectiveDate (after the 14:30 ET same-day revision window). Repo "
        "operations: SOURCE_TIMESTAMP from lastUpdated (America/New_York)."
    ),
    availability_bases=(AvailabilityBasis.RELEASE_RULE, AvailabilityBasis.SOURCE_TIMESTAMP),
    requires_credentials=False,
    credential_env=None,
    endpoints=(
        f"{NYFED_HOST}/api/rates/{{secured|unsecured}}/{{type}}/search.json",
        f"{NYFED_HOST}/api/rp/results/search.json",
    ),
    known_limitations=(
        "The business-day set is a superset of SIFMA closures, so a stamp can be a day "
        "late when SIFMA recommended only an early close.",
        "Reference-rate stamps are 7 hours after the 08:00 ET first publication, to cover "
        "the revision window; intraday use of the first print is not modelled.",
        "Pre-revision first prints are not served by the API and are not recoverable.",
    ),
)


def parse_rates(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    refs = payload.get("refRates")
    if refs is None:
        raise ProviderError("NY Fed rates response has no 'refRates'")
    rows: list[dict[str, Any]] = []
    report = {"records": 0, "revised": 0}
    for r in refs:
        report["records"] += 1
        eff = date.fromisoformat(r["effectiveDate"])
        rtype = str(r["type"]).upper()
        available = local_instant(
            next_business_day(eff, us_bond_market_holidays_superset), RATE_AVAILABLE_ET, NEW_YORK
        )
        if str(r.get("revisionIndicator", "")).strip():
            report["revised"] += 1
        for f in RATE_FIELDS:
            if f not in r:
                continue
            rows.append(
                {
                    "series_id": f"{rtype}:{f}",
                    "period": eff.isoformat(),
                    "period_start": pd.Timestamp(eff, tz="UTC"),
                    "value": None if r[f] is None else float(r[f]),
                    "available_at": available,
                    "availability_basis": AvailabilityBasis.RELEASE_RULE,
                    "vintage": None,
                    "attributes": {"revisionIndicator": r.get("revisionIndicator", "")},
                }
            )
    return rows, report


def parse_repo(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        ops = payload["repo"]["operations"]
    except (KeyError, TypeError):
        raise ProviderError("NY Fed repo response has no repo.operations") from None
    rows: list[dict[str, Any]] = []
    report = {"operations": 0, "not_results": 0}
    for op in ops:
        report["operations"] += 1
        if op.get("auctionStatus") != "Results":
            report["not_results"] += 1
            continue
        posted = pd.Timestamp(op["lastUpdated"]).tz_localize(NEW_YORK).tz_convert("UTC")
        series = f"RP:{op['operationType']}:{op['term']}".replace(" ", "_")
        for f in REPO_FIELDS:
            if f not in op:
                continue
            rows.append(
                {
                    "series_id": f"{series}:{f}",
                    "period": str(op["operationId"]),
                    "period_start": pd.Timestamp(date.fromisoformat(op["operationDate"]), tz="UTC"),
                    "value": float(op[f]),
                    "available_at": posted,
                    "availability_basis": AvailabilityBasis.SOURCE_TIMESTAMP,
                    "vintage": None,
                    "attributes": {
                        "operationMethod": op.get("operationMethod"),
                        "settlementDate": op.get("settlementDate"),
                        "maturityDate": op.get("maturityDate"),
                        "lastUpdated": op.get("lastUpdated"),
                    },
                }
            )
    return rows, report


class NyFedMarketsProvider:
    """Reference rates and repo operation results. Public; no credentials."""

    def __init__(self, *, http_client: Any | None = None, host: str = NYFED_HOST) -> None:
        self.http_client = http_client
        self.host = host

    @property
    def descriptor(self) -> MacroProviderDescriptor:
        return DESCRIPTOR

    def describe(self) -> str:
        return DESCRIPTOR.render()

    def _dates(self, start: date, end: date) -> dict[str, str]:
        if end < start:
            raise ProviderError(f"end {end} is before start {start}")
        return {"startDate": start.isoformat(), "endDate": end.isoformat()}

    def rate_request(self, rate: str, start: date, end: date) -> tuple[str, dict[str, Any]]:
        key = rate.lower()
        if key not in RATE_PATHS:
            raise ProviderError(f"unknown NY Fed reference rate {rate!r}; known: {sorted(RATE_PATHS)}")
        return f"{self.host}/api/rates/{RATE_PATHS[key]}/{key}/search.json", self._dates(start, end)

    def fetch(
        self, rate: str, *, start: date, end: date, now: pd.Timestamp | None = None
    ) -> MacroDataset:
        """Reference rate observations with effective dates in ``[start, end]``."""
        fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        url, params = self.rate_request(rate, start, end)
        payload = json.loads(http_get_text(self.http_client, url, params=params, what=f"NY Fed {rate}"))
        rows, report = parse_rates(payload)
        if not rows:
            raise ProviderError(f"NY Fed returned no {rate} observations for {start}..{end}")
        return MacroDataset(
            provider="nyfed", dataset_key=rate.lower(), frame=macro_frame(rows),
            descriptor=DESCRIPTOR, request={"path": re.sub(r"^https?://[^/]+", "", url), **params},
            parser_version=PARSER_VERSION, fetched_at=fetched_at, report=report,
        )

    def fetch_repo(
        self, *, start: date, end: date, now: pd.Timestamp | None = None
    ) -> MacroDataset:
        """Repo and reverse repo operation results with operation dates in ``[start, end]``."""
        fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        url = f"{self.host}/api/rp/results/search.json"
        params = self._dates(start, end)
        payload = json.loads(http_get_text(self.http_client, url, params=params, what="NY Fed repo"))
        rows, report = parse_repo(payload)
        if not rows:
            raise ProviderError(f"NY Fed returned no repo results for {start}..{end}")
        return MacroDataset(
            provider="nyfed", dataset_key="repo", frame=macro_frame(rows),
            descriptor=DESCRIPTOR, request={"path": "/api/rp/results/search.json", **params},
            parser_version=PARSER_VERSION, fetched_at=fetched_at, report=report,
        )
