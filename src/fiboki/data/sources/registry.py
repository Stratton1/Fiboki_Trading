"""Every external data source Fiboki knows about, with its terms.

Licences are a product requirement. Each :class:`SourceEntry` records where
the terms are (``terms_url``), what they permit (``terms_summary``, quoted or
closely paraphrased from that page when it was read, with the date), and a
``terms_status``:

* ``permitted``: the terms allow our use (automated retrieval and storage for
  research), usually with an attribution we must carry.
* ``personal_only``: allowed for the operator's own, non-commercial research;
  no redistribution, no commercial product built on it without a licence.
* ``opt_in_unclear``: no licence found for the automated use, or the terms
  arguably prohibit it. Implemented only behind an explicit opt-in, off by
  default, never exercised in CI.
* ``forbidden``: the terms prohibit it (or it needs a paid licence we do not
  hold). Never implemented; listed so nobody adds it by accident.

The registry is data, read by ``describe_sources()`` for the API and docs.
``tests/unit/test_source_registry.py`` fails when an implemented source
module is not listed here, or a listed one lacks a terms URL.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "SOURCE_REGISTRY",
    "SourceEntry",
    "SourceKind",
    "TermsStatus",
    "describe_sources",
    "registry_markdown",
    "source",
]

READ = "read 2026-09-29"


class TermsStatus(str, Enum):
    PERMITTED = "permitted"
    PERSONAL_ONLY = "personal_only"
    OPT_IN_UNCLEAR = "opt_in_unclear"
    FORBIDDEN = "forbidden"


class SourceKind(str, Enum):
    NEWS = "news"
    CALENDAR = "calendar"
    POSITIONING = "positioning"
    MACRO = "macro"
    PRICES = "prices"


@dataclass(frozen=True, slots=True)
class SourceEntry:
    name: str
    kind: SourceKind
    cost: str
    terms_url: str
    terms_status: TermsStatus
    terms_summary: str
    key_env: tuple[str, ...]
    cadence: str
    history_depth: str
    point_in_time: str
    module: str | None
    implemented: bool
    feed_keys: tuple[str, ...] = ()
    notes: str = ""
    extra_urls: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["terms_status"] = self.terms_status.value
        d["key_env"] = list(self.key_env)
        d["feed_keys"] = list(self.feed_keys)
        d["extra_urls"] = list(self.extra_urls)
        return d


N, C, P, M, PR = (SourceKind.NEWS, SourceKind.CALENDAR, SourceKind.POSITIONING,
                  SourceKind.MACRO, SourceKind.PRICES)
OK, PERS, UNCL, NO = (TermsStatus.PERMITTED, TermsStatus.PERSONAL_ONLY,
                      TermsStatus.OPT_IN_UNCLEAR, TermsStatus.FORBIDDEN)
_RSS_PIT = "observed_at (our clock at first poll); vendor pubDate informational only"
_RSS_HIST = "from the recorder's first poll; a feed holds only its latest items"
_NEWS_MOD = "fiboki.data.news.sources"

SOURCE_REGISTRY: tuple[SourceEntry, ...] = (
    # ------------------------------------------------------------------ news
    SourceEntry(
        "fed_rss", N, "free", "https://www.federalreserve.gov/disclaimer.htm", OK,
        f"'Unless otherwise indicated, information on Board's website is in the public domain "
        f"and may be copied and distributed without permission. Please cite to the Board' ({READ})",
        (), "300 s poll", _RSS_HIST, _RSS_PIT, _NEWS_MOD, True,
        ("fed_press_monetary", "fed_press_all", "fed_speeches"),
        "speeches_and_testimony.xml covers speeches.xml and testimony.xml (verified on "
        "https://www.federalreserve.gov/feeds/feeds.htm)"),
    SourceEntry(
        "ecb_rss", N, "free", "https://www.ecb.europa.eu/services/disclaimer/html/index.en.html",
        OK, f"Free use of information obtained directly from the site, citing the ECB; documents "
        f"that are sold must say the information is free on the ECB site ({READ})",
        (), "300 s poll", _RSS_HIST, _RSS_PIT, _NEWS_MOD, True, ("ecb_press", "ecb_blog"),
        "The ECB has no separate speeches feed: press.html carries press releases, speeches, "
        "interviews and press conferences (https://www.ecb.europa.eu/home/html/rss.en.html)"),
    SourceEntry(
        "boe_rss", N, "free", "https://www.bankofengland.co.uk/legal", OK,
        f"Open Government Licence v3.0, except third-party exchange-rate series ({READ})",
        (), "300 s poll", _RSS_HIST, _RSS_PIT, _NEWS_MOD, True, ("boe_news", "boe_speeches")),
    SourceEntry(
        "boj_rss", N, "free", "https://www.boj.or.jp/en/about/copyright.htm", PERS,
        f"May be copied with the Bank of Japan credited; 'copying or reproduction of the content "
        f"for commercial purposes' is prohibited ({READ})",
        (), "300 s poll", _RSS_HIST, _RSS_PIT, _NEWS_MOD, True, ("boj_whatsnew",),
        "whatsnew.xml is the only English feed linked from https://www.boj.or.jp/en/index.htm; "
        "BoJ speeches appear there and in the BIS feed"),
    SourceEntry(
        "snb_rss", N, "free", "https://www.snb.ch/en/srv/disclaimer_copyright", PERS,
        f"May be saved, transmitted or used 'for non-commercial purposes', with source ({READ})",
        (), "300 s poll", _RSS_HIST, _RSS_PIT, _NEWS_MOD, True, ("snb_pressrel", "snb_mopo")),
    SourceEntry(
        "rba_rss", N, "free", "https://www.rba.gov.au/copyright/", OK,
        f"Creative Commons Attribution 4.0; 'Source: Reserve Bank of Australia' ({READ})",
        (), "300 s poll", _RSS_HIST, _RSS_PIT, _NEWS_MOD, True, ("rba_media", "rba_speeches")),
    SourceEntry(
        "bis_cbspeeches", N, "free", "https://www.bis.org/terms_conditions.htm", PERS,
        f"Download, display and redistribute 'for non-commercial purposes'; extracts up to 400 "
        f"words with the BIS cited ({READ})",
        (), "300 s poll", _RSS_HIST,
        _RSS_PIT + "; BIS republishes speeches days after delivery, stamped midnight",
        _NEWS_MOD, True, ("bis_cbspeeches",),
        "Found on https://www.bis.org/rss/index.htm. Stored as NewsSource.OTHER (the source enum "
        "is a CHECK constraint in existing store files)."),
    SourceEntry(
        "finnhub_news", N, "free tier (60 calls/min, enforced client-side)",
        "https://finnhub.io/terms-of-service", PERS,
        "Terms page refuses automated reading (robots.txt), so not read this session; personal "
        "tier assumed personal research use, no redistribution",
        ("FIBOKI_FINNHUB_API_KEY",), "60 s per category", "vendor keeps a short rolling list",
        _RSS_PIT, _NEWS_MOD, True, ("finnhub_forex", "finnhub_general"),
        "Key in the X-Finnhub-Token header, never the URL"),
    SourceEntry(
        "marketaux_news", N, "free tier (100 requests/day, 3 articles/request)",
        "https://www.marketaux.com/tos", PERS,
        f"'solely for your personal, non-commercial use' ({READ})",
        ("FIBOKI_MARKETAUX_API_KEY",), "900 s", "vendor search window", _RSS_PIT, _NEWS_MOD, True,
        ("marketaux_all",), "Token must travel in the URL; scrubbed from every error"),
    SourceEntry(
        "gdelt_doc", N, "free", "https://www.gdeltproject.org/about.html", OK,
        f"'unlimited and unrestricted use for any academic, commercial, or governmental use of "
        f"any kind without fee'; cite the GDELT Project with a link ({READ})",
        ("FIBOKI_GDELT_ENABLED",), "900 s per query, 1 request per 5 s",
        "API searches a rolling 3 months; at most 250 results per request",
        _RSS_PIT + "; seendate kept as vendor time", _NEWS_MOD, True,
        tuple(f"gdelt_{k}" for k in ("central_banks", "fx_majors", "us_dollar", "gold",
                                     "equity_indices", "macro_releases")),
        "Opt-in. Articles are other publishers' copyright: store headline, URL and metadata "
        "only, as the recorder does", ("https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",)),
    # -------------------------------------------------------------- calendar
    SourceEntry(
        "official_calendar", C, "free",
        "https://www.federalreserve.gov/disclaimer.htm", OK,
        "Dates published by public bodies (Fed, ECB, BoE, BoJ, BLS, ONS under OGL); see the "
        "fixture header for every URL",
        (), "committed fixture, refreshed by hand", "2024-01-01 to the declared end",
        "scheduled times only; no values", "fiboki.marketstate.calendar", True,
        notes="The only calendar used for blackouts. Everything below is compared with it."),
    SourceEntry(
        "finnhub_calendar", C, "PREMIUM: Finnhub marks /calendar/economic 'Premium Access Required'",
        "https://finnhub.io/terms-of-service", PERS,
        "Paid plan terms; not read this session (robots.txt)",
        ("FIBOKI_FINNHUB_API_KEY",), "on demand (weekly is enough)",
        "recent and upcoming; history is Enterprise-only",
        "dated snapshot files; scheduled times only; 'time' zone inferred UTC from the schema "
        "sample", "fiboki.data.providers.finnhub", True,
        notes="Secondary: written beside the official calendar and diffed, never merged",
        extra_urls=("https://finnhub.io/static/swagger.json",)),
    SourceEntry(
        "forexfactory_feed", C, "free", "https://www.forexfactory.com/notices", UNCL,
        "'The copying, republication or redistribution of FEED, in part or in whole, is "
        f"explicitly prohibited.' No licence for the nfs.faireconomy.media export found ({READ})",
        ("FIBOKI_FF_CALENDAR_OPT_IN",), "at most hourly; the feed is the current week",
        "current week only", "dated snapshot files; scheduled times only",
        "fiboki.data.providers.forexfactory_feed", True,
        notes="Opt-in, off by default, never committed; value is calendar_diff against the "
              "official calendar", extra_urls=("https://nfs.faireconomy.media/ff_calendar_thisweek.json",)),
    # ----------------------------------------------------------- positioning
    SourceEntry(
        "cftc_cot", P, "free", "https://www.cftc.gov/WebPolicy/index.htm", OK,
        f"'Government information at the CFTC website is in the public domain'; acknowledgement "
        f"requested ({READ})",
        (), "weekly (Friday 15:30 ET release)", "2006 onwards (TFF)",
        "available_at by the CFTC release rule, overrides and unresolved windows",
        "fiboki.data.providers.cftc_cot", True),
    SourceEntry(
        "oanda_books", P, "free with an OANDA account", "https://www.oanda.com/site/terms-of-use",
        PERS, "Served to the account holder; personal research only. Endpoint no longer in the "
        "current v20 reference; reported discontinued (third-party, 2024-09)",
        ("FIBOKI_OANDA_BOOKS_TOKEN", "FIBOKI_OANDA_BOOKS_ENVIRONMENT"), "hourly",
        "from our first successful poll", "observed_at; book 'time' kept as vendor time",
        "fiboki.data.positioning.oanda_books", True,
        notes="Availability not established: the first live poll decides",
        extra_urls=("https://developer.oanda.com/rest-live-v20/release-notes",
                    "https://dekalogblog.blogspot.com/2024/09/discontinuation-of-oandas-orderbook-and.html")),
    SourceEntry(
        "myfxbook_outlook", P, "free (100 requests/24 h; paid 2,880)", "https://www.myfxbook.com/api",
        PERS, f"API use accepts the Terms of use; site terms: 'Reproduction is prohibited by law' "
        f"({READ})",
        ("FIBOKI_MYFXBOOK_EMAIL", "FIBOKI_MYFXBOOK_PASSWORD"), "hourly (24 of 100 daily requests)",
        "from our first poll", "observed_at only (no vendor time)",
        "fiboki.data.positioning.myfxbook", True, extra_urls=("https://www.myfxbook.com/terms",)),
    # ----------------------------------------------------------------- macro
    SourceEntry(
        "alfred", M, "free (API key)", "https://fred.stlouisfed.org/docs/api/terms_of_use.html", OK,
        "FRED API terms; third-party 'Copyright' series need the owner's permission beyond "
        "personal use; the FRED notice is required",
        ("FIBOKI_FRED_API_KEY",), "on demand", "ALFRED vintage history", "VINTAGE",
        "fiboki.data.providers.alfred", True),
    SourceEntry(
        "fred_cross_asset_daily", M, "free (API key)",
        "https://fred.stlouisfed.org/docs/api/terms_of_use.html", PERS,
        "DGS2/DGS10/DTWEXBGS public domain (Board), DCOILWTICO public domain (EIA); VIXCLS is "
        "'Copyright, 2016, Chicago Board Options Exchange' so the pack as a whole is personal only",
        ("FIBOKI_FRED_API_KEY",), "daily", "series inception, vintages per ALFRED", "VINTAGE",
        "fiboki.data.providers.fred_pack", True,
        extra_urls=("https://fred.stlouisfed.org/series/VIXCLS",
                    "https://www.eia.gov/about/copyrights_reuse.php")),
    SourceEntry(
        "ecb_sdmx", M, "free",
        "https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.en.html",
        OK, "ESCB statistics: free reuse with citation, no modification",
        (), "daily", "series inception", "RELEASE_RULE (EXR) / FIRST_SEEN",
        "fiboki.data.providers.ecb_sdmx", True),
    SourceEntry(
        "boe_iadb", M, "free", "https://www.bankofengland.co.uk/legal", OK,
        "OGL v3.0 except third-party exchange-rate series; SONIA notice",
        (), "daily", "series inception", "RELEASE_RULE / FIRST_SEEN",
        "fiboki.data.providers.boe_iadb", True),
    SourceEntry(
        "ons", M, "free", "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        OK, "Open Government Licence v3.0", (), "per release", "archived versions",
        "SOURCE_TIMESTAMP / RELEASE_RULE", "fiboki.data.providers.ons", True),
    SourceEntry(
        "nyfed", M, "free", "https://www.newyorkfed.org/privacy/termsofuse", OK,
        "Terms of Use with the prescribed notice and non-affiliation disclaimer",
        (), "daily", "series inception", "RELEASE_RULE / SOURCE_TIMESTAMP",
        "fiboki.data.providers.nyfed", True),
    # ---------------------------------------------------------------- prices
    SourceEntry(
        "histdata", PR, "free", "https://www.histdata.com/f-a-q/", UNCL,
        "FAQ: 'free data ... no warranty'; no licence or redistribution terms stated "
        f"({READ})", (), "bulk download", "2000s onwards (M1, bid, EST without DST)",
        "historical bars; no availability semantics needed", "fiboki.data.providers.histdata",
        True, notes="Read from the V1 store; never redistributed"),
    SourceEntry(
        "dukascopy", PR, "free", "https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/",
        UNCL, "Site terms: 'You shall not use or attempt to use any scraper, robot, bot, spider "
        "... to access, acquire, copy, or monitor any portion of the WEBSITE' and it 'may not be "
        f"used to construct a database'. Whether datafeed.dukascopy.com is covered is unclear ({READ})",
        (), "not fetched", "tick history", "historical ticks",
        "fiboki.data.providers.dukascopy", True,
        notes="Only URL building and decoding are implemented; the HTTP fetch is a stub. Do not "
              "implement it until the terms question is answered in writing."),
    SourceEntry(
        "oanda_candles", PR, "free with an OANDA account", "https://www.oanda.com/site/terms-of-use",
        PERS, "Served to the account holder for their own use",
        (), "live recorder / on demand", "per v20 limits", "closed candles only",
        "fiboki.data.providers.oanda", True),
    # ------------------------------------------------ not implemented, by terms
    SourceEntry(
        "investing_com", C, "n/a", "https://www.investing.com/about-us/terms-and-conditions", NO,
        "'It is prohibited to use, store, reproduce, display, modify, transmit or distribute the "
        f"data contained in this website without the explicit prior written permission' ({READ})",
        (), "n/a", "n/a", "n/a", None, False),
    SourceEntry(
        "tradingview_undocumented", PR, "n/a", "https://www.tradingview.com/policies/", NO,
        "Prohibits non-display and automated use and third-party tools enabling it; 'We do not "
        f"permit commercial usage of any of our services or APIs' ({READ})",
        (), "n/a", "n/a", "n/a", None, False,
        notes="The Lightweight Charts LIBRARY is a separate, Apache-2.0 product and is unaffected"),
    SourceEntry(
        "myfxbook_scraping", P, "n/a", "https://www.myfxbook.com/terms", NO,
        f"Site material: 'Reproduction is prohibited by law' ({READ}). Use the official API "
        "(myfxbook_outlook) instead", (), "n/a", "n/a", "n/a", None, False),
    SourceEntry(
        "forexfactory_scraping", C, "n/a", "https://www.forexfactory.com/notices", NO,
        "Copying calendar schedules and specs is prohibited without written consent; access "
        f"other than by FEI's interface and instructions is not permitted ({READ})",
        (), "n/a", "n/a", "n/a", None, False),
    SourceEntry(
        "reuters", N, "paid licence (LSEG / Reuters Connect)",
        "https://developers.lseg.com/en/product/news/overview", NO,
        "'News Feeds are licensed primarily for programmatic internal end uses'; redistribution "
        f"needs separate licences ({READ}). Not free; forbidden without a licence",
        (), "n/a", "n/a", "n/a", None, False,
        extra_urls=("https://www.reutersagency.com/en/licensing/",)),
    SourceEntry(
        "associated_press", N, "paid licence (AP Media API)", "https://developer.ap.org/", NO,
        f"'Access all your licensed content in one place': customer content only ({READ}). "
        "Not free; forbidden without a licence", (), "n/a", "n/a", "n/a", None, False),
)


def source(name: str) -> SourceEntry:
    for e in SOURCE_REGISTRY:
        if e.name == name:
            return e
    raise KeyError(f"no source {name!r} in the registry")


def describe_sources(
    *, kind: SourceKind | str | None = None, implemented_only: bool = False,
) -> list[dict[str, Any]]:
    """Every registered source as a plain dict, for the API and the docs."""
    k = None if kind is None else SourceKind(kind)
    return [
        e.to_dict() for e in SOURCE_REGISTRY
        if (k is None or e.kind is k) and (e.implemented or not implemented_only)
    ]


def registry_markdown() -> str:
    """The registry as a Markdown table (the one in DATA_ARCHITECTURE.md §14.4)."""
    head = ("| Source | Kind | Cost | Terms status | Key env | Cadence | Point in time | Module |\n"
            "|---|---|---|---|---|---|---|---|")
    rows = [
        f"| `{e.name}` | {e.kind.value} | {e.cost} | [{e.terms_status.value}]({e.terms_url}) | "
        f"{', '.join(f'`{v}`' for v in e.key_env) or 'none'} | {e.cadence} | {e.point_in_time} | "
        f"{'`' + e.module + '`' if e.module else 'not implemented'} |"
        for e in SOURCE_REGISTRY
    ]
    return "\n".join([head, *rows])
