"""The headline recorder: poll every configured source, dedupe, append.

``NewsRecorder.poll_once(now)`` is the whole unit of work. ``now`` is the
recorder's clock reading for the poll and becomes ``observed_at`` for every
new row; it is passed in (never read inside) so a test can pin it and so one
poll has one stamp. A source that fails is recorded as failed in the poll log
and the others carry on; nothing about a failure is silent, and nothing is
retried inside a poll (the next poll is the retry).

Restart safety: inserts for a poll are one transaction and dedupe is by
``(source, url_hash)`` in the database, so a crash mid-poll loses at most that
poll's inserts and the next poll re-inserts them with a later ``observed_at``.
That is the honest direction to be wrong in: a headline is never stamped
earlier than we can prove we held it.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from fiboki.data.news.sources import OFFICIAL_FEEDS, FeedSpec, RssFeedReader
from fiboki.data.news.store import HeadlineStore, NewItem, to_utc_text

__all__ = ["NewsRecorder", "PollResult", "gaps_in_polls", "run_loop"]


@dataclass(slots=True)
class PollResult:
    started_at: datetime
    finished_at: datetime
    per_feed: dict[str, dict[str, Any]] = field(default_factory=dict)

    def total(self, key: str) -> int:
        return sum(int(v.get(key, 0)) for v in self.per_feed.values())

    @property
    def errors(self) -> dict[str, str]:
        return {k: v["error"] for k, v in self.per_feed.items() if v.get("error")}

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "new": self.total("new"),
            "fetched": self.total("fetched"),
            "duplicate": self.total("duplicate"),
            "revised": self.total("revised"),
            "rejected": self.total("rejected"),
            "errors": self.errors,
            "per_feed": self.per_feed,
        }


class NewsRecorder:
    """Owns a store and a list of readers (anything with ``key``, ``source``,
    ``min_interval_s`` and ``fetch() -> (items, rejected)``, optionally a
    ``last_report`` dict merged into that reader's poll-log entry)."""

    def __init__(
        self,
        store: HeadlineStore,
        readers: Iterable[Any],
        *,
        disabled: dict[str, str] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.readers = list(readers)
        keys = [r.key for r in self.readers]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate reader keys: {sorted(keys)}")
        self.disabled = dict(disabled or {})
        self._last_attempt: dict[str, datetime] = {}
        self._wall_clock = wall_clock

    @classmethod
    def official(
        cls,
        store: HeadlineStore,
        http_client: Any,
        *,
        feeds: Iterable[FeedSpec] = OFFICIAL_FEEDS,
        extra_readers: Iterable[Any] = (),
        disabled: dict[str, str] | None = None,
    ) -> NewsRecorder:
        feeds = tuple(feeds)
        for spec in feeds:
            store.register_feed(
                feed_key=spec.key, source=spec.source, url=spec.url, fmt=spec.fmt,
                discovered_from=spec.discovered_from, retrieved_at=spec.retrieved_at,
                notes=spec.notes,
            )
        readers = [RssFeedReader(spec, http_client) for spec in feeds]
        return cls(store, [*readers, *extra_readers], disabled=disabled)

    def poll_once(self, now: datetime) -> PollResult:
        """Fetch every due source, append new headlines stamped ``now``, log the poll."""
        if now.tzinfo is None:
            raise ValueError("poll_once needs an aware UTC 'now'; observed_at is never naive")
        to_utc_text(now)  # validates
        per_feed: dict[str, dict[str, Any]] = {}
        batch: list[tuple[str, list[NewItem]]] = []
        for reader in self.readers:
            last = self._last_attempt.get(reader.key)
            interval = float(getattr(reader, "min_interval_s", 0.0) or 0.0)
            if last is not None and (now - last).total_seconds() < interval:
                per_feed[reader.key] = {"skipped": f"min interval {interval:.0f}s"}
                continue
            self._last_attempt[reader.key] = now
            try:
                items, rejected = reader.fetch()
            except Exception as exc:  # recorded, not raised: one bad feed must not stop the rest
                per_feed[reader.key] = {"error": f"{type(exc).__name__}: {exc}", "fetched": 0}
                continue
            per_feed[reader.key] = {"fetched": len(items), "rejected": rejected}
            # A reader may report more about its last fetch (GDELT: a response
            # truncated at maxrecords). Recorded in the poll log, never hidden.
            extra = getattr(reader, "last_report", None)
            if extra:
                per_feed[reader.key].update(dict(extra))
            batch.append((reader.key, items))
        for key, items in batch:
            per_feed[key].update(self.store.record(items, now))
        for name, why in self.disabled.items():
            per_feed[name] = {"disabled": why}
        finished = self._wall_clock() if self._wall_clock is not None else now
        result = PollResult(started_at=now, finished_at=max(finished, now), per_feed=per_feed)
        self.store.log_poll(result.started_at, result.finished_at, result.to_dict())
        return result


def gaps_in_polls(
    starts: list[datetime], *, threshold: timedelta, until: datetime | None = None
) -> list[dict[str, Any]]:
    """Intervals between consecutive poll starts longer than ``threshold``.

    With ``until``, the span from the last poll to ``until`` counts too, so a
    recorder that stopped an hour ago shows a gap rather than looking healthy.
    """
    points = sorted(starts)
    if until is not None and points:
        points = [*points, until]
    out = []
    for a, b in pairwise(points):
        if b - a > threshold:
            out.append({"from": a.isoformat(), "to": b.isoformat(),
                        "seconds": round((b - a).total_seconds(), 1)})
    return out


def run_loop(
    recorder: NewsRecorder,
    *,
    interval_s: float,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
    max_polls: int | None = None,
    on_poll: Callable[[PollResult], None] | None = None,
) -> int:
    """Poll every ``interval_s`` on a fixed schedule until stopped; return polls made.

    The schedule is anchored to the first poll, so a slow poll does not drift
    the cadence; a poll that overruns its slot starts the next one at once
    rather than piling up.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    polls = 0
    anchor = clock()
    while not should_stop():
        poll_start = clock()
        result = recorder.poll_once(poll_start)
        polls += 1
        if on_poll is not None:
            on_poll(result)
        if max_polls is not None and polls >= max_polls:
            break
        slot = int((poll_start - anchor).total_seconds() // interval_s) + 1
        next_due = anchor + timedelta(seconds=interval_s * slot)
        while not should_stop():
            remaining = (next_due - clock()).total_seconds()
            if remaining <= 0:
                break
            sleep(min(remaining, 5.0))
    return polls
