"""Write the workspace e2e fixtures from the REAL backend.

Run from the repository root:

    PYTHONPATH=src .venv/bin/python apps/web/tests/e2e/workspace-fixtures/generate.py

It builds the API in-process against a throwaway state directory, seeds it
(recorded quotes, headlines, alert rules and a notification), signs in and
writes each route's answer as JSON beside this file. A spec therefore drives
the screens with payloads the API can actually send; nothing here is
hand-shaped. Times are pinned (``NOW``) so the files are reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "src"))
OUT = Path(__file__).resolve().parent

from fastapi.testclient import TestClient  # noqa: E402

ORIGIN = "https://workstation.fiboki.test"
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write(name: str, payload: object) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", name)


def seed(state: Path) -> None:
    """Hooks for the later screens (quotes, news, alerts) append here."""
    try:
        from seed_monitors import seed_monitors  # type: ignore[import-not-found]
    except ImportError:
        return
    seed_monitors(state, NOW)


def main() -> None:
    sys.path.insert(0, str(OUT))
    from fiboki.api.app import create_app
    from fiboki.api.settings import load_settings

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        os.environ["FIBOKI_OPERATORS"] = f"joe:admin:{_sha('pw')}"
        settings = load_settings({
            "FIBOKI_STATE_DIR": str(state),
            "FIBOKI_ALLOWED_ORIGINS": ORIGIN,
            "FIBOKI_COOKIE_SECURE": "false",
            "FIBOKI_SESSION_SECRET": "fixture-secret",
        })
        seed(state)
        app = create_app(settings, configure_logs=False)
        with TestClient(app, base_url=ORIGIN) as client:
            client.post("/api/auth/login", json={"username": "joe", "password": "pw"}, headers={"Origin": ORIGIN})
            write("panels", client.get("/api/workspace/panels").json())
            write("presets", client.get("/api/workspace/presets").json())
            try:
                from routes_monitors import dump_monitors  # type: ignore[import-not-found]
            except ImportError:
                return
            dump_monitors(client, write, NOW)


if __name__ == "__main__":
    main()
