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
#:
#: RE-PINNED 2026-09-30 for the four XAUUSD documents (the fifth,
#: rsi_band_mean_reversion, runs on EURUSD and did not move). Reason: XAUUSD's
#: ``size_step``/``min_size`` went from 0.01 to 0.1 oz because OANDA, the only
#: broker, accepts gold in 0.1-oz increments (practice instruments endpoint,
#: XAU_USD ``tradeUnitsPrecision`` 1, ``minimumTradeSize`` "0.1", fixture
#: tests/fixtures/oanda/practice_instruments_2026-09-30.json). Sizes now round
#: down to 0.1 oz (ichimoku_kumo_trend's flat run, first three trades: 89.13 ->
#: 89.1, 59.33 -> 59.3, 38.69 -> 38.6 oz), so every monetary column moved. Trade
#: counts, rejection counts, ``signals_seen`` and allocation-decision counts are
#: unchanged for all five, and with the step set back to 0.01 all five old
#: hashes reproduce exactly, which is what shows the step is the only cause.
_PINS: dict[str, tuple[str, str, str, int, dict[str, int], int, int]] = {
    "donchian_breakout_atr": (
        "8224d20fa882556f378fd811dc68b8abbaa9ab0a27738195a224cc463e480d80",
        "39e841e3a26da2fa7f82e220739d47a62f0a90431bc2411e0b19f19bc187a25a",
        "1ba4c1c18761a9759f6588faa1ac049731db1acd589478c3451a284a29715d8a",
        106,
        {"allocation_dropped": 284, "cooldown": 2},
        392,
        392,
    ),
    "fib_golden_pocket_pullback": (
        "3aa814f82618200999160e540895297f140172a3039cf2bf7409a69bf7406b5d",
        "80155aff42bdc67dce84c1dceb6f7c3cd4e3654411fe89833c04ae3bba2dd13e",
        "6f7a58b1d3a0176ea5db97cb36461863a68602168e746e58b8c1e55bcb55d7c8",
        32,
        {"allocation_dropped": 24, "cooldown": 4},
        60,
        60,
    ),
    "ichimoku_kumo_trend": (
        "298ccf7674b69bcc45e2a6b159c11de1d4158d16bafc7599f02b1b67cce731a0",
        "48003fe57eb705bd4fa945c657ab72ac3314026be3785208aa21cb0e42ed33c3",
        "1750d35d28d1794e4f1250c8b68ff51fc13a89e0b82e2a7730be8b00761587ec",
        12,
        {},
        12,
        12,
    ),
    "macd_ema_trend_hybrid": (
        "04030a759881f079c233c51ed41b0c0d3993a0deb2ab65727bc4f579f28ee088",
        "8b19685fb78a90840bb977eea5b2917820443c9f32105b5d41e4745a0ef4069b",
        "30d83947b015300eeccb137a738ce199bf99a6f9e2bf1014a38cea0ae9578e59",
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
        "b8c809062ae39448230b25043f0f28624190de94f454ac40e97af1f5a21a1d09"
    )
    assert flat.allocation_ledger == [] and flat.trade_allocation_index == []
    assert "allocation" not in flat.ledger_frame().columns
