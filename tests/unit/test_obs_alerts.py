"""The alert taxonomy V1 lacked, and a watchdog that runs on a timer.

V1's alerting channel worked. It had nothing to send, because the event enum
only contained the trade lifecycle: there was no ``worker_down`` and no
``heartbeat_stale``, so a dead worker produced silence. And freshness was
computed when a human loaded a page, which is a dashboard widget, not
monitoring.
"""
from __future__ import annotations

import json
import time

import pytest

from fiboki.obs.alerts import (
    Alert,
    AlertDispatcher,
    AlertEvent,
    ConsoleChannel,
    FileChannel,
    HeartbeatView,
    HeartbeatWatchdog,
    MemoryChannel,
    Severity,
    TelegramChannel,
    TelegramConfig,
    WatchdogThresholds,
    WebhookChannel,
    WebhookConfig,
    build_default_dispatcher,
    default_severity,
)

# --------------------------------------------------------------- the taxonomy

REQUIRED_OPERATIONAL = [
    "WORKER_DOWN",
    "HEARTBEAT_STALE",
    "RECONCILIATION_DIVERGENCE",
    "DATA_STALE",
    "BROKER_UNHEALTHY",
    "RISK_LIMIT_BREACH",
    "KILL_SWITCH_ACTIVATED",
    "ORDER_REJECTED_REPEATEDLY",
    "STRATEGY_DEGRADED",
]

REQUIRED_LIFECYCLE = [
    "ORDER_SUBMITTED",
    "ORDER_REJECTED",
    "POSITION_OPENED",
    "POSITION_CLOSED",
    "STOP_LOSS_HIT",
    "TAKE_PROFIT_HIT",
]


@pytest.mark.parametrize("name", REQUIRED_OPERATIONAL)
def test_the_operational_events_v1_lacked_exist(name):
    """Without these the alerting channel has nothing to send about a dead worker."""
    assert hasattr(AlertEvent, name), f"AlertEvent.{name} is missing"


@pytest.mark.parametrize("name", REQUIRED_LIFECYCLE)
def test_the_trade_lifecycle_events_exist(name):
    assert hasattr(AlertEvent, name)


def test_a_dead_worker_is_critical_and_a_filled_order_is_not():
    """Severity defaults must reflect what wakes somebody at 03:00."""
    assert default_severity(AlertEvent.WORKER_DOWN) is Severity.CRITICAL
    assert default_severity(AlertEvent.RECONCILIATION_DIVERGENCE) is Severity.CRITICAL
    assert default_severity(AlertEvent.KILL_SWITCH_ACTIVATED) is Severity.CRITICAL
    assert default_severity(AlertEvent.POSITION_OPENED) is Severity.INFO


# ---------------------------------------------------------------- dispatcher


def test_a_failing_channel_does_not_silence_the_others():
    """V1: one misconfigured webhook suppressed every other channel."""
    broken = MemoryChannel(name="broken", fail_on=frozenset({AlertEvent.WORKER_DOWN}))
    good = MemoryChannel(name="good")
    dispatcher = AlertDispatcher([broken, good])

    dispatcher.fire(AlertEvent.WORKER_DOWN, "worker a is gone")

    assert good.events() == [AlertEvent.WORKER_DOWN]
    assert len(dispatcher.delivery_failures) == 1
    assert dispatcher.delivery_failures[0][0] == "broken"


def test_repeat_suppression_is_windowed_not_one_shot():
    """A level condition must fire again after the window.

    A one-shot alert for a stale heartbeat means it is mentioned at 02:00 and
    never again, which is how a weekend outage goes unnoticed.
    """
    now = {"t": 0.0}
    channel = MemoryChannel()
    dispatcher = AlertDispatcher(
        [channel], repeat_after_seconds=100.0, clock=lambda: now["t"]
    )

    assert dispatcher.fire(AlertEvent.HEARTBEAT_STALE, "stale", source="w1") is not None
    assert dispatcher.fire(AlertEvent.HEARTBEAT_STALE, "stale", source="w1") is None
    now["t"] = 101.0
    assert dispatcher.fire(AlertEvent.HEARTBEAT_STALE, "stale", source="w1") is not None
    assert len(channel.sent) == 2
    assert dispatcher.suppressed_counts()["heartbeat_stale:w1"] == 1


def test_different_sources_are_suppressed_independently():
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel], repeat_after_seconds=1e6)
    dispatcher.fire(AlertEvent.WORKER_DOWN, "a", source="w1")
    dispatcher.fire(AlertEvent.WORKER_DOWN, "b", source="w2")
    assert len(channel.sent) == 2


def test_force_bypasses_suppression():
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel], repeat_after_seconds=1e6)
    dispatcher.fire(AlertEvent.ORDER_UNKNOWN, "x", source="w1")
    dispatcher.fire(AlertEvent.ORDER_UNKNOWN, "x", source="w1", force=True)
    assert len(channel.sent) == 2


def test_min_severity_filters():
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel], min_severity=Severity.ERROR)
    dispatcher.fire(AlertEvent.POSITION_OPENED, "info level")
    dispatcher.fire(AlertEvent.WORKER_DOWN, "critical")
    assert channel.events() == [AlertEvent.WORKER_DOWN]


# ------------------------------------------------------------------ channels


def test_file_channel_writes_parseable_jsonl_including_quotes(tmp_path):
    path = tmp_path / "alerts.jsonl"
    channel = FileChannel(path)
    channel.send(
        Alert(
            AlertEvent.BROKER_UNHEALTHY,
            'venue said "ACCOUNT_NOT_ENABLED"\nretry later',
            context={"body": '{"errorCode": "X"}'},
        )
    )
    rows = channel.read()
    assert len(rows) == 1
    assert "ACCOUNT_NOT_ENABLED" in rows[0]["message"]
    assert json.loads(rows[0]["context"]["body"])["errorCode"] == "X"


def test_console_channel_writes_to_a_stream():
    import io

    stream = io.StringIO()
    ConsoleChannel(stream=stream).send(Alert(AlertEvent.DATA_STALE, "EURUSD is 40m old"))
    assert "DATA_STALE".lower() in stream.getvalue().lower()


def test_webhook_channel_posts_through_an_injected_transport():
    calls = []
    channel = WebhookChannel(
        WebhookConfig(url="https://example.invalid/hook", headers={"X-Key": "k"}),
        transport=lambda url, payload, headers, timeout: calls.append(
            (url, payload, headers, timeout)
        ),
    )
    channel.send(Alert(AlertEvent.WORKER_DOWN, "gone", severity=Severity.CRITICAL))
    assert len(calls) == 1
    url, payload, headers, timeout = calls[0]
    assert url == "https://example.invalid/hook"
    assert payload["event"] == "worker_down"
    assert headers["X-Key"] == "k"


def test_webhook_without_a_transport_raises_rather_than_silently_dropping():
    channel = WebhookChannel(WebhookConfig(url="https://example.invalid/hook"))
    with pytest.raises(RuntimeError, match="no transport"):
        channel.send(Alert(AlertEvent.WORKER_DOWN, "gone", severity=Severity.CRITICAL))


def test_telegram_channel_is_fixture_tested_with_no_credentials():
    """The interface is exercised end to end; no token exists anywhere."""
    calls = []
    channel = TelegramChannel(
        TelegramConfig(bot_token="TEST-TOKEN", chat_id="-100123"),
        transport=lambda url, payload, headers, timeout: calls.append((url, payload)),
    )
    channel.send(
        Alert(
            AlertEvent.KILL_SWITCH_ACTIVATED,
            "flatten requested by joe",
            severity=Severity.CRITICAL,
            context={"reason": "spread <blown> out"},
        )
    )
    url, payload = calls[0]
    assert url.endswith("/sendMessage")
    assert payload["chat_id"] == "-100123"
    assert "kill_switch_activated" in payload["text"]
    # HTML-escaped, so a reason containing markup cannot break the message.
    assert "&lt;blown&gt;" in payload["text"]


def test_telegram_config_never_reprs_the_token():
    config = TelegramConfig(bot_token="123456:SECRET-VALUE", chat_id="1")
    assert "SECRET-VALUE" not in json.dumps(config.redacted())
    assert config.redacted()["bot_token"].startswith("***")


def test_telegram_from_env_is_absent_without_credentials():
    assert TelegramChannel.from_env(env={}) is None
    assert (
        TelegramChannel.from_env(
            env={"FIBOKI_TELEGRAM_BOT_TOKEN": "t", "FIBOKI_TELEGRAM_CHAT_ID": "c"}
        )
        is not None
    )


def test_the_default_dispatcher_degrades_to_console_without_config(tmp_path):
    dispatcher = build_default_dispatcher(env={})
    assert dispatcher.channel_names() == ("console",)
    configured = build_default_dispatcher(
        env={"FIBOKI_ALERT_LOG": str(tmp_path / "a.jsonl")}
    )
    assert "file" in configured.channel_names()


# ------------------------------------------------------------------ watchdog


def test_the_watchdog_fires_WORKER_DOWN_when_a_heartbeat_goes_stale():
    """THE test. No page is loaded; the watchdog evaluates on its own."""
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel])
    age = {"seconds": 5.0}
    watchdog = HeartbeatWatchdog(
        dispatcher,
        lambda: [HeartbeatView("research@mac:412", "research", age["seconds"])],
        thresholds=WatchdogThresholds(stale_after_seconds=60, down_after_seconds=300),
    )

    assert watchdog.evaluate() == []          # fresh

    age["seconds"] = 90.0
    fired = watchdog.evaluate()
    assert [a.event for a in fired] == [AlertEvent.HEARTBEAT_STALE]

    age["seconds"] = 600.0
    fired = watchdog.evaluate()
    assert [a.event for a in fired] == [AlertEvent.WORKER_DOWN]
    assert fired[0].severity is Severity.CRITICAL
    assert "research@mac:412" in fired[0].message
    # The message must say what the operator should conclude.
    assert "DEAD" in fired[0].message


def test_the_watchdog_alerts_on_a_worker_that_NEVER_started():
    """With no heartbeat row there is nothing to compute freshness from.

    V1 rendered a blank in that case and everyone read a blank as 'fine'. It
    is the state after a reboot, which is exactly when it matters.
    """
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel])
    watchdog = HeartbeatWatchdog(
        dispatcher, lambda: [], expected_workers=("research@mac:1", "live@mac:2")
    )
    fired = watchdog.evaluate()
    assert [a.event for a in fired] == [AlertEvent.WORKER_DOWN, AlertEvent.WORKER_DOWN]
    assert "no heartbeat at all" in fired[0].message


def test_the_watchdog_carries_the_last_error_into_the_alert():
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel])
    watchdog = HeartbeatWatchdog(
        dispatcher,
        lambda: [
            HeartbeatView("w1", "research", 400.0, status="failing", last_error="OOM killed")
        ],
    )
    fired = watchdog.evaluate()
    assert fired[0].context["last_error"] == "OOM killed"
    assert fired[0].context["last_status"] == "failing"


def test_the_watchdog_runs_on_a_timer_without_anybody_looking():
    """The property that makes this monitoring rather than a dashboard."""
    channel = MemoryChannel()
    dispatcher = AlertDispatcher([channel], repeat_after_seconds=0.0)
    watchdog = HeartbeatWatchdog(
        dispatcher,
        lambda: [HeartbeatView("w1", "research", 9999.0)],
        interval_seconds=0.02,
    )
    with watchdog:
        deadline = time.monotonic() + 5.0
        while watchdog.ticks < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert watchdog.ticks >= 3, "the watchdog thread never ran"
    assert AlertEvent.WORKER_DOWN in channel.events()
    assert not watchdog.running()


def test_a_watchdog_evaluation_failure_does_not_kill_the_thread():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database briefly unreachable")
        return [HeartbeatView("w1", "research", 1.0)]

    watchdog = HeartbeatWatchdog(
        AlertDispatcher([MemoryChannel()]), flaky, interval_seconds=0.02
    )
    with watchdog:
        deadline = time.monotonic() + 5.0
        while calls["n"] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert calls["n"] >= 3
    assert "database briefly unreachable" in watchdog.last_error
