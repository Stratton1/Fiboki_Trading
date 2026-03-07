"""Tests for BOT-02 through BOT-06 (Ichimoku family strategies)."""

import pytest

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.core.models import Timeframe
from fibokei.strategies.bot02_kijun_pullback import KijunPullback
from fibokei.strategies.bot03_flat_senkou_b import FlatSenkouBBounce
from fibokei.strategies.bot04_chikou_momentum import ChikouMomentum
from fibokei.strategies.bot05_mtfa_sanyaku import MTFASanyaku
from fibokei.strategies.bot06_nwave import NWaveStructural


STRATEGIES = [
    (KijunPullback, "bot02_kijun_pullback", "Kijun-sen Pullback", "ichimoku"),
    (FlatSenkouBBounce, "bot03_flat_senkou_b", "Flat Senkou Span B Bounce", "ichimoku"),
    (ChikouMomentum, "bot04_chikou_momentum", "Chikou Open Space Momentum", "ichimoku"),
    (MTFASanyaku, "bot05_mtfa_sanyaku", "MTFA Sanyaku", "ichimoku"),
    (NWaveStructural, "bot06_nwave", "N-Wave Structural Targeting", "ichimoku"),
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


class TestStrategyIndicators:
    @pytest.mark.parametrize("cls,sid,name,family", STRATEGIES)
    def test_compute_indicators(self, cls, sid, name, family, sample_eurusd_h1_path):
        from fibokei.data.loader import load_ohlcv_csv

        df = load_ohlcv_csv(sample_eurusd_h1_path, "EURUSD", Timeframe.H1)
        s = cls()
        result = s.run_preparation(df)
        # All strategies should add Ichimoku columns
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

        # Just verify no errors — some strategies may produce 0 signals on this data
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
