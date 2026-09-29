"""CFTC Commitments of Traders via the public Socrata API (publicreporting.cftc.gov).

A first-party client written from the Socrata SODA query conventions and the
CFTC's published release schedule; no code copied.

Point-in-time semantics: the Friday release rule
------------------------------------------------
A COT report describes positions "as of" a Tuesday close and is released at
**15:30 America/New_York on the Friday** of the same week (CFTC Release
Schedule page, retrieved 2026-09-28). The dataset row carries only the as-of
date, so ``available_at`` is computed by :func:`cot_release_time`:

1. **Published overrides** (``RELEASE_OVERRIDE``): the post-shutdown backlog
   table in CFTC press release 9147-25 (as-of 2025-09-30 .. 2025-12-23), and
   the first delayed report of the 2018-19 lapse (as-of 2018-12-24, released
   2019-02-01, CFTC press release 7864-19). The CFTC gives dates, not times;
   15:30 ET is assumed.
2. **Unresolved windows** (``UNRESOLVED``, never returned by ``as_of``): the
   remaining 2018-19 catch-up (Tuesday/Friday releases "until current", no
   per-report dates published) and the October 2013 lapse. The bounds are
   deliberately WIDE rather than verified: excluding a few good reports is
   cheap, stamping a delayed one on its normal Friday is look-ahead.
3. **The rule** (``RELEASE_RULE``): the first Friday after the as-of date at
   15:30 ET; if a US federal holiday falls on any weekday after the as-of date
   up to and including that Friday, the release moves to the next business
   day after the Friday (Monday, or later if that is also a holiday). This
   reproduces all six 2026 exceptions on the CFTC schedule (Jan 5, Jun 22,
   Jul 6, Nov 16, Nov 30, Dec 28) and leaves Monday-holiday weeks on Friday,
   as the schedule does.

Known approximation, stated: ad-hoc federal closures by executive order (for
example a Christmas Eve closure) and any future appropriations lapse are not
in the rule. They can only make the true release LATER than the stamp, so each
must be added to the override table or the unresolved windows when it
happens. The Socrata system field ``:created_at`` is not used: bulk reloads
reset it and it says nothing reliable about first publication.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, time, timedelta
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
    us_federal_holidays,
)

__all__ = [
    "COT_DATASETS",
    "CftcCotProvider",
    "cot_release_time",
    "parse_cot_rows",
]

CFTC_HOST = "https://publicreporting.cftc.gov"
PARSER_VERSION = "fiboki.data.providers.cftc_cot/1"
RELEASE_TIME_ET = time(15, 30)

#: Socrata dataset ids (publicreporting.cftc.gov). TFF is where the currency
#: futures live with the dealer / asset-manager / leveraged-funds split.
COT_DATASETS: dict[str, str] = {
    "tff_futures_only": "gpe5-46if",
    "legacy_futures_only": "6dca-aqww",
    "disaggregated_futures_only": "72hh-3qpy",
}

#: Default TFF position fields recorded as separate long-format series.
TFF_FIELDS: tuple[str, ...] = (
    "open_interest_all",
    "dealer_positions_long_all",
    "dealer_positions_short_all",
    "asset_mgr_positions_long",
    "asset_mgr_positions_short",
    "lev_money_positions_long",
    "lev_money_positions_short",
    "other_rept_positions_long",
    "other_rept_positions_short",
    "nonrept_positions_long_all",
    "nonrept_positions_short_all",
)

#: As-of date -> actual release date, from CFTC press releases 9147-25 (2025
#: backlog) and 7864-19 (first report after the 2018-19 lapse).
RELEASE_OVERRIDES: dict[date, date] = {
    date(2018, 12, 24): date(2019, 2, 1),
    date(2025, 9, 30): date(2025, 11, 19),
    date(2025, 10, 7): date(2025, 11, 21),
    date(2025, 10, 14): date(2025, 11, 25),
    date(2025, 10, 21): date(2025, 12, 2),
    date(2025, 10, 28): date(2025, 12, 5),
    date(2025, 11, 4): date(2025, 12, 9),
    date(2025, 11, 10): date(2025, 12, 10),
    date(2025, 11, 18): date(2025, 12, 12),
    date(2025, 11, 25): date(2025, 12, 15),
    date(2025, 12, 2): date(2025, 12, 17),
    date(2025, 12, 9): date(2025, 12, 19),
    date(2025, 12, 16): date(2025, 12, 23),
    date(2025, 12, 23): date(2025, 12, 29),
}

#: As-of date ranges (inclusive) whose release instant is not established.
#: WIDE bounds by design; see the module docstring.
UNRESOLVED_WINDOWS: tuple[tuple[date, date, str], ...] = (
    (date(2013, 9, 24), date(2013, 11, 30),
     "October 2013 appropriations lapse; catch-up schedule not recorded here"),
    (date(2018, 12, 18), date(2019, 3, 31),
     "2018-19 lapse; CFTC published catch-up only as 'Tuesday and Friday until current'"),
)

DESCRIPTOR = MacroProviderDescriptor(
    name="cftc_cot",
    source="U.S. Commodity Futures Trading Commission, Commitments of Traders (Socrata API)",
    licence=(
        "Public domain US government information; the CFTC requests appropriate "
        "acknowledgement in any subsequent use"
    ),
    licence_url="https://www.cftc.gov/WebPolicy/index.htm",
    attribution="Source: U.S. Commodity Futures Trading Commission, Commitments of Traders.",
    point_in_time=(
        "RELEASE_RULE: as-of Tuesday -> Friday 15:30 America/New_York, moved to the next "
        "business day when a federal holiday falls between the as-of date and that Friday; "
        "RELEASE_OVERRIDE for published backlog schedules; UNRESOLVED (never served) for "
        "lapse windows without a published per-report schedule."
    ),
    availability_bases=(
        AvailabilityBasis.RELEASE_OVERRIDE,
        AvailabilityBasis.RELEASE_RULE,
        AvailabilityBasis.UNRESOLVED,
    ),
    requires_credentials=False,
    credential_env=None,
    endpoints=tuple(f"{CFTC_HOST}/resource/{v}.json" for v in COT_DATASETS.values()),
    known_limitations=(
        "Ad-hoc executive-order closures and future appropriations lapses are not in the "
        "rule and would make a stamp EARLY until added to the overrides.",
        "Override release times are assumed to be 15:30 ET; the CFTC published dates only.",
        "Unresolved windows (2013, 2018-19) are wide by design and exclude some good rows.",
        "COT figures are occasionally corrected after release; a correction overwrites the "
        "Socrata row and the pre-correction value is not recoverable from this API.",
    ),
)


def cot_release_time(as_of_date: date) -> tuple[pd.Timestamp | None, AvailabilityBasis]:
    """``(available_at, basis)`` for a report with this as-of date."""
    if as_of_date in RELEASE_OVERRIDES:
        return (
            local_instant(RELEASE_OVERRIDES[as_of_date], RELEASE_TIME_ET, NEW_YORK),
            AvailabilityBasis.RELEASE_OVERRIDE,
        )
    for lo, hi, _why in UNRESOLVED_WINDOWS:
        if lo <= as_of_date <= hi:
            return None, AvailabilityBasis.UNRESOLVED
    friday = as_of_date + timedelta(days=(4 - as_of_date.weekday()) % 7 or 7)
    between = [as_of_date + timedelta(days=i) for i in range(1, (friday - as_of_date).days + 1)]
    holidays = us_federal_holidays(as_of_date.year) | us_federal_holidays(friday.year)
    release = friday
    if any(d in holidays for d in between if d.weekday() < 5):
        release = next_business_day(friday, us_federal_holidays)
    return local_instant(release, RELEASE_TIME_ET, NEW_YORK), AvailabilityBasis.RELEASE_RULE


def parse_cot_rows(
    records: Iterable[dict[str, Any]], *, fields: tuple[str, ...] = TFF_FIELDS
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Long-format rows (series ``<contract_code>:<field>``) plus a parse report."""
    rows: list[dict[str, Any]] = []
    report = {"records": 0, "missing_field": 0, "unresolved": 0}
    for rec in records:
        report["records"] += 1
        try:
            raw_date = rec["report_date_as_yyyy_mm_dd"]
            code = str(rec["cftc_contract_market_code"]).strip()
        except KeyError as exc:
            raise ProviderError(f"COT record without {exc}") from None
        as_of_date = date.fromisoformat(str(raw_date)[:10])
        available, basis = cot_release_time(as_of_date)
        if basis is AvailabilityBasis.UNRESOLVED:
            report["unresolved"] += 1
        for f in fields:
            raw = rec.get(f)
            if raw is None or str(raw).strip() == "":
                report["missing_field"] += 1
                value = None
            else:
                value = float(raw)
            rows.append(
                {
                    "series_id": f"{code}:{f}",
                    "period": as_of_date.isoformat(),
                    "period_start": pd.Timestamp(as_of_date, tz="UTC"),
                    "value": value,
                    "available_at": available,
                    "availability_basis": basis,
                    "vintage": None,
                    "attributes": {
                        "market": rec.get("market_and_exchange_names"),
                        "row_id": rec.get("id"),
                    },
                }
            )
    return rows, report


class CftcCotProvider:
    """COT reports for chosen contract codes. Public; no credentials."""

    def __init__(
        self,
        *,
        http_client: Any | None = None,
        report: str = "tff_futures_only",
        host: str = CFTC_HOST,
        page_size: int = 50_000,
    ) -> None:
        if report not in COT_DATASETS:
            raise ProviderError(f"unknown COT report {report!r}; known: {sorted(COT_DATASETS)}")
        self.http_client = http_client
        self.report = report
        self.host = host
        self.page_size = page_size

    @property
    def descriptor(self) -> MacroProviderDescriptor:
        return DESCRIPTOR

    def describe(self) -> str:
        return DESCRIPTOR.render()

    def request_params(
        self, contract_codes: tuple[str, ...], *, since: date | None = None, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        for c in contract_codes:
            if not c.isalnum():
                raise ProviderError(f"contract code {c!r} is not alphanumeric")
        quoted = ",".join(f"'{c}'" for c in contract_codes)
        where = f"cftc_contract_market_code in({quoted})"
        if since is not None:
            where += f" AND report_date_as_yyyy_mm_dd >= '{since.isoformat()}T00:00:00.000'"
        return f"{self.host}/resource/{COT_DATASETS[self.report]}.json", {
            "$where": where,
            "$order": "report_date_as_yyyy_mm_dd ASC, cftc_contract_market_code ASC",
            "$limit": self.page_size,
            "$offset": offset,
        }

    def fetch(
        self,
        contract_codes: tuple[str, ...],
        *,
        since: date | None = None,
        fields: tuple[str, ...] = TFF_FIELDS,
        now: pd.Timestamp | None = None,
    ) -> MacroDataset:
        if not contract_codes:
            raise ProviderError("name at least one CFTC contract market code (e.g. 099741 = EUR)")
        fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        records: list[dict[str, Any]] = []
        offset = 0
        while True:
            url, params = self.request_params(contract_codes, since=since, offset=offset)
            page = json.loads(http_get_text(self.http_client, url, params=params, what="CFTC COT"))
            if not isinstance(page, list):
                raise ProviderError(f"CFTC COT: expected a JSON array, got {type(page).__name__}")
            records.extend(page)
            offset += len(page)
            if len(page) < self.page_size:
                break
        if not records:
            raise ProviderError(f"CFTC COT returned no rows for {contract_codes} since {since}")
        rows, report = parse_cot_rows(records, fields=fields)
        _, params = self.request_params(contract_codes, since=since)
        params.pop("$offset")
        return MacroDataset(
            provider="cftc_cot",
            dataset_key=f"{self.report}:{'-'.join(sorted(contract_codes))}",
            frame=macro_frame(rows),
            descriptor=DESCRIPTOR,
            request={**params, "fields": list(fields)},
            parser_version=PARSER_VERSION,
            fetched_at=fetched_at,
            report=report,
        )
