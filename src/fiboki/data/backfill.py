"""OANDA v20 candle backfill: history into the store as its own source.

What this writes
----------------
For each (instrument, granularity) one **RAW** dataset version with
``source="oanda_practice"`` and ``price_basis = MID``, then one **VALIDATED**
version derived from it by the integrity path the V1 migration uses
(:func:`fiboki.data.integrity.validate` against the instrument's session
calendar; nothing repaired). HistData versions are never touched: the store is
content-addressed, so a version with a different source, basis and lineage has
a different id and sits beside them.

What research reads when both exist -- read this before running it
------------------------------------------------------------------
:meth:`fiboki.data.store.DataStore.read_latest` resolves through
:meth:`fiboki.data.versioning.DatasetCatalogue.latest`, which returns the
newest version of the asked-for kind **by ``created_at``, regardless of source
or price basis**. It has no source or basis filter. After a backfill the OANDA
version is the newest, so research, validation, campaigns and the markets API
read OANDA MID bars for that instrument and timeframe from then on. That is a
change of input: :func:`plan_backfill` puts a warning on every job that will
shadow an existing non-OANDA validated version, and ``--dry-run`` prints it.

Paging, and what is refused
---------------------------
v20 caps a request at 5000 candles. Pages are requested with ``from`` +
``count``; the next page starts ``from`` the last candle of the previous one, so
consecutive pages overlap by one candle, which is deduplicated. A candle seen
twice with different prices is a refusal, not a choice. Timestamps must be
strictly increasing within and across pages (no silent sort). Incomplete
candles are dropped and counted. A page shorter than ``count`` is the end of
history; so is a page whose last candle reaches the window's end.

Requests go through an injected limiter (every attempt, retries included) and
an injected retry runner. This module is in ``data/`` and may not import
``fiboki.broker`` (``tests/unit/test_layering.py``), so the CLI passes
:class:`fiboki.broker.retry.ReadRetry`, whose classifier
(:func:`fiboki.broker.retry.is_retryable`) decides: 429, 5xx and transport
failures are retried with bounded backoff; any other 4xx is a hard refusal that
names the instrument. A job that fails part-way writes **nothing**: a
truncated history registered as the newest version would be read by research
as if it were the whole series.

Resume
------
If an ``oanda_practice`` RAW version exists for the pair, and it was requested
from the same or an earlier start, the job fetches only from its last bar plus
one bar and writes a new version holding the prior bars plus the new ones (so
the newest version is always the whole series; ``extends_version`` in its
lineage names the one it grew from). Nothing new to fetch writes nothing and
says why. Asking for an earlier start than the stored one refetches in full.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.calendars import calendar_for
from fiboki.data.integrity import IntegrityConfig, validate
from fiboki.data.providers.base import AuthenticationRequired, ProviderError
from fiboki.data.providers.oanda import (
    GRANULARITY,
    OANDA_PRACTICE_HOST,
    parse_candles_response,
)
from fiboki.data.schema import (
    ASK_COLUMNS,
    BID_COLUMNS,
    Adjustment,
    DatasetKind,
    PriceBasis,
    canonical_frame,
    describe_frame,
    validate_frame_shape,
)
from fiboki.data.versioning import DatasetVersion, TransformationStep

__all__ = [
    "BACKFILL_CODE_VERSION",
    "BACKFILL_GRANULARITIES",
    "DEFAULT_START",
    "OANDA_BACKFILL_SOURCE",
    "V20_MAX_COUNT",
    "BackfillJob",
    "BackfillOutcome",
    "BackfillRefused",
    "BackfillReport",
    "JobRefused",
    "assert_backfill_params",
    "make_v20_fetch",
    "plan_backfill",
    "run_backfill",
    "select_within_budget",
    "timeframe_for_granularity",
]

#: The ``source`` every version this module writes carries.
OANDA_BACKFILL_SOURCE = "oanda_practice"
#: v20's per-request ceiling for ``count``.
V20_MAX_COUNT = 5000
#: Default first bar requested. H4 history is recorded back to 2005-01-03T00:00Z
#: (tests/fixtures/oanda/candles_EUR_USD_H4_M_from2005.json).
DEFAULT_START = pd.Timestamp("2005-01-01T00:00:00Z")
#: Part of every lineage step this module writes; bump on any change that could
#: alter stored bytes.
BACKFILL_CODE_VERSION = "1.0.0"
CANDLES_ENDPOINT = "GET /v3/instruments/{instrument}/candles"

#: OANDA granularity names accepted by the backfill, and the timeframe each is
#: stored under. ``D`` is v20's name for the daily candle; Fiboki calls it D1.
BACKFILL_GRANULARITIES: dict[str, Timeframe] = {
    "H1": Timeframe.H1,
    "H4": Timeframe.H4,
    "D": Timeframe.D1,
}

#: Query parameters every backfill request must carry. ``price=M``: one basis
#: per stored version, and mid is what the engine executes on. The alignment
#: pair pins H4 and D to UTC midnight, matching the epoch-anchored research
#: frames (see ``data/providers/oanda.py``).
REQUIRED_PARAMS: dict[str, Any] = {
    "price": "M",
    "dailyAlignment": 0,
    "alignmentTimezone": "UTC",
}

#: ``fetch(symbol, timeframe, from_, count) -> v20 candles payload``.
FetchFn = Callable[[str, Timeframe, pd.Timestamp, int], Mapping[str, Any]]


class RetryRunner(Protocol):
    """Anything with ``run(fn)``; in production :class:`fiboki.broker.retry.ReadRetry`."""

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any: ...


class Limiter(Protocol):
    def acquire(self) -> Any: ...


class BackfillRefused(ValueError):
    """The request cannot be planned (unknown symbol or granularity). Nothing is fetched."""


class JobRefused(RuntimeError):
    """One job could not complete. Its message names the instrument and granularity."""


def timeframe_for_granularity(granularity: str) -> Timeframe:
    """``H1`` -> H1, ``H4`` -> H4, ``D`` -> D1. Anything else is refused by name."""
    key = str(granularity).strip().upper()
    if key not in BACKFILL_GRANULARITIES:
        raise BackfillRefused(
            f"granularity {granularity!r} is not backfilled; use OANDA's names "
            f"{sorted(BACKFILL_GRANULARITIES)} (D is the daily candle, stored as D1)"
        )
    tf = BACKFILL_GRANULARITIES[key]
    assert GRANULARITY[tf] == key, "backfill and provider granularity maps disagree"
    return tf


def _bar(tf: Timeframe) -> pd.Timedelta:
    return pd.Timedelta(minutes=tf.minutes)


def _utc(value: pd.Timestamp | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _floor(ts: pd.Timestamp, tf: Timeframe) -> pd.Timestamp:
    # Epoch-anchored; 60, 240 and 1440 minutes all divide a UTC day.
    return ts.floor(f"{tf.minutes}min")


def _ceil(ts: pd.Timestamp, tf: Timeframe) -> pd.Timestamp:
    return ts.ceil(f"{tf.minutes}min")


def _iso(ts: pd.Timestamp | None) -> str | None:
    return None if ts is None else ts.isoformat()


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackfillJob:
    """One (instrument, granularity) window. ``end`` is EXCLUSIVE (a bar open)."""

    instrument: str
    oanda_name: str
    granularity: str
    timeframe: Timeframe
    start: pd.Timestamp
    end: pd.Timestamp
    #: fetch | resume | refetch | current | validate_only
    action: str
    requested_from: pd.Timestamp
    prior_raw_id: str | None = None
    prior_last: pd.Timestamp | None = None
    reason: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def fetches(self) -> bool:
        return self.action in ("fetch", "resume", "refetch")

    @property
    def slots(self) -> int:
        """Aligned bar opens in ``[start, end)``: an upper bound on candles."""
        if not self.fetches:
            return 0
        first = _ceil(self.start, self.timeframe)
        if first >= self.end:
            return 0
        return int((self.end - first) / _bar(self.timeframe))

    def estimated_requests(self, page: int = V20_MAX_COUNT) -> int:
        """Upper bound on requests (retries excluded).

        Every page but the last is full and overlaps its predecessor by one
        candle, so ``n`` pages cover ``(n - 1)(page - 1) + 1`` distinct candles
        before the last; with ``s`` slots that gives
        ``n <= (s - 2) // (page - 1) + 1``. Weekends make the real count lower:
        FX trades about 120 of the week's 168 hours.
        """
        if not self.fetches:
            return 0
        s = self.slots
        if s <= 0:
            return 1  # one request to learn there is nothing yet
        return max(1, (s - 2) // (page - 1) + 1)


def _requested_from_of(version: DatasetVersion) -> pd.Timestamp | None:
    for step in version.lineage:
        if step.operation == "oanda_v20_candles_backfill":
            raw = step.parameters.get("requested_from")
            return _utc(raw) if raw else None
    return None


def _has_validated_child(store: Any, version_id: str) -> bool:
    return any(
        child.kind is DatasetKind.VALIDATED for child in store.catalogue.children(version_id)
    )


def plan_backfill(
    store: Any,
    instruments: Iterable[str],
    granularities: Iterable[str],
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
    *,
    now: pd.Timestamp | None = None,
) -> list[BackfillJob]:
    """Decide each (instrument, granularity)'s window. Reads the catalogue; writes nothing.

    ``start`` defaults to :data:`DEFAULT_START`. ``end`` is exclusive and is
    capped at the current bar's open, so every candle in the window has closed.
    Unknown symbols and granularities are refused up front, all named at once.
    """
    symbols = [str(s).strip().upper() for s in instruments if str(s).strip()]
    grans = [str(g).strip().upper() for g in granularities if str(g).strip()]
    if not symbols:
        raise BackfillRefused("no instruments given")
    if not grans:
        raise BackfillRefused("no granularities given")
    unknown = [s for s in symbols if not instrument_registry.exists(s)]
    unmapped: list[str] = []
    for s in symbols:
        if s in unknown:
            continue
        try:
            instrument_registry.oanda_name_for(s)
        except KeyError:
            unmapped.append(s)
    if unknown or unmapped:
        parts = []
        if unknown:
            parts.append(f"not registered: {', '.join(unknown)}")
        if unmapped:
            parts.append(f"no OANDA instrument: {', '.join(unmapped)}")
        raise BackfillRefused(
            "refusing the whole backfill before any request: " + "; ".join(parts)
            + ". Registered symbols are in core/instruments.py."
        )
    tfs = {g: timeframe_for_granularity(g) for g in grans}

    requested = _utc(start) if start is not None else DEFAULT_START
    clock = _utc(now) if now is not None else pd.Timestamp.now(tz="UTC")

    jobs: list[BackfillJob] = []
    for sym in dict.fromkeys(symbols):
        name = instrument_registry.oanda_name_for(sym)
        for gran in dict.fromkeys(grans):
            tf = tfs[gran]
            bar = _bar(tf)
            cap = _floor(clock, tf)
            window_end = cap if end is None else min(_floor(_utc(end), tf), cap)

            warnings: list[str] = []
            latest = store.catalogue.latest(sym, tf, kind=DatasetKind.VALIDATED)
            if latest is not None and latest.source != OANDA_BACKFILL_SOURCE:
                warnings.append(
                    f"research switches {sym} {tf.value} from {latest.version_id} "
                    f"({latest.source}, {latest.price_basis.value}) to this OANDA MID "
                    "version: read_latest returns the newest validated version "
                    "regardless of source"
                )

            priors = [
                v
                for v in store.catalogue.list_versions(
                    instrument=sym, timeframe=tf, kind=DatasetKind.RAW,
                    source=OANDA_BACKFILL_SOURCE,
                )
                if v.last_timestamp is not None
            ]
            prior = max(priors, key=lambda v: (v.last_timestamp, v.created_at), default=None)
            prior_from = _requested_from_of(prior) if prior is not None else None

            common = {
                "instrument": sym,
                "oanda_name": name,
                "granularity": gran,
                "timeframe": tf,
                "end": window_end,
                "warnings": tuple(warnings),
            }
            if prior is None:
                jobs.append(BackfillJob(start=requested, action="fetch",
                                        requested_from=requested, **common))
                continue
            assert prior.last_timestamp is not None
            if prior_from is None or requested < prior_from:
                jobs.append(
                    BackfillJob(
                        start=requested, action="refetch", requested_from=requested,
                        prior_raw_id=prior.version_id, prior_last=prior.last_timestamp,
                        reason=(
                            f"stored OANDA history was requested from {_iso(prior_from)}; "
                            f"{requested.isoformat()} is earlier, so the series is "
                            "refetched in full"
                        ),
                        **common,
                    )
                )
                continue
            resume_at = prior.last_timestamp + bar
            if resume_at >= window_end:
                validated = _has_validated_child(store, prior.version_id)
                jobs.append(
                    BackfillJob(
                        start=resume_at,
                        action="current" if validated else "validate_only",
                        requested_from=prior_from,
                        prior_raw_id=prior.version_id,
                        prior_last=prior.last_timestamp,
                        reason=(
                            f"last stored bar {prior.last_timestamp.isoformat()} "
                            f"({prior.version_id}); the next bar, "
                            f"{resume_at.isoformat()}, has not closed before "
                            f"{window_end.isoformat()}"
                            + ("" if validated else "; its validated version is "
                               "missing and will be written from the stored raw bars")
                        ),
                        **common,
                    )
                )
                continue
            jobs.append(
                BackfillJob(
                    start=resume_at, action="resume", requested_from=prior_from,
                    prior_raw_id=prior.version_id, prior_last=prior.last_timestamp,
                    reason=f"resuming after {prior.last_timestamp.isoformat()}",
                    **common,
                )
            )
    return jobs


def select_within_budget(
    jobs: Sequence[BackfillJob], max_requests: int | None, *, page: int = V20_MAX_COUNT
) -> tuple[list[BackfillJob], list[BackfillJob]]:
    """Split ``jobs`` into (run now, deferred) by the upper-bound request estimate.

    A job is never started unless its whole estimate fits, so a budget cannot
    cut a job in half. Jobs that fetch nothing always run.
    """
    if max_requests is None:
        return list(jobs), []
    if max_requests < 1:
        raise BackfillRefused("--max-requests must be at least 1")
    run: list[BackfillJob] = []
    deferred: list[BackfillJob] = []
    spent = 0
    for job in jobs:
        cost = job.estimated_requests(page)
        if cost and (deferred or spent + cost > max_requests):
            deferred.append(job)
            continue
        spent += cost
        run.append(job)
    return run, deferred


# ---------------------------------------------------------------------------
# Fetch (network glue with an INJECTED client; the transport is built in cli.py)
# ---------------------------------------------------------------------------


def assert_backfill_params(params: Mapping[str, Any]) -> None:
    """Refuse a request that is not mid-priced, UTC-aligned, from+count."""
    wrong = {k: params.get(k) for k, v in REQUIRED_PARAMS.items() if params.get(k) != v}
    if wrong:
        raise ProviderError(
            f"backfill request parameters {wrong} differ from the required "
            f"{REQUIRED_PARAMS}; stored bars must be mid-priced and UTC-aligned"
        )
    if "to" in params:
        raise ProviderError("backfill pages with from+count; 'to' is not sent")
    if "from" not in params or "count" not in params:
        raise ProviderError("backfill requests carry both 'from' and 'count'")


def make_v20_fetch(provider: Any) -> FetchFn:
    """A :data:`FetchFn` over an :class:`~fiboki.data.providers.oanda.OandaCandlesProvider`.

    The provider must be MID-priced, pointed at the practice host (checked by
    parsed hostname), and carry a token and an HTTP client
    (``workers.feeds.TransportHttpClient`` over the practice-only
    ``HttpxTransport`` in production). A non-2xx response raises through the
    client's ``raise_for_status`` with its status attached, which is what the
    retry classifier reads.
    """
    practice = urlsplit(OANDA_PRACTICE_HOST).hostname
    host = urlsplit(str(provider.host)).hostname
    if urlsplit(str(provider.host)).scheme != "https" or host != practice:
        raise ProviderError(
            f"the backfill reads the OANDA practice host only ({practice}); "
            f"got {provider.host!r}"
        )
    if provider.price_basis is not PriceBasis.MID:
        raise ProviderError("the backfill stores MID bars; construct the provider with MID")
    if not provider.api_token:
        raise AuthenticationRequired(
            "the OANDA backfill needs FIBOKI_OANDA_PRACTICE_TOKEN; nothing was fetched"
        )
    if provider.http_client is None:
        raise ProviderError("no HTTP client configured for the backfill")

    def fetch(
        symbol: str, timeframe: Timeframe, start: pd.Timestamp, count: int
    ) -> Mapping[str, Any]:
        url, params = provider.request_params(symbol, timeframe, start=start, count=count)
        assert_backfill_params(params)
        response = provider.http_client.get(
            url, params=params, headers={"Authorization": f"Bearer {provider.api_token}"}
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, Mapping):
            raise ProviderError(f"v20 candles response for {symbol} is not an object")
        return body

    return fetch


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BackfillOutcome:
    job: BackfillJob
    #: written | skipped | refused | deferred
    status: str
    reason: str = ""
    new_bars: int = 0
    total_rows: int = 0
    pages: int = 0
    raw_version_id: str | None = None
    validated_version_id: str | None = None
    quality: str | None = None
    integrity_summary: str = ""
    counters: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.job.instrument,
            "granularity": self.job.granularity,
            "timeframe": self.job.timeframe.value,
            "action": self.job.action,
            "status": self.status,
            "reason": self.reason,
            "from": _iso(self.job.start),
            "to_exclusive": _iso(self.job.end),
            "new_bars": self.new_bars,
            "total_rows": self.total_rows,
            "pages": self.pages,
            "raw_version_id": self.raw_version_id,
            "validated_version_id": self.validated_version_id,
            "quality": self.quality,
            "integrity": self.integrity_summary,
            "counters": dict(self.counters),
            "warnings": list(self.job.warnings),
        }


@dataclass(slots=True)
class BackfillReport:
    outcomes: list[BackfillOutcome] = field(default_factory=list)
    requests: int = 0

    def of(self, status: str) -> list[BackfillOutcome]:
        return [o for o in self.outcomes if o.status == status]

    @property
    def refused(self) -> list[BackfillOutcome]:
        return self.of("refused")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "written": len(self.of("written")),
            "skipped": len(self.of("skipped")),
            "refused": len(self.refused),
            "deferred": len(self.of("deferred")),
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


def _status(exc: BaseException) -> int | None:
    for candidate in (
        getattr(exc, "status", None),
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


@dataclass(slots=True)
class _Fetched:
    candles: list[dict[str, Any]]
    pages: int
    counters: dict[str, int]


def _candle_time(candle: Mapping[str, Any], label: str) -> pd.Timestamp:
    try:
        return _utc(str(candle["time"]))
    except (KeyError, ValueError) as exc:
        raise JobRefused(f"{label}: candle without a parseable time: {candle!r}"[:300]) from exc


def _fingerprint(candle: Mapping[str, Any]) -> tuple[Any, ...]:
    mid = candle.get("mid") or {}
    return (*(str(mid.get(k)) for k in ("o", "h", "l", "c")), candle.get("volume"))


def _fetch_window(
    job: BackfillJob,
    fetch: FetchFn,
    *,
    page: int,
    limiter: Limiter | None,
    retry: RetryRunner | None,
    report: BackfillReport,
    log: Callable[[str], None],
) -> _Fetched:
    label = f"{job.instrument} {job.granularity}"
    bar = _bar(job.timeframe)
    kept: dict[pd.Timestamp, dict[str, Any]] = {}
    last_kept: pd.Timestamp | None = None
    counters = {
        "candles_received": 0,
        "incomplete_dropped": 0,
        "duplicates_dropped": 0,
        "outside_window": 0,
    }
    cursor = job.start
    pages = 0

    while True:
        def attempt(at: pd.Timestamp = cursor) -> Mapping[str, Any]:
            if limiter is not None:
                limiter.acquire()
            report.requests += 1
            return fetch(job.instrument, job.timeframe, at, page)

        try:
            payload = retry.run(attempt) if retry is not None else attempt()
        except Exception as exc:
            status = _status(exc)
            what = f"HTTP {status}" if status is not None else type(exc).__name__
            raise JobRefused(
                f"{label} ({job.oanda_name}): {what} on the page from "
                f"{cursor.isoformat()}; nothing written for this job. {exc}"[:600]
            ) from exc
        pages += 1

        got_name = payload.get("instrument")
        got_gran = payload.get("granularity")
        if (got_name is not None and got_name != job.oanda_name) or (
            got_gran is not None and got_gran != job.granularity
        ):
            raise JobRefused(
                f"{label}: asked for {job.oanda_name} {job.granularity}, the response "
                f"is {got_name} {got_gran}"
            )
        candles = payload.get("candles")
        if not isinstance(candles, list):
            raise JobRefused(f"{label}: v20 response has no 'candles' list")
        counters["candles_received"] += len(candles)
        if not candles:
            break

        times = [_candle_time(c, label) for c in candles]
        for earlier, later in pairwise(times):
            if later <= earlier:
                raise JobRefused(
                    f"{label}: candle times not strictly increasing within a page "
                    f"({earlier.isoformat()} then {later.isoformat()}); refusing rather "
                    "than sorting"
                )
        for candle, t in zip(candles, times, strict=True):
            if not candle.get("complete", False):
                counters["incomplete_dropped"] += 1
                continue
            if t < job.start or t >= job.end:
                counters["outside_window"] += 1
                continue
            if t in kept:
                if _fingerprint(kept[t]) != _fingerprint(candle):
                    raise JobRefused(
                        f"{label}: candle {t.isoformat()} arrived twice with different "
                        "values; a closed candle must not change"
                    )
                counters["duplicates_dropped"] += 1
                continue
            if last_kept is not None and t <= last_kept:
                raise JobRefused(
                    f"{label}: page goes back in time ({t.isoformat()} after "
                    f"{last_kept.isoformat()})"
                )
            kept[t] = candle
            last_kept = t

        last = times[-1]
        log(f"  {label}: page {pages} to {last.isoformat()} ({len(kept)} bars kept)")
        if len(candles) < page or last + bar >= job.end:
            break
        if last <= cursor:
            raise JobRefused(f"{label}: paging made no progress at {cursor.isoformat()}")
        cursor = last

    return _Fetched(candles=list(kept.values()), pages=pages, counters=counters)


_ADJUSTMENTS: tuple[Adjustment, ...] = (
    Adjustment(
        kind="price_basis_declaration",
        description="OHLC is the v20 mid component (price=M); no bid or ask stored",
        parameters={"price_basis": PriceBasis.MID.value, "price": "M"},
    ),
    Adjustment(
        kind="volume_semantics",
        description=(
            "v20 'volume' is a broker tick count, stored as tick_volume. There is no "
            "'volume' column: traded volume does not exist for this source."
        ),
        parameters={"tick_volume": "v20_volume", "volume": "not_stored"},
    ),
    Adjustment(
        kind="candle_alignment",
        description="dailyAlignment=0, alignmentTimezone=UTC: H4 and D open at UTC midnight",
        parameters={"dailyAlignment": 0, "alignmentTimezone": "UTC"},
    ),
)


def _frame_from_candles(job: BackfillJob, candles: list[dict[str, Any]]) -> pd.DataFrame:
    frame, _ = parse_candles_response(
        {"instrument": job.oanda_name, "granularity": job.granularity, "candles": candles},
        instrument=job.instrument,
        timeframe=job.timeframe,
        price_basis=PriceBasis.MID,
    )
    # One basis per version: a bid or ask side that arrived anyway (a response
    # to price=MBA) is not stored, and neither is the absent-volume column.
    return frame.drop(columns=["volume", *BID_COLUMNS, *ASK_COLUMNS], errors="ignore")


def _validate_and_register(
    store: Any,
    raw_version: DatasetVersion,
    meta: Any,
    frame: pd.DataFrame,
    outcome: BackfillOutcome,
) -> None:
    """The V1-migration integrity path (``data/migrate_v1._migrate_one``):
    validate against the session calendar, repair nothing, register a
    VALIDATED version carrying the verdict as its quality."""
    try:
        calendar = calendar_for(meta.instrument)
    except KeyError:
        calendar = None
    cfg = IntegrityConfig()
    integrity = validate(frame, calendar=calendar, config=cfg)
    canonical_meta = replace(
        meta, quality=integrity.quality, kind=DatasetKind.VALIDATED, gaps=integrity.gaps
    )
    step = TransformationStep(
        operation="validate",
        parameters={
            "calendar": calendar.name if calendar else "none",
            "integrity_config": cfg.to_dict(),
            "repairs_applied": [],
        },
        code_version=BACKFILL_CODE_VERSION,
        inputs=(raw_version.version_id,),
    )
    validated = store.write_canonical(
        frame,
        canonical_meta,
        source_version=raw_version,
        transformation=step,
        integrity=integrity,
        kind=DatasetKind.VALIDATED,
    )
    outcome.validated_version_id = validated.version_id
    outcome.quality = integrity.quality.value
    outcome.integrity_summary = integrity.summary()


def _run_one(
    job: BackfillJob,
    fetch: FetchFn,
    store: Any,
    *,
    page: int,
    limiter: Limiter | None,
    retry: RetryRunner | None,
    report: BackfillReport,
    log: Callable[[str], None],
) -> BackfillOutcome:
    label = f"{job.instrument} {job.granularity}"
    outcome = BackfillOutcome(job=job, status="skipped", reason=job.reason)

    if job.action == "current":
        return outcome
    if job.action == "validate_only":
        assert job.prior_raw_id is not None
        version = store.catalogue.resolve(job.prior_raw_id)
        frame = store.read(job.prior_raw_id, allow_suspect=True)
        meta = store.read_metadata(Path(version.storage_path))
        _validate_and_register(store, version, meta, frame, outcome)
        outcome.status = "written"
        outcome.raw_version_id = version.version_id
        outcome.total_rows = len(frame)
        return outcome

    fetched = _fetch_window(
        job, fetch, page=page, limiter=limiter, retry=retry, report=report, log=log
    )
    outcome.pages = fetched.pages
    outcome.counters = fetched.counters
    if not fetched.candles:
        if job.action == "resume":
            outcome.reason = (
                f"no complete candles after {job.prior_last.isoformat() if job.prior_last else '?'}"
                f" in [{job.start.isoformat()}, {job.end.isoformat()}); nothing written"
            )
            return outcome
        raise JobRefused(
            f"{label}: OANDA returned no complete candles in "
            f"[{job.start.isoformat()}, {job.end.isoformat()}); nothing written. This is "
            "an absence, not an empty dataset."
        )

    new = _frame_from_candles(job, fetched.candles)
    frame = new
    rows_from_prior = 0
    if job.action == "resume":
        assert job.prior_raw_id is not None
        prior = store.read(job.prior_raw_id, allow_suspect=True)
        if prior.index.max() >= new.index.min():
            raise JobRefused(
                f"{label}: new bars start at {new.index.min().isoformat()}, not after the "
                f"stored {prior.index.max().isoformat()}"
            )
        rows_from_prior = len(prior)
        frame = pd.concat([prior, new])
    frame = canonical_frame(
        frame, instrument=job.instrument, timeframe=job.timeframe, price_basis=PriceBasis.MID
    )
    validate_frame_shape(frame)
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise JobRefused(f"{label}: combined index is not strictly increasing")

    host = urlsplit(OANDA_PRACTICE_HOST).hostname
    params = {"granularity": job.granularity, **REQUIRED_PARAMS, "count": page}
    lineage_params = {
        "endpoint": CANDLES_ENDPOINT.format(instrument=job.oanda_name),
        "host": host,
        "instrument": job.oanda_name,
        "params": params,
        "paging": "from=<last candle of the previous page>+count; 1-candle overlap deduplicated",
        "window": {"from": job.start.isoformat(), "to_exclusive": job.end.isoformat()},
        "requested_from": job.requested_from.isoformat(),
        "page_count": fetched.pages,
        **fetched.counters,
        "extends_version": job.prior_raw_id if job.action == "resume" else None,
        "rows_from_prior": rows_from_prior,
    }
    meta = describe_frame(
        frame,
        source=OANDA_BACKFILL_SOURCE,
        source_identifier=(
            f"https://{host}/v3/instruments/{job.oanda_name}/candles?"
            + "&".join(f"{k}={v}" for k, v in params.items())
        ),
        timezone_of_origin="UTC",
        quality=DataQuality.RAW,
        kind=DatasetKind.RAW,
        adjustments=_ADJUSTMENTS,
        extra={"backfill": lineage_params},
    )
    raw_step = TransformationStep(
        operation="oanda_v20_candles_backfill",
        parameters=lineage_params,
        code_version=BACKFILL_CODE_VERSION,
        inputs=(job.prior_raw_id,) if job.action == "resume" and job.prior_raw_id else (),
    )
    raw = store.write_raw(frame, meta, lineage=(raw_step,))
    outcome.raw_version_id = raw.version_id
    outcome.new_bars = len(new)
    outcome.total_rows = len(frame)
    _validate_and_register(store, raw.version, raw.metadata, frame, outcome)
    outcome.status = "written"
    outcome.reason = job.reason or f"{len(new)} bars from {job.start.isoformat()}"
    return outcome


def run_backfill(
    jobs: Sequence[BackfillJob],
    fetch: FetchFn,
    store: Any,
    *,
    page: int = V20_MAX_COUNT,
    limiter: Limiter | None = None,
    retry: RetryRunner | None = None,
    log: Callable[[str], None] = lambda _msg: None,
    deferred: Sequence[BackfillJob] = (),
) -> BackfillReport:
    """Run ``jobs`` in order. A refused job is recorded and the batch continues."""
    if not 2 <= page <= V20_MAX_COUNT:
        raise BackfillRefused(f"page must be in [2, {V20_MAX_COUNT}], got {page}")
    report = BackfillReport()
    for index, job in enumerate(jobs, start=1):
        log(
            f"[{index}/{len(jobs)}] {job.instrument} {job.granularity} {job.action}: "
            f"{job.start.isoformat()} -> {job.end.isoformat()}"
        )
        try:
            outcome = _run_one(
                job, fetch, store, page=page, limiter=limiter, retry=retry,
                report=report, log=log,
            )
        except (JobRefused, ProviderError) as exc:
            outcome = BackfillOutcome(job=job, status="refused", reason=str(exc))
        report.outcomes.append(outcome)
        log(f"  -> {outcome.status}: {outcome.reason or outcome.raw_version_id}")
    for job in deferred:
        report.outcomes.append(
            BackfillOutcome(
                job=job, status="deferred",
                reason=f"over the request budget (estimate {job.estimated_requests(page)})",
            )
        )
    return report


def estimate_total(jobs: Iterable[BackfillJob], page: int = V20_MAX_COUNT) -> int:
    return sum(j.estimated_requests(page) for j in jobs)
