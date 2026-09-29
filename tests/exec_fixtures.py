"""Builders for portfolio / risk / broker tests.

Kept out of ``conftest.py`` deliberately: these are constructors, not fixtures,
and a test that builds its own inputs explicitly is far easier to read when it
fails at 3am than one that inherits five layers of fixture defaults.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fiboki.core.contracts import AccountState, Position, Signal, TradePlan
from fiboki.core.enums import Direction, ExecutionMode, StrategyLifecycle
from fiboki.core.instruments import get as get_instrument
from fiboki.portfolio.construction import CandidateSignal, PortfolioSnapshot
from fiboki.portfolio.sizing import SizingPolicy, size_trade
from fiboki.risk.gateway import MarketView, RiskContext, StrategyView, VenueView
from fiboki.risk.limits import DEFAULT_LIMITS, LimitSet

NOW = pd.Timestamp("2024-06-03 12:00", tz="UTC")


def make_signal(
    *,
    instrument: str = "EURUSD",
    strategy_id: str = "ichimoku_a",
    direction: Direction = Direction.LONG,
    reference_price: float = 1.1000,
    stop_distance: float = 0.0030,
    take_profit_distance: float | None = 0.0060,
    confidence: float = 1.0,
    bar_time: pd.Timestamp = NOW,
) -> Signal:
    stop = (
        reference_price - stop_distance
        if direction is Direction.LONG
        else reference_price + stop_distance
    )
    tps: tuple[float, ...] = ()
    if take_profit_distance is not None:
        tps = (reference_price + direction.sign * abs(take_profit_distance),)
    return Signal(
        strategy_id=strategy_id,
        instrument=instrument,
        timeframe="H1",
        direction=direction,
        bar_time=bar_time,
        reference_price=reference_price,
        stop_price=stop,
        take_profit_prices=tps,
        confidence=confidence,
    )


def make_account(equity: float = 100_000.0, **kwargs) -> AccountState:
    params = {
        "balance": equity,
        "equity": equity,
        "currency": "GBP",
        "peak_equity": equity,
    }
    params.update(kwargs)
    return AccountState(**params)


def make_plan(
    signal: Signal | None = None,
    *,
    equity: float = 100_000.0,
    risk_fraction: float = 0.01,
    fx: float = 1.0,
    portfolio_weight: float = 1.0,
) -> TradePlan:
    sig = signal or make_signal()
    outcome = size_trade(
        signal=sig,
        instrument=get_instrument(sig.instrument),
        account=make_account(equity),
        fx_quote_to_account=fx,
        policy=SizingPolicy(risk_fraction=risk_fraction),
        portfolio_weight=portfolio_weight,
    )
    return outcome.require()


def make_snapshot(
    *,
    equity: float = 100_000.0,
    as_of: pd.Timestamp = NOW,
    positions: tuple[Position, ...] = (),
    **kwargs,
) -> PortfolioSnapshot:
    account = kwargs.pop("account", None) or make_account(equity, **{
        k: kwargs.pop(k) for k in ("margin_used", "peak_equity") if k in kwargs
    })
    return PortfolioSnapshot(account=account, as_of=as_of, open_positions=positions, **kwargs)


def healthy_market(
    *,
    now: pd.Timestamp = NOW,
    mid_price: float = 1.1000,
    spread_pips: float = 1.0,
    instrument: str = "EURUSD",
) -> MarketView:
    instr = get_instrument(instrument)
    return MarketView(
        mid_price=mid_price,
        spread_price=spread_pips * instr.pip_size,
        quote_time=now - pd.Timedelta(seconds=2),
        last_bar_time=now - pd.Timedelta(seconds=30),
        market_open=True,
        regime="trend",
        event_times=(),
    )


def healthy_venue() -> VenueView:
    return VenueView(connected=True, score=1.0, message="ok")


def make_context(
    plan: TradePlan | None = None,
    *,
    now: pd.Timestamp = NOW,
    mode: ExecutionMode = ExecutionMode.PAPER,
    limits: LimitSet = DEFAULT_LIMITS,
    lifecycle: StrategyLifecycle = StrategyLifecycle.PAPER,
    snapshot: PortfolioSnapshot | None = None,
    **kwargs,
) -> RiskContext:
    p = plan or make_plan()
    # The loss/exposure inputs are Optional on RiskContext and a missing one
    # BLOCKS (audit F P1-4). This fixture describes a flat, fresh book, so it
    # says so explicitly rather than relying on a benign default.
    for name, value in (
        ("open_risk_amount", 0.0),
        ("correlated_exposure", 0.0),
        ("daily_pnl", 0.0),
        ("weekly_pnl", 0.0),
        ("fx_quote_to_account", 1.0),
    ):
        kwargs.setdefault(name, value)
    return RiskContext(
        plan=p,
        snapshot=snapshot or make_snapshot(as_of=now),
        now=now,
        mode=mode,
        limits=limits,
        market=kwargs.pop("market", healthy_market(now=now, instrument=p.instrument)),
        venue=kwargs.pop("venue", healthy_venue()),
        strategy=kwargs.pop("strategy", StrategyView(lifecycle=lifecycle, health=1.0)),
        **kwargs,
    )


def make_candidate(
    *,
    instrument: str = "EURUSD",
    strategy_id: str = "ichimoku_a",
    vol: float = 0.08,
    health: float = 1.0,
    confidence: float = 1.0,
    lifecycle: StrategyLifecycle = StrategyLifecycle.PAPER,
) -> CandidateSignal:
    return CandidateSignal(
        signal=make_signal(
            instrument=instrument, strategy_id=strategy_id, confidence=confidence
        ),
        annualised_vol=vol,
        health=health,
        lifecycle=lifecycle,
    )


def make_position(
    *,
    instrument: str = "EURUSD",
    strategy_id: str = "ichimoku_a",
    size: float = 10_000.0,
    direction: Direction = Direction.LONG,
    entry_price: float = 1.1000,
    stop_loss: float = 1.0970,
    venue_ref: str | None = None,
    entry_time: pd.Timestamp = NOW,
) -> Position:
    return Position(
        instrument=instrument,
        direction=direction,
        size=size,
        entry_price=entry_price,
        entry_time=entry_time,
        stop_loss=stop_loss,
        strategy_id=strategy_id,
        venue_ref=venue_ref,
    )


def synthetic_frame(n: int = 400, seed: int = 7, start: str = "2024-01-01") -> pd.DataFrame:
    """A coherent OHLC frame: a seeded random walk with valid bar geometry."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    close = 1.10 + np.cumsum(rng.normal(0, 0.0007, n))
    high = close + np.abs(rng.normal(0, 0.0004, n))
    low = close - np.abs(rng.normal(0, 0.0004, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    open_ = np.clip(open_, low, high)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close}, index=idx
    )
