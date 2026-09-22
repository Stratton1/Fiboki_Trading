"""Shared test fixtures for Fiboki."""

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"

# ─── CRITICAL — set BEFORE pytest collects any test module ────────────────
#
# fibokei.api.app freezes DATABASE_URL at module-import time:
#
#   DATABASE_URL = os.environ.get("FIBOKEI_DATABASE_URL") or "sqlite:///fibokei.db"
#
# So if ANY test module performs a top-level `from fibokei.api.app import …`
# (e.g. tests/test_worker_external_flag.py imports _start_worker_thread),
# pytest collection imports app.py before the per-test fixture below has had
# a chance to set the env var, and DATABASE_URL freezes to the on-disk
# `sqlite:///fibokei.db` default. From that point on, every test writes to
# the same shared on-disk file regardless of what the fixture sets, and the
# whole suite is no longer isolated.
#
# Setting the env via os.environ.setdefault at conftest module level
# guarantees the variable is in os.environ before pytest performs collection,
# so app.py's module-level read picks up the in-memory URL and every
# subsequent create_app() call produces an isolated engine.
#
# `setdefault` preserves any explicit value the operator passes in (e.g.
# `FIBOKEI_DATABASE_URL=sqlite:///tmp.db python -m pytest`).
os.environ.setdefault("FIBOKEI_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("FIBOKEI_JWT_SECRET", "test-secret-for-testing")
os.environ.setdefault("FIBOKEI_USER_JOE_PASSWORD", "testpass123")
os.environ.setdefault("FIBOKEI_USER_TOM_PASSWORD", "testpass456")
os.environ.setdefault("FIBOKEI_LOCAL_DEV", "1")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def sample_eurusd_h1_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "sample_eurusd_h1.csv"


@pytest.fixture
def api_client():
    """Create a test client for the FastAPI app with in-memory DB."""
    os.environ["FIBOKEI_JWT_SECRET"] = "test-secret-for-testing"
    os.environ["FIBOKEI_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["FIBOKEI_USER_JOE_PASSWORD"] = "testpass123"
    os.environ["FIBOKEI_USER_TOM_PASSWORD"] = "testpass456"
    os.environ["FIBOKEI_LOCAL_DEV"] = "1"

    from starlette.testclient import TestClient

    from fibokei.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_token(api_client):
    """Get a valid JWT token for testing."""
    response = api_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(auth_token):
    """Get authorization headers for testing."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def auth_client(api_client):
    """Return an API client with cookie auth already set via login."""
    api_client.post(
        "/api/v1/auth/login",
        data={"username": "joe", "password": "testpass123"},
    )
    return api_client
