"""The REAL :class:`~fiboki.broker.oanda.Transport`: HTTP over ``httpx``.

``broker/oanda.py`` defines the ``Transport`` protocol and ships
:class:`~fiboki.broker.oanda.RecordedTransport`, which replays fixtures. Until
this module nothing in ``src/`` could send a byte to a venue, which is why the
live feed was "implemented but not composed" (``docs/v2/EXECUTION_ARCHITECTURE.md``
§7). :class:`HttpxTransport` is the one production implementation.
``RecordedTransport`` stays the test double: every test that exercises an
adapter, the candle feed or the pricing source keeps using it.

What this transport does, and deliberately does not do
------------------------------------------------------
* **One request per call. No retries.** ``httpx`` is constructed with
  ``HTTPTransport(retries=0)`` and ``follow_redirects=False``. Retrying is a
  decision about the REQUEST, not the wire: reads are retried by
  :func:`fiboki.broker.retry.retry_idempotent_read` above this layer, and
  writes are never retried at all. A transport that retried on its own would
  resend an order whose first attempt had an unknown outcome, which is the
  exact failure ``tests/unit/test_retry_scope.py`` exists to prevent.
* **Timeouts on every request**, from the caller's ``timeout`` argument, for
  connect, read, write and pool acquisition alike. A socket that never answers
  is the failure that wedges a worker past its lease.
* **A host allow-list, by PARSED hostname.** The transport is constructed with
  the exact hostnames it may reach; every request URL is parsed with
  :func:`urllib.parse.urlsplit` and refused unless its scheme is ``https`` and
  its hostname is in the list. A trailing slash, a port or a path cannot
  defeat that, and ``api-fxpractice.oanda.com.attacker.example`` is not
  ``api-fxpractice.oanda.com``. The paper-forward entrypoint builds one with
  the OANDA practice host only, so a live host is unreachable through it even
  if every other control failed.
* **Credentials are never logged and never kept.** The bearer token arrives
  in the ``headers`` argument on each call, from the caller that read it from
  the environment. This class stores no header, its ``repr`` names only the
  allowed hosts, and a failure is re-raised with the method and path but
  never the headers. ``bearer_token_from_env`` reads a token once, strips it,
  and refuses an empty one with a message that names the variable and not
  the value.

Errors
------
A transport failure (connect, read timeout, TLS) propagates as the ``httpx``
exception it is. :func:`fiboki.broker.retry.is_retryable` classifies those by
class name (``TransportError``) and the adapter and feed wrap them in their
own taxonomy, so a timeout on an order still becomes UNKNOWN and never a
rejection. A response of any status is returned, not raised: deciding what a
404 or a 429 means is the caller's job.

Construction is restricted
--------------------------
``tests/unit/test_http_transport.py`` parses ``src/`` and fails if
``HttpxTransport(`` is called anywhere but ``src/fiboki/entrypoints/`` and
``src/fiboki/cli.py``: a component that can reach the network must be assembled
in the reviewed composition root, never inside a library module.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from fiboki.broker.oanda import HttpResponse

__all__ = [
    "HostNotAllowed",
    "HttpxTransport",
    "MissingCredential",
    "bearer_token_from_env",
]


class HostNotAllowed(RuntimeError):
    """A request named a host (or scheme) this transport was not built for."""


class MissingCredential(RuntimeError):
    """A required credential is absent from the environment. Names the variable only."""


def bearer_token_from_env(environ: Mapping[str, str], name: str) -> str:
    """Read one token from ``environ[name]``. Never echoes the value."""
    token = str(environ.get(name, "") or "").strip()
    if not token:
        raise MissingCredential(
            f"{name} is not set. Put it in ~/.fiboki/env (chmod 600); it is read once "
            "at start and never written to a log, a ledger or the repository."
        )
    if any(ch.isspace() for ch in token):
        raise MissingCredential(
            f"{name} contains whitespace; a token pasted with a line break or a "
            "trailing comment is refused rather than sent."
        )
    return token


class HttpxTransport:
    """``Transport`` over a single ``httpx.Client``: timeouts, no retries, allow-listed hosts."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        client: httpx.Client | None = None,
        user_agent: str = "fiboki/2 paper-forward",
    ) -> None:
        hosts = frozenset(h.strip().lower() for h in allowed_hosts if h and h.strip())
        if not hosts:
            raise ValueError("an HttpxTransport needs at least one allowed host")
        self.allowed_hosts = hosts
        self._owns_client = client is None
        self._client = client or httpx.Client(
            transport=httpx.HTTPTransport(retries=0),
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        self.requests = 0

    def __repr__(self) -> str:
        return f"HttpxTransport(allowed_hosts={sorted(self.allowed_hosts)!r})"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpxTransport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the protocol ------------------------------------------------------

    def _assert_allowed(self, url: str) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https":
            raise HostNotAllowed(
                f"refusing {parts.scheme or '<no scheme>'}:// for {host or url!r}: "
                "credentials are only ever sent over https"
            )
        if host not in self.allowed_hosts:
            raise HostNotAllowed(
                f"host {host!r} is not in this transport's allow-list "
                f"{sorted(self.allowed_hosts)}. The comparison is on the PARSED "
                "hostname; a prefix or a look-alike domain does not match."
            )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        self._assert_allowed(url)
        if timeout <= 0:
            raise ValueError("a transport timeout must be positive")
        self.requests += 1
        response = self._client.request(
            method.upper(),
            url,
            headers=dict(headers),
            content=None if body is None else json.dumps(body).encode("utf-8"),
            timeout=httpx.Timeout(timeout),
        )
        try:
            parsed: Any = response.json() if response.content else {}
        except ValueError:
            parsed = {"_unparsed_body": response.text[:2000]}
        if not isinstance(parsed, dict):
            parsed = {"_body": parsed}
        return HttpResponse(
            status=int(response.status_code),
            body=parsed,
            headers={k: v for k, v in response.headers.items()},
        )
