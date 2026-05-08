"""IG broker execution adapter — demo account only.

Translates Fiboki execution commands into IG REST API calls via IGClient.
All operations are routed through the demo API; production is hard-blocked.
"""

import logging
import time

from fibokei.core.instruments import get_ig_epic, get_symbol_by_epic
from fibokei.execution.adapter import ExecutionAdapter
from fibokei.execution.ig_client import IGClient, IGClientError

# Retry config for deal confirmation: IG can take a few seconds to confirm
_CONFIRM_MAX_ATTEMPTS = 3
_CONFIRM_RETRY_DELAY = 1.5  # seconds between attempts

# Hard caps — maximum contracts per trade regardless of account size.
# Prevents outsized positions from bad data or extreme market conditions.
_MAX_CONTRACTS_INDEX = 5.0
_MAX_CONTRACTS_FX = 20.0
_MAX_CONTRACTS_OTHER = 10.0

# Cache TTLs
_MARKET_CACHE_TTL = 3600   # 1 hour — IG contract specs rarely change
_BALANCE_CACHE_TTL = 300   # 5 minutes — balance updates after fills

logger = logging.getLogger(__name__)


class IGExecutionAdapter(ExecutionAdapter):
    """IG broker adapter — demo account execution."""

    def __init__(self, client: IGClient | None = None) -> None:
        self._client = client or IGClient()
        self._authenticated = False
        # market details cache: epic → {value_per_pip, one_pip_means, min_deal_size, _at}
        self._market_cache: dict[str, dict] = {}
        # account balance cache
        self._balance_cache: float = 0.0
        self._balance_cache_at: float = 0.0

    def _ensure_auth(self) -> None:
        if not self._authenticated:
            self._client.authenticate()
            self._authenticated = True

    def _get_market_details(self, epic: str) -> dict:
        """Return cached market details for an epic.

        Fetches from IG if missing or stale. Returns safe defaults on error
        so a single bad market lookup never blocks order placement.

        Returned keys:
          value_per_pip   — monetary value of 1 pip in account currency per 1 contract
          one_pip_means   — price movement = 1 pip (0.0001 for FX, 1.0 for indices)
          min_deal_size   — IG minimum contract size for this instrument
        """
        cached = self._market_cache.get(epic)
        if cached and (time.time() - cached["_at"]) < _MARKET_CACHE_TTL:
            return cached

        defaults = {"value_per_pip": 1.0, "one_pip_means": 1.0, "min_deal_size": 1.0, "_at": time.time()}
        try:
            data = self._client.get_market(epic)
            instr = data.get("instrument", {})
            rules = data.get("dealingRules", {})
            details = {
                "value_per_pip": float(instr.get("valueOfOnePip") or 1.0),
                "one_pip_means": float(instr.get("onePipMeans") or 1.0),
                "min_deal_size": float(
                    (rules.get("minDealSize") or {}).get("value") or 1.0
                ),
                "_at": time.time(),
            }
            self._market_cache[epic] = details
            logger.debug(
                "Market details cached for %s: vpp=%.4f opm=%.6f min=%.2f",
                epic, details["value_per_pip"], details["one_pip_means"], details["min_deal_size"],
            )
            return details
        except Exception as exc:
            logger.warning("Failed to fetch market details for %s: %s — using defaults", epic, exc)
            self._market_cache[epic] = defaults
            return defaults

    def _get_account_balance(self) -> float:
        """Return cached IG account balance. Refreshes every 5 minutes."""
        if self._balance_cache > 0 and (time.time() - self._balance_cache_at) < _BALANCE_CACHE_TTL:
            return self._balance_cache
        try:
            acct = self._client.get_account_info()
            balance = acct.get("balance", {})
            self._balance_cache = float(balance.get("balance", 0.0) or 0.0)
            self._balance_cache_at = time.time()
            logger.debug("IG account balance refreshed: %.2f", self._balance_cache)
        except Exception as exc:
            logger.warning("Failed to refresh account balance: %s — using cached %.2f", exc, self._balance_cache)
        return self._balance_cache

    def _calculate_size(
        self,
        epic: str,
        stop_price_distance: float | None,
        risk_pct: float,
    ) -> tuple[float, float]:
        """Calculate IG contract size and stop distance in IG native pips/points.

        Formula:
            stop_in_pips = stop_price_distance / one_pip_means
            size = (balance × risk_pct%) / (stop_in_pips × value_per_pip)

        Returns:
            (size, stop_in_pips) — size ready to send to IG, stop in IG-native units.
            stop_in_pips is 0.0 if no valid stop was provided.
        """
        mkt = self._get_market_details(epic)
        value_per_pip: float = mkt["value_per_pip"]
        one_pip_means: float = mkt["one_pip_means"]
        min_size: float = mkt["min_deal_size"]

        # Determine hard cap by instrument class
        if epic.startswith("IX."):
            max_size = _MAX_CONTRACTS_INDEX
        elif "CFD" in epic:
            max_size = _MAX_CONTRACTS_FX
        else:
            max_size = _MAX_CONTRACTS_OTHER

        # Convert price-unit stop distance → IG native pips/points
        stop_in_pips = 0.0
        if stop_price_distance and stop_price_distance > 0 and one_pip_means > 0:
            stop_in_pips = stop_price_distance / one_pip_means

        # Fall back to minimum size when we can't size properly
        if stop_in_pips <= 0 or value_per_pip <= 0:
            logger.warning(
                "Cannot risk-size %s (stop=%.6f vpp=%.4f) — using min %.2f",
                epic, stop_price_distance or 0.0, value_per_pip, min_size,
            )
            return min_size, stop_in_pips

        balance = self._get_account_balance()
        if balance <= 0:
            logger.warning("Account balance unavailable for %s — using min %.2f", epic, min_size)
            return min_size, stop_in_pips

        risk_amount = balance * (risk_pct / 100.0)
        raw_size = risk_amount / (stop_in_pips * value_per_pip)
        size = max(min_size, min(round(raw_size, 2), max_size))

        logger.info(
            "IG size %s: bal=%.2f risk_pct=%.1f%% stop_px=%.6f → pips=%.2f "
            "vpp=%.4f risk=%.2f raw=%.4f → size=%.2f [min=%.2f max=%.2f]",
            epic, balance, risk_pct, stop_price_distance or 0.0,
            stop_in_pips, value_per_pip, risk_amount, raw_size, size, min_size, max_size,
        )
        return size, stop_in_pips

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

        # Risk-proportional sizing using IG's own contract spec.
        # Formula: size = (balance × risk_pct%) / (stop_in_pips × value_per_pip)
        # _calculate_size also converts the price-unit stop distance to IG-native
        # pips/points (stop_price_distance / one_pip_means), which is what IG
        # requires in the stopDistance field.
        risk_pct = float(order.get("risk_pct", 1.0))
        stop_price_dist = order.get("stop_distance")    # price units from strategy
        limit_price_dist = order.get("limit_distance")  # price units from strategy

        size, stop_in_pips = self._calculate_size(epic, stop_price_dist, risk_pct)

        # Convert limit distance to IG-native pips the same way
        limit_in_pips = 0.0
        if limit_price_dist and limit_price_dist > 0:
            mkt = self._market_cache.get(epic, {})
            opm = mkt.get("one_pip_means", 1.0) or 1.0
            limit_in_pips = limit_price_dist / opm

        params: dict = {
            "epic": epic,
            "direction": direction,
            "size": size,        # must be numeric — IG v2 API rejects strings
            "orderType": "MARKET",
            "expiry": "-",       # required by IG for rolling CFDs (.IP instruments);
                                 # "-" = no fixed expiry (daily rolling contract)
            "currencyCode": order.get("currency", "GBP"),
            "guaranteedStop": False,
            "forceOpen": True,
        }

        # Only include stopDistance / limitDistance if meaningful (> 0)
        if stop_in_pips > 0:
            params["stopDistance"] = round(stop_in_pips, 1)
        if limit_in_pips > 0:
            params["limitDistance"] = round(limit_in_pips, 1)

        requested_price = order.get("requested_price")
        t_start = time.monotonic()

        try:
            result = self._client.open_position(params)
            fill_latency_ms = int((time.monotonic() - t_start) * 1000)
            deal_ref = result.get("dealReference", "")
            if deal_ref:
                confirmation = self._fetch_confirmation_with_retry(deal_ref)
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
            logger.error(
                "IG place_order failed: %s | params=%s",
                e, {k: v for k, v in params.items() if k != "guaranteedStop"},
            )
            return {"status": "rejected", "reason": str(e), "error_code": e.error_code}

    def _fetch_confirmation_with_retry(self, deal_reference: str) -> dict:
        """Fetch deal confirmation with retry backoff.

        IG occasionally takes a few seconds to process a deal before
        the confirmation endpoint is ready.  Retry up to
        _CONFIRM_MAX_ATTEMPTS times with a short delay before giving up.
        """
        last_error: Exception | None = None
        for attempt in range(1, _CONFIRM_MAX_ATTEMPTS + 1):
            try:
                return self._client.get_deal_confirmation(deal_reference)
            except IGClientError as e:
                last_error = e
                if attempt < _CONFIRM_MAX_ATTEMPTS:
                    logger.warning(
                        "Deal confirmation attempt %d/%d failed for %s: %s — retrying",
                        attempt, _CONFIRM_MAX_ATTEMPTS, deal_reference, e,
                    )
                    time.sleep(_CONFIRM_RETRY_DELAY)
        logger.error(
            "Deal confirmation failed after %d attempts for %s: %s",
            _CONFIRM_MAX_ATTEMPTS, deal_reference, last_error,
        )
        # Return a partial response so the order isn't lost — status will be
        # "PENDING_CONFIRMATION" and the audit log will capture it.
        return {"dealStatus": "PENDING_CONFIRMATION", "dealReference": deal_reference}

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
                confirmation = self._fetch_confirmation_with_retry(deal_ref)
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
