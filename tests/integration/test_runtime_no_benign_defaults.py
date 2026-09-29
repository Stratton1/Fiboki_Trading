"""The runtime context builder: no benign defaults, like-for-like open risk,
and the PAPER composition's explicit permission for missing inputs.

Round 4 integration (workers/runtime.py):

* a missing P&L ledger, correlation matrix or market-open source is a ``None``
  input, never ``0.0`` or ``True``;
* ``open_risk`` is priced with the SAME cost-inclusive rule as a new plan's
  ``risk_amount`` (``fixed_fractional_v2``), so ``max_account_risk`` adds like
  to like;
* the replay composition selects ``limits_v2_paper`` through
  ``default_limit_set`` and builds its gateway with
  ``paper_allows_missing_inputs=True``, recorded in the summary; a DEMO/LIVE
  composition never does.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from fiboki.backtest.engine import expected_entry_time
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.core.contracts import Position
from fiboki.core.enums import ExecutionMode
from fiboki.core.money import IdentityFxSource
from fiboki.portfolio.sizing import SIZING_POLICY_V1, SizingPolicy
from fiboki.risk.limits import (
    CONSERVATIVE_LIMITS_V2,
    DEFAULT_LIMITS_V2,
    PAPER_LIMITS,
    PAPER_LIMITS_V2,
)
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import BarBatch
from fiboki.workers.runtime import (
    RiskContextBuilder,
    event_source_horizon_minutes,
    replay_market_open_source,
)
from tests.exec_fixtures import make_plan, make_signal
from tests.integration.test_risk_context_inputs import _session

NOW = pd.Timestamp("2024-06-05 12:00", tz="UTC")


@pytest.fixture()
def store(tmp_path) -> WorkerStore:
    with WorkerStore.sqlite_at(tmp_path / "workers.sqlite") as handle:
        yield handle


@dataclass
class _Book:
    open_positions: list[Position]

    def positions(self) -> list[Position]:
        return list(self.open_positions)


def _builder(adapter, **kw) -> RiskContextBuilder:
    return RiskContextBuilder(adapter=adapter, clock=lambda: NOW, fx=IdentityFxSource(),
                              account_ccy="USD", **kw)


def test_unsupplied_sources_are_none_not_benign() -> None:
    broker = PaperBroker(config=PaperConfig(initial_balance=100_000.0, account_ccy="USD"),
                         fx=IdentityFxSource())
    context = _builder(broker)(make_plan(signal=make_signal(instrument="EURUSD")))
    assert context.daily_pnl is None
    assert context.weekly_pnl is None
    assert context.correlated_exposure is None
    assert context.market.market_open is None
    # The builder's limit set comes from the one source of truth.
    assert context.limits is PAPER_LIMITS_V2
    assert _builder(broker, mode=ExecutionMode.DEMO).limits is DEFAULT_LIMITS_V2
    assert _builder(broker, mode=ExecutionMode.LIVE).limits is CONSERVATIVE_LIMITS_V2
    assert _builder(broker, limits=PAPER_LIMITS).limits is PAPER_LIMITS


@pytest.mark.parametrize("policy_id", ["fixed_fractional_v2", SIZING_POLICY_V1])
def test_open_risk_uses_the_same_definition_as_a_new_plan(policy_id: str) -> None:
    """A position opened exactly as a plan was sized carries the plan's risk."""
    policy = SizingPolicy(risk_fraction=0.01, policy_id=policy_id)
    signal = make_signal(instrument="EURUSD", stop_distance=0.0005)
    plan = make_plan(signal=signal)
    if policy_id == SIZING_POLICY_V1:
        from fiboki.core.instruments import get as get_instrument
        from fiboki.portfolio.sizing import size_trade
        from tests.exec_fixtures import make_account

        plan = size_trade(signal=signal, instrument=get_instrument("EURUSD"),
                          account=make_account(100_000.0), fx_quote_to_account=1.0,
                          policy=policy).require()
    position = Position(
        instrument="EURUSD",
        direction=signal.direction,
        size=plan.size,
        entry_price=signal.reference_price,
        entry_time=expected_entry_time(signal),
        stop_loss=signal.stop_price,
    )
    builder = _builder(_Book([position]), sizing_policy=policy)
    assert builder.open_risk(NOW) == pytest.approx(plan.risk_amount, rel=1e-9)
    bare = abs(signal.reference_price - signal.stop_price) * plan.size
    if policy_id == SIZING_POLICY_V1:
        assert builder.open_risk(NOW) == pytest.approx(bare, rel=1e-9)
    else:
        assert builder.open_risk(NOW) > bare * 1.05, "v2 open risk includes the stop-out costs"


def test_replay_market_open_source_is_the_data() -> None:
    broker = PaperBroker(config=PaperConfig(initial_balance=100_000.0, account_ccy="USD"),
                         fx=IdentityFxSource())
    builder = _builder(broker)
    source = replay_market_open_source(builder)
    assert source("EURUSD", NOW) is False, "no bar seen: closed, not assumed open"
    builder.observe(BarBatch(frames={"EURUSD": type("B", (), {"timestamp": NOW})()}))
    assert source("EURUSD", NOW) is True
    assert source("EURUSD", NOW + pd.Timedelta(hours=1)) is False
    assert source("GBPUSD", NOW) is False


def test_event_source_horizon_covers_the_v2_window() -> None:
    assert event_source_horizon_minutes(PAPER_LIMITS, 240.0) == 15.0
    # v2 paper: 30 before / 15 after the decision, which is up to one bar after
    # ``now``, reaching at least one bar ahead of it.
    assert event_source_horizon_minutes(PAPER_LIMITS_V2, 240.0) == 480.0
    assert event_source_horizon_minutes(PAPER_LIMITS_V2, 60.0) == 120.0
    assert event_source_horizon_minutes(PAPER_LIMITS_V2, 5.0) == 35.0
    assert event_source_horizon_minutes(PAPER_LIMITS_V2, 0.0) == 30.0


def test_the_replay_composition_is_paper_v2_and_records_its_permission(store) -> None:
    session = _session(store)
    assert session.gateway.mode is ExecutionMode.PAPER
    assert session.gateway.paper_allows_missing_inputs is True
    assert session.context_builder.limits is PAPER_LIMITS_V2
    assert session.context_builder.market_open_source is not None
    for _ in range(60):
        session.worker.run_cycle()
    summary = session.summary()
    assert summary["limits_version"] == "limits_v2_paper"
    assert summary["paper_allows_missing_inputs"] is True
    assert summary["sizing"]["open_risk_basis"].startswith("fixed_fractional_v2")
    rows = session.telemetry()
    assert rows, "the replay produced gateway attempts"
    assert {r["limits_version"] for r in rows} == {"limits_v2_paper"}
    # The sourced market-open input lets the replay trade: nothing is refused
    # as closed or unknown, and orders are accepted.
    assert not [k for k in summary["block_reasons"] if k.startswith("market")]
    assert summary["accepted"] > 0
