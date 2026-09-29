"""Sign in, sign out, and 'who am I'.

Credentials never appear in a URL, a query string or a log line. The session
lives in an httpOnly + Secure + SameSite cookie, and a CSRF token is issued
alongside it as a readable cookie for double-submit.

The operator directory is read from ``FIBOKI_OPERATORS`` as
``user:role:<hash>`` triples. ``<hash>`` is versioned by its prefix:

* ``scrypt$<n>$<r>$<p>$<salt b64>$<key b64>``: salted scrypt (RFC 7914), the
  format :func:`hash_password` writes. Generate one with
  ``python -c "from fiboki.api.routers.auth import hash_password as h; print(h('...'))"``.
* bare 64-character hex, or ``sha256$<hex>``: LEGACY unsalted SHA-256. Still
  verified so no operator is locked out by an upgrade, but it is a weak hash
  (one GPU guesses billions a second and identical passwords share a hash):
  every successful legacy login logs a warning and ``docs/v2/SECURITY_MODEL.md``
  says to rotate. Audit F P2-16.

The final comparison is :func:`hmac.compare_digest`, and a miss still performs
a full scrypt derivation against a dummy so the response time does not
distinguish an unknown user from a wrong password.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.security import (
    Principal,
    Role,
    clear_session,
    current_principal,
    issue_session,
    validate_csrf,
    validate_origin,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_log = logging.getLogger("fiboki.api.auth")

#: scrypt cost. n=2**14, r=8 is ~16 MiB and tens of milliseconds per check:
#: negligible for two operators, expensive for an offline guesser.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024
SCRYPT_PREFIX = "scrypt$"
LEGACY_PREFIX = "sha256$"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
        dklen=_SCRYPT_DKLEN, maxmem=_SCRYPT_MAXMEM,
    )


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """``scrypt$n$r$p$salt$key``: the format new directory entries should use.

    Contains no ``:`` or ``,``, so it drops straight into ``FIBOKI_OPERATORS``.
    """
    if not password:
        raise ValueError("refusing to hash an empty password")
    salt = salt if salt is not None else secrets.token_bytes(16)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{SCRYPT_PREFIX}{SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def is_legacy_hash(stored: str) -> bool:
    """True for an unsalted SHA-256 entry, which should be rotated."""
    return not stored.startswith(SCRYPT_PREFIX)


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a versioned stored hash.

    Unrecognised formats verify as ``False``; they never raise, so a typo in
    the directory is a refused login, not a 500.
    """
    if stored.startswith(SCRYPT_PREFIX):
        try:
            n_text, r_text, p_text, salt_text, key_text = stored[len(SCRYPT_PREFIX):].split("$")
            n, r, p = int(n_text), int(r_text), int(p_text)
            salt, expected = _unb64(salt_text), _unb64(key_text)
            supplied = hashlib.scrypt(
                password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                dklen=len(expected), maxmem=_SCRYPT_MAXMEM,
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(supplied, expected)
    legacy = stored[len(LEGACY_PREFIX):] if stored.startswith(LEGACY_PREFIX) else stored
    supplied_hex = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(supplied_hex, legacy.lower())


#: Verified on every miss so an unknown user costs the same as a known one.
_DUMMY_HASH = hash_password("fiboki-timing-equaliser", salt=b"\x00" * 16)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class PrincipalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    display_name: str
    role: str
    expires_at: datetime
    #: Rendered next to every admin-only control so the operator knows in
    #: advance whether a button will work.
    can_arm_kill_switch: bool
    can_promote: bool


def _directory() -> dict[str, tuple[str, str]]:
    """``{username: (role, stored_hash)}`` from ``FIBOKI_OPERATORS``."""
    raw = os.environ.get("FIBOKI_OPERATORS", "").strip()
    out: dict[str, tuple[str, str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            continue
        user, role, digest = parts
        stored = digest.strip()
        # Hex is case-insensitive; base64 in a scrypt entry is NOT.
        if not stored.startswith(SCRYPT_PREFIX):
            stored = stored.lower()
        out[user.strip().lower()] = (role.strip().lower(), stored)
    return out


def _view(principal: Principal) -> PrincipalView:
    return PrincipalView(
        user_id=principal.user_id,
        display_name=principal.display_name,
        role=principal.role.value,
        expires_at=datetime.fromtimestamp(principal.expires_at, tz=UTC),
        can_arm_kill_switch=principal.is_admin,
        can_promote=principal.is_admin,
    )


@router.post("/login", response_model=PrincipalView)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    audit: AuditDep,
) -> PrincipalView:
    validate_origin(request, settings.allowed_origins)
    limiter = request.app.state.login_limiter
    source = request.client.host if request.client else "unknown"
    username = body.username.strip().lower()
    limiter.check(username, source)

    entry = _directory().get(username)
    stored = entry[1] if entry else _DUMMY_HASH
    ok = verify_password(body.password, stored) and entry is not None
    if ok and is_legacy_hash(stored):
        _log.warning(
            "operator %s signed in with a LEGACY unsalted sha256 hash; rotate it to "
            "scrypt (fiboki.api.routers.auth.hash_password)",
            username,
        )

    if not ok:
        limiter.record_failure(username, source)
        audit.record(
            "auth.login",
            actor=username,
            actor_role="unknown",
            outcome="refused",
            reason="invalid credentials",
            execution_mode=settings.execution_mode.value,
            correlation_id=current_correlation_id(),
            source_ip=source,
        )
        # One message for both failure modes: a different message for "no such
        # user" is a user-enumeration oracle.
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            "That username and password did not match.",
        )

    limiter.record_success(username, source)
    assert entry is not None  # ok implies it; stated for the type checker
    try:
        role = Role(entry[0])
    except ValueError:
        role = Role.VIEWER
    principal = issue_session(
        response,
        settings=settings,
        store=request.app.state.sessions,
        user_id=username,
        display_name=username,
        role=role,
    )
    audit.record(
        "auth.login",
        actor=username,
        actor_role=role.value,
        outcome="allowed",
        reason="operator sign-in",
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=source,
    )
    return _view(principal)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(request: Request, settings: SettingsDep) -> Response:
    validate_origin(request, settings.allowed_origins)
    validate_csrf(request)
    from fiboki.api.security import read_principal

    principal = read_principal(request)
    if principal is not None:
        request.app.state.sessions.revoke(principal.session_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session(response, settings)
    return response


@router.get("/me", response_model=PrincipalView)
def me(request: Request) -> PrincipalView:
    return _view(current_principal(request))
