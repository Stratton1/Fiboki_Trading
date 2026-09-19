"""Authentication, CSRF defence, rate limiting and RBAC.

The V1 failures this closes:

* **No Origin check on mutating requests.** V1's session cookie was sent on
  cross-site form posts, and several dangerous endpoints took no body
  (``POST /bots/{id}/approve``, ``POST /system/killswitch``). A no-body POST is
  a "simple request": the browser sends it with credentials and never fires a
  preflight, so CORS configuration alone stops nothing. Here every mutating
  request must carry an ``Origin`` (or, for older agents, a ``Referer``) whose
  scheme+host+port is on an exact allow-list, and a double-submit CSRF token.
* **Unlimited login attempts.**
* **A ``require_admin`` that existed but was applied to two routes.** Here the
  admin dependency is applied by the router factory, and
  ``tests/api/test_security.py`` enumerates every mutating route and asserts it.
* **Tokens in URLs.** Nothing in this module reads a credential from a query
  string or a path parameter; the session lives only in an httpOnly cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any

from fastapi import Depends, Request, Response, status

from fiboki.api.errors import ApiError
from fiboki.api.settings import Settings

__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "Principal",
    "Role",
    "SessionStore",
    "current_principal",
    "issue_session",
    "require_admin",
    "require_operator",
    "validate_origin",
]

CSRF_COOKIE = "fiboki_csrf"
CSRF_HEADER = "X-Fiboki-CSRF"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class Role(str, Enum):
    """Least privilege first. ``VIEWER`` cannot mutate anything at all."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return {"viewer": 0, "operator": 1, "admin": 2}[self.value]

    def satisfies(self, required: Role) -> bool:
        return self.rank >= required.rank


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    display_name: str
    role: Role
    session_id: str
    issued_at: float
    expires_at: float

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


# ------------------------------------------------------------- signing


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign_session(payload: dict[str, Any], secret: bytes) -> str:
    """``base64(json).base64(hmac)``. Opaque to the browser; verified here."""
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    mac = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def verify_session(token: str, secret: bytes) -> dict[str, Any] | None:
    try:
        body, mac = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _unb64(mac)
    except Exception:
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        return json.loads(_unb64(body))
    except Exception:
        return None


# -------------------------------------------------------------- store


class SessionStore:
    """Server-side revocation list.

    A signed cookie alone cannot be revoked before it expires, which is not
    acceptable for an account that can arm a kill switch. The signature proves
    the cookie was minted here; this store decides whether it is still valid.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def open(self, session_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._sessions[session_id] = record

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(session_id)
        if record is None:
            return None
        if record["expires_at"] <= time.time():
            self.revoke(session_id)
            return None
        return record

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def revoke_user(self, user_id: str) -> int:
        with self._lock:
            doomed = [s for s, r in self._sessions.items() if r["user_id"] == user_id]
            for s in doomed:
                self._sessions.pop(s, None)
        return len(doomed)

    def active(self) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for r in self._sessions.values() if r["expires_at"] > now)


# --------------------------------------------------------- rate limiter


class LoginRateLimiter:
    """Fixed-window attempt counter with a lockout, keyed by identity+source.

    Keyed by both so one user's typos cannot lock the whole office out, and one
    source cannot enumerate usernames without tripping its own limit.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._attempts: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _key(self, identity: str, source: str) -> str:
        return f"{identity.lower()}|{source}"

    def check(self, identity: str, source: str) -> None:
        key = self._key(identity, source)
        now = time.time()
        with self._lock:
            until = self._locked_until.get(key, 0.0)
            if until > now:
                raise ApiError(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "login_locked_out",
                    "Too many failed sign-in attempts. Try again shortly.",
                    headers={"Retry-After": str(int(until - now))},
                )

    def record_failure(self, identity: str, source: str) -> None:
        key = self._key(identity, source)
        now = time.time()
        window = self._settings.login_window_seconds
        with self._lock:
            recent = [t for t in self._attempts.get(key, []) if now - t < window]
            recent.append(now)
            self._attempts[key] = recent
            if len(recent) >= self._settings.login_max_attempts:
                self._locked_until[key] = now + self._settings.login_lockout_seconds
                self._attempts[key] = []

    def record_success(self, identity: str, source: str) -> None:
        key = self._key(identity, source)
        with self._lock:
            self._attempts.pop(key, None)
            self._locked_until.pop(key, None)


# ------------------------------------------------------------- origin


def _normalise(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def origin_of(request: Request) -> tuple[str, str]:
    """Return ``(origin, source_header)``. Referer is reduced to its origin."""
    origin = request.headers.get("origin")
    if origin:
        return _normalise(origin), "origin"
    referer = request.headers.get("referer")
    if referer:
        try:
            from urllib.parse import urlsplit

            parts = urlsplit(referer)
            if parts.scheme and parts.netloc:
                return _normalise(f"{parts.scheme}://{parts.netloc}"), "referer"
        except Exception:
            pass
    return "", "none"


def validate_origin(request: Request, allowed: Iterable[str]) -> None:
    """Refuse any mutating request that does not prove where it came from.

    Absence is a refusal, not a pass. A cross-site form post carries an Origin;
    a request with none is either a non-browser client (which should send one
    anyway, and the deployment can allow-list it) or an attempt to slip past a
    check that treats 'missing' as 'same site'. V1 had neither branch.
    """
    if request.method in _SAFE_METHODS:
        return
    allow = {_normalise(a) for a in allowed if a.strip()}
    origin, source = origin_of(request)
    if not origin:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "origin_missing",
            "This request carried no Origin or Referer header. Mutating requests "
            "must identify the page that made them.",
        )
    if origin not in allow:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "origin_not_allowed",
            "The requesting origin is not on this deployment's allow-list.",
            context={"origin_header": source},
        )


def validate_csrf(request: Request) -> None:
    """Double-submit: the header must equal the cookie.

    Belt and braces with the Origin check. An attacker's page can cause the
    cookie to be sent but cannot read it to populate the header, because the
    CSRF cookie is same-site and the session cookie is httpOnly.
    """
    if request.method in _SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "csrf_failed",
            "The CSRF token was missing or did not match. Reload the page and retry.",
        )


# ------------------------------------------------------------ sessions


def issue_session(
    response: Response,
    *,
    settings: Settings,
    store: SessionStore,
    user_id: str,
    display_name: str,
    role: Role,
) -> Principal:
    now = time.time()
    session_id = secrets.token_urlsafe(24)
    expires_at = now + settings.session_ttl_seconds
    record = {
        "user_id": user_id,
        "display_name": display_name,
        "role": role.value,
        "issued_at": now,
        "expires_at": expires_at,
    }
    store.open(session_id, record)
    token = sign_session({"sid": session_id, "exp": expires_at}, settings.session_secret)

    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        **settings.cookie_kwargs,  # type: ignore[arg-type]
    )
    # The CSRF cookie is deliberately readable by the page's own JavaScript so
    # it can echo it into the header. It is not a credential on its own.
    csrf = secrets.token_urlsafe(24)
    csrf_kwargs = dict(settings.cookie_kwargs)
    csrf_kwargs["httponly"] = False
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=settings.session_ttl_seconds,
        **csrf_kwargs,  # type: ignore[arg-type]
    )
    return Principal(
        user_id=user_id,
        display_name=display_name,
        role=role,
        session_id=session_id,
        issued_at=now,
        expires_at=expires_at,
    )


def clear_session(response: Response, settings: Settings) -> None:
    for name in (settings.cookie_name, CSRF_COOKIE):
        response.delete_cookie(name, path="/", domain=settings.cookie_domain)


def read_principal(request: Request) -> Principal | None:
    settings: Settings = request.app.state.settings
    store: SessionStore = request.app.state.sessions
    raw = request.cookies.get(settings.cookie_name)
    if not raw:
        return None
    payload = verify_session(raw, settings.session_secret)
    if not payload:
        return None
    record = store.get(str(payload.get("sid", "")))
    if record is None:
        return None
    return Principal(
        user_id=record["user_id"],
        display_name=record["display_name"],
        role=Role(record["role"]),
        session_id=str(payload["sid"]),
        issued_at=record["issued_at"],
        expires_at=record["expires_at"],
    )


# --------------------------------------------------------- dependencies


def current_principal(request: Request) -> Principal:
    principal = read_principal(request)
    if principal is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "not_authenticated",
            "Sign in to continue.",
        )
    return principal


def _require(role: Role):
    def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        settings: Settings = request.app.state.settings
        validate_origin(request, settings.allowed_origins)
        validate_csrf(request)
        if not principal.role.satisfies(role):
            # Recorded, not merely refused: an operator reaching for an admin
            # control is a fact an incident review wants.
            trail = getattr(request.app.state, "audit", None)
            if trail is not None:
                from fiboki.api.logging import current_correlation_id

                trail.record(
                    f"rbac.denied:{request.method} {request.url.path}",
                    actor=principal.user_id,
                    actor_role=principal.role.value,
                    outcome="refused",
                    reason=f"requires {role.value}",
                    target=request.url.path,
                    execution_mode=settings.execution_mode.value,
                    correlation_id=current_correlation_id(),
                    source_ip=request.client.host if request.client else "",
                )
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "insufficient_role",
                f"This action requires the {role.value} role. "
                f"You are signed in as {principal.role.value}.",
                context={"required_role": role.value, "your_role": principal.role.value},
            )
        return principal

    return dependency


require_operator = _require(Role.OPERATOR)
require_admin = _require(Role.ADMIN)
