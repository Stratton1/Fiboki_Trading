"""The holdout registry must refuse a second look. This is the package's core test.

V1's "held-out" out-of-sample numbers were computed on data the parameter search
had already read, and nothing in the code could have noticed. Every other test in
this package checks that a statistic is computed correctly; this one checks that
the partition those statistics are computed on is real.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.validation.evaluation import DateWindow
from fiboki.validation.holdout import (
    HoldoutAlreadyConsumed,
    HoldoutError,
    HoldoutLeak,
    HoldoutRegistry,
    UnknownHoldout,
)

START = pd.Timestamp("2019-01-01", tz="UTC")
END = pd.Timestamp("2024-01-01", tz="UTC")
DATASET = "eurusd_h1_v7"
HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture
def registry():
    with HoldoutRegistry.in_memory() as reg:
        reg.define(DATASET, data_start=START, data_end=END)
        yield reg


class TestTheSecondLookIsRefused:
    """The structural guarantee. If any of these invert, the package is a lie."""

    def test_a_second_claim_for_the_same_strategy_raises(self, registry) -> None:
        registry.claim(DATASET, HASH_A, strategy_id="alpha", actor="joe")
        with pytest.raises(HoldoutAlreadyConsumed) as exc:
            registry.claim(DATASET, HASH_A, strategy_id="alpha", actor="joe")
        message = str(exc.value)
        assert HASH_A[:12] in message
        assert "A holdout evaluated twice is an in-sample number" in message

    def test_it_raises_even_when_the_first_claim_never_recorded_an_outcome(
        self, registry
    ) -> None:
        """A crash mid-evaluation must not buy a retry.

        The claim is written BEFORE the evaluation runs precisely so that a
        process which dies halfway through has still spent the look. The
        alternative is a loop that re-rolls the holdout until it likes the answer.
        """
        registry.claim(DATASET, HASH_A)
        consumption = registry.consumption(DATASET, HASH_A)
        assert consumption is not None
        assert consumption.outcome is None  # nothing was ever recorded

        with pytest.raises(HoldoutAlreadyConsumed):
            registry.claim(DATASET, HASH_A)

    def test_renaming_the_strategy_does_not_buy_a_second_look(self, registry) -> None:
        """Keyed on CONTENT hash, so a new id for the same strategy is refused."""
        registry.claim(DATASET, HASH_A, strategy_id="n_wave_v1")
        with pytest.raises(HoldoutAlreadyConsumed):
            registry.claim(DATASET, HASH_A, strategy_id="n_wave_v2_renamed")

    def test_a_different_strategy_may_still_claim(self, registry) -> None:
        registry.claim(DATASET, HASH_A)
        token = registry.claim(DATASET, HASH_B)
        assert token.strategy_content_hash == HASH_B
        assert set(registry.consumed_hashes(DATASET)) == {HASH_A, HASH_B}

    def test_a_new_dataset_version_is_a_new_holdout(self, registry) -> None:
        """The legitimate way to get another look: data that did not exist before."""
        registry.claim(DATASET, HASH_A)
        registry.define("eurusd_h1_v8", data_start=START, data_end=END)
        token = registry.claim("eurusd_h1_v8", HASH_A)
        assert token.segment.dataset_version_id == "eurusd_h1_v8"

    def test_the_refusal_survives_a_reopened_database(self, tmp_path) -> None:
        path = tmp_path / "holdout.sqlite"
        with HoldoutRegistry(path) as reg:
            reg.define(DATASET, data_start=START, data_end=END)
            reg.claim(DATASET, HASH_A, actor="agent:research-loop")
        with HoldoutRegistry(path) as reopened:
            assert reopened.is_consumed(DATASET, HASH_A)
            with pytest.raises(HoldoutAlreadyConsumed):
                reopened.claim(DATASET, HASH_A)

    def test_an_outcome_can_only_be_recorded_once(self, registry) -> None:
        token = registry.claim(DATASET, HASH_A)
        registry.record_outcome(token, {"net_profit": 120.0})
        with pytest.raises(HoldoutError, match="already carries an outcome"):
            registry.record_outcome(token, {"net_profit": 999.0})


class TestTheSegmentIsTheFinalFifth:
    def test_the_default_segment_is_the_last_20_percent_of_the_range(
        self, registry
    ) -> None:
        segment = registry.segment(DATASET)
        assert segment.holdout_fraction == pytest.approx(0.20)
        total = (END - START).total_seconds()
        held = (segment.data_end - segment.holdout_start).total_seconds()
        assert held / total == pytest.approx(0.20, abs=1e-9)

    def test_research_and_holdout_windows_tile_the_range_without_overlap(
        self, registry
    ) -> None:
        segment = registry.segment(DATASET)
        assert not segment.research_window.overlaps(segment.window)
        assert segment.research_window.end == segment.window.start
        assert segment.research_window.start == segment.data_start
        assert segment.window.end == segment.data_end

    def test_defining_the_same_segment_twice_is_a_no_op(self, registry) -> None:
        again = registry.define(DATASET, data_start=START, data_end=END)
        assert again.holdout_start == registry.segment(DATASET).holdout_start

    def test_moving_a_defined_holdout_raises(self, registry) -> None:
        with pytest.raises(HoldoutError, match="refusing to redefine"):
            registry.define(DATASET, data_start=START, data_end=END, holdout_fraction=0.05)
        with pytest.raises(HoldoutError, match="refusing to redefine"):
            registry.define(
                DATASET, data_start=START, data_end=pd.Timestamp("2025-01-01", tz="UTC")
            )

    def test_an_undefined_dataset_cannot_be_claimed(self, registry) -> None:
        with pytest.raises(UnknownHoldout):
            registry.claim("never_defined", HASH_A)

    def test_define_from_index_puts_the_last_bar_inside_the_holdout(self) -> None:
        index = pd.date_range("2020-01-01", periods=500, freq="h", tz="UTC")
        with HoldoutRegistry.in_memory() as reg:
            segment = reg.define_from_index("bars_v1", index)
            assert segment.window.contains(index[-1])
            assert segment.research_window.contains(index[0])


class TestLeakageIsRefusedStructurally:
    def test_a_research_window_overlapping_the_holdout_raises(self, registry) -> None:
        segment = registry.segment(DATASET)
        leaky = DateWindow("greedy_train", START, segment.holdout_start + pd.Timedelta(days=1))
        with pytest.raises(HoldoutLeak, match="overlaps the reserved holdout"):
            registry.assert_untouched(DATASET, leaky)

    def test_the_legitimate_research_window_passes(self, registry) -> None:
        segment = registry.segment(DATASET)
        registry.assert_untouched(DATASET, segment.research_window)

    def test_every_window_is_checked_not_just_the_first(self, registry) -> None:
        segment = registry.segment(DATASET)
        clean = segment.research_window
        leaky = DateWindow("fold_5_test", segment.holdout_start, segment.data_end)
        with pytest.raises(HoldoutLeak):
            registry.assert_untouched(DATASET, clean, leaky)


class TestTheRecordIsAuditable:
    def test_the_consumption_records_who_and_why(self, registry) -> None:
        registry.claim(
            DATASET,
            HASH_A,
            strategy_id="n_wave",
            experiment_id="exp_123",
            actor="agent:research-loop",
            code_version="deadbeef",
        )
        record = registry.consumption(DATASET, HASH_A)
        assert record.actor == "agent:research-loop"
        assert record.experiment_id == "exp_123"
        assert record.code_version == "deadbeef"
        assert record.claimed_at.tzinfo is not None

    def test_the_outcome_is_attached_to_the_claim(self, registry) -> None:
        token = registry.claim(DATASET, HASH_A)
        registry.record_outcome(token, {"net_profit": -40.0, "n_trades": 88})
        record = registry.consumption(DATASET, HASH_A)
        assert record.outcome == {"net_profit": -40.0, "n_trades": 88}
        assert record.outcome_recorded_at is not None

    def test_consumptions_list_in_claim_order(self, registry) -> None:
        registry.claim(DATASET, HASH_A)
        registry.claim(DATASET, HASH_B)
        assert [c.strategy_content_hash for c in registry.consumptions(DATASET)] == [
            HASH_A,
            HASH_B,
        ]
