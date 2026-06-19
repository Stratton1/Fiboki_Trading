"""Audit Fiboki's instrument→IG-epic mapping against a live IG account.

For each instrument, checks whether the catalogue's static epic is actually
priceable/tradeable on *this* account (the Z5ZAV "Failed to retrieve price
information" problem). If not, searches IG for a tradeable alternative and
reports the resolved epic. Read-only — never places an order.

Run on a service with IG credentials (the worker, or the API if IG read creds
are configured). The output drives a verified repoint of ``core/instruments.py``
and lets IG's tradable set define the research/test universe.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TRADEABLE = ("TRADEABLE", "EDITS_ONLY")


def _probe_epic(client, epic: str) -> str | None:
    """Return the epic's marketStatus if readable on this account, else None."""
    if not epic:
        return None
    try:
        market = client.get_market(epic)
    except Exception:
        return None
    snapshot = market.get("snapshot") or {}
    return snapshot.get("marketStatus")


def _search_tradeable(client, inst, bad_epic: str) -> tuple[str | None, str | None]:
    """Find a tradeable epic for ``inst`` that differs from ``bad_epic``."""
    try:
        terms = [inst.name.split("/")[0].strip(), inst.symbol]
    except Exception:
        terms = [inst.symbol]
    for term in terms:
        try:
            markets = client.search_markets(term)
        except Exception:
            continue
        for m in markets:
            epic = m.get("epic", "")
            if not epic or epic == bad_epic:
                continue
            if m.get("marketStatus") not in (None, *_TRADEABLE):
                continue
            status = _probe_epic(client, epic)
            if status in _TRADEABLE:
                return epic, status
    return None, None


def audit_instrument_epics(client, symbols: list[str] | None = None) -> list[dict]:
    """Audit each IG-mapped instrument's epic against the live account.

    Returns one dict per instrument:
      symbol, name, static_epic, status (ok|remapped|unavailable),
      resolved_epic, market_status, detail.
    """
    from fibokei.core.instruments import get_ig_supported_instruments

    insts = get_ig_supported_instruments()
    if symbols:
        wanted = {s.upper() for s in symbols}
        insts = [i for i in insts if i.symbol.upper() in wanted]

    results: list[dict] = []
    for inst in insts:
        static_epic = inst.ig_epic
        entry = {
            "symbol": inst.symbol,
            "name": inst.name,
            "static_epic": static_epic,
            "status": "ok",
            "resolved_epic": static_epic,
            "market_status": None,
            "detail": "",
        }
        ms = _probe_epic(client, static_epic)
        if ms in _TRADEABLE:
            entry["market_status"] = ms
            results.append(entry)
            continue
        resolved, rms = _search_tradeable(client, inst, bad_epic=static_epic)
        if resolved:
            entry.update(
                status="remapped", resolved_epic=resolved, market_status=rms,
                detail=f"static epic not priceable on this account; use {resolved}",
            )
        else:
            entry.update(
                status="unavailable", resolved_epic=None,
                detail="no tradeable epic found for this account",
            )
        results.append(entry)
    return results


def summarize_audit(results: list[dict]) -> dict:
    """Counts + an epic-override map (symbol→resolved_epic) for remapped rows."""
    overrides = {
        r["symbol"]: r["resolved_epic"]
        for r in results
        if r["status"] == "remapped" and r["resolved_epic"]
    }
    return {
        "total": len(results),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "remapped": sum(1 for r in results if r["status"] == "remapped"),
        "unavailable": sum(1 for r in results if r["status"] == "unavailable"),
        "epic_overrides": overrides,
        "tradable_symbols": [
            r["symbol"] for r in results if r["status"] in ("ok", "remapped")
        ],
    }
