"""Application assembly.

Order matters. The correlation-id middleware runs outermost so that even a
failure inside CORS handling carries an id; the CSRF/Origin checks run inside
the route dependencies, where they can see the resolved principal and write an
audit row.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from fiboki.api.errors import install_exception_handlers
from fiboki.api.health import HealthReport, build_health
from fiboki.api.logging import (
    configure_logging,
    new_correlation_id,
    set_actor,
    set_correlation_id,
)
from fiboki.api.platform import build_platform
from fiboki.api.routers import auth as auth_router
from fiboki.api.routers import intelligence as intelligence_router
from fiboki.api.routers import markets as markets_router
from fiboki.api.routers import research as research_router
from fiboki.api.routers import system as system_router
from fiboki.api.routers import trading as trading_router
from fiboki.api.security import CSRF_HEADER, LoginRateLimiter, SessionStore
from fiboki.api.settings import Settings, load_settings

log = logging.getLogger("fiboki.api")

__all__ = ["create_app"]

CORRELATION_HEADER = "X-Correlation-Id"


def create_app(settings: Settings | None = None, *, configure_logs: bool = True) -> FastAPI:
    settings = settings or load_settings()
    if configure_logs:
        configure_logging()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        platform = build_platform(settings)
        application.state.settings = settings
        application.state.platform = platform
        application.state.audit = platform.audit
        application.state.sessions = SessionStore()
        application.state.login_limiter = LoginRateLimiter(settings)
        report = build_health(platform, settings)
        log.info(
            "startup",
            extra={
                "execution_mode": settings.execution_mode.value,
                "health": report.status,
                "allowed_origins": len(settings.allowed_origins),
                "live_compiled_in": platform.mode_state()["live_compiled_in"],
            },
        )
        yield
        log.info("shutdown", extra={"uptime_seconds": round(platform.uptime_seconds, 1)})

    app = FastAPI(
        title="Fiboki V2 API",
        version="2.0.0",
        description=(
            "Every numeric field in every response carries a Provenance. There is "
            "no endpoint that can enable live execution."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # Populate state eagerly too, so TestClient use without a lifespan still
    # works and so a misconfiguration fails at construction rather than at the
    # first request.
    platform = build_platform(settings)
    app.state.settings = settings
    app.state.platform = platform
    app.state.audit = platform.audit
    app.state.sessions = SessionStore()
    app.state.login_limiter = LoginRateLimiter(settings)

    @app.middleware("http")
    async def correlate(request: Request, call_next: Callable) -> Response:
        # An inbound id is accepted only if it looks like one of ours; otherwise
        # a caller could inject newlines into the log stream.
        inbound = request.headers.get(CORRELATION_HEADER, "")
        cid = inbound if inbound.isalnum() and len(inbound) <= 64 else new_correlation_id()
        set_correlation_id(cid)
        set_actor("")
        started = time.perf_counter()
        response = await call_next(request)
        duration = round((time.perf_counter() - started) * 1000, 2)
        response.headers[CORRELATION_HEADER] = cid
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration,
            },
        )
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Cache-Control", "no-store, no-cache, must-revalidate, private"
        )
        return response

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", CSRF_HEADER, CORRELATION_HEADER],
            expose_headers=[CORRELATION_HEADER],
            max_age=600,
        )

    install_exception_handlers(app)

    @app.get("/api/health", response_model=HealthReport, tags=["system"])
    def health(request: Request) -> HealthReport:
        """Measured, not asserted. See :mod:`fiboki.api.health`."""
        return build_health(request.app.state.platform, request.app.state.settings)

    @app.get("/api/version", tags=["system"])
    def version(request: Request) -> dict[str, Any]:
        s: Settings = request.app.state.settings
        return {
            "api_version": "2.0.0",
            "build_sha": s.build_sha or None,
            "build_time": s.build_time or None,
            "execution_mode": s.execution_mode.value,
        }

    for module in (
        auth_router,
        system_router,
        trading_router,
        research_router,
        markets_router,
        intelligence_router,
    ):
        app.include_router(module.router)

    return app


app = None  # populated by ASGI servers via the factory below


def asgi_factory() -> FastAPI:  # pragma: no cover - uvicorn entry point
    return create_app()
