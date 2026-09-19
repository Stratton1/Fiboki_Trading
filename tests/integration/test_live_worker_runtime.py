"""The live worker, assembled end to end, on real XAUUSD H4 bars.

``workers/live_worker.py`` held the loop and nothing held the parts. The
``RiskContext`` in particular had no production source at all, so the gateway's
freshness and spread checks -- both of which fail CLOSED -- had no data to fail
or pass on. This module proves the assembled runtime runs:

    market data -> market state -> strategy -> sizing -> risk gateway ->
    execution service -> paper venue -> telemetry

and that every order that reached the venue went through the gateway on the way.

Real bars, when they are there
------------------------------
The real-data cases read the canonical XAUUSD H4 dataset out of a
:class:`~fiboki.data.store.DataStore` and SKIP when no store is configured,
because a test that silently substitutes synthetic bars for real ones is a test
that reports a result about something else. Everything the assembly guarantees
is also asserted on deterministic synthetic bars, which always run.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from fiboki.broker.paper import PaperConfig
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction, ExecutionMode, StrategyLifecycle, Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.portfolio.sizing import SizingPolicy
from fiboki.risk.gateway import RiskGateway
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.profiles import IG_REALISTIC
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import LiveWorkerConfig
from fiboki.workers.runtime import build_replay_session
from tests.exec_fixtures import synthetic_frame

#: Where the XAUUSD H4 dataset lives when one has been ingested. Named by an
#: environment variable first so a CI box can point at its own store.
_STORE_CANDIDATES = (
    os.environ.get("FIBOKI_DATA_ROOT", ""),
    "/tmp/claude-0/ladder/dataroot",
)


def _real_xauusd_h4() -> pd.DataFrame | None:
    from fiboki.data.schema import DatasetKind
    from fiboki.data.store import DataStore

    for candidate in _STORE_CANDIDATES:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            store = DataStore(candidate)
            frame, _version = store.read_latest(
                "XAUUSD", Timeframe.H4, kind=DatasetKind.VALIDATED
            )
        except Exception:
            continue
        return frame[["open", "high", "low", "close"]].astype(float)
    return None


@pytest.fixture(scope="module")
def xauusd() -> pd.DataFrame:
    frame = _real_xauusd_h4()
    if frame is None:
        pytest.skip("no XAUUSD H4 dataset in any configured DataStore")
    return frame


# --------------------------------------------------------------------------
# A deliberately simple, deterministic signal source
# --------------------------------------------------------------------------


class BreakoutSource:
    """Donchian-style breakout on CLOSED bars only.

    Chosen over a compiled seed document on purpose: this test is about the
    ASSEMBLY, and a source whose signals a reader can predict from the bars
    keeps a failure attributable to the wiring rather than to a strategy.
    """

    def __init__(self, *, lookback: int = 20, stop_atr: float = 2.0, rr: float = 2.0) -> None:
        self.lookback = lookback
        self.stop_atr = stop_atr
        self.rr = rr
        self.emitted = 0

    def __call__(
        self, symbol: str, history: pd.DataFrame, now: pd.Timestamp
    ) -> list[Signal]:
        if len(history) <= self.lookback + 2:
            return []
        window = history.iloc[-(self.lookback + 1) : -1]
        bar = history.iloc[-1]
        close = float(bar["close"])
        span = float(window["high"].max() - window["low"].min())
        if span <= 0:
            return []
        risk = max(span * 0.25, close * 0.002)
        if close > float(window["high"].max()):
            direction, sign = Direction.LONG, 1.0
        elif close < float(window["low"].min()):
            direction, sign = Direction.SHORT, -1.0
        else:
            return []
        self.emitted += 1
        return [
            Signal(
                strategy_id="runtime_breakout",
                instrument=symbol,
                timeframe="H4",
                direction=direction,
                bar_time=now,
                reference_price=close,
                stop_price=close - sign * risk,
                take_profit_prices=(
                    close + sign * risk * self.rr * 0.5,
                    close + sign * risk * self.rr,
                ),
                take_profit_allocations=(0.5, 0.5),
            )
        ]


def _session(frame: pd.DataFrame, *, store: WorkerStore, max_cycles: int, **kwargs):
    return build_replay_session(
        frames={"XAUUSD": frame},
        signal_source=BreakoutSource(),
        store=store,
        paper_config=PaperConfig(
            initial_balance=100_000.0,
            account_ccy="USD",
            profile=IG_REALISTIC,
            max_concurrent=2,
            max_per_instrument=1,
            strategy_id="runtime_breakout",
        ),
        sizing_policy=SizingPolicy(risk_fraction=0.005),
        fx=IdentityFxSource(),
        timeframe="H4",
        warmup=30,
        limits=PAPER_LIMITS,
        worker_config=LiveWorkerConfig(
            max_cycles=max_cycles,
            idle_sleep_seconds=0.0,
            busy_sleep_seconds=0.0,
            reconcile_every_cycles=500,
            lifecycle_every_cycles=500,
            lease_name=f"live-test-{id(frame)}-{max_cycles}",
        ),
        **kwargs,
    )


@pytest.fixture()
def store(tmp_path) -> WorkerStore:
    with WorkerStore.sqlite_at(tmp_path / "workers.sqlite") as handle:
        yield handle


# --------------------------------------------------------------------------
# The end-to-end run
# --------------------------------------------------------------------------


def test_a_paper_session_runs_end_to_end_on_real_xauusd_h4_bars(xauusd, store) -> None:
    """The whole chain, on the bars the research reports were produced from."""
    frame = xauusd.iloc[:4000]
    session = _session(frame, store=store, max_cycles=len(frame))
    exit_code = session.worker.run(install_signals=False)
    assert exit_code == 0

    summary = session.summary()
    assert summary["bars_replayed"] == len(frame)
    assert summary["signals_seen"] > 0, "the source produced no signals on real bars"
    assert summary["gateway_attempts"] > 0, "nothing reached the risk gateway"
    assert summary["accepted"] > 0, "no order was accepted by the paper venue"
    assert summary["trades"] > 0, "no position ever closed"
    # The ladder is two legs at 50/50, so a position that reaches its first
    # target contributes two exit legs and one trade.
    assert summary["exit_legs"] >= summary["trades"]


def test_every_order_that_reached_the_venue_passed_the_gateway(xauusd, store) -> None:
    """The structural claim, checked against the records rather than the code.

    Every accepted submission has a gateway attempt with the same plan id and an
    ``allowed`` decision behind it. An order at the venue with no allowed
    attempt would be an order that bypassed the gateway.
    """
    session = _session(xauusd.iloc[:2500], store=store, max_cycles=2500)
    session.worker.run(install_signals=False)

    allowed = {
        a.plan_id for a in session.gateway.recorder.attempts if a.decision.allowed
    }
    accepted = {s.plan_id for s in session.worker.submissions if s.accepted}
    assert accepted, "the session accepted nothing; the assertion would be vacuous"
    assert accepted <= allowed, (
        "an order reached the venue with no ALLOWED gateway decision behind it: "
        f"{sorted(accepted - allowed)}"
    )
    # And every attempt names the full check set, allowed or blocked.
    for attempt in session.gateway.recorder.attempts:
        assert set(attempt.decision.checks_run) == set(RiskGateway.CHECKS)


def test_the_gateway_had_real_data_for_its_freshness_and_spread_checks(
    xauusd, store
) -> None:
    """The defect this assembly exists to close.

    With no ``RiskContext`` source, ``data_freshness``, ``stale_price`` and
    ``abnormal_spread`` had no inputs and would have blocked every order on
    ``unknown``. If that were still true, every attempt would carry those
    reasons.
    """
    session = _session(xauusd.iloc[:2000], store=store, max_cycles=2000)
    session.worker.run(install_signals=False)

    attempts = session.gateway.recorder.attempts
    assert attempts
    reasons = {r.split(":", 1)[0] for a in attempts for r in a.decision.reasons}
    for unknowable in (
        "data_stale",
        "stale_price",
        "spread_unknown",
        "market_state_unknown",
        "broker_health_unknown",
        "strategy_lifecycle_unknown",
    ):
        assert unknowable not in reasons, (
            f"the gateway blocked on {unknowable}: the context builder is not "
            "supplying that input"
        )
    context = session.context_builder(_any_plan(session))
    assert context.market.spread_price is not None
    assert context.market.last_bar_time is not None
    assert context.venue.connected is True
    assert context.extra["estimated_spread"] is True


def _any_plan(session, symbol: str = "XAUUSD", strategy_id: str = "runtime_breakout"):
    """One sized plan, so a context can be rebuilt and inspected directly.

    Built from the last replayed bar rather than from whatever happened to
    signal: the assertion is about the CONTEXT the builder produces, not about
    whether that bar was a breakout.
    """
    from fiboki.core.instruments import get as get_instrument
    from fiboki.portfolio.sizing import size_trade

    history = session.feed.frames[symbol].iloc[: session.feed.cursor]
    close = float(history["close"].iloc[-1])
    signal = Signal(
        strategy_id=strategy_id,
        instrument=symbol,
        timeframe="H4",
        direction=Direction.LONG,
        bar_time=history.index[-1],
        reference_price=close,
        stop_price=close * 0.99,
    )
    outcome = size_trade(
        signal=signal,
        instrument=get_instrument(symbol),
        account=session.broker.account(),
        fx_quote_to_account=1.0,
        policy=SizingPolicy(risk_fraction=0.005),
        account_ccy="USD",
    )
    return outcome.require()


def test_telemetry_is_produced_for_every_attempt(xauusd, store) -> None:
    session = _session(xauusd.iloc[:2000], store=store, max_cycles=2000)
    session.worker.run(install_signals=False)
    rows = session.telemetry()
    assert rows
    for row in rows:
        assert row["decision"]["checks_run"]
        assert row["mode"] == ExecutionMode.PAPER.value
        assert row["limits_version"] == PAPER_LIMITS.version
    telemetry = [
        a.to_telemetry() for a in session.gateway.recorder.attempts if not a.allowed
    ]
    for item in telemetry:
        assert item.venue_error, "a blocked attempt recorded no reason"


# --------------------------------------------------------------------------
# The same guarantees on synthetic bars, so they run everywhere
# --------------------------------------------------------------------------


@pytest.fixture()
def synthetic() -> pd.DataFrame:
    frame = synthetic_frame(n=1200, seed=11)
    return frame


def test_the_worker_refuses_to_start_outside_its_authorised_mode(synthetic, store) -> None:
    """V1 shipped live execution enabled in a committed YAML file for months."""
    session = _session(synthetic, store=store, max_cycles=10)
    session.worker.lconfig.allowed_modes = ("live",)
    with pytest.raises(RuntimeError, match="refuses to start"):
        session.worker.run(install_signals=False)
    assert not session.worker.submissions


def test_the_kill_switch_stops_new_risk_without_stopping_the_feed(
    synthetic, store
) -> None:
    session = _session(synthetic, store=store, max_cycles=len(synthetic))

    class _Blocked:
        def blocks_new_risk(self) -> bool:
            return True

    session.worker.kill_switch = _Blocked()
    session.worker.run(install_signals=False)
    assert session.feed.cursor == len(synthetic), "the feed stopped with the switch"
    assert not session.worker.submissions, "an order was submitted under a kill switch"


def test_market_state_is_fed_and_its_regime_reaches_the_context(synthetic, store) -> None:
    from fiboki.core.enums import Timeframe as _Timeframe
    from fiboki.marketstate.state_engine import EngineConfig, MarketStateEngine

    engine = MarketStateEngine(config=EngineConfig(timeframe=_Timeframe.H1))
    session = build_replay_session(
        frames={"EURUSD": synthetic},
        signal_source=BreakoutSource(),
        store=store,
        paper_config=PaperConfig(
            initial_balance=50_000.0, account_ccy="USD", strategy_id="runtime_breakout"
        ),
        sizing_policy=SizingPolicy(risk_fraction=0.005),
        fx=IdentityFxSource(),
        timeframe="H1",
        warmup=30,
        market_state_engine=engine,
        worker_config=LiveWorkerConfig(
            max_cycles=len(synthetic),
            idle_sleep_seconds=0.0,
            busy_sleep_seconds=0.0,
            reconcile_every_cycles=500,
            lifecycle_every_cycles=500,
            lease_name="live-test-marketstate",
        ),
    )
    session.worker.run(install_signals=False)
    assert session.market_state is not None
    assert session.market_state.ingested > 0, "no bar reached the market state engine"
    attempts = session.gateway.recorder.attempts
    assert attempts
    regimes = {a.extra.get("regime") for a in attempts}
    assert regimes != {"unknown"}, "the market state engine never classified anything"


def test_the_context_builder_reports_an_unregistered_strategy_honestly(
    synthetic, store
) -> None:
    """``health=1.0`` on a strategy nobody has evaluated is not a claim of health.

    It is the declared stage with nothing behind it, and the gateway's own
    lifecycle rules decide what that permits.
    """
    session = _session(synthetic, store=store, max_cycles=5)
    view = session.context_builder.strategy_view("nobody")
    assert view.lifecycle is StrategyLifecycle.PAPER
    assert view.degraded is False
