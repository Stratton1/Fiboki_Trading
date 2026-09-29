"""Point-in-time headline recording (no LLM anywhere in this package).

    store      append-only SQLite headline store; ``HeadlineStore.query`` is the
               point-in-time read (``observed_at <= as_of``, nothing else)
    sources    official central-bank RSS/Atom readers, optional Finnhub and
               Marketaux clients
    recorder   ``NewsRecorder.poll_once(now)`` and the fixed-cadence loop

News is recorded first and classified later (docs/v2/AGENTIC_INTEGRATION_PLAN.md
D-A5): a headline's value for research is its first-seen instant on our own
clock, which cannot be reconstructed afterwards from vendor timestamps.
"""
from __future__ import annotations

from fiboki.data.news.recorder import NewsRecorder, PollResult, gaps_in_polls, run_loop
from fiboki.data.news.sources import OFFICIAL_FEEDS, FeedSpec, parse_feed
from fiboki.data.news.store import (
    Headline,
    HeadlineStore,
    NewItem,
    NewsSource,
    NewsStoreError,
    default_store_path,
)

__all__ = [
    "OFFICIAL_FEEDS",
    "FeedSpec",
    "Headline",
    "HeadlineStore",
    "NewItem",
    "NewsRecorder",
    "NewsSource",
    "NewsStoreError",
    "PollResult",
    "default_store_path",
    "gaps_in_polls",
    "parse_feed",
    "run_loop",
]
