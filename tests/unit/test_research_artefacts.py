"""Agent artefacts live in the PLATFORM ledger, not in a second research store.

``fiboki/agents/research_store.py`` was a parallel implementation of
``fiboki/research/experiment.py`` plus ``fiboki/research/memory.py``: its own
persistence, its own append-only guarantee and its own institutional memory. Two
stores meant two histories, and the one the agents wrote to was invisible to the
mechanism built to stop them rediscovering dead ends.

These tests pin the migration: one database, one append-only guarantee enforced
by the database itself, and agent work visible to research memory.
"""
from __future__ import annotations

import importlib

import pytest
from sqlalchemy import text

from fiboki.research import ResearchStore
from fiboki.research.artefacts import Hypothesis, ResearchNote
from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    is_append_only_violation,
)


def test_the_duplicate_store_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("fiboki.agents.research_store")


def test_the_agent_package_re_exports_the_platform_store() -> None:
    from fiboki.agents import ResearchStore as FromAgents

    assert FromAgents is ResearchStore


def test_artefacts_share_the_experiment_ledger_s_database() -> None:
    ledger = ExperimentLedger.in_memory()
    store = ResearchStore(ledger=ledger)
    store.add_note(ResearchNote(title="A filed note", body="b" * 25))
    ledger.create(
        ExperimentDraft(
            actor_kind=ActorKind.AGENT,
            actor_name="agent:test",
            reason="the artefact and the experiment must land in one place",
        )
    )
    with ledger.engine.connect() as conn:
        tables = {
            row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert {"experiment", "research_artefact"} <= tables


def test_append_only_is_enforced_by_the_DATABASE_not_by_the_api() -> None:
    """The old store had no ``update`` method. That is a convention.

    A convention holds until someone opens the file. A trigger is a property of
    the artefact, and it is the reason a research record is worth keeping: the
    results nobody liked cannot be quietly tidied away.
    """
    store = ResearchStore()
    note = store.add_note(ResearchNote(title="Immutable note", body="b" * 25))
    with store.ledger.engine.begin() as conn, pytest.raises(Exception) as update_exc:
        conn.execute(
            text("UPDATE research_artefact SET payload_json = '{}' WHERE id = :i"),
            {"i": note.note_id},
        )
    assert is_append_only_violation(update_exc.value)

    with store.ledger.engine.begin() as conn, pytest.raises(Exception) as delete_exc:
        conn.execute(
            text("DELETE FROM research_artefact WHERE id = :i"), {"i": note.note_id}
        )
    assert is_append_only_violation(delete_exc.value)


def test_the_store_still_refuses_a_duplicate_through_its_own_api() -> None:
    store = ResearchStore()
    note = store.add_note(ResearchNote(title="Only once", body="b" * 25))
    with pytest.raises(ValueError, match="append-only"):
        store.add_note(note)


def test_the_store_exposes_the_research_memory_it_is_built_on() -> None:
    """The point of the migration: agent work is visible to "have we tried this?"."""
    store = ResearchStore()
    store.add_hypothesis(
        Hypothesis(
            title="Ichimoku adds nothing on FX",
            statement="s" * 45,
            rationale="r" * 45,
            testable_prediction="p" * 25,
            falsifier="f" * 25,
        )
    )
    store.record_experiment(
        ExperimentDraft(
            actor_kind=ActorKind.AGENT,
            actor_name="agent:strategy-engineer",
            reason="adding an RSI confirmation to the ichimoku kumo trend strategy",
        )
    )
    recall = store.memory.recall(text="adding an RSI confirmation to an ichimoku strategy")
    assert recall.matches, recall.describe()
    assert "ichimoku" in recall.matches[0].why or recall.matches[0].similarity > 0.0


def test_the_store_persists_across_instances_on_one_directory(tmp_path) -> None:
    directory = tmp_path / "research"
    first = ResearchStore(directory)
    note = first.add_note(ResearchNote(title="Persisted note", body="b" * 25))
    first.close()

    second = ResearchStore(directory)
    assert second.counts()["notes"] == 1
    assert second.get("notes", note.note_id).title == "Persisted note"


def test_there_is_still_no_update_or_delete_on_the_api() -> None:
    public = {n for n in dir(ResearchStore) if not n.startswith("_")}
    assert not (public & {"update", "delete", "remove", "clear", "overwrite"})
