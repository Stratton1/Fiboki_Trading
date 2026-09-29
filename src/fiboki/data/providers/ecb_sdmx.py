"""ECB Data Portal, SDMX 2.1 REST (data-api.ecb.europa.eu), CSV format.

A first-party client written from the ECB Data Portal API documentation; no
code copied.

Point-in-time semantics
-----------------------
The ECB's SDMX responses carry no publication instant per observation and,
checked on 2026-09-28, ``includeHistory=true`` returned the same single
version for ``EXR``, so there are no vintages to read. Two bases are used:

* **Daily euro reference rates** (flow ``EXR``, ``FREQ=D``): ``RELEASE_RULE``.
  The ECB states the reference rates "are usually updated at around 16:00 CET
  every working day". ``available_at`` is **17:00 Europe/Berlin on the
  observation date**: the stated time plus a one-hour margin, DST-correct.
* **Everything else**: ``FIRST_SEEN``, the instant of our fetch. That is honest
  only going forward: a backtest before the first fetch sees nothing, which is
  the correct answer for a series whose release instants we cannot establish.
  Declaring a series-specific rule is how to do better, never a default lag.

Revisions overwrite values in place at the ECB. Refetching produces a new
content-addressed version; comparing two stored versions is the only record of
a revision this source offers.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, time
from typing import Any

import pandas as pd

from fiboki.data.providers.base import ProviderError
from fiboki.data.providers.macro_base import (
    FRANKFURT,
    AvailabilityBasis,
    MacroDataset,
    MacroProviderDescriptor,
    http_get_text,
    local_instant,
    macro_frame,
)

__all__ = ["ECB_HOST", "EcbSdmxProvider", "parse_ecb_csv", "period_start"]

ECB_HOST = "https://data-api.ecb.europa.eu"
PARSER_VERSION = "fiboki.data.providers.ecb_sdmx/1"
EXR_PUBLICATION = time(17, 0)

DESCRIPTOR = MacroProviderDescriptor(
    name="ecb_sdmx",
    source="European Central Bank Data Portal, SDMX 2.1 REST API",
    licence=(
        "ESCB free access and free reuse of publicly released statistics, commercial or "
        "not, provided the source is cited and the statistics (and metadata) are not "
        "modified; third-party data needs its originator's permission"
    ),
    licence_url=(
        "https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/"
        "html/usage_policy.en.html"
    ),
    attribution="Source: ECB statistics.",
    point_in_time=(
        "EXR daily reference rates: RELEASE_RULE, available_at = 17:00 Europe/Berlin on the "
        "observation date (ECB: 'around 16:00 CET', plus one hour). All other series: "
        "FIRST_SEEN at fetch time, valid forward only."
    ),
    availability_bases=(AvailabilityBasis.RELEASE_RULE, AvailabilityBasis.FIRST_SEEN),
    requires_credentials=False,
    credential_env=None,
    endpoints=(f"{ECB_HOST}/service/data/{{flow}}/{{key}}",),
    known_limitations=(
        "No vintages: a revision overwrites the value; only stored versions record it.",
        "Non-EXR series are FIRST_SEEN, so history before our first fetch is not "
        "point-in-time usable.",
        "The EXR rule ignores TARGET closing days only in the sense that no row exists; "
        "an unusually late publication (after 17:00 CET) would make the stamp early.",
    ),
)

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")
_YEAR = re.compile(r"^(\d{4})$")
_SEMESTER = re.compile(r"^(\d{4})-S([12])$")


def period_start(label: str) -> pd.Timestamp:
    """UTC start of an SDMX ``TIME_PERIOD`` (day, ISO week, month, quarter, semester, year)."""
    text = label.strip()
    if m := _QUARTER.match(text):
        return pd.Timestamp(int(m[1]), 3 * int(m[2]) - 2, 1, tz="UTC")
    if m := _MONTH.match(text):
        return pd.Timestamp(int(m[1]), int(m[2]), 1, tz="UTC")
    if m := _SEMESTER.match(text):
        return pd.Timestamp(int(m[1]), 6 * int(m[2]) - 5, 1, tz="UTC")
    if m := _YEAR.match(text):
        return pd.Timestamp(int(m[1]), 1, 1, tz="UTC")
    try:
        return pd.Timestamp(date.fromisoformat(text), tz="UTC")
    except ValueError:
        raise ProviderError(f"unrecognised SDMX TIME_PERIOD {label!r}") from None


def parse_ecb_csv(text: str, *, fetched_at: pd.Timestamp) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows for :func:`macro_frame` from an ECB ``format=csvdata`` body."""
    if text.lstrip().startswith("<"):
        raise ProviderError("ECB returned markup, not CSV (an error page or a bad key)")
    reader = csv.DictReader(io.StringIO(text))
    needed = {"KEY", "TIME_PERIOD", "OBS_VALUE"}
    if not reader.fieldnames or not needed <= set(reader.fieldnames):
        raise ProviderError(f"ECB CSV lacks {sorted(needed)}; header={reader.fieldnames}")
    rows: list[dict[str, Any]] = []
    report = {"rows": 0, "missing_values": 0, "release_rule": 0, "first_seen": 0}
    for rec in reader:
        report["rows"] += 1
        key = rec["KEY"].strip()
        label = rec["TIME_PERIOD"].strip()
        raw = (rec.get("OBS_VALUE") or "").strip()
        value = None if raw in ("", "NaN") else float(raw)
        if value is None:
            report["missing_values"] += 1
        flow, _, _rest = key.partition(".")
        if flow == "EXR" and (rec.get("FREQ") or "").strip() == "D":
            available = local_instant(date.fromisoformat(label), EXR_PUBLICATION, FRANKFURT)
            basis = AvailabilityBasis.RELEASE_RULE
            report["release_rule"] += 1
        else:
            available = fetched_at
            basis = AvailabilityBasis.FIRST_SEEN
            report["first_seen"] += 1
        rows.append(
            {
                "series_id": key,
                "period": label,
                "period_start": period_start(label),
                "value": value,
                "available_at": available,
                "availability_basis": basis,
                "vintage": None,
                "attributes": {
                    "OBS_STATUS": (rec.get("OBS_STATUS") or "").strip(),
                    "OBS_CONF": (rec.get("OBS_CONF") or "").strip(),
                    "TITLE": (rec.get("TITLE") or "").strip(),
                    "UNIT": (rec.get("UNIT") or "").strip(),
                },
            }
        )
    return rows, report


class EcbSdmxProvider:
    """One SDMX data query: ``flow`` plus dot-separated ``key``. Public; no credentials."""

    def __init__(self, *, http_client: Any | None = None, host: str = ECB_HOST) -> None:
        self.http_client = http_client
        self.host = host

    @property
    def descriptor(self) -> MacroProviderDescriptor:
        return DESCRIPTOR

    def describe(self) -> str:
        return DESCRIPTOR.render()

    def request_params(
        self, flow: str, key: str, *, start_period: str | None = None, end_period: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        if not re.fullmatch(r"[A-Z0-9_]+", flow) or not re.fullmatch(r"[A-Za-z0-9_.+]+", key):
            raise ProviderError(f"refusing SDMX flow/key {flow!r}/{key!r}")
        params: dict[str, Any] = {"format": "csvdata"}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period
        return f"{self.host}/service/data/{flow}/{key}", params

    def fetch(
        self,
        flow: str,
        key: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
        now: pd.Timestamp | None = None,
    ) -> MacroDataset:
        fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        url, params = self.request_params(flow, key, start_period=start_period, end_period=end_period)
        text = http_get_text(self.http_client, url, params=params, what=f"ECB {flow}/{key}")
        rows, report = parse_ecb_csv(text, fetched_at=fetched_at)
        if not rows:
            raise ProviderError(f"ECB returned no observations for {flow}/{key}")
        return MacroDataset(
            provider="ecb_sdmx",
            dataset_key=f"{flow}.{key}",
            frame=macro_frame(rows),
            descriptor=DESCRIPTOR,
            request={"flow": flow, "key": key, **params},
            parser_version=PARSER_VERSION,
            fetched_at=fetched_at,
            report=report,
        )
