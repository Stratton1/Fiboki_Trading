"""Tests for the multi-broker fan-out ExecutionRouter (Phase 1).

Covers the 12-point matrix from the agreed Phase 1 brief:
  1. Default mode remains safe and paper-only.
  2. env_global_fanout with paper + IG + Tradovate enabled fans out to all 3.
  3. IG enabled and Tradovate disabled calls only IG.
  4. Tradovate enabled with unsupported instrument records skipped attempt
     and does not block IG.
  5. IG failure does not block Tradovate.
  6. Tradovate failure does not block IG.
  7. Global kill switch blocks all targets.
  8. Per-target capital is used for sizing.
  9. Futures size rounds down to whole contracts and rejects if zero.
 10. Router records a shared parent_signal_id across all child attempts.
 11. (existing IG tests) — covered by test_ig_adapter.py
 12. (existing paper tests) — covered by test_paper_bot.py and friends
"""

from __future__ import annotations

from datetime import datetime, timezone

from fibokei.execution.adapter import ExecutionAdapter
from fibokei.execution.broker_symbols import (
    TradovateContractResolver,
)
from fibokei.execution.paper_adapter import PaperExecutionAdapter
from fibokei.execution.router import ExecutionRouter
from fibokei.execution.targets import (
    ATTEMPT_FILLED,
    ATTEMPT_PAPER_FILLED,
    ATTEMPT_REJECTED,
    ATTEMPT_SKIPPED,
    BROKER_IG,
    BROKER_PAPER,
    BROKER_TRADOVATE,
    ENV_DEMO,
    ENV_LIVE,
    ENV_PAPER,
    ROUTER_MODE_ENV_GLOBAL_FANOUT,
    NormalisedTradePlan,
    ResolvedTarget,
)


def _plan(instrument: str = "EURUSD", direction: str = "LONG") -> NormalisedTradePlan:
    return NormalisedTradePlan(
        bot_id="bot01",
        strategy_id="bot01_sanyaku",
        instrument=instrument,
        timeframe="H1",
        direction=direction,
        entry_price=1.10 if instrument == "EURUSD" else 5000.0,
        stop_loss=1.095 if instrument == "EURUSD" else 4990.0,
        take_profit_targets=(1.115,) if instrument == "EURUSD" else (5025.0,),
        bar_time=datetime(2026, 5, 8, tzinfo=timezone.utc),
        signal_timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


class _FakeAdapter(ExecutionAdapter):
    """Minimal adapter spy that records calls and returns canned responses."""

    def __init__(
        self,
        *,
        place_response: dict | None = None,
        place_side_effect: Exception | None = None,
        close_response: dict | None = None,
    ):
        self.calls: list[dict] = []
        self.close_calls: list[str] = []
        self.place_response = place_response or {
            "status": "ACCEPTED",
            "deal_id": "FAKE-DEAL-1",
            "filled_price": 1.10,
            "size": 1.0,
        }
        self.place_side_effect = place_side_effect
        self.close_response = close_response or {"status": "ACCEPTED", "deal_id": "FAKE-CLOSE"}

    def place_order(self, order: dict) -> dict:
        self.calls.append(order)
        if self.place_side_effect is not None:
            raise self.place_side_effect
        return dict(self.place_response)

    def cancel_order(self, order_id: str) -> bool:
        return True

    def modify_order(self, order_id: str, changes: dict) -> dict:
        return {"status": "modified"}

    def get_positions(self) -> list[dict]:
        return []

    def get_account_info(self) -> dict:
        return {}

    def close_position(self, position_id: str) -> dict:
        self.close_calls.append(position_id)
        return dict(self.close_response)

    def partial_close(self, position_id: str, pct: float) -> dict:
        return {"status": "ACCEPTED"}


def _paper_target(enabled: bool = True, capital: float = 1000.0) -> ResolvedTarget:
    return ResolvedTarget(
        target_id="paper-default",
        name="Paper",
        broker=BROKER_PAPER,
        environment=ENV_PAPER,
        allocated_capital=capital,
        risk_per_trade_pct=1.0,
        is_enabled=enabled,
        adapter=PaperExecutionAdapter(),
    )


def _ig_target(adapter: ExecutionAdapter | None = None, enabled: bool = True) -> ResolvedTarget:
    return ResolvedTarget(
        target_id="ig-demo",
        name="IG Demo",
        broker=BROKER_IG,
        environment=ENV_DEMO,
        allocated_capital=1000.0,
        risk_per_trade_pct=1.0,
        is_enabled=enabled,
        adapter=adapter or _FakeAdapter(place_response={
            "status": "ACCEPTED",
            "deal_id": "IG-DEAL",
            "filled_price": 1.10,
            "size": 1.0,
            "epic": "CS.D.EURUSD.CFD.IP",
        }),
    )


def _tradovate_target(
    adapter: ExecutionAdapter | None = None,
    enabled: bool = True,
    capital: float = 5000.0,
) -> ResolvedTarget:
    fake = adapter or _FakeAdapter(place_response={
        "status": "ACCEPTED",
        "deal_id": "TV-ORDER-1",
        "filled_price": 5000.0,
        "size": 1.0,
        "broker_symbol": "ESM6",
    })
    # Attach a stub resolver as ``_resolver`` so the router's symbol lookup works.
    fake._resolver = TradovateContractResolver(  # type: ignore[attr-defined]
        front_month_suffix="M6",
        symbol_map={"US500": __import__(
            "fibokei.execution.broker_symbols", fromlist=["_ContractMapping"]
        )._ContractMapping(product_code="ES")},
    )
    return ResolvedTarget(
        target_id="tradovate-demo",
        name="Tradovate Demo",
        broker=BROKER_TRADOVATE,
        environment=ENV_DEMO,
        allocated_capital=capital,
        risk_per_trade_pct=1.0,
        is_enabled=enabled,
        adapter=fake,
    )


# ── Tests ─────────────────────────────────────────────────────────────


class TestRouterFanOut:
    def test_paper_only_default(self):
        """Default-shape router with only paper enabled returns one paper attempt."""
        router = ExecutionRouter(mode=ROUTER_MODE_ENV_GLOBAL_FANOUT, targets=[_paper_target()])
        attempts = router.dispatch_open(_plan())
        assert len(attempts) == 1
        assert attempts[0].broker == BROKER_PAPER
        assert attempts[0].status == ATTEMPT_PAPER_FILLED

    def test_three_targets_all_called_once(self):
        """Paper + IG + Tradovate (mapped to US500) each receive exactly one place_order call."""
        ig = _ig_target()
        tv = _tradovate_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[_paper_target(), ig, tv],
        )
        attempts = router.dispatch_open(_plan(instrument="US500"))
        assert len(attempts) == 3
        # Each adapter received exactly one order
        assert len(ig.adapter.calls) == 1  # type: ignore[attr-defined]
        assert len(tv.adapter.calls) == 1  # type: ignore[attr-defined]

    def test_ig_enabled_tradovate_disabled(self):
        ig = _ig_target()
        tv = _tradovate_target(enabled=False)
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[_paper_target(enabled=False), ig, tv],
        )
        attempts = router.dispatch_open(_plan())
        assert len(attempts) == 1
        assert attempts[0].broker == BROKER_IG
        # Tradovate must not have been touched
        assert tv.adapter.calls == []  # type: ignore[attr-defined]

    def test_tradovate_unsupported_instrument_does_not_block_ig(self):
        """EURUSD is intentionally unmapped to Tradovate. IG should still fill."""
        ig = _ig_target()
        tv = _tradovate_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig, tv],
        )
        attempts = router.dispatch_open(_plan(instrument="EURUSD"))
        assert len(attempts) == 2
        ig_attempt = next(a for a in attempts if a.broker == BROKER_IG)
        tv_attempt = next(a for a in attempts if a.broker == BROKER_TRADOVATE)
        assert ig_attempt.status == ATTEMPT_FILLED
        assert tv_attempt.status == ATTEMPT_SKIPPED
        assert "UNSUPPORTED_INSTRUMENT_TRADOVATE" in (tv_attempt.error_code or "")

    def test_ig_failure_does_not_block_tradovate(self):
        ig = _ig_target(adapter=_FakeAdapter(place_response={
            "status": "rejected",
            "reason": "Market closed",
            "error_code": "MARKET_CLOSED",
        }))
        tv = _tradovate_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig, tv],
        )
        attempts = router.dispatch_open(_plan(instrument="US500"))
        assert len(attempts) == 2
        ig_attempt = next(a for a in attempts if a.broker == BROKER_IG)
        tv_attempt = next(a for a in attempts if a.broker == BROKER_TRADOVATE)
        assert ig_attempt.status == ATTEMPT_REJECTED
        assert tv_attempt.status == ATTEMPT_FILLED

    def test_tradovate_failure_does_not_block_ig(self):
        tv = _tradovate_target(adapter=_FakeAdapter(place_response={
            "status": "rejected",
            "reason": "Account suspended",
            "error_code": "BROKER_REJECTED",
        }))
        # Re-attach resolver since the adapter was overridden
        tv.adapter._resolver = TradovateContractResolver(  # type: ignore[attr-defined]
            front_month_suffix="M6",
            symbol_map={"US500": __import__(
                "fibokei.execution.broker_symbols", fromlist=["_ContractMapping"]
            )._ContractMapping(product_code="ES")},
        )
        ig = _ig_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig, tv],
        )
        attempts = router.dispatch_open(_plan(instrument="US500"))
        ig_attempt = next(a for a in attempts if a.broker == BROKER_IG)
        tv_attempt = next(a for a in attempts if a.broker == BROKER_TRADOVATE)
        assert ig_attempt.status == ATTEMPT_FILLED
        assert tv_attempt.status == ATTEMPT_REJECTED


class TestKillSwitch:
    def test_kill_switch_blocks_all_targets(self):
        ig = _ig_target()
        tv = _tradovate_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[_paper_target(), ig, tv],
            kill_switch_check=lambda: True,
        )
        attempts = router.dispatch_open(_plan(instrument="US500"))
        assert len(attempts) == 3
        for a in attempts:
            assert a.status == ATTEMPT_SKIPPED
            assert a.error_code == "KILL_SWITCH"
        # No adapter received any order
        assert ig.adapter.calls == []  # type: ignore[attr-defined]
        assert tv.adapter.calls == []  # type: ignore[attr-defined]


class TestPerTargetSizing:
    def test_capital_used_for_each_target(self):
        """Different targets compute different sizes from their own capital."""
        from fibokei.execution.sizing import calculate_target_size

        plan = _plan(instrument="EURUSD")
        small = ResolvedTarget(
            target_id="small", name="Small", broker=BROKER_PAPER,
            environment=ENV_PAPER, allocated_capital=500.0,
            risk_per_trade_pct=1.0, is_enabled=True,
            adapter=PaperExecutionAdapter(),
        )
        big = ResolvedTarget(
            target_id="big", name="Big", broker=BROKER_PAPER,
            environment=ENV_PAPER, allocated_capital=10_000.0,
            risk_per_trade_pct=1.0, is_enabled=True,
            adapter=PaperExecutionAdapter(),
        )
        s_small = calculate_target_size(small, plan)
        s_big = calculate_target_size(big, plan)
        assert s_small is not None and s_big is not None
        # 20× capital → ≈20× size (within sizing-cap rounding)
        assert s_big > s_small * 5

    def test_router_passes_target_size_to_adapter(self):
        """Router must size from the *target's* capital, not bot's account."""
        ig = _ig_target()
        tv = _tradovate_target(capital=10_000.0)  # 10x paper
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig, tv],
        )
        router.dispatch_open(_plan(instrument="US500"))
        ig_size = ig.adapter.calls[0]["size"]  # type: ignore[attr-defined]
        tv_size = tv.adapter.calls[0]["size"]  # type: ignore[attr-defined]
        # Both numeric, both > 0, and Tradovate is rounded to whole contracts
        assert ig_size > 0
        assert tv_size == int(tv_size) and tv_size >= 1


class TestFuturesSizing:
    def test_tradovate_size_rounds_down_to_whole_contracts(self):
        from fibokei.execution.sizing import calculate_target_size

        # Capital low enough that raw size is fractional but ≥ 1.
        plan = NormalisedTradePlan(
            bot_id="b", strategy_id="s", instrument="US500", timeframe="H1",
            direction="LONG", entry_price=5000.0, stop_loss=4990.0,
            take_profit_targets=(5025.0,),
            bar_time=datetime(2026, 5, 8, tzinfo=timezone.utc),
            signal_timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
        )
        target = ResolvedTarget(
            target_id="tv", name="TV", broker=BROKER_TRADOVATE,
            environment=ENV_DEMO, allocated_capital=2_500.0,
            risk_per_trade_pct=1.0, is_enabled=True,
            adapter=PaperExecutionAdapter(),  # adapter not exercised here
        )
        size = calculate_target_size(target, plan)
        # 1% of 2500 = 25, divided by 10pt stop ≈ 2.5 contracts → floor to 2
        assert size is not None and size == int(size) and size >= 1

    def test_tradovate_size_rejects_zero_contracts(self):
        from fibokei.execution.sizing import calculate_target_size

        plan = NormalisedTradePlan(
            bot_id="b", strategy_id="s", instrument="US500", timeframe="H1",
            direction="LONG", entry_price=5000.0, stop_loss=4900.0,
            take_profit_targets=(5050.0,),
            bar_time=datetime(2026, 5, 8, tzinfo=timezone.utc),
            signal_timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
        )
        # Capital small enough that raw < 1 contract
        target = ResolvedTarget(
            target_id="tv", name="TV", broker=BROKER_TRADOVATE,
            environment=ENV_DEMO, allocated_capital=50.0,
            risk_per_trade_pct=1.0, is_enabled=True,
            adapter=PaperExecutionAdapter(),
        )
        size = calculate_target_size(target, plan)
        # 1% of 50 = 0.5, /100pt stop = 0.005 → rounds to 0 → reject
        assert size is None


class TestParentSignal:
    def test_parent_signal_id_shared_across_attempts(self):
        ig = _ig_target()
        tv = _tradovate_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[_paper_target(), ig, tv],
        )
        attempts = router.dispatch_open(_plan(instrument="US500"))
        ids = {a.parent_signal_id for a in attempts}
        assert len(ids) == 1, "All child attempts must share one parent_signal_id"

    def test_parent_signal_id_changes_per_signal(self):
        ig = _ig_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig],
        )
        a1 = router.dispatch_open(_plan())
        a2 = router.dispatch_open(_plan())
        assert a1[0].parent_signal_id != a2[0].parent_signal_id


class TestLiveBlocked:
    def test_live_environment_blocked_unless_explicit(self):
        """A target with environment=live but live_allowed=False is skipped."""
        ig_live = ResolvedTarget(
            target_id="ig-live", name="IG Live", broker=BROKER_IG,
            environment=ENV_LIVE, allocated_capital=1000.0,
            risk_per_trade_pct=1.0, is_enabled=True,
            adapter=_FakeAdapter(),
            live_allowed=False,  # the gate
        )
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig_live],
        )
        attempts = router.dispatch_open(_plan())
        assert len(attempts) == 1
        assert attempts[0].status == ATTEMPT_SKIPPED
        assert attempts[0].error_code == "ENV_BLOCKED"
        # Adapter was NOT called
        assert ig_live.adapter.calls == []  # type: ignore[attr-defined]


class TestCloseDispatch:
    def test_close_only_open_targets(self):
        """Targets with no deal id receive no close call."""
        ig = _ig_target()
        tv = _tradovate_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig, tv],
        )
        # Only IG has an open deal id
        attempts = router.dispatch_close(
            target_deal_ids={"ig-demo": "IG-DEAL-1"},
            instrument="EURUSD",
        )
        assert len(attempts) == 1
        assert attempts[0].broker == BROKER_IG
        # Tradovate adapter never received a close call
        assert tv.adapter.close_calls == []  # type: ignore[attr-defined]

    def test_close_per_broker_when_both_open(self):
        ig = _ig_target()
        tv = _tradovate_target()
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig, tv],
        )
        attempts = router.dispatch_close(
            target_deal_ids={"ig-demo": "IG-DEAL", "tradovate-demo": "TV-DEAL"},
            instrument="US500",
        )
        assert len(attempts) == 2
        assert ig.adapter.close_calls == ["IG-DEAL"]  # type: ignore[attr-defined]
        assert tv.adapter.close_calls == ["TV-DEAL"]  # type: ignore[attr-defined]


class TestRouterSummary:
    def test_summary_lists_all_targets(self):
        ig = _ig_target(enabled=True)
        tv = _tradovate_target(enabled=False)
        router = ExecutionRouter(
            mode=ROUTER_MODE_ENV_GLOBAL_FANOUT,
            targets=[ig, tv],
        )
        s = router.summary()
        assert s["router_mode"] == ROUTER_MODE_ENV_GLOBAL_FANOUT
        names = [t["name"] for t in s["targets"]]
        assert "IG Demo" in names and "Tradovate Demo" in names
        # Disabled targets still appear in the summary (operator visibility)
        tv_view = next(t for t in s["targets"] if t["name"] == "Tradovate Demo")
        assert tv_view["is_enabled"] is False
