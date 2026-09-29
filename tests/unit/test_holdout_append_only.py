"""The holdout registry refuses UPDATE and DELETE at the database (audit F P1-7 item 3, P2-14).

Before: ``DELETE FROM holdout_consumption`` reset the registry and handed every
strategy a fresh look, and outcomes were written by UPDATE-ing the claim row.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from fiboki.validation.holdout import APPEND_ONLY_MESSAGE, HoldoutError, HoldoutRegistry

DATASET = "ds_eurusd_h1_v1"


@pytest.fixture
def registry(tmp_path):
    reg = HoldoutRegistry(tmp_path / "holdout.sqlite")
    reg.define(DATASET, data_start="2015-01-01", data_end="2025-01-01")
    yield reg
    reg.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM holdout_consumption",
        "UPDATE holdout_consumption SET strategy_content_hash = 'x'",
        "DELETE FROM holdout_outcome",
        "UPDATE holdout_outcome SET outcome_json = '{}'",
    ],
)
def test_a_spent_look_cannot_be_edited_or_removed(registry, sql) -> None:
    token = registry.claim(DATASET, "a" * 64, actor="joe")
    registry.record_outcome(token, {"sharpe": 0.4})
    with pytest.raises(DatabaseError, match="append-only"), registry._engine.begin() as conn:
        conn.execute(text(sql))
    assert len(registry.consumptions(DATASET)) == 1
    assert APPEND_ONLY_MESSAGE.startswith("holdout registry is append-only")


def test_the_outcome_is_appended_not_written_into_the_claim(registry) -> None:
    token = registry.claim(DATASET, "b" * 64, actor="joe")
    registry.record_outcome(token, {"sharpe": 0.4})
    [consumption] = registry.consumptions(DATASET)
    assert consumption.outcome == {"sharpe": 0.4}
    assert consumption.outcome_recorded_at is not None
    with registry._engine.connect() as conn:
        inline = conn.execute(text("SELECT outcome_json FROM holdout_consumption")).scalar()
        rows = conn.execute(text("SELECT COUNT(*) FROM holdout_outcome")).scalar()
    assert inline is None and rows == 1


def test_a_second_outcome_is_refused(registry) -> None:
    token = registry.claim(DATASET, "c" * 64, actor="joe")
    registry.record_outcome(token, {"sharpe": 0.4})
    with pytest.raises(HoldoutError, match="already carries an outcome"):
        registry.record_outcome(token, {"sharpe": 2.0})
    assert registry.consumptions(DATASET)[0].outcome == {"sharpe": 0.4}


def test_a_legacy_inline_outcome_is_still_read_and_still_final(tmp_path) -> None:
    path = tmp_path / "holdout.sqlite"
    reg = HoldoutRegistry(path)
    reg.define(DATASET, data_start="2015-01-01", data_end="2025-01-01")
    token = reg.claim(DATASET, "d" * 64, actor="joe")
    with reg._engine.begin() as conn:  # a file written before holdout_outcome existed
        conn.exec_driver_sql("DROP TRIGGER holdout_consumption_no_update")
        conn.exec_driver_sql("UPDATE holdout_consumption SET outcome_json = '{\"sharpe\": 0.1}'")
    reg.close()
    reopened = HoldoutRegistry(path)  # re-installs the triggers
    assert reopened.consumptions(DATASET)[0].outcome == {"sharpe": 0.1}
    with pytest.raises(HoldoutError, match="already carries an outcome"):
        reopened.record_outcome(token, {"sharpe": 9.9})
    with pytest.raises(DatabaseError), reopened._engine.begin() as conn:
        conn.execute(text("DELETE FROM holdout_consumption"))
    reopened.close()


def test_every_connection_runs_in_wal_with_a_busy_timeout(registry) -> None:
    with registry._engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 2


def test_the_ledgers_use_the_listener_too(tmp_path) -> None:
    from fiboki.data.versioning import DatasetCatalogue
    from fiboki.research.experiment import ExperimentLedger

    for store in (
        ExperimentLedger(tmp_path / "e.sqlite"),
        DatasetCatalogue(tmp_path / "d.sqlite"),
    ):
        with store._engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
        store.close()
