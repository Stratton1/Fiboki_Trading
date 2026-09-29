"""The ledger stamps the schema revision it creates; health compares it.

The API's ``migration_revision`` check read ``alembic_version``, which V2 never
writes (there is no alembic environment; the ledger evolves by ``create_all``
and ``add_missing_columns``), so it was degraded on every deployment forever.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fiboki.research.experiment import (
    SCHEMA_REVISION_TABLE,
    ExperimentLedger,
    read_schema_revision,
    schema_revision,
)


def test_the_revision_is_a_deterministic_content_hash() -> None:
    first, second = schema_revision(), schema_revision()
    assert first == second
    assert first.startswith("ledger_") and len(first) == len("ledger_") + 12
    int(first[len("ledger_"):], 16)


def test_opening_a_ledger_stamps_the_revision_once(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    ledger = ExperimentLedger(path)
    assert ledger.schema_revision == schema_revision()
    ledger.close()
    assert read_schema_revision(path) == schema_revision()
    ExperimentLedger(path).close()  # same code, reopened: no second row
    with sqlite3.connect(path) as conn:
        rows = conn.execute(f"SELECT revision, applied_at FROM {SCHEMA_REVISION_TABLE}").fetchall()
    assert len(rows) == 1 and rows[0][1].endswith("+00:00")


def test_a_newer_code_revision_appends_and_is_the_one_read(tmp_path: Path) -> None:
    """History is kept (append-only); the newest stamp wins."""
    path = tmp_path / "ledger.sqlite"
    ExperimentLedger(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"INSERT INTO {SCHEMA_REVISION_TABLE} VALUES ('ledger_000000000000', '2020-01-01T00:00:00+00:00')"
        )
    assert read_schema_revision(path) == schema_revision(), "older applied_at never wins"


def test_a_v1_alembic_file_still_reports_and_a_missing_file_is_unknown(tmp_path: Path) -> None:
    v1 = tmp_path / "v1.sqlite"
    with sqlite3.connect(v1) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('abc123')")
    assert read_schema_revision(v1) == "abc123"
    assert read_schema_revision(tmp_path / "nope.sqlite") is None
    empty = tmp_path / "empty.sqlite"
    with sqlite3.connect(empty) as conn:
        conn.execute("CREATE TABLE t (x INT)")
    assert read_schema_revision(empty) is None
