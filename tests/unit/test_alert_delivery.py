"""Alerts leave the machine, CRITICAL ones at least once, and a watchdog runs (audit F P1-9).

Before: ``build_default_dispatcher`` built the Telegram and webhook channels
with ``transport=None``, so every send raised and was merely logged; nothing
ever constructed a ``HeartbeatWatchdog`` outside tests. No network is used
here: every transport is ``httpx.MockTransport`` or a recorder.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from fiboki.obs import alerts as alerts_module
from fiboki.obs.alerts import (
    Alert,
    AlertDispatcher,
    AlertEvent,
    AlertOutbox,
    FileChannel,
    OutboxChannel,
    Severity,
    TelegramChannel,
    TelegramConfig,
    WatchdogThresholds,
    build_default_dispatcher,
)
from fiboki.obs.health import DEFAULT_HEALTH_THRESHOLDS

TOKEN = "123456:SECRET-bot-token"


class Recorder:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": self.status < 300})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _env(tmp_path, **extra: str) -> dict[str, str]:
    return {"FIBOKI_STATE_DIR": str(tmp_path / "state"), **extra}


# --------------------------------------------------------------- transports


def test_the_default_dispatcher_injects_an_http_transport_for_telegram(tmp_path) -> None:
    rec = Recorder()
    env = _env(tmp_path, FIBOKI_TELEGRAM_BOT_TOKEN=TOKEN, FIBOKI_TELEGRAM_CHAT_ID="42")
    dispatcher = build_default_dispatcher(env=env, http_client=rec.client())
    assert dispatcher.channel_names() == ("console", "telegram")
    dispatcher.fire(AlertEvent.WORKER_DOWN, "research worker is dead", force=True)
    assert dispatcher.delivery_failures == []
    [request] = rec.requests
    assert request.url.host == "api.telegram.org"
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == "42" and "worker_down" in body["text"]


def test_the_default_dispatcher_injects_an_http_transport_for_the_webhook(tmp_path) -> None:
    rec = Recorder()
    env = _env(tmp_path, FIBOKI_ALERT_WEBHOOK_URL="https://hooks.example.test/T/abc")
    dispatcher = build_default_dispatcher(env=env, http_client=rec.client())
    dispatcher.fire(AlertEvent.BROKER_UNHEALTHY, "venue down", force=True)
    assert [r.url.host for r in rec.requests] == ["hooks.example.test"]
    assert json.loads(rec.requests[0].content)["event"] == "broker_unhealthy"


def test_a_delivery_failure_never_carries_the_token_or_the_url(tmp_path) -> None:
    rec = Recorder(status=502)
    env = _env(
        tmp_path,
        FIBOKI_TELEGRAM_BOT_TOKEN=TOKEN,
        FIBOKI_TELEGRAM_CHAT_ID="42",
        FIBOKI_ALERT_WEBHOOK_URL="https://hooks.example.test/T/secret-path",
    )
    dispatcher = build_default_dispatcher(env=env, http_client=rec.client())
    dispatcher.fire(AlertEvent.BROKER_UNHEALTHY, "x", force=True)
    errors = " ".join(error for _n, _a, error in dispatcher.delivery_failures)
    assert "502" in errors
    assert TOKEN not in errors and "secret-path" not in errors


def test_a_raw_transport_exception_is_scrubbed_too() -> None:
    def leaky(url, payload, headers, timeout):
        raise RuntimeError(f"connection refused for {url}")

    channel = TelegramChannel(TelegramConfig(bot_token=TOKEN, chat_id="1"), transport=leaky)
    with pytest.raises(alerts_module.AlertDeliveryError) as info:
        channel.send(Alert(AlertEvent.WORKER_DOWN, "x", severity=Severity.CRITICAL))
    assert TOKEN not in str(info.value)


# ------------------------------------------------------------------- outbox


def test_a_critical_alert_that_fails_is_retried_until_delivered(tmp_path) -> None:
    rec = Recorder(status=503)
    outbox_path = tmp_path / "outbox.sqlite"
    env = _env(tmp_path, FIBOKI_TELEGRAM_BOT_TOKEN=TOKEN, FIBOKI_TELEGRAM_CHAT_ID="42")
    dispatcher = build_default_dispatcher(
        env=env, http_client=rec.client(), outbox_path=outbox_path
    )
    dispatcher.fire(AlertEvent.WORKER_DOWN, "dead", force=True)  # CRITICAL by default
    assert len(dispatcher.delivery_failures) == 1
    outbox = AlertOutbox(outbox_path, base_backoff_seconds=0.0)
    assert outbox.counts() == {"undelivered": 1, "delivered": 0}

    # A NEW process (fresh dispatcher over the same outbox) owes the alert.
    rec.status = 200
    retry = build_default_dispatcher(env=env, http_client=rec.client(), outbox_path=outbox_path)
    for channel in retry.channels:
        if isinstance(channel, OutboxChannel):
            channel.outbox.base_backoff_seconds = 0.0
    # Make the failed row due now regardless of the backoff it was given.
    retry_outbox = next(c.outbox for c in retry.channels if isinstance(c, OutboxChannel))
    retry_outbox.clock = lambda: datetime.now(tz=UTC) + timedelta(hours=2)
    assert retry.retry_pending() == (1, 0)
    assert outbox.counts() == {"undelivered": 0, "delivered": 1}
    assert len(rec.requests) == 2  # the failed attempt and the redelivery
    assert retry.retry_pending() == (0, 0)  # delivered means delivered


def test_below_critical_the_outbox_is_not_used(tmp_path) -> None:
    rec = Recorder()
    outbox_path = tmp_path / "outbox.sqlite"
    env = _env(tmp_path, FIBOKI_TELEGRAM_BOT_TOKEN=TOKEN, FIBOKI_TELEGRAM_CHAT_ID="42")
    dispatcher = build_default_dispatcher(env=env, http_client=rec.client(), outbox_path=outbox_path)
    dispatcher.fire(AlertEvent.DATA_STALE, "old bar", force=True)  # WARNING
    assert AlertOutbox(outbox_path).counts() == {"undelivered": 0, "delivered": 0}
    assert len(rec.requests) == 1


def test_the_outbox_is_written_before_the_send(tmp_path) -> None:
    outbox = AlertOutbox(tmp_path / "o.sqlite")
    seen: list[dict] = []

    class Inner:
        name = "telegram"

        def send(self, alert: Alert) -> None:
            seen.append(outbox.counts())

    OutboxChannel(Inner(), outbox).send(Alert(AlertEvent.WORKER_DOWN, "x", severity=Severity.CRITICAL))
    assert seen == [{"undelivered": 1, "delivered": 0}]
    assert outbox.counts() == {"undelivered": 0, "delivered": 1}


def test_an_alert_round_trips_through_the_outbox() -> None:
    alert = Alert(AlertEvent.RISK_LIMIT_BREACH, "m", severity=Severity.CRITICAL,
                  source="s", dedupe_key="k", context={"a": 1})
    back = Alert.from_dict(alert.to_dict())
    assert back.to_dict() == alert.to_dict()


# -------------------------------------------------------------- file channel


def test_the_file_channel_survives_a_torn_last_alert(tmp_path) -> None:
    path = tmp_path / "alerts.jsonl"
    channel = FileChannel(path)
    channel.send(Alert(AlertEvent.DATA_STALE, "one"))
    with path.open("ab") as fh:
        fh.write(b'{"_crc32":"0badf00d","event":"wor')
    rows = channel.read()
    assert [r["message"] for r in rows] == ["one"]
    # A plain JSON-lines reader (the incidents router) still parses every line.
    assert all(json.loads(line) for line in path.read_text().splitlines())


def test_a_torn_ledger_raises_a_critical_alert_through_the_dispatcher(tmp_path) -> None:
    from fiboki.core.durable import durable_append, read_payloads

    dispatcher = build_default_dispatcher(env=_env(tmp_path))
    memory = alerts_module.MemoryChannel()
    dispatcher.add_channel(memory)
    ledger = tmp_path / "intents.jsonl"
    durable_append(ledger, '{"a":1}')
    with ledger.open("ab") as fh:
        fh.write(b'{"a":')
    read_payloads(ledger)
    assert memory.events() == [AlertEvent.LEDGER_TORN_TAIL]
    assert memory.sent[0].severity is Severity.CRITICAL


# --------------------------------------------------------------- thresholds


def test_the_watchdog_and_health_check_share_one_threshold_value() -> None:
    from fiboki.obs.health import WorkerHeartbeatCheck

    assert WatchdogThresholds().stale_after_seconds == DEFAULT_HEALTH_THRESHOLDS.worker_stale_after_seconds
    assert WatchdogThresholds().down_after_seconds == DEFAULT_HEALTH_THRESHOLDS.worker_down_after_seconds
    check = WorkerHeartbeatCheck(heartbeats=lambda: [])
    assert (check.stale_after_seconds, check.down_after_seconds) == (
        DEFAULT_HEALTH_THRESHOLDS.worker_stale_after_seconds,
        DEFAULT_HEALTH_THRESHOLDS.worker_down_after_seconds,
    )


# ---------------------------------------------------------------------- CLI


@pytest.fixture
def recorded_http(monkeypatch):
    rec = Recorder()
    real = alerts_module.httpx_transport
    monkeypatch.setattr(alerts_module, "httpx_transport", lambda client=None: real(rec.client()))
    return rec


def test_fiboki_alerts_test_sends_through_every_channel(tmp_path, monkeypatch, recorded_http) -> None:
    from fiboki.cli import app

    for key, value in _env(
        tmp_path, FIBOKI_TELEGRAM_BOT_TOKEN=TOKEN, FIBOKI_TELEGRAM_CHAT_ID="42",
        FIBOKI_ALERT_LOG=str(tmp_path / "alerts.jsonl"),
    ).items():
        monkeypatch.setenv(key, value)
    result = CliRunner().invoke(app, ["alerts", "test", "--critical", "--json"])
    assert result.exit_code == 0, result.output
    # The console channel logs JSON lines first; the command's payload is last.
    payload = json.loads(result.output[result.output.index('{\n  "sent"'):])
    assert payload["channels"] == {"console": "ok", "file": "ok", "telegram": "ok"}
    assert payload["severity"] == "critical"
    assert len(recorded_http.requests) == 1
    assert "alert_test" in json.loads(recorded_http.requests[0].content)["text"]
    # httpx logs the request URL at INFO; the bot token must not reach any log.
    assert TOKEN not in result.output


def test_fiboki_alerts_test_fails_when_nothing_leaves_the_machine(tmp_path, monkeypatch) -> None:
    from fiboki.cli import app

    for name in ("FIBOKI_TELEGRAM_BOT_TOKEN", "FIBOKI_TELEGRAM_CHAT_ID", "FIBOKI_ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FIBOKI_STATE_DIR", str(tmp_path / "state"))
    result = CliRunner().invoke(app, ["alerts", "test"])
    assert result.exit_code == 1
    assert "no remote channel" in result.output


def test_fiboki_watchdog_run_alerts_on_a_stale_worker(tmp_path, monkeypatch, recorded_http) -> None:
    from fiboki.cli import app
    from fiboki.workers.base import Heartbeat, WorkerStore

    db = tmp_path / "state.db"
    for key, value in _env(
        tmp_path, FIBOKI_STATE_DB=str(db), FIBOKI_TELEGRAM_BOT_TOKEN=TOKEN,
        FIBOKI_TELEGRAM_CHAT_ID="42", FIBOKI_EXPECTED_WORKERS="",
    ).items():
        monkeypatch.setenv(key, value)
    with WorkerStore.sqlite_at(db) as store:
        old = datetime.now(tz=UTC) - timedelta(minutes=20)
        Heartbeat(store, "research@mac:1", "research", clock=lambda: old).write("running")

    result = CliRunner().invoke(app, ["watchdog", "run", "--once", "--interval", "0.01"])
    assert result.exit_code == 0, result.output
    texts = [json.loads(r.content)["text"] for r in recorded_http.requests]
    assert any("worker_down" in t and "research@mac:1" in t for t in texts), texts

    with WorkerStore.sqlite_at(db) as store:
        kinds = {v.kind for v in store.heartbeats()}
        leases = {row["lease_name"] for row in store.lease_rows()}
    assert "watchdog" in kinds  # the watchdog is itself supervised: it beats...
    assert "watchdog" in leases  # ...and holds a lease


def test_a_second_watchdog_exits_75(tmp_path, monkeypatch) -> None:
    from fiboki.cli import app
    from fiboki.workers.base import WorkerLease, WorkerStore

    db = tmp_path / "state.db"
    monkeypatch.setenv("FIBOKI_STATE_DB", str(db))
    monkeypatch.setenv("FIBOKI_STATE_DIR", str(tmp_path / "state"))
    with WorkerStore.sqlite_at(db) as store:
        WorkerLease(store, "watchdog", "watchdog@other:1", ttl_seconds=600).acquire()
        result = CliRunner().invoke(app, ["watchdog", "run", "--once"])
    assert result.exit_code == 75, result.output


def test_dispatcher_isolation_is_unchanged() -> None:
    memory = alerts_module.MemoryChannel(fail_on=frozenset({AlertEvent.WORKER_DOWN}))
    other = alerts_module.MemoryChannel(name="other")
    AlertDispatcher([memory, other]).fire(AlertEvent.WORKER_DOWN, "x")
    assert other.events() == [AlertEvent.WORKER_DOWN]
