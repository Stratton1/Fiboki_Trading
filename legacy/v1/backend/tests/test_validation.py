"""Tests for validation rerun module."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.research.validation import (
    ValidationBatchResult,
    ValidationResult,
    ValidationStatus,
    run_validation_rerun,
)


FIXTURES_DIR = Path(__file__).parent.parent.parent / "data" / "fixtures"


def _load_fixture_df():
    path = FIXTURES_DIR / "sample_eurusd_h1.csv"
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    df["instrument"] = "EURUSD"
    df["timeframe"] = "H1"
    return df


class TestValidationStatus:
    def test_enum_values(self):
        assert ValidationStatus.PENDING.value == "pending"
        assert ValidationStatus.VALIDATED_SAME_SOURCE.value == "validated_same_source"
        assert ValidationStatus.VALIDATED_ALTERNATE.value == "validated_alternate"
        assert ValidationStatus.FAILED.value == "failed"
        assert ValidationStatus.SKIPPED.value == "skipped"


class TestValidationDataclasses:
    def test_result_defaults(self):
        r = ValidationResult(strategy_id="s1", instrument="EURUSD", timeframe="H1")
        assert r.passed is False
        assert r.validation_status == "pending"

    def test_batch_defaults(self):
        b = ValidationBatchResult()
        assert b.total_validated == 0
        assert b.pass_rate == 0.0


class TestRunValidationRerun:
    def test_basic_validation(self):
        """Validate a single combination with fixture data."""
        shortlist = [{
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "original_score": 0.5,
            "original_trades": 50,
        }]
        # Patch load_canonical to return fixture data
        with patch("fibokei.research.validation.load_canonical", return_value=_load_fixture_df()):
            batch = run_validation_rerun(shortlist)

        assert isinstance(batch, ValidationBatchResult)
        assert len(batch.results) == 1
        assert batch.total_validated == 1
        assert batch.total_skipped == 0
        r = batch.results[0]
        assert r.strategy_id == "bot01_sanyaku"
        assert r.validation_status in (
            ValidationStatus.VALIDATED_SAME_SOURCE.value,
            ValidationStatus.FAILED.value,
        )

    def test_no_data_skips(self):
        """When no data is available, the combo is skipped."""
        shortlist = [{
            "strategy_id": "bot01_sanyaku",
            "instrument": "XYZABC",
            "timeframe": "H1",
            "original_score": 0.5,
        }]
        with patch("fibokei.research.validation.load_canonical", return_value=None):
            batch = run_validation_rerun(shortlist)

        assert batch.total_skipped == 1
        assert batch.total_validated == 0
        assert batch.results[0].validation_status == ValidationStatus.SKIPPED.value

    def test_alternate_provider_status(self):
        """When validation_provider is set, status should be VALIDATED_ALTERNATE."""
        shortlist = [{
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "original_score": 0.3,
        }]
        with patch("fibokei.research.validation.load_canonical", return_value=_load_fixture_df()):
            batch = run_validation_rerun(shortlist, validation_provider="dukascopy")

        r = batch.results[0]
        assert r.validation_provider == "dukascopy"
        if r.passed:
            assert r.validation_status == ValidationStatus.VALIDATED_ALTERNATE.value

    def test_pass_rate_computation(self):
        """Pass rate is correctly computed."""
        shortlist = [
            {"strategy_id": "bot01_sanyaku", "instrument": "EURUSD", "timeframe": "H1", "original_score": 0.01},
            {"strategy_id": "bot01_sanyaku", "instrument": "EURUSD", "timeframe": "H1", "original_score": 0.01},
        ]
        with patch("fibokei.research.validation.load_canonical", return_value=_load_fixture_df()):
            batch = run_validation_rerun(shortlist)

        assert batch.total_validated == 2
        assert 0.0 <= batch.pass_rate <= 1.0
        expected_rate = round(batch.total_passed / batch.total_validated, 4)
        assert batch.pass_rate == expected_rate

    def test_score_divergence(self):
        """Score divergence = original - validation."""
        shortlist = [{
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "original_score": 0.5,
        }]
        with patch("fibokei.research.validation.load_canonical", return_value=_load_fixture_df()):
            batch = run_validation_rerun(shortlist)

        r = batch.results[0]
        expected_div = round(0.5 - r.validation_score, 4)
        assert r.score_divergence == expected_div

    def test_multiple_combos_mixed(self):
        """Mix of available and unavailable data."""
        shortlist = [
            {"strategy_id": "bot01_sanyaku", "instrument": "EURUSD", "timeframe": "H1", "original_score": 0.3},
            {"strategy_id": "bot01_sanyaku", "instrument": "MISSING", "timeframe": "H1", "original_score": 0.3},
        ]

        def mock_load(inst, tf, provider=None):
            if inst == "EURUSD":
                return _load_fixture_df()
            return None

        with patch("fibokei.research.validation.load_canonical", side_effect=mock_load):
            batch = run_validation_rerun(shortlist)

        assert len(batch.results) == 2
        assert batch.total_skipped == 1
        assert batch.total_validated == 1
