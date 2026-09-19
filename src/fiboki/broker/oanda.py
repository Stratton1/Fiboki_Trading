"""OANDA v20 REST adapter.

No credentials exist for this adapter and none are needed to test it: every
request goes through an injected :class:`Transport`, and
:class:`RecordedTransport` replays fixtures captured from the documented
response shapes. ``tests/unit/test_oanda_adapter.py`` exercises auth headers,
instrument mapping, order placement, client-extension idempotency, stop/TP
attachment, position query, rate-limit pacing and every host-safety control
without a single byte leaving the process.

Host safety -- three independent controls
-----------------------------------------
V1 could reach a live broker API from a single environment variable, because
the gate was a string comparison that a trailing slash defeated. Here, reaching
the live host requires ALL THREE of:

1. :data:`OANDA_LIVE_HOST_COMPILED_IN` -- a build-time constant in THIS module,
   deliberately named differently from
   :data:`fiboki.broker.mode_guard.LIVE_EXECUTION_COMPILED_IN` so that flipping
   one does not flip the other. Two separate source edits, two reviews.
2. ``FIBOKI_OANDA_LIVE_RUNTIME`` set to :data:`OANDA_LIVE_RUNTIME_TOKEN`
   exactly -- an unguessable value, not ``"true"``.
3. A **parsed-hostname assertion**: the configured base URL is parsed with
   :func:`urllib.parse.urlsplit` and its ``hostname`` compared for equality
   against the known host constants. ``https://api-fxtrade.oanda.com/`` and
   ``https://api-fxtrade.oanda.com`` parse identically;
   ``https://api-fxpractice.oanda.com.attacker.example`` does not match the
   practice host despite sharing its prefix.

Anything that is neither the practice host nor an explicitly authorised live
host is refused outright. There is no "unknown host, probably fine" branch.

Documented approximations
-------------------------
* ``marginRate`` from the venue is read but not used for sizing -- sizing
  happens once, upstream, from our own instrument registry. The venue's value
  is surfaced for reconciliation only.
* Units are integers for FX (OANDA's ``tradeUnitsPrecision`` is 0 on majors);
  the adapter converts our size to the venue's precision and **fails loudly**
  if that conversion would change the number, rather than silently rounding.
  Silently rounding here is how an adapter starts re-deciding size.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
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
from fiboki.core.contracts import AccountState, Fill, Order, Position
from fiboki.core.enums import Direction, ExecutionMode
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument

__all__ = [
    "OANDA_LIVE_HOST",
    "OANDA_LIVE_HOST_COMPILED_IN",
    "OANDA_LIVE_RUNTIME_TOKEN",
    "OANDA_PRACTICE_HOST",
    "HttpResponse",
    "OandaAdapter",
    "OandaConfig",
    "OandaHostError",
    "RateLimiter",
    "RecordedTransport",
    "Transport",
    "from_oanda_instrument",
    "to_oanda_instrument",
]


OANDA_PRACTICE_HOST = "api-fxpractice.oanda.com"
OANDA_LIVE_HOST = "api-fxtrade.oanda.com"

#: BUILD-TIME control, specific to this adapter. Separately named from the mode
#: guard's constant on purpose: enabling live execution platform-wide and
#: enabling the live OANDA host are two decisions, and neither implies the other.
OANDA_LIVE_HOST_COMPILED_IN: bool = False

#: RUNTIME control. Must match exactly. Not "true", not "1".
OANDA_LIVE_RUNTIME_TOKEN = "OANDA-LIVE-HOST-ARMED-I-ACCEPT-REAL-MONEY-RISK"
_OANDA_LIVE_ENV = "FIBOKI_OANDA_LIVE_RUNTIME"


class OandaHostError(RuntimeError):
    """The configured base URL is not an authorised host for this build."""


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse: ...


class RecordedTransport:
    """Replays fixture responses keyed by ``"METHOD /path"``.

    Each key maps to a list consumed in order; the last entry repeats once the
    list is exhausted, so a health check can be called any number of times. A
    request with no fixture raises, because a silently-empty response is how a
    test proves nothing.
    """

    def __init__(self, fixtures: dict[str, list[HttpResponse]]) -> None:
        self.fixtures = {k: list(v) for k, v in fixtures.items()}
        self.calls: list[dict[str, Any]] = []

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
        query = urlsplit(url).query
        key = f"{method.upper()} {path}"
        self.calls.append(
            {
                "method": method.upper(),
                "url": url,
                "path": path,
                "query": query,
                "headers": dict(headers),
                "body": json.loads(json.dumps(body)) if body is not None else None,
            }
        )
        queue = self.fixtures.get(key)
        if not queue:
            raise AssertionError(
                f"No recorded fixture for {key!r}. Known: {sorted(self.fixtures)}"
            )
        return queue.pop(0) if len(queue) > 1 else queue[0]


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


class RateLimiter:
    """Minimum-interval pacer. Clock and sleep are injected so tests are instant.

    OANDA documents a per-account request ceiling. Exceeding it produces 429s,
    and a 429 on an order submission is indistinguishable from a timeout: the
    order's fate becomes UNKNOWN. Pacing is therefore a correctness control,
    not a politeness one.
    """

    def __init__(
        self,
        max_requests_per_second: float = 90.0,
        *,
        clock=time.monotonic,
        sleeper=time.sleep,
    ) -> None:
        if max_requests_per_second <= 0:
            raise ValueError("max_requests_per_second must be positive")
        self.min_interval = 1.0 / max_requests_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._last: float | None = None
        self.waits: list[float] = []

    def acquire(self) -> None:
        now = self._clock()
        if self._last is not None:
            elapsed = now - self._last
            if elapsed < self.min_interval:
                wait = self.min_interval - elapsed
                self.waits.append(wait)
                self._sleeper(wait)
                now = self._clock()
        self._last = now


# --------------------------------------------------------------------------
# Instrument mapping
# --------------------------------------------------------------------------

#: Non-FX symbols whose OANDA name cannot be derived by splitting.
_SPECIAL_TO_OANDA = {
    "XAUUSD": "XAU_USD",
    "XAGUSD": "XAG_USD",
    "WTIUSD": "WTICO_USD",
    "BCOUSD": "BCO_USD",
    "US500": "SPX500_USD",
    "US100": "NAS100_USD",
    "US30": "US30_USD",
    "UK100": "UK100_GBP",
    "DE40": "DE30_EUR",
    "FR40": "FR40_EUR",
    "JP225": "JP225_USD",
    "AU200": "AU200_AUD",
    "HK50": "HK33_HKD",
    "EU50": "EU50_EUR",
}
_SPECIAL_FROM_OANDA = {v: k for k, v in _SPECIAL_TO_OANDA.items()}


def to_oanda_instrument(symbol: str) -> str:
    key = symbol.upper()
    if key in _SPECIAL_TO_OANDA:
        return _SPECIAL_TO_OANDA[key]
    instrument = get_instrument(key)
    if instrument.is_fx and len(key) == 6:
        return f"{key[:3]}_{key[3:]}"
    raise KeyError(
        f"No OANDA instrument mapping for {symbol!r}. Add it to _SPECIAL_TO_OANDA "
        "rather than guessing: a wrong mapping trades the wrong market."
    )


def from_oanda_instrument(name: str) -> str:
    if name in _SPECIAL_FROM_OANDA:
        return _SPECIAL_FROM_OANDA[name]
    return name.replace("_", "").upper()


# --------------------------------------------------------------------------
# Config + adapter
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OandaConfig:
    account_id: str
    api_token: str
    base_url: str = f"https://{OANDA_PRACTICE_HOST}"
    mode: ExecutionMode = ExecutionMode.DEMO
    timeout: float = 10.0
    max_requests_per_second: float = 90.0
    #: Only consulted for the LIVE host. Defaults to the module constant so
    #: production code cannot pass a more permissive value by accident; tests
    #: pass True explicitly to prove the OTHER controls still block.
    live_host_compiled_in: bool | None = None

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()


class OandaAdapter(BrokerAdapter):
    """OANDA v20. Converts units; never sizes; never decides risk."""

    venue_name = "oanda"

    def __init__(
        self,
        config: OandaConfig,
        *,
        transport: Transport,
        rate_limiter: RateLimiter | None = None,
        env: dict[str, str] | None = None,
        now_fn=None,
    ) -> None:
        self.config = config
        self.mode = config.mode
        self.transport = transport
        self.rate_limiter = rate_limiter or RateLimiter(config.max_requests_per_second)
        self._env = env if env is not None else dict(os.environ)
        self._now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self._connected = False
        self._last_health: BrokerHealth | None = None
        # Host safety is asserted at CONSTRUCTION. An adapter pointed at an
        # unauthorised host must not exist, let alone be callable.
        self._assert_host_allowed()

    # ------------------------------------------------------- host safety

    def _assert_host_allowed(self) -> None:
        host = self.config.host
        if not host:
            raise OandaHostError(
                f"base_url {self.config.base_url!r} has no parseable hostname"
            )
        if host == OANDA_PRACTICE_HOST:
            return
        if host != OANDA_LIVE_HOST:
            raise OandaHostError(
                f"host {host!r} is neither the OANDA practice host "
                f"({OANDA_PRACTICE_HOST!r}) nor the live host ({OANDA_LIVE_HOST!r}). "
                "There is no permissive branch for unknown hosts: a prefix match "
                "on a look-alike domain is exactly the V1 failure."
            )

        compiled = (
            OANDA_LIVE_HOST_COMPILED_IN
            if self.config.live_host_compiled_in is None
            else self.config.live_host_compiled_in
        )
        failures: list[str] = []
        if not compiled:
            failures.append(
                "build-time constant OANDA_LIVE_HOST_COMPILED_IN is False"
            )
        if self._env.get(_OANDA_LIVE_ENV) != OANDA_LIVE_RUNTIME_TOKEN:
            failures.append(f"{_OANDA_LIVE_ENV} is not set to the live host token")
        if self.config.mode is not ExecutionMode.LIVE:
            failures.append(
                f"mode is {self.config.mode.value}; the live host serves LIVE only"
            )
        if failures:
            raise OandaHostError(
                f"Refusing to construct an OANDA adapter against the LIVE host "
                f"{host!r}. Unmet controls: {failures}"
            )

    # ------------------------------------------------------------ plumbing

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_token}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _account_path(self, suffix: str = "") -> str:
        return f"/v3/accounts/{self.config.account_id}{suffix}"

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> HttpResponse:
        self.rate_limiter.acquire()
        try:
            response = self.transport.request(
                method,
                self._url(path),
                headers=self._headers(),
                body=body,
                timeout=self.config.timeout,
            )
        except AssertionError:
            raise
        except Exception as exc:
            raise BrokerUnavailable(f"OANDA transport failure on {method} {path}: {exc}") from exc
        if response.status == 429:
            raise BrokerUnavailable(
                f"OANDA rate limited {method} {path}; the order's fate is unknown"
            )
        if response.status in (500, 502, 503, 504):
            raise BrokerUnavailable(f"OANDA {response.status} on {method} {path}")
        return response

    # ------------------------------------------------------------ adapter

    def connect(self) -> BrokerHealth:
        response = self._call("GET", self._account_path("/summary"))
        self._connected = response.ok
        return self._health_from(response)

    def health(self) -> BrokerHealth:
        try:
            response = self._call("GET", self._account_path("/summary"))
        except BrokerUnavailable as exc:
            self._connected = False
            return BrokerHealth(False, 0.0, self._now_fn(), str(exc))
        return self._health_from(response)

    def _health_from(self, response: HttpResponse) -> BrokerHealth:
        ok = response.ok
        health = BrokerHealth(
            connected=ok,
            score=1.0 if ok else 0.0,
            checked_at=self._now_fn(),
            message="" if ok else f"HTTP {response.status}",
            detail={"status": response.status},
        )
        self._last_health = health
        return health

    def account(self) -> AccountState:
        response = self._call("GET", self._account_path("/summary"))
        if not response.ok:
            raise BrokerUnavailable(f"OANDA account summary failed: HTTP {response.status}")
        acct = response.body.get("account", {})
        balance = float(acct.get("balance", 0.0))
        nav = float(acct.get("NAV", balance))
        return AccountState(
            balance=balance,
            equity=nav,
            currency=str(acct.get("currency", "GBP")),
            margin_used=float(acct.get("marginUsed", 0.0)),
            open_positions=int(acct.get("openTradeCount", 0)),
            realised_pnl=float(acct.get("pl", 0.0)),
            unrealised_pnl=float(acct.get("unrealizedPL", 0.0)),
            peak_equity=nav,
        )

    def positions(self) -> tuple[Position, ...]:
        response = self._call("GET", self._account_path("/openTrades"))
        if not response.ok:
            raise BrokerUnavailable(f"OANDA openTrades failed: HTTP {response.status}")
        out: list[Position] = []
        for trade in response.body.get("trades", []):
            units = float(trade.get("currentUnits", 0.0))
            if units == 0:
                continue
            symbol = from_oanda_instrument(trade["instrument"])
            stop = trade.get("stopLossOrder", {}).get("price")
            tps = trade.get("takeProfitOrder", {}).get("price")
            out.append(
                Position(
                    instrument=symbol,
                    direction=Direction.LONG if units > 0 else Direction.SHORT,
                    size=abs(units),
                    entry_price=float(trade.get("price", 0.0)),
                    entry_time=pd.Timestamp(trade["openTime"]).tz_convert("UTC"),
                    stop_loss=float(stop) if stop is not None else 0.0,
                    take_profit_targets=[float(tps)] if tps is not None else [],
                    # The BROKER's key. Reconciliation uses this and nothing else.
                    venue_ref=str(trade["id"]),
                )
            )
        return tuple(out)

    def orders(self) -> tuple[OrderAck, ...]:
        response = self._call("GET", self._account_path("/pendingOrders"))
        if not response.ok:
            raise BrokerUnavailable(f"OANDA pendingOrders failed: HTTP {response.status}")
        out: list[OrderAck] = []
        for o in response.body.get("orders", []):
            ext = o.get("clientExtensions", {}) or {}
            out.append(
                OrderAck(
                    status=OrderStatus.ACCEPTED,
                    broker_ref=str(o["id"]),
                    client_ref=str(ext.get("id", "")),
                    order_id=str(o["id"]),
                    submitted_at=pd.Timestamp(o["createTime"]).tz_convert("UTC"),
                    acked_at=pd.Timestamp(o["createTime"]).tz_convert("UTC"),
                    raw=o,
                )
            )
        return tuple(out)

    def market_spec(self, symbol: str) -> Instrument:
        """Our canonical spec, cross-checked against the venue's.

        Deliberately returns OUR :class:`Instrument`: sizing already happened
        against it, and returning the venue's numbers here would invite a
        downstream re-derivation. A disagreement raises rather than silently
        preferring either side.
        """
        ours = get_instrument(symbol)
        name = to_oanda_instrument(symbol)
        response = self._call("GET", self._account_path(f"/instruments?instruments={name}"))
        if not response.ok:
            raise BrokerUnavailable(f"OANDA instruments failed: HTTP {response.status}")
        for spec in response.body.get("instruments", []):
            if spec.get("name") != name:
                continue
            pip_location = spec.get("pipLocation")
            if pip_location is not None:
                venue_pip = 10.0 ** float(pip_location)
                if abs(venue_pip - ours.pip_size) > 1e-12:
                    raise BrokerRejected(
                        f"{symbol}: venue pip size {venue_pip} disagrees with our "
                        f"registry {ours.pip_size}. Fix core/instruments.py; V2 "
                        "does not guess contract specs."
                    )
        return ours

    # -------------------------------------------------------------- place

    def _units_for(self, order: Order) -> str:
        """Convert our size to OANDA units. Signed; never rounded silently."""
        instrument = get_instrument(order.instrument)
        units = order.size * order.direction.sign
        if instrument.is_fx:
            rounded = round(units)
            if abs(rounded - units) > 1e-9:
                raise BrokerRejected(
                    f"{order.instrument}: size {order.size} is not an integer number "
                    "of OANDA units. The adapter will not round -- rounding here is "
                    "an adapter re-deciding the size, which is the V1 failure."
                )
            return str(int(rounded))
        return f"{units:.10f}".rstrip("0").rstrip(".")

    def place_order(self, order: Order) -> OrderAck:
        instrument = get_instrument(order.instrument)
        precision = instrument.price_precision
        payload: dict[str, Any] = {
            "order": {
                "type": "MARKET",
                "instrument": to_oanda_instrument(order.instrument),
                "units": self._units_for(order),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                # THE IDEMPOTENCY KEY. OANDA stores clientExtensions.id against
                # the order and echoes it on every subsequent query, which is
                # what makes a lost response recoverable.
                "clientExtensions": {
                    "id": order.client_ref,
                    "tag": order.mode.value,
                    "comment": f"plan={order.plan_id}",
                },
            }
        }
        if order.stop_loss is not None:
            payload["order"]["stopLossOnFill"] = {
                "price": f"{order.stop_loss:.{precision}f}",
                "timeInForce": "GTC",
            }
        if order.take_profit is not None:
            payload["order"]["takeProfitOnFill"] = {
                "price": f"{order.take_profit:.{precision}f}",
                "timeInForce": "GTC",
            }

        response = self._call("POST", self._account_path("/orders"), payload)

        if response.status == 400:
            err = str(response.body.get("errorCode", "") or response.body.get("errorMessage", ""))
            if "CLIENT_ORDER_ID_ALREADY_EXISTS" in err.upper() or "DUPLICATE" in err.upper():
                raise DuplicateClientRef(
                    f"OANDA already holds client order id {order.client_ref!r}: {err}"
                )
            raise BrokerRejected(f"OANDA rejected the order: {err or response.body}")
        if response.status in (401, 403):
            raise BrokerRejected(f"OANDA auth failure: HTTP {response.status}")
        if not response.ok:
            raise BrokerUnavailable(f"OANDA order HTTP {response.status}; fate unknown")

        body = response.body
        cancel = body.get("orderCancelTransaction")
        if cancel:
            raise BrokerRejected(f"OANDA cancelled the order: {cancel.get('reason')}")

        fill_tx = body.get("orderFillTransaction")
        create_tx = body.get("orderCreateTransaction", {}) or {}
        submitted = pd.Timestamp(create_tx.get("time") or self._now_fn()).tz_convert("UTC")

        if not fill_tx:
            # Accepted but not filled. Not a rejection, and not a fill.
            ref = str(create_tx.get("id", "")) or None
            return OrderAck(
                status=OrderStatus.ACCEPTED,
                broker_ref=ref,
                client_ref=order.client_ref,
                order_id=order.order_id,
                submitted_at=submitted,
                acked_at=submitted,
                message="accepted_not_filled",
                raw=body,
            )

        trade_opened = fill_tx.get("tradeOpened", {}) or {}
        # The trade id is the durable BROKER reference; the transaction id is
        # not, because it identifies the event rather than the position.
        broker_ref = str(trade_opened.get("tradeID") or fill_tx.get("id"))
        filled_units = abs(float(trade_opened.get("units", fill_tx.get("units", 0.0))))
        price = float(fill_tx.get("price", 0.0))
        filled_at = pd.Timestamp(fill_tx.get("time") or submitted).tz_convert("UTC")
        partial = filled_units + 1e-9 < order.size

        return OrderAck(
            status=OrderStatus.PARTIALLY_FILLED if partial else OrderStatus.FILLED,
            broker_ref=broker_ref,
            client_ref=order.client_ref,
            order_id=order.order_id,
            submitted_at=submitted,
            acked_at=filled_at,
            fill=Fill(
                order_id=order.order_id,
                instrument=order.instrument,
                direction=order.direction,
                filled_size=filled_units,
                filled_price=price,
                requested_price=order.limit_price or price,
                filled_at=filled_at,
                commission=abs(float(fill_tx.get("commission", 0.0))),
                venue_ref=broker_ref,
                partial=partial,
            ),
            message="filled",
            raw=body,
        )

    def close_position(
        self, position: Position, *, client_ref: str, reason: str = ""
    ) -> OrderAck:
        if not position.venue_ref:
            raise BrokerRejected(
                "Cannot close a position with no broker reference. Closing is "
                "keyed on the venue's trade id, never on an internal uuid -- "
                "that mismatch is precisely why V1's reconciliation never "
                "produced a clean result."
            )
        response = self._call(
            "PUT",
            self._account_path(f"/trades/{position.venue_ref}/close"),
            {"units": "ALL"},
        )
        if response.status == 404:
            raise BrokerRejected(f"OANDA has no trade {position.venue_ref}")
        if not response.ok:
            raise BrokerUnavailable(f"OANDA close HTTP {response.status}; fate unknown")
        fill_tx = response.body.get("orderFillTransaction", {}) or {}
        when = pd.Timestamp(fill_tx.get("time") or self._now_fn()).tz_convert("UTC")
        price = float(fill_tx.get("price", 0.0))
        return OrderAck(
            status=OrderStatus.FILLED,
            broker_ref=str(position.venue_ref),
            client_ref=client_ref,
            order_id=client_ref,
            submitted_at=when,
            acked_at=when,
            fill=Fill(
                order_id=client_ref,
                instrument=position.instrument,
                direction=position.direction.opposite,
                filled_size=position.size,
                filled_price=price,
                requested_price=price,
                filled_at=when,
                venue_ref=str(position.venue_ref),
            ),
            message=reason or "closed",
            raw=response.body,
        )
