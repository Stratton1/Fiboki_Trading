"""Phase 20 gates: the strategy factory must be deterministic, serialisable,
look-ahead-free, and behaviourally identical to a hand-coded fixture.
"""

import numpy as np
import pandas as pd
import pytest

from fibokei.core.models import Direction, Timeframe
from fibokei.indicators.atr import ATR
from fibokei.indicators.moving_averages import EMA, RSI, SMA
from fibokei.strategies.factory import (
    RuleSpec,
    StopSpec,
    StrategySpec,
    TargetSpec,
    TrailingSpec,
    compile_spec,
    primitive_names,
)
from fibokei.strategies.factory.compiler import CompiledStrategy


def _synthetic_df(n=300, seed=7) -> pd.DataFrame:
    """Deterministic trending series with pullbacks."""
    rng = np.random.default_rng(seed)
    drift = np.linspace(0, 0.08, n)
    noise = rng.normal(0, 0.004, n).cumsum()
    close = 1.10 * (1 + drift + noise)
    high = close * (1 + np.abs(rng.normal(0, 0.0015, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0015, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts, "open": open_, "high": high,
        "low": low, "close": close, "volume": 1000.0,
    })


def _ema_cross_spec(**overrides) -> StrategySpec:
    base = dict(
        spec_id="ema_cross_test",
        name="EMA cross test",
        entry_rules=[RuleSpec(primitive="ema_cross", params={"fast": 8, "slow": 21})],
        filters=[RuleSpec(primitive="atr_min", params={"min_pct": 0.0001})],
        stop=StopSpec(model="atr_multiple", multiple=2.0, atr_period=14),
        target=TargetSpec(model="rr_multiple", multiple=2.0),
        direction="both",
    )
    base.update(overrides)
    return StrategySpec(**base)


class TestSpecModel:
    def test_round_trip_and_stable_hash(self):
        spec = _ema_cross_spec()
        clone = StrategySpec.from_json(spec.model_dump_json())
        assert clone.content_hash == spec.content_hash
        assert clone.canonical_json() == spec.canonical_json()

    def test_hash_changes_with_params(self):
        a = _ema_cross_spec()
        b = _ema_cross_spec(
            entry_rules=[RuleSpec(primitive="ema_cross", params={"fast": 9, "slow": 21})]
        )
        assert a.content_hash != b.content_hash

    def test_unknown_primitive_rejected(self):
        with pytest.raises(ValueError, match="Unknown rule primitive"):
            RuleSpec(primitive="time_travel_oracle")

    def test_zero_stop_rejected(self):
        with pytest.raises(ValueError, match="mandatory"):
            StopSpec(model="atr_multiple", multiple=0)

    def test_excess_risk_rejected(self):
        with pytest.raises(ValueError, match="central risk caps"):
            _ema_cross_spec(risk_pct=5.0)

    def test_primitive_registry_nonempty(self):
        names = primitive_names()
        assert "ema_cross" in names and "price_vs_kumo" in names

    def test_unknown_target_model_rejected(self):
        with pytest.raises(ValueError, match="Unknown target model"):
            TargetSpec(model="moon_shot")

    def test_unknown_trailing_model_rejected(self):
        with pytest.raises(ValueError, match="Unknown trailing model"):
            TrailingSpec(model="psychic_belt")

    def test_negative_trailing_multiple_rejected(self):
        with pytest.raises(ValueError, match="Trailing multiple"):
            TrailingSpec(model="atr", multiple=-1.0)

    def test_unknown_direction_rejected(self):
        with pytest.raises(ValueError, match="long\\|short\\|both"):
            _ema_cross_spec(direction="sideways")


class TestDeterminismGate:
    """Factory strategy must equal a hand-coded fixture, signal for signal."""

    def _hand_coded_signals(self, df: pd.DataFrame) -> list[tuple]:
        """Reference implementation: EMA(8/21) cross + ATR floor filter,
        ATR(14)x2 stop, 2R target — written independently of the factory."""
        df = df.copy()
        df = EMA(8).compute(df)
        df = EMA(21).compute(df)
        df = ATR(14).compute(df)
        out = []
        for i in range(1, len(df)):
            f0, s0 = df["ema_8"].iloc[i - 1], df["ema_21"].iloc[i - 1]
            f1, s1 = df["ema_8"].iloc[i], df["ema_21"].iloc[i]
            atr, close = df["atr"].iloc[i], df["close"].iloc[i]
            if any(pd.isna(v) for v in (f0, s0, f1, s1, atr)):
                continue
            if atr / close < 0.0001 or atr <= 0:
                continue
            if f0 <= s0 and f1 > s1:
                d = "long"
            elif f0 >= s0 and f1 < s1:
                d = "short"
            else:
                continue
            stop = close - 2 * atr if d == "long" else close + 2 * atr
            tp = close + 2 * abs(close - stop) if d == "long" else close - 2 * abs(close - stop)
            out.append((i, d, round(close, 10), round(stop, 10), round(tp, 10)))
        return out

    def _factory_signals(self, df: pd.DataFrame) -> list[tuple]:
        strat = compile_spec(_ema_cross_spec())
        df = strat.compute_indicators(strat.prepare_data(df.copy()))
        ctx = {"instrument": "EURUSD", "timeframe": Timeframe.H1}
        out = []
        for i in range(1, len(df)):
            sig = strat.generate_signal(df, i, ctx)
            if sig is None:
                continue
            d = "long" if sig.direction == Direction.LONG else "short"
            out.append((
                i, d, round(sig.proposed_entry, 10),
                round(sig.stop_loss, 10), round(sig.take_profit_primary, 10),
            ))
        return out

    def test_factory_equals_hand_coded(self):
        df = _synthetic_df()
        hand = self._hand_coded_signals(df)
        fact = self._factory_signals(df)
        assert len(hand) > 0, "fixture produced no signals — test data too quiet"
        assert fact == hand

    def test_repeat_compilation_is_deterministic(self):
        df = _synthetic_df()
        assert self._factory_signals(df) == self._factory_signals(df)


class TestNoLookAhead:
    def test_future_bars_do_not_change_signal(self):
        """Mutating bars AFTER idx must not change the signal at idx."""
        df = _synthetic_df()
        strat = compile_spec(_ema_cross_spec())
        prepared = strat.compute_indicators(strat.prepare_data(df.copy()))
        ctx = {"instrument": "EURUSD", "timeframe": Timeframe.H1}
        # Find a signal bar
        sig_idx, sig = next(
            (i, s) for i in range(1, len(prepared))
            if (s := strat.generate_signal(prepared, i, ctx)) is not None
        )
        # Wreck everything after sig_idx and recompute indicators
        df2 = df.copy()
        df2.loc[df2.index > sig_idx, ["open", "high", "low", "close"]] = 999.0
        prepared2 = strat.compute_indicators(strat.prepare_data(df2))
        sig2 = strat.generate_signal(prepared2, sig_idx, ctx)
        assert sig2 is not None
        assert sig2.proposed_entry == sig.proposed_entry
        assert sig2.stop_loss == sig.stop_loss


class TestTradePlanAndExits:
    def test_trade_plan_carries_stop_and_risk(self):
        df = _synthetic_df()
        strat = compile_spec(_ema_cross_spec())
        prepared = strat.compute_indicators(strat.prepare_data(df.copy()))
        ctx = {"instrument": "EURUSD", "timeframe": Timeframe.H1}
        sig = next(
            s for i in range(1, len(prepared))
            if (s := strat.generate_signal(prepared, i, ctx)) is not None
        )
        plan = strat.build_trade_plan(strat.validate_signal(sig, ctx), ctx)
        assert plan.stop_loss == sig.stop_loss
        assert plan.take_profit_targets == [sig.take_profit_primary]
        assert plan.risk_pct == 1.0
        assert plan.max_bars_in_trade == 50

    def test_time_stop_exit(self):
        from fibokei.core.trades import ExitReason
        df = _synthetic_df()
        strat = compile_spec(_ema_cross_spec(max_bars_in_trade=5))
        prepared = strat.compute_indicators(strat.prepare_data(df.copy()))
        pos = {"direction": "long", "entry_idx": 100, "stop_loss": 1.0}
        assert strat.generate_exit(pos, prepared, 105, {}) == ExitReason.TIME_STOP_EXIT
        assert strat.generate_exit(pos, prepared, 102, {}) in (None,
            ExitReason.OPPOSITE_SIGNAL_EXIT)


class TestDirectionFixtures:
    """Long-only and short-only specs must produce signals only in their
    declared direction, regardless of available data."""

    def test_long_only_produces_only_long_signals(self):
        df = _synthetic_df()
        strat = compile_spec(_ema_cross_spec(direction="long"))
        prepared = strat.compute_indicators(strat.prepare_data(df.copy()))
        ctx = {"instrument": "EURUSD", "timeframe": Timeframe.H1}
        sigs = [
            s for i in range(1, len(prepared))
            if (s := strat.generate_signal(prepared, i, ctx)) is not None
        ]
        assert sigs, "long-only fixture produced no signals"
        assert all(s.direction == Direction.LONG for s in sigs)

    def test_short_only_produces_only_short_signals(self):
        # Use a downward-drifting series so the short-only spec actually fires.
        df = _synthetic_df(seed=11)
        df["close"] = df["close"].iloc[0] * 2 - df["close"]  # mirror around start
        df["high"] = df[["open", "close"]].max(axis=1) * 1.001
        df["low"] = df[["open", "close"]].min(axis=1) * 0.999
        strat = compile_spec(_ema_cross_spec(direction="short"))
        prepared = strat.compute_indicators(strat.prepare_data(df.copy()))
        ctx = {"instrument": "EURUSD", "timeframe": Timeframe.H1}
        sigs = [
            s for i in range(1, len(prepared))
            if (s := strat.generate_signal(prepared, i, ctx)) is not None
        ]
        if sigs:  # data-dependent, just confirm purity when any fire
            assert all(s.direction == Direction.SHORT for s in sigs)


class TestRuleOrderInvariance:
    """All-rule semantics: reordering filter / confirmation lists must not
    change the produced signals (rules combine with AND)."""

    def test_reordered_filters_produce_identical_signals(self):
        df = _synthetic_df()
        spec_a = _ema_cross_spec(
            filters=[
                RuleSpec(primitive="atr_min", params={"min_pct": 0.0001}),
                RuleSpec(primitive="atr_max", params={"max_pct": 0.05}),
            ],
        )
        spec_b = _ema_cross_spec(
            filters=[
                RuleSpec(primitive="atr_max", params={"max_pct": 0.05}),
                RuleSpec(primitive="atr_min", params={"min_pct": 0.0001}),
            ],
        )
        def signals_for(spec):
            s = compile_spec(spec)
            prepared = s.compute_indicators(s.prepare_data(df.copy()))
            ctx = {"instrument": "EURUSD", "timeframe": Timeframe.H1}
            return [
                (i, sig.direction, round(sig.proposed_entry, 10))
                for i in range(1, len(prepared))
                if (sig := s.generate_signal(prepared, i, ctx)) is not None
            ]
        assert signals_for(spec_a) == signals_for(spec_b)


class TestIndicatorCoverage:
    """The compiler must include every indicator a spec ultimately needs,
    even when the indicator isn't referenced by any rule directly."""

    def test_atr_target_alone_pulls_atr_indicator(self):
        # Spec uses a non-ATR stop and a non-ATR rule, but an ATR target.
        # Before the fix this would KeyError at signal time.
        spec = _ema_cross_spec(
            stop=StopSpec(model="fixed_pct", multiple=1.0),
            target=TargetSpec(model="atr_multiple", multiple=2.0),
            filters=[],  # no rule-driven ATR either
        )
        strat = compile_spec(spec)
        names = [ind.name for ind in strat._indicators]
        assert "atr" in names, f"expected 'atr' in {names}"
        df = _synthetic_df()
        prepared = strat.compute_indicators(strat.prepare_data(df.copy()))
        assert "atr" in prepared.columns
        ctx = {"instrument": "EURUSD", "timeframe": Timeframe.H1}
        # No KeyError, no crash. Signal may or may not fire — just shouldn't crash.
        for i in range(1, len(prepared)):
            strat.generate_signal(prepared, i, ctx)

    def test_kijun_stop_pulls_ichimoku(self):
        spec = _ema_cross_spec(stop=StopSpec(model="kijun", multiple=1.0))
        strat = compile_spec(spec)
        names = [ind.name for ind in strat._indicators]
        assert "ichimoku" in names or any("kijun" in n for n in names) or any(
            "ichimoku" in n.lower() for n in names
        )


class TestRegistryIsolation:
    """Factory strategies in the global registry must be *deliberate and
    tiered* — only the curated traditional_gen1/hybrid_gen1 families (Phase 4+),
    never an untiered or ad-hoc compiled spec leaking in unclassified."""

    def test_registered_factory_strategies_are_tiered(self):
        from fibokei.strategies.registry import (
            classify_strategy,
            strategy_registry,
        )

        for sid, cls in strategy_registry._strategies.items():
            if not issubclass(cls, CompiledStrategy):
                continue
            # Every registered factory strategy must use a factory id and a
            # curated tier — never 'experimental' (that would be a silent leak).
            assert sid.startswith("factory_"), (
                f"CompiledStrategy {sid} registered without a factory id"
            )
            assert classify_strategy(sid) in (
                "traditional_gen1", "hybrid_gen1", "triple_hybrid_gen1"
            ), f"Factory strategy {sid} leaked in untiered/unclassified"

    def test_ad_hoc_compiled_spec_is_not_auto_registered(self):
        """Compiling a one-off spec must not register it globally."""
        from fibokei.strategies.factory.spec import RuleSpec, StrategySpec
        from fibokei.strategies.registry import strategy_registry

        spec = StrategySpec(
            spec_id="adhoc_isolation_probe",
            name="Ad-hoc Probe",
            entry_rules=[RuleSpec(primitive="price_vs_ema", params={"period": 50})],
        )
        strat = compile_spec(spec)
        assert strat.strategy_id not in strategy_registry._strategies


class TestIndicatorKnownValues:
    def test_sma_known_values(self):
        df = pd.DataFrame({"open": 0, "high": 0, "low": 0,
                           "close": [1.0, 2.0, 3.0, 4.0, 5.0]})
        out = SMA(3).compute(df)
        assert out["sma_3"].iloc[-1] == pytest.approx(4.0)
        assert out["sma_3"].iloc[2] == pytest.approx(2.0)

    def test_ema_known_values(self):
        # EMA(3), alpha=0.5: seeds at first close
        df = pd.DataFrame({"open": 0, "high": 0, "low": 0,
                           "close": [2.0, 4.0, 6.0]})
        out = EMA(3).compute(df)
        # ewm(span=3, adjust=False): e0=2, e1=2*0.5+4*0.5=3, e2=3*0.5+6*0.5=4.5
        assert out["ema_3"].iloc[-1] == pytest.approx(4.5)

    def test_rsi_extremes(self):
        up = pd.DataFrame({"open": 0, "high": 0, "low": 0,
                           "close": np.linspace(1, 2, 30)})
        out = RSI(14).compute(up)
        assert out["rsi_14"].iloc[-1] > 99.0  # pure uptrend → RSI ≈ 100
        down = pd.DataFrame({"open": 0, "high": 0, "low": 0,
                             "close": np.linspace(2, 1, 30)})
        out2 = RSI(14).compute(down)
        assert out2["rsi_14"].iloc[-1] < 1.0
