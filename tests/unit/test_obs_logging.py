"""The JSON log line must survive content that V1's f-string formatter did not.

V1 built log lines by interpolating into a JSON-shaped string. Any message
containing a quote produced an unparseable line, the shipper dropped it, and
the dropped lines were exactly the interesting ones (tracebacks, broker error
bodies). Every test here round-trips through ``json.loads``.
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from fiboki.obs.logging import (
    CORRELATION_KEY,
    JsonFormatter,
    bind,
    clear_context,
    configure_logging,
    correlation_id,
    current_context,
    get_logger,
    new_correlation_id,
)


@pytest.fixture(autouse=True)
def _clean_context():
    clear_context()
    yield
    clear_context()


def _emit(message, *, name="t", level=logging.INFO, **extra) -> dict:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service="fiboki", component="test"))
    logger = logging.getLogger(f"test.{name}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.log(level, message, extra=extra)
    raw = stream.getvalue()
    assert raw.count("\n") == 1, "a log record must be exactly one line"
    return json.loads(raw)


# ---------------------------------------------------------------- escaping

QUOTE_CASES = [
    'broker said "insufficient margin"',
    "newline\nin\nthe\nmiddle",
    "tab\there and carriage\rreturn",
    r"windows\path\to\file and a trailing backslash \\",
    'nested "quotes" with \\"escapes\\" inside',
    "unicode: £ € 日本語 🚨",
    "null-ish \x00 control \x01 chars",
    '{"looks": "like json already"}',
    "'single' and \"double\" together",
]


@pytest.mark.parametrize("message", QUOTE_CASES)
def test_message_round_trips_through_json(message):
    """THE regression test. Every one of these broke V1's formatter."""
    record = _emit(message)
    assert record["msg"] == message
    assert record["level"] == "INFO"


def test_message_with_quotes_and_newlines_together():
    message = 'line one "quoted"\nline two \\ backslash\nline three'
    record = _emit(message)
    assert record["msg"] == message
    # And the payload really is one line on the wire.
    assert "\n" not in json.dumps(record, ensure_ascii=False).replace("\\n", "")


def test_extra_values_with_quotes_survive():
    record = _emit("ok", broker_body='{"errorCode":"ACCOUNT_NOT_ENABLED"}')
    assert json.loads(record["broker_body"])["errorCode"] == "ACCOUNT_NOT_ENABLED"


def test_exception_traceback_is_captured_and_parseable():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.exc")
    logger.handlers = [handler]
    logger.propagate = False
    try:
        raise ValueError('a message with "quotes" and\na newline')
    except ValueError:
        logger.exception("cycle failed")
    record = json.loads(stream.getvalue())
    assert record["exc_type"] == "ValueError"
    assert "ValueError" in record["exc"]
    assert '"quotes"' in record["exc"]


def test_unserialisable_extra_does_not_lose_the_line():
    class Opaque:
        def __repr__(self) -> str:
            return 'Opaque(<"weird">)'

    record = _emit("ok", thing=Opaque())
    assert "weird" in record["thing"]


def test_bad_percent_args_still_emit_a_line():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.badargs")
    logger.handlers = [handler]
    logger.propagate = False
    logger.error("missing %s and %s", "one")  # deliberately wrong arity
    record = json.loads(stream.getvalue())
    assert "unformattable" in record["msg"]


# ------------------------------------------------------------- correlation


def test_correlation_id_appears_on_records_inside_the_binding():
    cid = new_correlation_id()
    with bind(correlation_id=cid, job_id="job_1"):
        record = _emit("inside")
    assert record[CORRELATION_KEY] == cid
    assert record["job_id"] == "job_1"

    outside = _emit("outside")
    assert CORRELATION_KEY not in outside


def test_bind_nests_and_restores_even_when_the_block_raises():
    with bind(correlation_id="outer", layer="a"):
        assert correlation_id() == "outer"
        with pytest.raises(RuntimeError), bind(correlation_id="inner"):
            assert correlation_id() == "inner"
            assert current_context()["layer"] == "a"
            raise RuntimeError("boom")
        assert correlation_id() == "outer"
    assert correlation_id() == ""


def test_extra_cannot_shadow_a_structural_field():
    """A dashboard filtering on ``level`` must not be defeated by extra=."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.shadow")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("ok", extra={"level": "TOTALLY_FINE", "service": "impostor"})
    record = json.loads(stream.getvalue())
    assert record["level"] == "INFO"
    assert record["service"] == "fiboki"
    assert record["msg"] == "ok"
    assert record["x_level"] == "TOTALLY_FINE"
    assert record["x_service"] == "impostor"


def test_get_logger_merges_static_and_per_call_extra():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    base = logging.getLogger("test.adapter")
    base.handlers = [handler]
    base.propagate = False
    base.setLevel(logging.INFO)

    logger = get_logger("test.adapter", worker_id="w1")
    logger.info("hello", extra={"job_id": "j1"})
    record = json.loads(stream.getvalue())
    # The stdlib adapter REPLACES extra; ours merges. Both must be present.
    assert record["worker_id"] == "w1"
    assert record["job_id"] == "j1"


def test_configure_logging_installs_json_on_a_non_tty():
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream, service="fiboki", component="worker")
    logging.getLogger("configured").info('has "quotes"')
    record = json.loads(stream.getvalue().strip())
    assert record["msg"] == 'has "quotes"'
    assert record["component"] == "worker"
    logging.getLogger().handlers.clear()


# ---------------------------------------------------------------- transport noise


def test_per_request_transport_loggers_are_held_at_warning(monkeypatch) -> None:
    import io
    import logging

    from fiboki.obs.logging import NOISY_TRANSPORT_LOGGERS, configure_logging

    monkeypatch.delenv("FIBOKI_LOG_HTTP", raising=False)
    buf = io.StringIO()
    configure_logging(stream=buf, json_output=False)
    for name in NOISY_TRANSPORT_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
    logging.getLogger("httpx").info("HTTP Request: GET https://example.invalid/ 200")
    logging.getLogger("httpx").warning("transport warning still visible")
    out = buf.getvalue()
    assert "HTTP Request" not in out and "transport warning still visible" in out


def test_request_lines_can_be_restored_for_debugging(monkeypatch) -> None:
    import io
    import logging

    from fiboki.obs.logging import configure_logging

    monkeypatch.setenv("FIBOKI_LOG_HTTP", "info")
    configure_logging(stream=io.StringIO(), json_output=False)
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.INFO
    monkeypatch.setenv("FIBOKI_LOG_HTTP", "nonsense")
    configure_logging(stream=io.StringIO(), json_output=False)
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
