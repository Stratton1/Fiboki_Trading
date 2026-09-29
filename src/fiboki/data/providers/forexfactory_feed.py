"""ForexFactory weekly calendar feed: OPT-IN, off by default, comparison only.

``terms_status: opt_in_unclear``. Read this before turning it on.

The feed is ``https://nfs.faireconomy.media/ff_calendar_thisweek.json`` (an
``.xml`` twin exists). It is published by Fair Economy, Inc. ("FEI"), which
runs ForexFactory. ForexFactory's notices page
(https://www.forexfactory.com/notices, read 2026-09-29) says, verbatim:

    "The copying, republication or redistribution of FEED, in part or in
    whole, is explicitly prohibited."

where FEED is the Fair Economy Economic Database (calendar data, event
information and compiled economic data), and:

    "The copying, republication, or redistribution of FEI's copyrighted
    content - including but not limited to, forum posts, calendar schedules
    and specs, news mix, news comments, trader lists, and account data - is
    explicitly prohibited without prior written consent."

and "Users are not permitted to interfere with Services or try to access them
using a method other than the interface and the instructions that FEI
provides." No licence for the ``nfs.faireconomy.media`` export itself was
found: it is unstated. Storing a copy is arguably "copying". Hence:

* Off unless ``FIBOKI_FF_CALENDAR_OPT_IN=true`` (strict boolean). Turning it
  on is the operator's decision about his own personal research use; it is
  never on in CI, and no fetched feed content is committed to this
  repository (the test fixture is CONSTRUCTED in the feed's shape).
* Output is a dated-events file tagged ``source: "forexfactory_feed"`` under
  the operator's data root. It never replaces, merges into or writes over the
  official calendar; its value is :func:`~fiboki.data.providers.calendar_feed.calendar_diff`
  catching omissions in our official fixture, each of which must then be
  confirmed at the PUBLISHER before the official file changes.
* Polite: at most one fetch per ``min_interval_s`` (default 3600 s) per
  process; the feed only changes when the week's schedule does.

Parsing rules. JSON ``date`` values carry a UTC offset
(``2026-09-29T08:30:00-04:00``) and are converted to UTC. The XML twin gives
a date and a wall-clock time with no zone, so it is NOT parsed: a zone-less
time is not guessed. ``impact`` "High"/"Medium"/"Low" map to high/medium/low;
"Holiday" and "Non-Economic" rows are counted and skipped (a bank holiday is
not a release). A local wall time of exactly 00:00 is how the feed is widely
reported to encode "All Day"/"Tentative" rows; such rows are written with
``time_known: false`` and a 24-hour ``window_end`` (the whole day), tagged
``ff_midnight_unverified``, which can only widen a blackout, never narrow it.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.data.providers.base import ProviderError, RateLimited
from fiboki.data.providers.calendar_feed import dated_event, snapshot_path, write_events_file
from fiboki.data.providers.macro_base import http_get_text

__all__ = [
    "ENV_FF_OPT_IN",
    "FF_FEED_SOURCE",
    "FF_JSON_URL",
    "FF_TERMS_URL",
    "ForexFactoryFeed",
    "ForexFactorySnapshot",
    "SourceNotOptedIn",
    "parse_ff_json",
]

ENV_FF_OPT_IN = "FIBOKI_FF_CALENDAR_OPT_IN"
FF_JSON_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_XML_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
FF_TERMS_URL = "https://www.forexfactory.com/notices"
FF_FEED_SOURCE = "forexfactory_feed"
FF_TERMS_STATUS = "opt_in_unclear"

_IMPACT = {"high": "high", "medium": "medium", "low": "low"}
_SKIP_IMPACT = {"holiday", "non-economic"}


class SourceNotOptedIn(ProviderError):
    """The operator has not opted in to a source whose terms are unclear."""


def _opted_in(environ: Mapping[str, str]) -> bool:
    raw = (environ.get(ENV_FF_OPT_IN) or "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    raise ValueError(f"{ENV_FF_OPT_IN}={raw!r} is not a boolean; use true/false")


def parse_ff_json(text: str, *, retrieved_at: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The weekly JSON feed to dated-event dicts; return ``(events, counts)``."""
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ProviderError("ForexFactory feed: expected a JSON array")
    counts = {"received": len(payload), "kept": 0, "skipped_holiday_or_non_economic": 0,
              "bad_impact": 0, "bad_time": 0, "midnight_unverified": 0}
    events: list[dict[str, Any]] = []
    for r in payload:
        impact_raw = str(r.get("impact") or "").strip().lower()
        if impact_raw in _SKIP_IMPACT:
            counts["skipped_holiday_or_non_economic"] += 1
            continue
        impact = _IMPACT.get(impact_raw)
        if impact is None:
            counts["bad_impact"] += 1
            continue
        try:
            local = pd.Timestamp(str(r.get("date") or ""))
        except ValueError:
            counts["bad_time"] += 1
            continue
        if pd.isna(local) or local.tzinfo is None:
            counts["bad_time"] += 1  # no offset: not guessed
            continue
        title = " ".join(str(r.get("title") or "").split())
        ccy = str(r.get("country") or "").strip().upper()
        if not title or len(ccy) != 3:
            counts["bad_time"] += 1
            continue
        utc = local.tz_convert("UTC")
        midnight = (local.hour, local.minute, local.second) == (0, 0, 0)
        tags = ["forexfactory_feed"]
        if midnight:
            counts["midnight_unverified"] += 1
            tags.append("ff_midnight_unverified")
        events.append(dated_event(
            event_time=utc, currency=ccy, name=title, impact=impact, source=FF_FEED_SOURCE,
            source_url=FF_JSON_URL, retrieved_at=retrieved_at, time_known=not midnight,
            window_end=(utc + pd.Timedelta(days=1)) if midnight else None, tags=tags,
        ))
        counts["kept"] += 1
    return events, counts


@dataclass(frozen=True, slots=True)
class ForexFactorySnapshot:
    events: tuple[dict[str, Any], ...]
    retrieved_at: pd.Timestamp
    report: dict[str, Any] = field(default_factory=dict)

    def write(self, root: str | Path) -> Path:
        path = snapshot_path(root, FF_FEED_SOURCE, self.retrieved_at)
        return write_events_file(
            path, self.events, source=FF_FEED_SOURCE, retrieved_at=self.retrieved_at.isoformat(),
            manifest={"report": self.report, "terms_url": FF_TERMS_URL,
                      "terms_status": FF_TERMS_STATUS,
                      "notice": "Copying, republication or redistribution of FEED is prohibited "
                                "(forexfactory.com/notices). Personal comparison copy only; do not "
                                "commit, share or publish.",
                      "role": "diff against the official calendar; never replaces it"},
        )


class ForexFactoryFeed:
    """Opt-in fetcher for the weekly JSON feed. Construct with :meth:`from_env`."""

    def __init__(self, http_client: Any, *, opted_in: bool, min_interval_s: float = 3600.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not opted_in:
            raise SourceNotOptedIn(
                f"ForexFactory feed is off: set {ENV_FF_OPT_IN}=true to opt in. Its terms prohibit "
                f"copying the feed and state no licence for the export ({FF_TERMS_URL})."
            )
        self.http_client = http_client
        self.min_interval_s = float(min_interval_s)
        self._clock = clock
        self._last: float | None = None

    @classmethod
    def from_env(cls, http_client: Any, *, env: Mapping[str, str] | None = None,
                 **kwargs: Any) -> ForexFactoryFeed:
        environ = os.environ if env is None else env
        return cls(http_client, opted_in=_opted_in(environ), **kwargs)

    def fetch(self, *, now: pd.Timestamp | None = None) -> ForexFactorySnapshot:
        t = self._clock()
        if self._last is not None and t - self._last < self.min_interval_s:
            raise RateLimited(
                f"ForexFactory feed fetched {t - self._last:.0f}s ago; minimum interval "
                f"{self.min_interval_s:.0f}s"
            )
        self._last = t
        retrieved = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        text = http_get_text(self.http_client, FF_JSON_URL, what="ForexFactory weekly feed")
        events, counts = parse_ff_json(text, retrieved_at=retrieved.isoformat())
        return ForexFactorySnapshot(events=tuple(events), retrieved_at=retrieved, report=counts)
