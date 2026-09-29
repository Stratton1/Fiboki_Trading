"""Myfxbook Community Outlook through its official API (retail sentiment snapshots).

Endpoints (https://www.myfxbook.com/api, read 2026-09-29):

* ``GET /api/login.json?email=&password=`` -> ``{"error": false, "message": "", "session": "..."}``
* ``GET /api/get-community-outlook.json?session=`` -> ``{"error": false, "symbols": [{"name",
  "shortPercentage", "longPercentage", "shortVolume", "longVolume", "longPositions",
  "shortPositions", "totalPositions", "avgShortPrice", "avgLongPrice"}], "general": {...}}``
* ``GET /api/logout.json?session=``

Limits and terms, as the API page states them: the free Community Outlook is
"limited to 100 requests per 24 hours" (paid: 2,880); "Sessions are IP-bound
and expire after 1 month"; "By using the Myfxbook API, you agree to the Terms
of use", and software built on it should be free. The site terms
(https://www.myfxbook.com/terms) say "Reproduction is prohibited by law" of
site material. Hence ``terms_status: personal_only``: record for your own
research, do not republish. Scraping Myfxbook web pages is NOT implemented
and must not be; only this API is used.

The API design puts the email and password in the query string. Both, and
the session id, are scrubbed from every error message. The session is
reused across polls (one login, then one request per poll) and a
:class:`~fiboki.data.providers.ratelimit.DailyRequestBudget` of 100 per UTC day
is enforced before every request, logins included.

No vendor timestamp is published for the outlook, so each poll's reading is
stored with ``vendor_time`` null and is available from ``observed_at`` only.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from fiboki.data.positioning.store import PositioningSnapshot
from fiboki.data.providers.base import AuthenticationRequired, ProviderError
from fiboki.data.providers.macro_base import http_get_text
from fiboki.data.providers.ratelimit import DailyRequestBudget

__all__ = [
    "ENV_MYFXBOOK_EMAIL",
    "ENV_MYFXBOOK_PASSWORD",
    "MYFXBOOK_FREE_DAILY_REQUESTS",
    "MYFXBOOK_SOURCE",
    "MyfxbookOutlookClient",
    "parse_outlook",
]

ENV_MYFXBOOK_EMAIL = "FIBOKI_MYFXBOOK_EMAIL"
ENV_MYFXBOOK_PASSWORD = "FIBOKI_MYFXBOOK_PASSWORD"
MYFXBOOK_SOURCE = "myfxbook_outlook"
MYFXBOOK_FREE_DAILY_REQUESTS = 100
HOST = "https://www.myfxbook.com"

_NUMERIC = ("shortPercentage", "longPercentage", "shortVolume", "longVolume", "longPositions",
            "shortPositions", "totalPositions", "avgShortPrice", "avgLongPrice")


def parse_outlook(payload: Mapping[str, Any], *, symbols: tuple[str, ...] | None = None
                  ) -> list[PositioningSnapshot]:
    if payload.get("error"):
        raise ProviderError(f"Myfxbook outlook error: {payload.get('message') or 'unspecified'}")
    rows = payload.get("symbols")
    if not isinstance(rows, list):
        raise ProviderError("Myfxbook outlook response has no 'symbols' list")
    wanted = None if symbols is None else {s.upper() for s in symbols}
    out: list[PositioningSnapshot] = []
    for r in rows:
        name = str(r.get("name") or "").upper()
        if not name or (wanted is not None and name not in wanted):
            continue
        body = {k: (None if r.get(k) is None else float(r[k])) for k in _NUMERIC}
        out.append(PositioningSnapshot(source=MYFXBOOK_SOURCE, instrument=name,
                                       kind="community_outlook", vendor_time=None, payload=body))
    general = payload.get("general")
    if isinstance(general, Mapping):
        out.append(PositioningSnapshot(
            source=MYFXBOOK_SOURCE, instrument="_GENERAL", kind="community_outlook_general",
            vendor_time=None, payload={k: general[k] for k in sorted(general)}))
    return out


class MyfxbookOutlookClient:
    """Login once, poll the outlook, stay inside the daily request budget."""

    def __init__(self, *, email: str | None, password: str | None, http_client: Any,
                 symbols: tuple[str, ...] | None = None, min_interval_s: float = 3600.0,
                 budget: DailyRequestBudget | None = None) -> None:
        self._email = email
        self._password = password
        self.http_client = http_client
        self.symbols = symbols
        self.key = "myfxbook_outlook"
        self.min_interval_s = float(min_interval_s)
        self.budget = budget if budget is not None else DailyRequestBudget(
            MYFXBOOK_FREE_DAILY_REQUESTS, name="myfxbook")
        self._session: str | None = None
        self.last_report: dict[str, Any] = {}

    @classmethod
    def from_env(cls, http_client: Any, *, env: Mapping[str, str] | None = None,
                 **kwargs: Any) -> MyfxbookOutlookClient:
        environ = os.environ if env is None else env
        return cls(email=(environ.get(ENV_MYFXBOOK_EMAIL) or "").strip() or None,
                   password=(environ.get(ENV_MYFXBOOK_PASSWORD) or "") or None,
                   http_client=http_client, **kwargs)

    def _secrets(self) -> tuple[str, ...]:
        return tuple(s for s in (self._email, self._password, self._session) if s)

    def _get(self, path: str, params: Mapping[str, Any], what: str) -> dict[str, Any]:
        self.budget.take()
        text = http_get_text(self.http_client, f"{HOST}{path}", params=params,
                             secrets=self._secrets(), what=what)
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ProviderError(f"{what}: expected a JSON object")
        return payload

    def _login(self) -> str:
        if not self._email or not self._password:
            raise AuthenticationRequired(
                f"Myfxbook needs {ENV_MYFXBOOK_EMAIL} and {ENV_MYFXBOOK_PASSWORD}")
        payload = self._get("/api/login.json", {"email": self._email, "password": self._password},
                            "Myfxbook login")
        session = str(payload.get("session") or "")
        if payload.get("error") or not session:
            msg = str(payload.get("message") or "login refused")
            for s in self._secrets():
                msg = msg.replace(s, "***")
            raise AuthenticationRequired(f"Myfxbook login failed: {msg}")
        self._session = session
        return session

    def fetch(self) -> list[PositioningSnapshot]:
        session = self._session or self._login()
        payload = self._get("/api/get-community-outlook.json", {"session": session},
                            "Myfxbook community outlook")
        if payload.get("error") and "session" in str(payload.get("message", "")).lower():
            # Expired or IP-changed session: one re-login, then give up until the next poll.
            self._session = None
            payload = self._get("/api/get-community-outlook.json", {"session": self._login()},
                                "Myfxbook community outlook")
        self.last_report = {"budget_remaining": self.budget.remaining}
        return parse_outlook(payload, symbols=self.symbols)

    def logout(self) -> None:
        if self._session:
            try:
                self._get("/api/logout.json", {"session": self._session}, "Myfxbook logout")
            finally:
                self._session = None
