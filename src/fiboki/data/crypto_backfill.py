"""Coinbase public candle backfill: spot crypto history into the store.

The OANDA backfill (``data/backfill.py``) is the model; this is its spot-crypto
sibling for the instruments in ``core/instruments_crypto.py``. What differs,
and why:

What this writes
----------------
For each (instrument, NATIVE granularity) -- H1 and D1 are what Coinbase
serves -- one **RAW** dataset version with ``source="coinbase_exchange_public"``
and ``price_basis = last_as_mid`` (trade prints declared a mid proxy, bound
recorded: ``data/providers/coinbase.py``), then one **VALIDATED** version from
the integrity path (:func:`fiboki.data.integrity.validate` against the 24/7
crypto calendar; nothing repaired). When H4 is asked for, it is DERIVED: the
validated H1 series is resampled (epoch-anchored UTC, empty buckets dropped,
a bucket kept only when its four hours have closed) into a **RESAMPLED**
version whose lineage names that H1 version, and validated in turn. Research
reads the newest validated version per (instrument, timeframe), exactly as for
OANDA; no other source exists for these symbols, so nothing is shadowed.

What is refused
---------------
* A symbol that is not a registered Coinbase product (all named at once).
* A page that is not strictly monotonic, a candle seen twice with different
  values, and a non-list body: the JOB is refused and writes nothing, so a
  truncated history is never registered as the newest version.
* A job that finds no closed candle at all: an absence, not an empty dataset.

Empty pages are NOT the end of history: Coinbase publishes no candle for an
interval without trades, and a thin book (BTC-GBP in 2015) has whole empty
days. Paging stops at the window's end. To avoid paging years of empty windows
before a product was listed, a fresh H1 job first finds the product's first
DAILY candle (300 days per request) and starts there.

Resume
------
As the OANDA backfill: if a RAW version from this source exists and was
requested from the same or an earlier start, the job fetches only from its last
bar plus one bar and writes a new version holding the prior bars plus the new
ones. Nothing new writes nothing. An earlier ``--from`` refetches in full.

Terms
-----
Coinbase's Market Data Terms (2026-08-07) license the data for the operator's
own research; no redistribution. The store lives on the operator's Mac and no
bulk bars are committed to the repository.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import DataQuality, Timeframe
from fiboki.data.backfill import (
    BackfillOutcome,
    BackfillRefused,
    BackfillReport,
    JobRefused,
    RetryRunner,
)
from fiboki.data.calendars import calendar_for
from fiboki.data.integrity import IntegrityConfig, validate
from fiboki.data.providers.base import ProviderError
from fiboki.data.providers.coinbase import (
    COINBASE_EXCHANGE_HOST,
    COINBASE_MAX_CANDLES,
    COINBASE_SOURCE,
    NATIVE_GRANULARITY,
    PRICE_ADJUSTMENTS,
    frame_from_candles,
    h1_to_h4,
    to_coinbase_product,
)
from fiboki.data.resample import ResampleSpec
from fiboki.data.schema import (
    DatasetKind,
    PriceBasis,
    canonical_frame,
    describe_frame,
    validate_frame_shape,
)
from fiboki.data.versioning import DatasetVersion, TransformationStep

__all__ = [
    "CRYPTO_BACKFILL_CODE_VERSION",
    "CRYPTO_DEFAULT_START",
    "CryptoBackfillJob",
    "plan_crypto_backfill",
    "run_crypto_backfill",
    "select_within_budget",
]

#: Part of every lineage step this module writes; bump on any change that could
#: alter stored bytes.
CRYPTO_BACKFILL_CODE_VERSION = "1.0.0"
#: Before the first GBP product (BTC-GBP, 2015-04-21). The listing probe finds
#: each product's real first bar.
CRYPTO_DEFAULT_START = pd.Timestamp("2015-01-01T00:00:00Z")
CANDLES_ENDPOINT = "GET /products/{product}/candles"
RAW_OPERATION = "coinbase_candles_backfill"

#: Accepted granularity names -> timeframe. ``D`` is accepted as the OANDA
#: backfill's spelling of the daily candle.
_GRANULARITIES: dict[str, Timeframe] = {
    "H1": Timeframe.H1, "H4": Timeframe.H4, "D1": Timeframe.D1, "D": Timeframe.D1,
}


def _bar(tf: Timeframe) -> pd.Timedelta:
    return pd.Timedelta(minutes=tf.minutes)


def _utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@dataclass(frozen=True, slots=True)
class CryptoBackfillJob:
    """One (instrument, native granularity) window. ``end`` is EXCLUSIVE.

    Attribute names match :class:`fiboki.data.backfill.BackfillJob` so the
    shared :class:`BackfillOutcome` and report render either.
    """

    instrument: str
    product_id: str
    granularity: str
    timeframe: Timeframe
    start: pd.Timestamp
    end: pd.Timestamp
    #: fetch | resume | refetch | current
    action: str
    requested_from: pd.Timestamp
    derive_h4: bool = False
    prior_raw_id: str | None = None
    prior_last: pd.Timestamp | None = None
    reason: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def oanda_name(self) -> str:  # the shared outcome renderer reads this name
        return self.product_id

    @property
    def fetches(self) -> bool:
        return self.action in ("fetch", "resume", "refetch")

    def estimated_requests(self, page: int = COINBASE_MAX_CANDLES) -> int:
        """Upper bound on requests (retries and the listing probe excluded)."""
        if not self.fetches:
            return 1 if self.derive_h4 else 0
        first = self.start.ceil(f"{self.timeframe.minutes}min")
        slots = max(0, int((self.end - first) / _bar(self.timeframe)))
        return max(1, -(-slots // page))


def _requested_from_of(version: DatasetVersion) -> pd.Timestamp | None:
    for step in version.lineage:
        if step.operation == RAW_OPERATION:
            raw = step.parameters.get("requested_from")
            return _utc(raw) if raw else None
    return None


def plan_crypto_backfill(
    store: Any,
    instruments: Iterable[str],
    granularities: Iterable[str],
    start: pd.Timestamp | str | None = None,
    *,
    now: pd.Timestamp | None = None,
) -> list[CryptoBackfillJob]:
    """Decide each job's window. Reads the catalogue; writes nothing.

    H4 is never fetched: asking for it plans (or reuses) the H1 job with
    ``derive_h4`` set. ``end`` is the current bar's open, so every candle in the
    window has closed.
    """
    symbols = [str(s).strip().upper() for s in instruments if str(s).strip()]
    grans = [str(g).strip().upper() for g in granularities if str(g).strip()]
    if not symbols:
        raise BackfillRefused("no instruments given")
    if not grans:
        raise BackfillRefused("no granularities given")
    unknown = [g for g in grans if g not in _GRANULARITIES]
    if unknown:
        raise BackfillRefused(f"granularities {unknown} are not backfilled; use H1, H4, D1")
    not_crypto = [s for s in symbols if s not in instrument_registry.CRYPTO_REGISTERED]
    if not_crypto:
        raise BackfillRefused(
            "refusing the whole backfill before any request: not a registered Coinbase "
            f"product: {', '.join(not_crypto)}. The crypto table is core/instruments_crypto.py."
        )
    wanted = {_GRANULARITIES[g] for g in grans}
    natives: list[tuple[Timeframe, bool]] = []
    if Timeframe.H1 in wanted or Timeframe.H4 in wanted:
        natives.append((Timeframe.H1, Timeframe.H4 in wanted))
    if Timeframe.D1 in wanted:
        natives.append((Timeframe.D1, False))

    requested = _utc(start) if start is not None else CRYPTO_DEFAULT_START
    clock = _utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    jobs: list[CryptoBackfillJob] = []
    for sym in dict.fromkeys(symbols):
        product = to_coinbase_product(sym)
        for tf, derive in natives:
            window_end = clock.floor(f"{tf.minutes}min")
            priors = [
                v
                for v in store.catalogue.list_versions(
                    instrument=sym, timeframe=tf, kind=DatasetKind.RAW, source=COINBASE_SOURCE
                )
                if v.last_timestamp is not None
            ]
            prior = max(priors, key=lambda v: (v.last_timestamp, v.created_at), default=None)
            prior_from = _requested_from_of(prior) if prior is not None else None
            common = {
                "instrument": sym, "product_id": product, "granularity": tf.value,
                "timeframe": tf, "end": window_end, "derive_h4": derive,
            }
            if prior is None:
                jobs.append(CryptoBackfillJob(start=requested, action="fetch",
                                              requested_from=requested, **common))
                continue
            assert prior.last_timestamp is not None
            if prior_from is None or requested < prior_from:
                jobs.append(CryptoBackfillJob(
                    start=requested, action="refetch", requested_from=requested,
                    prior_raw_id=prior.version_id, prior_last=prior.last_timestamp,
                    reason=f"stored history was requested from {prior_from}; "
                           f"{requested.isoformat()} is earlier, so it is refetched in full",
                    **common,
                ))
                continue
            resume_at = prior.last_timestamp + _bar(tf)
            if resume_at >= window_end:
                jobs.append(CryptoBackfillJob(
                    start=resume_at, action="current", requested_from=prior_from,
                    prior_raw_id=prior.version_id, prior_last=prior.last_timestamp,
                    reason=f"last stored bar {prior.last_timestamp.isoformat()}; the next has "
                           f"not closed before {window_end.isoformat()}",
                    **common,
                ))
                continue
            jobs.append(CryptoBackfillJob(
                start=resume_at, action="resume", requested_from=prior_from,
                prior_raw_id=prior.version_id, prior_last=prior.last_timestamp,
                reason=f"resuming after {prior.last_timestamp.isoformat()}", **common,
            ))
    return jobs


def select_within_budget(
    jobs: Sequence[CryptoBackfillJob], max_requests: int | None
) -> tuple[list[CryptoBackfillJob], list[CryptoBackfillJob]]:
    """(run now, deferred) by the upper-bound estimate; a job is never cut in half."""
    if max_requests is None:
        return list(jobs), []
    if max_requests < 1:
        raise BackfillRefused("--max-requests must be at least 1")
    run: list[CryptoBackfillJob] = []
    deferred: list[CryptoBackfillJob] = []
    spent = 0
    for job in jobs:
        cost = job.estimated_requests()
        if cost and (deferred or spent + cost > max_requests):
            deferred.append(job)
            continue
        spent += cost
        run.append(job)
    return run, deferred


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _first_listed_day(
    provider: Any, job: CryptoBackfillJob, runner: Callable[..., Any] | None
) -> pd.Timestamp | None:
    """The first daily candle at or after ``job.start``, by 300-day pages."""
    day = _bar(Timeframe.D1)
    cursor = job.start.floor("1D")
    while cursor < job.end:
        page_end = min(cursor + day * (COINBASE_MAX_CANDLES - 1), job.end - day)
        if page_end < cursor:
            return None
        rows = (
            runner(provider.fetch_page, job.instrument, Timeframe.D1, cursor, page_end)
            if runner is not None
            else provider.fetch_page(job.instrument, Timeframe.D1, cursor, page_end)
        )
        if rows:
            return pd.Timestamp(min(r[0] for r in rows), unit="s", tz="UTC")
        cursor = page_end + day
    return None


def _validate_and_register(
    store: Any, parent: DatasetVersion, meta: Any, frame: pd.DataFrame,
) -> tuple[Any, Any]:
    calendar = calendar_for(meta.instrument)
    cfg = IntegrityConfig()
    integrity = validate(frame, calendar=calendar, config=cfg)
    validated_meta = replace(
        meta, quality=integrity.quality, kind=DatasetKind.VALIDATED, gaps=integrity.gaps
    )
    step = TransformationStep(
        operation="validate",
        parameters={"calendar": calendar.name, "integrity_config": cfg.to_dict(),
                    "repairs_applied": []},
        code_version=CRYPTO_BACKFILL_CODE_VERSION,
        inputs=(parent.version_id,),
    )
    stored = store.write_canonical(
        frame, validated_meta, source_version=parent, transformation=step,
        integrity=integrity, kind=DatasetKind.VALIDATED,
    )
    return stored, integrity


def _derive_h4(
    store: Any, h1_validated: Any, h1_frame: pd.DataFrame, h1_end: pd.Timestamp,
    outcome: BackfillOutcome,
) -> None:
    """H4 from the whole validated H1 series; a RESAMPLED then a VALIDATED version."""
    h4, dropped = h1_to_h4(h1_frame, now=h1_end)
    if h4.empty:
        raise JobRefused(f"{outcome.job.instrument}: no complete H4 bucket in the H1 series")
    spec = ResampleSpec(source=Timeframe.H1, target=Timeframe.H4)
    step = replace(
        spec.to_step(),
        parameters={**spec.to_parameters(), "complete_buckets_only": True,
                    "incomplete_dropped": dropped, "complete_through": h1_end.isoformat()},
        inputs=(h1_validated.version_id,),
    )
    meta = describe_frame(
        h4, source=COINBASE_SOURCE,
        source_identifier=f"resample:{h1_validated.version_id}",
        timezone_of_origin="UTC", quality=DataQuality.RAW, kind=DatasetKind.RESAMPLED,
        adjustments=PRICE_ADJUSTMENTS,
        extra={"derived_from": h1_validated.version_id},
    )
    resampled = store.write_canonical(
        h4, meta, source_version=h1_validated.version, transformation=step,
        kind=DatasetKind.RESAMPLED,
    )
    validated, integrity = _validate_and_register(store, resampled.version, resampled.metadata, h4)
    outcome.counters["h4_rows"] = len(h4)
    outcome.counters["h4_incomplete_dropped"] = dropped
    outcome.reason = (outcome.reason + "; " if outcome.reason else "") + (
        f"H4 derived: {len(h4)} bars, {validated.version_id} ({integrity.quality.value})"
    )


def _run_one(
    job: CryptoBackfillJob, provider: Any, store: Any, *,
    runner: Callable[..., Any] | None, report: BackfillReport, log: Callable[[str], None],
) -> BackfillOutcome:
    label = f"{job.instrument} {job.granularity}"
    outcome = BackfillOutcome(job=job, status="skipped", reason=job.reason)  # type: ignore[arg-type]
    before = provider.requests

    def _count() -> None:
        report.requests += provider.requests - before

    if job.action == "current":
        if job.derive_h4 and job.prior_raw_id:
            # The H1 series is current; derive H4 only if no validated H4 yet
            # descends from its validated version (e.g. H4 asked for later).
            children = [
                c for c in store.catalogue.children(job.prior_raw_id)
                if c.kind is DatasetKind.VALIDATED
            ]
            if children:
                h1v = max(children, key=lambda c: c.created_at)
                latest_h4 = store.catalogue.latest(job.instrument, Timeframe.H4,
                                                   kind=DatasetKind.VALIDATED)
                if latest_h4 is None or h1v.version_id not in _ancestors(store, latest_h4):
                    frame = store.read(h1v.version_id, allow_suspect=True)
                    _derive_h4(store, _Stored(h1v), frame, job.end, outcome)
                    outcome.status = "written"
        return outcome

    start = job.start
    if job.action in ("fetch", "refetch") and job.timeframe is Timeframe.H1:
        listed = _first_listed_day(provider, job, runner)
        if listed is None:
            _count()
            raise JobRefused(f"{label}: no daily candle at all in [{job.start}, {job.end})")
        if listed > start:
            start = listed
            outcome.counters["listed_from"] = int(listed.timestamp())
    pages = {"n": 0, "empty": 0}

    def _on_page(page_end: pd.Timestamp, n: int) -> None:
        pages["n"] += 1
        pages["empty"] += int(n == 0)
        if pages["n"] % 25 == 0:
            log(f"  {label}: {pages['n']} pages, to {page_end.isoformat()}")

    try:
        rows = provider.fetch_native(job.instrument, job.timeframe, start=start, end=job.end,
                                     runner=runner, on_page=_on_page)
    except ProviderError as exc:
        _count()
        raise JobRefused(f"{label}: {exc}; nothing written for this job") from exc
    except Exception as exc:
        _count()
        status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        raise JobRefused(
            f"{label} ({job.product_id}): {'HTTP ' + str(status) if status else type(exc).__name__}"
            f"; nothing written for this job. {exc}"[:600]
        ) from exc
    _count()
    outcome.pages = pages["n"]
    outcome.counters.update(empty_pages=pages["empty"], candles_received=len(rows))
    if not rows:
        if job.action == "resume":
            outcome.reason = f"no closed candles after {job.prior_last}; nothing written"
            return outcome
        raise JobRefused(f"{label}: Coinbase returned no candles in [{start}, {job.end}); "
                         "this is an absence, not an empty dataset")
    new, parse = frame_from_candles(rows, instrument=job.instrument, timeframe=job.timeframe,
                                    now=job.end)
    outcome.counters["incomplete_dropped"] = parse["incomplete_dropped"]
    frame = new
    rows_from_prior = 0
    if job.action == "resume":
        assert job.prior_raw_id is not None
        prior = store.read(job.prior_raw_id, allow_suspect=True)
        if prior.index.max() >= new.index.min():
            raise JobRefused(f"{label}: new bars start at {new.index.min()}, not after the "
                             f"stored {prior.index.max()}")
        rows_from_prior = len(prior)
        frame = pd.concat([prior, new])
    frame = canonical_frame(frame, instrument=job.instrument, timeframe=job.timeframe,
                            price_basis=PriceBasis.LAST_AS_MID)
    validate_frame_shape(frame)
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise JobRefused(f"{label}: combined index is not strictly increasing")

    params = {"granularity": NATIVE_GRANULARITY[job.timeframe], "page": COINBASE_MAX_CANDLES}
    lineage_params = {
        "endpoint": CANDLES_ENDPOINT.format(product=job.product_id),
        "host": COINBASE_EXCHANGE_HOST,
        "product_id": job.product_id,
        "params": params,
        "paging": "start/end windows of 300 candles, both inclusive; empty pages continue",
        "window": {"from": start.isoformat(), "to_exclusive": job.end.isoformat()},
        "requested_from": job.requested_from.isoformat(),
        "page_count": pages["n"],
        "empty_pages": pages["empty"],
        "candles_received": len(rows),
        "incomplete_dropped": parse["incomplete_dropped"],
        "extends_version": job.prior_raw_id if job.action == "resume" else None,
        "rows_from_prior": rows_from_prior,
    }
    meta = describe_frame(
        frame, source=COINBASE_SOURCE,
        source_identifier=(f"https://{COINBASE_EXCHANGE_HOST}/products/{job.product_id}/candles"
                           f"?granularity={params['granularity']}"),
        timezone_of_origin="UTC", quality=DataQuality.RAW, kind=DatasetKind.RAW,
        adjustments=PRICE_ADJUSTMENTS, extra={"backfill": lineage_params},
    )
    raw_step = TransformationStep(
        operation=RAW_OPERATION, parameters=lineage_params,
        code_version=CRYPTO_BACKFILL_CODE_VERSION,
        inputs=(job.prior_raw_id,) if job.action == "resume" and job.prior_raw_id else (),
    )
    raw = store.write_raw(frame, meta, lineage=(raw_step,))
    validated, integrity = _validate_and_register(store, raw.version, raw.metadata, frame)
    outcome.raw_version_id = raw.version_id
    outcome.validated_version_id = validated.version_id
    outcome.quality = integrity.quality.value
    outcome.integrity_summary = integrity.summary()
    outcome.new_bars = len(new)
    outcome.total_rows = len(frame)
    outcome.status = "written"
    outcome.reason = job.reason or f"{len(new)} bars from {start.isoformat()}"
    if job.derive_h4:
        _derive_h4(store, validated, frame, job.end, outcome)
    return outcome


@dataclass(frozen=True, slots=True)
class _Stored:
    version: DatasetVersion

    @property
    def version_id(self) -> str:
        return self.version.version_id


def _ancestors(store: Any, version: DatasetVersion) -> set[str]:
    seen: set[str] = set()
    stack = list(version.parent_ids)
    while stack:
        vid = stack.pop()
        if vid in seen:
            continue
        seen.add(vid)
        try:
            stack.extend(store.catalogue.resolve(vid).parent_ids)
        except Exception:
            continue
    return seen


def run_crypto_backfill(
    jobs: Sequence[CryptoBackfillJob],
    provider: Any,
    store: Any,
    *,
    retry: RetryRunner | None = None,
    log: Callable[[str], None] = lambda _m: None,
    deferred: Sequence[CryptoBackfillJob] = (),
) -> BackfillReport:
    """Run ``jobs`` in order. A refused job is recorded and the batch continues.

    ``provider`` is a :class:`~fiboki.data.providers.coinbase.CoinbaseCandlesProvider`
    with its limiter (every request, retries included, passes through it).
    """
    runner = retry.run if retry is not None else None
    report = BackfillReport()
    for index, job in enumerate(jobs, start=1):
        log(f"[{index}/{len(jobs)}] {job.instrument} {job.granularity} {job.action}"
            f"{' (+H4)' if job.derive_h4 else ''}: {job.start.isoformat()} -> {job.end.isoformat()}")
        try:
            outcome = _run_one(job, provider, store, runner=runner, report=report, log=log)
        except (JobRefused, ProviderError) as exc:
            outcome = BackfillOutcome(job=job, status="refused", reason=str(exc))  # type: ignore[arg-type]
        report.outcomes.append(outcome)
        log(f"  -> {outcome.status}: {outcome.reason or outcome.raw_version_id}")
    for job in deferred:
        report.outcomes.append(BackfillOutcome(
            job=job,  # type: ignore[arg-type]
            status="deferred",
            reason=f"over the request budget (estimate {job.estimated_requests()})",
        ))
    return report


def describe_plan(jobs: Iterable[CryptoBackfillJob], deferred: Sequence[CryptoBackfillJob]) -> list[dict[str, Any]]:
    return [
        {
            "instrument": j.instrument, "product_id": j.product_id, "granularity": j.granularity,
            "derive_h4": j.derive_h4, "action": j.action, "from": j.start.isoformat(),
            "to_exclusive": j.end.isoformat(), "estimated_requests": j.estimated_requests(),
            "deferred": j in deferred, "reason": j.reason,
        }
        for j in jobs
    ]

