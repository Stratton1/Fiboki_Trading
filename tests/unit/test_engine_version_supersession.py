"""Stored results from an older engine generation are marked, never deleted.

The exit-vocabulary change invalidated every stored backtest and research
artefact that predates it: the old engine read ``take_profit_prices[0]`` and
nothing else, so every number it produced describes a stop-and-first-target-only
realisation of the document rather than the document. The evaluation cache key
was bumped so stale entries miss, but a ``BacktestRecord`` in the research store
does not self-invalidate -- it sits there looking exactly like a current one.

These tests pin the two halves of the fix: every record is stamped on the way
in, and a sweep appends a supersession note against every record carrying an
older stamp.
"""
from __future__ import annotations

import pytest

from fiboki.backtest.version import (
    ENGINE_VERSION,
    ENGINE_VERSION_HISTORY,
    is_current,
    supersession_reason,
)
from fiboki.research.artefacts import BacktestRecord, ResearchNote, ResearchStore


@pytest.fixture()
def store(tmp_path) -> ResearchStore:
    with ResearchStore(tmp_path / "research") as handle:
        yield handle


def _file_pre_change(store: ResearchStore, record: BacktestRecord) -> BacktestRecord:
    """File a record the way the OLD code did: straight in, with no stamp.

    ``add_backtest`` stamps anything unstamped, which is correct and is exactly
    why it cannot be used to simulate a pre-change record. Going through the
    store's own insert is the honest reproduction of a row written before the
    stamp existed.
    """
    store._add("backtests", record)
    return record


def _record(**kwargs) -> BacktestRecord:
    return BacktestRecord(
        strategy_id=kwargs.pop("strategy_id", "donchian_breakout_atr"),
        instruments=("XAUUSD",),
        timeframe="H4",
        metrics={"net_profit": 2087.24, "n_trades": 6},
        **kwargs,
    )


# --------------------------------------------------------------- the stamp


def test_a_new_record_is_stamped_with_the_current_engine_version(store) -> None:
    filed = store.add_backtest(_record())
    assert filed.engine_version == ENGINE_VERSION
    assert store.get_backtest(filed.backtest_id).engine_version == ENGINE_VERSION


def test_an_explicit_stamp_is_not_overwritten(store) -> None:
    """A migration replaying an old run must be able to say which engine ran it."""
    filed = store.add_backtest(_record(engine_version="engine_v1_stop_and_first_target"))
    assert filed.engine_version == "engine_v1_stop_and_first_target"


def test_an_empty_stamp_means_pre_change_not_unknown() -> None:
    assert not is_current("")
    assert not is_current(None)
    assert is_current(ENGINE_VERSION)
    assert dict(ENGINE_VERSION_HISTORY)[""].startswith("Unstamped")
    assert "not comparable" in supersession_reason("")


# --------------------------------------------------------------- the sweep


def test_the_sweep_marks_a_pre_change_record_without_deleting_it(store) -> None:
    stale = _file_pre_change(store, _record())
    current = store.add_backtest(_record(strategy_id="ichimoku_kumo_trend"))

    notes = store.sweep_superseded_backtests()
    assert len(notes) == 1
    note = notes[0]
    assert note.supersedes == stale.backtest_id
    assert store.SUPERSEDED_TAG in note.tags
    assert note.links["engine_version_found"] == "(unstamped)"
    assert note.links["engine_version_required"] == ENGINE_VERSION

    # NOTHING was removed: the superseded record is still readable, which is the
    # whole point -- a result quoted in a decision has to stay legible beside
    # the reason it should not have been.
    assert store.get_backtest(stale.backtest_id).backtest_id == stale.backtest_id
    assert store.get_backtest(current.backtest_id).engine_version == ENGINE_VERSION
    assert len(store.backtests_for("donchian_breakout_atr")) == 1


def test_the_sweep_is_idempotent(store) -> None:
    _file_pre_change(store, _record())
    first = store.sweep_superseded_backtests()
    second = store.sweep_superseded_backtests()
    assert len(first) == 1
    assert second == ()
    supersession_notes = [
        n
        for n in store.list("notes")
        if isinstance(n, ResearchNote) and store.SUPERSEDED_TAG in n.tags
    ]
    assert len(supersession_notes) == 1


def test_a_dry_run_writes_nothing(store) -> None:
    _file_pre_change(store, _record())
    planned = store.sweep_superseded_backtests(dry_run=True)
    assert len(planned) == 1
    assert store.superseded_backtest_ids() == frozenset()
    assert store.counts()["notes"] == 0


def test_a_record_from_a_different_future_generation_is_also_swept(store) -> None:
    """The sweep tests for EQUALITY with the current generation, not ordering.

    A record written by a build that has since been rolled back is as
    incomparable as one written before the change, and treating "newer" as
    acceptable would quietly admit numbers this engine cannot reproduce.
    """
    store.add_backtest(_record(engine_version="engine_v3_something_else"))
    notes = store.sweep_superseded_backtests()
    assert len(notes) == 1
    assert notes[0].links["engine_version_found"] == "engine_v3_something_else"


def test_superseded_records_can_be_excluded_from_what_gets_quoted(store) -> None:
    stale = _file_pre_change(store, _record())
    fresh = store.add_backtest(_record())
    store.sweep_superseded_backtests()

    everything = store.backtests_for("donchian_breakout_atr")
    quotable = store.backtests_for("donchian_breakout_atr", include_superseded=False)
    assert {r.backtest_id for r in everything} == {stale.backtest_id, fresh.backtest_id}
    assert [r.backtest_id for r in quotable] == [fresh.backtest_id]
    assert store.latest_backtest_for(
        "donchian_breakout_atr", include_superseded=False
    ).backtest_id == fresh.backtest_id


def test_the_note_names_what_changed_so_a_reader_need_not_guess(store) -> None:
    _file_pre_change(store, _record())
    note = store.sweep_superseded_backtests()[0]
    assert "trailing" in note.body
    assert "allocations discarded" in note.body
    assert ENGINE_VERSION in note.body
