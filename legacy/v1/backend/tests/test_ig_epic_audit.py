"""Tests for the IG epic audit (maps instruments → priceable epics).

Fake IG client so the audit runs offline: Gold's CFP epic is priceable;
GBPUSD's CFD epic is not but a MINI epic is found; HK50 has no tradeable epic.
"""

from fibokei.execution.ig_adapter import _pick_dealing_currency
from fibokei.execution.ig_epic_audit import (
    audit_instrument_epics,
    summarize_audit,
)


def test_pick_dealing_currency():
    # Default flagged → use it.
    assert _pick_dealing_currency(
        [{"code": "GBP", "isDefault": False}, {"code": "USD", "isDefault": True}]
    ) == "USD"
    # No default → first.
    assert _pick_dealing_currency([{"code": "EUR"}, {"code": "USD"}]) == "EUR"
    assert _pick_dealing_currency([]) is None
    assert _pick_dealing_currency(None) is None


class FakeIGClient:
    PRICEABLE = {"CS.D.CFPGOLD.CFP.IP", "CS.D.GBPUSD.MINI.IP"}

    def get_market(self, epic):
        if epic in self.PRICEABLE:
            return {"snapshot": {"marketStatus": "TRADEABLE"}}
        raise RuntimeError("Failed to retrieve price information for this currency")

    def search_markets(self, term):
        if "pound" in term.lower() or "gbp" in term.lower():
            return [{"epic": "CS.D.GBPUSD.MINI.IP", "marketStatus": "TRADEABLE"}]
        return []


def test_audit_classifies_ok_remapped_unavailable():
    client = FakeIGClient()
    results = audit_instrument_epics(client, symbols=["XAUUSD", "GBPUSD", "HK50"], delay=0)
    by_symbol = {r["symbol"]: r for r in results}

    assert by_symbol["XAUUSD"]["status"] == "ok"
    assert by_symbol["XAUUSD"]["resolved_epic"] == "CS.D.CFPGOLD.CFP.IP"

    assert by_symbol["GBPUSD"]["status"] == "remapped"
    assert by_symbol["GBPUSD"]["resolved_epic"] == "CS.D.GBPUSD.MINI.IP"

    assert by_symbol["HK50"]["status"] == "unavailable"
    assert by_symbol["HK50"]["resolved_epic"] is None


def test_summarize_audit_overrides_and_universe():
    client = FakeIGClient()
    results = audit_instrument_epics(client, symbols=["XAUUSD", "GBPUSD", "HK50"], delay=0)
    summary = summarize_audit(results)

    assert summary["ok"] == 1
    assert summary["remapped"] == 1
    assert summary["unavailable"] == 1
    assert summary["epic_overrides"] == {"GBPUSD": "CS.D.GBPUSD.MINI.IP"}
    assert set(summary["tradable_symbols"]) == {"XAUUSD", "GBPUSD"}
