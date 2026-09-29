"""The real ``Transport``: timeouts, no retries, allow-listed hosts, no leaked token.

``HttpxTransport`` is exercised against ``httpx.MockTransport`` -- the real
client, a fake socket -- so what is tested is what runs. The last test is
structural: the transport may be constructed only in the composition root
(``src/fiboki/entrypoints/``) and ``src/fiboki/cli.py``.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import httpx
import pytest

from fiboki.broker.http_transport import (
    HostNotAllowed,
    HttpxTransport,
    MissingCredential,
    bearer_token_from_env,
)
from fiboki.broker.oanda import OANDA_LIVE_HOST, OANDA_PRACTICE_HOST
from fiboki.broker.retry import is_retryable

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
PRACTICE = f"https://{OANDA_PRACTICE_HOST}"
TOKEN = "s3cr3t-practice-token"


def _transport(handler) -> HttpxTransport:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return HttpxTransport(allowed_hosts=(OANDA_PRACTICE_HOST,), client=client)


def test_a_get_returns_status_body_and_headers_and_passes_the_timeout() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions["timeout"]
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"candles": []}, headers={"RequestID": "42"})

    response = _transport(handler).request(
        "GET", f"{PRACTICE}/v3/instruments/EUR_USD/candles",
        headers={"Authorization": f"Bearer {TOKEN}"}, timeout=3.5,
    )
    assert response.status == 200 and response.body == {"candles": []}
    assert response.headers["requestid"] == "42"
    assert seen["timeout"] == {"connect": 3.5, "read": 3.5, "write": 3.5, "pool": 3.5}
    assert seen["auth"] == f"Bearer {TOKEN}"


def test_a_503_is_returned_once_and_never_retried_by_the_transport() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"errorMessage": "busy"}, headers={"Retry-After": "2"})

    response = _transport(handler).request("GET", f"{PRACTICE}/x", headers={})
    assert calls["n"] == 1, "retrying is the retry decorator's job, on reads only"
    assert response.status == 503 and response.headers["retry-after"] == "2"


def test_a_post_is_sent_once_with_a_json_body() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(201, json={"ok": True})

    _transport(handler).request("POST", f"{PRACTICE}/v3/x", headers={}, body={"a": 1})
    assert bodies == [b'{"a": 1}']


def test_a_transport_failure_propagates_as_a_retryable_read_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no answer", request=request)

    with pytest.raises(httpx.ConnectTimeout) as info:
        _transport(handler).request("GET", f"{PRACTICE}/x", headers={})
    assert is_retryable(info.value), "the read decorator must be able to classify it"


@pytest.mark.parametrize(
    "url",
    [
        f"https://{OANDA_LIVE_HOST}/v3/accounts",
        f"https://{OANDA_PRACTICE_HOST}.attacker.example/v3/accounts",
        f"http://{OANDA_PRACTICE_HOST}/v3/accounts",
        "https://example.com/",
    ],
)
def test_anything_but_the_allowed_host_over_https_is_refused(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("a refused request must not reach the wire")

    with pytest.raises(HostNotAllowed):
        _transport(handler).request("GET", url, headers={"Authorization": f"Bearer {TOKEN}"})


def test_a_trailing_slash_or_port_does_not_defeat_the_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    t = _transport(handler)
    assert t.request("GET", f"{PRACTICE}/", headers={}).status == 200
    assert t.request("GET", f"https://{OANDA_PRACTICE_HOST}:443/v3", headers={}).status == 200


def test_the_token_is_not_in_repr_logs_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    t = _transport(handler)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(httpx.ReadTimeout) as info:
        t.request("GET", f"{PRACTICE}/x", headers={"Authorization": f"Bearer {TOKEN}"})
    assert TOKEN not in repr(t)
    assert TOKEN not in str(info.value)
    assert TOKEN not in caplog.text


def test_bearer_token_from_env_names_the_variable_never_the_value() -> None:
    assert bearer_token_from_env({"X": f"  {TOKEN}\n"}, "X") == TOKEN
    with pytest.raises(MissingCredential, match="X is not set"):
        bearer_token_from_env({}, "X")
    with pytest.raises(MissingCredential) as info:
        bearer_token_from_env({"X": "abc def"}, "X")
    assert "abc" not in str(info.value)


def test_the_default_client_does_not_retry_or_follow_redirects() -> None:
    t = HttpxTransport(allowed_hosts=(OANDA_PRACTICE_HOST,))
    try:
        assert t._client.follow_redirects is False
        pool = t._client._transport
        assert isinstance(pool, httpx.HTTPTransport)
        assert pool._pool._retries == 0
    finally:
        t.close()


# ---------------------------------------------------------------- structure

ALLOWED_CONSTRUCTION = ("entrypoints/", "cli.py")


def _constructions(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "HttpxTransport":
                lines.append(node.lineno)
    return lines


def test_the_real_transport_is_constructed_only_in_the_composition_root() -> None:
    offenders: list[str] = []
    found_in_root = False
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC).as_posix()
        lines = _constructions(ast.parse(path.read_text(encoding="utf-8")))
        if not lines:
            continue
        if rel.startswith(ALLOWED_CONSTRUCTION):
            found_in_root = True
            continue
        offenders += [f"{rel}:{n}" for n in lines]
    assert not offenders, (
        "HttpxTransport can reach a venue; it is assembled only in "
        f"src/fiboki/entrypoints/ or cli.py. Found: {offenders}"
    )
    assert found_in_root, "the check is vacuous: nothing constructs the transport"


def test_the_construction_check_flags_a_planted_violation() -> None:
    planted = "from x import HttpxTransport\nt = HttpxTransport(allowed_hosts=('h',))\n"
    assert _constructions(ast.parse(planted)) == [2]
