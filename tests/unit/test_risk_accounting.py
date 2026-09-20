"""The four gateway inputs that used to be zeros, and the V1 defect they inherit.

``RiskGateway`` runs eighteen named checks and names all eighteen on every
decision. Four of them read fields nothing populated: ``daily_pnl``,
``weekly_pnl``, ``correlated_exposure`` and ``realised_portfolio_vol``. Each
defaulted to ``0.0``, so the check ran, appeared in the audit trail, and could
not fire. An operator reading that trail concludes the daily stop held.

The most important test in this file is
``test_the_day_boundary_is_derived_from_the_clock_not_from_a_job``. V1 kept a
running daily counter and reset it inside a 21:00 summary block, so a worker
that was down at 21:00 never reset it: the daily stop either latched on
yesterday's losses or, after a restart cleared the counter, never fired at all.
:class:`RealisedPnlLedger` has no counter and no reset -- the boundary is a
function of ``now`` -- so the failure is structurally impossible rather than
defended against. These tests pin that.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason, Provenance
from fiboki.portfolio.construction import CorrelationMatrix
from fiboki.risk.accounting import (
    RealisedPnlLedger,
    correlated_exposure,
    correlation_from_frames,
    realised_portfolio_vol,
    utc_day_start,
    utc_week_start,
)
from tests.exec_fixtures import make_snapshot, synthetic_frame


def trade(
    *,
    exit_time: pd.Timestamp,
    net: float,
    strategy_id: str = "s1",
    instrument: str = "EURUSD",
) -> Trade:
    return Trade(
        instrument=instrument,
        direction=Direction.LONG,
        size=10_000.0,
        entry_price=1.10,
        exit_price=1.10,
        entry_time=exit_time - pd.Timedelta(hours=1),
        exit_time=exit_time,
        exit_reason=ExitReason.STOP_LOSS,
        gross_pnl=net,
        spread_cost=0.0,
        commission=0.0,
        slippage_cost=0.0,
        financing_cost=0.0,
        net_pnl=net,
        account_ccy="GBP",
        strategy_id=strategy_id,
        provenance=Provenance.PAPER,
    )


# ==========================================================================
# Boundaries
# ==========================================================================


def test_the_day_starts_at_midnight_utc() -> None:
    now = pd.Timestamp("2024-06-05 23:59:59", tz="UTC")
    assert utc_day_start(now) == pd.Timestamp("2024-06-05 00:00", tz="UTC")


def test_the_week_starts_on_monday_utc() -> None:
    # 2024-06-05 is a Wednesday.
    now = pd.Timestamp("2024-06-05 12:00", tz="UTC")
    assert utc_week_start(now) == pd.Timestamp("2024-06-03 00:00", tz="UTC")
    monday = pd.Timestamp("2024-06-03 00:00", tz="UTC")
    assert utc_week_start(monday) == monday
    sunday = pd.Timestamp("2024-06-09 23:00", tz="UTC")
    assert utc_week_start(sunday) == pd.Timestamp("2024-06-03 00:00", tz="UTC")


def test_a_naive_timestamp_is_refused_rather_than_localised() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        utc_day_start(pd.Timestamp("2024-06-05 12:00"))


# ==========================================================================
# THE DEFECT
# ==========================================================================


def test_the_day_boundary_is_derived_from_the_clock_not_from_a_job() -> None:
    """V1's daily counter reset inside a 21:00 summary block. This one cannot.

    The ledger is queried twice over the SAME trade list, with no reset called,
    no job run and no method invoked in between -- only the clock moving past
    midnight. The daily number must fall away by itself.
    """
    losses = [
        trade(exit_time=pd.Timestamp("2024-06-05 09:00", tz="UTC"), net=-1_200.0),
        trade(exit_time=pd.Timestamp("2024-06-05 22:30", tz="UTC"), net=-800.0),
    ]
    ledger = RealisedPnlLedger(lambda: losses)

    during = ledger.windows(pd.Timestamp("2024-06-05 23:30", tz="UTC"))
    assert during.daily_pnl == pytest.approx(-2_000.0)
    assert during.daily_trades == 2

    after = ledger.windows(pd.Timestamp("2024-06-06 00:00:01", tz="UTC"))
    assert after.daily_pnl == 0.0, (
        "the day did not reset. Nothing was called between the two queries but "
        "the clock -- which is the entire point: V1's reset was a side effect of "
        "a 21:00 job, so a worker that was down at 21:00 never reset it."
    )
    assert after.daily_trades == 0
    # ... and the WEEK has not reset, because 6 June is still the same week.
    assert after.weekly_pnl == pytest.approx(-2_000.0)


def test_a_restart_cannot_clear_the_day_because_there_is_nothing_to_clear() -> None:
    """The other half of the V1 defect: a restart zeroed the accumulator."""
    losses = [trade(exit_time=pd.Timestamp("2024-06-05 09:00", tz="UTC"), net=-1_500.0)]
    now = pd.Timestamp("2024-06-05 18:00", tz="UTC")
    first = RealisedPnlLedger(lambda: losses).windows(now)
    # A brand-new ledger object: exactly what a restarted worker builds.
    after_restart = RealisedPnlLedger(lambda: losses).windows(now)
    assert after_restart.daily_pnl == first.daily_pnl == pytest.approx(-1_500.0)


def test_the_week_crosses_on_monday_with_no_job_having_run() -> None:
    losses = [trade(exit_time=pd.Timestamp("2024-06-07 15:00", tz="UTC"), net=-3_000.0)]
    ledger = RealisedPnlLedger(lambda: losses)
    friday = ledger.windows(pd.Timestamp("2024-06-07 23:00", tz="UTC"))
    assert friday.weekly_pnl == pytest.approx(-3_000.0)
    monday = ledger.windows(pd.Timestamp("2024-06-10 00:00:01", tz="UTC"))
    assert monday.weekly_pnl == 0.0
    assert monday.week_start == pd.Timestamp("2024-06-10 00:00", tz="UTC")


def test_the_ledger_reads_its_source_rather_than_a_snapshot_of_it() -> None:
    """A ledger that has to be TOLD about a trade will one day not be told."""
    rows: list[Trade] = []
    ledger = RealisedPnlLedger(lambda: rows)
    now = pd.Timestamp("2024-06-05 12:00", tz="UTC")
    assert ledger.windows(now).daily_pnl == 0.0
    rows.append(trade(exit_time=pd.Timestamp("2024-06-05 11:00", tz="UTC"), net=-500.0))
    assert ledger.windows(now).daily_pnl == pytest.approx(-500.0)


def test_a_trade_closing_in_the_future_is_not_counted() -> None:
    """A replay or clock defect must not let tomorrow's loss block today."""
    rows = [trade(exit_time=pd.Timestamp("2024-06-06 09:00", tz="UTC"), net=-9_000.0)]
    windows = RealisedPnlLedger(lambda: rows).windows(
        pd.Timestamp("2024-06-05 12:00", tz="UTC")
    )
    assert windows.daily_pnl == 0.0
    assert windows.weekly_pnl == 0.0


def test_a_naive_trade_exit_time_is_refused_loudly() -> None:
    bad = trade(exit_time=pd.Timestamp("2024-06-05 09:00", tz="UTC"), net=-1.0)
    object.__setattr__(bad, "exit_time", pd.Timestamp("2024-06-05 09:00"))
    with pytest.raises(ValueError, match="naive exit_time"):
        RealisedPnlLedger(lambda: [bad]).windows(
            pd.Timestamp("2024-06-05 12:00", tz="UTC")
        )


def test_windows_are_realised_by_default_and_say_so() -> None:
    rows = [trade(exit_time=pd.Timestamp("2024-06-05 09:00", tz="UTC"), net=-100.0)]
    plain = RealisedPnlLedger(lambda: rows, unrealised=lambda: -5_000.0)
    now = pd.Timestamp("2024-06-05 12:00", tz="UTC")
    assert plain.windows(now).daily_pnl == pytest.approx(-100.0)
    assert plain.windows(now).includes_unrealised is False

    marked = RealisedPnlLedger(
        lambda: rows, unrealised=lambda: -5_000.0, include_unrealised=True
    )
    marked_windows = marked.windows(now)
    assert marked_windows.daily_pnl == pytest.approx(-5_100.0)
    assert marked_windows.includes_unrealised is True
    assert marked_windows.unrealised == pytest.approx(-5_000.0)


def test_the_ledger_can_be_restricted_to_named_strategies() -> None:
    rows = [
        trade(exit_time=pd.Timestamp("2024-06-05 09:00", tz="UTC"), net=-100.0,
              strategy_id="a"),
        trade(exit_time=pd.Timestamp("2024-06-05 10:00", tz="UTC"), net=-900.0,
              strategy_id="b"),
    ]
    now = pd.Timestamp("2024-06-05 12:00", tz="UTC")
    assert RealisedPnlLedger(lambda: rows).windows(now).daily_pnl == pytest.approx(-1000.0)
    scoped = RealisedPnlLedger(lambda: rows, strategy_ids=frozenset({"a"}))
    assert scoped.windows(now).daily_pnl == pytest.approx(-100.0)


def test_the_window_reports_its_own_boundaries() -> None:
    """'The stop did not fire' and 'it measured the wrong day' look identical."""
    windows = RealisedPnlLedger(lambda: []).windows(
        pd.Timestamp("2024-06-05 12:00", tz="UTC")
    )
    row = windows.as_row()
    assert row["day_start"].startswith("2024-06-05T00:00")
    assert row["week_start"].startswith("2024-06-03T00:00")


# ==========================================================================
# Correlated exposure
# ==========================================================================


def test_correlated_exposure_sums_the_notional_above_the_threshold() -> None:
    matrix = CorrelationMatrix.from_mapping(
        {
            ("EURUSD", "GBPUSD"): 0.85,
            ("EURUSD", "USDJPY"): -0.10,
            ("GBPUSD", "USDJPY"): -0.05,
        },
        default=0.30,
    )
    snapshot = make_snapshot(
        instrument_exposure={"EURUSD": 50_000.0, "GBPUSD": 30_000.0, "USDJPY": 20_000.0},
        instrument_correlation=matrix,
    )
    total = correlated_exposure("EURUSD", snapshot=snapshot, threshold=0.60)
    assert total == pytest.approx(80_000.0), (
        "EURUSD's own 50k plus GBPUSD's correlated 30k; USDJPY is below threshold"
    )


def test_the_instruments_own_exposure_counts_towards_its_correlated_exposure() -> None:
    """rho(x, x) == 1. A second position in the same instrument is the most
    correlated exposure available and excluding it would blind the check."""
    snapshot = make_snapshot(instrument_exposure={"EURUSD": 40_000.0})
    assert correlated_exposure(
        "EURUSD", snapshot=snapshot, threshold=0.60
    ) == pytest.approx(40_000.0)


def test_a_short_does_not_net_against_a_long_for_this_limit() -> None:
    """Both positions still have to be got out of through the same gap."""
    snapshot = make_snapshot(
        instrument_exposure={"EURUSD": 50_000.0, "GBPUSD": -40_000.0},
        instrument_correlation=CorrelationMatrix.from_mapping(
            {("EURUSD", "GBPUSD"): 0.90}
        ),
    )
    assert correlated_exposure(
        "EURUSD", snapshot=snapshot, threshold=0.60
    ) == pytest.approx(90_000.0)


def test_an_unmeasured_pair_uses_the_matrixs_positive_default() -> None:
    """'We have not measured it' must not resolve to 'it is uncorrelated'."""
    snapshot = make_snapshot(
        instrument_exposure={"EURUSD": 10_000.0, "XAUUSD": 25_000.0},
        instrument_correlation=CorrelationMatrix(default=0.70),
    )
    assert correlated_exposure(
        "EURUSD", snapshot=snapshot, threshold=0.60
    ) == pytest.approx(35_000.0)


def test_correlation_is_measured_from_the_frames_being_traded() -> None:
    base = synthetic_frame(n=400, seed=7)
    same = base.copy()
    opposite = base.copy()
    opposite[["open", "high", "low", "close"]] = (
        2.2 - base[["open", "high", "low", "close"]]
    )
    matrix = correlation_from_frames(
        {"EURUSD": base, "GBPUSD": same, "USDCHF": opposite}
    )
    assert matrix.get("EURUSD", "GBPUSD") == pytest.approx(1.0, abs=1e-9)
    # Not exactly -1: a mirrored PRICE series does not give mirrored simple
    # RETURNS, because the denominators differ. Near enough is the honest bar.
    assert matrix.get("EURUSD", "USDCHF") == pytest.approx(-1.0, abs=1e-3)
    assert matrix.get("EURUSD", "EURUSD") == 1.0


def test_a_pair_with_too_little_overlap_stays_unmeasured() -> None:
    short = synthetic_frame(n=8, seed=3)
    other = synthetic_frame(n=8, seed=4)
    matrix = correlation_from_frames(
        {"EURUSD": short, "GBPUSD": other}, min_observations=30, default=0.42
    )
    assert matrix.get("EURUSD", "GBPUSD") == pytest.approx(0.42)


# ==========================================================================
# Realised portfolio volatility
# ==========================================================================


def test_realised_vol_is_annualised_from_the_observed_spacing() -> None:
    index = pd.date_range("2024-01-01", periods=300, freq="D", tz="UTC")
    rng = np.random.default_rng(11)
    returns = rng.normal(0, 0.01, len(index))
    equity = pd.Series(100_000.0 * np.cumprod(1 + returns), index=index)
    vol = realised_portfolio_vol(equity)
    # 1% daily, 365 calendar days a year -> roughly 19%.
    assert 0.15 < vol < 0.24, vol


def test_an_hourly_curve_is_not_annualised_as_if_it_were_daily() -> None:
    """A wrong annualisation factor is a wrong risk scalar, not a rounding error."""
    rng = np.random.default_rng(5)
    hourly_index = pd.date_range("2024-01-01", periods=2000, freq="h", tz="UTC")
    returns = rng.normal(0, 0.001, len(hourly_index))
    hourly = pd.Series(100_000.0 * np.cumprod(1 + returns), index=hourly_index)
    daily_index = pd.date_range("2024-01-01", periods=2000, freq="D", tz="UTC")
    daily = pd.Series(hourly.to_numpy(), index=daily_index)
    assert realised_portfolio_vol(hourly) > realised_portfolio_vol(daily) * 4


def test_too_few_observations_reads_unmeasured_rather_than_noise() -> None:
    index = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")
    equity = pd.Series([100.0, 120.0, 90.0, 140.0, 80.0, 160.0], index=index)
    assert realised_portfolio_vol(equity) == 0.0, (
        "a vol estimated from five returns is noise, and feeding it to a scaling "
        "rule would move real size for no reason"
    )


def test_a_curve_with_no_timestamps_refuses_to_guess_a_factor() -> None:
    assert realised_portfolio_vol([100.0 + i for i in range(100)]) == 0.0


def test_a_flat_curve_has_no_volatility() -> None:
    index = pd.date_range("2024-01-01", periods=100, freq="D", tz="UTC")
    assert realised_portfolio_vol(pd.Series([100.0] * 100, index=index)) == 0.0


def test_an_empty_curve_is_unmeasured_not_an_error() -> None:
    assert realised_portfolio_vol([]) == 0.0
    assert realised_portfolio_vol(pd.Series(dtype=float)) == 0.0
