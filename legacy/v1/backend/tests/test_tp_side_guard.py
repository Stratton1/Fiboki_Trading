"""Tests for the take-profit-side guard (sanitize_take_profits).

A take-profit on the wrong side of entry would otherwise be 'hit' immediately and
recorded as a take-profit at a loss (the nwave short bug). The guard strips it.
"""

from fibokei.backtester.position import sanitize_take_profits
from fibokei.core.models import Direction


def test_long_keeps_tp_above_entry():
    assert sanitize_take_profits([1.12], 1.10, Direction.LONG) == [1.12]


def test_long_drops_tp_below_entry():
    assert sanitize_take_profits([1.08], 1.10, Direction.LONG) == []


def test_short_keeps_tp_below_entry():
    assert sanitize_take_profits([1.08], 1.10, Direction.SHORT) == [1.08]


def test_short_drops_tp_above_entry():
    # The exact nwave bug: short with a target above entry.
    assert sanitize_take_profits([1.22675], 1.22577, Direction.SHORT) == []


def test_mixed_targets_filtered_by_side():
    assert sanitize_take_profits([1.08, 1.12, 1.15], 1.10, Direction.LONG) == [1.12, 1.15]


def test_empty_and_none():
    assert sanitize_take_profits([], 1.10, Direction.LONG) == []
    assert sanitize_take_profits(None, 1.10, Direction.SHORT) == []
