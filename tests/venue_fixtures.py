"""In-process IG and OANDA venues, spoken to over the adapters' real payloads.

``RecordedTransport`` replays a fixed queue of responses. That is the right
tool for asserting a single request's shape and the wrong one for driving a
900-bar session: every order would come back with the same ``dealId``, so
nothing could be amended, closed or reconciled per position and the test would
be proving that one canned response parses.

These two classes are :class:`fiboki.broker.oanda.Transport` implementations
that hold a real position book and answer the adapters' actual requests. They
exist so the IG and OANDA adapters can be driven through a whole session --
entry, trail, scale-out, close, reconciliation -- against something that
behaves like the venue rather than like a fixture.

What they are faithful about, because the test depends on it
-------------------------------------------------------------
* the URL paths and HTTP verbs the adapters build;
* the request payload field names (``dealReference``, ``stopLevel``,
  ``limitLevel``, ``clientExtensions.id``, ``stopLossOnFill`` ...);
* the response shapes the adapters parse;
* **the ONE stop and ONE limit per position constraint**, which is the whole
  point: setting a second target is not expressible in either API and these
  venues cannot be asked to do it;
* IG's whole-set amendment semantics (a level omitted from the payload is
  REMOVED) versus OANDA's per-order semantics (a level omitted is LEFT ALONE).
  The adapters compensate for that difference in opposite directions, and a
  fixture that ignored it would let a bug through in whichever direction it
  happened to be wrong.

What they are not
-----------------
A market. They fill at the price they are told, do not simulate a stop being
hit, and do not move. The market model is :class:`fiboki.sim.fills.FillSimulator`
and lives in the position book; putting a second one here would create exactly
the second source of truth this architecture exists to avoid.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from fiboki.broker.oanda import HttpResponse

__all__ = ["HttpVenueError", "IgHttpVenue", "OandaHttpVenue", "VenuePosition"]


class HttpVenueError(AssertionError):
    """The adapter sent something these venues do not implement."""


@dataclass
class VenuePosition:
    """One position as the venue holds it: ONE stop, ONE limit, and no more."""

    deal_id: str
    client_ref: str
    instrument: str
    direction: str
    size: float
    price: float
    opened_at: pd.Timestamp
    stop_level: float | None = None
    limit_level: float | None = None
    closed: bool = False
    #: Every amendment the venue accepted, in order. The test asserts against
    #: this rather than against our own record, which is the only way to prove
    #: a trail step actually left the process.
    amendments: list[dict[str, Any]] = field(default_factory=list)
    partials: list[float] = field(default_factory=list)


class _BaseHttpVenue:
    def __init__(
        self,
        *,
        prices: dict[str, float] | None = None,
        now: pd.Timestamp | None = None,
        currency: str = "GBP",
        balance: float = 50_000.0,
    ) -> None:
        self.prices = dict(prices or {})
        self.now = now or pd.Timestamp("2024-01-01", tz="UTC")
        self.currency = currency
        self.balance = balance
        self.positions: dict[str, VenuePosition] = {}
        self.calls: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        #: Refuse the first N amendments outright. Models "too close to market",
        #: which is the amendment rejection that actually happens in production.
        self.reject_amendments = 0
        #: Fail every amendment with a 503: the outcome is UNKNOWN.
        self.amend_unavailable = False
        self._amend_count = 0

    def price_for(self, symbol: str, fallback: float = 1.0) -> float:
        return float(self.prices.get(symbol, fallback))

    def open_positions(self) -> list[VenuePosition]:
        return [p for p in self.positions.values() if not p.closed]

    def _record(self, method: str, path: str, body: Any) -> None:
        self.calls.append({"method": method, "path": path, "body": body})

    def _amend_gate(self) -> HttpResponse | None:
        self._amend_count += 1
        if self.amend_unavailable:
            return HttpResponse(503, {"errorMessage": "service unavailable"})
        if self._amend_count <= self.reject_amendments:
            return HttpResponse(
                400,
                {
                    "errorCode": "ATTACHED_ORDER_LEVEL_DISTANCE_ERROR",
                    "errorMessage": "level too close to market",
                },
            )
        return None


# ==========================================================================
# IG
# ==========================================================================


class IgHttpVenue(_BaseHttpVenue):
    """IG's deal gateway, in process. ``PUT`` amends the WHOLE protective set."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        path = urlsplit(url).path
        # The adapter builds every URL from IG_DEMO_BASE_URL, whose path prefix
        # is /gateway/deal. Strip it so the routing below reads like the API.
        route = path.split("/gateway/deal", 1)[-1] or "/"
        self._record(method, route, body)

        if method == "POST" and route == "/session":
            return HttpResponse(
                200, {}, {"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"}
            )
        if method == "GET" and route == "/accounts":
            return HttpResponse(
                200,
                {
                    "accounts": [
                        {
                            "accountId": "ABC123",
                            "currency": self.currency,
                            "balance": {
                                "balance": self.balance,
                                "profitLoss": 0.0,
                                "deposit": 0.0,
                            },
                        }
                    ]
                },
            )
        if method == "GET" and route == "/positions":
            return HttpResponse(200, {"positions": [
                {
                    "position": {
                        "dealId": p.deal_id,
                        "direction": p.direction,
                        "size": p.size,
                        "level": p.price,
                        "createdDateUTC": p.opened_at.isoformat(),
                        "stopLevel": p.stop_level,
                        "limitLevel": p.limit_level,
                    },
                    "market": {"epic": p.instrument},
                }
                for p in self.open_positions()
            ]})
        if method == "GET" and route == "/workingorders":
            return HttpResponse(200, {"workingOrders": []})
        if method == "GET" and route.startswith("/confirms/"):
            ref = route.split("/confirms/", 1)[1]
            found = next(
                (p for p in self.positions.values() if p.client_ref == ref), None
            )
            if found is None:
                return HttpResponse(404, {"errorCode": "no such deal reference"})
            return HttpResponse(
                200,
                {
                    "dealStatus": "ACCEPTED",
                    "dealId": found.deal_id,
                    "level": found.price,
                    "size": found.size,
                },
            )
        if method == "POST" and route == "/positions/otc":
            return self._otc(body or {})
        if method == "PUT" and route.startswith("/positions/otc/"):
            return self._amend(route.rsplit("/", 1)[1], body or {})
        raise HttpVenueError(f"IgHttpVenue has no route for {method} {route}")

    # -- routes ---------------------------------------------------------

    def _otc(self, body: dict[str, Any]) -> HttpResponse:
        if "dealId" in body:
            return self._close(body)
        ref = str(body.get("dealReference", ""))
        if any(p.client_ref == ref for p in self.positions.values()):
            # IG honours the client-supplied reference as an idempotency key.
            return HttpResponse(409, {"errorCode": "duplicate dealReference"})
        deal_id = f"DIAAAA{next(self._ids):06d}"
        symbol = str(body["epic"])
        self.positions[deal_id] = VenuePosition(
            deal_id=deal_id,
            client_ref=ref,
            instrument=symbol,
            direction=str(body["direction"]),
            size=float(body["size"]),
            price=self.price_for(symbol),
            opened_at=self.now,
            stop_level=body.get("stopLevel"),
            limit_level=body.get("limitLevel"),
        )
        return HttpResponse(200, {"dealReference": ref})

    def _close(self, body: dict[str, Any]) -> HttpResponse:
        deal_id = str(body["dealId"])
        held = self.positions.get(deal_id)
        if held is None or held.closed:
            return HttpResponse(404, {"errorCode": "position not found"})
        size = float(body.get("size", held.size))
        if size > held.size + 1e-9:
            return HttpResponse(400, {"errorCode": "size exceeds position"})
        held.partials.append(size)
        held.size -= size
        if held.size <= 1e-12:
            held.closed = True
        return HttpResponse(200, {"level": self.price_for(held.instrument), "size": size})

    def _amend(self, deal_id: str, body: dict[str, Any]) -> HttpResponse:
        held = self.positions.get(deal_id)
        if held is None or held.closed:
            return HttpResponse(404, {"errorCode": "position not found"})
        gate = self._amend_gate()
        if gate is not None:
            return gate
        # WHOLE-SET semantics: whatever the payload names is what the position
        # ends up holding, including nulls. This is what makes the adapter's
        # restating of the level it was NOT given load-bearing rather than
        # defensive.
        held.stop_level = body.get("stopLevel")
        held.limit_level = body.get("limitLevel")
        held.amendments.append(
            {"stopLevel": held.stop_level, "limitLevel": held.limit_level}
        )
        return HttpResponse(200, {"dealReference": f"AMEND-{deal_id}"})


# ==========================================================================
# OANDA
# ==========================================================================


class OandaHttpVenue(_BaseHttpVenue):
    """OANDA v20, in process. Dependent orders are amended INDIVIDUALLY."""

    def __init__(self, *, account_id: str = "001-001-1234567-001", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.account_id = account_id

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        path = urlsplit(url).path
        prefix = f"/v3/accounts/{self.account_id}"
        route = path[len(prefix) :] if path.startswith(prefix) else path
        self._record(method, route, body)

        if method == "GET" and route == "/summary":
            return HttpResponse(
                200,
                {
                    "account": {
                        "balance": str(self.balance),
                        "NAV": str(self.balance),
                        "currency": self.currency,
                        "marginUsed": "0",
                        "openTradeCount": len(self.open_positions()),
                        "pl": "0",
                        "unrealizedPL": "0",
                    }
                },
            )
        if method == "GET" and route == "/openTrades":
            return HttpResponse(200, {"trades": [
                {
                    "id": p.deal_id,
                    "instrument": _to_oanda(p.instrument),
                    "currentUnits": str(p.size if p.direction == "BUY" else -p.size),
                    "price": str(p.price),
                    "openTime": p.opened_at.isoformat(),
                    "stopLossOrder": (
                        {"price": str(p.stop_level)} if p.stop_level else {}
                    ),
                    "takeProfitOrder": (
                        {"price": str(p.limit_level)} if p.limit_level else {}
                    ),
                }
                for p in self.open_positions()
            ]})
        if method == "GET" and route == "/pendingOrders":
            return HttpResponse(200, {"orders": []})
        if method == "POST" and route == "/orders":
            return self._place((body or {}).get("order", {}))
        if method == "PUT" and route.endswith("/close"):
            return self._close(route.split("/")[2], body or {})
        if method == "PUT" and route.endswith("/orders"):
            return self._amend(route.split("/")[2], body or {})
        raise HttpVenueError(f"OandaHttpVenue has no route for {method} {route}")

    # -- routes ---------------------------------------------------------

    def _place(self, order: dict[str, Any]) -> HttpResponse:
        client_ref = str((order.get("clientExtensions") or {}).get("id", ""))
        if any(p.client_ref == client_ref for p in self.positions.values()):
            return HttpResponse(
                400, {"errorCode": "CLIENT_ORDER_ID_ALREADY_EXISTS"}
            )
        units = float(order["units"])
        symbol = _from_oanda(str(order["instrument"]))
        trade_id = str(next(self._ids) + 1000)
        price = self.price_for(symbol)
        stop = (order.get("stopLossOnFill") or {}).get("price")
        target = (order.get("takeProfitOnFill") or {}).get("price")
        self.positions[trade_id] = VenuePosition(
            deal_id=trade_id,
            client_ref=client_ref,
            instrument=symbol,
            direction="BUY" if units > 0 else "SELL",
            size=abs(units),
            price=price,
            opened_at=self.now,
            stop_level=float(stop) if stop is not None else None,
            limit_level=float(target) if target is not None else None,
        )
        return HttpResponse(
            200,
            {
                "orderCreateTransaction": {"id": trade_id, "time": self.now.isoformat()},
                "orderFillTransaction": {
                    "id": trade_id,
                    "time": self.now.isoformat(),
                    "price": str(price),
                    "units": str(units),
                    "tradeOpened": {"tradeID": trade_id, "units": str(units)},
                },
            },
        )

    def _close(self, trade_id: str, body: dict[str, Any]) -> HttpResponse:
        held = self.positions.get(trade_id)
        if held is None or held.closed:
            return HttpResponse(404, {"errorMessage": "trade not found"})
        units = body.get("units", "ALL")
        size = held.size if units == "ALL" else abs(float(units))
        if size > held.size + 1e-9:
            return HttpResponse(400, {"errorCode": "CLOSEOUT_UNITS_INVALID"})
        held.partials.append(size)
        held.size -= size
        if held.size <= 1e-12:
            held.closed = True
        return HttpResponse(
            200,
            {
                "orderFillTransaction": {
                    "time": self.now.isoformat(),
                    "price": str(self.price_for(held.instrument)),
                    "units": str(size),
                }
            },
        )

    def _amend(self, trade_id: str, body: dict[str, Any]) -> HttpResponse:
        held = self.positions.get(trade_id)
        if held is None or held.closed:
            return HttpResponse(404, {"errorMessage": "trade not found"})
        gate = self._amend_gate()
        if gate is not None:
            return gate
        # PER-ORDER semantics: a dependent order absent from the payload is
        # LEFT ALONE. The opposite of IG, and the reason the two adapters do
        # not share an implementation of this method.
        if "stopLoss" in body:
            held.stop_level = float(body["stopLoss"]["price"])
        if "takeProfit" in body:
            held.limit_level = float(body["takeProfit"]["price"])
        held.amendments.append(
            {"stopLevel": held.stop_level, "limitLevel": held.limit_level}
        )
        out: dict[str, Any] = {}
        if "stopLoss" in body:
            out["stopLossOrderTransaction"] = {
                "id": f"SL-{trade_id}", "time": self.now.isoformat()
            }
        if "takeProfit" in body:
            out["takeProfitOrderTransaction"] = {
                "id": f"TP-{trade_id}", "time": self.now.isoformat()
            }
        return HttpResponse(200, out)


def _to_oanda(symbol: str) -> str:
    from fiboki.broker.oanda import to_oanda_instrument

    return to_oanda_instrument(symbol)


def _from_oanda(name: str) -> str:
    from fiboki.broker.oanda import from_oanda_instrument

    return from_oanda_instrument(name)
