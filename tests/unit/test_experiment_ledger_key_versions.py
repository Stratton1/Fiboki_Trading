"""A moved key must not read as "never tried" in the experiment ledger.

``strategy_content_hash`` feeds research memory's exact-match branch,
``LineageService.produced_by`` and ``strategy_ancestry``, and the campaign
checkpoint's cell key. All of them are derived from the DSL schema, so a schema
bump moves every one of them -- and before the version columns existed, each
lookup answered a moved key with an empty result set that read as new work:
novelty said novel, ancestry said ``known_to_ledger: False`` (i.e. "document
deleted" rather than "key moved"), and a resumed campaign started over.
"""
from __future__ import annotations

import pytest

from fiboki.core.versioned_key import UNSTAMPED
from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    LedgerError,
    LedgerKeyVersionMismatch,
    is_append_only_violation,
)
from fiboki.research.lineage import LineageService
from fiboki.research.memory import ResearchMemory
from fiboki.strategy.dsl import strategy_key_version
from fiboki.validation.report import report_key_version
from tests.validation_fixtures import seed_document

BUMPED = "2.1.0"


@pytest.fixture
def ledger():
    with ExperimentLedger.in_memory() as led:
        yield led


@pytest.fixture
def document():
    return seed_document()


def _draft(document=None, **kwargs) -> ExperimentDraft:
    return ExperimentDraft(
        actor_kind=ActorKind.HUMAN,
        actor_name="joe",
        reason="key-version regression coverage",
        strategy_document=document,
        **kwargs,
    )


def _unstamp(ledger: ExperimentLedger, *columns: str) -> None:
    """Fabricate the pre-migration state that the code can no longer produce.

    The append-only triggers have to be dropped and put back to do it, which is
    the point made from the other side: an in-place UPDATE is not available to the
    migration either, which is why the migration is a restatement row instead.
    ``test_the_append_only_triggers_still_forbid_an_in_place_update`` asserts that
    directly.
    """
    from fiboki.research.experiment import _TRIGGERS

    sets = ", ".join(f"{c}_key_version = ''" for c in columns)
    with ledger._engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER IF EXISTS experiment_no_update")
        conn.exec_driver_sql(f"UPDATE experiment SET {sets}")
    with ledger._engine.begin() as conn:
        for ddl in _TRIGGERS:
            conn.exec_driver_sql(ddl)


# --------------------------------------------------------------------------
# Stamping
# --------------------------------------------------------------------------


def test_a_written_experiment_stamps_every_key_it_has(ledger, document) -> None:
    exp = ledger.create(_draft(document))
    assert exp.strategy_content_hash == document.content_hash()
    assert exp.key_version("strategy_content_hash") == strategy_key_version()
    assert exp.key_version("structure_hash") == strategy_key_version()
    assert exp.to_dict()["key_versions"]["structure_hash"] == strategy_key_version()


def test_an_absent_key_is_not_given_a_version(ledger) -> None:
    """A version beside an empty hash asserts something about nothing."""
    exp = ledger.create(_draft())
    assert exp.strategy_content_hash == ""
    assert exp.key_version("strategy_content_hash") == UNSTAMPED
    assert exp.key_is_comparable("strategy_content_hash", strategy_key_version())


def test_a_validation_report_hash_is_stamped_with_the_report_version(
    ledger, document
) -> None:
    from fiboki.validation.report import ValidationReport

    report = ValidationReport(
        strategy_id=document.strategy_id,
        strategy_content_hash=document.content_hash(),
        dataset_version_id="eurusd_h1_v7",
        code_version="abc1234",
    )
    exp = ledger.create(_draft(document, validation_report=report))
    assert exp.validation_report_hash == report.content_hash()
    assert exp.key_version("validation_report_hash") == report_key_version()


# --------------------------------------------------------------------------
# A keyed lookup refuses rather than answering "never tried"
# --------------------------------------------------------------------------


def test_a_keyed_lookup_refuses_when_the_stored_keys_moved(ledger, document, monkeypatch) -> None:
    ledger.create(_draft(document))
    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    moved = document.model_copy(update={"schema_version": BUMPED})
    assert moved.content_hash() != document.content_hash()

    with pytest.raises(LedgerKeyVersionMismatch) as exc:
        ledger.list(strategy_content_hash=moved.content_hash())
    message = str(exc.value)
    assert "not comparable" in message
    assert "never tried" in message


def test_the_unfiltered_history_is_never_refused(ledger, document, monkeypatch) -> None:
    """Reading the history is always legitimate -- the audit and the migration need it."""
    ledger.create(_draft(document))
    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    assert len(ledger.list()) == 1
    assert ledger.key_version_census("strategy_content_hash") == {"dsl:2.0.0": 1}


def test_a_caller_can_force_a_possibly_incomplete_answer(ledger, document, monkeypatch) -> None:
    ledger.create(_draft(document))
    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    moved = document.model_copy(update={"schema_version": BUMPED})
    assert ledger.list(strategy_content_hash=moved.content_hash(), allow_stale_keys=True) == []


def test_lineage_raises_rather_than_reporting_the_document_unknown(
    ledger, document, monkeypatch
) -> None:
    """``known_to_ledger: False`` after a bump was a lie. It is now a refusal."""
    ledger.create(_draft(document))
    lineage = LineageService(ledger)
    assert lineage.produced_by(document.content_hash()) is not None

    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    moved = document.model_copy(update={"schema_version": BUMPED})
    with pytest.raises(LedgerKeyVersionMismatch):
        lineage.produced_by(moved.content_hash())
    with pytest.raises(LedgerKeyVersionMismatch):
        lineage.strategy_ancestry(moved.content_hash())


def test_research_memory_does_not_call_an_uncomparable_ledger_novel(
    ledger, document, monkeypatch
) -> None:
    """The dangerous direction. "Novel" licenses spending a holdout look."""
    ledger.create(_draft(document))
    memory = ResearchMemory(ledger)
    assert memory.recall(document).is_novel is False  # exact duplicate, comparable

    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    moved = document.model_copy(update={"schema_version": BUMPED})
    recall = memory.recall(moved)
    assert len(recall.incomparable) == 1
    assert recall.is_novel is False
    assert "CANNOT SAY" in recall.recommendation()
    assert recall.to_dict()["n_incomparable_key_versions"] == 1
    assert memory.has_been_tried(moved) is True


def test_an_unrelated_query_is_unaffected_when_every_key_is_comparable(
    ledger, document
) -> None:
    """The guard must not make a healthy ledger refuse ordinary work."""
    ledger.create(_draft(document))
    memory = ResearchMemory(ledger)
    recall = memory.recall(text="something entirely different")
    assert recall.incomparable == ()
    assert recall.is_novel is True


# --------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------


class TestRestatement:
    def test_an_unstamped_key_is_refused_not_assumed(self, ledger, document) -> None:
        ledger.create(_draft(document))
        _unstamp(ledger, "strategy_content_hash")
        assert ledger.key_version_census("strategy_content_hash") == {UNSTAMPED: 1}
        with pytest.raises(LedgerKeyVersionMismatch) as exc:
            ledger.list(strategy_content_hash=document.content_hash())
        assert "<unstamped>" in str(exc.value)

    def test_restating_makes_the_lookup_work_again(self, ledger, document) -> None:
        exp = ledger.create(_draft(document))
        _unstamp(ledger, "strategy_content_hash", "structure_hash")

        written = ledger.restate_key_versions(
            strategy_key_version(),
            stated_by="joe",
            reason=(
                "Every V2 document hashes under dsl SCHEMA_VERSION 2.0.0: the DSL "
                "validator refuses any other value on load, and the V2 history has "
                "only ever held 2.0.0."
            ),
        )
        assert written == 2  # one per (experiment, key column) pair that had a key

        found = ledger.list(strategy_content_hash=document.content_hash())
        assert [e.id for e in found] == [exp.id]
        assert found[0].key_version("strategy_content_hash") == strategy_key_version()

    def test_restating_does_not_touch_the_experiment_row(self, ledger, document) -> None:
        """A duplicated experiment row would inflate the trial count. This does not."""
        before = ledger.create(_draft(document, conclusion="promising"))
        _unstamp(ledger, "strategy_content_hash", "structure_hash")
        ledger.restate_key_versions(
            strategy_key_version(), stated_by="joe", reason="V2 only ever hashed under 2.0.0"
        )
        assert ledger.count() == 1
        after = ledger.get(before.id)
        assert after.strategy_content_hash == before.strategy_content_hash
        assert after.conclusion == "promising"
        assert after.created_at == before.created_at

    def test_the_append_only_triggers_still_forbid_an_in_place_update(
        self, ledger, document
    ) -> None:
        """Why a restatement exists at all, asserted rather than asserted-in-a-comment."""
        from sqlalchemy import text

        ledger.create(_draft(document))
        with (
            pytest.raises(Exception) as exc,
            ledger._engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE experiment SET strategy_content_hash_key_version = 'x'")
            )
        assert is_append_only_violation(exc.value)

    def test_restating_is_idempotent(self, ledger, document) -> None:
        ledger.create(_draft(document))
        _unstamp(ledger, "strategy_content_hash", "structure_hash")
        args = {"stated_by": "joe", "reason": "V2 only ever hashed under 2.0.0"}
        assert ledger.restate_key_versions(strategy_key_version(), **args) == 2
        assert ledger.restate_key_versions(strategy_key_version(), **args) == 0

    def test_a_stamped_key_outranks_a_later_restatement(self, ledger, document) -> None:
        ledger.create(_draft(document))
        assert (
            ledger.restate_key_versions(
                "dsl:9.9.9", stated_by="joe", reason="attempted revision"
            )
            == 0
        )
        census = ledger.key_version_census("strategy_content_hash")
        assert census == {strategy_key_version(): 1}

    def test_an_unattributed_restatement_is_refused(self, ledger, document) -> None:
        ledger.create(_draft(document))
        _unstamp(ledger, "strategy_content_hash")
        with pytest.raises(ValueError, match="stated_by and reason are mandatory"):
            ledger.restate_key_versions(strategy_key_version(), stated_by="", reason="x")
        with pytest.raises(ValueError, match="key_version is mandatory"):
            ledger.restate_key_versions("  ", stated_by="joe", reason="x")

    def test_an_unknown_key_column_is_refused(self, ledger) -> None:
        with pytest.raises(LedgerError, match="not a versioned key column"):
            ledger.key_version_census("outcome")


def test_a_ledger_file_without_the_columns_acquires_them_on_open(tmp_path) -> None:
    """The ALTER TABLE path, against a database built without the version columns."""
    from sqlalchemy import create_engine, text

    path = tmp_path / "research.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE experiment ("
                " id VARCHAR(64) PRIMARY KEY,"
                " created_at DATETIME,"
                " parent_experiment_id VARCHAR(64) DEFAULT '',"
                " actor_kind VARCHAR(16),"
                " actor_name VARCHAR(128),"
                " reason TEXT DEFAULT '',"
                " hypothesis_id VARCHAR(128) DEFAULT '',"
                " strategy_id VARCHAR(128) DEFAULT '',"
                " strategy_content_hash VARCHAR(64) DEFAULT '',"
                " strategy_version VARCHAR(32) DEFAULT '',"
                " structure_hash VARCHAR(64) DEFAULT '',"
                " structure_tokens_json JSON,"
                " strategy_document_json JSON,"
                " code_version VARCHAR(64) DEFAULT '',"
                " dataset_version_id VARCHAR(128) DEFAULT '',"
                " parameters_json JSON,"
                " engine_config_json JSON,"
                " outputs_json JSON,"
                " validation_report_json JSON,"
                " validation_report_hash VARCHAR(64) DEFAULT '',"
                " outcome VARCHAR(24) DEFAULT 'pending',"
                " conclusion TEXT DEFAULT '',"
                " rejection_reason TEXT DEFAULT '',"
                " rejected_at_rung VARCHAR(64) DEFAULT '',"
                " tags_json JSON)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO experiment (id, created_at, actor_kind, actor_name, reason,"
                " strategy_content_hash, structure_hash, structure_tokens_json,"
                " parameters_json, engine_config_json, outputs_json, tags_json)"
                " VALUES ('exp_legacy', '2025-01-01 00:00:00', 'human', 'joe', 'legacy',"
                " :h, :s, '[]', '{}', '{}', '{}', '[]')"
            ),
            {"h": "a" * 64, "s": "b" * 64},
        )
    engine.dispose()

    with ExperimentLedger(path) as ledger:
        assert set(ledger.columns_added) == {
            "strategy_content_hash_key_version",
            "structure_hash_key_version",
            "validation_report_hash_key_version",
        }
        assert ledger.key_version_census("strategy_content_hash") == {UNSTAMPED: 1}
        with pytest.raises(LedgerKeyVersionMismatch):
            ledger.list(strategy_content_hash="a" * 64)
        assert (
            ledger.restate_key_versions(
                strategy_key_version(),
                stated_by="joe",
                reason="V2 only ever hashed under dsl 2.0.0",
            )
            == 2
        )
        assert len(ledger.list(strategy_content_hash="a" * 64)) == 1
