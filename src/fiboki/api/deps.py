"""FastAPI dependencies. Thin: they resolve app state, they do not compute."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from fiboki.api.audit_trail import ApiAuditTrail
from fiboki.api.platform import Platform
from fiboki.api.security import Principal, current_principal
from fiboki.api.settings import Settings

__all__ = [
    "AnyPrincipal",
    "AuditDep",
    "PlatformDep",
    "SettingsDep",
    "get_audit",
    "get_platform",
    "get_settings",
]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_platform(request: Request) -> Platform:
    return request.app.state.platform


def get_audit(request: Request) -> ApiAuditTrail:
    return request.app.state.audit


SettingsDep = Annotated[Settings, Depends(get_settings)]
PlatformDep = Annotated[Platform, Depends(get_platform)]
AuditDep = Annotated[ApiAuditTrail, Depends(get_audit)]
AnyPrincipal = Annotated[Principal, Depends(current_principal)]
