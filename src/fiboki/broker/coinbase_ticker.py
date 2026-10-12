"""A LIVE spread source for spot crypto: Coinbase Exchange public ticker, top of book.

The crypto counterpart of :mod:`fiboki.broker.oanda_pricing`, for the paper
forward crypto wiring. Once per bar, right after the feed polls,
:meth:`CoinbaseTickerSpreadSource.sample` reads
``GET https://api.exchange.coinbase.com/products/{product_id}/ticker`` (public,
no key) for each instrument in play and keeps the best bid and best ask. Called
as the builder's ``spread_source(instrument, now)`` it returns ``ask - bid`` in
price units; the gateway's ``abnormal_spread`` divides it by the instrument's
typical spread in bps of the current mid (``Instrument.typical_spread_bps``).

It READS. There is no order endpoint here, no credential, and the transport it
is given is built in the composition root with the public Exchange host as its
only allowed host.

Unknown is never zero
---------------------
``nan`` (which the gateway blocks as ``spread_unknown``) when an instrument was
never sampled, its newest sample is older than ``max_quote_age_s``, either side
is missing, non-positive or crossed. A failed sample keeps the previous quotes,
which then age out: an outage becomes a refusal to open, never a fall-back to
the profile's own spread.

Documented approximations
-------------------------
* The ticker's ``time`` is the LAST TRADE's time, not the book's. The quote is
  stamped with the time it was RECEIVED (this source's clock), which is when
  the top of book was observed; the last-trade time travels in the journal row.
* One sample per bar; top of book only, as for OANDA.
* One request per product (the public ticker has no batch form); with two to
  six products and one sample per bar that is far inside the 10/s limit.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import quote, urlsplit

import pandas as pd

from fiboki.broker.base import BrokerUnavailable
from fiboki.broker.oanda_pricing import PricingSample, Quote
from fiboki.broker.retry import ReadRetry, retry_idempotent_read
from fiboki.core.instruments import Instrument, coinbase_product_for

__all__ = ["COINBASE_TICKER_HOST", "CoinbaseTickerSpreadSource"]

COINBASE_TICKER_HOST = "api.exchange.coinbase.com"


def _unavailable(message: str, status: int, headers: Mapping[str, str] | None) -> BrokerUnavailable:
    exc = BrokerUnavailable(message)
    exc.status = status  # type: ignore[attr-defined]
    exc.retry_after = next(  # type: ignore[attr-defined]
        (v for k, v in (headers or {}).items() if k.lower() == "retry-after"), None
    )
    return exc


class CoinbaseTickerSpreadSource:
    """``spread_source(instrument, now) -> spread in price units`` from the public ticker."""

    def __init__(
        self,
        *,
        transport: Any,
        base_url: str = f"https://{COINBASE_TICKER_HOST}",
        timeout: float = 10.0,
        read_retry: ReadRetry | None = None,
        clock: Callable[[], pd.Timestamp] | None = None,
        max_quote_age_s: float = 120.0,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "https" or (parts.hostname or "").lower() != COINBASE_TICKER_HOST:
            raise ValueError(
                f"the crypto spread source reads https://{COINBASE_TICKER_HOST} only; "
                f"{base_url!r} parses to {parts.scheme}://{parts.hostname}"
            )
        if max_quote_age_s <= 0:
            raise ValueError("max_quote_age_s must be positive")
        self.transport = transport
        self.base_url = f"https://{COINBASE_TICKER_HOST}"
        self.timeout = timeout
        self.read_retry = read_retry or ReadRetry(deadline_s=max(1.0, timeout))
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self.max_quote_age_s = float(max_quote_age_s)
        self.quotes: dict[str, Quote] = {}
        self.last_trade_time: dict[str, str] = {}
        self.samples: list[PricingSample] = []

    def __repr__(self) -> str:
        return f"CoinbaseTickerSpreadSource(quotes={sorted(self.quotes)!r})"

    @retry_idempotent_read
    def _get_ticker(self, url: str) -> Mapping[str, Any]:
        """The ONE request kind this class makes: a public GET, retried as a read."""
        response = self.transport.request(
            "GET", url, headers={"Accept": "application/json"}, timeout=self.timeout
        )
        status = int(response.status)
        if status == 429 or status >= 500:
            raise _unavailable(f"Coinbase ticker HTTP {status}", status, response.headers)
        if not 200 <= status < 300:
            raise _unavailable(
                f"Coinbase ticker refused: HTTP {status} {str(response.body)[:200]}",
                status, response.headers,
            )
        return response.body

    def _url(self, symbol: str) -> str:
        return f"{self.base_url}/products/{quote(coinbase_product_for(symbol), safe='-')}/ticker"

    def sample(self, instruments: Iterable[str]) -> PricingSample:
        """Read top of book for ``instruments``. Never raises; the sample says what failed."""
        symbols = tuple(sorted({s.upper() for s in instruments}))
        report = PricingSample(at=self.clock(), requested=symbols)
        errors: list[str] = []
        for symbol in symbols:
            try:
                body = self._get_ticker(self._url(symbol))
            except Exception as exc:  # retries exhausted, or a refusal
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}"[:200])
                continue
            parsed = self._parse(symbol, body, self.clock())
            if parsed is not None:
                report.quotes[symbol] = parsed
                self.quotes[symbol] = parsed
                self.last_trade_time[symbol] = str(body.get("time", ""))
        report.missing = tuple(s for s in symbols if s not in report.quotes)
        report.error = "; ".join(errors)[:500]
        self.samples.append(report)
        return report

    @staticmethod
    def _parse(symbol: str, body: Mapping[str, Any], received: pd.Timestamp) -> Quote | None:
        try:
            bid = float(body["bid"])
            ask = float(body["ask"])
        except (KeyError, TypeError, ValueError):
            return None
        return Quote(symbol, bid, ask, received, True)

    def quote_for(self, symbol: str, now: pd.Timestamp | None = None) -> Quote | None:
        q = self.quotes.get(symbol.upper())
        if q is None:
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
        """Current / typical (both in bps of the quote's mid). For display."""
        q = self.quote_for(instrument.symbol, now)
        if q is None or not instrument.typical_spread_bps or q.mid <= 0:
            return None
        return (q.spread / q.mid * 1e4) / instrument.typical_spread_bps
