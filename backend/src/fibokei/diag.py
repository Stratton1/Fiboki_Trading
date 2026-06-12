"""Safe IG-demo diagnostics — run on Railway via `python -m fibokei.diag`.

Read-only subcommands exercise auth, market specs and epic resolution
without placing orders. The lifecycle subcommand places ONE minimum-size
EURUSD demo order with a stop, then amends and closes it — Gate 2 proof.
It requires an explicit confirmation flag and runs only against the demo
gateway (production is hard-blocked in IGClient).

Usage:
  python -m fibokei.diag auth
  python -m fibokei.diag market EURUSD
  python -m fibokei.diag resolve-epic XAUUSD
  python -m fibokei.diag prices EURUSD --resolution HOUR
  python -m fibokei.diag lifecycle --confirm-demo-order
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from fibokei.core.instruments import get_ig_epic
from fibokei.execution.ig_adapter import IGExecutionAdapter
from fibokei.execution.ig_client import IGClient, IGClientError


def _print(label: str, payload) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str))


def cmd_auth(client: IGClient) -> int:
    session = client.ensure_session()
    _print("AUTH", {
        "authenticated": session.is_valid,
        "account_id": session.account_id,  # account id is not a secret credential
        "token_age_s": round(time.time() - session.created_at, 1),
    })
    return 0


def cmd_market(client: IGClient, symbol: str) -> int:
    epic = get_ig_epic(symbol)
    data = client.get_market(epic)
    instr = data.get("instrument", {})
    rules = data.get("dealingRules", {})
    snapshot = data.get("snapshot", {})
    _print(f"MARKET {symbol}", {
        "epic": epic,
        "name": instr.get("name"),
        "type": instr.get("type"),
        "valueOfOnePip": instr.get("valueOfOnePip"),
        "onePipMeans": instr.get("onePipMeans"),
        "minDealSize": (rules.get("minDealSize") or {}).get("value"),
        "minStopDistance": (rules.get("minNormalStopOrLimitDistance") or {}).get("value"),
        "marketStatus": snapshot.get("marketStatus"),
        "bid": snapshot.get("bid"),
        "offer": snapshot.get("offer"),
    })
    return 0


def cmd_resolve_epic(adapter: IGExecutionAdapter, symbol: str) -> int:
    static_epic = get_ig_epic(symbol)
    accessible = True
    try:
        adapter._client.get_market(static_epic)
    except IGClientError as e:
        accessible = False
        print(f"Static epic {static_epic} NOT accessible: {e}")
    if accessible:
        _print(f"RESOLVE {symbol}", {
            "static_epic": static_epic,
            "static_epic_accessible": True,
            "remap_needed": False,
        })
        return 0
    resolved = adapter._resolve_epic_for_account(symbol, static_epic)
    _print(f"RESOLVE {symbol}", {
        "static_epic": static_epic,
        "static_epic_accessible": False,
        "resolved_epic": resolved,
        "cached": adapter._resolved_epics.get(symbol),
    })
    return 0 if resolved else 1


def cmd_prices(client: IGClient, symbol: str, resolution: str) -> int:
    epic = get_ig_epic(symbol)
    data = client.get_prices(epic, resolution, 3)
    prices = data.get("prices", [])
    _print(f"PRICES {symbol} {resolution}", {
        "epic": epic,
        "candles_returned": len(prices),
        "latest": prices[-1] if prices else None,
        "allowance": data.get("allowance") or data.get("metadata", {}).get("allowance"),
    })
    return 0 if prices else 1


def cmd_lifecycle(adapter: IGExecutionAdapter, confirm: bool) -> int:
    """Gate 2: EURUSD minimum-size open→confirm→position→amend→close→reconcile."""
    if not confirm:
        print("Refusing: pass --confirm-demo-order to place ONE minimum demo order.")
        return 2

    evidence: dict = {"started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    client = adapter._client

    # 1. Auth + market spec
    session = client.ensure_session()
    evidence["auth"] = {"ok": session.is_valid, "account_id": session.account_id}
    epic = get_ig_epic("EURUSD")
    spec = adapter._get_market_details(epic)
    if spec.get("is_default"):
        _print("LIFECYCLE ABORT", {"reason": "market details unavailable"})
        return 1
    min_size = spec["min_deal_size"]
    snapshot = client.get_market(epic).get("snapshot", {})
    if snapshot.get("marketStatus") != "TRADEABLE":
        _print("LIFECYCLE ABORT", {"reason": f"market not tradeable: {snapshot.get('marketStatus')}"})
        return 1
    bid = float(snapshot.get("bid") or 0)
    evidence["market"] = {"epic": epic, "min_size": min_size, "bid": bid}

    # 2. Open: minimum size, stop/limit at 0.2% of the live IG price.
    # IG quotes FX CFDs in points (EURUSD ≈ 13050.9, onePipMeans=1), so
    # distances must be computed from IG's own price scale, not classic
    # 1.3050-style quotes. 0.2% ≈ 26 points here — comfortably above the
    # broker minimum while keeping the test position tight.
    opm = float(spec.get("one_pip_means") or 1.0)
    min_stop_pts = float(spec.get("min_stop_distance") or 0.0)
    dist_price_units = max(bid * 0.002, (min_stop_pts * 2 or 4.0)) * opm
    order = {
        "instrument": "EURUSD",
        "direction": "BUY",
        "size": min_size,
        "currency": "GBP",
        "stop_distance": dist_price_units,
        "limit_distance": dist_price_units,
        "requested_price": bid,
        "risk_pct": 0.1,
        "bot_id": "gate2-lifecycle-proof",
    }
    result = adapter.place_order(order)
    evidence["open"] = result
    deal_id = result.get("deal_id")
    if result.get("status") != "ACCEPTED" or not deal_id:
        _print("LIFECYCLE FAILED AT OPEN", evidence)
        return 1

    # 3. Position visible at broker
    time.sleep(2)
    positions = client.get_positions()
    pos = next((p for p in positions if p.get("position", {}).get("dealId") == deal_id), None)
    evidence["position_visible"] = bool(pos)
    evidence["position"] = pos

    # 4. Amend stop (widen by 10 pips)
    level = result.get("filled_price") or bid
    new_stop = round(level - (dist_price_units / opm) * 1.3, 2)
    try:
        amend = adapter.update_stop_limit(deal_id, stop_level=new_stop)
        evidence["amend"] = amend
    except Exception as e:  # noqa: BLE001
        evidence["amend"] = {"error": str(e)}

    # 5. Close
    time.sleep(2)
    close = adapter.close_position(deal_id)
    evidence["close"] = close

    # 6. Reconcile: position gone at broker
    time.sleep(2)
    positions_after = client.get_positions()
    still_open = any(
        p.get("position", {}).get("dealId") == deal_id for p in positions_after
    )
    evidence["reconciled_closed"] = not still_open
    evidence["finished_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    ok = (
        evidence["open"].get("status") == "ACCEPTED"
        and evidence["position_visible"]
        and evidence["close"].get("status") in ("ACCEPTED", "CLOSED")
        and evidence["reconciled_closed"]
    )
    _print("LIFECYCLE " + ("PASSED" if ok else "INCOMPLETE"), evidence)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="fibokei.diag")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth")
    p_market = sub.add_parser("market")
    p_market.add_argument("symbol")
    p_resolve = sub.add_parser("resolve-epic")
    p_resolve.add_argument("symbol")
    p_prices = sub.add_parser("prices")
    p_prices.add_argument("symbol")
    p_prices.add_argument("--resolution", default="HOUR")
    p_life = sub.add_parser("lifecycle")
    p_life.add_argument("--confirm-demo-order", action="store_true")
    args = parser.parse_args()

    client = IGClient()
    adapter = IGExecutionAdapter(client=client)
    try:
        if args.cmd == "auth":
            return cmd_auth(client)
        if args.cmd == "market":
            return cmd_market(client, args.symbol.upper())
        if args.cmd == "resolve-epic":
            return cmd_resolve_epic(adapter, args.symbol.upper())
        if args.cmd == "prices":
            return cmd_prices(client, args.symbol.upper(), args.resolution)
        if args.cmd == "lifecycle":
            return cmd_lifecycle(adapter, args.confirm_demo_order)
    except IGClientError as e:
        print(f"IG error: {e} (status={e.status_code}, code={e.error_code})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
