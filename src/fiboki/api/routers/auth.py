"""Sign in, sign out, and 'who am I'.

Credentials never appear in a URL, a query string or a log line. The session
lives in an httpOnly + Secure + SameSite cookie, and a CSRF token is issued
alongside it as a readable cookie for double-submit.

The operator directory is read from ``FIBOKI_OPERATORS`` as
``user:role:sha256hex`` triples. Passwords are compared with
:func:`hmac.compare_digest` against a stored hash, and a miss still performs the
comparison so the response time does not distinguish an unknown user from a
wrong password.
"""
from __future__ import annotations

import hashlib
import hmac
import os
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

_DUMMY_HASH = hashlib.sha256(b"fiboki-timing-equaliser").hexdigest()


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
    """``{username: (role, sha256_hex)}`` from ``FIBOKI_OPERATORS``."""
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
        out[user.strip().lower()] = (role.strip().lower(), digest.strip().lower())
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
    supplied = hashlib.sha256(body.password.encode("utf-8")).hexdigest()
    expected = entry[1] if entry else _DUMMY_HASH
    ok = hmac.compare_digest(supplied, expected) and entry is not None

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
