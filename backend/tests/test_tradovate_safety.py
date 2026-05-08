"""Safety tests for the Tradovate path.

These are the no-regret guardrails: live blocked unless explicit, missing
credentials never crash paper-mode bots, kill switch blocks Tradovate
attempts, and credentials never appear in logs or in tracebacks.
"""

from __future__ import annotations

import pytest

from fibokei.execution.router import ExecutionRouter
from fibokei.execution.targets import (
    BROKER_TRADOVATE,
    ENV_LIVE,
    ROUTER_MODE_ENV_GLOBAL_FANOUT,
    NormalisedTradePlan,
    ResolvedTarget,
)
from fibokei.execution.tradovate_adapter import TradovateExecutionAdapter
from fibokei.execution.tradovate_client import (
    TradovateClient,
    TradovateClientError,
)

_ENV = [
    "FIBOKEI_TRADOVATE_USERNAME",
    "FIBOKEI_TRADOVATE_PASSWORD",
    "FIBOKEI_TRADOVATE_CID",
    "FIBOKEI_TRADOVATE_SECRET",
    "FIBOKEI_TRADOVATE_ENV",
    "FIBOKEI_TRADOVATE_LIVE_ALLOWED",
    "FIBOKEI_LIVE_EXECUTION_ENABLED",
]


@pytest.fixture
def clean_env(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def test_paper_mode_does_not_crash_without_tradovate_creds(clean_env):
    """Constructing a Tradovate client with no creds must not raise.

    The router can hold a Tradovate target that's later disabled or never
    routed to. Construction itself must be cheap and side-effect-free.
    """
    c = TradovateClient()
    assert c.has_credentials is False
    # Authentication should fail loudly only when called.
    with pytest.raises(TradovateClientError):
        c.authenticate()


def test_live_blocked_unless_three_flags_set(clean_env):
    """Each flag in isolation is insufficient for live."""
    clean_env.setenv("FIBOKEI_TRADOVATE_USERNAME", "u")
    clean_env.setenv("FIBOKEI_TRADOVATE_PASSWORD", "p")
    clean_env.setenv("FIBOKEI_TRADOVATE_CID", "1")
    clean_env.setenv("FIBOKEI_TRADOVATE_SECRET", "s")

    # Just FIBOKEI_TRADOVATE_ENV=live → blocked
    clean_env.setenv("FIBOKEI_TRADOVATE_ENV", "live")
    with pytest.raises(TradovateClientError) as exc:
        TradovateClient().authenticate()
    assert exc.value.error_code == "LIVE_BLOCKED"

    # Plus LIVE_ALLOWED but no global → still blocked
    clean_env.setenv("FIBOKEI_TRADOVATE_LIVE_ALLOWED", "true")
    with pytest.raises(TradovateClientError) as exc:
        TradovateClient().authenticate()
    assert exc.value.error_code == "LIVE_BLOCKED"


def test_credentials_not_in_str_or_repr(clean_env):
    """A defence-in-depth check: even if someone logs the client, secrets are not exposed."""
    clean_env.setenv("FIBOKEI_TRADOVATE_USERNAME", "joeuser")
    clean_env.setenv("FIBOKEI_TRADOVATE_PASSWORD", "VERY-SECRET-PWD")
    clean_env.setenv("FIBOKEI_TRADOVATE_CID", "1234")
    clean_env.setenv("FIBOKEI_TRADOVATE_SECRET", "VERY-SECRET-API-KEY")
    c = TradovateClient()
    s = str(c) + repr(c)
    assert "VERY-SECRET-PWD" not in s
    assert "VERY-SECRET-API-KEY" not in s


def test_kill_switch_blocks_tradovate_via_router(clean_env):
    """When the global kill-switch lambda returns True, Tradovate is skipped."""
    from unittest.mock import MagicMock

    from fibokei.execution.broker_symbols import (
        TradovateContractResolver,
        _ContractMapping,
    )
    from fibokei.execution.tradovate_client import TradovateAccount

    client = MagicMock(spec=TradovateClient)
    client.has_credentials = True
    client.list_accounts.return_value = [
        TradovateAccount(account_id=1, name="X", account_type="Demo", user_id=1)
    ]
    resolver = TradovateContractResolver(
        front_month_suffix="M6",
        symbol_map={"US500": _ContractMapping(product_code="ES")},
    )
    adapter = TradovateExecutionAdapter(client=client, resolver=resolver)

    target = ResolvedTarget(
        target_id="tv",
        name="TV Demo",
        broker=BROKER_TRADOVATE,
        environment="demo",
        allocated_capital=5000.0,
        risk_per_trade_pct=1.0,
        is_enabled=True,
        adapter=adapter,
    )
    router = ExecutionRouter(
        mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
        targets=[target],
        kill_switch_check=lambda: True,
    )
    from datetime import datetime, timezone
    plan = NormalisedTradePlan(
        bot_id="b", strategy_id="s", instrument="US500", timeframe="H1",
        direction="LONG", entry_price=5000.0, stop_loss=4990.0,
        take_profit_targets=(5025.0,),
        bar_time=datetime(2026, 5, 8, tzinfo=timezone.utc),
        signal_timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    attempts = router.dispatch_open(plan)
    assert len(attempts) == 1
    assert attempts[0].error_code == "KILL_SWITCH"
    # Client was never called
    client.place_order.assert_not_called()


def test_live_environment_target_blocked_by_router(clean_env):
    """A target with environment='live' and live_allowed=False is skipped at the router gate."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from fibokei.execution.broker_symbols import (
        TradovateContractResolver,
        _ContractMapping,
    )

    fake_adapter = MagicMock()
    fake_adapter._resolver = TradovateContractResolver(
        front_month_suffix="M6",
        symbol_map={"US500": _ContractMapping(product_code="ES")},
    )
    target = ResolvedTarget(
        target_id="tv-live",
        name="Tradovate Live",
        broker=BROKER_TRADOVATE,
        environment=ENV_LIVE,
        allocated_capital=5000.0,
        risk_per_trade_pct=1.0,
        is_enabled=True,
        adapter=fake_adapter,
        live_allowed=False,
    )
    router = ExecutionRouter(
        mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
        targets=[target],
    )
    plan = NormalisedTradePlan(
        bot_id="b", strategy_id="s", instrument="US500", timeframe="H1",
        direction="LONG", entry_price=5000.0, stop_loss=4990.0,
        take_profit_targets=(5025.0,),
        bar_time=datetime(2026, 5, 8, tzinfo=timezone.utc),
        signal_timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    attempts = router.dispatch_open(plan)
    assert attempts[0].error_code == "ENV_BLOCKED"
    fake_adapter.place_order.assert_not_called()
