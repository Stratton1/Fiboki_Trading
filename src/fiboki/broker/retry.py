"""Bounded retry for IDEMPOTENT READS, and for nothing else.

Why this exists
---------------
``OandaAdapter._call`` raised :class:`~fiboki.broker.base.BrokerUnavailable`
on the first 429, 5xx or transport failure, for every request. For an order
that is exactly right: the order's fate is UNKNOWN and the execution service
must reconcile rather than resend. For a READ -- an account summary, the open
trades, the pending orders, the instrument spec, a candle fetch -- it meant one
transient 503 blocked a whole cycle (``broker_health`` fails, the context
builder cannot snapshot the book, reconciliation reports ``venue_unreachable``)
when asking again a second later would have answered.

Why it must NEVER touch a write
-------------------------------
Resending a request whose outcome is unknown is not a retry, it is a second
order. V1 treated a timeout as a rejection and moved on; the symmetric mistake
is treating it as "did not happen" and sending it again. The only safe response
to an unknown write outcome is reconciliation keyed on the client reference,
which ``ExecutionService`` already does. ``tests/unit/test_retry_scope.py``
parses ``src/`` and fails if this decorator is applied to any function whose
name mentions order, submit, close or amend, or to any function that issues a
non-GET request.

Semantics, stated precisely
---------------------------
* Retries ONLY on :class:`BrokerUnavailable`, transport failures
  (``ConnectionError``, ``TimeoutError`` and the HTTP-client equivalents), and
  responses carrying HTTP 429 or 5xx. A 4xx other than 429 is the venue saying
  no and is never retried; nor is :class:`BrokerRejected`,
  :class:`DuplicateClientRef` or an ``AssertionError`` from a test double.
* Exponential backoff with jitter: attempt ``n`` waits
  ``min(max_delay_s, base_delay_s * 2**(n-1))`` scaled into ``[0.5, 1.0)`` of
  itself by the jitter source ("equal jitter").
* ``Retry-After`` is honoured when present: the wait is at least that long.
* A per-call ``deadline_s`` bounds the whole thing. If the next wait would
  carry the call past the deadline, it gives up and re-raises the LAST error,
  unchanged in type, with a note saying why. The caller's error taxonomy is
  therefore preserved: a read that could not be answered is still
  ``BrokerUnavailable``, never a new exception type somebody has to learn.
* Every attempt goes through the injected ``rate_limiter`` when one is given.
  (The OANDA adapter does not pass one: its ``_call`` already acquires the
  adapter's :class:`~fiboki.broker.oanda.RateLimiter` on every request, retries
  included, and acquiring twice would halve the budget.)

Pattern after freqtrade ``exchange/common.py`` ``retrier`` (GPL-3.0, not
copied): the shape -- a decorator, a bounded count, backoff between attempts
-- is the common idea; the classification rules and the deadline are ours.

Clock, sleep and jitter are injected so tests are instant and deterministic.
"""
from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

from fiboki.broker.base import (
    BrokerRejected,
    BrokerUnavailable,
    DuplicateClientRef,
)

__all__ = [
    "RETRYABLE_STATUSES",
    "ReadRetry",
    "RetryAttempt",
    "is_retryable",
    "retry_after_seconds",
    "retry_idempotent_read",
    "status_of",
]

F = TypeVar("F", bound=Callable[..., Any])

#: HTTP statuses that mean "ask again later", not "no".
RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

#: Class names, anywhere in an exception's MRO, that mean the request never
#: produced an HTTP answer. Matched by name so this module does not import an
#: HTTP client: ``httpx.TransportError`` and ``requests.ConnectionError`` /
#: ``requests.Timeout`` are covered without a dependency.
_TRANSPORT_CLASS_NAMES: frozenset[str] = frozenset(
    {"TransportError", "ConnectionError", "Timeout", "TimeoutError"}
)

#: Attribute set on a wrapper so a test can find every decorated callable.
MARKER = "__fiboki_idempotent_read__"


def status_of(exc: BaseException) -> int | None:
    """The HTTP status an exception carries, if it carries one."""
    for candidate in (
        getattr(exc, "status", None),
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(getattr(exc, "response", None), "status", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _parse_retry_after(value: Any, *, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    reference = now or datetime.now(tz=UTC)
    return max(0.0, (when - reference).total_seconds())


def retry_after_seconds(exc: BaseException) -> float | None:
    """``Retry-After`` in seconds, from the exception or its response headers."""
    direct = _parse_retry_after(getattr(exc, "retry_after", None))
    if direct is not None:
        return direct
    headers = getattr(getattr(exc, "response", None), "headers", None) or getattr(
        exc, "headers", None
    )
    if not headers:
        return None
    try:
        items = dict(headers).items()
    except (TypeError, ValueError):
        return None
    for key, value in items:
        if str(key).lower() == "retry-after":
            return _parse_retry_after(value)
    return None


def is_retryable(exc: BaseException) -> bool:
    """May this failure of an IDEMPOTENT READ be asked again?

    The order of the tests matters. A positive refusal is never retried, even
    if it happens to carry a 5xx. An explicit status decides next, so a
    ``BrokerUnavailable`` built from a 401 would NOT be retried. Only then does
    the exception's type decide.
    """
    if isinstance(exc, BrokerRejected | DuplicateClientRef | AssertionError):
        return False
    status = status_of(exc)
    if status is not None:
        return status in RETRYABLE_STATUSES
    if isinstance(exc, BrokerUnavailable | ConnectionError | TimeoutError):
        return True
    return any(cls.__name__ in _TRANSPORT_CLASS_NAMES for cls in type(exc).__mro__)


@dataclass(frozen=True, slots=True)
class RetryAttempt:
    """One failed attempt that WAS retried, for tests and for the operator."""

    label: str
    attempt: int
    error: str
    wait_s: float
    retry_after_s: float | None = None


@dataclass
class ReadRetry:
    """The policy plus its injected clock, sleep, jitter and pacer."""

    max_attempts: int = 4
    deadline_s: float = 10.0
    base_delay_s: float = 0.25
    max_delay_s: float = 2.0
    #: Anything with ``acquire()``, typically the adapter's ``RateLimiter``.
    rate_limiter: Any = None
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    #: Uniform draw in ``[0, 1)``. Jitter changes only WHEN a read is retried,
    #: never what it returns, so it cannot affect a result's determinism.
    jitter: Callable[[], float] = random.random
    history: list[RetryAttempt] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.deadline_s <= 0:
            raise ValueError("deadline_s must be positive")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("delays must be non-negative")

    def backoff(self, attempt: int) -> float:
        """Wait before attempt ``attempt + 1``. Equal jitter over ``[0.5, 1.0)``."""
        ceiling = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return ceiling * (0.5 + 0.5 * float(self.jitter()))

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        label = getattr(fn, "__qualname__", repr(fn))
        started = self.clock()
        attempt = 0
        while True:
            attempt += 1
            if self.rate_limiter is not None:
                self.rate_limiter.acquire()
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if not is_retryable(exc):
                    raise
                if attempt >= self.max_attempts:
                    exc.add_note(
                        f"retry_idempotent_read: gave up on {label} after {attempt} "
                        f"attempt(s) (max_attempts={self.max_attempts})"
                    )
                    raise
                hint = retry_after_seconds(exc)
                wait = self.backoff(attempt)
                if hint is not None:
                    wait = max(wait, hint)
                elapsed = self.clock() - started
                if elapsed + wait > self.deadline_s:
                    exc.add_note(
                        f"retry_idempotent_read: gave up on {label} after {attempt} "
                        f"attempt(s); the next wait of {wait:.2f}s would pass the "
                        f"{self.deadline_s:.1f}s deadline ({elapsed:.2f}s elapsed)"
                    )
                    raise
                self.history.append(
                    RetryAttempt(
                        label=label,
                        attempt=attempt,
                        error=f"{type(exc).__name__}: {exc}",
                        wait_s=wait,
                        retry_after_s=hint,
                    )
                )
                self.sleeper(wait)


def retry_idempotent_read(
    func: F | None = None,
    *,
    retry: ReadRetry | None = None,
    attr: str = "read_retry",
) -> Any:
    """Decorate an IDEMPOTENT READ. Never an order, a close or an amendment.

    Usable bare (``@retry_idempotent_read``) on a method, in which case the
    policy is read from ``self.<attr>`` at call time so each instance can carry
    its own injected clock and sleep; or with an explicit ``retry=`` policy to
    wrap a plain callable, which is how the worker wraps the candle provider's
    ``fetch_bars`` (the data layer sits below ``broker`` and cannot import this
    module itself).
    """

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            runner = retry
            if runner is None and args:
                runner = getattr(args[0], attr, None)
            if not isinstance(runner, ReadRetry):
                runner = ReadRetry()
            return runner.run(fn, *args, **kwargs)

        setattr(wrapper, MARKER, True)
        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorate(func)
    return decorate
