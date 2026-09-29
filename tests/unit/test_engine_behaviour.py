"""Engine behaviour: mark-to-market equity, concurrency, financing, ruin.

These are the structural properties V1 lacked, each stated as an executable
assertion rather than a claim in a docstring.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.backtest.engine import (
    SIZING_POLICY_V1,
    BacktestConfig,
    BacktestEngine,
    FixedFractionalSizer,
    FixedSizeSizer,
    PrecomputedSignals,
    _nights_between,
    run_backtest,
)
from fiboki.core.contracts import AccountState, Signal
from fiboki.core.enums import Direction, ExitReason
from fiboki.core.instruments import get as get_instrument
from tests.helpers_exec import ConstantFx, flat_profile, make_frame

FX = ConstantFx({("USD", "GBP"): 0.80})


def _signal(ts, price, stop, tp, direction=Direction.LONG, instrument="EURUSD"):
    return Signal(
        strategy_id="beh", instrument=instrument, timeframe="H4",
        direction=direction, bar_time=pd.Timestamp(ts, tz="UTC"),
        reference_price=price, stop_price=stop, take_profit_prices=(tp,),
    )


def _run(data, signals, *, size=10_000, **cfg_kw):
    cfg = BacktestConfig(
        initial_balance=cfg_kw.pop("initial_balance", 100_000.0),
        account_ccy="GBP",
        profile=cfg_kw.pop("profile", flat_profile()),
        strategy_id="beh",
        **cfg_kw,
    )
    return run_backtest(
        data=data, config=cfg, strategy=PrecomputedSignals(signals),
        sizer=FixedSizeSizer(size), fx=FX,
    )


# ==========================================================================
# Mark to market
# ==========================================================================


def test_equity_marks_open_positions_every_bar():
    """
    THE V1 BUG, at engine level. A long entered at 1.1000 drops to 1.0500
    before recovering to 1.1200 and closing at target.

    Realised-only equity (V1) would be flat at 100,000 until the close, then
    step up: reported max drawdown 0.

    Mark-to-market equity must show the dip:
      at the 1.0500 bar, unrealised = (1.0500 - 1.1000) * 10,000 * 0.80 = -400
      equity = 100,000 - 400 = 99,600
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 08:00", 1.0990, 1.1000, 1.0500, 1.0500),
        ("2024-01-02 12:00", 1.0500, 1.0800, 1.0500, 1.0800),
        ("2024-01-02 16:00", 1.0800, 1.1250, 1.0800, 1.1200),
        ("2024-01-02 20:00", 1.1200, 1.1210, 1.1190, 1.1200),
    ])
    res = _run(
        {"EURUSD": frame},
        [_signal("2024-01-02 00:00", 1.1000, 1.0000, 1.1200)],
    )
    eq = res.equity_curve["equity"]
    assert eq.loc["2024-01-02 08:00+00:00"] == pytest.approx(99_600.0, abs=1e-9)
    assert float(eq.min()) == pytest.approx(99_600.0, abs=1e-9)
    assert res.equity_curve["drawdown"].min() == pytest.approx(-400.0, abs=1e-9)

    # And the trade itself was a WINNER: drawdown is invisible in the ledger.
    assert len(res.trades) == 1
    assert res.trades[0].net_pnl > 0
    assert res.trades[0].exit_reason is ExitReason.TAKE_PROFIT


def test_exposure_is_recorded_per_bar():
    """
    Exposure = |price * size * contract_size * fx| in the account currency.
    At the 1.1000 close with 10,000 units: 1.1000 * 10,000 * 0.80 = 8,800 GBP.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 08:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 12:00", 1.1000, 1.1600, 1.0990, 1.1500),
    ])
    res = _run(
        {"EURUSD": frame},
        [_signal("2024-01-02 00:00", 1.1000, 1.0500, 1.1500)],
    )
    exposure = res.equity_curve["exposure"]
    assert exposure.loc["2024-01-02 00:00+00:00"] == pytest.approx(0.0, abs=1e-12)
    assert exposure.loc["2024-01-02 04:00+00:00"] == pytest.approx(8_800.0, abs=1e-9)
    assert res.equity_curve["open_positions"].max() == 1


# ==========================================================================
# Concurrency
# ==========================================================================


def test_multiple_concurrent_positions_are_actually_held():
    """V1 structurally held one position, so portfolio effects were invisible."""
    idx = pd.date_range("2024-01-02", periods=12, freq="4h", tz="UTC")
    eur = pd.DataFrame(
        {"open": 1.10, "high": 1.11, "low": 1.09, "close": 1.10}, index=idx
    )
    xau = pd.DataFrame(
        {"open": 2000.0, "high": 2010.0, "low": 1990.0, "close": 2000.0}, index=idx
    )
    eur.index.name = xau.index.name = "timestamp"

    signals = [
        _signal("2024-01-02 00:00", 1.10, 1.00, 1.50, instrument="EURUSD"),
        _signal("2024-01-02 00:00", 2000.0, 1800.0, 2500.0, instrument="XAUUSD"),
    ]
    res = _run(
        {"EURUSD": eur, "XAUUSD": xau}, signals, size=10,
        max_concurrent=2, max_per_instrument=1,
    )
    assert int(res.equity_curve["open_positions"].max()) == 2
    assert {t.instrument for t in res.trades} == {"EURUSD", "XAUUSD"}


def test_max_concurrent_blocks_the_third_position_and_records_why():
    idx = pd.date_range("2024-01-02", periods=12, freq="4h", tz="UTC")
    frames = {}
    for sym, px in (("EURUSD", 1.10), ("XAUUSD", 2000.0), ("GBPUSD", 1.27)):
        f = pd.DataFrame({"open": px, "high": px * 1.01, "low": px * 0.99, "close": px}, index=idx)
        f.index.name = "timestamp"
        frames[sym] = f

    signals = [
        _signal("2024-01-02 00:00", 1.10, 1.00, 1.50, instrument="EURUSD"),
        _signal("2024-01-02 00:00", 2000.0, 1800.0, 2500.0, instrument="XAUUSD"),
        _signal("2024-01-02 00:00", 1.27, 1.10, 1.60, instrument="GBPUSD"),
    ]
    res = _run(frames, signals, size=10, max_concurrent=2)
    assert int(res.equity_curve["open_positions"].max()) == 2
    assert res.rejections.get("max_concurrent", 0) >= 1


def test_max_per_instrument_blocks_a_second_position_in_the_same_market():
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 08:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 12:00", 1.1000, 1.1010, 1.0990, 1.1000),
    ])
    signals = [
        _signal("2024-01-02 00:00", 1.1000, 1.0000, 1.5000),
        _signal("2024-01-02 04:00", 1.1000, 1.0000, 1.5000),
    ]
    res = _run({"EURUSD": frame}, signals, max_concurrent=5, max_per_instrument=1)
    assert int(res.equity_curve["open_positions"].max()) == 1
    assert res.rejections.get("max_per_instrument", 0) >= 1


# ==========================================================================
# Financing arithmetic
# ==========================================================================


@pytest.mark.parametrize(
    ("last", "now", "expected"),
    [
        # Rollover is 21:00 UTC. Half-open interval (last, now].
        ("2024-01-02 00:00", "2024-01-02 20:00", 0),   # 21:00 not yet reached
        ("2024-01-02 00:00", "2024-01-02 21:00", 1),   # exactly on the boundary
        ("2024-01-02 00:00", "2024-01-03 00:00", 1),
        ("2024-01-02 00:00", "2024-01-05 00:00", 3),   # 02, 03 and 04 at 21:00
        ("2024-01-02 22:00", "2024-01-03 20:00", 0),   # already past 02's rollover
        ("2024-01-02 22:00", "2024-01-03 21:00", 1),
        ("2024-01-02 00:00", "2024-01-02 00:00", 0),   # no elapsed time
    ],
)
def test_nights_between_counts_rollover_crossings(last, now, expected):
    assert _nights_between(
        pd.Timestamp(last, tz="UTC"), pd.Timestamp(now, tz="UTC"), 21
    ) == expected


def test_financing_is_not_charged_when_disabled():
    rows = []
    ts = pd.Timestamp("2024-01-01 20:00", tz="UTC")
    end = pd.Timestamp("2024-01-05 00:00", tz="UTC")
    while ts < end:
        rows.append((ts.strftime("%Y-%m-%d %H:%M"), 1.1000, 1.1000, 1.1000, 1.1000))
        ts += pd.Timedelta(hours=4)
    rows.append((end.strftime("%Y-%m-%d %H:%M"), 1.1000, 1.1050, 1.1000, 1.1000))
    frame = make_frame(rows)

    from fiboki.sim.profiles import FinancingModel

    profile = flat_profile(financing=FinancingModel(annual_bps_long=365.0))
    res = _run(
        {"EURUSD": frame},
        [_signal("2024-01-01 20:00", 1.1000, 1.0950, 1.1050)],
        profile=profile, charge_financing=False,
    )
    assert res.trades[0].financing_cost == pytest.approx(0.0, abs=1e-12)


# ==========================================================================
# Ruin
# ==========================================================================


def test_bankruptcy_guard_stops_the_run_and_closes_positions():
    """
    Balance 1,000 GBP, 100,000 EURUSD long at 1.1000 with a stop far away at
    0.5000. The price falls to 1.0000:
      unrealised = (1.0000 - 1.1000) * 100,000 * 0.80 = -8,000 GBP
      equity     = 1,000 - 8,000                      = -7,000 GBP  <= 0

    The engine must halt, close the position as MARGIN_CALL, and stop writing
    equity points. A backtest that trades on past zero is reporting a fantasy.
    """
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 08:00", 1.0990, 1.1000, 1.0000, 1.0000),
        ("2024-01-02 12:00", 1.0000, 1.0010, 0.9990, 1.0000),
        ("2024-01-02 16:00", 1.0000, 1.0010, 0.9990, 1.0000),
    ])
    res = _run(
        {"EURUSD": frame},
        [_signal("2024-01-02 00:00", 1.1000, 0.5000, 2.0000)],
        size=100_000, initial_balance=1_000.0,
    )
    assert res.bankrupt is True
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason is ExitReason.MARGIN_CALL
    # The run stopped at the bar that broke it, not at the end of the data.
    assert res.equity_curve.index[-1] == pd.Timestamp("2024-01-02 08:00", tz="UTC")
    assert float(res.equity_curve["equity"].iloc[-1]) <= 0.0


def test_a_position_open_at_the_end_of_data_is_closed_and_labelled():
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 08:00", 1.1000, 1.1010, 1.0990, 1.1000),
    ])
    res = _run({"EURUSD": frame}, [_signal("2024-01-02 00:00", 1.1000, 1.0000, 2.0000)])
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason is ExitReason.END_OF_DATA
    assert res.equity_curve["open_positions"].iloc[-1] == 0


# ==========================================================================
# Sizing
# ==========================================================================


def test_fixed_fractional_sizer_risks_the_intended_fraction():
    """
    Equity 100,000 GBP, risk 1% = 1,000 GBP.
    EURUSD stop distance 0.0050, contract_size 1.0, USD->GBP 0.80.

      risk per unit = 0.0050 * 1.0 * 0.80 = 0.0040 GBP
      size          = 1,000 / 0.0040      = 250,000 units

    Leverage check: notional per unit = 1.1000 * 1.0 * 0.80 = 0.88 GBP,
    cap = 100,000 * 30 / 0.88 = 3,409,090.9 units -- not binding here.

    This is the ``fixed_fractional_v1`` rule (bare stop distance), selected
    explicitly since engine_v3_realism made v2 the default. The v2 arithmetic,
    with the costs in the risk per unit, is pinned in
    ``tests/golden/test_golden_sizing_v2.py``.
    """
    sizer = FixedFractionalSizer(risk_fraction=0.01, policy_id=SIZING_POLICY_V1)
    instrument = get_instrument("EURUSD")
    account = AccountState(balance=100_000.0, equity=100_000.0, currency="GBP")
    sig = _signal("2024-01-02 00:00", 1.1000, 1.0950, 1.1200)
    size = sizer.size_for(sig, instrument, account, fx_quote_to_account=0.80)
    assert size == pytest.approx(250_000.0, abs=1e-6)


def test_fixed_fractional_sizer_is_capped_by_retail_leverage():
    """
    Risking 50% of equity would call for 12,500,000 units. FCA retail leverage
    on an FX major is 30:1, so the cap is

      100,000 * 30 / (1.1000 * 1.0 * 0.80) = 3,000,000 / 0.88 = 3,409,090.909...

    rounded DOWN to the 1-unit step = 3,409,090 units. Rounding down is the
    rule everywhere in V2: never round up into more risk.
    """
    sizer = FixedFractionalSizer(risk_fraction=0.5, policy_id=SIZING_POLICY_V1)
    instrument = get_instrument("EURUSD")
    account = AccountState(balance=100_000.0, equity=100_000.0, currency="GBP")
    sig = _signal("2024-01-02 00:00", 1.1000, 1.0950, 1.1200)
    size = sizer.size_for(sig, instrument, account, fx_quote_to_account=0.80)
    assert size == pytest.approx(3_409_090.0, abs=1e-6)


# ==========================================================================
# Input validation
# ==========================================================================


def _valid_frame():
    return make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1010, 1.0990, 1.1000),
    ])


def _engine(data):
    return BacktestEngine(
        data=data,
        config=BacktestConfig(initial_balance=1_000.0, account_ccy="GBP"),
        strategy=PrecomputedSignals([]),
        sizer=FixedSizeSizer(1.0),
        fx=FX,
    )


def test_a_naive_timestamp_index_is_refused():
    frame = _valid_frame()
    frame.index = frame.index.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        _engine({"EURUSD": frame})


def test_an_unsorted_index_is_refused():
    frame = _valid_frame().iloc[::-1]
    with pytest.raises(ValueError, match="sorted ascending"):
        _engine({"EURUSD": frame})


def test_duplicate_timestamps_are_refused():
    frame = _valid_frame()
    frame = pd.concat([frame, frame.iloc[[0]]]).sort_index()
    with pytest.raises(ValueError, match="duplicate"):
        _engine({"EURUSD": frame})


def test_nan_prices_are_refused():
    frame = _valid_frame()
    frame.iloc[0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        _engine({"EURUSD": frame})


def test_an_unregistered_instrument_is_refused():
    with pytest.raises(KeyError, match="Unknown instrument"):
        _engine({"NOTREAL": _valid_frame()})


def test_the_result_carries_its_config_and_data_fingerprints():
    """A result that cannot say what produced it is not a result."""
    res = _run({"EURUSD": _valid_frame()}, [])
    assert res.config_fingerprint["account_ccy"] == "GBP"
    assert res.config_fingerprint["profile"]["name"] == "GOLDEN_FLAT"
    # ``price_basis`` added by engine_v3_realism (P1-3): an unlabelled frame is
    # recorded as an explicit assumption rather than silently taken as mid.
    assert set(res.data_fingerprint["EURUSD"]) == {
        "price_basis", "bars", "first", "last", "sha256"
    }
    assert res.data_fingerprint["EURUSD"]["price_basis"] == "assumed_mid"
    assert res.data_fingerprint["EURUSD"]["bars"] == 2
