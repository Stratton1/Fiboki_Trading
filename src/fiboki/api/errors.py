"""Error responses that say enough to act on and nothing an attacker can use.

The body carries a stable ``code``, a human ``detail`` written for an operator,
and the request's correlation id. It never carries a traceback, a file path, a
SQL fragment, a driver message or a settings value. Those go to the log, joined
to the same correlation id.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fiboki.api.logging import current_correlation_id

__all__ = ["ApiError", "ErrorBody", "install_exception_handlers"]

log = logging.getLogger("fiboki.api.error")


class ErrorBody(BaseModel):
    code: str
    detail: str
    correlation_id: str = ""
    #: Optional structured hints, e.g. the failed mode-guard controls. Only
    #: ever populated by code that deliberately chose the contents.
    context: dict[str, Any] = {}


class ApiError(HTTPException):
    """An error whose message was written to be shown to an operator."""

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        *,
        context: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.context = context or {}


def _body(code: str, detail: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return ErrorBody(
        code=code,
        detail=detail,
        correlation_id=current_correlation_id(),
        context=context or {},
    ).model_dump(mode="json")


_GENERIC_BY_STATUS = {
    status.HTTP_401_UNAUTHORIZED: ("not_authenticated", "Sign in to continue."),
    status.HTTP_403_FORBIDDEN: ("forbidden", "You do not have access to this action."),
    status.HTTP_404_NOT_FOUND: ("not_found", "That resource does not exist."),
    status.HTTP_405_METHOD_NOT_ALLOWED: ("method_not_allowed", "Method not allowed."),
    status.HTTP_429_TOO_MANY_REQUESTS: ("rate_limited", "Too many attempts. Try later."),
}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        log.warning(
            "api_error",
            extra={"code": exc.code, "status": exc.status_code, "context": exc.context},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, str(exc.detail), exc.context),
            headers=exc.headers,
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code, generic = _GENERIC_BY_STATUS.get(exc.status_code, ("error", "Request failed."))
        # A raw HTTPException raised deep in a dependency may carry an internal
        # message. Client errors we authored read fine; anything 5xx is replaced.
        detail = generic if exc.status_code >= 500 else str(exc.detail or generic)
        log.warning("http_error", extra={"status": exc.status_code, "code": code})
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field names and positions are safe and necessary; the submitted input
        # is not echoed back, because it may contain a credential.
        fields = [
            {"loc": [str(p) for p in err.get("loc", ())], "type": err.get("type", "")}
            for err in exc.errors()
        ][:20]
        log.info("validation_error", extra={"fields": fields})
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_body(
                "invalid_request",
                "The request did not match the expected shape.",
                {"fields": fields},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Full detail to the log; a correlation id and nothing else to the caller.
        log.exception("unhandled_exception", extra={"exc_class": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_body(
                "internal_error",
                "The request failed inside the platform. Quote the correlation id "
                "to an operator; the cause is in the server log.",
            ),
        )
