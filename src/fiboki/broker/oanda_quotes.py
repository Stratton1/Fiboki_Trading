"""OANDA practice pricing as a :class:`~fiboki.data.recorder.QuoteFeed` (USER_ACTIONS P2).

Why
---
Every backtest charges a static spread table. The quotes a live account would
have traded against are the one dataset that cannot be reconstructed later, so
the recorder (:mod:`fiboki.data.recorder`) was built before any broker existed
and waited for a real source. This is that source: it polls
``GET /v3/accounts/{id}/pricing`` on the PRACTICE host through
:class:`~fiboki.broker.oanda_pricing.OandaPricingSpreadSource` (the same client,
host assertion, retry policy and rate limiter the paper-forward worker uses)
and yields one :class:`~fiboki.data.recorder.QuoteRecord` per instrument each
time the venue's quote changes.

What it is not
--------------
* Not a stream. OANDA also offers a streaming endpoint; a poll every
  ``interval_s`` (default 30 s) is what is built, because the purpose is an
  hour-of-week spread profile and a measured gap between candle and executable
  prices, not tick data. Changes between polls are not seen; the record says so
  through its timestamps.
* Not an order path. Practice host only, a read only; nothing here can place,
  amend or cancel anything. The token is read once and never logged.

Records
-------
``timestamp`` is the VENUE's quote time; ``latency_ms`` is how long after that
this process received it (a poll measure, not network latency).
``market_state`` is OPEN when OANDA marks the instrument ``tradeable`` and
CLOSED otherwise; a non-tradeable quote is still recorded (weekend and rollover
spreads are part of what is being measured). An unchanged quote (same venue
time, bid and ask) is not recorded twice. A failed poll records nothing and is
reported through ``on_poll``; the loop carries on.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from fiboki.broker.oanda_pricing import OandaPricingSpreadSource, PricingSample
from fiboki.data.recorder import MarketState, QuoteFeed, QuoteRecord

__all__ = [
    "DEFAULT_RECORDED_INSTRUMENTS",
    "OandaPricingQuoteFeed",
    "PollReport",
]

#: The research universe's series (K5) plus the GBP crosses its FX conversion
#: reads: what a measured spread profile is first needed for.
DEFAULT_RECORDED_INSTRUMENTS: tuple[str, ...] = (
    "AUDJPY", "AUDUSD", "DE40", "EURGBP", "EURJPY", "EURUSD", "GBPAUD", "GBPCAD",
    "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "NZDUSD", "UK100", "US500", "USDCAD",
    "USDCHF", "USDJPY", "XAGUSD", "XAUUSD",
)

PROVIDER = "oanda_practice_pricing"


@dataclass
class PollReport:
    """What one poll did. Passed to ``on_poll``; never contains the token."""

    at: pd.Timestamp
    requested: int
    received: int
    recorded: int
    missing: tuple[str, ...] = ()
    error: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "requested": self.requested,
            "received": self.received,
            "recorded": self.recorded,
            "missing": list(self.missing),
            "error": self.error,
        }


@dataclass
class OandaPricingQuoteFeed(QuoteFeed):
    """Polls OANDA practice pricing and yields changed quotes as ``QuoteRecord``."""

    source: OandaPricingSpreadSource
    interval_s: float = 30.0
    sleeper: Callable[[float], None] = field(default=lambda s: None)
    should_stop: Callable[[], bool] = field(default=lambda: False)
    on_poll: Callable[[PollReport], None] | None = None
    name: str = PROVIDER
    _last: dict[str, tuple[pd.Timestamp, float, float]] = field(default_factory=dict)
    _sequence: int = 0

    def __post_init__(self) -> None:
        if self.interval_s < 5.0:
            raise ValueError(
                "interval_s below 5 s is not needed for a spread profile and spends the "
                "account's shared request budget; refusing"
            )

    def poll(self, instruments: list[str]) -> tuple[list[QuoteRecord], PollReport]:
        """One pricing read. Returns the records to write and a report."""
        sample: PricingSample = self.source.sample(instruments)
        received_at = self.source.clock()
        records: list[QuoteRecord] = []
        for symbol, quote in sorted(sample.quotes.items()):
            key = (quote.time, quote.bid, quote.ask)
            if self._last.get(symbol) == key:
                continue
            self._last[symbol] = key
            latency = (received_at - quote.time).total_seconds() * 1000.0
            records.append(
                QuoteRecord(
                    # datetime holds microseconds; OANDA sends nanoseconds. Truncate
                    # explicitly (the dedupe key above keeps the full value).
                    timestamp=quote.time.floor("us").to_pydatetime(),
                    instrument=symbol,
                    bid=quote.bid,
                    ask=quote.ask,
                    provider=self.name,
                    market_state=MarketState.OPEN if quote.tradeable else MarketState.CLOSED,
                    session_open=bool(quote.tradeable),
                    latency_ms=round(max(latency, 0.0), 3),
                    broker_status="tradeable" if quote.tradeable else "not_tradeable",
                    sequence=self._sequence,
                )
            )
            self._sequence += 1
        report = PollReport(
            at=sample.at,
            requested=len(sample.requested),
            received=len(sample.quotes),
            recorded=len(records),
            missing=sample.missing,
            error=sample.error,
        )
        return records, report

    def stream(self, instruments: list[str], *, limit: int | None = None) -> Iterator[QuoteRecord]:
        emitted = 0
        while not self.should_stop():
            records, report = self.poll(instruments)
            if self.on_poll is not None:
                self.on_poll(report)
            for record in records:
                yield record
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
            if self.should_stop():
                return
            self.sleeper(self.interval_s)
