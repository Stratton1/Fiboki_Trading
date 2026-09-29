"""Live bar feeds: the thing that pulls bars for a worker from a venue.

``workers/live_worker.py`` declared :class:`~fiboki.workers.live_worker.BarFeed`
as a protocol and ``workers/runtime.py`` said, in its own table, that nothing
implemented it for a venue. :class:`OandaPollingBarFeed` is that implementation
for OANDA v20 candles. It is a POLLER, deliberately: a streaming price feed is
a tick source, and Fiboki's strategies are defined on closed bars.

What it does, in order, every time a bar boundary passes
--------------------------------------------------------
1. **Wakes at the boundary plus an offset** (default 5 s) and never inside the
   window between the two. A candle requested at 10:00:00.2 is, at most
   venues, still the forming one; asking again at 10:00:05 is cheaper than
   reasoning about it. :meth:`wait_until_due` does the waiting and bounds any
   single call to ``max_block_s`` so the worker's loop still renews its lease
   and writes a heartbeat while an H4 bar is forming.
2. **Fetches** through the existing :class:`~fiboki.data.providers.oanda.
   OandaCandlesProvider`, whose parser drops every ``complete: false`` candle.
   The venue's ``complete`` flag is AUTHORITATIVE: this module never decides
   that a candle is closed because the clock says it should be.
3. **Uses the clock only as a second check.** If the bar that should have
   closed at the boundary is still absent (or still ``complete: false``), the
   missing instruments are re-polled up to ``repoll_attempts`` times,
   ``repoll_interval_s`` apart. If the bar is still missing once its close is
   at least ``late_candle_grace_s`` in the past, a ``DATA_QUALITY_DEFECT``
   alert is raised and the instrument is simply NOT evaluated this cycle.
4. **Never gap-fills.** A missing bar is missing. No bar is synthesised,
   forward-filled or carried; the history holds exactly what the venue sent.
5. **Consults** :mod:`fiboki.data.calendars`. An instrument whose session was
   closed for the whole of the bar that just ended is not fetched, is not
   "late", and is reported in ``BarBatch.market_closed`` so a Friday bar on a
   Sunday reads as "closed", not "stale".
6. **Reports** per-instrument last-bar age into the batch (for the freshness
   metric and ``DATA_STALE``) and, through :meth:`attach`, the bar CLOSE time
   and close price into :class:`~fiboki.workers.runtime.RiskContextBuilder`,
   which is what the gateway's ``data_freshness`` and ``stale_price`` checks
   read. The feed does NOT decide what is too old to trade; the gateway does,
   against its own limit set, and this module only supplies the timestamp.

Which bar is delivered
----------------------
Each poll delivers, per instrument, the NEWEST complete bar not delivered
before. If the worker was down for three bars, the three are appended to the
history but only the newest is offered for evaluation, and the gateway's
freshness check decides whether it is still tradeable. Evaluating the older
ones late would be a signal computed on information the market has already
moved past.

What it is not
--------------
Not a quote stream. ``stale_price`` is fed the bar's close time because there
is no separate tick timestamp; the same approximation
:class:`~fiboki.workers.runtime.RiskContextBuilder` documents for paper.
Not a spread source: the venue's quoted spread reaches the builder's
``spread_source`` from :class:`fiboki.broker.oanda_pricing.OandaPricingSpreadSource`,
sampled once per bar right after this feed polls (the paper-forward entrypoint,
``fiboki/entrypoints/paper_forward.py``, does both in that order).

Pattern after freqtrade ``worker.py`` (boundary-aligned throttling with a
post-candle offset) and ``exchange/exchange.py`` (dropping the forming candle
on the venue's own finality flag) (GPL-3.0, not copied).

Determinism and tests
---------------------
The clock, the sleep and the retry policy are injected. With a fake clock whose
sleep advances it, and :class:`~fiboki.broker.oanda.RecordedTransport` behind
:class:`TransportHttpClient`, every test of this module is instant and never
touches the network.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from fiboki.broker.oanda import RateLimiter
from fiboki.broker.retry import ReadRetry, retry_idempotent_read
from fiboki.core.enums import Timeframe
from fiboki.data import calendars as _calendars
from fiboki.data.providers.base import AuthenticationRequired, ProviderError
from fiboki.obs import metrics as _metrics
from fiboki.obs.alerts import AlertEvent
from fiboki.obs.logging import get_logger
from fiboki.sim.fills import Bar
from fiboki.workers.base import log_once
from fiboki.workers.live_worker import BarBatch

__all__ = [
    "LATE_CANDLES",
    "CandleHttpError",
    "OandaPollingBarFeed",
    "PollReport",
    "PollingFeedConfig",
    "TransportHttpClient",
]

_log = get_logger("fiboki.workers.feeds")

#: Candles that were still absent (or still ``complete: false``) after the
#: grace window. The alert says which; the counter says how often.
LATE_CANDLES = _metrics.REGISTRY.counter(
    "fiboki_feed_late_candles_total",
    "Closed-bar candles still absent or incomplete after the feed's grace window",
    ("instrument", "timeframe"),
)


# ---------------------------------------------------------------------------
# Transport glue: the broker Transport, spoken to as an HTTP client
# ---------------------------------------------------------------------------


class CandleHttpError(RuntimeError):
    """A non-2xx candle response. Carries the status so retry can classify it."""

    def __init__(
        self,
        status: int,
        url: str,
        *,
        retry_after: Any = None,
        body: Any = None,
    ) -> None:
        super().__init__(f"HTTP {status} on GET {url}: {body!r}"[:500])
        self.status = status
        self.retry_after = retry_after
        self.body = body


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    body: Any
    headers: Mapping[str, str]
    url: str

    def json(self) -> Any:
        return self.body

    def raise_for_status(self) -> None:
        if 200 <= self.status_code < 300:
            return
        retry_after = next(
            (v for k, v in self.headers.items() if k.lower() == "retry-after"), None
        )
        raise CandleHttpError(
            self.status_code, self.url, retry_after=retry_after, body=self.body
        )


class TransportHttpClient:
    """Lets the candle provider speak through a :class:`~fiboki.broker.oanda.Transport`.

    The provider expects an ``httpx``-shaped ``get(url, params=, headers=)``;
    the broker side of the codebase speaks ``Transport.request``. Bridging the
    two here means one transport (and one recorded-fixture double) serves both
    the adapter and the feed, and a test never needs a second fake.
    """

    def __init__(self, transport: Any, *, timeout: float = 10.0) -> None:
        self.transport = transport
        self.timeout = timeout

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _Response:
        full = f"{url}?{urlencode(dict(params))}" if params else url
        response = self.transport.request(
            "GET", full, headers=dict(headers or {}), timeout=self.timeout
        )
        return _Response(
            status_code=int(response.status),
            body=response.body,
            headers=dict(getattr(response, "headers", {}) or {}),
            url=full,
        )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PollingFeedConfig:
    instruments: tuple[str, ...]
    timeframe: Timeframe
    #: Seconds after the boundary before the first fetch. Never fetches inside
    #: ``[boundary, boundary + offset)``.
    offset_s: float = 5.0
    #: A candle whose close is at least this far in the past and is still
    #: absent or incomplete raises ``DATA_QUALITY_DEFECT``.
    late_candle_grace_s: float = 20.0
    #: Re-polls of a missing candle after the first fetch, and their spacing.
    repoll_attempts: int = 3
    repoll_interval_s: float = 5.0
    #: Longest a single :meth:`OandaPollingBarFeed.wait_until_due` call blocks.
    #: Must sit well inside the worker's lease TTL and the watchdog's stale
    #: threshold (90 s and 120 s by default).
    max_block_s: float = 30.0
    #: Candles requested on the first fetch of an instrument.
    warmup_bars: int = 500
    #: Candles kept per instrument.
    history_bars: int = 5000
    #: v20's per-request ceiling.
    max_request_bars: int = 5000

    def __post_init__(self) -> None:
        if not self.instruments:
            raise ValueError("a polling feed needs at least one instrument")
        span = self.timeframe.minutes * 60.0
        if not 0.0 <= self.offset_s < span:
            raise ValueError(f"offset_s must be in [0, {span}) for {self.timeframe.value}")
        if self.repoll_attempts < 0 or self.repoll_interval_s < 0:
            raise ValueError("repoll settings must be non-negative")
        if self.late_candle_grace_s < 0:
            raise ValueError("late_candle_grace_s must be non-negative")
        if self.max_block_s <= 0:
            raise ValueError("max_block_s must be positive")
        if not 2 <= self.warmup_bars <= self.max_request_bars:
            raise ValueError("warmup_bars must be between 2 and max_request_bars")
        if self.history_bars < self.warmup_bars:
            raise ValueError("history_bars must be at least warmup_bars")


@dataclass
class PollReport:
    """What one poll saw. Kept on the feed for the worker summary and tests."""

    at: pd.Timestamp
    boundary: pd.Timestamp
    expected_start: pd.Timestamp
    fetches: int = 0
    repolls: int = 0
    delivered: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    late: tuple[str, ...] = ()
    closed_market: tuple[str, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The feed
# ---------------------------------------------------------------------------


class OandaPollingBarFeed:
    """Boundary-aligned OANDA v20 candle poller. Implements ``BarFeed``."""

    def __init__(
        self,
        provider: Any,
        config: PollingFeedConfig,
        *,
        clock: Callable[[], pd.Timestamp] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        read_retry: ReadRetry | None = None,
        rate_limiter: Any = None,
        dispatcher: Any = None,
        calendar_for: Callable[[str], Any] = _calendars.calendar_for,
        source: str = "oanda-feed",
    ) -> None:
        self.provider = provider
        self.config = config
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self.sleeper = sleeper
        # The candle fetch is an idempotent GET: retried with backoff through
        # the account's rate limiter. Pass the ADAPTER's limiter in production
        # so candles and orders share one request budget.
        self.read_retry = read_retry or ReadRetry(
            rate_limiter=rate_limiter or RateLimiter()
        )
        self._fetch = retry_idempotent_read(retry=self.read_retry)(provider.fetch_bars)
        self.dispatcher = dispatcher
        self.source = source
        # Raises KeyError for an unregistered symbol: trading hours are not
        # guessed any more than contract specs are.
        self.calendars = {sym: calendar_for(sym) for sym in config.instruments}
        self.timeframe = config.timeframe.value
        self._tf = pd.Timedelta(minutes=config.timeframe.minutes)
        self._last_boundary: pd.Timestamp | None = None
        self._history: dict[str, pd.DataFrame] = {}
        self._delivered: dict[str, pd.Timestamp] = {}
        self._observers: list[Any] = []
        self.reports: list[PollReport] = []
        self.polls = 0

    # -- composition ------------------------------------------------------

    def attach(self, builder: Any) -> OandaPollingBarFeed:
        """Wire this feed into a :class:`RiskContextBuilder` (or anything alike).

        Sets the builder's ``market_open_source`` to the session calendar and
        registers it to receive bar close times and prices after every poll.
        The builder then owns the freshness INPUT and the gateway owns the
        freshness CHECK; this feed owns neither the threshold nor the decision.
        """
        if hasattr(builder, "market_open_source"):
            builder.market_open_source = self.is_market_open
        self._observers.append(builder)
        return self

    def history(self, symbol: str) -> pd.DataFrame:
        """Every complete bar held for ``symbol``, oldest first. A copy."""
        frame = self._history.get(symbol)
        if frame is None:
            raise KeyError(
                f"no bars held for {symbol!r} yet; the feed has not polled it. "
                "An empty frame here would be indistinguishable from an "
                "instrument with no history."
            )
        return frame.copy()

    # -- calendar ---------------------------------------------------------

    def is_market_open(self, symbol: str, ts: pd.Timestamp) -> bool:
        return bool(self.calendars[symbol].is_open(ts))

    def _bar_expected(self, symbol: str, start: pd.Timestamp) -> bool:
        """Was the session open at any point during ``[start, start + tf)``?"""
        end = start + self._tf
        step = min(self._tf, pd.Timedelta(minutes=15))
        probes = list(pd.date_range(start, end, freq=step, inclusive="left"))
        probes.append(end - pd.Timedelta(seconds=1))
        return any(self.is_market_open(symbol, t) for t in probes)

    # -- timing -----------------------------------------------------------

    def _boundary(self, now: pd.Timestamp) -> pd.Timestamp:
        # Epoch-anchored, which is how every stored research frame is anchored
        # (data/resample.py) and what the provider asks v20 for (align_utc).
        return now.floor(self._tf)

    def next_due(self, now: pd.Timestamp | None = None) -> pd.Timestamp:
        """When the next poll may run: a boundary plus the offset."""
        now = self.clock() if now is None else now
        base = (
            self._boundary(now)
            if self._last_boundary is None
            else self._last_boundary + self._tf
        )
        return base + pd.Timedelta(seconds=self.config.offset_s)

    def wait_until_due(self, should_stop: Callable[[], bool] | None = None) -> bool:
        """Block until the next poll is due, for at most ``max_block_s``.

        True: due now, poll. False: not yet (or a stop was requested); the
        caller should return to its loop, beat, and ask again. Sleeps in slices
        of at most one second so a stop request is honoured promptly.
        """
        now = self.clock()
        limit = now + pd.Timedelta(seconds=self.config.max_block_s)
        # Guards against a sleeper that does not advance the clock.
        for _ in range(int(self.config.max_block_s) + 3):
            due = self.next_due(now)
            if now >= due:
                return True
            if should_stop is not None and should_stop():
                return False
            if now >= limit:
                return False
            self.sleeper(
                max(0.0, min((due - now).total_seconds(), (limit - now).total_seconds(), 1.0))
            )
            now = self.clock()
        return now >= self.next_due(now)

    # -- the poll ---------------------------------------------------------

    def poll(self) -> BarBatch:
        self.polls += 1
        now = self.clock()
        boundary = self._boundary(now)
        offset = pd.Timedelta(seconds=self.config.offset_s)
        if now < boundary + offset:
            # Called directly without wait_until_due: still never fetch inside
            # the offset window. Bounded by offset_s.
            self.sleeper((boundary + offset - now).total_seconds())
            now = self.clock()
            boundary = self._boundary(now)

        previous_boundary = self._last_boundary
        self._last_boundary = boundary
        expected_start = boundary - self._tf
        report = PollReport(at=now, boundary=boundary, expected_start=expected_start)

        pending: list[str] = []
        for sym in self.config.instruments:
            if not self._bar_expected(sym, expected_start) and sym in self._history:
                # The session was shut for the whole bar: nothing new can
                # exist, and not fetching is not the same as missing.
                continue
            pending.append(sym)

        first_round = tuple(pending)
        incomplete_seen: dict[str, int] = {}
        for attempt in range(self.config.repoll_attempts + 1):
            if attempt:
                report.repolls += 1
                self.sleeper(self.config.repoll_interval_s)
            still_missing: list[str] = []
            for sym in pending:
                outcome, incomplete = self._fetch_into_history(sym, expected_start, report)
                incomplete_seen[sym] = incomplete_seen.get(sym, 0) + incomplete
                if outcome != "ok" and self._bar_expected(sym, expected_start):
                    still_missing.append(sym)
            if attempt == 0 and first_round and all(sym in report.errors for sym in first_round):
                # Nothing answered at all: an outage, not a late candle. Re-arm
                # this boundary so the next cycle retries it, and raise so the
                # worker loop records a FAILED cycle on the heartbeat rather
                # than an idle one that looks like a quiet market.
                self._last_boundary = previous_boundary
                raise ProviderError(
                    "every candle fetch failed: "
                    + "; ".join(f"{k}: {v}" for k, v in sorted(report.errors.items()))
                )
            pending = still_missing
            if not pending:
                break

        after = self.clock()
        missing = sorted(pending)
        report.missing = tuple(missing)
        late: list[str] = []
        lateness = (after - boundary).total_seconds()
        for sym in missing:
            if lateness >= self.config.late_candle_grace_s:
                late.append(sym)
                self._late_candle(
                    sym,
                    expected_start,
                    lateness,
                    incomplete_seen.get(sym, 0),
                    report.errors.get(sym, ""),
                )
        report.late = tuple(late)

        batch = self._batch(after, report)
        self.reports.append(report)
        return batch

    def _fetch_into_history(
        self, symbol: str, expected_start: pd.Timestamp, report: PollReport
    ) -> tuple[str, int]:
        """Fetch and merge. Returns ``("ok"|"absent"|"error", incomplete_dropped)``."""
        held = self._history.get(symbol)
        if held is None or held.empty:
            count = self.config.warmup_bars
        else:
            behind = int((expected_start - held.index[-1]) / self._tf)
            count = min(self.config.max_request_bars, max(2, behind + 2))
        report.fetches += 1
        try:
            fetched = self._fetch(symbol, self.config.timeframe, count=count)
        except AuthenticationRequired:
            raise
        except ProviderError as exc:
            # Includes "no complete candles in this response": an answer, with
            # nothing closed in it. Treated as absent and re-polled.
            report.errors.pop(symbol, None)
            if log_once(f"feed_provider_error:{symbol}", 300):
                _log.warning(
                    "candle fetch returned nothing usable",
                    extra={"instrument": symbol, "error": str(exc)},
                )
            return "absent", 0
        except Exception as exc:  # retries already exhausted
            report.errors[symbol] = f"{type(exc).__name__}: {exc}"
            _log.error(
                "candle fetch failed after retries",
                extra={"instrument": symbol, "error": report.errors[symbol]},
            )
            return "error", 0
        report.errors.pop(symbol, None)
        incomplete = int((getattr(fetched, "extra", {}) or {}).get("incomplete_dropped", 0))
        frame = fetched.frame
        merged = frame if held is None else pd.concat([held, frame])
        # keep="first": a bar already held is the bar that may already have been
        # evaluated. A venue that later sends a different "complete" candle for
        # the same stamp does not get to rewrite it silently.
        merged = merged[~merged.index.duplicated(keep="first")].sort_index()
        self._history[symbol] = merged.iloc[-self.config.history_bars :]
        newest = self._history[symbol].index[-1]
        return ("ok" if newest >= expected_start else "absent"), incomplete

    def _late_candle(
        self,
        symbol: str,
        expected_start: pd.Timestamp,
        lateness: float,
        incomplete: int,
        error: str,
    ) -> None:
        state = "still complete:false" if incomplete else "absent"
        if error:
            state = f"unfetchable ({error})"
        message = (
            f"{symbol} {self.timeframe} candle {expected_start.isoformat()} closed "
            f"{lateness:.0f}s ago and is {state} after "
            f"{self.config.repoll_attempts} re-poll(s). It is NOT gap-filled and "
            f"{symbol} is not evaluated this bar."
        )
        LATE_CANDLES.inc(instrument=symbol, timeframe=self.timeframe)
        if log_once(f"late_candle:{symbol}", 300):
            _log.warning(message, extra={"instrument": symbol, "lateness_s": lateness})
        if self.dispatcher is not None:
            self.dispatcher.fire(
                AlertEvent.DATA_QUALITY_DEFECT,
                message,
                source=self.source,
                dedupe_key=f"late_candle:{symbol}:{self.timeframe}",
                instrument=symbol,
                timeframe=self.timeframe,
                expected_start=expected_start.isoformat(),
                lateness_s=round(lateness, 3),
                incomplete_seen=incomplete,
            )

    def _batch(self, now: pd.Timestamp, report: PollReport) -> BarBatch:
        frames: dict[str, Bar] = {}
        closed: set[str] = set()
        ages: dict[str, float] = {}
        closes: dict[str, pd.Timestamp] = {}
        prices: dict[str, float] = {}
        market_closed: set[str] = set()
        for sym in self.config.instruments:
            if not self.is_market_open(sym, now):
                market_closed.add(sym)
            held = self._history.get(sym)
            if held is None or held.empty:
                continue
            newest = held.index[-1]
            close_at = newest + self._tf
            row = held.iloc[-1]
            # NOT clamped at zero: a negative age is clock skew against the
            # venue, and the gateway blocks it as data_from_the_future.
            ages[sym] = (now - close_at).total_seconds()
            closes[sym] = close_at
            prices[sym] = float(row["close"])
            last = self._delivered.get(sym)
            if last is not None and newest <= last:
                continue
            try:
                bar = Bar(
                    newest,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                )
            except ValueError as exc:
                report.errors[sym] = str(exc)
                if self.dispatcher is not None:
                    self.dispatcher.fire(
                        AlertEvent.DATA_QUALITY_DEFECT,
                        f"{sym} {self.timeframe} candle {newest.isoformat()} is "
                        f"incoherent and was not evaluated: {exc}",
                        source=self.source,
                        dedupe_key=f"incoherent_candle:{sym}",
                        instrument=sym,
                    )
                self._delivered[sym] = newest
                continue
            frames[sym] = bar
            closed.add(sym)
            self._delivered[sym] = newest
        report.delivered = tuple(sorted(closed))
        report.closed_market = tuple(sorted(market_closed))
        for observer in self._observers:
            observer.observe_closes(closes, prices)
        return BarBatch(
            frames=frames,
            closed_instruments=frozenset(closed),
            ages=ages,
            timeframe=self.timeframe,
            market_closed=frozenset(market_closed),
        )

    # -- introspection ----------------------------------------------------

    def held_instruments(self) -> Iterable[str]:
        return tuple(sorted(self._history))
