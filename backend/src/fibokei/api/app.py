"""FastAPI application for Fiboki Trading."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and seed users on startup."""
    engine, session_factory = _create_engine_and_session()
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Seed users
    from fibokei.api.seed import seed_users

    with session_factory() as session:
        seed_users(session)

    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="Fiboki Trading API",
        version="1.0.0",
        description="Multi-strategy trading platform API",
        lifespan=lifespan,
    )

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

    return application


app = create_app()
