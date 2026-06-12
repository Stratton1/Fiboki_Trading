"""IG broker execution adapter — demo account only.

Translates Fiboki execution commands into IG REST API calls via IGClient.
All operations are routed through the demo API; production is hard-blocked.
"""

import logging
import os
import time

from fibokei.core.instruments import get_ig_epic, get_instrument, get_symbol_by_epic
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
        # symbol → account-type-correct epic, resolved at runtime when the
        # static mapping points at an exchange this apiUser cannot access
        # (observed: 'unauthorised access, apiUser has no access to the
        # relevant exchange. Epic=CS.D.USCGC.TODAY.IP exchangeId=FX_BET_ALL')
        self._resolved_epics: dict[str, str] = {}

    def _resolve_epic_for_account(self, symbol: str, bad_epic: str) -> str | None:
        """Find an epic for ``symbol`` that this account can actually trade.

        Searches IG markets by the instrument's human name (falling back to
        the symbol) and returns the first TRADEABLE candidate that differs
        from the inaccessible static epic. Result is cached per symbol and
        every remap is logged so the catalogue can be corrected later.
        """
        cached = self._resolved_epics.get(symbol)
        if cached:
            return cached
        try:
            inst = get_instrument(symbol)
            terms = [inst.name.split("/")[0].strip(), symbol]
        except KeyError:
            terms = [symbol]
        for term in terms:
            try:
                markets = self._client.search_markets(term)
            except IGClientError as e:
                logger.warning("IG epic search '%s' failed: %s", term, e)
                continue
            for m in markets:
                epic = m.get("epic", "")
                if not epic or epic == bad_epic:
                    continue
                if m.get("marketStatus") not in (None, "TRADEABLE", "EDITS_ONLY"):
                    continue
                # Verify this account can read the market's details — proxy
                # for exchange access without placing an order.
                try:
                    self._client.get_market(epic)
                except IGClientError:
                    continue
                self._resolved_epics[symbol] = epic
                logger.warning(
                    "IG epic remapped for %s: %s → %s (%s). Static mapping in "
                    "core/instruments.py is not valid for this account type.",
                    symbol, bad_epic, epic, m.get("instrumentName", ""),
                )
                return epic
        logger.error("IG epic resolution failed for %s (static epic %s)", symbol, bad_epic)
        return None

    def _ensure_auth(self) -> None:
        """Ensure a valid IG session exists.

        Delegates to IGClient.ensure_session() which handles TTL-based
        re-authentication with a threading lock.  The local _authenticated
        flag is kept for backwards compatibility with callers that check it
        but has no real effect — session validity is managed entirely by
        the client.
        """
        self._client.ensure_session()
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

        defaults = {
            "value_per_pip": 1.0, "one_pip_means": 1.0, "min_deal_size": 1.0,
            "min_stop_distance": 0.0, "is_default": True, "_at": time.time(),
        }

        def _num(value, fallback: float) -> float:
            """Parse IG numeric fields that may carry unit suffixes.

            The CFD account returns e.g. onePipMeans='0.0001 USD/EUR' and
            valueOfOnePip='10 USD' — take the leading numeric token.
            (Verified on real IG demo 2026-06-12.)
            """
            if value is None:
                return fallback
            try:
                return float(str(value).split()[0].replace(",", ""))
            except (ValueError, IndexError):
                return fallback

        try:
            data = self._client.get_market(epic)
            instr = data.get("instrument", {})
            rules = data.get("dealingRules", {})
            details = {
                "value_per_pip": _num(instr.get("valueOfOnePip"), 1.0),
                "one_pip_means": _num(instr.get("onePipMeans"), 1.0),
                "min_deal_size": float(
                    (rules.get("minDealSize") or {}).get("value") or 1.0
                ),
                "min_stop_distance": float(
                    (rules.get("minNormalStopOrLimitDistance") or {}).get("value") or 0.0
                ),
                # IG quote scale reference: FX CFDs quote in points (EURUSD
                # ≈ 13050.9) while strategy feeds quote classic (≈ 1.3051).
                # Used to detect and normalise the price-scale mismatch.
                "snapshot_bid": float(
                    (data.get("snapshot", {}) or {}).get("bid") or 0.0
                ),
                "is_default": False,
                "_at": time.time(),
            }
            self._market_cache[epic] = details
            logger.debug(
                "Market details cached for %s: vpp=%.4f opm=%.6f min=%.2f",
                epic, details["value_per_pip"], details["one_pip_means"], details["min_deal_size"],
            )
            return details
        except Exception as exc:
            # DO NOT cache the defaults: dealing on default specs destroyed
            # FX stops (onePipMeans=1.0 turned a 45-pip stop into 0.0) and
            # exploded sizes to the hard cap. Defaults are returned for
            # read-only callers but are flagged so place_order refuses to
            # deal on them, and the next call retries the fetch.
            logger.warning(
                "Failed to fetch market details for %s: %s — returning "
                "is_default specs (orders on this epic will be rejected)",
                epic, exc,
            )
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
            epic = self._resolved_epics.get(symbol) or get_ig_epic(symbol)
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

        # Refuse to deal on default/unfetched market specs — risk-sizing and
        # pip conversion are meaningless without the real contract spec.
        market_spec = self._get_market_details(epic)
        if market_spec.get("is_default"):
            logger.warning(
                "IG order rejected pre-submission: market details unavailable "
                "for %s (symbol=%s bot=%s)", epic, symbol, order.get("bot_id", ""),
            )
            return {
                "status": "rejected",
                "reason": f"Market details unavailable for {epic}; refusing to size blind",
                "error_code": "MARKET_DETAILS_UNAVAILABLE",
            }

        # ── Price-scale normalisation ────────────────────────────────
        # Strategies compute prices/distances on the classic feed scale
        # (EURUSD ≈ 1.3051) but IG CFD epics may quote in points
        # (EURUSD ≈ 13050.9, a clean power-of-10 multiple). Detect the
        # scale from the live IG snapshot vs the strategy's requested
        # price and normalise distances + requested price accordingly.
        # Verified on real IG demo 2026-06-12: without this, a valid
        # 45-pip stop converts to 0.0045 IG points (STOP_TOO_TIGHT).
        requested_price = order.get("requested_price")
        snapshot_bid = float(market_spec.get("snapshot_bid") or 0.0)
        if requested_price and requested_price > 0 and snapshot_bid > 0:
            import math
            ratio = snapshot_bid / requested_price
            if ratio > 3.0 or ratio < 1 / 3.0:
                power = round(math.log10(ratio))
                scale = 10.0 ** power
                # Only trust clean power-of-10 scale mismatches (±25%);
                # anything else means prices disagree for another reason
                # (stale feed, wrong instrument) — leave untouched and let
                # the stop gates reject if conversion is wrong.
                if 0.75 <= (ratio / scale) <= 1.25:
                    if stop_price_dist:
                        stop_price_dist = stop_price_dist * scale
                    if limit_price_dist:
                        limit_price_dist = limit_price_dist * scale
                    requested_price = requested_price * scale
                    logger.info(
                        "IG price-scale normalised for %s: feed→IG ×%g "
                        "(snapshot=%.2f, requested=%.5f)",
                        symbol, scale, snapshot_bid, order.get("requested_price"),
                    )

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

        # Validate the BROKER-UNIT stop after conversion and rounding.
        # A valid 45-pip price-unit stop converted with a wrong onePipMeans
        # rounds to 0.0 and reaches IG as a naked order. Require the final
        # stopDistance to clear IG's own minimum for this market.
        broker_stop = params.get("stopDistance", 0.0)
        min_stop = max(float(market_spec.get("min_stop_distance") or 0.0), 0.1)
        if 0.0 < stop_in_pips and broker_stop < min_stop:
            logger.warning(
                "IG order rejected pre-submission: converted stop %.2f below "
                "minimum %.2f (epic=%s symbol=%s bot=%s opm=%.6f)",
                broker_stop, min_stop, epic, symbol,
                order.get("bot_id", ""), market_spec.get("one_pip_means", 0.0),
            )
            return {
                "status": "rejected",
                "reason": (
                    f"Converted stop {broker_stop} below broker minimum "
                    f"{min_stop} for {epic}"
                ),
                "error_code": "STOP_TOO_TIGHT",
            }

        # Safety gate: refuse to open broker positions without a stop-loss.
        # Deployed logs showed naked size-capped orders (size=20, stop=0.0)
        # reaching IG. Default-on; set FIBOKEI_IG_REQUIRE_STOP=false to
        # restore the old behaviour deliberately.
        require_stop = os.environ.get(
            "FIBOKEI_IG_REQUIRE_STOP", "true"
        ).strip().lower() in ("1", "true", "yes", "on")
        if require_stop and stop_in_pips <= 0:
            logger.warning(
                "IG order rejected pre-submission: no stop-loss (symbol=%s bot=%s). "
                "Strategy emitted stop_distance=%s.",
                symbol, order.get("bot_id", ""), stop_price_dist,
            )
            return {
                "status": "rejected",
                "reason": "Stop-loss required for IG execution (FIBOKEI_IG_REQUIRE_STOP)",
                "error_code": "MISSING_STOP",
            }

        # NOTE: requested_price was resolved (and possibly scale-normalised)
        # above — do not re-read it from the order here.

        # Diagnostic log — shows every IG order attempt with all key parameters
        # so failures are immediately diagnosable from Railway logs.
        logger.info(
            "IG place_order: epic=%s dir=%s size=%.2f expiry=%s stop=%.1f limit=%.1f "
            "currency=%s entry=%.5f bot=%s",
            epic, direction, size, params["expiry"],
            params.get("stopDistance", 0.0), params.get("limitDistance", 0.0),
            params["currencyCode"], requested_price or 0.0,
            order.get("bot_id", ""),
        )

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
                    # Both prices are on IG's quote scale here; convert the
                    # difference to IG pips/points via onePipMeans.
                    opm_s = float(market_spec.get("one_pip_means") or 1.0) or 1.0
                    slippage_pips = round(abs(filled_price - requested_price) / opm_s, 2)
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
            # Account-type/exchange mismatch: the static epic belongs to an
            # exchange this apiUser cannot access (e.g. spread-bet epics on
            # a CFD key → 'no access to the relevant exchange'). The 403 is
            # raised before any deal is created, so one retry with a
            # runtime-resolved epic is duplicate-safe.
            if (
                "no access to the relevant exchange" in str(e)
                and self._resolved_epics.get(symbol) != epic
                and not order.get("_epic_retry")
            ):
                resolved = self._resolve_epic_for_account(symbol, epic)
                if resolved and resolved != epic:
                    retry_order = {**order, "_epic_retry": True}
                    logger.info(
                        "Retrying IG order for %s on resolved epic %s", symbol, resolved
                    )
                    return self.place_order(retry_order)
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
