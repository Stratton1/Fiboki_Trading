"""Point-in-time macro series: the shared contract for the macro providers.

Bar providers (``base.py``) declare price basis, timezone and completeness. A
macro provider has a different question to answer before it may hand over a
single number: **when could we have known it?** A CPI print for August is not
knowable in August; a revised GDP estimate replaces the first estimate at a
specific instant; a COT report "as of Tuesday" is published on Friday. A
backtest that reads a macro value at its reference date instead of its
publication instant is a look-ahead bug that no bar-level test will catch.

So every observation here carries two times, and they are never conflated:

* ``period`` / ``period_start``: what the number is ABOUT (the reference
  period, verbatim from the source, plus a UTC start instant for sorting).
* ``available_at``: the earliest UTC instant at which this exact value was
  public, derived by a declared :class:`AvailabilityBasis`. ``superseded_at``
  (optional) closes the window when the source itself says the value stopped
  being current (ALFRED vintages).

:func:`as_of` is the only sanctioned read. It returns, per ``(series_id,
period)``, the value that was current at ``as_of`` and never a row whose
``available_at`` is later. A row whose availability cannot be established is
stamped ``UNRESOLVED`` with a null ``available_at`` and is **never** returned.

The rule for every stamping approximation in this package: when unsure, stamp
LATER. A late stamp costs a little research realism; an early stamp is a
look-ahead bug. Holiday sets below are therefore deliberately *supersets* of
the true closures, because an extra holiday can only delay a stamp.

Nothing here performs network access by itself. Providers take an injected
``http_client`` with the ``httpx``-shaped ``get(url, params=, headers=)`` that
``oanda.py`` already uses (and that ``workers/feeds.TransportHttpClient``
bridges to a broker ``Transport``), so tests run against recorded fixtures.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from fiboki.data.providers.base import (
    AuthenticationRequired,
    ProviderError,
    RateLimited,
)
from fiboki.data.versioning import (
    TransformationStep,
    VersionConflict,
    compute_version_id,
)

__all__ = [
    "MACRO_COLUMNS",
    "USER_AGENT",
    "AvailabilityBasis",
    "MacroDataset",
    "MacroDatasetStore",
    "MacroProviderDescriptor",
    "as_of",
    "england_bank_holidays_superset",
    "http_get_text",
    "macro_content_checksum",
    "macro_frame",
    "next_business_day",
    "us_bond_market_holidays_superset",
    "us_federal_holidays",
]

NEW_YORK = ZoneInfo("America/New_York")
CHICAGO = ZoneInfo("America/Chicago")
LONDON = ZoneInfo("Europe/London")
FRANKFURT = ZoneInfo("Europe/Berlin")

#: Sent on every macro request. The Bank of England's IADB returns "Access
#: Denied" to the default ``python-httpx`` agent (verified 2026-09-28), and an
#: honest, specific agent is the polite thing to send anyway.
USER_AGENT = "fiboki-data/2 (point-in-time macro research; non-commercial)"


class AvailabilityBasis(str, Enum):
    """How ``available_at`` was established. Ordered roughly by trust."""

    #: The source publishes vintages (ALFRED ``realtime_start``/``realtime_end``).
    VINTAGE = "vintage"
    #: The source stamps its own publication instant (NY Fed repo ``lastUpdated``,
    #: ONS version archive instants).
    SOURCE_TIMESTAMP = "source_timestamp"
    #: A published exception to a schedule (CFTC's post-shutdown backlog table).
    RELEASE_OVERRIDE = "release_override"
    #: A deterministic rule from a documented schedule, stamped conservatively.
    RELEASE_RULE = "release_rule"
    #: Our own clock at fetch time. Valid only FORWARD from the first fetch.
    FIRST_SEEN = "first_seen"
    #: Cannot be established. ``available_at`` is null; never returned by as_of.
    UNRESOLVED = "unresolved"


#: Fixed column order, because the content checksum must not depend on dict
#: iteration or on which provider built the frame.
MACRO_COLUMNS: tuple[str, ...] = (
    "series_id",
    "period",
    "period_start",
    "value",
    "available_at",
    "superseded_at",
    "availability_basis",
    "vintage",
    "attributes",
)


@dataclass(frozen=True, slots=True)
class MacroProviderDescriptor:
    """What a macro provider is. Read this before trusting its output.

    The macro analogue of :class:`~fiboki.data.providers.base.ProviderCapabilities`.
    ``attribution`` is the notice the source's terms require wherever the data
    is redistributed or displayed; it is not optional decoration.
    """

    name: str
    source: str
    licence: str
    licence_url: str
    attribution: str
    point_in_time: str
    availability_bases: tuple[AvailabilityBasis, ...]
    requires_credentials: bool
    credential_env: str | None
    endpoints: tuple[str, ...]
    known_limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "licence": self.licence,
            "licence_url": self.licence_url,
            "attribution": self.attribution,
            "point_in_time": self.point_in_time,
            "availability_bases": [b.value for b in self.availability_bases],
            "requires_credentials": self.requires_credentials,
            "credential_env": self.credential_env,
            "endpoints": list(self.endpoints),
            "known_limitations": list(self.known_limitations),
        }

    def render(self) -> str:
        lines = [
            f"{self.name}: {self.source}",
            f"  licence: {self.licence} <{self.licence_url}>",
            f"  attribution: {self.attribution}",
            f"  point-in-time: {self.point_in_time}",
            "  availability: " + ", ".join(b.value for b in self.availability_bases),
            f"  credentials: {self.credential_env or 'none'}",
        ]
        lines += [f"  limitation: {x}" for x in self.known_limitations]
        return "\n".join(lines)


# --------------------------------------------------------------------- frame


def _utc(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        raise ProviderError(
            f"naive timestamp {value!r} reached the macro frame; every instant must be "
            "made UTC by the provider that knows the source convention"
        )
    return ts.tz_convert("UTC")


def macro_frame(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Build the canonical long-format frame from row dicts.

    Sorting is by ``(series_id, period_start, period, available_at)`` so two
    fetches of identical content produce identical bytes. ``value`` NaN means
    the source published an explicit "no value" (FRED ``.``, a blank IADB
    cell); it is kept as a record and excluded from :func:`as_of` by default.
    """
    records: list[dict[str, Any]] = []
    for r in rows:
        basis = AvailabilityBasis(r["availability_basis"])
        avail = _utc(r.get("available_at"))
        if avail is None and basis is not AvailabilityBasis.UNRESOLVED:
            raise ProviderError(
                f"{r['series_id']} {r['period']}: available_at is missing but the basis "
                f"is {basis.value}; only UNRESOLVED rows may lack an availability instant"
            )
        if avail is not None and basis is AvailabilityBasis.UNRESOLVED:
            raise ProviderError("an UNRESOLVED row must not carry an available_at")
        value = r.get("value")
        records.append(
            {
                "series_id": str(r["series_id"]),
                "period": str(r["period"]),
                "period_start": _utc(r["period_start"]),
                "value": float("nan") if value is None else float(value),
                "available_at": avail,
                "superseded_at": _utc(r.get("superseded_at")),
                "availability_basis": basis.value,
                "vintage": None if r.get("vintage") is None else str(r["vintage"]),
                "attributes": json.dumps(r.get("attributes") or {}, sort_keys=True),
            }
        )
    frame = pd.DataFrame.from_records(records, columns=list(MACRO_COLUMNS))
    for col in ("period_start", "available_at", "superseded_at"):
        frame[col] = pd.to_datetime(frame[col], utc=True)
    frame["value"] = frame["value"].astype("float64")
    frame = frame.sort_values(
        ["series_id", "period_start", "period", "available_at", "vintage"],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
    return frame


def macro_content_checksum(frame: pd.DataFrame) -> str:
    """Deterministic content hash of a macro frame (no wall-clock input)."""
    missing = [c for c in MACRO_COLUMNS if c not in frame.columns]
    if missing:
        raise ProviderError(f"macro frame is missing columns {missing}")
    buf = io.StringIO()
    out = frame[list(MACRO_COLUMNS)].copy()
    for col in ("period_start", "available_at", "superseded_at"):
        out[col] = out[col].map(lambda t: "" if pd.isna(t) else t.isoformat())
    out["value"] = out["value"].map(lambda v: "nan" if pd.isna(v) else repr(float(v)))
    out.to_csv(buf, index=False, lineterminator="\n")
    h = hashlib.blake2b(digest_size=32)
    h.update(b"fiboki-macro-v1\x00")
    h.update(buf.getvalue().encode("utf-8"))
    return h.hexdigest()


def as_of(
    frame: pd.DataFrame,
    when: pd.Timestamp | datetime | str,
    *,
    series_ids: Iterable[str] | None = None,
    include_missing: bool = False,
) -> pd.DataFrame:
    """The values that were current at ``when``, one row per (series, period).

    A row qualifies when ``available_at <= when`` and it had not been
    superseded by then (``superseded_at`` null or ``> when``); among qualifying
    rows for the same period the latest ``available_at`` wins, which is the
    vintage an observer at ``when`` would have been looking at. UNRESOLVED rows
    (null ``available_at``) never qualify.
    """
    at = _utc(when)
    if at is None:
        raise ProviderError("as_of needs an explicit instant; None is not a point in time")
    view = frame
    if series_ids is not None:
        wanted = set(series_ids)
        view = view[view["series_id"].isin(wanted)]
    known = view["available_at"].notna() & (view["available_at"] <= at)
    live = view["superseded_at"].isna() | (view["superseded_at"] > at)
    view = view[known & live]
    if not include_missing:
        view = view[view["value"].notna()]
    view = view.sort_values(
        ["series_id", "period_start", "period", "available_at"], kind="mergesort"
    )
    view = view.drop_duplicates(["series_id", "period"], keep="last")
    return view.reset_index(drop=True)


# ------------------------------------------------------------------ dataset


@dataclass(frozen=True, slots=True)
class MacroDataset:
    """One fetch, described: the frame plus how it was arrived at.

    ``request`` must not contain credentials; it enters the lineage and is
    therefore hashed into the version id and written to disk.
    """

    provider: str
    dataset_key: str
    frame: pd.DataFrame
    descriptor: MacroProviderDescriptor
    request: dict[str, Any]
    parser_version: str
    fetched_at: pd.Timestamp
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def content_checksum(self) -> str:
        return macro_content_checksum(self.frame)

    @property
    def lineage(self) -> tuple[TransformationStep, ...]:
        return (
            TransformationStep(
                operation="macro_fetch",
                parameters={
                    "provider": self.provider,
                    "dataset_key": self.dataset_key,
                    "request": self.request,
                },
                code_version=self.parser_version,
            ),
        )

    @property
    def version_id(self) -> str:
        return compute_version_id(self.content_checksum, self.lineage)

    def as_of(self, when: Any, **kwargs: Any) -> pd.DataFrame:
        return as_of(self.frame, when, **kwargs)


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class MacroDatasetStore:
    """Content-addressed, write-once storage for :class:`MacroDataset`.

    Layout: ``<root>/macro/<provider>/<dataset_key>/<version_id>/{data.parquet,
    _dataset.json}``. The root must already be a marked Fiboki data root
    (``DataStore.initialise``), for the same reason ``data/store.py`` refuses an
    unmarked one: a plausible-looking wrong directory must fail loudly.
    Writing an existing version is a no-op when the checksum matches and a
    :class:`VersionConflict` otherwise; nothing is ever overwritten.
    """

    def __init__(self, root: str | Path) -> None:
        from fiboki.data.store import ROOT_MARKER, DataRootNotFound

        self.root = Path(root).expanduser()
        if not (self.root / ROOT_MARKER).exists():
            raise DataRootNotFound(
                f"{self.root} is not a Fiboki data root (no {ROOT_MARKER}); run "
                "DataStore.initialise on it first"
            )

    def _dir(self, provider: str, dataset_key: str, version_id: str) -> Path:
        return self.root / "macro" / _SAFE.sub("_", provider) / _SAFE.sub("_", dataset_key) / version_id

    def write(self, dataset: MacroDataset) -> tuple[str, Path, bool]:
        """Persist; return ``(version_id, directory, created)``."""
        vid = dataset.version_id
        checksum = dataset.content_checksum
        target = self._dir(dataset.provider, dataset.dataset_key, vid)
        meta_path = target / "_dataset.json"
        if meta_path.exists():
            existing = json.loads(meta_path.read_text())
            if existing.get("content_checksum") != checksum:
                raise VersionConflict(
                    f"{vid} already stored with checksum {existing.get('content_checksum')}"
                    f", not {checksum}. Content-addressed ids must never collide."
                )
            return vid, target, False
        target.mkdir(parents=True, exist_ok=False)
        tmp = target / "data.parquet.tmp"
        dataset.frame.to_parquet(tmp, index=False)
        tmp.replace(target / "data.parquet")
        meta = {
            "version_id": vid,
            "content_checksum": checksum,
            "provider": dataset.provider,
            "dataset_key": dataset.dataset_key,
            "lineage": [s.to_dict() for s in dataset.lineage],
            "descriptor": dataset.descriptor.to_dict(),
            "fetched_at": dataset.fetched_at.isoformat(),
            "report": dataset.report,
            "row_count": len(dataset.frame),
        }
        tmp_meta = target / "_dataset.json.tmp"
        tmp_meta.write_text(json.dumps(meta, indent=2, sort_keys=True, default=str))
        tmp_meta.replace(meta_path)
        return vid, target, True

    def versions(self, provider: str, dataset_key: str) -> list[dict[str, Any]]:
        base = self.root / "macro" / _SAFE.sub("_", provider) / _SAFE.sub("_", dataset_key)
        if not base.exists():
            return []
        out = [json.loads(p.read_text()) for p in sorted(base.glob("*/_dataset.json"))]
        return sorted(out, key=lambda m: m["fetched_at"])

    def load(self, provider: str, dataset_key: str, version_id: str) -> pd.DataFrame:
        target = self._dir(provider, dataset_key, version_id)
        meta_path = target / "_dataset.json"
        if not meta_path.exists():
            from fiboki.data.store import DatasetNotFound

            raise DatasetNotFound(f"no macro dataset {provider}/{dataset_key}/{version_id}")
        frame = pd.read_parquet(target / "data.parquet")
        meta = json.loads(meta_path.read_text())
        if macro_content_checksum(frame) != meta["content_checksum"]:
            from fiboki.data.store import ChecksumMismatch

            raise ChecksumMismatch(f"{version_id}: stored bytes no longer hash to the catalogue")
        return frame


# --------------------------------------------------------------------- HTTP


def _redact(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def http_get_text(
    client: Any,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    secrets: Iterable[str] = (),
    what: str = "request",
) -> str:
    """GET through the injected client; return the body text or raise typed errors.

    Never retries (the data layer cannot import ``broker.retry``; callers that
    want retries wrap this at composition time, as ``workers/feeds.py`` does).
    Every message is scrubbed of ``secrets``, because ``httpx`` puts the full
    URL (and so a query-string API token) into its exception text.
    """
    if client is None:
        raise ProviderError(f"{what}: no HTTP client configured")
    hdrs = {"User-Agent": USER_AGENT, **dict(headers or {})}
    secrets = tuple(s for s in secrets if s)
    try:
        response = client.get(url, params=dict(params or {}), headers=hdrs)
    except Exception as exc:  # transport failure: surface, redacted
        raise ProviderError(_redact(f"{what}: transport failure: {exc}", secrets)) from None
    status = int(getattr(response, "status_code", 0))
    if status in (401, 403):
        raise AuthenticationRequired(_redact(f"{what}: HTTP {status} from {url}", secrets))
    if status == 429:
        raise RateLimited(_redact(f"{what}: HTTP 429 (rate limited) from {url}", secrets))
    if status >= 400 or status == 0:
        raise ProviderError(_redact(f"{what}: HTTP {status} from {url}", secrets))
    text = getattr(response, "text", None)
    if text is None:
        body = getattr(response, "body", None)
        text = json.dumps(body) if body is not None else ""
    return str(text)


# ---------------------------------------------------------------- calendars


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_federal_holidays(year: int) -> frozenset[date]:
    """US federal holidays as OBSERVED (5 U.S.C. 6103), post-1971 rules.

    Saturday holidays are observed on Friday, Sunday ones on Monday; that
    includes New Year's Day falling on a Saturday, observed on 31 December of
    the previous year, which is why the next year's New Year is checked too.
    Juneteenth from 2021. Ad-hoc closures by executive order are NOT included.
    """
    days = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        _observed(date(year, 11, 11)),  # Veterans Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2021:
        days.add(_observed(date(year, 6, 19)))
    nxt = _observed(date(year + 1, 1, 1))
    if nxt.year == year:
        days.add(nxt)
    return frozenset(d for d in days if d.year == year)


#: Unscheduled US market closures (national days of mourning, Hurricane Sandy).
#: Included in the bond-market SUPERSET; if any was only an early close for the
#: Treasury market, the effect is a stamp one business day late, never early.
_US_AD_HOC_CLOSURES: frozenset[date] = frozenset(
    {
        date(2004, 6, 11),
        date(2007, 1, 2),
        date(2012, 10, 30),
        date(2018, 12, 5),
        date(2025, 1, 9),
    }
)


def us_bond_market_holidays_superset(year: int) -> frozenset[date]:
    """A SUPERSET of SIFMA full-closure days: federal holidays + Good Friday + ad hoc.

    SIFMA's actual recommendation differs year to year (Good Friday has been an
    early close in some years). Using a superset means a rate "published on
    the next business day" is stamped on or after its true publication day.
    """
    good_friday = _easter(year) - timedelta(days=2)
    extra = {d for d in _US_AD_HOC_CLOSURES if d.year == year}
    return us_federal_holidays(year) | {good_friday} | extra


#: One-off and moved England and Wales bank holidays. The ORIGINAL date of a
#: moved holiday stays in the set too (the superset rule).
_UK_SPECIAL: frozenset[date] = frozenset(
    {
        date(1999, 12, 31),
        date(2002, 6, 3),
        date(2002, 6, 4),
        date(2011, 4, 29),
        date(2012, 6, 4),
        date(2012, 6, 5),
        date(2020, 5, 8),
        date(2022, 6, 2),
        date(2022, 6, 3),
        date(2022, 9, 19),
        date(2023, 5, 8),
    }
)


def england_bank_holidays_superset(year: int) -> frozenset[date]:
    """A SUPERSET of England and Wales bank holidays (hence of London non-business days).

    Rule-based days plus every known one-off. Weekend substitutes are added
    generously: when 1 January or 25/26 December falls at a weekend, both of
    the following two weekdays candidates are included.
    """
    easter = _easter(year)
    days = {
        date(year, 1, 1),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        _nth_weekday(year, 5, 0, 1),
        _last_weekday(year, 5, 0),
        _last_weekday(year, 8, 0),
        date(year, 12, 25),
        date(year, 12, 26),
    }
    if date(year, 1, 1).weekday() >= 5:
        days |= {date(year, 1, 2), date(year, 1, 3)}
    if date(year, 12, 25).weekday() >= 5 or date(year, 12, 26).weekday() >= 5:
        days |= {date(year, 12, 27), date(year, 12, 28)}
    days |= {d for d in _UK_SPECIAL if d.year == year}
    return frozenset(days)


def is_business_day(d: date, holidays: Callable[[int], frozenset[date]]) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_business_day(d: date, holidays: Callable[[int], frozenset[date]]) -> date:
    """The first business day strictly after ``d``."""
    nxt = d + timedelta(days=1)
    while not is_business_day(nxt, holidays):
        nxt += timedelta(days=1)
    return nxt


def local_instant(d: date, at: time, tz: ZoneInfo) -> pd.Timestamp:
    """``d`` at local wall time ``at`` in ``tz``, as a UTC timestamp (DST-correct)."""
    return pd.Timestamp(datetime.combine(d, at, tzinfo=tz)).tz_convert("UTC")
