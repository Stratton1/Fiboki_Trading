"""The four dead risk inputs, end to end, and the daily stop actually blocking.

``RiskGateway`` names nineteen checks on every decision. Four of them --
``daily_loss``, ``weekly_loss``, ``max_correlated_exposure`` and the volatility
targeting that reads ``PortfolioSnapshot.realised_portfolio_vol`` -- had no
production data source. They ran, they were named in the audit trail, and they
read ``0.0``. A daily stop that reads zero cannot fire, and an audit trail that
says it was evaluated is worse than one that says nothing, because a reader
concludes the limit held.

This file asserts the three things that matter:

1. a session assembled by :func:`build_replay_session` carries REAL values for
   all four, and says on every context which of them are real;
2. a breached daily limit BLOCKS new entries and still PERMITS exits -- a loss
   limit that stops you getting flat is a trap, not a control;
3. the day boundary is derived from the clock, so it resets with no job having
   run. V1's did not.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.core.contracts import Signal, Trade
from fiboki.core.enums import (
    Direction,
    ExecutionMode,
    ExitReason,
    Provenance,
    StrategyLifecycle,
)
from fiboki.core.money import IdentityFxSource
from fiboki.portfolio.construction import CorrelationMatrix
from fiboki.portfolio.sizing import SizingPolicy
from fiboki.risk.accounting import RealisedPnlLedger
from fiboki.risk.gateway import ExitContext, MarketView, RiskGateway, VenueView
from fiboki.risk.killswitch import RequestKind
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.profiles import IG_REALISTIC
from fiboki.workers.base import WorkerStore
from fiboki.workers.runtime import RiskContextBuilder, build_replay_session
from tests.exec_fixtures import (
    healthy_market,
    healthy_venue,
    make_plan,
    make_signal,
    make_snapshot,
    synthetic_frame,
)

SYMBOL = "EURUSD"
OTHER = "GBPUSD"


@pytest.fixture()
def store(tmp_path) -> WorkerStore:
    with WorkerStore.sqlite_at(tmp_path / "workers.sqlite") as handle:
        yield handle


def pnl_trade(*, exit_time: pd.Timestamp, net: float) -> Trade:
    return Trade(
        instrument=SYMBOL,
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
        account_ccy="USD",
        strategy_id="losing_strategy",
        provenance=Provenance.PAPER,
    )


# ==========================================================================
# 1. The builder carries real values
# ==========================================================================


def _signal_source(symbol, history, now):
    if len(history) != 40:
        return ()
    price = float(history["close"].iloc[-1])
    return (
        Signal(
            strategy_id="runtime_strategy",
            instrument=symbol,
            timeframe="H1",
            direction=Direction.LONG,
            bar_time=now,
            reference_price=price,
            stop_price=price - 0.0030,
            take_profit_prices=(price + 0.0060,),
        ),
    )


def _session(store: WorkerStore, **kwargs):
    frames = {
        SYMBOL: synthetic_frame(n=120, seed=7),
        OTHER: synthetic_frame(n=120, seed=11),
    }
    return build_replay_session(
        frames=frames,
        signal_source=_signal_source,
        store=store,
        paper_config=PaperConfig(
            initial_balance=100_000.0,
            account_ccy="USD",
            profile=IG_REALISTIC,
            strategy_id="runtime_strategy",
        ),
        sizing_policy=SizingPolicy(risk_fraction=0.01),
        timeframe="H1",
        **kwargs,
    )


def test_a_replay_session_wires_all_four_inputs(store) -> None:
    session = _session(store)
    live = session.context_builder.risk_inputs_live()
    assert live == {
        "daily_pnl": True,
        "weekly_pnl": True,
        "correlated_exposure": True,
        "realised_portfolio_vol": True,
    }, (
        "build_replay_session left a gateway input without a data source. Each "
        "of these feeds a NAMED check that runs either way; an unwired one is a "
        "check that cannot fire while appearing in the audit trail."
    )


def test_the_correlation_matrix_is_measured_from_the_replayed_frames(store) -> None:
    session = _session(store)
    matrix = session.context_builder.correlation
    assert isinstance(matrix, CorrelationMatrix)
    assert set(matrix.labels) == {SYMBOL, OTHER}
    rho = matrix.get(SYMBOL, OTHER)
    assert -1.0 <= rho <= 1.0
    assert rho != 0.0, "an exactly-zero correlation is the suspicious default"


def test_every_context_states_which_inputs_are_real(store) -> None:
    """An audit that says ``daily_loss`` ran must also say against what."""
    session = _session(store)
    plan = make_plan(signal=make_signal(instrument=SYMBOL))
    session.feed.poll()
    context = session.context_builder(plan)
    assert context.extra["risk_inputs_live"]["daily_pnl"] is True
    assert "pnl_window" in context.extra
    assert context.extra["pnl_window"]["day_start"].endswith("+00:00")
    assert "realised_portfolio_vol" in context.extra


def test_realised_daily_pnl_reaches_the_context_from_closed_trades(store) -> None:
    session = _session(store)
    for _ in range(60):
        session.worker.run_cycle()
    now = session.feed.now
    assert now is not None

    # Whatever the replay produced, the ledger must agree with the book.
    windows = session.context_builder.windows(now)
    assert windows is not None
    expected = sum(
        t.net_pnl
        for t in session.broker.book.trades
        if t.exit_time >= now.normalize() and t.exit_time <= now
    )
    assert windows.daily_pnl == pytest.approx(expected)

    plan = make_plan(signal=make_signal(instrument=SYMBOL))
    context = session.context_builder(plan)
    assert context.daily_pnl == pytest.approx(windows.daily_pnl)
    assert context.weekly_pnl == pytest.approx(windows.weekly_pnl)


def test_correlated_exposure_is_non_zero_once_a_position_is_open(store) -> None:
    session = _session(store)
    for _ in range(60):
        session.worker.run_cycle()
        if session.broker.book.open:
            break
    assert session.broker.book.open, "no position opened; the test would prove nothing"
    plan = make_plan(signal=make_signal(instrument=SYMBOL))
    context = session.context_builder(plan)
    assert context.correlated_exposure > 0.0, (
        "the max_correlated_exposure check is still reading a zero with an open "
        "position on the books"
    )


def test_realised_portfolio_vol_is_measured_once_the_curve_is_long_enough(store) -> None:
    session = _session(store)
    for _ in range(119):
        session.worker.run_cycle()
    vol = session.context_builder.realised_vol()
    assert vol > 0.0, "the equity curve produced no volatility estimate"
    snapshot = session.context_builder.snapshot(session.feed.now)
    assert snapshot.realised_portfolio_vol == pytest.approx(vol)


def test_the_session_summary_reports_the_values_not_just_that_checks_ran(store) -> None:
    session = _session(store)
    for _ in range(60):
        session.worker.run_cycle()
    summary = session.summary()
    assert summary["risk_inputs_live"]["correlated_exposure"] is True
    assert summary["daily_pnl"] is not None
    assert summary["weekly_pnl"] is not None
    assert summary["realised_portfolio_vol"] >= 0.0


def test_without_a_ledger_the_builder_falls_back_and_says_so() -> None:
    """Backwards compatible, and honest about it."""
    broker = PaperBroker(
        config=PaperConfig(initial_balance=10_000.0), fx=IdentityFxSource()
    )
    builder = RiskContextBuilder(
        adapter=broker,
        clock=lambda: pd.Timestamp("2024-06-05 12:00", tz="UTC"),
        fx=IdentityFxSource(),
        account_ccy="USD",
        daily_pnl=-123.0,
    )
    assert builder.risk_inputs_live() == {
        "daily_pnl": False,
        "weekly_pnl": False,
        "correlated_exposure": False,
        "realised_portfolio_vol": False,
    }
    plan = make_plan(signal=make_signal(instrument=SYMBOL))
    context = builder(plan)
    assert context.daily_pnl == -123.0
    assert context.correlated_exposure == 0.0


# ==========================================================================
# 2. A breached daily limit blocks ENTRIES and permits EXITS
# ==========================================================================


def _gateway() -> RiskGateway:
    return RiskGateway(limits=PAPER_LIMITS)


def _breaching_builder(*, equity: float, loss: float, now: pd.Timestamp):
    """A builder whose ledger holds a loss large enough to breach the limit."""
    broker = PaperBroker(
        config=PaperConfig(initial_balance=equity, account_ccy="USD"),
        fx=IdentityFxSource(),
    )
    losses = [pnl_trade(exit_time=now - pd.Timedelta(hours=2), net=loss)]
    builder = RiskContextBuilder(
        adapter=broker,
        clock=lambda: now,
        fx=IdentityFxSource(),
        account_ccy="USD",
        limits=PAPER_LIMITS,
        pnl_ledger=RealisedPnlLedger(lambda: losses),
        correlation=CorrelationMatrix(default=0.3),
        # A quoted spread, injected. The builder's docstring is explicit that a
        # live deployment must supply one rather than reuse the fill model's,
        # and the gateway blocks on an unknown spread -- so a test that did not
        # supply one would be asserting against ``spread_unknown`` rather than
        # against the loss limit it is about.
        spread_source=lambda instrument, now: 0.00012,
    )
    builder.last_bar_times[SYMBOL] = now - pd.Timedelta(seconds=30)
    return builder, broker


def test_a_breached_daily_limit_blocks_a_new_entry() -> None:
    now = pd.Timestamp("2024-06-05 18:00", tz="UTC")
    equity = 100_000.0
    # PAPER_LIMITS keeps the default 3% daily loss limit.
    builder, _broker = _breaching_builder(equity=equity, loss=-3_500.0, now=now)
    context = builder(make_plan(signal=make_signal(instrument=SYMBOL), equity=equity))
    assert context.daily_pnl == pytest.approx(-3_500.0)

    decision = _gateway().evaluate(context)
    assert not decision.allowed
    assert any(r.startswith("daily_loss") for r in decision.reasons), decision.reasons
    assert "daily_loss" in decision.checks_run


def test_the_same_session_under_the_limit_is_allowed() -> None:
    """The control must be the LOSS and not the plumbing around it."""
    now = pd.Timestamp("2024-06-05 18:00", tz="UTC")
    equity = 100_000.0
    builder, _broker = _breaching_builder(equity=equity, loss=-500.0, now=now)
    context = builder(make_plan(signal=make_signal(instrument=SYMBOL), equity=equity))
    decision = _gateway().evaluate(context)
    assert decision.allowed, decision.reasons


def test_a_breached_daily_limit_still_permits_an_exit() -> None:
    """A loss limit that stops you getting flat is a trap, not a control."""
    now = pd.Timestamp("2024-06-05 18:00", tz="UTC")
    gateway = _gateway()
    builder, _broker = _breaching_builder(equity=100_000.0, loss=-3_500.0, now=now)
    blocked = gateway.evaluate(
        builder(make_plan(signal=make_signal(instrument=SYMBOL), equity=100_000.0))
    )
    assert not blocked.allowed

    exit_decision = gateway.evaluate_exit(
        ExitContext(
            instrument=SYMBOL,
            strategy_id="losing_strategy",
            size=10_000.0,
            now=now,
            mode=ExecutionMode.PAPER,
            position_id="pos_1",
            limits=PAPER_LIMITS,
            market=healthy_market(now=now, instrument=SYMBOL),
            venue=healthy_venue(),
            request_kind=RequestKind.CLOSE,
        )
    )
    assert exit_decision.allowed, exit_decision.reasons
    assert "daily_loss" not in exit_decision.checks_run, (
        "the exit check set must not include the loss limits; running them would "
        "refuse to let the book out precisely when the book is over its limit"
    )


def test_the_block_survives_until_the_clock_crosses_midnight() -> None:
    """And then lifts BY ITSELF, with no reset job having run.

    This is the V1 defect stated as a gateway outcome rather than as a ledger
    number: the worker was down at 21:00, the counter never reset, and the
    daily stop was either stuck on or -- after a restart cleared it -- stuck
    off. Here there is no counter, so the block lifts when the day does.
    """
    equity = 100_000.0
    evening = pd.Timestamp("2024-06-05 23:30", tz="UTC")
    losses = [pnl_trade(exit_time=pd.Timestamp("2024-06-05 09:00", tz="UTC"),
                        net=-3_500.0)]
    broker = PaperBroker(
        config=PaperConfig(initial_balance=equity, account_ccy="USD"),
        fx=IdentityFxSource(),
    )
    clock = {"now": evening}
    builder = RiskContextBuilder(
        adapter=broker,
        clock=lambda: clock["now"],
        fx=IdentityFxSource(),
        account_ccy="USD",
        limits=PAPER_LIMITS,
        pnl_ledger=RealisedPnlLedger(lambda: losses),
        correlation=CorrelationMatrix(default=0.3),
        # A quoted spread, injected. The builder's docstring is explicit that a
        # live deployment must supply one rather than reuse the fill model's,
        # and the gateway blocks on an unknown spread -- so a test that did not
        # supply one would be asserting against ``spread_unknown`` rather than
        # against the loss limit it is about.
        spread_source=lambda instrument, now: 0.00012,
    )
    builder.last_bar_times[SYMBOL] = evening - pd.Timedelta(seconds=30)
    gateway = _gateway()

    plan = make_plan(signal=make_signal(instrument=SYMBOL), equity=equity)
    before = gateway.evaluate(builder(plan))
    assert not before.allowed
    assert any(r.startswith("daily_loss") for r in before.reasons)

    # The clock crosses. NOTHING else happens: no reset, no job, no restart.
    clock["now"] = pd.Timestamp("2024-06-06 00:00:01", tz="UTC")
    builder.last_bar_times[SYMBOL] = clock["now"] - pd.Timedelta(seconds=30)
    after = gateway.evaluate(
        builder(make_plan(signal=make_signal(instrument=SYMBOL,
                                             bar_time=clock["now"]), equity=equity))
    )
    assert after.allowed, after.reasons
    # ... and the WEEKLY limit is untouched by the day rolling over.
    assert builder.last_windows is not None
    assert builder.last_windows.weekly_pnl == pytest.approx(-3_500.0)


def test_a_breached_weekly_limit_blocks_even_on_a_clean_day() -> None:
    equity = 100_000.0
    now = pd.Timestamp("2024-06-07 10:00", tz="UTC")  # Friday
    losses = [
        pnl_trade(exit_time=pd.Timestamp("2024-06-03 10:00", tz="UTC"), net=-2_500.0),
        pnl_trade(exit_time=pd.Timestamp("2024-06-04 10:00", tz="UTC"), net=-2_500.0),
        pnl_trade(exit_time=pd.Timestamp("2024-06-05 10:00", tz="UTC"), net=-2_000.0),
    ]
    broker = PaperBroker(
        config=PaperConfig(initial_balance=equity, account_ccy="USD"),
        fx=IdentityFxSource(),
    )
    builder = RiskContextBuilder(
        adapter=broker,
        clock=lambda: now,
        fx=IdentityFxSource(),
        account_ccy="USD",
        limits=PAPER_LIMITS,
        pnl_ledger=RealisedPnlLedger(lambda: losses),
        correlation=CorrelationMatrix(default=0.3),
        # A quoted spread, injected. The builder's docstring is explicit that a
        # live deployment must supply one rather than reuse the fill model's,
        # and the gateway blocks on an unknown spread -- so a test that did not
        # supply one would be asserting against ``spread_unknown`` rather than
        # against the loss limit it is about.
        spread_source=lambda instrument, now: 0.00012,
    )
    builder.last_bar_times[SYMBOL] = now - pd.Timedelta(seconds=30)
    context = builder(make_plan(signal=make_signal(instrument=SYMBOL), equity=equity))
    assert context.daily_pnl == 0.0, "today is clean"
    assert context.weekly_pnl == pytest.approx(-7_000.0)

    decision = _gateway().evaluate(context)
    assert not decision.allowed
    assert any(r.startswith("weekly_loss") for r in decision.reasons), decision.reasons
    assert not any(r.startswith("daily_loss") for r in decision.reasons)


def test_correlated_exposure_can_actually_breach_its_limit() -> None:
    """The check was unfireable at zero; prove it fires at a real number."""
    equity = 100_000.0
    now = pd.Timestamp("2024-06-05 12:00", tz="UTC")
    limits = PAPER_LIMITS.derive(
        "limits_test_correlated", max_correlated_exposure_pct=50.0
    )
    snapshot = make_snapshot(
        equity=equity,
        as_of=now,
        instrument_exposure={SYMBOL: 400_000.0, OTHER: 300_000.0},
        instrument_correlation=CorrelationMatrix.from_mapping(
            {(SYMBOL, OTHER): 0.9}, default=0.3
        ),
    )
    from fiboki.risk.accounting import correlated_exposure

    total = correlated_exposure(SYMBOL, snapshot=snapshot, threshold=0.6)
    assert total == pytest.approx(700_000.0)

    from fiboki.risk.gateway import RiskContext

    context = RiskContext(
        plan=make_plan(signal=make_signal(instrument=SYMBOL), equity=equity),
        snapshot=snapshot,
        now=now,
        mode=ExecutionMode.PAPER,
        limits=limits,
        market=healthy_market(now=now, instrument=SYMBOL),
        venue=healthy_venue(),
        strategy=__import__(
            "fiboki.risk.gateway", fromlist=["StrategyView"]
        ).StrategyView(lifecycle=StrategyLifecycle.PAPER),
        correlated_exposure=total,
    )
    decision = RiskGateway(limits=limits).evaluate(context)
    assert not decision.allowed
    assert any(
        r.startswith("max_correlated_exposure") for r in decision.reasons
    ), decision.reasons


def test_a_zero_correlated_exposure_cannot_breach_the_default_limit() -> None:
    """Why the unwired field mattered: at zero the check was decorative."""
    equity = 100_000.0
    now = pd.Timestamp("2024-06-05 12:00", tz="UTC")
    from fiboki.risk.gateway import RiskContext, StrategyView

    context = RiskContext(
        plan=make_plan(signal=make_signal(instrument=SYMBOL), equity=equity),
        snapshot=make_snapshot(
            equity=equity,
            as_of=now,
            instrument_exposure={SYMBOL: 400_000.0, OTHER: 300_000.0},
        ),
        now=now,
        mode=ExecutionMode.PAPER,
        limits=PAPER_LIMITS,
        market=healthy_market(now=now, instrument=SYMBOL),
        venue=healthy_venue(),
        strategy=StrategyView(lifecycle=StrategyLifecycle.PAPER),
        correlated_exposure=0.0,
    )
    decision = RiskGateway(limits=PAPER_LIMITS).evaluate(context)
    assert "max_correlated_exposure" in decision.checks_run
    assert not any(
        r.startswith("max_correlated_exposure") for r in decision.reasons
    ), (
        "with the field at zero the check ran and could not fire -- which is "
        "exactly the state the wiring exists to end"
    )


# ==========================================================================
# 3. Volatility targeting was silently disabled
# ==========================================================================


def test_volatility_targeting_now_has_a_number_to_target_against(store) -> None:
    from fiboki.portfolio.construction import ConstructionConfig

    session = _session(store)
    for _ in range(119):
        session.worker.run_cycle()
    snapshot = session.context_builder.snapshot(session.feed.now)
    config = ConstructionConfig(target_portfolio_vol=0.12)
    assert config.target_portfolio_vol is not None
    assert snapshot.realised_portfolio_vol > 0.0, (
        "portfolio/construction.py treats an unmeasured vol as NEUTRAL, so vol "
        "targeting was configured, unit-tested and silently disabled on every "
        "live path"
    )


def test_market_view_and_venue_view_still_carry_data_after_the_change(store) -> None:
    """Regression guard: the original purpose of this builder still holds."""
    session = _session(store)
    for _ in range(45):
        session.worker.run_cycle()
    plan = make_plan(signal=make_signal(instrument=SYMBOL))
    context = session.context_builder(plan)
    assert isinstance(context.market, MarketView)
    assert context.market.last_bar_time is not None
    assert context.market.spread_price is not None
    assert isinstance(context.venue, VenueView)
    assert context.venue.connected is True


# ==========================================================================
# 4. managed_exit_exposure reaches the metric registry
# ==========================================================================


def test_managed_exit_exposure_is_published_as_a_metric() -> None:
    """The number has to leave the process, or nobody can page on it.

    ``broker`` sits BELOW ``obs`` in the dependency order that
    ``tests/unit/test_layering.py`` enforces, so the manager computes the number
    and ``workers`` publishes it. This asserts the hand-off exists.
    """
    from fiboki.broker.position_manager import ManagedExitExposure
    from fiboki.obs.metrics import render_prometheus
    from fiboki.workers.runtime import publish_managed_exit_exposure

    exposure = ManagedExitExposure(
        at=pd.Timestamp("2024-06-05 12:00", tz="UTC"),
        amount=1_234.5,
        positions=3,
        exposed_positions=2,
        account_ccy="GBP",
    )
    published = publish_managed_exit_exposure(exposure, venue="oanda")
    assert published == pytest.approx(1_234.5)

    text = render_prometheus()
    assert "fiboki_managed_exit_exposure" in text
    assert 'venue="oanda"' in text
    assert "fiboki_managed_exit_positions" in text


def test_the_broker_package_does_not_reach_up_into_obs_or_workers() -> None:
    """The position manager takes its alert and telemetry sinks as Protocols.

    Stated here as well as in the layering test, because the temptation to
    ``import fiboki.obs.alerts`` from the manager is real and the failure it
    would cause -- a circular import at worker start -- is a long way from the
    line that caused it.
    """
    import ast
    from pathlib import Path

    source = Path("src/fiboki/broker/position_manager.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "fiboki":
                imported.add(parts[1])
    assert not (imported & {"obs", "workers", "api", "agents"}), imported
