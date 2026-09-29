"""Entry locks: one decision, three paths, byte-identical ledgers.

In the style of ``tests/integration/test_venue_position_manager.py`` (the
five-path parity suite). A strategy DOCUMENT declaring both a cooldown and an
instrument-scoped stop streak is compiled once and driven, on the same bars,
through:

* :class:`~fiboki.backtest.engine.BacktestEngine`, where the lock is enforced by
  the engine's :class:`~fiboki.backtest.locks.LockBook` on the decision bar;
* :class:`~fiboki.broker.paper.PaperBroker` behind a
  :class:`~fiboki.risk.gateway.RiskGateway` whose ``instrument_lock`` check reads
  a :class:`~fiboki.backtest.locks.LedgerLockView` over the paper TRADE ledger --
  in the second variant the gateway is thrown away and rebuilt from a ledger
  file on disk at EVERY bar, which is a restart at every bar;
* :class:`~fiboki.broker.position_manager.VenuePositionManager` over
  :class:`~fiboki.broker.simulated_venue.SimulatedVenue`, submitting through
  :class:`~fiboki.broker.execution_service.ExecutionService` -- the production
  order path, gateway included.

Asserted: identical trade and per-fill leg ledgers, identical non-lock
rejections, and that the gateway refused exactly the (bar, instrument) pairs
the engine's lock book refused, for exactly the same named lock -- and nothing
else. The fixture is held to arming both rules and to at least one lock that
spans a weekend, so a pass cannot come from locks that never bit.

Why the paper paths go through the gateway rather than through the position
book: in production every order reaches a venue through
``ExecutionService.submit`` (an AST test enforces it), and the gateway is the
one component every such order passes. The paper adapter itself is unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    FixedFractionalSizer,
    run_backtest,
)
from fiboki.backtest.exits import exit_policy_from_document, lock_policy_from_document
from fiboki.backtest.locks import ClosedTrade, LedgerLockView, LockRegistration
from fiboki.backtest.position import BookConfig, PositionBook
from fiboki.broker.execution_service import ExecutionService, InMemoryIntentStore
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.broker.position_manager import RetryPolicy, VenuePositionManager
from fiboki.broker.simulated_venue import SimulatedVenue, VenueConfig
from fiboki.core.contracts import Order, TradePlan
from fiboki.core.enums import (
    ExecutionMode,
    OrderType,
    Provenance,
    StrategyLifecycle,
    Timeframe,
)
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.portfolio.construction import PortfolioSnapshot
from fiboki.risk.gateway import (
    ExitContext,
    InMemoryAttemptRecorder,
    MarketView,
    RiskContext,
    RiskGateway,
    StrategyView,
    VenueView,
)
from fiboki.risk.killswitch import RequestKind
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.fills import Bar, FillSimulator, IntrabarPolicy
from fiboki.sim.profiles import IG_REALISTIC
from fiboki.strategy.compiler import compile_strategy
from fiboki.strategy.dsl import LOCKS_DECLARED_FEATURE, StrategyDocument
from tests.lock_fixtures import lock_document, session_frame

SYMBOL = "EURUSD"
START = 100_000.0
RISK = 0.0025
LOCKS = {
    "cooldown_bars_after_close": 3,
    "stop_streak": {"n_stops": 2, "lookback_bars": 30, "lock_bars": 12, "scope": "instrument"},
}


# ==========================================================================
# Shared inputs
# ==========================================================================


class _Signals:
    """The compiled document, asked once per bar. Shared by every path."""

    def __init__(self, document: StrategyDocument, frame: pd.DataFrame) -> None:
        self.compiled = compile_strategy(document)
        self.prepared = self.compiled.prepare(frame)

    def at(self, j: int):
        signal = self.compiled.generate_signal(self.prepared, j, SYMBOL, "H1")
        return () if signal is None else (signal,)

    def on_bar(self, ctx):  # the engine's Strategy protocol
        j = ctx._cursor.get(SYMBOL, -1)
        return () if j < 0 else self.at(j)


def _bar(frame: pd.DataFrame, i: int) -> Bar:
    row = frame.iloc[i]
    return Bar(frame.index[i], float(row["open"]), float(row["high"]), float(row["low"]),
               float(row["close"]))


def _plan(signal, size: float, rate: float) -> TradePlan:
    instrument = get_instrument(signal.instrument)
    return TradePlan(
        signal=signal,
        size=size,
        account_ccy="USD",
        risk_amount=size * signal.stop_distance * instrument.contract_size * rate,
        sizing_basis="test:fixed_fractional",
        max_leverage_applied=instrument.retail_leverage,
    )


def _context(plan: TradePlan, account, bar: Bar) -> RiskContext:
    """A permissive but REAL context: every one of the nineteen checks runs."""
    now = bar.timestamp + pd.Timedelta(hours=1)  # the moment the bar closed
    instrument = get_instrument(plan.instrument)
    return RiskContext(
        plan=plan,
        snapshot=PortfolioSnapshot(account=account, as_of=now),
        now=now,
        mode=ExecutionMode.PAPER,
        limits=PAPER_LIMITS,
        market=MarketView(
            mid_price=bar.close,
            spread_price=instrument.typical_spread_pips * instrument.pip_size,
            quote_time=now,
            last_bar_time=now,
            market_open=True,
        ),
        venue=VenueView(connected=True, score=1.0),
        strategy=StrategyView(lifecycle=StrategyLifecycle.PAPER),
        request_kind=RequestKind.OPEN,
        # A flat book in an account-currency-quoted instrument, stated.
        open_risk_amount=0.0,
        correlated_exposure=0.0,
        daily_pnl=0.0,
        weekly_pnl=0.0,
        fx_quote_to_account=1.0,
    )


def _registrations(document: StrategyDocument) -> dict[str, LockRegistration]:
    policy = lock_policy_from_document(document)
    assert policy is not None
    return {document.strategy_id: LockRegistration(policy, Timeframe.H1)}


def ledger(trades) -> str:
    return BacktestResult(
        trades=list(trades), equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
    ).ledger_text()


def leg_ledger(legs) -> str:
    return BacktestResult(
        trades=[], equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
        exit_legs=list(legs),
    ).leg_ledger_text()


def _lock_blocks_from(recorder: InMemoryAttemptRecorder) -> list[tuple[pd.Timestamp, str, str]]:
    out = []
    for attempt in recorder.blocked:
        reasons = attempt.decision.reasons
        assert all(r.startswith("instrument_lock:") for r in reasons), (
            f"the gateway refused for a reason other than a lock: {reasons}. Parity "
            "with the engine would then be accidental."
        )
        (reason,) = reasons
        bar_time = attempt.decided_at - pd.Timedelta(hours=1)
        out.append((bar_time, attempt.instrument, reason.removeprefix("instrument_lock:")))
    return out


# ==========================================================================
# The three paths
# ==========================================================================


def run_engine(document, frame) -> BacktestResult:
    return run_backtest(
        data={SYMBOL: frame},
        config=BacktestConfig(
            initial_balance=START, account_ccy="USD", profile=IG_REALISTIC,
            intrabar_policy=IntrabarPolicy.STOP_FIRST, strategy_id="lock_parity",
        ),
        strategy=_Signals(document, frame),
        sizer=FixedFractionalSizer(risk_fraction=RISK),
        fx=IdentityFxSource(),
        exit_policy=exit_policy_from_document(document),
    )


def run_paper_through_gateway(
    document, frame, *, ledger_file: Path | None = None, no_lock_source: bool = False
):
    """The paper adapter, with every entry decided by the risk gateway.

    With ``ledger_file``, closed trades are persisted to disk after every bar
    and a BRAND NEW gateway, reading only that file, decides every entry: a
    process restart between every pair of bars.
    """
    signals = _Signals(document, frame)
    policy = exit_policy_from_document(document)
    regs = _registrations(document)
    fx = IdentityFxSource()
    sizer = FixedFractionalSizer(risk_fraction=RISK)
    paper = PaperBroker(
        config=PaperConfig(
            initial_balance=START, account_ccy="USD", profile=IG_REALISTIC,
            provenance=Provenance.PAPER, exit_policy=policy, strategy_id="lock_parity",
        ),
        fx=fx,
    )
    paper.set_bar_interval(SYMBOL, pd.Timedelta(hours=1))
    recorder = InMemoryAttemptRecorder()
    persisted = 0

    def read_ledger() -> list[ClosedTrade]:
        assert ledger_file is not None
        rows = [json.loads(x) for x in ledger_file.read_text().splitlines() if x]
        return [
            ClosedTrade(r["strategy_id"], r["instrument"], pd.Timestamp(r["exit_time"]),
                        r["exit_reason"])
            for r in rows
        ]

    gateway = RiskGateway(
        recorder=recorder, limits=PAPER_LIMITS,
        locks=None if no_lock_source else LedgerLockView(regs, lambda: list(paper.trades)),
    )
    instrument = get_instrument(SYMBOL)
    last = None
    for i in range(len(frame)):
        bar = _bar(frame, i)
        last = bar
        paper.on_bar({SYMBOL: bar}, bar_index=i, timestamp=bar.timestamp)
        if ledger_file is not None:
            with ledger_file.open("a") as fh:
                for trade in paper.trades[persisted:]:
                    fh.write(json.dumps({
                        "strategy_id": trade.strategy_id, "instrument": trade.instrument,
                        "exit_time": trade.exit_time.isoformat(),
                        "exit_reason": trade.exit_reason.value,
                    }) + "\n")
            persisted = len(paper.trades)
            # The restart: nothing survives but the file.
            gateway = RiskGateway(
                recorder=recorder, limits=PAPER_LIMITS,
                locks=LedgerLockView(regs, read_ledger),
            )
        account = paper.account()
        for signal in signals.at(i):
            paper.book.schedule_reversal(signal.instrument, signal.direction, index=i)
            size = sizer.size_for(signal, instrument, account, 1.0)
            if size <= 0:
                paper.book.bump("sized_to_zero")
                continue
            plan = _plan(signal, size, 1.0)
            if not gateway.evaluate(_context(plan, account, bar)).allowed:
                continue
            paper.register_plan(plan.plan_id, signal.strategy_id)
            paper.place_order(
                Order(
                    plan_id=plan.plan_id, instrument=SYMBOL, direction=signal.direction,
                    size=size, order_type=OrderType.MARKET, mode=ExecutionMode.PAPER,
                    client_ref=f"paper-{plan.plan_id}", stop_loss=signal.stop_price,
                    take_profit=signal.take_profit_prices[0] if signal.take_profit_prices else None,
                    take_profit_prices=tuple(signal.take_profit_prices),
                    take_profit_allocations=tuple(signal.take_profit_allocations),
                    created_at=bar.timestamp,
                )
            )
    paper.finish({SYMBOL: last})
    return paper, recorder


def run_venue_manager(document, frame):
    """``VenuePositionManager.submit``: gateway, durable intent, venue, book."""
    signals = _Signals(document, frame)
    policy = exit_policy_from_document(document)
    regs = _registrations(document)
    fx = IdentityFxSource()
    sizer = FixedFractionalSizer(risk_fraction=RISK)
    book = PositionBook(
        sim=FillSimulator(profile=IG_REALISTIC, intrabar_policy=IntrabarPolicy.STOP_FIRST),
        fx=fx,
        config=BookConfig(account_ccy="USD", strategy_id="lock_parity",
                          provenance=Provenance.PAPER),
        policy=policy,
        initial_balance=START,
        latency_bars=IG_REALISTIC.latency_bars,
        financing_profile=IG_REALISTIC.financing,
    )
    venue = SimulatedVenue(
        VenueConfig(prices={SYMBOL: 1.10}), balance=START, currency="USD",
        now=frame.index[0],
    )
    recorder = InMemoryAttemptRecorder()
    lock_view = LedgerLockView(regs, lambda: list(book.trades))
    execution = ExecutionService(
        adapter=venue,
        gateway=RiskGateway(recorder=recorder, limits=PAPER_LIMITS, locks=lock_view),
        store=InMemoryIntentStore(),
        mode=ExecutionMode.PAPER,
    )
    manager: VenuePositionManager

    def exit_context(position, kind: str) -> ExitContext:
        now = manager.now or frame.index[0]
        return ExitContext(
            instrument=position.instrument, strategy_id=position.strategy_id or "",
            size=position.size, now=now, mode=ExecutionMode.PAPER,
            position_id=position.position_id, limits=PAPER_LIMITS,
            market=MarketView(mid_price=1.10, spread_price=0.00012, quote_time=now,
                              last_bar_time=now, market_open=True),
            venue=VenueView(connected=True, score=1.0),
            request_kind=(RequestKind.PROTECTIVE_AMEND if kind == "amend"
                          else RequestKind.REDUCE if kind == "reduce" else RequestKind.CLOSE),
        )

    manager = VenuePositionManager(
        execution=execution, book=book, context_factory=exit_context, fx=fx,
        account_ccy="USD", latency_bars=IG_REALISTIC.latency_bars,
        retry=RetryPolicy(attempts=2, initial_backoff_seconds=0.0), sleeper=lambda _s: None,
    )
    manager.set_bar_interval(SYMBOL, pd.Timedelta(hours=1))
    instrument = get_instrument(SYMBOL)
    last = None
    for i in range(len(frame)):
        bar = _bar(frame, i)
        last = bar
        venue.now = bar.timestamp
        manager.on_bar({SYMBOL: bar}, bar_index=i, timestamp=bar.timestamp)
        account = manager.account()
        for signal in signals.at(i):
            manager.book.schedule_reversal(signal.instrument, signal.direction, index=i)
            size = sizer.size_for(signal, instrument, account, 1.0)
            if size <= 0:
                manager.book.bump("sized_to_zero")
                continue
            plan = _plan(signal, size, 1.0)
            # ORDER MATTERS, and a live worker must copy it: the lock is asked
            # FIRST, exactly where the engine asks it. The book's own pre-trade
            # check consumes a sequence number when it refuses, so running it
            # before the lock would advance the counter on a signal the engine
            # never sequenced, and the two price paths would decorrelate.
            if lock_view.lock_for(signal.instrument, signal.strategy_id, signal.bar_time,
                                  declared=True, timeframe=signal.timeframe) is not None:
                # Still a gateway decision on the record: submit is refused.
                outcome = manager.submit(plan, _context(plan, account, bar),
                                         strategy_id=signal.strategy_id)
                assert not outcome.accepted
                continue
            if manager.precheck_entry(SYMBOL) is not None:
                continue
            outcome = manager.submit(plan, _context(plan, account, bar),
                                     strategy_id=signal.strategy_id)
            assert outcome.accepted, outcome
    manager.finish({SYMBOL: last})
    return manager, recorder


# ==========================================================================
# Fixtures
# ==========================================================================


@pytest.fixture(scope="module")
def document() -> StrategyDocument:
    doc = lock_document(LOCKS)
    assert doc.locks is not None
    return doc


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    # Seed 5: chosen because its locks include ones armed on a Friday that are
    # still refusing entries after the weekend (asserted below).
    return session_frame(2400, seed=5)


@pytest.fixture(scope="module")
def engine(document, frame) -> BacktestResult:
    return run_engine(document, frame)


@pytest.fixture(scope="module")
def engine_blocks(engine) -> list[tuple[pd.Timestamp, str, str]]:
    return [(b["bar_time"], b["instrument"], b["lock"]) for b in engine.lock_blocks]


def _non_lock(rejections: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in rejections.items() if k != "instrument_lock"}


# ==========================================================================
# The tests
# ==========================================================================


def test_the_fixture_exercises_both_rules_and_a_weekend(engine, engine_blocks) -> None:
    kinds = {code.split(":")[0] for _, _, code in engine_blocks}
    assert kinds == {"cooldown", "stop_streak"}, kinds
    assert engine.rejections["instrument_lock"] == len(engine_blocks) > 0
    assert len(engine.trades) > 40
    reasons = {t.exit_reason.value for t in engine.trades}
    assert {"stop_loss", "take_profit"} <= reasons
    # At least one lock was armed on a Friday and still refused an entry after
    # the weekend -- the case a wall-clock lock gets wrong.
    spans = [
        (ts, code) for ts, _, code in engine_blocks
        if ts.dayofweek in (6, 0)
        and pd.Timestamp(code.split("armed=")[1].split(":bars")[0]).dayofweek == 4
    ]
    assert spans, "no lock spanned a weekend; the fixture does not test the calendar"


def test_locks_change_the_ledger(document, frame, engine) -> None:
    """Otherwise every assertion below could pass with locks switched off."""
    unlocked = run_engine(lock_document(None), frame)
    assert unlocked.ledger_text() != engine.ledger_text()
    assert unlocked.lock_blocks == []


def test_paper_through_the_gateway_matches_the_engine(document, frame, engine,
                                                      engine_blocks) -> None:
    paper, recorder = run_paper_through_gateway(document, frame)
    assert ledger(paper.trades) == engine.ledger_text()
    assert leg_ledger(paper.exit_legs) == engine.leg_ledger_text()
    assert paper.rejections == _non_lock(engine.rejections)
    assert paper.balance == engine.final_equity
    assert _lock_blocks_from(recorder) == engine_blocks


def test_a_gateway_restarted_at_every_bar_blocks_the_same_bars(
    document, frame, engine, engine_blocks, tmp_path
) -> None:
    ledger_file = tmp_path / "trades.jsonl"
    ledger_file.touch()
    paper, recorder = run_paper_through_gateway(document, frame, ledger_file=ledger_file)
    assert ledger(paper.trades) == engine.ledger_text()
    assert _lock_blocks_from(recorder) == engine_blocks


def test_the_venue_manager_through_execution_service_matches(document, frame, engine,
                                                             engine_blocks) -> None:
    manager, recorder = run_venue_manager(document, frame)
    assert ledger(manager.book.trades) == engine.ledger_text()
    assert leg_ledger(manager.book.exit_legs) == engine.leg_ledger_text()
    assert manager.book.rejections == _non_lock(engine.rejections)
    assert _lock_blocks_from(recorder) == engine_blocks


def test_every_gateway_decision_named_the_check(document, frame) -> None:
    _paper, recorder = run_paper_through_gateway(document, frame)
    assert recorder.attempts
    for attempt in recorder.attempts:
        assert "instrument_lock" in attempt.decision.checks_run


def test_a_gateway_with_no_lock_state_trades_nothing_for_this_document(document,
                                                                         frame) -> None:
    """Fail-closed, end to end: the document declares locks, the deployment
    wired no lock source, and so not one entry is dealt."""
    paper, recorder = run_paper_through_gateway(document, frame, no_lock_source=True)
    assert paper.trades == []
    assert recorder.attempts and not any(a.allowed for a in recorder.attempts)
    for attempt in recorder.attempts:
        assert attempt.decision.reasons == (
            "instrument_lock_state_unavailable:no_lock_source",
        )
    assert LOCKS_DECLARED_FEATURE == "locks_declared"
