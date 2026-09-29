"""Client-side request budgets for free-tier data sources.

A vendor's rate limit is a term of use, not a suggestion, and hitting it
produces a 429 that looks exactly like an outage in the poll log. So the
limit is enforced here, before a request leaves the process, with an
injectable clock so the behaviour is tested deterministically.

* :class:`SlidingWindowLimiter`: at most ``max_calls`` in any ``window_s``
  seconds (Finnhub free tier: 60 per 60 s; GDELT DOC API: 1 per 5 s).
* :class:`DailyRequestBudget`: at most ``limit`` requests per UTC calendar
  day (Myfxbook Community Outlook free tier: 100 per 24 h). Held in memory:
  a process restart resets it, which is why callers also keep a generous
  polling interval rather than spending the budget to the last request.

Neither retries. A limiter that would have to wait either sleeps (``block``)
or raises :class:`~fiboki.data.providers.base.RateLimited`; the next poll is
the retry.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime

from fiboki.data.providers.base import RateLimited

__all__ = [
    "FINNHUB_FREE_CALLS_PER_MINUTE",
    "GDELT_MIN_SPACING_S",
    "DailyRequestBudget",
    "SlidingWindowLimiter",
    "finnhub_free_tier_limiter",
    "gdelt_limiter",
]

#: Finnhub free plan: 60 calls per minute (plus a 30 calls/second cap on every
#: plan, stated in the API's own "Limits" section of its swagger schema).
FINNHUB_FREE_CALLS_PER_MINUTE = 60

#: GDELT's 429 body, verified 2026-09-29: "Please limit requests to one every
#: 5 seconds". Half a second of margin on top.
GDELT_MIN_SPACING_S = 5.5


class SlidingWindowLimiter:
    """At most ``max_calls`` calls in any window of ``window_s`` seconds."""

    def __init__(
        self,
        max_calls: int,
        window_s: float,
        *,
        name: str = "limiter",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls < 1 or window_s <= 0:
            raise ValueError("max_calls must be >= 1 and window_s > 0")
        self.max_calls = int(max_calls)
        self.window_s = float(window_s)
        self.name = name
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.window_s:
            self._calls.popleft()

    def wait_time(self) -> float:
        """Seconds until one more call is allowed (0 when allowed now)."""
        now = self._clock()
        self._prune(now)
        if len(self._calls) < self.max_calls:
            return 0.0
        return max(0.0, self.window_s - (now - self._calls[0]))

    def acquire(self, *, block: bool = True, max_wait_s: float = 120.0) -> float:
        """Take one call slot; return the seconds slept.

        With ``block=False``, or when the wait would exceed ``max_wait_s``,
        raise :class:`RateLimited` instead of sleeping.
        """
        waited = 0.0
        while True:
            wait = self.wait_time()
            if wait <= 0:
                self._calls.append(self._clock())
                return waited
            if not block or waited + wait > max_wait_s:
                raise RateLimited(
                    f"{self.name}: client-side limit of {self.max_calls} call(s) per "
                    f"{self.window_s:g}s reached; next slot in {wait:.1f}s"
                )
            self._sleep(wait)
            waited += wait


class DailyRequestBudget:
    """At most ``limit`` requests per UTC calendar day, counted in this process."""

    def __init__(
        self,
        limit: int,
        *,
        name: str = "budget",
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = int(limit)
        self.name = name
        self._clock = clock
        self._day: date | None = None
        self._used = 0

    def _roll(self) -> None:
        today = self._clock().astimezone(UTC).date()
        if today != self._day:
            self._day, self._used = today, 0

    @property
    def remaining(self) -> int:
        self._roll()
        return self.limit - self._used

    def take(self, n: int = 1) -> None:
        """Spend ``n`` requests or raise :class:`RateLimited` without spending any."""
        self._roll()
        if self._used + n > self.limit:
            raise RateLimited(
                f"{self.name}: daily budget of {self.limit} requests exhausted for "
                f"{self._day} (UTC); used {self._used}"
            )
        self._used += n


def finnhub_free_tier_limiter(**kwargs: object) -> SlidingWindowLimiter:
    """One limiter per API key: share it between every client using that key."""
    return SlidingWindowLimiter(FINNHUB_FREE_CALLS_PER_MINUTE, 60.0, name="finnhub", **kwargs)  # type: ignore[arg-type]


def gdelt_limiter(**kwargs: object) -> SlidingWindowLimiter:
    """One request per :data:`GDELT_MIN_SPACING_S`, shared by every GDELT query."""
    return SlidingWindowLimiter(1, GDELT_MIN_SPACING_S, name="gdelt", **kwargs)  # type: ignore[arg-type]
