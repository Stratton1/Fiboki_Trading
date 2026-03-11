"""FastAPI application for Fiboki Trading."""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fibokei.api.auth import Base
from fibokei.api.routes.auth import router as auth_router
from fibokei.api.routes.instruments import router as instruments_router
from fibokei.api.routes.strategies import router as strategies_router

_raw_db_url = os.environ.get("FIBOKEI_DATABASE_URL") or os.environ.get("DATABASE_URL", "sqlite:///fibokei.db")
# Render/Railway may provide postgres:// which SQLAlchemy 2.0 rejects
if _raw_db_url.startswith("postgres://"):
    DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = _raw_db_url


def _configure_logging() -> None:
    """Configure structured logging based on environment."""
    is_production = not os.environ.get("FIBOKEI_LOCAL_DEV")
    level = logging.INFO

    if is_production:
        # JSON-style structured logging for production
        fmt = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
    else:
        # Human-readable for local dev
        fmt = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

    logging.basicConfig(level=level, format=fmt, force=True)
    # Quieten noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _validate_required_env_vars() -> None:
    """Fail fast if critical environment variables are missing in production."""
    logger = logging.getLogger("fibokei.startup")
    is_production = not os.environ.get("FIBOKEI_LOCAL_DEV")

    if not is_production:
        logger.info("Local dev mode — skipping env var validation")
        return

    required = {
        "FIBOKEI_JWT_SECRET": "JWT signing key (min 32 chars)",
        "FIBOKEI_CORS_ORIGINS": "Allowed CORS origins",
    }
    # Database URL can come from either var
    has_db = os.environ.get("FIBOKEI_DATABASE_URL") or os.environ.get("DATABASE_URL")

    missing = []
    if not has_db:
        missing.append("FIBOKEI_DATABASE_URL or DATABASE_URL — PostgreSQL connection string")
    for var, desc in required.items():
        if not os.environ.get(var):
            missing.append(f"{var} — {desc}")

    # Warn about weak JWT secret
    jwt_secret = os.environ.get("FIBOKEI_JWT_SECRET", "")
    if jwt_secret and len(jwt_secret) < 32:
        logger.warning("FIBOKEI_JWT_SECRET is shorter than 32 characters — consider using a stronger secret")

    if missing:
        msg = "Missing required environment variables:\n  " + "\n  ".join(missing)
        logger.error(msg)
        raise SystemExit(f"Startup aborted: {msg}")


def _create_engine_and_session():
    connect_args = {}
    if DATABASE_URL.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if DATABASE_URL == "sqlite:///:memory:":
            from sqlalchemy.pool import StaticPool
            engine = create_engine(DATABASE_URL, connect_args=connect_args, poolclass=StaticPool)
        else:
            engine = create_engine(DATABASE_URL, connect_args=connect_args)
    else:
        engine = create_engine(DATABASE_URL)

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


def _init_sentry() -> None:
    """Initialize Sentry error tracking if DSN is configured."""
    dsn = os.environ.get("FIBOKEI_SENTRY_DSN", "")
    if not dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=float(os.environ.get("FIBOKEI_SENTRY_TRACES_RATE", "0.1")),
        environment=os.environ.get("FIBOKEI_SENTRY_ENV", "production"),
        release=os.environ.get("FIBOKEI_VERSION", "0.1.0"),
    )
    logging.getLogger("fibokei.startup").info("Sentry error tracking enabled")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and seed users on startup."""
    _configure_logging()
    _init_sentry()
    _validate_required_env_vars()

    engine, session_factory = _create_engine_and_session()
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Seed users
    from fibokei.api.seed import seed_users

    with session_factory() as session:
        seed_users(session)

    # Validate IG demo configuration at startup
    _validate_ig_config()

    # Log data directory resolution for diagnostics
    _log_data_paths()

    logger = logging.getLogger("fibokei.startup")
    logger.info("Fiboki Trading API started — database=%s", "postgresql" if "postgresql" in DATABASE_URL else "sqlite")

    yield


def _validate_ig_config() -> None:
    """Log IG demo integration configuration status at startup."""
    logger = logging.getLogger("fibokei.startup")
    from fibokei.core.feature_flags import FeatureFlags

    flags = FeatureFlags()
    mode = flags.execution_mode

    if mode == "paper":
        logger.info("Execution mode: paper (IG demo integration inactive)")
        return

    # IG demo or live mode — check credentials
    required = {
        "FIBOKEI_IG_API_KEY": os.environ.get("FIBOKEI_IG_API_KEY", ""),
        "FIBOKEI_IG_USERNAME": os.environ.get("FIBOKEI_IG_USERNAME", ""),
        "FIBOKEI_IG_PASSWORD": os.environ.get("FIBOKEI_IG_PASSWORD", ""),
    }
    missing = [k for k, v in required.items() if not v]

    if missing:
        logger.warning(
            "Execution mode is '%s' but IG credentials are incomplete. "
            "Missing: %s. IG operations will fail at runtime.",
            mode,
            ", ".join(missing),
        )
    else:
        account_id = os.environ.get("FIBOKEI_IG_ACCOUNT_ID", "")
        logger.info(
            "Execution mode: %s — IG demo credentials configured%s",
            mode,
            f" (account: {account_id})" if account_id else "",
        )


def _log_data_paths() -> None:
    """Log resolved data paths and available starter files at startup."""
    logger = logging.getLogger("fibokei.startup")
    from fibokei.data.paths import get_data_root, get_starter_dir, get_canonical_dir

    data_root = get_data_root()
    starter = get_starter_dir()
    canonical = get_canonical_dir()
    logger.info("Data root: %s (exists=%s)", data_root, data_root.exists())
    logger.info("Starter dir: %s (exists=%s)", starter, starter.exists())
    logger.info("Canonical dir: %s (exists=%s)", canonical, canonical.exists())

    if starter.exists():
        starter_files = list(starter.rglob("*.parquet"))
        logger.info("Starter dataset: %d parquet files", len(starter_files))
        for f in starter_files[:10]:
            logger.info("  %s", f.relative_to(starter))
    else:
        logger.warning("Starter directory does not exist — charts/backtests will fail")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="Fiboki Trading API",
        version="1.0.0",
        description="Multi-strategy trading platform API",
        lifespan=lifespan,
    )

    # Trust proxy headers (X-Forwarded-Proto etc.) so redirects use HTTPS
    # when behind Railway/Render reverse proxy
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    application.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    # CORS — localhost only in local dev, production origins from env var
    origins = []
    if os.environ.get("FIBOKEI_LOCAL_DEV"):
        origins.append("http://localhost:3000")
    extra_origins = os.environ.get("FIBOKEI_CORS_ORIGINS", "")
    if extra_origins:
        origins.extend(o.strip() for o in extra_origins.split(",") if o.strip())

    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID + logging middleware
    @application.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        logger = logging.getLogger("fibokei.http")
        logger.info(
            "%s %s %d %.0fms rid=%s",
            request.method, request.url.path, response.status_code, duration_ms, request_id,
        )
        response.headers["X-Request-ID"] = request_id
        return response

    # Mount routers
    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(instruments_router, prefix="/api/v1")
    application.include_router(strategies_router, prefix="/api/v1")
    from fibokei.api.routes.backtests import router as backtests_router
    application.include_router(backtests_router, prefix="/api/v1")
    from fibokei.api.routes.research import router as research_router
    application.include_router(research_router, prefix="/api/v1")
    from fibokei.api.routes.paper import router as paper_router
    application.include_router(paper_router, prefix="/api/v1")
    from fibokei.api.routes.trades import router as trades_router
    application.include_router(trades_router, prefix="/api/v1")
    from fibokei.api.routes.system import router as system_router
    application.include_router(system_router, prefix="/api/v1")
    from fibokei.api.routes.market_data import router as market_data_router
    application.include_router(market_data_router, prefix="/api/v1")
    from fibokei.api.routes.charts import router as charts_router
    application.include_router(charts_router, prefix="/api/v1")
    from fibokei.api.routes.data import router as data_router
    application.include_router(data_router, prefix="/api/v1")
    from fibokei.api.routes.execution import router as execution_router
    application.include_router(execution_router, prefix="/api/v1")
    from fibokei.api.routes.drawings import router as drawings_router
    application.include_router(drawings_router, prefix="/api/v1")
    from fibokei.api.routes.jobs import router as jobs_router
    application.include_router(jobs_router, prefix="/api/v1")
    from fibokei.api.routes.bookmarks import router as bookmarks_router
    application.include_router(bookmarks_router, prefix="/api/v1")

    return application


app = create_app()
