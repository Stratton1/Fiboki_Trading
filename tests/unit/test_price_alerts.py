"""Price and level alert evaluation (obs/price_alerts.py): deterministic, idempotent, honest about firing."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fiboki.data.recorder import MarketState, QuoteRecord
from fiboki.obs.price_alerts import AlertConflict, PriceAlertStore, RuleSpec

T0 = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def q(sec: int, mid: float, symbol: str = "EURUSD", spread: float = 0.0002) -> QuoteRecord:
    return QuoteRecord(timestamp=T0 + timedelta(seconds=sec), instrument=symbol, bid=mid - spread / 2,
                       ask=mid + spread / 2, provider="oanda_practice_pricing", market_state=MarketState.OPEN,
                       sequence=sec)


@pytest.fixture
def store(tmp_path):
    return PriceAlertStore(tmp_path / "alerts.sqlite")


def test_above_fires_once_and_disarms(store):
    rule = store.create("joe", RuleSpec("eurusd", "above", 1.1000), actor="joe", now=T0)
    assert rule.spec.instrument == "EURUSD" and rule.armed
    fired = store.evaluate([q(10, 1.0990), q(20, 1.1000), q(30, 1.1010)], now=T0 + timedelta(seconds=31))
    assert [(n.price, n.kind) for n in fired] == [(1.1000, "above")]  # at the level counts as above
    after = store.get("joe", rule.id)
    assert after is not None and after.armed is False and after.fired_count == 1
    assert store.evaluate([q(40, 1.0900), q(50, 1.1100)]) == []  # disarmed


def test_rearm_fires_again_after_going_back(store):
    store.create("joe", RuleSpec("EURUSD", "below", 1.1000, mode="rearm"), actor="joe", now=T0)
    fired = store.evaluate([q(10, 1.1010), q(20, 1.0990), q(30, 1.0980), q(40, 1.1020), q(50, 1.0950)])
    assert [n.price for n in fired] == [1.0990, 1.0950]


def test_cross_needs_a_known_side_and_fires_both_ways(store):
    store.create("joe", RuleSpec("EURUSD", "cross", 1.1000, mode="rearm"), actor="joe", now=T0)
    fired = store.evaluate([q(10, 1.0990), q(20, 1.1005), q(30, 1.1010), q(40, 1.0995)])
    assert [(round(n.price, 4), n.message.split(" crossed to ")[1].split(" ")[0]) for n in fired] == [
        (1.1005, "above"), (1.0995, "below"),
    ]
    # A cross rule's first quote only learns the side.
    store.create("joe", RuleSpec("GBPUSD", "cross", 1.2700), actor="joe", now=T0)
    assert store.evaluate([q(60, 1.2800, "GBPUSD")]) == []


def test_replaying_the_same_quotes_never_fires_twice(store):
    store.create("joe", RuleSpec("EURUSD", "cross", 1.1000, mode="rearm"), actor="joe", now=T0)
    quotes = [q(10, 1.0990), q(20, 1.1010)]
    assert len(store.evaluate(quotes)) == 1
    assert store.evaluate(quotes) == []
    assert store.counts("joe")[0] == 1


def test_quotes_before_the_rule_existed_are_ignored(store):
    store.create("joe", RuleSpec("EURUSD", "above", 1.1000), actor="joe", now=T0 + timedelta(minutes=5))
    assert store.evaluate([q(10, 1.2000)]) == []


def test_side_selection_uses_the_quoted_side(store):
    store.create("joe", RuleSpec("EURUSD", "above", 1.1000, side="ask"), actor="joe", now=T0)
    fired = store.evaluate([q(10, 1.0999, spread=0.0004)])  # ask 1.1001
    assert len(fired) == 1 and fired[0].price == pytest.approx(1.1001)


def test_evaluation_is_deterministic(tmp_path):
    def run(path):
        s = PriceAlertStore(path)
        s.create("joe", RuleSpec("EURUSD", "cross", 1.1000, mode="rearm"), actor="joe", now=T0)
        quotes = [q(i, 1.1 + ((-1) ** i) * 0.0003 * (i % 4)) for i in range(1, 40)]
        return [(n.price, n.kind, n.quote_time) for n in s.evaluate(quotes, now=T0 + timedelta(minutes=1))]

    assert run(tmp_path / "a.sqlite") == run(tmp_path / "b.sqlite")


def test_edit_rearms_and_is_versioned(store):
    rule = store.create("joe", RuleSpec("EURUSD", "above", 1.1000), actor="joe", now=T0)
    store.evaluate([q(10, 1.2000)])
    with pytest.raises(AlertConflict):
        store.update("joe", rule.id, RuleSpec("EURUSD", "above", 1.3000), base_version=7, actor="joe")
    edited = store.update("joe", rule.id, RuleSpec("EURUSD", "above", 1.3000), base_version=1, actor="joe")
    assert edited.version == 2 and edited.armed and edited.last_side is None
    store.delete("joe", rule.id, base_version=2, actor="joe")
    assert store.rules("joe") == []


def test_invalid_specs_are_refused(store):
    for bad in (RuleSpec("EURUSD", "above", 0.0), RuleSpec("EURUSD", "sideways", 1.1),  # type: ignore[arg-type]
                RuleSpec("EURUSD", "above", float("nan")), RuleSpec("EURUSD", "above", 1.1, note="x\x00")):
        with pytest.raises(ValueError):
            store.create("joe", bad, actor="joe")


def test_read_marker_moves_forward_only_and_is_per_operator(store):
    store.create("joe", RuleSpec("EURUSD", "cross", 1.1, mode="rearm"), actor="joe", now=T0)
    store.create("tom", RuleSpec("EURUSD", "above", 1.1), actor="tom", now=T0)
    store.evaluate([q(10, 1.09), q(20, 1.11), q(30, 1.09), q(40, 1.11)])
    total, unread, through = store.counts("joe")
    assert (total, unread, through) == (3, 3, 0)
    newest = store.notifications("joe")[0].seq
    assert store.mark_read("joe", newest - 1) == newest - 1
    assert store.counts("joe")[1] == 1
    assert store.mark_read("joe", 1) == newest - 1  # never back
    assert store.mark_read("joe", 10**9) == newest  # capped at the newest
    assert store.counts("tom") == (1, 1, 0)
    assert store.evaluator_state().fired == 4 and store.evaluator_state().quotes_seen == 4


def test_the_history_tables_are_append_only(store):
    import sqlite3

    store.create("joe", RuleSpec("EURUSD", "above", 1.1), actor="joe", now=T0)
    store.evaluate([q(10, 1.2)])
    with sqlite3.connect(store.path) as conn, pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM notification")
