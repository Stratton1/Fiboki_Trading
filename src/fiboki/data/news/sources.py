"""Headline sources: official central-bank feeds, and two optional vendor APIs.

Every reader turns bytes into :class:`~fiboki.data.news.store.NewItem`\\ s and
nothing else. No reader stamps availability: the recorder stamps
``observed_at`` from its own clock, once, for the whole poll.

Official feeds
--------------
:data:`OFFICIAL_FEEDS` lists the feed URLs found on each bank's own RSS index
page on 2026-09-28 (``discovered_from`` is that page; ``retrieved_at`` is when
the feed itself was fetched and parsed). None was guessed. Formats seen:
RSS 2.0 (Fed, ECB, BoE, BoJ, SNB) and RSS 1.0 / RDF with the RSS-CB
extension (RBA). Atom is supported for ``OTHER`` feeds.

XML safety: the standard library parser does not resolve external entities,
and any document declaring a DOCTYPE with an ENTITY is refused outright,
which closes entity-expansion attacks without a new dependency. Bodies above
:data:`MAX_FEED_BYTES` are refused.

The BIS central bankers' speeches feed (``bis_cbspeeches``, added 2026-09-29)
is found on https://www.bis.org/rss/index.htm. It is a REPUBLICATION channel:
the BIS posts speeches days after delivery, stamped at midnight, so it is
useful for text and coverage (it carries banks with no feed of their own) and
useless for event timing. BIS terms permit non-commercial redistribution and
limited extracts with citation. Its source is ``NewsSource.OTHER`` because
the store's source enum is a CHECK constraint baked into existing database
files; widening it is a store schema change owned elsewhere.

Vendor and open APIs (optional, off until configured)
-----------------------------------------------------
* **Finnhub** ``GET /api/v1/news?category=<general|forex>``; key sent in the
  ``X-Finnhub-Token`` header, never in the URL. The free tier's 60 calls per
  minute is enforced client-side by one limiter shared by every Finnhub
  client built from the same key (:func:`vendor_clients_from_env`).
* **Marketaux** ``GET /v1/news/all``; the API requires ``api_token`` as a query
  parameter, so every error message is scrubbed of it before it is logged.
* **GDELT DOC 2.0** ``GET https://api.gdeltproject.org/api/v2/doc/doc``
  (``mode=ArtList&format=json``): free and open, opt-in with
  ``FIBOKI_GDELT_ENABLED=true``. See :class:`GdeltDocClient` for its limits.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from fiboki.data.news.store import NewItem, NewsSource, normalise_url
from fiboki.data.providers.ratelimit import (
    SlidingWindowLimiter,
    finnhub_free_tier_limiter,
    gdelt_limiter,
)

__all__ = [
    "ENV_FINNHUB_API_KEY",
    "ENV_GDELT_ENABLED",
    "ENV_MARKETAUX_API_KEY",
    "GDELT_ATTRIBUTION",
    "GDELT_QUERIES",
    "MAX_FEED_BYTES",
    "OFFICIAL_FEEDS",
    "FeedFormatError",
    "FeedSpec",
    "FinnhubNewsClient",
    "GdeltDocClient",
    "GdeltQuery",
    "MarketauxNewsClient",
    "RssFeedReader",
    "SourceFetchError",
    "env_flag",
    "parse_feed",
    "parse_gdelt",
    "vendor_clients_from_env",
]

ENV_FINNHUB_API_KEY = "FIBOKI_FINNHUB_API_KEY"
ENV_MARKETAUX_API_KEY = "FIBOKI_MARKETAUX_API_KEY"
ENV_GDELT_ENABLED = "FIBOKI_GDELT_ENABLED"
MAX_FEED_BYTES = 5_000_000
USER_AGENT = "fiboki-news-recorder/1 (headline archive for research; polite polling)"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}


class FeedFormatError(ValueError):
    """The body is not a feed this module understands (or is unsafe to parse)."""


class SourceFetchError(RuntimeError):
    """A source could not be fetched. Its message never contains a credential."""


class HttpClient(Protocol):  # the httpx-shaped client used across data/providers
    def get(self, url: str, *, params: Mapping[str, Any] | None = ...,
            headers: Mapping[str, str] | None = ...) -> Any: ...


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def _http_get(client: Any, url: str, *, params: Mapping[str, Any] | None = None,
              headers: Mapping[str, str] | None = None, secrets: tuple[str, ...] = (),
              what: str, raw: bool = False) -> str | bytes:
    if client is None:
        raise SourceFetchError(f"{what}: no HTTP client configured")
    hdrs = {"User-Agent": USER_AGENT, **dict(headers or {})}
    try:
        response = client.get(url, params=dict(params or {}), headers=hdrs)
    except Exception as exc:
        raise SourceFetchError(_redact(f"{what}: transport failure: {exc}", secrets)) from None
    status = int(getattr(response, "status_code", 0))
    if not 200 <= status < 300:
        raise SourceFetchError(_redact(f"{what}: HTTP {status}", secrets))
    if raw and isinstance(getattr(response, "content", None), bytes):
        # XML declares its own encoding; hand the parser bytes, not a guess.
        return response.content
    text = getattr(response, "text", None)
    if text is None:
        body = getattr(response, "body", None)
        text = json.dumps(body) if body is not None else ""
    return str(text)


# ----------------------------------------------------------- official feeds


@dataclass(frozen=True, slots=True)
class FeedSpec:
    key: str
    source: NewsSource
    url: str
    fmt: str
    discovered_from: str
    retrieved_at: str
    notes: str = ""


_RETRIEVED = "2026-09-28T22:40:00Z"

#: Every URL below was taken from the bank's own RSS index page (``discovered_from``)
#: and fetched and parsed successfully at ``retrieved_at``. None was guessed.
OFFICIAL_FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec("fed_press_monetary", NewsSource.FED_RSS,
             "https://www.federalreserve.gov/feeds/press_monetary.xml", "rss2.0",
             "https://www.federalreserve.gov/feeds/feeds.htm", _RETRIEVED,
             "Press releases: monetary policy (FOMC statements, minutes)"),
    FeedSpec("fed_press_all", NewsSource.FED_RSS,
             "https://www.federalreserve.gov/feeds/press_all.xml", "rss2.0",
             "https://www.federalreserve.gov/feeds/feeds.htm", _RETRIEVED,
             "Press releases: all"),
    FeedSpec("fed_speeches", NewsSource.FED_RSS,
             "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml", "rss2.0",
             "https://www.federalreserve.gov/feeds/feeds.htm", _RETRIEVED,
             "Speeches and testimony"),
    FeedSpec("ecb_press", NewsSource.ECB_RSS,
             "https://www.ecb.europa.eu/rss/press.html", "rss2.0",
             "https://www.ecb.europa.eu/home/html/rss.en.html", _RETRIEVED,
             "Press releases, speeches, interviews, press conferences"),
    FeedSpec("ecb_blog", NewsSource.ECB_RSS,
             "https://www.ecb.europa.eu/rss/blog.html", "rss2.0",
             "https://www.ecb.europa.eu/home/html/rss.en.html", _RETRIEVED, "The ECB Blog"),
    FeedSpec("boe_news", NewsSource.BOE_RSS,
             "https://www.bankofengland.co.uk/rss/news", "rss2.0",
             "https://www.bankofengland.co.uk/rss", _RETRIEVED, "News (includes MPC decisions)"),
    FeedSpec("boe_speeches", NewsSource.BOE_RSS,
             "https://www.bankofengland.co.uk/rss/speeches", "rss2.0",
             "https://www.bankofengland.co.uk/rss", _RETRIEVED, "Speeches"),
    FeedSpec("boj_whatsnew", NewsSource.BOJ_RSS,
             "https://www.boj.or.jp/en/rss/whatsnew.xml", "rss2.0",
             "https://www.boj.or.jp/en/index.htm", _RETRIEVED,
             "What's new (English); the only feed linked from the English home page"),
    FeedSpec("snb_pressrel", NewsSource.SNB_RSS,
             "https://www.snb.ch/public/rss/en/pressrel", "rss2.0",
             "https://www.snb.ch/en/services-events/digital-services/rss-calendar-feeds",
             _RETRIEVED, "Press releases: general topics"),
    FeedSpec("snb_mopo", NewsSource.SNB_RSS,
             "https://www.snb.ch/public/rss/en/mopo", "rss2.0",
             "https://www.snb.ch/en/services-events/digital-services/rss-calendar-feeds",
             _RETRIEVED, "Monetary policy assessments"),
    FeedSpec("rba_media", NewsSource.RBA_RSS,
             "https://www.rba.gov.au/rss/rss-cb-media-releases.xml", "rss1.0",
             "https://www.rba.gov.au/rss/", _RETRIEVED, "Media releases (RSS-CB 1.2)"),
    FeedSpec("rba_speeches", NewsSource.RBA_RSS,
             "https://www.rba.gov.au/rss/rss-cb-speeches.xml", "rss1.0",
             "https://www.rba.gov.au/rss/", _RETRIEVED, "Speeches (RSS-CB 1.2)"),
    FeedSpec("bis_cbspeeches", NewsSource.OTHER,
             "https://www.bis.org/doclist/cbspeeches.rss", "rss1.0",
             "https://www.bis.org/rss/index.htm", "2026-09-29T10:30:00Z",
             "BIS central bankers' speeches (English), RSS 1.0 with the RSS-CB speech "
             "extension. Republished days after delivery; not an event clock. "
             "Terms: non-commercial, cite the BIS (https://www.bis.org/terms_conditions.htm)"),
)


_ENTITY_DECL = re.compile(rb"<!ENTITY", re.IGNORECASE)


def _parse_date(text: str | None) -> datetime | None:
    """RFC 822 (RSS 2.0) or ISO 8601 (dc:date, Atom) to aware UTC, or None."""
    if not text or not text.strip():
        return None
    raw = text.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return None  # a zone-less vendor stamp is not guessed; it stays informational-null
    return dt.astimezone(UTC)


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(body: bytes | str, *, source: NewsSource, feed_key: str) -> tuple[list[NewItem], int]:
    """Parse RSS 2.0, RSS 1.0 (RDF) or Atom; return ``(items, rejected)``.

    ``rejected`` counts entries without a title or without both a link and an
    id: they are reported, never silently dropped.
    """
    data = body.encode("utf-8") if isinstance(body, str) else body
    if len(data) > MAX_FEED_BYTES:
        raise FeedFormatError(f"{feed_key}: body of {len(data)} bytes exceeds {MAX_FEED_BYTES}")
    if _ENTITY_DECL.search(data):
        raise FeedFormatError(f"{feed_key}: refusing a feed that declares XML entities")
    data = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedFormatError(f"{feed_key}: not well-formed XML: {exc}") from None
    kind = _local(root.tag)
    if kind == "rss":
        entries = root.findall("./channel/item")
        fmt = "rss2.0"
    elif kind == "RDF":
        entries = root.findall("rss1:item", _NS)
        fmt = "rss1.0"
    elif kind == "feed" and root.tag.startswith("{" + _NS["atom"]):
        entries = root.findall("atom:entry", _NS)
        fmt = "atom"
    else:
        raise FeedFormatError(f"{feed_key}: root element {root.tag!r} is not RSS or Atom")

    items: list[NewItem] = []
    rejected = 0
    for e in entries:
        if fmt == "rss2.0":
            title = _text(e.find("title"))
            link = _text(e.find("link"))
            guid = _text(e.find("guid"))
            summary = _text(e.find("description"))
            published = _parse_date(_text(e.find("pubDate")) or _text(e.find("dc:date", _NS)))
        elif fmt == "rss1.0":
            title = _text(e.find("rss1:title", _NS))
            link = _text(e.find("rss1:link", _NS))
            guid = e.get(f"{{{_NS['rdf']}}}about")
            summary = _text(e.find("rss1:description", _NS))
            published = _parse_date(_text(e.find("dc:date", _NS)))
        else:
            title = _text(e.find("atom:title", _NS))
            link = None
            for ln in e.findall("atom:link", _NS):
                if ln.get("rel", "alternate") == "alternate" and ln.get("href"):
                    link = ln.get("href", "").strip()
                    break
            guid = _text(e.find("atom:id", _NS))
            summary = _text(e.find("atom:summary", _NS)) or _text(e.find("atom:content", _NS))
            published = _parse_date(
                _text(e.find("atom:published", _NS)) or _text(e.find("atom:updated", _NS))
            )
        if not title or not (link or guid):
            rejected += 1
            continue
        items.append(
            NewItem(
                source=source,
                feed_key=feed_key,
                url=link or "",
                title=" ".join(title.split()),
                summary=summary,
                source_item_id=guid,
                vendor_published_at=published,
                raw={"format": fmt, "feed_key": feed_key,
                     "item_xml": ET.tostring(e, encoding="unicode")},
            )
        )
    return items, rejected


class RssFeedReader:
    """Fetch and parse one official feed."""

    def __init__(self, spec: FeedSpec, http_client: Any) -> None:
        self.spec = spec
        self.http_client = http_client
        self.key = spec.key
        self.source = spec.source
        self.min_interval_s = 0.0

    def fetch(self) -> tuple[list[NewItem], int]:
        body = _http_get(self.http_client, self.spec.url, what=f"feed {self.spec.key}", raw=True)
        return parse_feed(body, source=self.spec.source, feed_key=self.spec.key)


# ------------------------------------------------------------------ vendors


class FinnhubNewsClient:
    """Finnhub market news (personal tier). ``minId`` is not used: dedupe does that job."""

    HOST = "https://finnhub.io"

    def __init__(self, api_key: str, http_client: Any, *, category: str = "forex",
                 min_interval_s: float = 60.0,
                 rate_limiter: SlidingWindowLimiter | None = None) -> None:
        if not api_key:
            raise ValueError("FinnhubNewsClient needs an API key; leave it unconfigured instead")
        if category not in ("general", "forex", "crypto", "merger"):
            raise ValueError(f"unknown Finnhub news category {category!r}")
        self._key = api_key
        self.http_client = http_client
        self.category = category
        self.key = f"finnhub_{category}"
        self.source = NewsSource.FINNHUB
        self.min_interval_s = min_interval_s
        #: The free tier's 60/min is per KEY: pass the same limiter to every
        #: client (news and calendar) that uses this key.
        self.rate_limiter = rate_limiter if rate_limiter is not None else finnhub_free_tier_limiter()

    def fetch(self) -> tuple[list[NewItem], int]:
        self.rate_limiter.acquire()
        text = _http_get(
            self.http_client, f"{self.HOST}/api/v1/news", params={"category": self.category},
            headers={"X-Finnhub-Token": self._key}, secrets=(self._key,), what=self.key,
        )
        return parse_finnhub(str(text), feed_key=self.key)


def parse_finnhub(text: str, *, feed_key: str) -> tuple[list[NewItem], int]:
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise FeedFormatError(f"{feed_key}: expected a JSON array, got {type(payload).__name__}")
    items: list[NewItem] = []
    rejected = 0
    for rec in payload:
        title = str(rec.get("headline") or "").strip()
        url = str(rec.get("url") or "").strip()
        rid = rec.get("id")
        if not title or not (url or rid is not None):
            rejected += 1
            continue
        ts = rec.get("datetime")
        published = datetime.fromtimestamp(int(ts), tz=UTC) if isinstance(ts, int | float) and ts > 0 else None
        items.append(
            NewItem(
                source=NewsSource.FINNHUB, feed_key=feed_key, url=url, title=" ".join(title.split()),
                summary=(str(rec.get("summary")).strip() or None) if rec.get("summary") else None,
                source_item_id=None if rid is None else str(rid),
                vendor_published_at=published, raw=rec,
            )
        )
    return items, rejected


class MarketauxNewsClient:
    """Marketaux ``/v1/news/all``. The token must travel as a query parameter."""

    HOST = "https://api.marketaux.com"

    def __init__(self, api_token: str, http_client: Any, *, language: str = "en",
                 search: str | None = None, min_interval_s: float = 900.0) -> None:
        if not api_token:
            raise ValueError("MarketauxNewsClient needs an API token; leave it unconfigured instead")
        self._token = api_token
        self.http_client = http_client
        self.language = language
        self.search = search
        self.key = "marketaux_all"
        self.source = NewsSource.MARKETAUX
        #: Free/Basic plans cap requests per day; 900 s is 96 requests a day.
        self.min_interval_s = min_interval_s

    def fetch(self) -> tuple[list[NewItem], int]:
        params: dict[str, Any] = {"language": self.language, "api_token": self._token}
        if self.search:
            params["search"] = self.search
        text = _http_get(self.http_client, f"{self.HOST}/v1/news/all", params=params,
                         secrets=(self._token,), what=self.key)
        return parse_marketaux(str(text), feed_key=self.key)


def parse_marketaux(text: str, *, feed_key: str) -> tuple[list[NewItem], int]:
    payload = json.loads(text)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise FeedFormatError(f"{feed_key}: response has no 'data' array")
    items: list[NewItem] = []
    rejected = 0
    for rec in data:
        title = str(rec.get("title") or "").strip()
        url = str(rec.get("url") or "").strip()
        uuid = rec.get("uuid")
        if not title or not (url or uuid):
            rejected += 1
            continue
        items.append(
            NewItem(
                source=NewsSource.MARKETAUX, feed_key=feed_key, url=url,
                title=" ".join(title.split()),
                summary=(str(rec.get("description") or rec.get("snippet") or "").strip() or None),
                source_item_id=None if uuid is None else str(uuid),
                vendor_published_at=_parse_date(rec.get("published_at")), raw=rec,
            )
        )
    return items, rejected


# -------------------------------------------------------------------- GDELT


#: Required by the GDELT terms (https://www.gdeltproject.org/about.html): "any
#: use or redistribution of the data must include a citation to the GDELT
#: Project and a link to this website".
GDELT_ATTRIBUTION = "Source: The GDELT Project (https://www.gdeltproject.org/)."

_GDELT_SEENDATE = re.compile(r"^(\d{8})T?(\d{6})Z?$")


@dataclass(frozen=True, slots=True)
class GdeltQuery:
    """One curated DOC 2.0 query. ``key`` becomes the feed key ``gdelt_<key>``."""

    key: str
    query: str
    notes: str = ""


#: Curated queries for FX, gold, indices and central banks. GDELT syntax: OR
#: groups must be parenthesised, phrases quoted; ``sourcelang:english`` keeps
#: the archive to text we can read. Kept few and broad: every query costs one
#: request per poll and GDELT allows one request every five seconds.
GDELT_QUERIES: tuple[GdeltQuery, ...] = (
    GdeltQuery("central_banks",
               '("Federal Reserve" OR "European Central Bank" OR "Bank of England" OR '
               '"Bank of Japan" OR "Swiss National Bank" OR "Reserve Bank of Australia") '
               "sourcelang:english",
               "The six banks whose official feeds are recorded, as the press reports them"),
    GdeltQuery("fx_majors",
               '("forex" OR "currency markets" OR "sterling" OR "yen" OR "euro zone" OR '
               '"Swiss franc" OR "Australian dollar") sourcelang:english',
               "Major-currency market coverage"),
    GdeltQuery("us_dollar",
               '("dollar index" OR "US dollar" OR "greenback" OR "Treasury yields") '
               "sourcelang:english", "USD and the US rates complex"),
    GdeltQuery("gold",
               '("gold price" OR "gold prices" OR "bullion" OR "spot gold") sourcelang:english',
               "XAUUSD"),
    GdeltQuery("equity_indices",
               '("S&P 500" OR "Nasdaq" OR "Dow Jones" OR "FTSE 100" OR "DAX" OR "Nikkei") '
               "sourcelang:english", "Index CFDs"),
    GdeltQuery("macro_releases",
               '("rate hike" OR "rate cut" OR "inflation data" OR "nonfarm payrolls" OR '
               '"consumer prices" OR "jobs report") sourcelang:english',
               "Scheduled-release coverage, for comparison with the official calendar"),
)


def _parse_gdelt_seendate(raw: Any) -> datetime | None:
    m = _GDELT_SEENDATE.match(str(raw or "").strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_gdelt(text: str, *, feed_key: str) -> tuple[list[NewItem], int]:
    """Parse a DOC 2.0 ``ArtList`` JSON body; dedupe by normalised URL within it.

    GDELT answers some malformed queries with HTTP 200 and a plain-text
    message; that is a :class:`FeedFormatError`, never an empty result. A
    response with no matches is ``{}`` (no ``articles`` key), which is a real
    empty result. ``seendate`` (GDELT's crawl instant, UTC) is kept as the
    informational vendor time; availability is still our ``observed_at``.
    """
    body = text.strip()
    if not body:
        return [], 0
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise FeedFormatError(f"{feed_key}: GDELT returned non-JSON: {body[:160]!r}") from None
    if not isinstance(payload, dict):
        raise FeedFormatError(f"{feed_key}: expected a JSON object from GDELT")
    articles = payload.get("articles", [])
    if not isinstance(articles, list):
        raise FeedFormatError(f"{feed_key}: 'articles' is not a list")
    items: list[NewItem] = []
    seen: set[str] = set()
    rejected = 0
    for rec in articles:
        title = " ".join(str(rec.get("title") or "").split())
        url = str(rec.get("url") or "").strip()
        if not title or not url:
            rejected += 1
            continue
        norm = normalise_url(url)
        if norm in seen:
            continue  # the same article listed twice in one response
        seen.add(norm)
        items.append(
            NewItem(
                source=NewsSource.OTHER, feed_key=feed_key, url=url, title=title, summary=None,
                source_item_id=None, vendor_published_at=_parse_gdelt_seendate(rec.get("seendate")),
                raw={"provider": "gdelt_doc_v2", **{k: rec.get(k) for k in (
                    "url", "title", "seendate", "domain", "language", "sourcecountry")}},
            )
        )
    return items, rejected


class GdeltDocClient:
    """GDELT DOC 2.0 article search for one curated query.

    Limits, stated (GDELT's DOC 2.0 announcement, fetched 2026-09-29, and its
    429 body): the API searches a rolling window of the last **3 months** of
    coverage; ``maxrecords`` is at most **250** per request; requests are
    limited to **one every 5 seconds** per client, enforced here by a limiter
    shared across all GDELT queries. The query asks for the newest articles in
    the last ``timespan``; when a response holds exactly ``maxrecords`` the
    window was truncated and older matches in it were not returned, which
    ``last_report["truncated"]`` records so the gap is visible.

    Items carry ``NewsSource.OTHER`` and feed key ``gdelt_<query key>``; the
    store's ``(source, url_hash)`` dedupe therefore merges the same article
    across GDELT queries (and with the BIS feed if a URL ever coincides).
    """

    HOST = "https://api.gdeltproject.org"
    PATH = "/api/v2/doc/doc"
    MAX_RECORDS = 250

    def __init__(self, spec: GdeltQuery, http_client: Any, *, timespan: str = "1h",
                 maxrecords: int = MAX_RECORDS, min_interval_s: float = 900.0,
                 rate_limiter: SlidingWindowLimiter | None = None) -> None:
        if not 1 <= maxrecords <= self.MAX_RECORDS:
            raise ValueError(f"maxrecords must be in 1..{self.MAX_RECORDS}")
        self.spec = spec
        self.http_client = http_client
        self.timespan = timespan
        self.maxrecords = int(maxrecords)
        self.key = f"gdelt_{spec.key}"
        self.source = NewsSource.OTHER
        self.min_interval_s = min_interval_s
        self.rate_limiter = rate_limiter if rate_limiter is not None else gdelt_limiter()
        self.last_report: dict[str, Any] = {}

    def params(self) -> dict[str, Any]:
        return {"query": self.spec.query, "mode": "ArtList", "format": "json",
                "maxrecords": self.maxrecords, "timespan": self.timespan, "sort": "DateDesc"}

    def fetch(self) -> tuple[list[NewItem], int]:
        self.rate_limiter.acquire()
        text = _http_get(self.http_client, f"{self.HOST}{self.PATH}", params=self.params(),
                         what=self.key)
        items, rejected = parse_gdelt(str(text), feed_key=self.key)
        truncated = len(items) + rejected >= self.maxrecords
        self.last_report = {"truncated": truncated} if truncated else {}
        return items, rejected


def env_flag(environ: Mapping[str, str], name: str, default: bool = False) -> bool:
    """Strict boolean from the environment, with the same spellings as
    ``fiboki.api.settings.parse_bool`` (which the data layer may not import).
    Anything unrecognised raises rather than silently meaning False."""
    raw = (environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean; use true/false. Refusing to guess.")


def vendor_clients_from_env(
    http_client: Any, *, env: Mapping[str, str] | None = None,
    finnhub_categories: tuple[str, ...] = ("forex", "general"),
    marketaux_min_interval_s: float = 900.0,
    finnhub_limiter: SlidingWindowLimiter | None = None,
    gdelt_queries: tuple[GdeltQuery, ...] = GDELT_QUERIES,
) -> tuple[list[Any], dict[str, str]]:
    """Configured vendor and open-API clients, plus a reason for each one that is off.

    A source that is not configured is OFF and says so; it is never a silent
    skip. Every Finnhub client built here shares one 60-per-minute limiter
    (pass ``finnhub_limiter`` to share it with the calendar client too).
    """
    environ = os.environ if env is None else env
    clients: list[Any] = []
    off: dict[str, str] = {}
    fh = (environ.get(ENV_FINNHUB_API_KEY) or "").strip()
    if fh:
        limiter = finnhub_limiter if finnhub_limiter is not None else finnhub_free_tier_limiter()
        clients += [FinnhubNewsClient(fh, http_client, category=c, rate_limiter=limiter)
                    for c in finnhub_categories]
    else:
        off["finnhub"] = f"{ENV_FINNHUB_API_KEY} not set"
    mx = (environ.get(ENV_MARKETAUX_API_KEY) or "").strip()
    if mx:
        clients.append(MarketauxNewsClient(mx, http_client, min_interval_s=marketaux_min_interval_s))
    else:
        off["marketaux"] = f"{ENV_MARKETAUX_API_KEY} not set"
    if env_flag(environ, ENV_GDELT_ENABLED):
        shared = gdelt_limiter()
        clients += [GdeltDocClient(q, http_client, rate_limiter=shared) for q in gdelt_queries]
    else:
        off["gdelt"] = f"{ENV_GDELT_ENABLED} not true (opt-in)"
    return clients, off
