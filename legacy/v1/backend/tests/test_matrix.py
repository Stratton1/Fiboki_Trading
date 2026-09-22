"""Tests for research matrix engine."""

import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.core.models import Timeframe
from fibokei.research.filter import apply_exploratory_filter, apply_minimum_trade_filter
from fibokei.research.matrix import ResearchMatrix, ResearchResult


class TestResearchMatrix:
    def test_run_two_strategies(self, sample_eurusd_h1_path):
        data_dir = str(sample_eurusd_h1_path.parent)
        matrix = ResearchMatrix(
            strategies=["bot01_sanyaku", "bot02_kijun_pullback"],
            instruments=["EURUSD"],
            timeframes=[Timeframe.H1],
            config=BacktestConfig(),
        )
        results = matrix.run(data_dir)
        assert len(results) == 2
        # Results should be ranked
        assert results[0].rank == 1
        assert results[1].rank == 2
        assert results[0].composite_score >= results[1].composite_score

    def test_results_have_required_fields(self, sample_eurusd_h1_path):
        data_dir = str(sample_eurusd_h1_path.parent)
        matrix = ResearchMatrix(
            strategies=["bot01_sanyaku"],
            instruments=["EURUSD"],
            timeframes=[Timeframe.H1],
        )
        results = matrix.run(data_dir)
        assert len(results) == 1
        r = results[0]
        assert r.strategy_id == "bot01_sanyaku"
        assert r.instrument == "EURUSD"
        assert r.timeframe == "H1"
        assert isinstance(r.total_trades, int)
        assert isinstance(r.composite_score, float)
        assert r.rank == 1

    def test_missing_data_skips(self, sample_eurusd_h1_path):
        data_dir = str(sample_eurusd_h1_path.parent)
        matrix = ResearchMatrix(
            strategies=["bot01_sanyaku"],
            instruments=["XYZNONEXISTENT"],
            timeframes=[Timeframe.H1],
        )
        results = matrix.run(data_dir)
        # Should skip non-existent data
        assert len(results) == 0

    def test_unknown_strategy_skips(self, sample_eurusd_h1_path):
        data_dir = str(sample_eurusd_h1_path.parent)
        matrix = ResearchMatrix(
            strategies=["bot_nonexistent"],
            instruments=["EURUSD"],
            timeframes=[Timeframe.H1],
        )
        results = matrix.run(data_dir)
        assert len(results) == 0


class TestFilters:
    def test_minimum_trade_filter(self):
        results = [
            ResearchResult(strategy_id="a", instrument="X", timeframe="H1", total_trades=100),
            ResearchResult(strategy_id="b", instrument="X", timeframe="H1", total_trades=50),
            ResearchResult(strategy_id="c", instrument="X", timeframe="H1", total_trades=10),
        ]
        qualified, insufficient = apply_minimum_trade_filter(results, min_trades=80)
        assert len(qualified) == 1
        assert len(insufficient) == 2
        assert qualified[0].strategy_id == "a"

    def test_exploratory_filter(self):
        results = [
            ResearchResult(strategy_id="a", instrument="X", timeframe="H1", total_trades=100),
            ResearchResult(strategy_id="b", instrument="X", timeframe="H1", total_trades=50),
            ResearchResult(strategy_id="c", instrument="X", timeframe="H1", total_trades=10),
        ]
        exploratory = apply_exploratory_filter(results, min_trades=40)
        assert len(exploratory) == 1
        assert exploratory[0].strategy_id == "b"
        assert exploratory[0].status == "exploratory"
