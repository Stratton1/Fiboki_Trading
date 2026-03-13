"""IG broker execution adapter — demo account only.

Translates Fiboki execution commands into IG REST API calls via IGClient.
All operations are routed through the demo API; production is hard-blocked.
"""

import logging

from fibokei.core.instruments import get_ig_epic, get_symbol_by_epic
from fibokei.execution.adapter import ExecutionAdapter
from fibokei.execution.ig_client import IGClient, IGClientError

logger = logging.getLogger(__name__)


class IGExecutionAdapter(ExecutionAdapter):
    """IG broker adapter — demo account execution."""

    def __init__(self, client: IGClient | None = None) -> None:
        self._client = client or IGClient()
        self._authenticated = False

    def _ensure_auth(self) -> None:
        if not self._authenticated:
            self._client.authenticate()
            self._authenticated = True

    def place_order(self, order: dict) -> dict:
        """Place a market order on IG demo.

        Expected order keys:
          - instrument: Fiboki symbol (e.g. "EURUSD")
          - direction: "BUY" or "SELL"
          - size: float
          - currency: str (default "USD")
          - stop_distance: float | None
          - limit_distance: float | None
        """
        try:
            self._ensure_auth()
        except IGClientError as e:
            logger.error("IG auth failed: %s", e)
            return {"status": "rejected", "reason": str(e), "error_code": getattr(e, "error_code", "")}

        symbol = order.get("instrument", "")
        try:
            epic = get_ig_epic(symbol)
        except KeyError:
            return {"status": "rejected", "reason": f"No IG epic for {symbol}"}

        direction = order.get("direction", "BUY").upper()
        size = order.get("size", 1.0)

        params: dict = {
            "epic": epic,
            "direction": direction,
            "size": str(size),
            "orderType": "MARKET",
            "currencyCode": order.get("currency", "USD"),
            "guaranteedStop": False,
            "forceOpen": True,
        }

        if order.get("stop_distance") is not None:
            params["stopDistance"] = order["stop_distance"]
        if order.get("limit_distance") is not None:
            params["limitDistance"] = order["limit_distance"]

        import time

        requested_price = order.get("requested_price")
        t_start = time.monotonic()

        try:
            result = self._client.open_position(params)
            fill_latency_ms = int((time.monotonic() - t_start) * 1000)
            deal_ref = result.get("dealReference", "")
            if deal_ref:
                confirmation = self._client.get_deal_confirmation(deal_ref)
                filled_price = confirmation.get("level")
                slippage_pips = None
                if filled_price is not None and requested_price is not None:
                    slippage_pips = round(abs(filled_price - requested_price) * 10000, 2)
                return {
                    "status": confirmation.get("dealStatus", "UNKNOWN"),
                    "deal_id": confirmation.get("dealId", ""),
                    "deal_reference": deal_ref,
                    "direction": direction,
                    "size": size,
                    "epic": epic,
                    "level": filled_price,
                    "reason": confirmation.get("reason", ""),
                    "requested_price": requested_price,
                    "filled_price": filled_price,
                    "slippage_pips": slippage_pips,
                    "fill_latency_ms": fill_latency_ms,
                }
            return {"status": "UNKNOWN", "deal_reference": deal_ref, "raw": result}
        except IGClientError as e:
            logger.error("IG place_order failed: %s", e)
            return {"status": "rejected", "reason": str(e), "error_code": e.error_code}

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a working order by deal ID."""
        try:
            self._ensure_auth()
            self._client.delete_working_order(order_id)
            return True
        except IGClientError as e:
            logger.error("IG cancel_order failed for %s: %s", order_id, e)
            return False

    def modify_order(self, order_id: str, changes: dict) -> dict:
        """Modify a working order (level, stop, limit)."""
        try:
            self._ensure_auth()
            result = self._client.update_working_order(order_id, changes)
            return {"status": "modified", "order_id": order_id, "raw": result}
        except IGClientError as e:
            logger.error("IG modify_order failed for %s: %s", order_id, e)
            return {"status": "failed", "reason": str(e)}

    def get_positions(self) -> list[dict]:
        """Return all open positions, mapped to Fiboki symbols."""
        try:
            self._ensure_auth()
            raw_positions = self._client.get_positions()
        except IGClientError as e:
            logger.error("IG get_positions failed: %s", e)
            return []

        positions = []
        for pos in raw_positions:
            market = pos.get("market", {})
            position = pos.get("position", {})
            epic = market.get("epic", "")
            try:
                symbol = get_symbol_by_epic(epic)
            except KeyError:
                symbol = epic  # Pass through unknown epics

            positions.append({
                "deal_id": position.get("dealId", ""),
                "instrument": symbol,
                "epic": epic,
                "direction": position.get("direction", ""),
                "size": position.get("size", 0),
                "open_level": position.get("openLevel", 0),
                "current_level": market.get("bid", 0),
                "stop_level": position.get("stopLevel"),
                "limit_level": position.get("limitLevel"),
                "pnl": position.get("upl", 0),
                "currency": position.get("currency", ""),
                "created_date": position.get("createdDateUTC", ""),
            })
        return positions

    def get_account_info(self) -> dict:
        """Return IG demo account info."""
        try:
            self._ensure_auth()
            acct = self._client.get_account_info()
            balance = acct.get("balance", {})
            return {
                "account_id": acct.get("accountId", ""),
                "account_name": acct.get("accountName", ""),
                "balance": balance.get("balance", 0),
                "equity": balance.get("balance", 0) + balance.get("profitLoss", 0),
                "deposit": balance.get("deposit", 0),
                "available": balance.get("available", 0),
                "pnl": balance.get("profitLoss", 0),
                "currency": acct.get("currency", ""),
            }
        except IGClientError as e:
            logger.error("IG get_account_info failed: %s", e)
            return {"error": str(e)}

    def close_position(self, position_id: str) -> dict:
        """Close an open position by deal ID.

        Requires knowing the direction and size — fetches from open positions.
        """
        try:
            self._ensure_auth()
            # Find the position to get direction and size
            positions = self._client.get_positions()
            target = None
            for pos in positions:
                if pos.get("position", {}).get("dealId") == position_id:
                    target = pos
                    break

            if not target:
                return {"status": "failed", "reason": f"Position {position_id} not found"}

            position = target["position"]
            close_direction = "SELL" if position["direction"] == "BUY" else "BUY"
            size = position.get("size", position.get("dealSize", 0))

            result = self._client.close_position(position_id, close_direction, float(size))
            deal_ref = result.get("dealReference", "")
            if deal_ref:
                confirmation = self._client.get_deal_confirmation(deal_ref)
                return {
                    "status": confirmation.get("dealStatus", "UNKNOWN"),
                    "deal_id": confirmation.get("dealId", ""),
                    "reason": confirmation.get("reason", ""),
                }
            return {"status": "UNKNOWN", "raw": result}
        except IGClientError as e:
            logger.error("IG close_position failed for %s: %s", position_id, e)
            return {"status": "failed", "reason": str(e)}

    def partial_close(self, position_id: str, pct: float) -> dict:
        """Partially close a position by percentage.

        IG doesn't have a native partial close — we close a fraction of the size.
        """
        try:
            self._ensure_auth()
            positions = self._client.get_positions()
            target = None
            for pos in positions:
                if pos.get("position", {}).get("dealId") == position_id:
                    target = pos
                    break

            if not target:
                return {"status": "failed", "reason": f"Position {position_id} not found"}

            position = target["position"]
            total_size = float(position.get("size", position.get("dealSize", 0)))
            close_size = round(total_size * (pct / 100.0), 2)
            if close_size <= 0:
                return {"status": "failed", "reason": "Calculated close size is 0"}

            close_direction = "SELL" if position["direction"] == "BUY" else "BUY"
            result = self._client.close_position(position_id, close_direction, close_size)
            deal_ref = result.get("dealReference", "")
            if deal_ref:
                confirmation = self._client.get_deal_confirmation(deal_ref)
                return {
                    "status": confirmation.get("dealStatus", "UNKNOWN"),
                    "deal_id": confirmation.get("dealId", ""),
                    "closed_size": close_size,
                    "remaining_size": total_size - close_size,
                }
            return {"status": "UNKNOWN", "raw": result}
        except IGClientError as e:
            logger.error("IG partial_close failed for %s: %s", position_id, e)
            return {"status": "failed", "reason": str(e)}

    def update_stop_limit(self, deal_id: str, stop_level: float | None = None,
                          limit_level: float | None = None) -> dict:
        """Update stop/limit on an open position."""
        try:
            self._ensure_auth()
            result = self._client.update_position(deal_id, stop_level, limit_level)
            return {"status": "updated", "deal_id": deal_id, "raw": result}
        except IGClientError as e:
            logger.error("IG update_stop_limit failed for %s: %s", deal_id, e)
            return {"status": "failed", "reason": str(e)}
