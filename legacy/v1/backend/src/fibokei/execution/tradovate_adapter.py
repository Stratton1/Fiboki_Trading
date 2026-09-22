"""Tradovate ExecutionAdapter — Phase 1 demo-first scaffold.

Implements the ``ExecutionAdapter`` interface so the multi-broker router
can dispatch orders to a Tradovate demo account in parallel with IG demo.
Like the IG adapter, this never trades unsupported instruments, never
silently approximates futures sizing, and never reaches the live API
unless every safety gate is explicitly opened.

What's a Phase 1 stub vs. shipped:

* Auth, request envelope, error handling — shipped.
* Place/cancel/close — shipped against the placeholder endpoints with
  ``TODO_VERIFY`` markers.
* Real Tradovate ``/contract/find`` for front-month resolution — stub.
* Streaming socket integration — out of scope.
"""

from __future__ import annotations

import logging
import time

from fibokei.execution.adapter import ExecutionAdapter
from fibokei.execution.broker_symbols import (
    TradovateContract,
    TradovateContractResolver,
    UnsupportedSymbol,
    get_default_tradovate_resolver,
)
from fibokei.execution.tradovate_client import (
    TradovateClient,
    TradovateClientError,
    TradovateOrderResult,
)

logger = logging.getLogger(__name__)


def _direction_to_action(direction: str) -> str:
    """Map Fiboki/IG-style direction to Tradovate ``action`` field.

    Tradovate uses literal ``Buy`` / ``Sell`` rather than IG's ``BUY`` /
    ``SELL``; the case matters for the API.
    """
    return "Buy" if str(direction or "").upper() == "BUY" else "Sell"


def _opposite_action(action: str) -> str:
    return "Sell" if action == "Buy" else "Buy"


class TradovateExecutionAdapter(ExecutionAdapter):
    """Tradovate broker adapter — demo by default, live hard-blocked."""

    def __init__(
        self,
        client: TradovateClient | None = None,
        resolver: TradovateContractResolver | None = None,
        account_id: int | None = None,
        account_spec: str | None = None,
    ) -> None:
        self._client = client or TradovateClient()
        self._resolver = resolver or get_default_tradovate_resolver()
        self._account_id = account_id or 0
        # ``accountSpec`` is the account *name* (e.g. "DEMO12345") and is a
        # required field on /order/placeOrder per the Tradovate tutorial.
        # Without both id + spec the API responds with a Violation.
        self._account_spec = account_spec or ""
        self._authenticated = False
        # Local cache of (deal_id → contract) so close_position can construct
        # the reverse-side payload without re-querying Tradovate.
        self._open_orders: dict[str, dict] = {}

    # ── Internal helpers ─────────────────────────────────────────────

    def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        self._client.authenticate()
        if not self._account_id or not self._account_spec:
            try:
                accounts = self._client.list_accounts()
                if accounts:
                    if not self._account_id:
                        self._account_id = accounts[0].account_id
                    if not self._account_spec:
                        # accountSpec is the account NAME (required by
                        # /order/placeOrder per Tradovate tutorial).
                        self._account_spec = accounts[0].name
            except TradovateClientError:
                # Leave account_id/spec unset — place_order will surface a
                # clean rejection rather than crashing.
                pass
        self._authenticated = True

    def _build_order_payload(
        self,
        contract: TradovateContract,
        action: str,
        size: int,
    ) -> dict:
        """Construct the Tradovate ``/order/placeOrder`` body.

        Required fields per the official tutorial repo
        (`tradovate/example-api-js`, EX-4a-Place-An-Order):

          * ``accountId`` — integer account id (from /account/list)
          * ``accountSpec`` — account NAME string (also from /account/list).
            Without BOTH id and spec, the API returns a Violation.
          * ``action`` — "Buy" or "Sell" (case-sensitive)
          * ``symbol`` — Tradovate contract symbol (e.g. "ESM6")
          * ``orderQty`` — integer contract count
          * ``orderType`` — "Market" / "Limit" / "Stop" etc.
          * ``isAutomated`` — MUST be ``true`` for bot-placed orders. The
            tutorial explicitly warns this is an exchange-policy
            requirement.

        Phase 1 sends a market order with no bracket orders attached.
        Stop-loss / take-profit are tracked client-side via the bot's
        Position object; broker-side OCO/OSO orders arrive in a later phase.
        """
        return {
            "accountId": int(self._account_id),
            "accountSpec": self._account_spec,
            "action": action,
            "symbol": contract.contract_symbol,
            "orderQty": int(size),
            "orderType": "Market",
            "isAutomated": True,
        }

    # ── ExecutionAdapter interface ──────────────────────────────────

    def place_order(self, order: dict) -> dict:
        """Place a market order on Tradovate.

        Expected order keys (broker-neutral, supplied by router):
          - ``instrument``: Fiboki symbol (e.g. "US500")
          - ``direction``: "BUY" or "SELL"
          - ``size``: integer contracts (sizing already rounded by router)
          - ``requested_price``: optional, used for slippage attribution
          - ``stop_distance``: optional, ignored Phase 1
          - ``limit_distance``: optional, ignored Phase 1
          - ``bot_id``: optional audit pointer
        """
        try:
            self._ensure_auth()
        except TradovateClientError as e:
            return {
                "status": "rejected",
                "reason": str(e),
                "error_code": e.error_code or "AUTH_FAILED",
            }

        if not self._account_id:
            return {
                "status": "rejected",
                "reason": "No Tradovate account resolved",
                "error_code": "NO_ACCOUNT",
            }

        symbol = order.get("instrument", "")
        resolved = self._resolver.resolve(symbol)
        if isinstance(resolved, UnsupportedSymbol):
            return {
                "status": "rejected",
                "reason": resolved.detail or resolved.code,
                "error_code": resolved.code,
            }

        raw_size = order.get("size", 0) or 0
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return {
                "status": "rejected",
                "reason": f"Tradovate requires whole-contract size > 0 (got {raw_size})",
                "error_code": "INVALID_SIZE",
            }

        action = _direction_to_action(order.get("direction", "BUY"))
        payload = self._build_order_payload(resolved, action, size)

        t_start = time.monotonic()
        try:
            result: TradovateOrderResult = self._client.place_order(payload)
        except TradovateClientError as e:
            logger.error(
                "Tradovate place_order failed: %s | symbol=%s action=%s size=%d",
                e, resolved.contract_symbol, action, size,
            )
            return {
                "status": "rejected",
                "reason": str(e),
                "error_code": e.error_code or "API_ERROR",
            }
        latency_ms = int((time.monotonic() - t_start) * 1000)

        if not result.success:
            return {
                "status": "rejected",
                "reason": result.failure_reason or "Tradovate rejected order",
                "error_code": "BROKER_REJECTED",
                "fill_latency_ms": latency_ms,
            }

        deal_id = str(result.order_id)
        self._open_orders[deal_id] = {
            "contract_symbol": resolved.contract_symbol,
            "product_code": resolved.product_code,
            "action": action,
            "size": size,
            "instrument": symbol,
        }
        return {
            "status": "ACCEPTED",
            "deal_id": deal_id,
            "order_id": deal_id,
            "broker_symbol": resolved.contract_symbol,
            "size": float(size),
            "filled_size": float(result.filled_size) if result.filled_size else None,
            "filled_price": result.filled_price,
            "level": result.filled_price,
            "fill_latency_ms": latency_ms,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._ensure_auth()
            return self._client.cancel_order(int(order_id))
        except (TradovateClientError, ValueError) as e:
            logger.error("Tradovate cancel_order failed for %s: %s", order_id, e)
            return False

    def modify_order(self, order_id: str, changes: dict) -> dict:
        # Phase 1: order modification not implemented. Return a typed reject
        # so callers can distinguish "not yet supported" from "broker error".
        return {
            "status": "failed",
            "reason": "Order modification not implemented for Tradovate in Phase 1",
            "error_code": "NOT_IMPLEMENTED",
        }

    def get_positions(self) -> list[dict]:
        try:
            self._ensure_auth()
            raw = self._client.list_positions()
        except TradovateClientError as e:
            logger.error("Tradovate get_positions failed: %s", e)
            return []
        positions: list[dict] = []
        for pos in raw or []:
            net_pos = pos.get("netPos", 0) or 0
            if not net_pos:
                continue
            positions.append(
                {
                    "deal_id": str(pos.get("id", "") or ""),
                    "broker_symbol": pos.get("contractMaturityId", "") or "",
                    "instrument": pos.get("contract", {}).get("name", "")
                    if isinstance(pos.get("contract"), dict)
                    else "",
                    "direction": "BUY" if net_pos > 0 else "SELL",
                    "size": abs(float(net_pos)),
                    "open_level": pos.get("netPrice"),
                }
            )
        return positions

    def get_account_info(self) -> dict:
        try:
            self._ensure_auth()
            if not self._account_id:
                return {"error": "No Tradovate account resolved"}
            summary = self._client.get_account_summary(self._account_id)
            return {
                "account_id": str(self._account_id),
                "balance": summary.get("amount") or summary.get("totalCashValue"),
                "equity": summary.get("totalCashValue") or summary.get("amount"),
                "currency": summary.get("currency", "USD"),
                "raw": summary,
            }
        except TradovateClientError as e:
            return {"error": str(e), "error_code": e.error_code}

    def close_position(self, position_id: str) -> dict:
        """Close an open position by submitting an opposite-side market order."""
        try:
            self._ensure_auth()
        except TradovateClientError as e:
            return {"status": "failed", "reason": str(e), "error_code": e.error_code}

        record = self._open_orders.get(position_id)
        if not record:
            return {
                "status": "failed",
                "reason": f"No tracked Tradovate order for {position_id}",
                "error_code": "UNKNOWN_DEAL_ID",
            }

        payload = {
            "accountId": int(self._account_id),
            "accountSpec": self._account_spec,
            "action": _opposite_action(record["action"]),
            "symbol": record["contract_symbol"],
            "orderQty": int(record["size"]),
            "orderType": "Market",
            "isAutomated": True,
        }
        try:
            result = self._client.place_order(payload)
        except TradovateClientError as e:
            return {"status": "failed", "reason": str(e), "error_code": e.error_code}

        if not result.success:
            return {
                "status": "failed",
                "reason": result.failure_reason or "Close rejected",
                "error_code": "CLOSE_REJECTED",
            }

        # Forget the open record once close has been accepted.
        self._open_orders.pop(position_id, None)
        return {
            "status": "ACCEPTED",
            "deal_id": str(result.order_id),
            "raw": result.raw,
        }

    def partial_close(self, position_id: str, pct: float) -> dict:
        """Tradovate has no native partial-close; submit a smaller opposite order."""
        try:
            self._ensure_auth()
        except TradovateClientError as e:
            return {"status": "failed", "reason": str(e), "error_code": e.error_code}

        record = self._open_orders.get(position_id)
        if not record:
            return {
                "status": "failed",
                "reason": f"No tracked Tradovate order for {position_id}",
                "error_code": "UNKNOWN_DEAL_ID",
            }
        full_size = int(record["size"])
        close_size = int(full_size * (pct / 100.0))
        if close_size <= 0:
            return {
                "status": "failed",
                "reason": "Partial close size rounds to 0 contracts",
                "error_code": "INVALID_SIZE",
            }

        payload = {
            "accountId": int(self._account_id),
            "accountSpec": self._account_spec,
            "action": _opposite_action(record["action"]),
            "symbol": record["contract_symbol"],
            "orderQty": close_size,
            "orderType": "Market",
            "isAutomated": True,
        }
        try:
            result = self._client.place_order(payload)
        except TradovateClientError as e:
            return {"status": "failed", "reason": str(e), "error_code": e.error_code}
        if not result.success:
            return {
                "status": "failed",
                "reason": result.failure_reason or "Partial close rejected",
                "error_code": "CLOSE_REJECTED",
            }

        record["size"] = full_size - close_size
        if record["size"] <= 0:
            self._open_orders.pop(position_id, None)
        return {
            "status": "ACCEPTED",
            "deal_id": str(result.order_id),
            "closed_size": float(close_size),
            "remaining_size": float(record["size"]) if record else 0.0,
        }

    # ── Health probe ─────────────────────────────────────────────────

    def healthcheck(self) -> dict:
        """Return a lightweight readiness summary without placing orders."""
        info = {
            "broker": "tradovate",
            "env": self._client.env,
            "configured": self._client.has_credentials,
            "reachable": False,
            "account_id": None,
            "account_name": None,
            "supported_symbols_count": len(self._resolver.supported_symbols()),
            "error": None,
        }
        if not info["configured"]:
            info["error"] = "Tradovate credentials not configured"
            return info
        try:
            self._client.authenticate()
            accounts = self._client.list_accounts()
            if accounts:
                acct = accounts[0]
                info["account_id"] = str(acct.account_id)
                info["account_name"] = acct.name
            info["reachable"] = True
        except TradovateClientError as e:
            info["error"] = str(e)
        return info
