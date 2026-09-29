"""A DSL schema bump must not hand every strategy a second look at the holdout.

``HoldoutConsumption`` is keyed on ``(dataset_version_id, strategy_content_hash)``.
``StrategyDocument.content_hash()`` hashes ``semantic_payload()``, and
``semantic_payload()`` includes ``schema_version``. So bumping
``fiboki.strategy.dsl.SCHEMA_VERSION`` moves EVERY content hash in existence, and
before this test existed the registry read a moved key exactly as it reads an
unknown one: "this strategy has never looked". The one-look-per-strategy
guarantee -- the platform's single strongest defence against overfitting -- failed
silently and in the permissive direction.

The first test in this file asserts the premise (the hash really does move) and the
second asserts the fix (the second look is REFUSED, not granted). If the second one
ever inverts, the holdout discipline is decoration.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.versioned_key import UNSTAMPED
from fiboki.strategy.dsl import strategy_key_version
from fiboki.validation.holdout import (
    HoldoutAlreadyConsumed,
    HoldoutKeyVersionMismatch,
    HoldoutRegistry,
)
from tests.validation_fixtures import seed_document

START = pd.Timestamp("2019-01-01", tz="UTC")
END = pd.Timestamp("2024-01-01", tz="UTC")
DATASET = "eurusd_h1_v7"
BUMPED = "2.1.0"


@pytest.fixture
def registry():
    with HoldoutRegistry.in_memory() as reg:
        reg.define(DATASET, data_start=START, data_end=END)
        yield reg


@pytest.fixture
def document():
    return seed_document()


def _under_schema(document, schema_version: str):
    """The same strategy as it would be written under another DSL schema.

    ``model_copy`` rather than ``model_validate`` on purpose: the field validator
    refuses a foreign ``schema_version`` on load, which is correct and is also why
    this is the only way to hold both versions of one document in one process.
    """
    return document.model_copy(update={"schema_version": schema_version})


# --------------------------------------------------------------------------
# The premise
# --------------------------------------------------------------------------


def test_the_schema_version_is_inside_the_content_hash(document) -> None:
    """The bug's precondition, asserted rather than assumed.

    If this ever fails -- because ``schema_version`` was added to
    ``StrategyDocument.NON_SEMANTIC`` -- the refusal below becomes unnecessary and
    somebody should come and read this file before deleting it.
    """
    assert "schema_version" in document.semantic_payload()
    bumped = _under_schema(document, BUMPED)
    assert bumped.content_hash() != document.content_hash()


# --------------------------------------------------------------------------
# The fix
# --------------------------------------------------------------------------


def test_a_schema_bump_does_not_grant_a_second_look(registry, document, monkeypatch) -> None:
    """The whole point of this work.

    A strategy looks at the holdout under schema 2.0.0. The schema is then bumped,
    which moves its content hash. Claiming under the moved hash must be REFUSED --
    the registry cannot prove the look is unspent, so it does not grant it -- and
    no second consumption row may be written.
    """
    registry.claim(
        DATASET,
        document.content_hash(),
        strategy_id=document.strategy_id,
        actor="joe",
    )
    assert len(registry.consumptions(DATASET)) == 1

    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    assert strategy_key_version() == f"dsl:{BUMPED}"
    moved = _under_schema(document, BUMPED)
    assert moved.content_hash() != document.content_hash()

    with pytest.raises(HoldoutKeyVersionMismatch) as exc:
        registry.claim(DATASET, moved.content_hash(), strategy_id=moved.strategy_id)

    message = str(exc.value)
    assert "not comparable" in message
    assert "refuses the look" in message
    assert "mint a NEW dataset version" in message
    # The refusal is worth nothing if the row was written anyway.
    assert len(registry.consumptions(DATASET)) == 1


def test_the_refusal_is_not_a_hash_collision_artefact(registry, document, monkeypatch) -> None:
    """A genuinely NEW strategy is refused too, and that is correct.

    After a bump, no incoming hash can be compared with the stored ones, so the
    registry cannot tell a first look from a second for ANY strategy. Refusing all
    of them is the conservative answer; granting the ones that happen not to
    collide is the bug, dressed up.
    """
    registry.claim(DATASET, document.content_hash(), actor="joe")
    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    with pytest.raises(HoldoutKeyVersionMismatch):
        registry.claim(DATASET, "f" * 64)


def test_a_stamped_claim_records_the_version_it_was_keyed_under(registry, document) -> None:
    registry.claim(DATASET, document.content_hash(), actor="joe")
    consumption = registry.consumption(DATASET, document.content_hash())
    assert consumption is not None
    assert consumption.key_version == strategy_key_version()
    assert consumption.key_version_restated is False
    assert consumption.to_dict()["strategy_content_hash_key_version"] == strategy_key_version()


def test_claims_under_one_version_still_behave_exactly_as_before(registry, document) -> None:
    """The guard must not weaken the original guarantee it protects."""
    registry.claim(DATASET, document.content_hash(), actor="joe")
    with pytest.raises(HoldoutAlreadyConsumed):
        registry.claim(DATASET, document.content_hash(), actor="joe")
    # A different strategy under the SAME version is unaffected.
    other = registry.claim(DATASET, "c" * 64, actor="joe")
    assert other.token
    assert len(registry.consumptions(DATASET)) == 2


def test_a_bump_does_not_block_a_fresh_dataset_version(registry, document, monkeypatch) -> None:
    """The documented remedy actually works.

    The error tells an operator to mint a new dataset version. That has to be more
    than advice, or the guard is a dead end that somebody will delete.
    """
    registry.claim(DATASET, document.content_hash(), actor="joe")
    monkeypatch.setattr("fiboki.strategy.dsl.SCHEMA_VERSION", BUMPED)
    registry.define("eurusd_h1_v8", data_start=START, data_end=END)
    token = registry.claim(
        "eurusd_h1_v8", _under_schema(document, BUMPED).content_hash(), actor="joe"
    )
    assert token.token
    assert registry.consumption(
        "eurusd_h1_v8", _under_schema(document, BUMPED).content_hash()
    ).key_version == f"dsl:{BUMPED}"


# --------------------------------------------------------------------------
# The migration: rows written before the column existed
# --------------------------------------------------------------------------


class TestUnstampedRows:
    """A registry file written before the key-version column existed."""

    @staticmethod
    def _unstamp(registry: HoldoutRegistry) -> None:
        """Make every stored claim look like one written before the column.

        Writing the pre-migration state directly is the only way to test the
        migration: the code can no longer produce it.
        """
        from sqlalchemy import text

        from fiboki.validation.holdout import install_append_only_triggers

        # A file written before the column existed also predates the
        # append-only triggers, so the trigger is lifted to write that state
        # and restored straight after (the same pattern as the experiment
        # ledger's key-version tests).
        with registry._engine.begin() as conn:
            conn.exec_driver_sql("DROP TRIGGER IF EXISTS holdout_consumption_no_update")
            conn.execute(
                text(
                    "UPDATE holdout_consumption "
                    "SET strategy_content_hash_key_version = ''"
                )
            )
        install_append_only_triggers(registry._engine)

    def test_an_unstamped_row_is_refused_not_assumed(self, registry, document) -> None:
        registry.claim(DATASET, document.content_hash(), actor="joe")
        self._unstamp(registry)
        assert registry.consumptions(DATASET)[0].key_version == UNSTAMPED
        with pytest.raises(HoldoutKeyVersionMismatch) as exc:
            registry.claim(DATASET, "d" * 64)
        assert "<unstamped>" in str(exc.value)

    def test_restating_the_version_makes_the_registry_usable_again(
        self, registry, document
    ) -> None:
        registry.claim(DATASET, document.content_hash(), actor="joe")
        self._unstamp(registry)

        touched = registry.restate_key_versions(
            strategy_key_version(),
            stated_by="joe",
            reason=(
                "Every V2 document hashes under dsl SCHEMA_VERSION 2.0.0: the DSL "
                "validator refuses any other value on load, and 2.0.0 is the only "
                "value the V2 history has ever held."
            ),
        )
        assert touched == 1

        restated = registry.consumptions(DATASET)[0]
        assert restated.key_version == strategy_key_version()
        assert restated.key_version_restated is True
        # And the original guarantee is back in force, not merely unblocked.
        token = registry.claim(DATASET, "d" * 64)
        assert token.token
        with pytest.raises(HoldoutAlreadyConsumed):
            registry.claim(DATASET, document.content_hash())

    def test_restating_is_idempotent(self, registry, document) -> None:
        registry.claim(DATASET, document.content_hash(), actor="joe")
        self._unstamp(registry)
        args = {"stated_by": "joe", "reason": "V2 only ever hashed under 2.0.0"}
        assert registry.restate_key_versions(strategy_key_version(), **args) == 1
        assert registry.restate_key_versions(strategy_key_version(), **args) == 0

    def test_a_restatement_leaves_the_claim_row_alone(self, registry, document) -> None:
        """The claim still says what it said. Only the attestation is new."""
        registry.claim(DATASET, document.content_hash(), actor="joe", note="ladder rung 6")
        self._unstamp(registry)
        registry.restate_key_versions(
            strategy_key_version(), stated_by="joe", reason="V2 only ever hashed under 2.0.0"
        )
        row = registry.consumptions(DATASET)[0]
        assert row.strategy_content_hash == document.content_hash()
        assert row.note == "ladder rung 6"
        assert row.actor == "joe"

    def test_an_unattributed_restatement_is_refused(self, registry, document) -> None:
        """A guess here re-opens the holdout, so it has to be signed."""
        registry.claim(DATASET, document.content_hash(), actor="joe")
        self._unstamp(registry)
        with pytest.raises(ValueError, match="stated_by and reason are mandatory"):
            registry.restate_key_versions(strategy_key_version(), stated_by="", reason="")
        with pytest.raises(ValueError, match="key_version is mandatory"):
            registry.restate_key_versions("", stated_by="joe", reason="because")

    def test_a_stamped_row_is_never_overwritten_by_a_restatement(
        self, registry, document
    ) -> None:
        registry.claim(DATASET, document.content_hash(), actor="joe")
        assert (
            registry.restate_key_versions(
                "dsl:9.9.9", stated_by="joe", reason="attempted revision"
            )
            == 0
        )
        assert registry.consumptions(DATASET)[0].key_version == strategy_key_version()


# --------------------------------------------------------------------------
# Migrating a file on disk
# --------------------------------------------------------------------------


def test_a_registry_file_without_the_column_acquires_it_on_open(tmp_path) -> None:
    """The ALTER TABLE path, against a database built without the column."""
    from sqlalchemy import create_engine, text

    path = tmp_path / "holdout.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE holdout_consumption ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " token VARCHAR(64),"
                " dataset_version_id VARCHAR(128),"
                " strategy_content_hash VARCHAR(64),"
                " strategy_id VARCHAR(128),"
                " experiment_id VARCHAR(64),"
                " actor VARCHAR(128),"
                " code_version VARCHAR(64),"
                " claimed_at DATETIME,"
                " outcome_json JSON,"
                " outcome_recorded_at DATETIME,"
                " note TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO holdout_consumption"
                " (token, dataset_version_id, strategy_content_hash, claimed_at)"
                " VALUES ('hold_legacy', :d, :h, '2025-01-01 00:00:00')"
            ),
            {"d": DATASET, "h": "a" * 64},
        )
    engine.dispose()

    with HoldoutRegistry(path) as registry:
        assert registry.columns_added == ("strategy_content_hash_key_version",)
        registry.define(DATASET, data_start=START, data_end=END)
        legacy = registry.consumptions(DATASET)
        assert len(legacy) == 1
        assert legacy[0].key_version == UNSTAMPED
        # Unstamped, so refused rather than assumed.
        with pytest.raises(HoldoutKeyVersionMismatch):
            registry.claim(DATASET, "e" * 64)
        assert (
            registry.restate_key_versions(
                strategy_key_version(),
                stated_by="joe",
                reason="V2 only ever hashed under dsl 2.0.0",
            )
            == 1
        )
        assert registry.claim(DATASET, "e" * 64).token
