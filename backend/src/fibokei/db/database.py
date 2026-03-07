"""Database engine and session management."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from fibokei.db.models import Base

DEFAULT_URL = "sqlite:///fibokei.db"


def get_engine(url: str = DEFAULT_URL, **kwargs) -> Engine:
    """Create a SQLAlchemy engine."""
    return create_engine(url, **kwargs)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to the engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables defined in the ORM models."""
    Base.metadata.create_all(engine)
