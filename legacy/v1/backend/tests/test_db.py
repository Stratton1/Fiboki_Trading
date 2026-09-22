"""Tests for database persistence layer."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.result import BacktestResult
from fibokei.core.models import Direction, Timeframe
from fibokei.core.trades import ExitReason, TradeResult
from fibokei.db.database import get_engine, get_session_factory, init_db
from fibokei.db.models import BacktestRunModel, Base, ResearchResultModel, TradeModel
from fibokei.db.repository import (
    get_backtest_results,
    get_research_rankings,
    save_backtest_result,
    save_dataset_meta,
    save_research_results,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database and session for testing."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _make_trade(pnl: float) -> TradeResult:
    return TradeResult(
        trade_id="t1",
        strategy_id="bot01_sanyaku",
        instrument="EURUSD",
        timeframe=Timeframe.H1,
        direction=Direction.LONG,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        entry_price=1.10,
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        exit_price=1.10 + pnl / 10000,
        exit_reason=ExitReason.TAKE_PROFIT_HIT if pnl > 0 else ExitReason.STOP_LOSS_HIT,
        pnl=pnl,
        pnl_pct=pnl / 100,
        position_size=10000,
        bars_in_trade=10,
    )


def _make_backtest_result(trades=None) -> BacktestResult:
    if trades is None:
        trades = [_make_trade(100), _make_trade(-50)]
    return BacktestResult(
        strategy_id="bot01_sanyaku",
        instrument="EURUSD",
        timeframe=Timeframe.H1,
        config=BacktestConfig(),
        trades=trades,
        equity_curve=[10000.0] * 50,
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 5, tzinfo=timezone.utc),
        total_bars=50,
    )


class TestDatabaseInit:
    def test_create_tables(self):
        engine = create_engine("sqlite:///:memory:")
        init_db(engine)
        # Check tables exist
        assert "backtest_runs" in Base.metadata.tables
        assert "trades" in Base.metadata.tables
        assert "research_results" in Base.metadata.tables
        assert "datasets" in Base.metadata.tables
        assert "strategy_configs" in Base.metadata.tables

    def test_get_engine_default(self):
        engine = get_engine("sqlite:///:memory:")
        assert engine is not None


class TestBacktestPersistence:
    def test_save_backtest_result(self, db_session):
        result = _make_backtest_result()
        metrics = {
            "total_net_profit": 50.0,
            "sharpe_ratio": 1.5,
            "max_drawdown_pct": 5.0,
            "total_trades": 2,
        }
        run = save_backtest_result(db_session, result, metrics)
        assert run.id is not None
        assert run.strategy_id == "bot01_sanyaku"
        assert run.instrument == "EURUSD"
        assert run.total_trades == 2
        assert run.net_profit == 50.0

    def test_save_and_retrieve_trades(self, db_session):
        result = _make_backtest_result()
        metrics = {"total_net_profit": 50.0}
        run = save_backtest_result(db_session, result, metrics)

        trades = db_session.scalars(
            select(TradeModel).where(TradeModel.backtest_run_id == run.id)
        ).all()
        assert len(trades) == 2
        assert trades[0].strategy_id == "bot01_sanyaku"

    def test_retrieve_backtest_results(self, db_session):
        result = _make_backtest_result()
        save_backtest_result(db_session, result, {"total_net_profit": 50.0})

        runs = get_backtest_results(db_session)
        assert len(runs) == 1
        assert runs[0].strategy_id == "bot01_sanyaku"

    def test_filter_by_strategy(self, db_session):
        result1 = _make_backtest_result()
        save_backtest_result(db_session, result1, {"total_net_profit": 50.0})

        result2 = _make_backtest_result()
        result2.strategy_id = "bot02_kijun_pullback"
        save_backtest_result(db_session, result2, {"total_net_profit": 30.0})

        runs = get_backtest_results(db_session, strategy_id="bot01_sanyaku")
        assert len(runs) == 1
        assert runs[0].strategy_id == "bot01_sanyaku"

    def test_filter_by_instrument(self, db_session):
        result1 = _make_backtest_result()
        save_backtest_result(db_session, result1, {"total_net_profit": 50.0})

        result2 = _make_backtest_result()
        result2.instrument = "GBPUSD"
        save_backtest_result(db_session, result2, {"total_net_profit": 30.0})

        runs = get_backtest_results(db_session, instrument="GBPUSD")
        assert len(runs) == 1
        assert runs[0].instrument == "GBPUSD"


class TestResearchPersistence:
    def test_save_research_results(self, db_session):
        results = [
            {
                "run_id": "run_001",
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "composite_score": 0.85,
                "rank": 1,
                "metrics": {"win_rate": 0.6},
            },
            {
                "run_id": "run_001",
                "strategy_id": "bot02_kijun_pullback",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "composite_score": 0.72,
                "rank": 2,
                "metrics": {"win_rate": 0.55},
            },
        ]
        models = save_research_results(db_session, results)
        assert len(models) == 2
        assert models[0].composite_score == 0.85

    def test_get_research_rankings(self, db_session):
        results = [
            {
                "run_id": "run_001",
                "strategy_id": "bot01_sanyaku",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "composite_score": 0.72,
                "rank": 2,
            },
            {
                "run_id": "run_001",
                "strategy_id": "bot02_kijun_pullback",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "composite_score": 0.85,
                "rank": 1,
            },
        ]
        save_research_results(db_session, results)

        rankings = get_research_rankings(db_session)
        assert len(rankings) == 2
        assert rankings[0].composite_score == 0.85  # Highest first
        assert rankings[0].strategy_id == "bot02_kijun_pullback"


class TestDatasetPersistence:
    def test_save_dataset_meta(self, db_session):
        meta = {
            "instrument": "EURUSD",
            "timeframe": "H1",
            "bar_count": 750,
            "file_path": "data/fixtures/sample_eurusd_h1.csv",
        }
        model = save_dataset_meta(db_session, meta)
        assert model.id is not None
        assert model.instrument == "EURUSD"
        assert model.bar_count == 750
