"""The structured trade journal store (ops/journal_notes.py)."""
from __future__ import annotations

import pytest

from fiboki.ops.journal_notes import JournalNoteStore, NoteConflict, NoteInvalid, validate


def _store(tmp_path) -> JournalNoteStore:
    return JournalNoteStore(tmp_path / "journal_notes.sqlite")


def test_a_note_is_versioned_and_its_history_kept(tmp_path):
    store = _store(tmp_path)
    first = store.put(validate("trd_1", note="clean breakout", tags=["Breakout", "london open"], grade="a"),
                      actor="joe", expected_version=0)
    assert first.version == 1 and first.tags == ("breakout", "london-open") and first.grade == "A"
    second = store.put(validate("trd_1", note="exited early", mistakes=["early-exit"], grade="B",
                                intervention_reason="closed by hand before NFP"), actor="tom", expected_version=1)
    assert second.version == 2 and store.get("trd_1").updated_by == "tom"
    hist = store.history("trd_1")
    assert [h["version"] for h in hist] == [1, 2] and hist[0]["body"]["note"] == "clean breakout"


def test_a_stale_write_is_refused_not_overwritten(tmp_path):
    store = _store(tmp_path)
    store.put(validate("trd_1", note="v1"), actor="joe", expected_version=0)
    with pytest.raises(NoteConflict) as info:
        store.put(validate("trd_1", note="from another tab"), actor="tom", expected_version=0)
    assert info.value.current == 1 and store.get("trd_1").note == "v1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"grade": "D"},
        {"tags": ["has spaces!"]},
        {"tags": [f"t{i}" for i in range(13)]},
        {"screenshot_url": "javascript:alert(1)"},
        {"screenshot_url": "ftp://x/y.png"},
        {"note": "x" * 4001},
    ],
)
def test_invalid_fields_are_refused(kwargs):
    with pytest.raises(NoteInvalid):
        validate("trd_1", **kwargs)


def test_filters_and_vocabulary_are_server_side(tmp_path):
    store = _store(tmp_path)
    store.put(validate("a", tags=["breakout"], grade="A"), actor="joe", expected_version=0)
    store.put(validate("b", tags=["breakout", "news"], grade="C", mistakes=["chased"],
                       intervention_reason="manual close"), actor="joe", expected_version=0)
    store.put(validate("c", grade="C", screenshot_url="https://img.example/c.png"), actor="joe", expected_version=0)
    assert {n.trade_id for n in store.matching(tag="breakout")} == {"a", "b"}
    assert {n.trade_id for n in store.matching(grade="C")} == {"b", "c"}
    assert {n.trade_id for n in store.matching(mistake="chased")} == {"b"}
    assert {n.trade_id for n in store.matching(intervened=True)} == {"b"}
    assert store.vocabulary() == {"tags": {"breakout": 2, "news": 1}, "mistakes": {"chased": 1}, "grades": {"A": 1, "C": 2}}
