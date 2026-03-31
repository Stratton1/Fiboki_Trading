"""IG REST API client for demo account integration.

Handles authentication, session management, and low-level API calls.
Only supports IG demo environment — production URLs are explicitly blocked.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# IG demo API base URL — production is intentionally NOT supported
IG_DEMO_BASE = "https://demo-api.ig.com/gateway/deal"
IG_PROD_BASE = "https://api.ig.com/gateway/deal"

# Session token lifetime: IG tokens last ~6 hours; re-auth at 5h
SESSION_TTL_SECONDS = 5 * 3600


@dataclass
class IGSession:
    """Holds IG API session state."""

    cst: str = ""
    x_security_token: str = ""
    account_id: str = ""
    created_at: float = 0.0

    @property
    def is_valid(self) -> bool:
        if not self.cst or not self.x_security_token:
            return False
        return (time.time() - self.created_at) < SESSION_TTL_SECONDS

    @property
    def headers(self) -> dict[str, str]:
        return {
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.x_security_token,
        }


class IGClientError(Exception):
    """Raised on IG API errors."""

    def __init__(self, message: str, status_code: int = 0, error_code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class IGClient:
    """Low-level IG REST API client — demo only.

    Credentials come from environment variables:
      - FIBOKEI_IG_API_KEY
      - FIBOKEI_IG_USERNAME
      - FIBOKEI_IG_PASSWORD
      - FIBOKEI_IG_ACCOUNT_ID (optional — selects sub-account)
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("FIBOKEI_IG_API_KEY", "")
        self._username = os.environ.get("FIBOKEI_IG_USERNAME", "")
        self._password = os.environ.get("FIBOKEI_IG_PASSWORD", "")
        self._target_account = os.environ.get("FIBOKEI_IG_ACCOUNT_ID", "")
        self._base_url = IG_DEMO_BASE
        self._session = IGSession()
        self._http = httpx.Client(timeout=30.0)
        self._auth_lock = threading.Lock()  # Prevent concurrent re-auth storms

    def _ensure_demo_only(self) -> None:
        """Hard block against production usage."""
        if self._base_url == IG_PROD_BASE:
            raise IGClientError(
                "Production IG API is blocked. Only demo is supported.",
                status_code=0,
                error_code="PRODUCTION_BLOCKED",
            )

    def _common_headers(self) -> dict[str, str]:
        return {
            "X-IG-API-KEY": self._api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
        }

    def authenticate(self) -> IGSession:
        """Authenticate with IG demo API and return a session."""
        self._ensure_demo_only()

        if not self._api_key or not self._username or not self._password:
            raise IGClientError(
                "IG credentials not configured. Set FIBOKEI_IG_API_KEY, "
                "FIBOKEI_IG_USERNAME, FIBOKEI_IG_PASSWORD.",
                error_code="MISSING_CREDENTIALS",
            )

        headers = {**self._common_headers(), "VERSION": "2"}
        payload = {
            "identifier": self._username,
            "password": self._password,
        }

        resp = self._http.post(
            f"{self._base_url}/session", headers=headers, json=payload
        )
        if resp.status_code != 200:
            raise IGClientError(
                f"IG auth failed: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
                error_code=resp.json().get("errorCode", "UNKNOWN"),
            )

        self._session = IGSession(
            cst=resp.headers.get("CST", ""),
            x_security_token=resp.headers.get("X-SECURITY-TOKEN", ""),
            account_id=resp.json().get("currentAccountId", ""),
            created_at=time.time(),
        )

        # Switch to target account if specified
        if self._target_account and self._session.account_id != self._target_account:
            self._switch_account(self._target_account)

        logger.info("IG demo session established for account %s", self._session.account_id)
        return self._session

    def _switch_account(self, account_id: str) -> None:
        """Switch to a different IG sub-account."""
        headers = {
            **self._common_headers(),
            **self._session.headers,
            "VERSION": "1",
        }
        resp = self._http.put(
            f"{self._base_url}/session",
            headers=headers,
            json={"accountId": account_id},
        )
        if resp.status_code != 200:
            raise IGClientError(
                f"Account switch failed: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
            )
        self._session.account_id = account_id

    def ensure_session(self) -> IGSession:
        """Return a valid session, re-authenticating if needed.

        Uses a threading lock so concurrent requests don't all try to
        re-authenticate simultaneously, which would spam the IG session
        endpoint and risk rate-limiting.
        """
        if self._session.is_valid:
            return self._session
        with self._auth_lock:
            # Re-check inside the lock — another thread may have authenticated
            # while we waited.
            if not self._session.is_valid:
                self.authenticate()
        return self._session

    def _request(
        self, method: str, path: str, *, version: str = "1", json: dict | None = None,
        delete_method_override: bool = False,
    ) -> dict:
        """Make an authenticated API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            path: API path (e.g., "/positions").
            version: IG API version header.
            json: Request body.
            delete_method_override: IG requires POST with _method=DELETE for
                some endpoints; set True for those.
        """
        self._ensure_demo_only()
        session = self.ensure_session()

        headers = {
            **self._common_headers(),
            **session.headers,
            "VERSION": version,
        }

        url = f"{self._base_url}{path}"

        if delete_method_override:
            headers["_method"] = "DELETE"
            resp = self._http.post(url, headers=headers, json=json)
        else:
            resp = self._http.request(method, url, headers=headers, json=json)

        if resp.status_code >= 400:
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            raise IGClientError(
                f"IG API error: {method} {path} → {resp.status_code}",
                status_code=resp.status_code,
                error_code=body.get("errorCode", "UNKNOWN"),
            )

        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ── Account ──────────────────────────────────────────────────────

    def get_accounts(self) -> list[dict]:
        """Get all accounts."""
        data = self._request("GET", "/accounts")
        return data.get("accounts", [])

    def get_account_info(self) -> dict:
        """Get current account balance and margin info."""
        accounts = self.get_accounts()
        for acct in accounts:
            if acct.get("accountId") == self._session.account_id:
                return acct
        return accounts[0] if accounts else {}

    # ── Positions ────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Get all open positions."""
        data = self._request("GET", "/positions", version="2")
        return data.get("positions", [])

    def open_position(self, params: dict) -> dict:
        """Open a new position (OTC deal).

        Required params: epic, direction (BUY/SELL), size, orderType,
        currencyCode. Optional: stopLevel, limitLevel, etc.
        """
        return self._request("POST", "/positions/otc", version="2", json=params)

    def close_position(self, deal_id: str, direction: str, size: float) -> dict:
        """Close an open position.

        direction should be the *opposite* of the opening direction.
        """
        payload = {
            "dealId": deal_id,
            "direction": direction,
            "size": str(size),
            "orderType": "MARKET",
        }
        return self._request(
            "DELETE", "/positions/otc",
            version="1",
            json=payload,
            delete_method_override=True,
        )

    def update_position(self, deal_id: str, stop_level: float | None = None,
                        limit_level: float | None = None) -> dict:
        """Update stop/limit on an open position."""
        payload: dict = {}
        if stop_level is not None:
            payload["stopLevel"] = stop_level
        if limit_level is not None:
            payload["limitLevel"] = limit_level
        return self._request("PUT", f"/positions/otc/{deal_id}", version="2", json=payload)

    # ── Orders (working orders) ──────────────────────────────────────

    def get_working_orders(self) -> list[dict]:
        """Get all working orders."""
        data = self._request("GET", "/workingorders", version="2")
        return data.get("workingOrders", [])

    def create_working_order(self, params: dict) -> dict:
        """Create a working order (limit/stop entry)."""
        return self._request("POST", "/workingorders/otc", version="2", json=params)

    def delete_working_order(self, deal_id: str) -> dict:
        """Delete a working order."""
        return self._request("DELETE", f"/workingorders/otc/{deal_id}", version="2")

    def update_working_order(self, deal_id: str, changes: dict) -> dict:
        """Update a working order."""
        return self._request("PUT", f"/workingorders/otc/{deal_id}", version="2", json=changes)

    # ── Deal confirmation ────────────────────────────────────────────

    def get_deal_confirmation(self, deal_reference: str) -> dict:
        """Get confirmation for a deal reference (returned by open/close)."""
        return self._request("GET", f"/confirms/{deal_reference}")

    # ── Market info ──────────────────────────────────────────────────

    def get_market(self, epic: str) -> dict:
        """Get market details for an epic."""
        return self._request("GET", f"/markets/{epic}", version="3")

    def get_prices(
        self,
        epic: str,
        resolution: str = "HOUR",
        num_points: int = 200,
    ) -> dict:
        """Fetch recent price candles from IG REST API.

        Args:
            epic: IG market epic (e.g. "CS.D.EURUSD.CFD.IP").
            resolution: MINUTE, MINUTE_5, MINUTE_15, MINUTE_30,
                HOUR, HOUR_4, DAY, WEEK.
            num_points: Number of candles (max 200 per IG).

        Returns:
            dict with "prices" list from IG. Each price entry has:
            snapshotTime, openPrice, highPrice, lowPrice, closePrice,
            lastTradedVolume.
        """
        return self._request(
            "GET",
            f"/prices/{epic}/{resolution}/{num_points}",
            version="3",
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._http.close()
