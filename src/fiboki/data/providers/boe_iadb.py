"""Bank of England Interactive Statistical Database (IADB), CSV download.

A first-party client written from the IADB's documented CSV query parameters;
no code copied. The IADB answers "Access Denied" to the default ``httpx``
user agent (checked 2026-09-28), so every request carries
:data:`~fiboki.data.providers.macro_base.USER_AGENT`.

Point-in-time semantics
-----------------------
The IADB has no vintages and no per-value publication stamp. Availability is
declared per series; an unregistered series is ``FIRST_SEEN``.

* ``IUDSOIA`` (SONIA, daily): ``RELEASE_RULE``. The Bank publishes the rate
  for a London business day "at 9am on the following London business day"
  and republishes, if at all, "no later than midday on the same day" (SONIA
  key features and policies page, retrieved 2026-09-28). The IADB may hold the
  republished value, so ``available_at`` = **12:00 Europe/London on the next
  London business day**, using a SUPERSET of England and Wales bank holidays.
* ``IUDBEDR`` (Bank Rate, daily): ``RELEASE_RULE``. A change is announced at
  12:00 London on the decision day and effective that day; ``available_at`` =
  **12:00 Europe/London on the observation date**, which is at or after every
  scheduled announcement. An emergency announcement is earlier, so still safe.

Blank cells (SONIA for the most recent day before it is published) are kept
as explicit NaN records and counted.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

import pandas as pd

from fiboki.data.providers.base import ProviderError
from fiboki.data.providers.macro_base import (
    LONDON,
    AvailabilityBasis,
    MacroDataset,
    MacroProviderDescriptor,
    england_bank_holidays_superset,
    http_get_text,
    local_instant,
    macro_frame,
    next_business_day,
)

__all__ = ["IADB_URL", "SERIES_RULES", "BoeIadbProvider", "parse_iadb_csv"]

IADB_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
PARSER_VERSION = "fiboki.data.providers.boe_iadb/1"


def _sonia_rule(d: date) -> pd.Timestamp:
    return local_instant(next_business_day(d, england_bank_holidays_superset), time(12, 0), LONDON)


def _bank_rate_rule(d: date) -> pd.Timestamp:
    return local_instant(d, time(12, 0), LONDON)


#: Series code -> rule from observation date to available_at.
SERIES_RULES: dict[str, Callable[[date], pd.Timestamp]] = {
    "IUDSOIA": _sonia_rule,
    "IUDBEDR": _bank_rate_rule,
}

DESCRIPTOR = MacroProviderDescriptor(
    name="boe_iadb",
    source="Bank of England, Interactive Statistical Database (IADB)",
    licence=(
        "UK Open Government Licence v3.0; selected exchange-rate series are reproduced "
        "under third-party licence and are EXCLUDED from it"
    ),
    licence_url="https://www.bankofengland.co.uk/legal",
    attribution=(
        "Contains Bank of England data licensed under the Open Government Licence v3.0. "
        "SONIA: 'SONIA and/or SONIA Compounded Index data licensed under the Open "
        "Government Licence v3.0 and copyright the Governor and Company of the Bank of England.'"
    ),
    point_in_time=(
        "RELEASE_RULE for registered series: IUDSOIA -> 12:00 London on the next London "
        "business day (after the republication deadline); IUDBEDR -> 12:00 London on the "
        "observation date. Unregistered series: FIRST_SEEN at fetch time."
    ),
    availability_bases=(AvailabilityBasis.RELEASE_RULE, AvailabilityBasis.FIRST_SEEN),
    requires_credentials=False,
    credential_env=None,
    endpoints=(IADB_URL,),
    known_limitations=(
        "No vintages; a correction overwrites the IADB value in place.",
        "The bank-holiday set is a superset (moved holidays keep their original date too), "
        "so a SONIA stamp can be one business day late, never early.",
        "Unregistered series are FIRST_SEEN: history before our first fetch is not "
        "point-in-time usable until a rule is declared in SERIES_RULES.",
        "Exchange-rate series are third-party licensed and must not be redistributed.",
    ),
)


def parse_iadb_csv(
    text: str, *, fetched_at: pd.Timestamp
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows from a ``CSVF=TN`` (tabular, no titles) IADB download."""
    if text.lstrip()[:1] == "<":
        raise ProviderError("IADB returned markup, not CSV (access denied or a bad series code)")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or header[0].strip().upper() != "DATE" or len(header) < 2:
        raise ProviderError(f"IADB CSV header not understood: {header}")
    codes = [h.strip() for h in header[1:]]
    rows: list[dict[str, Any]] = []
    report = {"dates": 0, "missing_values": 0, "first_seen": 0, "release_rule": 0}
    for line in reader:
        if not line or not line[0].strip():
            continue
        report["dates"] += 1
        d = datetime.strptime(line[0].strip(), "%d %b %Y").date()
        for code, raw in zip(codes, line[1:] + [""] * (len(codes) - len(line) + 1), strict=False):
            raw = raw.strip()
            value = None if raw == "" else float(raw)
            if value is None:
                report["missing_values"] += 1
            rule = SERIES_RULES.get(code)
            if rule is None:
                available, basis = fetched_at, AvailabilityBasis.FIRST_SEEN
                report["first_seen"] += 1
            else:
                available, basis = rule(d), AvailabilityBasis.RELEASE_RULE
                report["release_rule"] += 1
            rows.append(
                {
                    "series_id": code,
                    "period": d.isoformat(),
                    "period_start": pd.Timestamp(d, tz="UTC"),
                    "value": value,
                    "available_at": available,
                    "availability_basis": basis,
                    "vintage": None,
                    "attributes": {},
                }
            )
    return rows, report


class BoeIadbProvider:
    """Daily IADB series by code. Public; no credentials."""

    def __init__(self, *, http_client: Any | None = None, url: str = IADB_URL) -> None:
        self.http_client = http_client
        self.url = url

    @property
    def descriptor(self) -> MacroProviderDescriptor:
        return DESCRIPTOR

    def describe(self) -> str:
        return DESCRIPTOR.render()

    def request_params(
        self, series_codes: tuple[str, ...], *, start: date, end: date | None = None
    ) -> tuple[str, dict[str, Any]]:
        for c in series_codes:
            if not c.isalnum():
                raise ProviderError(f"IADB series code {c!r} is not alphanumeric")
        return self.url, {
            "csv.x": "yes",
            "Datefrom": start.strftime("%d/%b/%Y"),
            "Dateto": "now" if end is None else end.strftime("%d/%b/%Y"),
            "SeriesCodes": ",".join(series_codes),
            "CSVF": "TN",
            "UsingCodes": "Y",
            "VPD": "Y",
            "VFD": "N",
        }

    def fetch(
        self,
        series_codes: tuple[str, ...],
        *,
        start: date,
        end: date | None = None,
        now: pd.Timestamp | None = None,
    ) -> MacroDataset:
        if not series_codes:
            raise ProviderError("name at least one IADB series code (e.g. IUDSOIA)")
        fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        url, params = self.request_params(series_codes, start=start, end=end)
        text = http_get_text(self.http_client, url, params=params, what="BoE IADB")
        rows, report = parse_iadb_csv(text, fetched_at=fetched_at)
        if not rows:
            raise ProviderError(f"IADB returned no rows for {series_codes} from {start}")
        if end is None:
            # "now" is resolved by the server; pin it in the lineage so two
            # fetches on different days are not mistaken for the same request.
            params = {**params, "Dateto": f"now@{fetched_at.date().isoformat()}"}
        return MacroDataset(
            provider="boe_iadb",
            dataset_key="-".join(sorted(series_codes)),
            frame=macro_frame(rows),
            descriptor=DESCRIPTOR,
            request=params,
            parser_version=PARSER_VERSION,
            fetched_at=fetched_at,
            report=report,
        )
