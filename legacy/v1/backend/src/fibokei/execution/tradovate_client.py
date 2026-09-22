"""Tradovate REST client — Phase 1 typed scaffold.

Demo/sandbox first. Live (real-money) endpoints are hard-blocked unless
the operator has explicitly opted in via ``FIBOKEI_TRADOVATE_LIVE_ALLOWED=true``
**and** the global ``FIBOKEI_LIVE_EXECUTION_ENABLED=true``. Mirrors the
safety pattern used by ``ig_client.IGClient._ensure_demo_only``.

Important Phase-1 caveats:

* The exact Tradovate API path constants below are placeholders sourced
  from public Tradovate documentation. They are flagged ``TODO_VERIFY`` so
  that the first real demo connection has a single place to confirm.
* Credentials are read from environment variables only — never logged,
  never echoed in test fixtures, never written to disk.
* Every public method ends in a typed result/error; the adapter never
  needs to ``except Exception``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Environment / URL constants ──────────────────────────────────────────

#: Demo / sandbox base URL (TODO_VERIFY against latest Tradovate docs).
TRADOVATE_DEMO_BASE = "https://demo.tradovateapi.com/v1"

#: Production / live base URL — hard-blocked unless live explicitly allowed.
TRADOVATE_LIVE_BASE = "https://live.tradovateapi.com/v1"

#: Access tokens are short-lived; re-authenticate after this many seconds.
SESSION_TTL_SECONDS = 75 * 60  # 75 minutes (Tradovate access tokens last 80m)


# ── Typed primitives ─────────────────────────────────────────────────────


@dataclass
class TradovateSession:
    """Holds Tradovate OAuth-style session state.

    Tradovate authenticates via username + password + ``cid`` + ``sec`` and
    returns an ``accessToken`` plus an ``mdAccessToken`` for the market-data
    socket. We only need the access token for REST calls.
    """

    access_token: str = ""
    md_access_token: str = ""
    user_id: int = 0
    name: str = ""
    expires_at: float = 0.0  # unix seconds
    account_id: int = 0
    created_at: float = 0.0

    @property
    def is_valid(self) -> bool:
        if not self.access_token:
            return False
        # Always re-auth a few minutes before official expiry.
        if self.expires_at:
            return time.time() < (self.expires_at - 60)
        return (time.time() - self.created_at) < SESSION_TTL_SECONDS

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(frozen=True)
class TradovateAccount:
    """A Tradovate account record (typed view of /account/list)."""

    account_id: int
    name: str
    account_type: str
    user_id: int
    legal_status: str = ""
    archived: bool = False


@dataclass(frozen=True)
class TradovateOrderResult:
    """Result of placing a Tradovate order.

    ``success`` is True only when Tradovate accepted the order and returned
    an ``orderId``. ``failure_reason`` carries the broker's rejection text
    when ``success`` is False.
    """

    success: bool
    order_id: int = 0
    fill_id: int = 0
    filled_price: Optional[float] = None
    filled_size: Optional[float] = None
    failure_reason: str = ""
    raw: dict = field(default_factory=dict)


class TradovateClientError(Exception):
    """Raised on any Tradovate API failure.

    ``error_code`` is a stable string the audit log can filter on.
    ``status_code`` is the HTTP code if available (0 if pre-flight).
    """

    def __init__(self, message: str, *, status_code: int = 0, error_code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


# ── The client ───────────────────────────────────────────────────────────


class TradovateClient:
    """Low-level Tradovate REST client — demo by default, live hard-blocked.

    Credentials come from environment variables:

      * ``FIBOKEI_TRADOVATE_USERNAME``
      * ``FIBOKEI_TRADOVATE_PASSWORD``
      * ``FIBOKEI_TRADOVATE_CID``
      * ``FIBOKEI_TRADOVATE_SECRET``
      * ``FIBOKEI_TRADOVATE_APP_ID``
      * ``FIBOKEI_TRADOVATE_APP_VERSION``
      * ``FIBOKEI_TRADOVATE_DEVICE_ID`` (optional)

    Environment selection:

      * ``FIBOKEI_TRADOVATE_ENV=demo|live`` — defaults to ``demo``.
      * ``FIBOKEI_TRADOVATE_LIVE_ALLOWED=true|false`` — must be ``true``
        AND global ``FIBOKEI_LIVE_EXECUTION_ENABLED=true`` for live to be
        permitted.

    Optionally ``FIBOKEI_TRADOVATE_BASE_URL`` overrides the default URL
    (used for testing against a stub).
    """

    def __init__(self) -> None:
        self._username = os.environ.get("FIBOKEI_TRADOVATE_USERNAME", "")
        self._password = os.environ.get("FIBOKEI_TRADOVATE_PASSWORD", "")
        self._cid = os.environ.get("FIBOKEI_TRADOVATE_CID", "")
        self._secret = os.environ.get("FIBOKEI_TRADOVATE_SECRET", "")
        self._app_id = os.environ.get("FIBOKEI_TRADOVATE_APP_ID", "Fiboki")
        self._app_version = os.environ.get("FIBOKEI_TRADOVATE_APP_VERSION", "1.0")
        self._device_id = os.environ.get("FIBOKEI_TRADOVATE_DEVICE_ID", "fiboki-1")
        self._target_account = os.environ.get("FIBOKEI_TRADOVATE_ACCOUNT_ID", "")

        env = os.environ.get("FIBOKEI_TRADOVATE_ENV", "demo").lower().strip() or "demo"
        override = os.environ.get("FIBOKEI_TRADOVATE_BASE_URL", "").strip()
        if override:
            self._base_url = override
            self._env = env
        elif env == "live":
            self._base_url = TRADOVATE_LIVE_BASE
            self._env = "live"
        else:
            self._base_url = TRADOVATE_DEMO_BASE
            self._env = "demo"

        self._session = TradovateSession()
        self._http = httpx.Client(timeout=30.0)
        self._auth_lock = threading.Lock()

    # ── Safety gates ────────────────────────────────────────────────

    def _live_explicitly_allowed(self) -> bool:
        """Live trading requires multiple independent flags to all be true."""
        live_allowed = os.environ.get(
            "FIBOKEI_TRADOVATE_LIVE_ALLOWED", "false"
        ).lower() == "true"
        global_live = os.environ.get(
            "FIBOKEI_LIVE_EXECUTION_ENABLED", "false"
        ).lower() == "true"
        env_is_live = self._env == "live"
        return live_allowed and global_live and env_is_live

    def _ensure_env_allowed(self) -> None:
        """Block live URLs unless every safety gate is explicitly open."""
        if self._base_url == TRADOVATE_LIVE_BASE and not self._live_explicitly_allowed():
            raise TradovateClientError(
                "Tradovate live API is hard-blocked. Set FIBOKEI_TRADOVATE_LIVE_ALLOWED=true "
                "AND FIBOKEI_LIVE_EXECUTION_ENABLED=true AND FIBOKEI_TRADOVATE_ENV=live to enable.",
                status_code=0,
                error_code="LIVE_BLOCKED",
            )

    @property
    def env(self) -> str:
        return self._env

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def has_credentials(self) -> bool:
        return bool(self._username and self._password and self._cid and self._secret)

    # ── Authentication ─────────────────────────────────────────────

    def authenticate(self) -> TradovateSession:
        """Authenticate with Tradovate and return a session.

        Audited 2026-05-08 against the official Tradovate API tutorial repo
        (`tradovate/example-api-js`). Path is lowercase
        ``/auth/accesstokenrequest`` per the EX-1-Simple-Request example.
        Payload field names (``name``, ``password``, ``appId``, ``appVersion``,
        ``cid`` numeric, ``sec``, ``deviceId``) match the official
        ``tutorialsCredentials.js`` template exactly.
        """
        self._ensure_env_allowed()

        if not self.has_credentials:
            raise TradovateClientError(
                "Tradovate credentials not configured. Set FIBOKEI_TRADOVATE_USERNAME, "
                "FIBOKEI_TRADOVATE_PASSWORD, FIBOKEI_TRADOVATE_CID, FIBOKEI_TRADOVATE_SECRET.",
                error_code="MISSING_CREDENTIALS",
            )

        payload = {
            "name": self._username,
            "password": self._password,
            "appId": self._app_id,
            "appVersion": self._app_version,
            "cid": int(self._cid) if self._cid.isdigit() else self._cid,
            "sec": self._secret,
            "deviceId": self._device_id,
        }
        try:
            resp = self._http.post(
                f"{self._base_url}/auth/accesstokenrequest",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise TradovateClientError(
                f"Tradovate auth network error: {e}",
                error_code="NETWORK",
            ) from e

        if resp.status_code != 200:
            raise TradovateClientError(
                f"Tradovate auth failed: HTTP {resp.status_code}",
                status_code=resp.status_code,
                error_code="AUTH_HTTP_ERROR",
            )

        body = resp.json() if resp.content else {}
        access_token = body.get("accessToken", "")
        if not access_token:
            err_text = body.get("errorText") or body.get("p-ticket") or "no accessToken returned"
            raise TradovateClientError(
                f"Tradovate auth rejected: {err_text}",
                status_code=resp.status_code,
                error_code=body.get("errorText", "AUTH_REJECTED"),
            )

        # ``expirationTime`` is an ISO-8601 string; we coerce to epoch loosely.
        expires_at = 0.0
        exp_raw = body.get("expirationTime")
        if isinstance(exp_raw, (int, float)):
            expires_at = float(exp_raw)
        # We don't strictly need exact expiry parsing for Phase 1 — TTL
        # backstop kicks in.

        self._session = TradovateSession(
            access_token=access_token,
            md_access_token=body.get("mdAccessToken", ""),
            user_id=int(body.get("userId", 0) or 0),
            name=body.get("name", "") or "",
            expires_at=expires_at,
            created_at=time.time(),
        )

        logger.info(
            "Tradovate %s session established for user_id=%s", self._env, self._session.user_id
        )
        return self._session

    def ensure_session(self) -> TradovateSession:
        """Re-authenticate if the current session is invalid/expired."""
        if self._session.is_valid:
            return self._session
        with self._auth_lock:
            if not self._session.is_valid:
                self.authenticate()
        return self._session

    # ── Internal request helper ────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        self._ensure_env_allowed()
        session = self.ensure_session()
        headers = {
            **session.headers,
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}{path}"
        try:
            resp = self._http.request(
                method, url, headers=headers, json=json, params=params
            )
        except httpx.HTTPError as e:
            raise TradovateClientError(
                f"Tradovate {method} {path} network error: {e}",
                error_code="NETWORK",
            ) from e

        if resp.status_code >= 400:
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            raise TradovateClientError(
                f"Tradovate API error: {method} {path} → HTTP {resp.status_code}",
                status_code=resp.status_code,
                error_code=body.get("errorText", "HTTP_ERROR"),
            )

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ── Account ─────────────────────────────────────────────────────

    def list_accounts(self) -> list[TradovateAccount]:
        """List accounts visible to the authenticated user.

        TODO_VERIFY: confirm path ``/account/list`` and field names against
        latest Tradovate docs before first real demo run.
        """
        data = self._request("GET", "/account/list") or []
        return [
            TradovateAccount(
                account_id=int(row.get("id", 0) or 0),
                name=row.get("name", "") or "",
                account_type=row.get("accountType", "") or "",
                user_id=int(row.get("userId", 0) or 0),
                legal_status=row.get("legalStatus", "") or "",
                archived=bool(row.get("archived", False)),
            )
            for row in data
        ]

    def get_account_summary(self, account_id: int) -> dict:
        """Fetch cash-balance summary for a single account.

        TODO_VERIFY: ``/cashBalance/getCashBalanceSnapshot`` requires a body
        with ``accountId``. Confirm against current API spec.
        """
        return self._request(
            "POST",
            "/cashBalance/getCashBalanceSnapshot",
            json={"accountId": int(account_id)},
        ) or {}

    # ── Positions ───────────────────────────────────────────────────

    def list_positions(self) -> list[dict]:
        """List open positions for the authenticated user."""
        return self._request("GET", "/position/list") or []

    # ── Orders ──────────────────────────────────────────────────────

    def place_order(self, payload: dict) -> TradovateOrderResult:
        """Place an order.

        ``payload`` is the Tradovate ``/order/placeOrder`` body. Caller is
        responsible for formatting (the adapter does this). Returns a typed
        result. Network/auth errors raise :class:`TradovateClientError`;
        broker rejections come back inside the result with ``success=False``.
        """
        body = self._request("POST", "/order/placeOrder", json=payload) or {}
        order_id = int(body.get("orderId", 0) or 0)
        failure = body.get("failureText") or body.get("failureReason") or ""
        if order_id and not failure:
            return TradovateOrderResult(
                success=True,
                order_id=order_id,
                raw=body,
            )
        return TradovateOrderResult(
            success=False,
            failure_reason=failure or "no orderId returned",
            raw=body,
        )

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a working order. Returns True on success."""
        body = self._request(
            "POST",
            "/order/cancelOrder",
            json={"orderId": int(order_id)},
        ) or {}
        return bool(body.get("commandStatus", "").lower() in ("success", "ok"))

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()
