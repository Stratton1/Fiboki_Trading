"""The polling feed inside a real ``LiveWorker`` loop, on a fake clock.

Proves the composition, not the parts: the worker waits on the feed, the feed
wakes at boundary + offset, the batch reaches the evaluator once per bar, the
wait is not charged to the cycle budget, the bar CLOSE time reaches the
``RiskContextBuilder`` the gateway reads, and startup reconciliation runs
before the first fetch. RecordedTransport only; no network.
"""
from __future__ import annotations

import pandas as pd

from fiboki.broker.oanda import HttpResponse, RecordedTransport
from fiboki.broker.retry import ReadRetry
from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.data.providers.oanda import OandaCandlesProvider
from fiboki.obs.alerts import AlertDispatcher, MemoryChannel
from fiboki.workers.base import WorkerStore, reset_log_once
from fiboki.workers.feeds import OandaPollingBarFeed, PollingFeedConfig, TransportHttpClient
from fiboki.workers.live_worker import LiveWorker, LiveWorkerConfig
from fiboki.workers.runtime import RiskContextBuilder

CANDLES = "GET /v3/instruments/EUR_USD/candles"
H1 = pd.Timedelta(hours=1)
WED_10 = pd.Timestamp("2026-01-07T10:00:00Z")


class FakeClock:
    def __init__(self, start: pd.Timestamp) -> None:
        self.now = start

    def __call__(self) -> pd.Timestamp:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now = self.now + pd.Timedelta(seconds=float(seconds))

    def mono(self) -> float:
        return self.now.value / 1e9


def _payload(last_start: pd.Timestamp, n: int = 5) -> HttpResponse:
    candles = []
    for i in range(n):
        start = last_start - H1 * (n - 1 - i)
        candles.append(
            {
                "complete": True,
                "volume": 5,
                "time": start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "mid": {"o": "1.10000", "h": "1.10100", "l": "1.09900", "c": "1.10050"},
            }
        )
    candles.append(
        {
            "complete": False,
            "volume": 1,
            "time": (last_start + H1).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "mid": {"o": "1.10050", "h": "1.10060", "l": "1.10040", "c": "1.10055"},
        }
    )
    return HttpResponse(200, {"instrument": "EUR_USD", "granularity": "H1", "candles": candles})


class Recon:
    still_unknown = ()
    orphan_broker_refs = ()
    size_mismatches = ()
    errors = ()

    def summary(self) -> str:
        return "clean"


class Execution:
    class _Mode:
        value = "paper"

    class _Adapter:
        name = "fake"

    def __init__(self, log: list[str]) -> None:
        self.mode = self._Mode()
        self.adapter = self._Adapter()
        self.log = log

    def reconcile(self):
        self.log.append("reconcile")
        return Recon()

    def submit(self, plan, context):  # pragma: no cover - evaluator yields no plans
        raise AssertionError("no plans expected")


class Evaluator:
    def __init__(self) -> None:
        self.seen: list[tuple[str, pd.Timestamp]] = []

    def evaluate(self, instruments, batch):
        for sym in instruments:
            self.seen.append((sym, batch.frames[sym].timestamp))
        return []


def test_the_polling_feed_drives_the_worker_once_per_closed_bar(tmp_path) -> None:
    reset_log_once()
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=2))
    log: list[str] = []

    class LoggingTransport(RecordedTransport):
        def request(self, method, url, **kwargs):
            log.append("fetch")
            return super().request(method, url, **kwargs)

    transport = LoggingTransport({CANDLES: [_payload(WED_10 - H1), _payload(WED_10)]})
    provider = OandaCandlesProvider(api_token="t", http_client=TransportHttpClient(transport))
    feed = OandaPollingBarFeed(
        provider,
        PollingFeedConfig(instruments=("EURUSD",), timeframe=Timeframe.H1, warmup_bars=10),
        clock=clock,
        sleeper=clock.sleep,
        read_retry=ReadRetry(clock=clock.mono, sleeper=clock.sleep, jitter=lambda: 0.0),
    )
    builder = RiskContextBuilder(adapter=object(), clock=clock, fx=IdentityFxSource())
    feed.attach(builder)
    evaluator = Evaluator()
    channel = MemoryChannel()
    store = WorkerStore.sqlite_at(tmp_path / "state.db")
    worker = LiveWorker(
        execution_service=Execution(log),
        feed=feed,
        evaluator=evaluator,
        context_builder=builder,
        store=store,
        config=LiveWorkerConfig(max_cycles=1, idle_sleep_seconds=0.0),
        dispatcher=AlertDispatcher([channel]),
        worker="live@test:1",
        monotonic=clock.mono,
    )

    # A full process start: lease, startup reconcile, one cycle.
    assert worker.run(install_signals=False) == 0
    assert log[0] == "reconcile" and log[1] == "fetch"
    assert clock.now >= WED_10 + pd.Timedelta(seconds=5)
    assert evaluator.seen == [("EURUSD", WED_10 - H1)]

    # Between bars: bounded waits, idle cycles, no fetch, no evaluation.
    fetches = log.count("fetch")
    for _ in range(3):
        result = worker.run_cycle()
        assert "waiting" in result.detail
    assert log.count("fetch") == fetches
    assert len(evaluator.seen) == 1

    # Next boundary: exactly one more evaluation, of the 10:00 bar.
    clock.now = WED_10 + H1 + pd.Timedelta(seconds=4)
    worker.run_cycle()
    assert evaluator.seen[-1] == ("EURUSD", WED_10)
    assert len(evaluator.seen) == 2

    # The builder the gateway reads holds the CLOSE of that bar, and the
    # calendar says the session is open.
    view = builder.market_view(get_instrument("EURUSD"), clock())
    assert view.last_bar_time == WED_10 + H1
    assert view.market_open is True
    # Neither cycle was over budget: the wait was not charged to it.
    assert not [a for a in channel.sent if "budget" in a.message]
    store.close()
