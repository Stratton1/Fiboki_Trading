"""Builders for the event-channel tests (agentic plan Wave 4).

Constructors, not fixtures, like ``tests/exec_fixtures.py``.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.data.news.store import HeadlineStore, NewItem, NewsSource
from fiboki.marketstate.events import AnnotationDraft, AnnotationStore

#: The decision instant the unit tests reason about (matches tests/exec_fixtures.NOW).
AT = pd.Timestamp("2024-06-03 12:00", tz="UTC")
DIGEST = "sha256:" + "ab" * 32
MANIFEST = "cd" * 32


def draft(**overrides: Any) -> AnnotationDraft:
    params: dict[str, Any] = {
        "source_ids": ("h1",),
        "event_type": "geopolitical",
        "currencies": ("USD",),
        "severity": 3,
        "scheduled": False,
        "confidence": 0.9,
        "rationale": "",
        "observed_at": (AT - pd.Timedelta(minutes=30)).to_pydatetime(),
        "available_at": (AT - pd.Timedelta(minutes=25)).to_pydatetime(),
        "model_id": "echo-1",
        "model_digest": DIGEST,
        "manifest_hash": MANIFEST,
    }
    params.update(overrides)
    return AnnotationDraft(**params)


def annotation_store(
    path: Path,
    drafts: tuple[AnnotationDraft, ...] = (),
    *,
    scans: tuple[tuple[pd.Timestamp, str], ...] = (),
) -> AnnotationStore:
    """A store at ``path`` with ``drafts`` filed and one scan row per ``(as_of, outcome)``."""
    store = AnnotationStore(path)
    if drafts:
        store.append(drafts, batch_digest="b" * 64, recorded_by="test", workflow_id="wf_test")
    for as_of, outcome in scans:
        when = as_of.to_pydatetime()
        store.log_scan(
            as_of=when,
            since=when - timedelta(minutes=15),
            finished_at=when + timedelta(seconds=30),
            outcome=outcome,
            n_headlines=0,
            n_batches=0,
            n_annotations=0,
            truncated=False,
            workflow_id="wf_scan",
        )
    return store


def headline_store(
    path: Path, titles: list[tuple[str, datetime]], *, source: NewsSource = NewsSource.FED_RSS
) -> HeadlineStore:
    """A headline store with one item per ``(title, observed_at)``, in that order."""
    store = HeadlineStore(path)
    for i, (title, observed) in enumerate(titles):
        store.record(
            [
                NewItem(
                    source=source,
                    feed_key=f"{source.value}:test",
                    url=f"https://example.test/{source.value}/{i}",
                    title=title,
                    summary=None,
                    source_item_id=None,
                    vendor_published_at=None,
                    raw={"i": i},
                )
            ],
            observed,
        )
    return store


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


__all__ = ["AT", "DIGEST", "MANIFEST", "annotation_store", "draft", "headline_store", "utc"]
