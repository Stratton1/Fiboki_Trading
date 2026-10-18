"""SEARCH: real entities for the palette and the command line, exact match first.

The palette used to offer "Open trade EURUSD", "Open strategy EURUSD" and so on
for whatever was typed, and ranked a fuzzy match on a page title above the
instrument itself (EURUSD scored Exposure first). It offered entities that did
not exist. This endpoint returns only things that exist, each with its route.

Ranking (``tier``, lower first), case-insensitive, on the id and the label:

  0  exact match
  1  prefix of the id or label
  2  prefix of a word in the label
  3  substring

Ties break on kind (instrument, strategy, session, candidate, run, trade,
incident), then on the id, so the order is total and deterministic. The
workstation merges its own page and function registry with these hits by the
same tiers (lib/search.ts), so a screen named "Exposure" can never outrank the
instrument EURUSD for the query "EURUSD".
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api import links
from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.models import Page, SourceNote
from fiboki.core import instruments as instrument_registry

router = APIRouter(prefix="/api/search", tags=["search"])

__all__ = ["KIND_ORDER", "SearchHit", "rank_hits", "router", "tier_of"]

KIND_ORDER = ("instrument", "strategy", "session", "candidate", "run", "trade", "incident")
_WORD = re.compile(r"[\s_\-/.:]+")


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = Field(pattern="^(instrument|strategy|session|candidate|run|trade|incident)$")
    id: str
    label: str
    detail: str
    href: str
    tier: int = Field(ge=0, le=3)


def tier_of(query: str, ident: str, label: str) -> int | None:
    q = query.strip().lower()
    if not q:
        return None
    i, lab = ident.lower(), label.lower()
    if q in (i, lab):
        return 0
    if i.startswith(q) or lab.startswith(q):
        return 1
    if any(word.startswith(q) for word in _WORD.split(lab) + _WORD.split(i) if word):
        return 2
    if q in i or q in lab:
        return 3
    return None


def rank_hits(hits: list[SearchHit]) -> list[SearchHit]:
    order = {k: n for n, k in enumerate(KIND_ORDER)}
    return sorted(hits, key=lambda h: (h.tier, order[h.kind], h.id))


def _runs(settings: Any, q: str, limit: int) -> list[tuple[str, str, str]]:
    path = getattr(settings, "experiment_db", None)
    if path is None:
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT a.id, a.strategy_id, json_extract(a.payload_json, '$.timeframe') "
            "FROM research_artefact a WHERE a.collection = 'backtests' "
            "AND (lower(a.id) LIKE :q OR lower(a.strategy_id) LIKE :q) "
            "ORDER BY a.created_at DESC LIMIT :limit",
            {"q": f"%{q.lower()}%", "limit": limit},
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [(str(r[0]), str(r[1] or ""), str(r[2] or "")) for r in rows]


@router.get("", response_model=Page[SearchHit])
def search(
    platform: PlatformDep,
    settings: SettingsDep,
    q: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(20, ge=1, le=100),
) -> Page[SearchHit]:
    """Entities matching ``q``, best first. Only entities that exist."""
    hits: list[SearchHit] = []

    def add(kind: str, ident: str, label: str, detail: str, href: str) -> None:
        tier = tier_of(q, ident, label)
        if tier is not None:
            hits.append(SearchHit(kind=kind, id=ident, label=label, detail=detail, href=href, tier=tier))

    for sym in instrument_registry.all_symbols():
        try:
            inst = instrument_registry.get(sym)
            detail = f"{inst.asset_class.value} · {inst.base}/{inst.quote} · {inst.venue}"
        except Exception:
            detail = "instrument"
        add("instrument", sym, sym, detail, links.chart(sym))

    names: dict[str, str] = {}
    for doc in platform.strategy_documents():
        names[doc.strategy_id] = getattr(doc, "name", doc.strategy_id)
    for sid in sorted(set(platform.strategy_ids) | set(names)):
        add("strategy", sid, names.get(sid, sid), "strategy · tearsheet and runs", links.strategy(sid))

    try:
        statuses = list(platform.lifecycle.statuses())
    except Exception:
        statuses = []
    for status_obj in statuses:
        h = status_obj.strategy_content_hash
        add(
            "candidate",
            h,
            f"{status_obj.strategy_id} ({status_obj.lifecycle.value})",
            f"lifecycle · {h[:12]}",
            links.lifecycle(h),
        )

    journal = platform.journal
    if journal is not None:
        for session in journal.sessions:
            summary = platform.session_summary(session.session_id) or {}
            label = str(summary.get("wiring_version") or session.session_id)
            add(
                "session",
                session.session_id,
                label,
                f"paper bot · {session.strategy_id} · {session.instrument} {session.timeframe}",
                links.session(session.session_id),
            )

    trades, _ = platform.trades(limit=10_000)
    for trade in trades:
        if tier_of(q, trade.trade_id, trade.trade_id) is None:
            continue
        add(
            "trade",
            trade.trade_id,
            trade.trade_id,
            f"{trade.provenance.value} · {trade.strategy_id} · {trade.instrument}",
            links.journal_trade(trade.trade_id),
        )

    for run_id, strategy_id, timeframe in _runs(settings, q.strip(), limit):
        add("run", run_id, run_id, f"backtest · {strategy_id} {timeframe}".strip(), links.research_run(run_id))

    ranked = rank_hits(hits)
    return Page(
        items=ranked[:limit],
        total=len(ranked),
        limit=limit,
        source=SourceNote(
            kind="live",
            detail="Instrument registry, strategy registry, lifecycle store, paper sessions, "
            "the trade record and the research ledger, matched now.",
        ),
    )
