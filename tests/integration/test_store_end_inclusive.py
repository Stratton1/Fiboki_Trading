"""``DataStore.read`` says in its signature that the end bound is INCLUSIVE (audit P3-9).

``DateWindow`` is half-open ``[start, end)``; the store's range read is closed
``[start, end]``. A caller that builds a train/test split from store reads and
forgets the difference puts the boundary bar in both halves. The argument is
now ``end_inclusive``; ``end`` remains as an alias with identical behaviour so
existing callers keep working.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from tests.data_fixtures import make_bars, make_metadata


def test_the_end_bound_is_inclusive_under_its_new_name(store) -> None:
    frame = make_bars(periods=48, timeframe=Timeframe.H1)
    stored = store.write_raw(frame, make_metadata(frame))
    boundary = frame.index[10]
    got = store.read(stored.version_id, end_inclusive=boundary)
    assert got.index[-1] == boundary
    assert len(got) == 11


def test_the_old_name_is_an_alias_with_the_same_meaning(store) -> None:
    frame = make_bars(periods=48, timeframe=Timeframe.H1)
    stored = store.write_raw(frame, make_metadata(frame))
    boundary = frame.index[10]
    new = store.read(stored.version_id, end_inclusive=boundary)
    old = store.read(stored.version_id, end=boundary)
    pd.testing.assert_frame_equal(new, old)


def test_passing_both_names_is_refused(store) -> None:
    frame = make_bars(periods=12, timeframe=Timeframe.H1)
    stored = store.write_raw(frame, make_metadata(frame))
    with pytest.raises(TypeError, match="not both"):
        store.read(stored.version_id, end_inclusive=frame.index[3], end=frame.index[3])
