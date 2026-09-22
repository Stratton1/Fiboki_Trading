"""Centralized data directory resolution.

Works in both local dev and Docker by walking up the directory tree
to find the data/ directory, with an env var override for explicit control.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def get_data_root() -> Path:
    """Return the root data directory.

    Resolution order:
    1. FIBOKEI_DATA_DIR env var (explicit override)
    2. Walk up from this file to find a directory containing data/
    3. Fallback: /app/data (Docker default)
    """
    env_dir = os.environ.get("FIBOKEI_DATA_DIR")
    if env_dir:
        return Path(env_dir)

    # Walk up from this file's location looking for a data/ directory
    # that contains at least one of the expected subdirectories.
    # In local dev: paths.py is at backend/src/fibokei/data/paths.py
    #   parents[3] = /app (in Docker) or .../Fiboki_Trading/backend (local)
    #   parents[4] = / (in Docker) or .../Fiboki_Trading (local)
    # So we start from parents[2] and walk up to cover both cases.
    start = Path(__file__).resolve().parent  # fibokei/data/
    current = start
    for _ in range(10):
        candidate = current / "data"
        if candidate.is_dir() and any(
            (candidate / sub).is_dir()
            for sub in ("canonical", "fixtures", "starter")
        ):
            return candidate
        if current.parent == current:
            break
        current = current.parent

    return Path("/app/data")


def get_canonical_dir() -> Path:
    """Return the canonical data directory (data/canonical/)."""
    return get_data_root() / "canonical"


def get_fixtures_dir() -> Path:
    """Return the legacy fixtures directory (data/fixtures/)."""
    return get_data_root() / "fixtures"


def get_starter_dir() -> Path:
    """Return the starter dataset directory (data/starter/).

    When FIBOKEI_DATA_DIR points to a Railway volume, the starter data
    won't exist there — it's bundled in the Docker image at /app/data/starter/.
    Fall back to the Docker-bundled location if the primary path doesn't exist.
    """
    primary = get_data_root() / "starter"
    if primary.is_dir():
        return primary
    # Docker-bundled fallback (image always has /app/data/starter/)
    docker_bundled = Path("/app/data/starter")
    if docker_bundled.is_dir():
        return docker_bundled
    return primary
