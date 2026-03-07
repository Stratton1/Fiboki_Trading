"""Tests for BOT-07 through BOT-12 (Hybrid family strategies)."""

import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.core.models import Timeframe
from fibokei.strategies.bot07_kumo_twist import KumoTwistAnticipator
from fibokei.strategies.bot08_kihon_suchi import KihonSuchiCycle
from fibokei.strategies.bot09_golden_cloud import GoldenCloudConfluence
from fibokei.strategies.bot10_kijun_fib import KijunFibContinuation
from fibokei.strategies.bot11_sanyaku_fib_ext import SanyakuFibExtension
from fibokei.strategies.bot12_kumo_fib_tz import KumoFibTimeZone


STRATEGIES = [
    (KumoTwistAnticipator, "bot07_kumo_twist", "Kumo Twist Anticipator", "ichimoku"),
    (KihonSuchiCycle, "bot08_kihon_suchi", "Kihon Suchi Time Cycle Confluence", "ichimoku"),
    (GoldenCloudConfluence, "bot09_golden_cloud", "Golden Cloud Confluence", "hybrid"),
    (KijunFibContinuation, "bot10_kijun_fib", "Kijun + 38.2% Shallow Continuation", "hybrid"),
    (SanyakuFibExtension, "bot11_sanyaku_fib_ext", "Sanyaku + Fib Extension Targets", "hybrid"),
    (KumoFibTimeZone, "bot12_kumo_fib_tz", "Kumo Twist + Fibonacci Time Zone", "hybrid"),
]


class TestStrategyIdentity:
    @pytest.mark.parametrize("cls,sid,name,family", STRATEGIES)
    def test_identity_fields(self, cls, sid, name, family):
        s = cls()
        assert s.strategy_id == sid
        assert s.strategy_name == name
        assert s.strategy_family == family
        assert s.supports_long is True
        assert s.supports_short is True

    def test_bot07_complexity_high(self):
        assert KumoTwistAnticipator().complexity_level == "high"

    def test_bot12_complexity_advanced(self):
        assert KumoFibTimeZone().complexity_level == "advanced"

    def test_hybrid_requires_fibonacci(self):
        assert GoldenCloudConfluence().requires_fibonacci is True
        assert KijunFibContinuation().requires_fibonacci is True
        assert SanyakuFibExtension().requires_fibonacci is True
        assert KumoFibTimeZone().requires_fibonacci is True


class TestStrategyIndicators:
    @pytest.mark.parametrize("cls,sid,name,family", STRATEGIES)
    def test_compute_indicators(self, cls, sid, name, family, sample_eurusd_h1_path):
        from fibokei.data.loader import load_ohlcv_csv

        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        s = cls()
        result = s.run_preparation(df)
        assert "tenkan_sen" in result.columns
        assert "kijun_sen" in result.columns
        assert "atr" in result.columns


class TestStrategySignalGeneration:
    @pytest.mark.parametrize("cls,sid,name,family", STRATEGIES)
    def test_generates_signals_without_error(self, cls, sid, name, family, sample_eurusd_h1_path):
        from fibokei.data.loader import load_ohlcv_csv

        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        s = cls()
        df = s.run_preparation(df)
        context = {"instrument": "EURUSD", "timeframe": Timeframe.H1}

        signals = []
        for i in range(len(df)):
            sig = s.generate_signal(df, i, context.copy())
            if sig is not None:
                signals.append(sig)
                assert sig.strategy_id == sid
                assert sig.signal_valid is True

        assert isinstance(signals, list)


class TestStrategyBacktest:
    @pytest.mark.parametrize("cls,sid,name,family", STRATEGIES)
    def test_backtest_runs_without_error(self, cls, sid, name, family, sample_eurusd_h1_path):
        from fibokei.data.loader import load_ohlcv_csv

        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        bt = Backtester(cls(), BacktestConfig())
        result = bt.run(df, "EURUSD", Timeframe.H1)
        assert result.strategy_id == sid
        assert result.total_bars > 0
        assert len(result.equity_curve) > 0


class TestStrategyRegistry:
    def test_all_twelve_registered(self):
        from fibokei.strategies.registry import strategy_registry

        available = strategy_registry.list_available()
        ids = [s["id"] for s in available]
        for _, sid, _, _ in STRATEGIES:
            assert sid in ids, f"{sid} not registered"

    def test_total_strategy_count(self):
        from fibokei.strategies.registry import strategy_registry

        available = strategy_registry.list_available()
        assert len(available) == 12
