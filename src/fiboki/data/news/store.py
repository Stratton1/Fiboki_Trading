"""The append-only headline store, and its point-in-time read.

One SQLite file (``<state_dir>/news/headlines.sqlite``, WAL). Every table is
append-only, enforced by triggers in the database itself (the pattern of
``research/experiment.py``), so no code path, including a future one, can
rewrite what was recorded:

* ``headline``: one row per ``(source, url_hash)``, first-seen content only.
* ``headline_revision``: a later poll that saw the same item with different
  content (a retitled press release) is appended here; the headline row is
  never touched. :meth:`HeadlineStore.query` serves the first-seen row.
* ``feed_source``: the configured feed URLs and when they were discovered.
* ``poll_log``: one row per poll with per-feed outcomes, which is what makes
  capture gaps visible rather than silent.

``INSERT OR REPLACE`` is refused as well as ``UPDATE`` and ``DELETE``: SQLite
implements REPLACE as delete-then-insert WITHOUT firing delete triggers, so a
``BEFORE INSERT`` trigger rejects any insert that would collide.

The availability instant of a headline is ``observed_at``: the moment OUR
clock first saw it. ``vendor_published_at`` is what the feed claimed and is
informational only; feeds back-date, omit and mis-zone it, and a backtest that
trusted it would read headlines before anyone could have.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "APPEND_ONLY_MESSAGE",
    "CURRENCY_BY_SOURCE",
    "Headline",
    "HeadlineStore",
    "NewsSource",
    "NewsStoreError",
    "content_hash",
    "default_store_path",
    "normalise_url",
    "to_utc_text",
    "url_hash",
]

APPEND_ONLY_MESSAGE = "news store is append-only"
SCHEMA_VERSION = 1


class NewsStoreError(RuntimeError):
    pass


class NewsSource(str, Enum):
    FED_RSS = "fed_rss"
    ECB_RSS = "ecb_rss"
    BOE_RSS = "boe_rss"
    BOJ_RSS = "boj_rss"
    SNB_RSS = "snb_rss"
    RBA_RSS = "rba_rss"
    FINNHUB = "finnhub"
    MARKETAUX = "marketaux"
    OTHER = "other"


#: Central-bank source -> the currency it sets policy for. Vendor sources have
#: no deterministic currency mapping (tagging them is a later, separate step).
CURRENCY_BY_SOURCE: dict[NewsSource, str] = {
    NewsSource.FED_RSS: "USD",
    NewsSource.ECB_RSS: "EUR",
    NewsSource.BOE_RSS: "GBP",
    NewsSource.BOJ_RSS: "JPY",
    NewsSource.SNB_RSS: "CHF",
    NewsSource.RBA_RSS: "AUD",
}


def default_store_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / "news" / "headlines.sqlite"


# ------------------------------------------------------------------ helpers


def to_utc_text(value: datetime) -> str:
    """Fixed-width UTC ISO text, so lexical order in SQLite is time order."""
    if value.tzinfo is None:
        raise NewsStoreError(f"naive datetime {value!r}; every stored instant is UTC-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _from_text(text: str | None) -> datetime | None:
    if text is None:
        return None
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def normalise_url(url: str) -> str:
    """Lower-case scheme and host, drop the fragment; keep path and query verbatim.

    Deliberately minimal: collapsing ``//`` in a path or reordering a query
    could merge two distinct items, and a false merge loses a headline, which
    is worse than a false duplicate.
    """
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def url_hash(url: str, *, fallback_id: str | None = None) -> str:
    key = normalise_url(url) if url.strip() else f"id:{fallback_id or ''}"
    if key == "id:":
        raise NewsStoreError("an item needs a URL or a source item id to be deduplicated")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def content_hash(title: str, summary: str | None, url: str, vendor_published_at: str | None) -> str:
    payload = json.dumps(
        {"title": title, "summary": summary, "url": url, "vendor_published_at": vendor_published_at},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- schema

_SOURCES_SQL = ",".join(f"'{s.value}'" for s in NewsSource)

_DDL = (
    f"""
    CREATE TABLE IF NOT EXISTS headline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL CHECK (source IN ({_SOURCES_SQL})),
        source_item_id TEXT,
        url TEXT NOT NULL,
        url_hash TEXT NOT NULL,
        title TEXT NOT NULL CHECK (length(title) > 0),
        summary TEXT,
        vendor_published_at TEXT,
        observed_at TEXT NOT NULL CHECK (length(observed_at) = 27),
        raw_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        feed_key TEXT NOT NULL,
        UNIQUE (source, url_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS headline_observed ON headline (observed_at, id)",
    """
    CREATE TABLE IF NOT EXISTS headline_revision (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        headline_id INTEGER NOT NULL REFERENCES headline (id),
        observed_at TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        vendor_published_at TEXT,
        raw_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        feed_key TEXT NOT NULL,
        UNIQUE (headline_id, content_hash)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS feed_source (
        feed_key TEXT PRIMARY KEY,
        source TEXT NOT NULL CHECK (source IN ({_SOURCES_SQL})),
        url TEXT NOT NULL,
        format TEXT NOT NULL,
        discovered_from TEXT NOT NULL,
        retrieved_at TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS poll_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        outcomes_json TEXT NOT NULL
    )
    """,
    "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)

_APPEND_ONLY_TABLES: dict[str, str] = {
    "headline": "(source = NEW.source AND url_hash = NEW.url_hash) OR id = NEW.id",
    "headline_revision": "(headline_id = NEW.headline_id AND content_hash = NEW.content_hash) OR id = NEW.id",
    "feed_source": "feed_key = NEW.feed_key",
    "poll_log": "id = NEW.id",
    "store_meta": "key = NEW.key",
}


def _triggers() -> list[str]:
    out: list[str] = []
    for table, collision in _APPEND_ONLY_TABLES.items():
        out.append(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}'); END"
        )
        out.append(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}'); END"
        )
        out.append(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {collision}) "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}: row exists'); END"
        )
    return out


# ------------------------------------------------------------------- models


@dataclass(frozen=True, slots=True)
class Headline:
    """One recorded headline. ``available_at`` is ``observed_at``, by definition."""

    id: int
    source: NewsSource
    source_item_id: str | None
    url: str
    url_hash: str
    title: str
    summary: str | None
    vendor_published_at: datetime | None
    observed_at: datetime
    raw_json: str
    content_hash: str
    feed_key: str

    @property
    def available_at(self) -> datetime:
        return self.observed_at

    @property
    def currency(self) -> str | None:
        return CURRENCY_BY_SOURCE.get(self.source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source.value,
            "source_item_id": self.source_item_id,
            "url": self.url,
            "title": self.title,
            "summary": self.summary,
            "vendor_published_at": (
                None if self.vendor_published_at is None else self.vendor_published_at.isoformat()
            ),
            "observed_at": self.observed_at.isoformat(),
            "content_hash": self.content_hash,
            "feed_key": self.feed_key,
        }


@dataclass(frozen=True, slots=True)
class NewItem:
    """What a source reader hands the store: everything except our clock and id."""

    source: NewsSource
    feed_key: str
    url: str
    title: str
    summary: str | None
    source_item_id: str | None
    vendor_published_at: datetime | None
    raw: Mapping[str, Any]

    @property
    def url_hash(self) -> str:
        return url_hash(self.url, fallback_id=self.source_item_id)

    @property
    def vendor_published_text(self) -> str | None:
        v = self.vendor_published_at
        return None if v is None else to_utc_text(v)

    @property
    def content_hash(self) -> str:
        return content_hash(
            self.title, self.summary, normalise_url(self.url), self.vendor_published_text
        )


# -------------------------------------------------------------------- store


class HeadlineStore:
    """Append-only SQLite headline store with a point-in-time query."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._tx():
            for ddl in (*_DDL, *_triggers()):
                self._conn.execute(ddl)
            row = self._conn.execute(
                "SELECT value FROM store_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO store_meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row[0]) != SCHEMA_VERSION:
                raise NewsStoreError(
                    f"{self.path} has schema {row[0]}, this code writes {SCHEMA_VERSION}"
                )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> HeadlineStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    # -- writes -----------------------------------------------------------

    def register_feed(
        self,
        *,
        feed_key: str,
        source: NewsSource,
        url: str,
        fmt: str,
        discovered_from: str,
        retrieved_at: str,
        notes: str = "",
    ) -> bool:
        """Record a feed once. Same facts again: no-op. Different facts: refuse.

        A changed URL is a new feed with a new key; rewriting the old row would
        erase the record of where earlier headlines came from.
        """
        with self._tx() as conn:
            row = conn.execute(
                "SELECT source, url FROM feed_source WHERE feed_key = ?", (feed_key,)
            ).fetchone()
            if row is not None:
                if (row[0], row[1]) != (source.value, url):
                    raise NewsStoreError(
                        f"feed {feed_key!r} is recorded as {row[0]} {row[1]}; refusing to "
                        f"redefine it as {source.value} {url}. Use a new feed key."
                    )
                return False
            conn.execute(
                "INSERT INTO feed_source (feed_key, source, url, format, discovered_from, "
                "retrieved_at, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (feed_key, source.value, url, fmt, discovered_from, retrieved_at, notes),
            )
            return True

    def record(self, items: Iterable[NewItem], observed_at: datetime) -> dict[str, int]:
        """Insert unseen items stamped ``observed_at``; return counts.

        Counts: ``new``, ``duplicate`` (same source+URL, same content),
        ``revised`` (same source+URL, new content: appended to
        ``headline_revision``), ``revision_duplicate`` (that revision already
        recorded). One transaction per call, so a crash loses the whole poll's
        inserts or none of them, and the next poll re-inserts them.
        """
        stamp = to_utc_text(observed_at)
        counts = {"new": 0, "duplicate": 0, "revised": 0, "revision_duplicate": 0}
        with self._tx() as conn:
            for item in items:
                uh = item.url_hash
                ch = item.content_hash
                raw = json.dumps(item.raw, sort_keys=True, ensure_ascii=False, default=str)
                row = conn.execute(
                    "SELECT id, content_hash FROM headline WHERE source = ? AND url_hash = ?",
                    (item.source.value, uh),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO headline (source, source_item_id, url, url_hash, title, "
                        "summary, vendor_published_at, observed_at, raw_json, content_hash, "
                        "feed_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (item.source.value, item.source_item_id, item.url.strip(), uh,
                         item.title, item.summary, item.vendor_published_text, stamp, raw, ch,
                         item.feed_key),
                    )
                    counts["new"] += 1
                    continue
                if row[1] == ch:
                    counts["duplicate"] += 1
                    continue
                seen = conn.execute(
                    "SELECT 1 FROM headline_revision WHERE headline_id = ? AND content_hash = ?",
                    (row[0], ch),
                ).fetchone()
                if seen is not None:
                    counts["revision_duplicate"] += 1
                    continue
                conn.execute(
                    "INSERT INTO headline_revision (headline_id, observed_at, title, summary, "
                    "vendor_published_at, raw_json, content_hash, feed_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (row[0], stamp, item.title, item.summary, item.vendor_published_text, raw,
                     ch, item.feed_key),
                )
                counts["revised"] += 1
        return counts

    def log_poll(
        self, started_at: datetime, finished_at: datetime, outcomes: Mapping[str, Any]
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO poll_log (started_at, finished_at, outcomes_json) VALUES (?, ?, ?)",
                (to_utc_text(started_at), to_utc_text(finished_at),
                 json.dumps(outcomes, sort_keys=True, default=str)),
            )

    # -- reads ------------------------------------------------------------

    def query(
        self,
        as_of: datetime,
        since: datetime,
        sources: Iterable[NewsSource | str] | None = None,
        currencies_hint: Iterable[str] | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[Headline, ...]:
        """Headlines first observed in ``[since, as_of]``, oldest first.

        Point-in-time by construction: the filter is on ``observed_at`` and
        nothing else, so no row observed after ``as_of`` can be returned,
        whatever its vendor timestamp says. ``as_of`` is mandatory; there is
        no "latest" mode.

        ``currencies_hint`` narrows the CENTRAL-BANK sources to the banks of
        those currencies; vendor sources are not currency-tagged at this layer
        and pass through unchanged (so it is a hint, not a filter on them).
        With ``limit``, the NEWEST ``limit`` rows in the window are returned,
        still oldest first.
        """
        if as_of is None or since is None:
            raise NewsStoreError("query needs both as_of and since; there is no 'latest' mode")
        if since > as_of:
            raise NewsStoreError(f"since {since} is after as_of {as_of}")
        wanted = None if sources is None else {NewsSource(s) for s in sources}
        if currencies_hint is not None:
            ccys = {c.upper() for c in currencies_hint}
            banks = {s for s, c in CURRENCY_BY_SOURCE.items() if c in ccys}
            vendors = {s for s in NewsSource if s not in CURRENCY_BY_SOURCE}
            allowed = banks | vendors
            wanted = allowed if wanted is None else wanted & allowed
        sql = (
            "SELECT id, source, source_item_id, url, url_hash, title, summary, "
            "vendor_published_at, observed_at, raw_json, content_hash, feed_key FROM headline "
            "WHERE observed_at >= ? AND observed_at <= ?"
        )
        params: list[Any] = [to_utc_text(since), to_utc_text(as_of)]
        if wanted is not None:
            if not wanted:
                return ()
            sql += f" AND source IN ({','.join('?' * len(wanted))})"
            params += sorted(s.value for s in wanted)
        if limit is not None:
            sql = f"SELECT * FROM ({sql} ORDER BY observed_at DESC, id DESC LIMIT ?)"
            params.append(int(limit))
        sql += " ORDER BY observed_at ASC, id ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return tuple(_row_to_headline(r) for r in rows)

    def status(self) -> dict[str, Any]:
        """Row counts, per-source and per-feed recency, and the poll history."""
        conn = self._conn
        total = conn.execute("SELECT COUNT(*) FROM headline").fetchone()[0]
        per_source = {
            src: {"rows": n, "first_observed_at": first, "last_observed_at": last}
            for src, n, first, last in conn.execute(
                "SELECT source, COUNT(*), MIN(observed_at), MAX(observed_at) FROM headline "
                "GROUP BY source ORDER BY source"
            )
        }
        revisions = conn.execute("SELECT COUNT(*) FROM headline_revision").fetchone()[0]
        feeds = [
            dict(zip(("feed_key", "source", "url", "format", "discovered_from", "retrieved_at"), r,
                     strict=True))
            for r in conn.execute(
                "SELECT feed_key, source, url, format, discovered_from, retrieved_at "
                "FROM feed_source ORDER BY feed_key"
            )
        ]
        polls = [
            (_from_text(s), _from_text(f), json.loads(o))
            for s, f, o in conn.execute(
                "SELECT started_at, finished_at, outcomes_json FROM poll_log ORDER BY id"
            )
        ]
        return {
            "rows": total,
            "revisions": revisions,
            "per_source": per_source,
            "feeds": feeds,
            "polls": polls,
        }


def _row_to_headline(r: tuple[Any, ...]) -> Headline:
    observed = _from_text(r[8])
    assert observed is not None  # NOT NULL in the schema
    return Headline(
        id=int(r[0]),
        source=NewsSource(r[1]),
        source_item_id=r[2],
        url=r[3],
        url_hash=r[4],
        title=r[5],
        summary=r[6],
        vendor_published_at=_from_text(r[7]),
        observed_at=observed,
        raw_json=r[9],
        content_hash=r[10],
        feed_key=r[11],
    )
