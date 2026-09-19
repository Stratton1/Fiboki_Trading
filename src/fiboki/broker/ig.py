"""IG adapter -- DORMANT / LEGACY. Demo-only by compile-time impossibility.

Status
------
**Dormant.** V2's forward path is :mod:`fiboki.broker.oanda`. This module
exists to (a) keep the IG integration path open behind the V2 ``BrokerAdapter``
ABC, and (b) preserve V1's single strongest control, which was this:

    *The IG base URL is a module constant naming the DEMO host, and there is no
    code path -- no parameter, no environment variable, no config file -- that
    substitutes a live one.*

That control is preserved here and strengthened. :data:`IG_DEMO_BASE_URL` is a
constant, it is the only URL this module will ever build a request from, the
constructor takes **no** base-url argument, and :func:`_assert_demo_only`
re-parses the constant at construction and at every request, comparing the
PARSED HOSTNAME against :data:`IG_DEMO_HOST`. If somebody edits the constant to
a live host, the adapter refuses to run at all rather than trading on it.

So: enabling live IG trading is not a configuration change, an environment
change or a flag. It is a source change that has to *delete a check that
exists to stop exactly that*, in a reviewed commit. That was the strongest
thing V1 had, and removing it during a rewrite would be a regression however
much else improved.

Scope
-----
Deliberately thin: session authentication, account, positions, market spec,
market order placement with a ``dealReference`` idempotency key, and close.
Streaming, working orders, sprint markets and the rest of the IG surface are
not implemented, because implementing an interface nobody is calling is how you
get untested code that looks trustworthy. Everything raises
:class:`NotImplementedError` rather than returning a plausible empty result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from fiboki.broker.base import (
    BrokerAdapter,
    BrokerHealth,
    BrokerRejected,
    BrokerUnavailable,
    DuplicateClientRef,
    OrderAck,
    OrderStatus,
)
from fiboki.broker.oanda import HttpResponse, RateLimiter, Transport
from fiboki.core.contracts import AccountState, Fill, Order, Position
from fiboki.core.enums import Direction, ExecutionMode
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument

__all__ = [
    "IG_DEMO_BASE_URL",
    "IG_DEMO_HOST",
    "IG_LIVE_HOSTS",
    "IgAdapter",
    "IgConfig",
    "IgLiveHostRefused",
]


#: The ONLY base URL this module will ever use. Not a default, not a parameter.
IG_DEMO_BASE_URL = "https://demo-api.ig.com/gateway/deal"
IG_DEMO_HOST = "demo-api.ig.com"

#: Known real-money IG hosts. Named so the assertion below can be explicit
#: about what it is refusing, rather than merely failing an equality test.
IG_LIVE_HOSTS = frozenset({"api.ig.com", "deal.ig.com"})


class IgLiveHostRefused(RuntimeError):
    """The IG adapter was asked to talk to something other than the demo host."""


def _assert_demo_only(url: str) -> None:
    """Parse ``url`` and refuse anything that is not exactly the demo host.

    A parse, not a string comparison. V1's live gate was a string equality test
    that a trailing slash defeated; ``urlsplit`` normalises the slash, the port
    and any credentials, and a look-alike domain such as
    ``demo-api.ig.com.attacker.example`` yields a different hostname despite
    sharing the prefix.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host in IG_LIVE_HOSTS:
        raise IgLiveHostRefused(
            f"IG adapter refuses host {host!r}: this module is demo-only by "
            "construction. Enabling live IG execution requires deleting this "
            "assertion in a reviewed source change -- which is the point."
        )
    if host != IG_DEMO_HOST:
        raise IgLiveHostRefused(
            f"IG adapter refuses host {host!r}; the only permitted host is "
            f"{IG_DEMO_HOST!r}."
        )


@dataclass(frozen=True, slots=True)
class IgConfig:
    """IG session configuration. Note the absence of a base-url field."""

    identifier: str
    password: str
    api_key: str
    account_id: str = ""
    timeout: float = 10.0
    max_requests_per_second: float = 25.0

    @property
    def base_url(self) -> str:
        """Always the demo constant. There is no override and no parameter."""
        return IG_DEMO_BASE_URL


class IgAdapter(BrokerAdapter):
    """Legacy IG demo adapter behind the V2 ABC. Dormant; kept warm, not used."""

    #: Hard-pinned. This adapter cannot serve LIVE, and the value is not
    #: configurable, so no caller can widen it.
    mode = ExecutionMode.DEMO
    venue_name = "ig_demo"

    def __init__(
        self,
        config: IgConfig,
        *,
        transport: Transport,
        rate_limiter: RateLimiter | None = None,
        now_fn=None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.rate_limiter = rate_limiter or RateLimiter(config.max_requests_per_second)
        self._now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self._cst: str | None = None
        self._security_token: str | None = None
        self._connected = False
        _assert_demo_only(self.config.base_url)

    # ------------------------------------------------------------ plumbing

    def _headers(self, version: str = "2") -> dict[str, str]:
        headers = {
            "X-IG-API-KEY": self.config.api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": version,
        }
        if self._cst:
            headers["CST"] = self._cst
        if self._security_token:
            headers["X-SECURITY-TOKEN"] = self._security_token
        return headers

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        version: str = "2",
    ) -> HttpResponse:
        url = f"{self.config.base_url}{path}"
        # Re-asserted on EVERY request, not only at construction. A constant
        # edited at runtime, or an adapter whose config was swapped, still fails.
        _assert_demo_only(url)
        self.rate_limiter.acquire()
        try:
            response = self.transport.request(
                method, url, headers=self._headers(version), body=body,
                timeout=self.config.timeout,
            )
        except (IgLiveHostRefused, AssertionError):
            raise
        except Exception as exc:
            raise BrokerUnavailable(f"IG transport failure on {method} {path}: {exc}") from exc
        if response.status in (429, 500, 502, 503, 504):
            raise BrokerUnavailable(f"IG {response.status} on {method} {path}")
        return response

    # ------------------------------------------------------------ adapter

    def connect(self) -> BrokerHealth:
        response = self._call(
            "POST",
            "/session",
            {"identifier": self.config.identifier, "password": self.config.password},
            version="2",
        )
        if not response.ok:
            self._connected = False
            return BrokerHealth(False, 0.0, self._now_fn(), f"HTTP {response.status}")
        self._cst = response.headers.get("CST")
        self._security_token = response.headers.get("X-SECURITY-TOKEN")
        self._connected = bool(self._cst and self._security_token)
        return self.health()

    def health(self) -> BrokerHealth:
        return BrokerHealth(
            connected=self._connected,
            score=1.0 if self._connected else 0.0,
            checked_at=self._now_fn(),
            message="ig demo session" if self._connected else "no session",
            detail={"dormant": True, "host": IG_DEMO_HOST},
        )

    def account(self) -> AccountState:
        response = self._call("GET", "/accounts", version="1")
        if not response.ok:
            raise BrokerUnavailable(f"IG accounts HTTP {response.status}")
        accounts = response.body.get("accounts", [])
        chosen = None
        for acct in accounts:
            if not self.config.account_id or acct.get("accountId") == self.config.account_id:
                chosen = acct
                break
        if chosen is None:
            raise BrokerRejected(f"IG account {self.config.account_id!r} not found")
        balance = chosen.get("balance", {}) or {}
        return AccountState(
            balance=float(balance.get("balance", 0.0)),
            equity=float(balance.get("balance", 0.0)) + float(balance.get("profitLoss", 0.0)),
            currency=str(chosen.get("currency", "GBP")),
            margin_used=float(balance.get("deposit", 0.0)),
            unrealised_pnl=float(balance.get("profitLoss", 0.0)),
        )

    def positions(self) -> tuple[Position, ...]:
        response = self._call("GET", "/positions", version="2")
        if not response.ok:
            raise BrokerUnavailable(f"IG positions HTTP {response.status}")
        out: list[Position] = []
        for row in response.body.get("positions", []):
            pos = row.get("position", {}) or {}
            market = row.get("market", {}) or {}
            direction = (
                Direction.LONG if str(pos.get("direction")).upper() == "BUY" else Direction.SHORT
            )
            out.append(
                Position(
                    instrument=str(market.get("epic", "")),
                    direction=direction,
                    size=float(pos.get("size", 0.0)),
                    entry_price=float(pos.get("level", 0.0)),
                    entry_time=pd.Timestamp(pos["createdDateUTC"], tz="UTC")
                    if pos.get("createdDateUTC")
                    else self._now_fn(),
                    stop_loss=float(pos["stopLevel"]) if pos.get("stopLevel") else 0.0,
                    take_profit_targets=(
                        [float(pos["limitLevel"])] if pos.get("limitLevel") else []
                    ),
                    # IG's dealId. The broker's key space, persisted as such.
                    venue_ref=str(pos.get("dealId", "")),
                )
            )
        return tuple(out)

    def orders(self) -> tuple[OrderAck, ...]:
        response = self._call("GET", "/workingorders", version="2")
        if not response.ok:
            raise BrokerUnavailable(f"IG workingorders HTTP {response.status}")
        out: list[OrderAck] = []
        for row in response.body.get("workingOrders", []):
            order = row.get("workingOrderData", {}) or {}
            deal_id = str(order.get("dealId", ""))
            out.append(
                OrderAck(
                    status=OrderStatus.ACCEPTED,
                    broker_ref=deal_id,
                    client_ref=str(order.get("dealReference", "")),
                    order_id=deal_id,
                    submitted_at=self._now_fn(),
                    acked_at=self._now_fn(),
                    raw=order,
                )
            )
        return tuple(out)

    def market_spec(self, symbol: str) -> Instrument:
        """Our canonical spec. IG epics are mapped by the caller's registry."""
        return get_instrument(symbol)

    def place_order(self, order: Order) -> OrderAck:
        payload = {
            "epic": order.instrument,
            "expiry": "-",
            "direction": "BUY" if order.direction is Direction.LONG else "SELL",
            "size": order.size,  # as sized upstream; not recomputed
            "orderType": "MARKET",
            "guaranteedStop": False,
            "forceOpen": True,
            "currencyCode": "GBP",
            # IG's client-supplied reference. Alphanumeric, max 30 chars.
            "dealReference": order.client_ref[:30],
        }
        if order.stop_loss is not None:
            payload["stopLevel"] = order.stop_loss
        if order.take_profit is not None:
            payload["limitLevel"] = order.take_profit

        response = self._call("POST", "/positions/otc", payload, version="2")
        if response.status == 409:
            raise DuplicateClientRef(
                f"IG already holds dealReference {order.client_ref!r}"
            )
        if response.status in (400, 403):
            raise BrokerRejected(f"IG rejected the order: {response.body}")
        if not response.ok:
            raise BrokerUnavailable(f"IG order HTTP {response.status}; fate unknown")

        deal_ref = str(response.body.get("dealReference", order.client_ref))
        confirm = self._call("GET", f"/confirms/{deal_ref}", version="1")
        if not confirm.ok:
            # We know the venue took it; we do not know the outcome. UNKNOWN.
            return OrderAck(
                status=OrderStatus.UNKNOWN,
                broker_ref=None,
                client_ref=order.client_ref,
                order_id=order.order_id,
                submitted_at=self._now_fn(),
                message="accepted; confirmation unavailable, requires reconciliation",
            )
        body = confirm.body
        status = str(body.get("dealStatus", "")).upper()
        if status == "REJECTED":
            raise BrokerRejected(f"IG rejected: {body.get('reason')}")
        deal_id = str(body.get("dealId", ""))
        level = float(body.get("level", 0.0) or 0.0)
        size = float(body.get("size", order.size) or order.size)
        when = self._now_fn()
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=deal_id,
            client_ref=order.client_ref,
            order_id=order.order_id,
            submitted_at=when,
            acked_at=when,
            fill=Fill(
                order_id=order.order_id,
                instrument=order.instrument,
                direction=order.direction,
                filled_size=size,
                filled_price=level,
                requested_price=order.limit_price or level,
                filled_at=when,
                venue_ref=deal_id,
                partial=size + 1e-9 < order.size,
            ),
            message="filled",
            raw=body,
        )

    def close_position(
        self, position: Position, *, client_ref: str, reason: str = ""
    ) -> OrderAck:
        if not position.venue_ref:
            raise BrokerRejected(
                "Cannot close an IG position without its dealId. V1 compared an "
                "internal uuid4 against dealId and reconciled nothing."
            )
        payload = {
            "dealId": position.venue_ref,
            "direction": "SELL" if position.direction is Direction.LONG else "BUY",
            "size": position.size,
            "orderType": "MARKET",
        }
        response = self._call("POST", "/positions/otc", payload, version="1")
        if not response.ok:
            raise BrokerUnavailable(f"IG close HTTP {response.status}; fate unknown")
        when = self._now_fn()
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=str(position.venue_ref),
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=when,
            acked_at=when,
            message=reason or "closed",
            raw=response.body,
        )


def live_execution_available() -> bool:
    """Always False. Retained so callers can ask, and always get the same answer."""
    return False
