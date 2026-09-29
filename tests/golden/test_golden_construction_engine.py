"""Golden pins: the five seed documents under the DEFAULT construction policy.

``tests/integration/test_locks_regression_pin.py`` pins the same five documents
on the same bars under ``construction=None`` (the flat ``risk_fraction``
path), and those pins did not move. These are their counterparts under
:func:`fiboki.validation.engine_evaluator.research_construction_policy`, the
policy every validation run now uses by default (the paper runtime's:
construction_v2, equal risk, PROBATIONARY, PAPER, health 1.0, regime unknown,
no conviction, realised vol measured on the engine's equity curve).

What moved and what did not, stated so a reader can check it: the trade
COUNTS and ``signals_seen`` equal the flat pins for all five documents (the
entries and exits are the same trades), and every monetary column moved
because every size did. The book's ``max_concurrent`` refusals of the flat
run become ``allocation_dropped``: a signal on the instrument already held
reads rho 1.0 at ``instrument_correlation`` (hard cap 0.85) and is refused
before it is sized, where the flat run sized it and let the book refuse it.

One trade, by hand (``rsi_band_mean_reversion``, its first trade, EURUSD on
bars scaled x1000, account USD so every rate is 1.0)
------------------------------------------------------------------------------
Decision bar 2021-03-29 12:00 UTC; the book is flat; equity 100,000.00.

    tier PROBATIONARY               base risk 0.25% (ceiling: sizer 1% -> 1.00%,
                                    min(0.25, 1.00) = 0.25)
    lifecycle, health, confidence   x 1.0 each
    no open book                    correlation, concentration, currency,
                                    margin, correlated budget x 1.0
    volatility target               x 1.0 (no_realised_vol: a flat equity curve)
    drawdown 0.00%                  x 1.0
    regime unknown (no source)      x 0.6
    conviction (none admissible)    x 1.0 (missing)
    weight = 0.6; risk = 0.25% x 0.6 = 0.15% of equity

    risk budget   = 100,000 x 0.0025 x 0.6                  = 150.00 USD
    stop distance = 1341.531721310911 - 1332.4274891454777  = 9.10423216543336
    stop-out cost = spread + 2 E[slippage] (IG_REALISTIC)    = 0.000138
    per unit      = 9.10423216543336 + 0.000138             = 9.104370165433359
    size          = floor(150.00 / 9.104370165433359)       = floor(16.4756) = 16
    leverage cap  = 100,000 x 30 / 1341.53                  = 2236 (not binding)

The flat run sized the same signal at 1% of equity: floor(1,000 /
9.104370165433359) = 109 units. The position stopped out: gross -145.67
(16 units rounded DOWN from 16.4756, so 16 x 9.1044 rather than the full 150),
net -149.06 after 0.004 of spread and slippage and 3.39 of financing, which
sizing excludes by design (it depends on the holding period). 0.149% of equity
against an intended 0.15%.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.backtest.engine import (
    DEFAULT_SIZING_COST_PROFILE,
    BacktestConfig,
    BacktestResult,
    FixedFractionalSizer,
    run_backtest,
    stop_out_cost_per_unit,
)
from fiboki.backtest.exits import exit_policy_from_document
from fiboki.core.enums import Provenance
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import research_construction_policy

pytestmark = pytest.mark.golden

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "research" / "strategies"


class _Runner:
    """Compile once, ask for a signal on each bar; keep what was emitted.

    Restated from ``test_locks_regression_pin.py`` rather than imported, so the
    two sets of pins cannot drift through a shared helper.
    """

    def __init__(self, document: StrategyDocument, symbol: str, frame: pd.DataFrame) -> None:
        self.compiled = compile_strategy(document)
        self.symbol = symbol
        self.prepared = self.compiled.prepare(frame)
        self.emitted: dict[pd.Timestamp, object] = {}

    def on_bar(self, ctx):
        j = ctx._cursor.get(self.symbol, -1)
        if j < 0:
            return ()
        signal = self.compiled.generate_signal(self.prepared, j, self.symbol, "H4")
        if signal is None:
            return ()
        self.emitted[signal.bar_time] = signal
        return (signal,)


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(3200, with_volume=False) * 1000.0


def _run(strategy_id: str, bars: pd.DataFrame, *, construction=True):
    doc = StrategyDocument.from_json((SEEDS / f"{strategy_id}.json").read_text()).bind_defaults()
    symbol = "XAUUSD" if "XAUUSD" in doc.universe else doc.universe[0]
    runner = _Runner(doc, symbol, bars)
    policy = exit_policy_from_document(doc)
    series = (
        {symbol: runner.prepared[list(policy.needs_series)]} if policy.needs_series else None
    )
    config = BacktestConfig(
        initial_balance=100_000.0,
        account_ccy=get_instrument(symbol).quote,
        max_concurrent=doc.position_management.max_concurrent_positions,
        max_per_instrument=doc.position_management.max_concurrent_positions,
        strategy_id="pin",
        provenance=Provenance.BACKTEST,
        construction=research_construction_policy() if construction else None,
    )
    result = run_backtest(
        data={symbol: bars[["open", "high", "low", "close"]]},
        config=config,
        strategy=runner,
        sizer=FixedFractionalSizer(risk_fraction=0.01),
        fx=IdentityFxSource(),
        exit_policy=policy,
        exit_series=series,
    )
    return result, runner, symbol


#: ``(ledger_sha256, leg_ledger_sha256, allocation_ledger_sha256, n_trades,
#: rejections, signals_seen, allocation_decisions)`` under the default policy.
#: Trade counts and signals_seen equal ``test_locks_regression_pin._LEDGER``.
_PINS: dict[str, tuple[str, str, str, int, dict[str, int], int, int]] = {
    "donchian_breakout_atr": (
        "4b8862a98555814d8724b518ffc6c5e985e2e3714d52e8095bab3fd9744410ab",
        "20dec9a42fdcf232b6330b6c511aec8eeac24cdad6731af5ea7754dc42dbb229",
        "f9dab1cebe69ba0d0742bb9b619a2e8751ca6a7012b938d231abe9d7d09f759d",
        106,
        {"allocation_dropped": 284, "cooldown": 2},
        392,
        392,
    ),
    "fib_golden_pocket_pullback": (
        "6f50f038e707a41d8949c49ed0c1cf4dba6cc3644acb85068b4fed53a756ec81",
        "30f7df77f45479037646b52b3a5ce7252bce16a1ef056b97866d29d2956a4463",
        "913106f76230d0ef1005f24c05dd955f2236d03591b04ff98464539ae0215a8b",
        32,
        {"allocation_dropped": 24, "cooldown": 4},
        60,
        60,
    ),
    "ichimoku_kumo_trend": (
        "53c1fdcef18012c7327cb3e93e78223cd39a66f70d375216a7d280c309ccedf0",
        "5c635a9cb44f9521d0f1ceee33ff2d59f182869cfbee259fc04d3ff77f495042",
        "a9c359476608f4f84a92ad8f8ac05b945808efbac2b9950dec485a67459660ff",
        12,
        {},
        12,
        12,
    ),
    "macd_ema_trend_hybrid": (
        "4ec6f4efb73f109ff4cee84ae58c951a76f18bbe69461e01c277072f23e51eea",
        "c25ce08d60d84ad853bb8d54cdea36290fb148c7a527e52d3e5ee47b6ba287e9",
        "f257d67d62668102c6b0795b39f80381e6352a125b3ddfea4657c50efead0acd",
        5,
        {},
        5,
        5,
    ),
    "rsi_band_mean_reversion": (
        "5e6a1555a8e4a43124df60b45dcba7df585ab5652bb125358de1e715b4a8046e",
        "060a3935aa824ecbcfaa745a53e9b8aeff741313d321499178a95f03b9bdcc70",
        "90d334871c14df44974d48da641a71a7947c83e540d6b5127180e64fc9a9b042",
        16,
        {},
        16,
        16,
    ),
}


@pytest.mark.parametrize("strategy_id", sorted(_PINS))
def test_the_default_policy_ledgers_are_pinned(strategy_id: str, bars: pd.DataFrame) -> None:
    result, _runner, _symbol = _run(strategy_id, bars)
    ledger, legs, allocations, n_trades, rejections, signals_seen, decisions = _PINS[strategy_id]
    assert result.ledger_sha256() == ledger
    assert result.leg_ledger_sha256() == legs
    assert result.allocation_ledger_sha256() == allocations
    assert len(result.trades) == n_trades
    assert result.rejections == rejections
    assert result.signals_seen == signals_seen
    assert len(result.allocation_ledger) == decisions
    fp = result.config_fingerprint["construction"]
    assert fp["construction_version"] == "construction_v2"
    assert fp["conviction_source"].startswith("none")
    assert all(x is not None for x in result.trade_allocations())


def test_one_trade_by_hand(bars: pd.DataFrame) -> None:
    """The arithmetic in the module docstring, recomputed from the run's own inputs."""
    result, runner, symbol = _run("rsi_band_mean_reversion", bars)
    row = result.allocation_ledger[result.trade_allocation_index[0]]
    trade = result.trades[0]
    signal = runner.emitted[pd.Timestamp(row["bar_time"])]
    instrument = get_instrument(symbol)

    assert row["bar_time"] == "2021-03-29T12:00:00+00:00"
    assert (row["tier"], row["base_risk_pct"], row["weight"]) == ("probationary", 0.25, 0.6)
    assert row["risk_pct"] == pytest.approx(0.15)
    factors = {step: factor for step, factor, _detail in row["trace"]}
    assert factors.pop("regime") == 0.6
    assert set(factors.values()) == {1.0}, factors
    details = {step: detail for step, _f, detail in row["trace"]}
    assert details["regime"] == "regime=unknown"
    assert details["conviction"].startswith("missing would_be=1.0000 applied=1.0000")

    equity = float(result.equity_curve.loc[pd.Timestamp(row["bar_time"]), "equity"])
    assert equity == 100_000.0
    budget = equity * 0.0025 * 0.6
    assert budget == pytest.approx(150.0)
    cost = stop_out_cost_per_unit(DEFAULT_SIZING_COST_PROFILE, instrument, signal)
    assert cost == pytest.approx(0.000138)
    per_unit = signal.stop_distance + cost
    assert per_unit == pytest.approx(9.104370165433359, rel=1e-12)
    assert budget / per_unit == pytest.approx(16.4756, abs=1e-4)
    assert row["size"] == trade.size == 16.0

    flat, _r, _s = _run("rsi_band_mean_reversion", bars, construction=False)
    assert flat.trades[0].entry_time == trade.entry_time
    assert flat.trades[0].size == float(int(1_000.0 / per_unit)) == 109.0
    assert trade.gross_pnl == pytest.approx(-145.67, abs=0.01)
    assert trade.net_pnl == pytest.approx(-149.06, abs=0.01)


def test_the_flat_pins_are_the_none_path(bars: pd.DataFrame) -> None:
    """``construction=None`` is the path ``test_locks_regression_pin`` pins."""
    flat: BacktestResult
    flat, _r, _s = _run("ichimoku_kumo_trend", bars, construction=False)
    assert flat.config_fingerprint["construction"] == "none"
    assert flat.ledger_sha256() == (
        "93243358e5127fb8c106763771805dcfa608f9f68fcc1f9a719d22e3f91e2b20"
    )
    assert flat.allocation_ledger == [] and flat.trade_allocation_index == []
    assert "allocation" not in flat.ledger_frame().columns
