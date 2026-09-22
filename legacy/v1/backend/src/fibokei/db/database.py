"""Database engine and session management."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from fibokei.db.models import Base

DEFAULT_URL = "sqlite:///fibokei.db"


def resolve_app_db_url() -> str:
    """Return the same database URL the API uses.

    Mirrors api/app.py resolution so research scripts write the lifecycle ledger
    into the *app* database (where the /research/candidates endpoint reads it),
    rather than a standalone file. Honours FIBOKEI_DATABASE_URL / DATABASE_URL
    (normalising the postgres:// → postgresql:// scheme), else local sqlite.
    """
    import os
    raw = os.environ.get("FIBOKEI_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw:
        return DEFAULT_URL
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql://", 1)
    return raw


def get_engine(url: str = DEFAULT_URL, **kwargs) -> Engine:
    """Create a SQLAlchemy engine.

    For SQLite, set a busy timeout so concurrent writers (the parallel research
    loops + the decay monitor all appending to the same ledger) wait for the lock
    instead of erroring with 'database is locked'.
    """
    if url.startswith("sqlite") and "connect_args" not in kwargs:
        kwargs["connect_args"] = {"timeout": 30}
    return create_engine(url, **kwargs)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to the engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables defined in the ORM models, then seed required rows.

    Phase 2 seed: ensures a single default Paper execution account exists.
    Idempotent — re-running ``init_db`` against an already-seeded database
    is a no-op.
    """
    Base.metadata.create_all(engine)
    _seed_default_paper_account(engine)


def _seed_default_paper_account(engine: Engine) -> None:
    """Idempotently seed the default Paper execution account.

    Mirrors the Alembic c3d4e5f6a7b8 migration's seed step so test
    environments using ``Base.metadata.create_all`` get the same starting
    state. Safe to call repeatedly.
    """
    from datetime import datetime, timezone

    from fibokei.db.models import ExecutionAccountModel

    factory = get_session_factory(engine)
    with factory() as session:
        existing = session.query(ExecutionAccountModel).filter_by(name="Paper").first()
        if existing is not None:
            return
        now = datetime.now(timezone.utc)
        paper = ExecutionAccountModel(
            name="Paper",
            broker="paper",
            environment="paper",
            base_currency="GBP",
            starting_balance=1000.0,
            allocated_capital=1000.0,
            risk_per_trade_pct=1.0,
            max_daily_loss_pct=4.0,
            max_weekly_loss_pct=8.0,
            max_open_positions=20,
            is_enabled=True,
            is_default=True,
            live_allowed=False,
            created_at=now,
            updated_at=now,
        )
        session.add(paper)
        session.commit()
