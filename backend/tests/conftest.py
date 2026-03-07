"""Shared test fixtures for FIBOKEI."""

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


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
