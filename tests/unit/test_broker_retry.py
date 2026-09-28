"""``retry_idempotent_read``: bounded retries for reads, and never for writes.

Every test here is instant: the clock, the sleep and the jitter are injected,
and the OANDA adapter is driven through ``RecordedTransport``.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.broker.base import BrokerRejected, BrokerUnavailable, DuplicateClientRef
from fiboki.broker.oanda import (
    OANDA_PRACTICE_HOST,
    HttpResponse,
    OandaAdapter,
    OandaConfig,
    RecordedTransport,
)
from fiboki.broker.retry import (
    ReadRetry,
    is_retryable,
    retry_after_seconds,
    retry_idempotent_read,
)
from fiboki.core.contracts import Order
from fiboki.core.enums import Direction, ExecutionMode, OrderType

ACCOUNT = "101-004-1234567-001"
NOW = pd.Timestamp("2026-01-07T10:00:00Z")
SUMMARY = HttpResponse(
    200,
    {
        "account": {
            "balance": "1000.0",
            "NAV": "1000.0",
            "currency": "GBP",
            "marginUsed": "0",
            "openTradeCount": 0,
        }
    },
)


class FakeTime:
    """A monotonic clock whose sleep advances it. Records every wait."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class CountingLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


def _retry(fake: FakeTime, **kwargs) -> ReadRetry:
    params = {"clock": fake.clock, "sleeper": fake.sleep, "jitter": lambda: 0.0}
    params.update(kwargs)
    return ReadRetry(**params)


def _path(suffix: str) -> str:
    return f"/v3/accounts/{ACCOUNT}{suffix}"


def _adapter(fixtures, fake: FakeTime, **retry_kwargs):
    transport = RecordedTransport(fixtures)
    adapter = OandaAdapter(
        OandaConfig(
            account_id=ACCOUNT,
            api_token="t",
            base_url=f"https://{OANDA_PRACTICE_HOST}",
            mode=ExecutionMode.DEMO,
        ),
        transport=transport,
        env={},
        now_fn=lambda: NOW,
        read_retry=_retry(fake, **retry_kwargs),
    )
    return adapter, transport


# ------------------------------------------------------------ the policy


def test_retries_then_succeeds() -> None:
    fake = FakeTime()
    limiter = CountingLimiter()
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise BrokerUnavailable("503")
        return "answer"

    runner = _retry(fake, rate_limiter=limiter)
    assert runner.run(flaky) == "answer"
    assert calls["n"] == 3
    # Every attempt, including the retries, went through the pacer.
    assert limiter.acquired == 3
    # Equal jitter at jitter()=0 gives exactly half of 0.25 and of 0.5.
    assert fake.slept == [pytest.approx(0.125), pytest.approx(0.25)]
    assert [a.attempt for a in runner.history] == [1, 2]


def test_gives_up_at_the_deadline_with_the_original_error_type() -> None:
    fake = FakeTime()
    calls = {"n": 0}

    def down() -> None:
        calls["n"] += 1
        fake.t += 3.0  # each attempt costs three seconds of wall time
        raise BrokerUnavailable("gateway timeout")

    runner = _retry(fake, deadline_s=5.0, max_attempts=10)
    with pytest.raises(BrokerUnavailable) as caught:
        runner.run(down)
    # 3s + wait, then 6s elapsed > 5s: gives up on the second failure.
    assert calls["n"] == 2
    assert any("deadline" in note for note in caught.value.__notes__)


def test_gives_up_after_max_attempts() -> None:
    fake = FakeTime()
    calls = {"n": 0}

    def down() -> None:
        calls["n"] += 1
        raise ConnectionError("reset by peer")

    with pytest.raises(ConnectionError) as caught:
        _retry(fake, max_attempts=4).run(down)
    assert calls["n"] == 4
    assert any("after 4 attempt(s)" in note for note in caught.value.__notes__)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_never_retries_a_rejected_4xx(status: int) -> None:
    fake = FakeTime()
    calls = {"n": 0}

    class HttpError(Exception):
        pass

    def refused() -> None:
        calls["n"] += 1
        exc = HttpError(f"HTTP {status}")
        exc.status = status  # type: ignore[attr-defined]
        raise exc

    with pytest.raises(HttpError):
        _retry(fake).run(refused)
    assert calls["n"] == 1
    assert fake.slept == []


@pytest.mark.parametrize(
    "exc",
    [
        BrokerRejected("venue said no"),
        DuplicateClientRef("seen it"),
        AssertionError("no fixture"),
        ValueError("a bug"),
    ],
)
def test_never_retries_a_refusal_or_a_bug(exc: Exception) -> None:
    assert is_retryable(exc) is False


def test_a_BrokerUnavailable_carrying_a_4xx_is_not_retried() -> None:
    exc = BrokerUnavailable("HTTP 401")
    exc.status = 401  # type: ignore[attr-defined]
    assert is_retryable(exc) is False


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_rate_limits_and_server_errors_are_retryable(status: int) -> None:
    exc = BrokerUnavailable(f"HTTP {status}")
    exc.status = status  # type: ignore[attr-defined]
    assert is_retryable(exc) is True


def test_retry_after_is_honoured() -> None:
    fake = FakeTime()
    calls = {"n": 0}

    def limited() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            exc = BrokerUnavailable("rate limited")
            exc.status = 429  # type: ignore[attr-defined]
            exc.retry_after = "3"  # type: ignore[attr-defined]
            raise exc
        return "ok"

    assert _retry(fake).run(limited) == "ok"
    assert fake.slept == [3.0]


def test_a_retry_after_beyond_the_deadline_gives_up_rather_than_hammering() -> None:
    fake = FakeTime()
    exc = BrokerUnavailable("rate limited")
    exc.status = 429  # type: ignore[attr-defined]
    exc.retry_after = "60"  # type: ignore[attr-defined]

    def limited() -> None:
        raise exc

    with pytest.raises(BrokerUnavailable):
        _retry(fake, deadline_s=10.0).run(limited)
    assert fake.slept == []


def test_retry_after_parses_seconds_and_http_dates() -> None:
    class Resp:
        def __init__(self) -> None:
            self.headers = {"Retry-After": "Wed, 07 Jan 2099 10:00:00 GMT"}

    with_response = Exception()
    with_response.response = Resp()  # type: ignore[attr-defined]
    assert retry_after_seconds(with_response) > 0
    exc = Exception()
    exc.retry_after = "2.5"  # type: ignore[attr-defined]
    assert retry_after_seconds(exc) == 2.5
    assert retry_after_seconds(Exception()) is None


def test_jitter_keeps_the_wait_between_half_and_all_of_the_ceiling() -> None:
    fake = FakeTime()
    low = _retry(fake, jitter=lambda: 0.0).backoff(3)
    high = _retry(fake, jitter=lambda: 0.999999).backoff(3)
    assert low == pytest.approx(0.5)  # ceiling min(2.0, 0.25 * 4) = 1.0
    assert high == pytest.approx(1.0, rel=1e-5)


def test_the_decorator_reads_the_instance_policy_at_call_time() -> None:
    fake = FakeTime()

    class Reader:
        def __init__(self) -> None:
            self.read_retry = _retry(fake)
            self.calls = 0

        @retry_idempotent_read
        def read(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("slow")
            return 7

    reader = Reader()
    assert reader.read() == 7
    assert reader.calls == 2
    assert len(reader.read_retry.history) == 1


# ------------------------------------------------------ the OANDA adapter


def test_an_account_read_survives_a_transient_503() -> None:
    fake = FakeTime()
    adapter, transport = _adapter(
        {f"GET {_path('/summary')}": [HttpResponse(503, {}), HttpResponse(502, {}), SUMMARY]},
        fake,
    )
    account = adapter.account()
    assert account.balance == pytest.approx(1000.0)
    assert len(transport.calls) == 3
    assert len(adapter.read_retry.history) == 2


def test_a_read_that_stays_down_still_raises_BrokerUnavailable() -> None:
    fake = FakeTime()
    adapter, transport = _adapter({f"GET {_path('/openTrades')}": [HttpResponse(503, {})]}, fake)
    with pytest.raises(BrokerUnavailable):
        adapter.positions()
    assert len(transport.calls) == 4  # max_attempts


def test_a_401_on_a_read_is_not_retried() -> None:
    fake = FakeTime()
    adapter, transport = _adapter({f"GET {_path('/summary')}": [HttpResponse(401, {})]}, fake)
    with pytest.raises(BrokerUnavailable, match="HTTP 401"):
        adapter.account()
    assert len(transport.calls) == 1
    assert fake.slept == []


def test_a_429_read_honours_the_venues_retry_after_header() -> None:
    fake = FakeTime()
    adapter, _ = _adapter(
        {
            f"GET {_path('/pendingOrders')}": [
                HttpResponse(429, {}, {"Retry-After": "2"}),
                HttpResponse(200, {"orders": []}),
            ]
        },
        fake,
    )
    assert adapter.orders() == ()
    assert fake.slept == [2.0]


def test_health_retries_before_reporting_disconnected() -> None:
    fake = FakeTime()
    adapter, transport = _adapter({f"GET {_path('/summary')}": [HttpResponse(503, {})]}, fake)
    health = adapter.health()
    assert health.connected is False
    assert len(transport.calls) == 4


def _order() -> Order:
    return Order(
        plan_id="plan_abc",
        instrument="EURUSD",
        direction=Direction.LONG,
        size=10_000.0,
        order_type=OrderType.MARKET,
        mode=ExecutionMode.DEMO,
        client_ref="FBK-plan_abc",
        stop_loss=1.09935,
        take_profit=1.10835,
    )


@pytest.mark.parametrize("status", [429, 503])
def test_an_order_is_NEVER_retried(status: int) -> None:
    """A resent order whose first outcome was unknown is a second order."""
    fake = FakeTime()
    adapter, transport = _adapter(
        {f"POST {_path('/orders')}": [HttpResponse(status, {}), HttpResponse(201, {})]},
        fake,
    )
    with pytest.raises(BrokerUnavailable):
        adapter.place_order(_order())
    assert len(transport.calls) == 1
    assert fake.slept == []
