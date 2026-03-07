"""Shared dependencies for API routes."""

from fastapi import Request


def get_db(request: Request):
    """Get a database session from the app state."""
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
