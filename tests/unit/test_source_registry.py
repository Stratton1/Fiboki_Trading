"""Every implemented data source is in the registry, with its terms.

Licences are a product requirement: a source module that exists without a
registry entry is a source nobody checked the terms of. These tests fail when
a provider, feed, calendar, positioning client or pack is added without one,
and when a source whose terms forbid it acquires a module.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from fiboki.data.news.sources import GDELT_QUERIES, OFFICIAL_FEEDS
from fiboki.data.providers import MACRO_DATASET_PACKS, MACRO_PROVIDERS, SECONDARY_CALENDARS
from fiboki.data.sources import (
    SOURCE_REGISTRY,
    SourceKind,
    TermsStatus,
    describe_sources,
    registry_markdown,
    source,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
DOC = Path(__file__).resolve().parents[2] / "docs" / "v2" / "DATA_ARCHITECTURE.md"

#: Modules under data/providers and data/positioning that are infrastructure,
#: not sources.
INFRASTRUCTURE = {
    "data/providers/__init__.py", "data/providers/base.py", "data/providers/macro_base.py",
    "data/providers/ratelimit.py", "data/providers/calendar_feed.py",
    "data/positioning/__init__.py", "data/positioning/store.py", "data/positioning/recorder.py",
}


def _names() -> set[str]:
    return {e.name for e in SOURCE_REGISTRY}


def test_names_are_unique_and_every_entry_is_well_formed():
    names = [e.name for e in SOURCE_REGISTRY]
    assert len(names) == len(set(names))
    for e in SOURCE_REGISTRY:
        assert isinstance(e.kind, SourceKind) and isinstance(e.terms_status, TermsStatus)
        assert e.terms_url.startswith("https://"), e.name
        assert e.terms_summary and e.cost and e.cadence and e.point_in_time, e.name


def test_every_implemented_source_has_an_importable_module_and_terms():
    for e in SOURCE_REGISTRY:
        if e.implemented:
            assert e.module, e.name
            importlib.import_module(e.module)
            assert e.terms_status is not TermsStatus.FORBIDDEN, e.name


def test_forbidden_sources_are_never_implemented():
    forbidden = [e for e in SOURCE_REGISTRY if e.terms_status is TermsStatus.FORBIDDEN]
    assert {e.name for e in forbidden} >= {"investing_com", "tradingview_undocumented",
                                            "myfxbook_scraping", "reuters", "associated_press"}
    for e in forbidden:
        assert not e.implemented and e.module is None, e.name


def test_every_source_module_in_the_tree_is_registered():
    registered = {e.module for e in SOURCE_REGISTRY if e.module}
    for folder in ("data/providers", "data/positioning"):
        for path in sorted((SRC / folder).glob("*.py")):
            rel = f"{folder}/{path.name}"
            if rel in INFRASTRUCTURE:
                continue
            module = "fiboki." + rel[:-3].replace("/", ".")
            assert module in registered, f"{module} has no source registry entry (terms unchecked)"


def test_every_feed_client_provider_calendar_and_pack_is_registered():
    feed_keys = {k for e in SOURCE_REGISTRY for k in e.feed_keys}
    assert {f.key for f in OFFICIAL_FEEDS} <= feed_keys
    assert {f"gdelt_{q.key}" for q in GDELT_QUERIES} <= feed_keys
    assert {"finnhub_forex", "finnhub_general", "marketaux_all"} <= feed_keys
    names = _names()
    assert set(MACRO_PROVIDERS) <= names
    assert set(MACRO_DATASET_PACKS) <= names
    assert set(SECONDARY_CALENDARS) <= names
    assert {"oanda_books", "myfxbook_outlook", "histdata", "dukascopy", "oanda_candles",
            "official_calendar"} <= names


def test_opt_in_sources_declare_their_switch():
    assert source("forexfactory_feed").terms_status is TermsStatus.OPT_IN_UNCLEAR
    assert source("forexfactory_feed").key_env == ("FIBOKI_FF_CALENDAR_OPT_IN",)
    assert source("myfxbook_outlook").terms_status is TermsStatus.PERSONAL_ONLY
    assert source("gdelt_doc").key_env == ("FIBOKI_GDELT_ENABLED",)


def test_describe_sources_filters_and_serialises():
    all_ = describe_sources()
    assert len(all_) == len(SOURCE_REGISTRY)
    assert all(isinstance(d["terms_status"], str) and isinstance(d["kind"], str) for d in all_)
    pos = describe_sources(kind="positioning", implemented_only=True)
    assert {d["name"] for d in pos} == {"cftc_cot", "oanda_books", "myfxbook_outlook"}


def test_the_documented_table_is_the_registry():
    text = DOC.read_text(encoding="utf-8")
    assert registry_markdown() in text, (
        "DATA_ARCHITECTURE.md §14.4 is out of date; paste fiboki.data.sources.registry_markdown()"
    )
