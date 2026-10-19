"""Re-run robustness diagnostics (research/robustness/rerun.py): determinism,
the look-ahead detector on a leaky and a clean toy, the warm-up report, the
stressed profiles, and the holdout fence every re-run diagnostic sits behind.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fiboki.agents.tools import InMemoryBarSource
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction, Timeframe
from fiboki.research.robustness.engine import BindingExecutor, BindingMismatch, RunBinding
from fiboki.research.robustness.job import run_diagnostic
from fiboki.research.robustness.rerun import (
    RUNNERS,
    lookahead_scan,
    noisy_frame,
    options_of,
)
from fiboki.sim.profiles import IG_REALISTIC, get_profile
from fiboki.sim.stress_profiles import stressed_profile
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.holdout import HoldoutLeak
from tests.agents_fixtures import gbp_fx_frames, trending_bars

REPO = Path(__file__).resolve().parents[2]
STRATEGY = "ichimoku_kumo_trend"


def _doc(strategy: str = STRATEGY) -> StrategyDocument:
    return StrategyDocument.from_json((REPO / "research" / "strategies" / f"{strategy}.json").read_text())


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return trending_bars(instrument="EURUSD", timeframe=Timeframe.H4, periods=1600, seed=11, drift=0.0002)


class SpyBars(InMemoryBarSource):
    """Records every ``end`` asked for and the last bar handed back."""

    def __init__(self, frames):
        super().__init__(frames)
        self.calls: list[tuple[str | None, pd.Timestamp]] = []

    def load(self, instrument, timeframe, *, start=None, end=None):
        out, version = super().load(instrument, timeframe, start=start, end=end)
        self.calls.append((end, out.index[-1]))
        return out, version


def _binding(frame: pd.DataFrame, *, start_at: int = 300, boundary_at: int = 1280,
             end: pd.Timestamp | None = None, strategy: str = STRATEGY,
             params: dict | None = None) -> RunBinding:
    doc = _doc(strategy)
    params = {"tenkan_period": 9, "kijun_period": 26} if params is None else params
    bound = doc.bind({**doc.default_values(), **params})
    b = frame.index[boundary_at]
    return RunBinding(
        run_id="bt_test", strategy_id=strategy, instrument="EURUSD", timeframe="H4",
        window_start=frame.index[start_at].isoformat(), window_end=(end or b).isoformat(),
        initial_balance=10_000.0, risk_fraction=0.01, account_ccy="USD", profile_name="IG_REALISTIC",
        parameters=params, construction="none", sizing_policy="fixed_fractional_v2",
        content_hash=bound.content_hash(), ledger_sha256="", dataset_version_id="inmem",
        run_last=frame.index[-1].isoformat(),
        boundary={"start": b.isoformat(), "basis": "provisional", "dataset_version_ids": ["inmem"], "detail": ""},
    )


def _executor(frame, **kw) -> tuple[BindingExecutor, SpyBars]:
    bars = SpyBars({("EURUSD", "H4"): frame, **gbp_fx_frames()})
    return BindingExecutor(_binding(frame, **kw), _doc(), bars), bars


SMALL = {
    "warmup": {"extra_bars": [80]},
    "lookahead": {"samples": 15},
    "execution": {"skip_levels": [0.1], "skip_seeds": 2, "delays": [1], "multipliers": [1.0, 2.0]},
    "perturbation": {"n": 4},
    "random": {"n_runs": 40},
    "noise": {"levels": [0.1], "n": 2},
}


# --------------------------------------------------------------- determinism


@pytest.mark.parametrize("kind", list(RUNNERS))
def test_every_rerun_diagnostic_is_deterministic(frame, kind):
    a = run_diagnostic(kind, _binding(frame), SMALL[kind], template=_doc(),
                       bars=InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()}))
    b = run_diagnostic(kind, _binding(frame), SMALL[kind], template=_doc(),
                       bars=InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()}))
    assert a == b


@pytest.mark.parametrize("kind", ["execution", "perturbation", "random", "noise"])
def test_a_different_seed_draws_differently(frame, kind):
    bars = InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()})
    a = run_diagnostic(kind, _binding(frame), {**SMALL[kind], "seed": 1}, template=_doc(), bars=bars)
    b = run_diagnostic(kind, _binding(frame), {**SMALL[kind], "seed": 2}, template=_doc(), bars=bars)
    assert a["output"] != b["output"]


# --------------------------------------------------------------- holdout


@pytest.mark.parametrize("kind", list(RUNNERS))
def test_no_rerun_diagnostic_reads_a_bar_at_or_after_the_boundary(frame, kind):
    ex, bars = _executor(frame)
    RUNNERS[kind](ex, **options_of(kind, SMALL[kind]))
    boundary = ex.boundary.start
    assert bars.calls, "the executor must load its bars through the source"
    for end, last in bars.calls:
        assert last < boundary
        assert pd.Timestamp(end, tz="UTC") < boundary
    assert ex.history.index[-1] < boundary


def test_a_window_reaching_the_holdout_is_refused(frame):
    with pytest.raises(HoldoutLeak):
        _executor(frame, end=frame.index[1400])


def test_a_binding_that_is_not_the_stored_strategy_is_refused(frame):
    b = _binding(frame)
    wrong = RunBinding.from_payload({**b.to_payload(), "parameters": {"tenkan_period": 7, "kijun_period": 26}})
    with pytest.raises(BindingMismatch):
        BindingExecutor(wrong, _doc(), InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()}))


# --------------------------------------------------------------- look-ahead


class _Toy:
    """A minimal SignalModel: LONG when ``feature`` is above the close."""

    warmup_period = 5

    def __init__(self, leaky: bool) -> None:
        self.leaky = leaky

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        # The leak: tomorrow's close, read today. The clean twin reads yesterday's.
        out["feature"] = df["close"].shift(-1 if self.leaky else 1)
        return out

    def generate_signal(self, df, idx, *, instrument, timeframe):
        f, c = df["feature"].iloc[idx], df["close"].iloc[idx]
        if not np.isfinite(f) or f <= c:
            return None
        return Signal(strategy_id="toy", instrument=instrument, timeframe="H4", direction=Direction.LONG,
                      bar_time=df.index[idx], reference_price=float(c), stop_price=float(c) * 0.99)


def test_the_lookahead_detector_flags_a_leaky_strategy(frame):
    out = lookahead_scan(_Toy(leaky=True), frame.iloc[:400], instrument="EURUSD", timeframe=Timeframe.H4,
                         samples=30, seed=3)
    assert out["biased"] is True
    assert out["flags"] and all("feature" in f["columns_changed"] for f in out["flags"])
    assert out["signal_flags"] > 0


def test_the_lookahead_detector_clears_a_clean_strategy(frame):
    out = lookahead_scan(_Toy(leaky=False), frame.iloc[:400], instrument="EURUSD", timeframe=Timeframe.H4,
                         samples=30, seed=3)
    assert out["biased"] is False and out["flags"] == []


def test_the_registered_strategy_is_causal(frame):
    ex, _ = _executor(frame)
    out = RUNNERS["lookahead"](ex, samples=25, seed=9)
    assert out["biased"] is False, out["flags"][:3]


# --------------------------------------------------------------- warm-up


def test_warmup_reports_signals_trades_and_indicator_drift(frame):
    ex, _ = _executor(frame)
    out = RUNNERS["warmup"](ex, extra_bars=[80, 10_000])
    assert out["warmup_period"] > 0
    first, late = out["runs"]
    assert (first["mode"], first["extra_bars"]) == ("extended", 80)
    # More history than exists before the window: the short run starts later instead.
    assert late["mode"] == "late_start" and 0 < late["extra_bars"] <= 10_000
    for r in out["runs"]:
        assert r["trades_matched"] + r["trades_only_short"] == r["trades_short"]
        assert r["indicator_columns"] and all(c["max_abs_diff"] >= 0 for c in r["indicator_columns"])
        assert r["stable"] == (r["signals_changed_after_warmup"] == 0 and r["trades_changed"] == 0
                               and r["trades_only_short"] == 0 and r["trades_only_long"] == 0)


def test_warmup_compares_a_recursive_indicator_for_real(frame):
    """Donchian's chandelier trails on Wilder's ATR, a recursive average. With 200
    more bars of history its value really does differ (the runs saw different
    data), and by the end of the declared warm-up the difference is below the
    report's 1e-9 relative tolerance: the declared warm-up is long enough."""
    strategy = "donchian_breakout_atr"
    bars = InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()})
    ex = BindingExecutor(_binding(frame, strategy=strategy, params={}), _doc(strategy), bars)
    out = RUNNERS["warmup"](ex, extra_bars=[200])
    (run,) = out["runs"]
    (atr,) = [c for c in run["indicator_columns"] if c["column"].startswith("atr")]
    assert atr["max_abs_diff"] > 0.0
    assert atr["bars_differing"] == 0 and run["signals_changed_after_warmup"] == 0


# --------------------------------------------------------------- stress profiles


def test_stressed_profiles_scale_costs_and_are_named():
    p = stressed_profile(IG_REALISTIC, spread_x=2.0, slippage_x=3.0)
    assert p.name.startswith("IG_REALISTIC~") and p.name != IG_REALISTIC.name
    instr = __import__("fiboki.core.instruments", fromlist=["get"]).get("EURUSD")
    assert p.spread_price(instr, 12, 1.1) == pytest.approx(2.0 * IG_REALISTIC.spread_price(instr, 12, 1.1))
    assert p.expected_slippage_price(instr) == pytest.approx(3.0 * IG_REALISTIC.expected_slippage_price(instr))
    assert stressed_profile(IG_REALISTIC) is IG_REALISTIC
    d = stressed_profile(IG_REALISTIC, latency_bars=2, rejection_probability=0.2, seed=5)
    assert (d.latency_bars, d.rejection_probability, d.seed) == (2, 0.2, 5)
    assert d.fingerprint() != IG_REALISTIC.fingerprint()
    assert get_profile("IG_REALISTIC") is IG_REALISTIC  # the registry is untouched


def test_execution_stress_runs_through_the_engine(frame):
    ex, _ = _executor(frame)
    out = RUNNERS["execution"](ex, skip_levels=[0.5], skip_seeds=2, delays=[2], multipliers=[1.0, 3.0])
    skip = next(r for r in out["rows"] if r["stress"] == "trade_skip")
    base = out["baseline"]
    assert skip["n_trades"]["p50"] < base["metrics"]["n_trades"]  # broker rejects took trades away
    cell = next(c for c in out["grid"] if c["spread_x"] == 3.0 and c["slippage_x"] == 3.0)
    assert cell["metrics"]["total_costs"] / max(cell["metrics"]["n_trades"], 1) > \
        base["metrics"]["total_costs"] / max(base["metrics"]["n_trades"], 1)


# --------------------------------------------------------------- noise


def test_noisy_bars_stay_valid_and_are_seeded(frame):
    a = noisy_frame(frame, atr_fraction=0.5, rng=np.random.default_rng(1))
    b = noisy_frame(frame, atr_fraction=0.5, rng=np.random.default_rng(1))
    pd.testing.assert_frame_equal(a, b)
    assert (a["high"] >= a[["open", "close"]].max(axis=1)).all()
    assert (a["low"] <= a[["open", "close"]].min(axis=1)).all()
    assert (a[["open", "high", "low", "close"]] > 0).all().all()
    assert not np.allclose(a["close"], frame["close"])
