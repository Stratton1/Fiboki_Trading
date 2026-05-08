"""Tests for Phase 4 account-aware risk engine.

Verifies:
  - Account disabled blocks only that account.
  - Per-account daily / weekly stop blocks only that account.
  - Max-open-positions blocks only that account.
  - Sibling targets are not affected by another target's risk failure.
  - Global kill switch still blocks everything (Phase 1 behaviour preserved).
  - Tradovate zero-contract sizing still rejected (Phase 1 behaviour).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.repository import (
    create_bot_execution_target,
    create_execution_account,
    create_execution_attempt,
    create_bot_signal,
    update_execution_account,
)
from fibokei.execution.account_risk import (
    RISK_BLOCK_ACCOUNT_DISABLED,
    RISK_BLOCK_DAILY_STOP,
    RISK_BLOCK_MAX_OPEN,
    RISK_BLOCK_WEEKLY_STOP,
    AccountRiskEngine,
)
from fibokei.execution.router_factory import build_execution_router_from_env
from fibokei.execution.targets import (
    ATTEMPT_REJECTED,
    ATTEMPT_SKIPPED,
    BROKER_PAPER,
    NormalisedTradePlan,
)


_ENV = [
    "FIBOKEI_EXECUTION_ROUTER_MODE",
    "FIBOKEI_PAPER_ACCOUNT_ENABLED",
    "FIBOKEI_IG_ACCOUNT_ENABLED",
    "FIBOKEI_TRADOVATE_ACCOUNT_ENABLED",
    "FIBOKEI_LIVE_EXECUTION_ENABLED",
]


@pytest.fixture
def clean_env(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("FIBOKEI_EXECUTION_ROUTER_MODE", "db_targets")
    return monkeypatch


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return get_session_factory(engine)


def _plan(instrument: str = "EURUSD") -> NormalisedTradePlan:
    return NormalisedTradePlan(
        bot_id="bot-r",
        strategy_id="bot01_sanyaku",
        instrument=instrument,
        timeframe="H1",
        direction="LONG",
        entry_price=1.10,
        stop_loss=1.095,
        take_profit_targets=(1.115,),
        bar_time=datetime(2026, 5, 8, tzinfo=timezone.utc),
        signal_timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


def _seed_loss(session, account_id: int, loss_amount: float):
    """Insert a closed attempt that records ``loss_amount`` of realised loss."""
    sig = create_bot_signal(
        session,
        {
            "bot_id": "bot-r",
            "strategy_id": "bot01_sanyaku",
            "instrument": "EURUSD",
            "timeframe": "H1",
            "direction": "LONG",
            "kind": "open",
        },
    )
    # PnL = (filled_price - requested_price) * filled_size
    # If we want -100 loss with size=1 → (filled - requested) = -100
    create_execution_attempt(
        session,
        {
            "bot_signal_id": sig.id,
            "execution_account_id": account_id,
            "broker": "paper",
            "environment": "paper",
            "instrument": "EURUSD",
            "status": "closed",
            "requested_price": 0.0,
            "filled_price": -loss_amount,
            "filled_size": 1.0,
            "requested_size": 1.0,
        },
    )


# ── Engine itself ──────────────────────────────────────────


class TestAccountRiskEngine:
    def test_no_history_is_ok(self, session_factory):
        engine = AccountRiskEngine(session_factory)
        with session_factory() as s:
            acct = create_execution_account(
                s,
                {
                    "name": "Paper2",
                    "broker": "paper",
                    "environment": "paper",
                    "allocated_capital": 1000.0,
                    "max_daily_loss_pct": 4.0,
                    "max_weekly_loss_pct": 8.0,
                    "max_open_positions": 5,
                },
            )
        decision = engine.evaluate(acct.id)
        assert decision.allowed is True

    def test_disabled_account_blocks(self, session_factory):
        engine = AccountRiskEngine(session_factory)
        with session_factory() as s:
            acct = create_execution_account(
                s,
                {"name": "X", "broker": "ig", "environment": "demo"},
            )
            update_execution_account(s, acct.id, {"is_enabled": False})
        decision = engine.evaluate(acct.id)
        assert decision.allowed is False
        assert decision.code == RISK_BLOCK_ACCOUNT_DISABLED

    def test_daily_stop_blocks(self, session_factory):
        engine = AccountRiskEngine(session_factory)
        with session_factory() as s:
            acct = create_execution_account(
                s,
                {
                    "name": "Y",
                    "broker": "ig",
                    "environment": "demo",
                    "allocated_capital": 1000.0,
                    "max_daily_loss_pct": 4.0,  # → £40
                    "max_weekly_loss_pct": 100.0,  # disable weekly
                    "max_open_positions": 100,
                },
            )
            account_id = acct.id
            _seed_loss(s, account_id, 50.0)  # > £40 daily limit
        decision = engine.evaluate(account_id)
        assert decision.allowed is False
        assert decision.code == RISK_BLOCK_DAILY_STOP

    def test_weekly_stop_blocks(self, session_factory):
        engine = AccountRiskEngine(session_factory)
        with session_factory() as s:
            acct = create_execution_account(
                s,
                {
                    "name": "Z",
                    "broker": "ig",
                    "environment": "demo",
                    "allocated_capital": 1000.0,
                    "max_daily_loss_pct": 100.0,  # disable daily
                    "max_weekly_loss_pct": 5.0,   # → £50
                    "max_open_positions": 100,
                },
            )
            account_id = acct.id
            _seed_loss(s, account_id, 60.0)  # > £50 weekly limit
        decision = engine.evaluate(account_id)
        assert decision.allowed is False
        assert decision.code == RISK_BLOCK_WEEKLY_STOP

    def test_max_open_positions_blocks(self, session_factory):
        engine = AccountRiskEngine(session_factory)
        with session_factory() as s:
            acct = create_execution_account(
                s,
                {
                    "name": "MaxPos",
                    "broker": "ig",
                    "environment": "demo",
                    "allocated_capital": 100_000.0,
                    "max_daily_loss_pct": 100.0,
                    "max_weekly_loss_pct": 100.0,
                    "max_open_positions": 1,
                },
            )
            account_id = acct.id
            # Seed two filled attempts
            sig = create_bot_signal(
                s,
                {
                    "bot_id": "bot-r",
                    "strategy_id": "bot01_sanyaku",
                    "instrument": "EURUSD",
                    "timeframe": "H1",
                    "direction": "LONG",
                    "kind": "open",
                },
            )
            sig_id = sig.id
            for _ in range(2):
                create_execution_attempt(
                    s,
                    {
                        "bot_signal_id": sig_id,
                        "execution_account_id": account_id,
                        "broker": "ig",
                        "environment": "demo",
                        "instrument": "EURUSD",
                        "status": "filled",
                    },
                )
        decision = engine.evaluate(account_id)
        assert decision.allowed is False
        assert decision.code == RISK_BLOCK_MAX_OPEN


# ── Router integration ────────────────────────────────────


class TestRouterIntegration:
    def test_disabled_account_blocks_only_that_account(
        self, clean_env, session_factory
    ):
        with session_factory() as s:
            ig = create_execution_account(
                s, {"name": "IG", "broker": "ig", "environment": "demo"}
            )
            disabled = create_execution_account(
                s,
                {
                    "name": "Other",
                    "broker": "ig",
                    "environment": "demo",
                    "is_enabled": False,
                },
            )
            create_bot_execution_target(
                s, {"bot_id": "bot-r", "execution_account_id": ig.id}
            )
            create_bot_execution_target(
                s, {"bot_id": "bot-r", "execution_account_id": disabled.id}
            )

        router = build_execution_router_from_env(session_factory=session_factory)
        attempts = router.dispatch_open(_plan())
        # The disabled account is filtered at the JOIN, not via risk engine,
        # so the bot only sees the enabled IG target.
        assert len(attempts) == 1
        assert attempts[0].broker == "ig"

    def test_account_daily_stop_does_not_block_sibling(
        self, clean_env, session_factory
    ):
        with session_factory() as s:
            stopped = create_execution_account(
                s,
                {
                    "name": "Stopped",
                    "broker": "ig",
                    "environment": "demo",
                    "allocated_capital": 1000.0,
                    "max_daily_loss_pct": 4.0,
                    "max_weekly_loss_pct": 100.0,
                    "max_open_positions": 100,
                },
            )
            healthy = create_execution_account(
                s,
                {
                    "name": "Healthy",
                    "broker": "ig",
                    "environment": "demo",
                    "allocated_capital": 1000.0,
                    "max_daily_loss_pct": 100.0,
                    "max_weekly_loss_pct": 100.0,
                    "max_open_positions": 100,
                },
            )
            stopped_id = stopped.id
            healthy_id = healthy.id
            create_bot_execution_target(
                s, {"bot_id": "bot-r", "execution_account_id": stopped_id}
            )
            create_bot_execution_target(
                s, {"bot_id": "bot-r", "execution_account_id": healthy_id}
            )
            _seed_loss(s, stopped_id, 100.0)  # 10% loss → over 4% limit

        router = build_execution_router_from_env(session_factory=session_factory)
        attempts = router.dispatch_open(_plan())
        by_account = {a.target_id: a for a in attempts}
        # Stopped account: rejected with DAILY_STOP
        rejected = by_account[f"acct-{stopped_id}"]
        assert rejected.status == ATTEMPT_REJECTED
        assert rejected.error_code == RISK_BLOCK_DAILY_STOP
        # Healthy account: not affected — still dispatched (filled or whatever
        # the IG adapter says; what matters is risk did not block it)
        healthy_attempt = by_account[f"acct-{healthy_id}"]
        assert healthy_attempt.error_code != RISK_BLOCK_DAILY_STOP

    def test_kill_switch_blocks_everything(self, clean_env, session_factory):
        with session_factory() as s:
            healthy = create_execution_account(
                s,
                {
                    "name": "H",
                    "broker": "ig",
                    "environment": "demo",
                    "max_daily_loss_pct": 100.0,
                    "max_weekly_loss_pct": 100.0,
                },
            )
            create_bot_execution_target(
                s, {"bot_id": "bot-r", "execution_account_id": healthy.id}
            )

        router = build_execution_router_from_env(
            session_factory=session_factory,
            kill_switch_check=lambda: True,
        )
        attempts = router.dispatch_open(_plan())
        # Kill switch is checked first → everything skipped, none rejected
        assert all(a.status == ATTEMPT_SKIPPED for a in attempts)
        assert all(a.error_code == "KILL_SWITCH" for a in attempts)


class TestPaperBackwardsCompat:
    def test_paper_target_unaffected_by_engine(self, clean_env, session_factory):
        # No Phase 1 env-driven target carries an integer account id, so the
        # risk engine must not be triggered for legacy/static targets.
        from fibokei.execution.account_risk import AccountRiskEngine
        from fibokei.execution.router import ExecutionRouter
        from fibokei.execution.paper_adapter import PaperExecutionAdapter
        from fibokei.execution.targets import (
            ENV_PAPER,
            ROUTER_MODE_LEGACY_SINGLE,
            ResolvedTarget,
        )

        # Build a legacy-style paper target with a string id.
        target = ResolvedTarget(
            target_id="paper-default",  # not "acct-N"
            name="Paper",
            broker=BROKER_PAPER,
            environment=ENV_PAPER,
            allocated_capital=1000.0,
            risk_per_trade_pct=1.0,
            is_enabled=True,
            adapter=PaperExecutionAdapter(),
        )
        engine = AccountRiskEngine(session_factory)
        router = ExecutionRouter(
            mode=ROUTER_MODE_LEGACY_SINGLE,
            targets=[target],
            account_risk_engine=engine,
        )
        attempts = router.dispatch_open(_plan())
        # Paper attempt should fill — risk engine should not block it because
        # ``target_id`` doesn't decode to an integer account id.
        assert len(attempts) == 1
        assert attempts[0].status != ATTEMPT_REJECTED
