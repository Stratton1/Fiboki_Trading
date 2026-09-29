"""A LIVE spread source: OANDA v20 pricing, sampled at bar close (audit F, P2-4).

The defect this closes
----------------------
``RiskGateway._check_abnormal_spread`` blocks when the spread in the
:class:`~fiboki.risk.gateway.MarketView` exceeds ``max_spread_multiple`` times
the instrument's registered typical spread. With no quote stream,
:class:`~fiboki.workers.runtime.RiskContextBuilder` filled that spread from the
EXECUTION PROFILE -- the same static table the fill simulator charges -- so in
paper the check compared the profile with a multiple of itself and could never
fire (a 5.0x limit against a profile that tops out near 3x), and against a real
venue adapter it had no mid price and returned "unknown", which blocks
everything. Either way it measured nothing about the market.

This module measures it. :meth:`OandaPricingSpreadSource.sample` reads
``GET /v3/accounts/{id}/pricing`` for the instruments in play, once per bar,
right after the bar closes, and keeps the best bid, best ask, the venue's quote
time and its ``tradeable`` flag per instrument. Called as the builder's
``spread_source(instrument, now)`` it returns ``ask - bid`` in PRICE units, the
quantity the gateway divides by ``typical_spread_pips * pip_size``. The
"typical" side stays the registry's (``core/instruments.py``); the "current"
side is now the venue's.

Unknown is never zero
---------------------
The source returns ``nan`` -- which the builder turns into ``None`` and the
gateway blocks as ``spread_unknown`` -- when the instrument was never sampled,
when its newest quote is older than ``max_quote_age_s``, when the venue marked
it not ``tradeable``, or when either side of the book is missing or crossed.
A failed sample keeps the previous quotes, which then age out on their own: a
pricing outage becomes a refusal to open within ``max_quote_age_s``, not a
silent fall back to the profile.

Practice host only
------------------
The base URL is parsed and its hostname must equal
:data:`fiboki.broker.oanda.OANDA_PRACTICE_HOST`. The live host is a hard error
here, with no token or flag that changes that: this source serves the paper
forward path, which has no business reading a live account.

A read, retried as a read
-------------------------
The single request is a GET made in :meth:`_get_pricing`, the one method
decorated with :func:`~fiboki.broker.retry.retry_idempotent_read` (bounded
backoff on 429/5xx/transport failures, ``Retry-After`` honoured, a deadline).
Pass the adapter's or the feed's :class:`~fiboki.broker.oanda.RateLimiter` so
candles, pricing and any order share one per-account request budget.

Documented approximations
-------------------------
* One sample per bar, taken ``offset_s`` after the boundary. The spread at the
  moment of a later decision in the same cycle is assumed equal to it; the
  quote's own timestamp travels with it and ``max_quote_age_s`` bounds how
  stale that assumption may get.
* Top of book only (``bids[0]``/``asks[0]``). A large order walks the book;
  that cost is the fill model's business, not this check's.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import pandas as pd

from fiboki.broker.base import BrokerUnavailable
from fiboki.broker.oanda import (
    OANDA_PRACTICE_HOST,
    OandaHostError,
    RateLimiter,
    from_oanda_instrument,
    to_oanda_instrument,
)
from fiboki.broker.retry import ReadRetry, retry_idempotent_read
from fiboki.core.instruments import Instrument

__all__ = [
    "OandaPricingSpreadSource",
    "PricingSample",
    "Quote",
]


@dataclass(frozen=True, slots=True)
class Quote:
    """Top of book for one instrument, as the venue sent it."""

    instrument: str
    bid: float
    ask: float
    time: pd.Timestamp
    tradeable: bool

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return 0.5 * (self.ask + self.bid)


@dataclass
class PricingSample:
    """What one sample saw. Kept for the journal, the summary and tests."""

    at: pd.Timestamp
    requested: tuple[str, ...]
    quotes: dict[str, Quote] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.missing

    def as_row(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "requested": list(self.requested),
            "missing": list(self.missing),
            "error": self.error,
            "quotes": {
                sym: {
                    "bid": q.bid,
                    "ask": q.ask,
                    "spread": q.spread,
                    "time": q.time.isoformat(),
                    "tradeable": q.tradeable,
                }
                for sym, q in sorted(self.quotes.items())
            },
        }


def _unavailable(message: str, status: int, headers: Mapping[str, str] | None) -> BrokerUnavailable:
    exc = BrokerUnavailable(message)
    exc.status = status  # type: ignore[attr-defined]
    exc.retry_after = next(  # type: ignore[attr-defined]
        (v for k, v in (headers or {}).items() if k.lower() == "retry-after"), None
    )
    return exc


class OandaPricingSpreadSource:
    """``spread_source(instrument, now) -> spread in price units`` from OANDA pricing."""

    def __init__(
        self,
        *,
        transport: Any,
        account_id: str,
        api_token: str,
        base_url: str = f"https://{OANDA_PRACTICE_HOST}",
        timeout: float = 10.0,
        rate_limiter: RateLimiter | None = None,
        read_retry: ReadRetry | None = None,
        clock: Callable[[], pd.Timestamp] | None = None,
        max_quote_age_s: float = 120.0,
    ) -> None:
        host = (urlsplit(base_url).hostname or "").lower()
        if host != OANDA_PRACTICE_HOST:
            raise OandaHostError(
                f"the pricing spread source reads the OANDA PRACTICE host only; "
                f"{base_url!r} parses to {host!r}. The live host is refused here "
                "outright, whatever else is configured."
            )
        if not account_id:
            raise ValueError("an OANDA pricing read needs the practice account id")
        if not api_token:
            raise ValueError("an OANDA pricing read needs a token")
        if max_quote_age_s <= 0:
            raise ValueError("max_quote_age_s must be positive")
        self.transport = transport
        self.account_id = account_id
        self._token = api_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        #: Retry policy for the ONE GET. Its limiter is the shared account budget.
        self.read_retry = read_retry or ReadRetry(
            rate_limiter=rate_limiter or RateLimiter(), deadline_s=max(1.0, timeout)
        )
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self.max_quote_age_s = float(max_quote_age_s)
        self.quotes: dict[str, Quote] = {}
        self.samples: list[PricingSample] = []

    def __repr__(self) -> str:  # never the token
        return (
            f"OandaPricingSpreadSource(host={urlsplit(self.base_url).hostname!r}, "
            f"quotes={sorted(self.quotes)!r})"
        )

    # -- the read ----------------------------------------------------------

    @retry_idempotent_read
    def _get_pricing(self, url: str) -> Mapping[str, Any]:
        """The ONE request this class makes. A GET, and the only retried method."""
        response = self.transport.request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept-Datetime-Format": "RFC3339",
            },
            timeout=self.timeout,
        )
        status = int(response.status)
        if status == 429 or status >= 500:
            raise _unavailable(f"OANDA pricing HTTP {status}", status, response.headers)
        if not 200 <= status < 300:
            # A 401/403/404 is the venue saying no. Not retried (status decides).
            raise _unavailable(
                f"OANDA pricing refused: HTTP {status} {str(response.body)[:200]}",
                status,
                response.headers,
            )
        return response.body

    def _url(self, instruments: Iterable[str]) -> str:
        names = ",".join(to_oanda_instrument(s) for s in instruments)
        return (
            f"{self.base_url}/v3/accounts/{quote(self.account_id, safe='')}/pricing"
            f"?instruments={quote(names, safe=',_')}"
        )

    def sample(self, instruments: Iterable[str]) -> PricingSample:
        """Read top of book for ``instruments``. Never raises; the sample says what failed."""
        symbols = tuple(sorted({s.upper() for s in instruments}))
        report = PricingSample(at=self.clock(), requested=symbols)
        if not symbols:
            self.samples.append(report)
            return report
        try:
            body = self._get_pricing(self._url(symbols))
        except Exception as exc:  # retries exhausted, or a refusal
            report.error = f"{type(exc).__name__}: {exc}"[:500]
            report.missing = symbols
            self.samples.append(report)
            return report
        for raw in body.get("prices", []) or []:
            parsed = self._parse(raw)
            if parsed is not None and parsed.instrument in symbols:
                report.quotes[parsed.instrument] = parsed
                self.quotes[parsed.instrument] = parsed
        report.missing = tuple(s for s in symbols if s not in report.quotes)
        self.samples.append(report)
        return report

    @staticmethod
    def _parse(raw: Mapping[str, Any]) -> Quote | None:
        try:
            symbol = from_oanda_instrument(str(raw["instrument"]))
            bids = raw.get("bids") or []
            asks = raw.get("asks") or []
            if not bids or not asks:
                return None
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            when = pd.Timestamp(raw["time"])
            when = when.tz_localize("UTC") if when.tzinfo is None else when.tz_convert("UTC")
        except (KeyError, TypeError, ValueError, IndexError):
            return None
        tradeable = bool(raw.get("tradeable", raw.get("status") == "tradeable"))
        return Quote(symbol, bid, ask, when, tradeable)

    # -- the builder's spread_source ---------------------------------------

    def quote_for(self, symbol: str, now: pd.Timestamp | None = None) -> Quote | None:
        """The newest usable quote, or ``None`` (never sampled, stale, halted, crossed)."""
        q = self.quotes.get(symbol.upper())
        if q is None or not q.tradeable:
            return None
        if not (math.isfinite(q.bid) and math.isfinite(q.ask)) or q.ask < q.bid or q.bid <= 0:
            return None
        stamp = self.clock() if now is None else now
        if (stamp - q.time).total_seconds() > self.max_quote_age_s:
            return None
        return q

    def __call__(self, instrument: Instrument, now: pd.Timestamp) -> float:
        q = self.quote_for(instrument.symbol, now)
        return float("nan") if q is None else float(q.spread)

    def spread_multiple(self, instrument: Instrument, now: pd.Timestamp) -> float | None:
        """Current / typical, the number ``abnormal_spread`` compares. For display."""
        q = self.quote_for(instrument.symbol, now)
        typical = instrument.typical_spread_pips * instrument.pip_size
        if q is None or typical <= 0:
            return None
        return q.spread / typical
