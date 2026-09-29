"""Office for National Statistics time series, via the ONS website's JSON view.

A first-party client written from the shape of the ONS website's time-series
``/data`` documents; no code copied.

Which ONS API, and why
----------------------
``api.ons.gov.uk/timeseries`` was retired on 25 November 2024 (its own
response says so), and the CMD dataset API at ``api.beta.ons.gov.uk`` marks
``cpih01`` "no longer being updated" (data to January 2026). What remains is
the public time-series page with a ``/data`` suffix, e.g.
``https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data``,
which returns JSON. A series is addressed by its CDID and dataset id, and the
URL also needs the topic path, so :data:`KNOWN_SERIES` records the paths that
were checked on 2026-09-28; any other path can be passed explicitly.

Point-in-time semantics (this is the release-calendar-aware provider)
--------------------------------------------------------------------
The document exposes three things this module uses:

* each observation's ``updateDate``: the London DATE its current value was
  published (stamped as local midnight);
* ``versions[]``: every archived previous version with an ``updateDate`` that
  is the exact instant it was SUPERSEDED, i.e. the publication instant of the
  next release (``2026-09-16T06:00:00Z`` = 07:00 BST for August 2026 CPI);
  each is fetchable at ``<uri>/data``, which gives real vintages;
* ``description.releaseDate`` and ``description.nextRelease``: the release
  calendar entry for this series, recorded in the fetch report.

``available_at`` for an observation is its ``updateDate`` DATE resolved to an
exact release instant from the versions table when a trustworthy one falls on
that London date (``SOURCE_TIMESTAMP``); otherwise **09:30 Europe/London** on
that date (``RELEASE_RULE``), the later of ONS's two standard release times
(09:30 historically, 07:00 now), so the rule is never early for a standard
release. "Trustworthy" is defined in :func:`_release_instants`: some archive
stamps precede the publication they belong to and are never used to make a
stamp earlier.

With ``include_previous`` the archived versions are fetched as well, and a
revised observation appears once per distinct ``(value, available_at)``;
:func:`~fiboki.data.providers.macro_base.as_of` then serves the value that was
current at the query instant.
"""
from __future__ import annotations

import json
import re
from datetime import time
from typing import Any

import pandas as pd

from fiboki.data.providers.base import ProviderError
from fiboki.data.providers.macro_base import (
    LONDON,
    AvailabilityBasis,
    MacroDataset,
    MacroProviderDescriptor,
    http_get_text,
    local_instant,
    macro_frame,
)

__all__ = ["KNOWN_SERIES", "ONS_HOST", "OnsTimeseriesProvider", "parse_ons_document"]

ONS_HOST = "https://www.ons.gov.uk"
PARSER_VERSION = "fiboki.data.providers.ons/1"
DATE_ONLY_RELEASE = time(9, 30)

#: (CDID, dataset id) -> topic path, each checked against the live site on 2026-09-28.
KNOWN_SERIES: dict[tuple[str, str], str] = {
    ("D7G7", "MM23"): "/economy/inflationandpriceindices/timeseries/d7g7/mm23",
    ("L55O", "MM23"): "/economy/inflationandpriceindices/timeseries/l55o/mm23",
    ("MGSX", "LMS"): "/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms",
    ("ECY2", "MGDP"): "/economy/grossdomesticproductgdp/timeseries/ecy2/mgdp",
}

DESCRIPTOR = MacroProviderDescriptor(
    name="ons",
    source="Office for National Statistics, website time-series JSON (www.ons.gov.uk/.../data)",
    licence="UK Open Government Licence v3.0",
    licence_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    attribution=(
        "Source: Office for National Statistics licensed under the Open Government Licence v3.0."
    ),
    point_in_time=(
        "Per-observation updateDate resolved to the exact release instant from the archived "
        "versions table (SOURCE_TIMESTAMP), else 09:30 Europe/London on that date "
        "(RELEASE_RULE); archived versions give vintages; releaseDate/nextRelease recorded."
    ),
    availability_bases=(AvailabilityBasis.SOURCE_TIMESTAMP, AvailabilityBasis.RELEASE_RULE),
    requires_credentials=False,
    credential_env=None,
    endpoints=(f"{ONS_HOST}/<topic>/timeseries/<cdid>/<dataset>/data",
               f"{ONS_HOST}/<topic>/timeseries/<cdid>/<dataset>/previous/v<N>/data"),
    known_limitations=(
        "The website JSON is not a versioned public API; its shape can change without notice.",
        "Without include_previous, a value revised after its first release is visible only "
        "from the revision date: history is point-in-time correct but thin.",
        "The 09:30 date-only rule is safe for ONS's standard 07:00 and 09:30 releases; a "
        "non-standard later release on the same day would be stamped early.",
        "Topic paths are needed in the URL; only KNOWN_SERIES were verified.",
    ),
)

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _period_start(label: str) -> pd.Timestamp:
    parts = label.strip().upper().split()
    if len(parts) == 1 and parts[0].isdigit():
        return pd.Timestamp(int(parts[0]), 1, 1, tz="UTC")
    if len(parts) == 2 and parts[1] in _MONTHS:
        return pd.Timestamp(int(parts[0]), _MONTHS[parts[1]], 1, tz="UTC")
    if len(parts) == 2 and re.fullmatch(r"Q[1-4]", parts[1]):
        return pd.Timestamp(int(parts[0]), 3 * int(parts[1][1]) - 2, 1, tz="UTC")
    raise ProviderError(f"unrecognised ONS period {label!r}")


def _is_round(ts: pd.Timestamp) -> bool:
    """A publication-system stamp: on the hour or half hour, zero seconds."""
    return ts.second == 0 and ts.microsecond == 0 and ts.nanosecond == 0 and ts.minute in (0, 30)


def _release_instants(doc: dict[str, Any]) -> dict[Any, tuple[pd.Timestamp, AvailabilityBasis]]:
    """London date -> (release instant, basis), from archived-version supersession stamps.

    Measured on D7G7 (2026-09-28): stamps on the hour or half hour match the
    ONS release times (06:00Z = 07:00 BST, 09:30Z = 09:30 GMT), but many
    2018-2021 stamps are irregular and some PRECEDE the publication (for
    example 05:07:24Z on a 07:00 BST release day): the archive was prepared
    before the release. So a round stamp is trusted as the instant, an
    irregular one only ever moves the 09:30 London date-only rule LATER, and
    with several stamps on one date the latest wins.
    """
    out: dict[Any, tuple[pd.Timestamp, AvailabilityBasis]] = {}
    for v in doc.get("versions") or []:
        ts = pd.Timestamp(v["updateDate"])
        local = ts.tz_convert(LONDON)
        if (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0):
            continue  # date-only: not an instant
        day = local.date()
        floor = local_instant(day, DATE_ONLY_RELEASE, LONDON)
        if _is_round(ts) or ts.tz_convert("UTC") > floor:
            cand = (ts.tz_convert("UTC"), AvailabilityBasis.SOURCE_TIMESTAMP)
        else:
            cand = (floor, AvailabilityBasis.RELEASE_RULE)
        if day not in out or cand[0] > out[day][0]:
            out[day] = cand
    return out


def _availability(
    update_date: str, instants: dict[Any, tuple[pd.Timestamp, AvailabilityBasis]]
) -> tuple[pd.Timestamp, AvailabilityBasis]:
    local = pd.Timestamp(update_date).tz_convert(LONDON)
    if (local.hour, local.minute, local.second, local.microsecond) != (0, 0, 0, 0):
        # A stamp with a time of day: trusted only if round, else never earlier than 09:30.
        ts = local.tz_convert("UTC")
        floor = local_instant(local.date(), DATE_ONLY_RELEASE, LONDON)
        if _is_round(ts) or ts > floor:
            return ts, AvailabilityBasis.SOURCE_TIMESTAMP
        return floor, AvailabilityBasis.RELEASE_RULE
    exact = instants.get(local.date())
    if exact is not None:
        return exact
    return local_instant(local.date(), DATE_ONLY_RELEASE, LONDON), AvailabilityBasis.RELEASE_RULE


def parse_ons_document(
    doc: dict[str, Any],
    *,
    series_id: str,
    instants: dict[Any, tuple[pd.Timestamp, AvailabilityBasis]] | None = None,
    vintage: str = "current",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows from one ONS time-series ``/data`` document (current or archived)."""
    if doc.get("type") != "timeseries":
        raise ProviderError(f"ONS document type {doc.get('type')!r} is not 'timeseries'")
    known = dict(instants or {})
    known.update(_release_instants(doc))
    rows: list[dict[str, Any]] = []
    report: dict[str, Any] = {"observations": 0, "missing_values": 0,
                              "source_timestamp": 0, "release_rule": 0}
    for bucket in ("years", "quarters", "months"):
        for o in doc.get(bucket) or []:
            report["observations"] += 1
            raw = str(o.get("value", "")).strip()
            value = None if raw in ("", "x", "..") else float(raw)
            if value is None:
                report["missing_values"] += 1
            if not o.get("updateDate"):
                raise ProviderError(f"ONS {series_id} {o.get('date')}: no updateDate")
            available, basis = _availability(o["updateDate"], known)
            report[basis.value] += 1
            rows.append(
                {
                    "series_id": f"{series_id}:{bucket}",
                    "period": str(o["date"]),
                    "period_start": _period_start(str(o["date"])),
                    "value": value,
                    "available_at": available,
                    "availability_basis": basis,
                    "vintage": vintage,
                    "attributes": {"label": o.get("label"), "updateDate": o["updateDate"]},
                }
            )
    desc = doc.get("description") or {}
    report["release_date"] = desc.get("releaseDate")
    report["next_release"] = desc.get("nextRelease")
    report["title"] = desc.get("title")
    return rows, report


class OnsTimeseriesProvider:
    """ONS time series by (CDID, dataset). Public; no credentials."""

    def __init__(self, *, http_client: Any | None = None, host: str = ONS_HOST) -> None:
        self.http_client = http_client
        self.host = host

    @property
    def descriptor(self) -> MacroProviderDescriptor:
        return DESCRIPTOR

    def describe(self) -> str:
        return DESCRIPTOR.render()

    def topic_path(self, cdid: str, dataset: str, path: str | None = None) -> str:
        if path is not None:
            if not re.fullmatch(r"/[a-z0-9/]+", path):
                raise ProviderError(f"refusing ONS topic path {path!r}")
            return path
        try:
            return KNOWN_SERIES[(cdid.upper(), dataset.upper())]
        except KeyError:
            raise ProviderError(
                f"ONS {cdid}/{dataset} has no verified topic path; pass path= explicitly"
            ) from None

    def _get(self, path: str) -> dict[str, Any]:
        text = http_get_text(self.http_client, f"{self.host}{path}/data", what=f"ONS {path}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ProviderError(f"ONS {path}/data did not return JSON") from None

    def fetch(
        self,
        cdid: str,
        dataset: str,
        *,
        path: str | None = None,
        include_previous: bool = False,
        max_previous: int | None = None,
        now: pd.Timestamp | None = None,
    ) -> MacroDataset:
        fetched_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        topic = self.topic_path(cdid, dataset, path)
        series_id = f"{cdid.upper()}/{dataset.upper()}"
        current = self._get(topic)
        instants = _release_instants(current)
        rows, report = parse_ons_document(current, series_id=series_id, instants=instants)
        previous = [v for v in (current.get("versions") or [])]
        if include_previous:
            chosen = previous if max_previous is None else previous[-max_previous:]
            for v in chosen:
                uri = str(v["uri"])
                if not re.fullmatch(r"/[a-z0-9/]+/previous/v\d+", uri):
                    raise ProviderError(f"unexpected ONS version uri {uri!r}")
                doc = self._get(uri)
                more, _ = parse_ons_document(
                    doc, series_id=series_id, instants=instants, vintage=str(v.get("label"))
                )
                rows.extend(more)
            report["previous_versions_fetched"] = len(chosen)
        frame = macro_frame(rows)
        # The same (period, value, available_at) appears in many archived
        # versions; keep one record of each distinct published value.
        frame = frame.drop_duplicates(
            ["series_id", "period", "value", "available_at"], keep="last"
        ).reset_index(drop=True)
        report["versions_listed"] = len(previous)
        return MacroDataset(
            provider="ons",
            dataset_key=series_id.replace("/", "_"),
            frame=frame,
            descriptor=DESCRIPTOR,
            request={"path": topic, "include_previous": include_previous,
                     "max_previous": max_previous},
            parser_version=PARSER_VERSION,
            fetched_at=fetched_at,
            report=report,
        )
