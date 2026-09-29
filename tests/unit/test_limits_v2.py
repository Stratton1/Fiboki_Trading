"""limits_v2: an asymmetric event blackout at decision time, and margin after the trade.

Audit P2-1: the v1 blackout is symmetric +/-15 minutes around the gateway's
``now``, which a replay sets to the bar's OPEN. An H1 entry at 12:10 with NFP
at 12:30 passes and is held through the release; on H4 the check runs about
four hours early. Audit P2-2: margin utilisation is measured before the trade,
so 45% in use plus a position needing 15% passes a 50% limit.

v2 sets are published beside v1, never over it: a stored decision names its
limit set, so v1 must keep meaning what it meant.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction
from fiboki.risk.gateway import MarketView, RiskGateway
from fiboki.risk.limits import (
    CONSERVATIVE_LIMITS,
    CONSERVATIVE_LIMITS_V2,
    DEFAULT_LIMITS,
    DEFAULT_LIMITS_V2,
    LIMIT_SETS,
    PAPER_LIMITS,
    PAPER_LIMITS_V2,
    LimitSet,
)
from tests.exec_fixtures import (
    healthy_market,
    make_account,
    make_context,
    make_plan,
    make_snapshot,
)


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


# ------------------------------------------------------------- the sets


def test_v1_fingerprints_are_byte_identical_to_before_v2_existed() -> None:
    """Computed on the tree before the v2 fields were added (HEAD 8896b3f)."""
    assert DEFAULT_LIMITS.fingerprint() == "d1b2fc6c0eb62d31"
    assert PAPER_LIMITS.fingerprint() == "a0db92dc13808b2b"
    assert CONSERVATIVE_LIMITS.fingerprint() == "36292f9b74f7da0f"


def test_v2_sets_are_registered_and_differ_only_in_the_v2_rules() -> None:
    for v1, v2 in (
        (DEFAULT_LIMITS, DEFAULT_LIMITS_V2),
        (PAPER_LIMITS, PAPER_LIMITS_V2),
        (CONSERVATIVE_LIMITS, CONSERVATIVE_LIMITS_V2),
    ):
        assert LIMIT_SETS[v2.version] is v2
        assert v2.fingerprint() != v1.fingerprint()
        a, b = v1.as_dict(), v2.as_dict()
        changed = {k for k in a if a[k] != b[k]} - {"version", "notes"}
        assert changed == {
            "event_blackout_pre_minutes",
            "event_blackout_post_minutes",
            "margin_utilisation_after_trade",
        }
    assert DEFAULT_LIMITS_V2.event_blackout_pre_minutes == 30.0
    assert DEFAULT_LIMITS_V2.event_blackout_post_minutes == 15.0


def test_half_an_asymmetric_window_is_refused() -> None:
    with pytest.raises(ValueError, match="both"):
        LimitSet(version="half", event_blackout_pre_minutes=30.0)


# ----------------------------------------------------------- the blackout


def _signal(bar_time: str, timeframe: str) -> Signal:
    return Signal(
        strategy_id="ichimoku_a", instrument="EURUSD", timeframe=timeframe,
        direction=Direction.LONG, bar_time=T(bar_time),
        reference_price=1.1000, stop_price=1.0970, take_profit_prices=(1.1060,),
    )


def _blocked(limits: LimitSet, *, bar_time: str, timeframe: str, now: str, event: str) -> bool:
    plan = make_plan(_signal(bar_time, timeframe))
    base = healthy_market(now=T(now))
    market = MarketView(
        mid_price=base.mid_price, spread_price=base.spread_price,
        quote_time=base.quote_time, last_bar_time=base.last_bar_time,
        market_open=True, event_times=(T(event),),
    )
    ctx = make_context(plan, now=T(now), limits=limits, market=market)
    return any(r.startswith("event_blackout") for r in RiskGateway().evaluate(ctx).reasons)


def test_the_audit_case_an_h1_entry_twenty_minutes_before_nfp() -> None:
    """H1 bar 11:00-12:00, decided at 12:10, NFP at 12:30.

    v1: |12:30 - 12:10| = 20 min > 15, passes, and the position is held through
    the release. v2: window [12:10 - 15m, 12:10 + max(30m, 60m)] =
    [11:55, 13:10] contains 12:30, blocks.
    """
    kw = dict(bar_time="2024-06-07 11:00", timeframe="H1", now="2024-06-07 12:10",
              event="2024-06-07 12:30")
    assert not _blocked(DEFAULT_LIMITS, **kw)
    assert _blocked(DEFAULT_LIMITS_V2, **kw)


def test_an_h4_replay_measures_from_the_bar_close_not_its_open() -> None:
    """H4 bar 08:00-12:00, replay ``now`` = 08:00 (the bar's open stamp).

    Event 12:20: v1 compares with 08:00, 4h20m away, passes. v2's decision time
    is the close, 12:00: window [11:45, 16:00], blocks.
    Event 08:05: v1 blocks on a release that was over three hours before the
    decision; v2 does not (08:05 < 11:45).
    """
    kw = dict(bar_time="2024-06-07 08:00", timeframe="H4", now="2024-06-07 08:00")
    assert not _blocked(DEFAULT_LIMITS, event="2024-06-07 12:20", **kw)
    assert _blocked(DEFAULT_LIMITS_V2, event="2024-06-07 12:20", **kw)
    assert _blocked(DEFAULT_LIMITS, event="2024-06-07 08:05", **kw)
    assert not _blocked(DEFAULT_LIMITS_V2, event="2024-06-07 08:05", **kw)


@pytest.mark.parametrize(
    ("event", "blocked"),
    [
        ("2024-06-07 11:44", False),  # 16 min before the decision: outside post=15
        ("2024-06-07 11:45", True),   # exactly post=15 before: inside (closed)
        ("2024-06-07 13:00", True),   # 60 min ahead: inside max(pre=30, one bar=60)
        ("2024-06-07 13:01", False),  # 61 min ahead: outside
    ],
)
def test_the_v2_window_edges(event: str, blocked: bool) -> None:
    """H1 bar 11:00-12:00 decided at its close: [11:45, 13:00]."""
    assert _blocked(
        DEFAULT_LIMITS_V2, bar_time="2024-06-07 11:00", timeframe="H1",
        now="2024-06-07 12:00", event=event,
    ) is blocked


# ------------------------------------------------------------- margin


def test_margin_is_measured_after_the_trade_under_v2() -> None:
    """Equity 100,000, 45,000 margin in use (45%). The plan's notional is
    size * 1.1000 on EURUSD at 30:1, so its margin is notional / 30. v1 sees
    45% < 50% and passes; v2 sees (45,000 + notional/30) / 100,000."""
    plan = make_plan()
    notional = plan.size * 1.1000
    after = (45_000.0 + notional / 30.0) / 100_000.0 * 100.0
    assert after > 50.0, "the fixture must push utilisation over the limit"
    snap = make_snapshot(account=make_account(100_000.0, margin_used=45_000.0))

    def reasons(limits: LimitSet) -> list[str]:
        ctx = make_context(plan, limits=limits, snapshot=snap)
        return [r for r in RiskGateway().evaluate(ctx).reasons if "margin" in r]

    assert reasons(DEFAULT_LIMITS) == []
    (reason,) = reasons(DEFAULT_LIMITS_V2)
    assert reason == f"margin_utilisation_after_trade:{after:.2f}%>=50.0%"


def test_v2_margin_passes_when_the_trade_fits() -> None:
    plan = make_plan()
    snap = make_snapshot(account=make_account(100_000.0, margin_used=10_000.0))
    ctx = make_context(plan, limits=DEFAULT_LIMITS_V2, snapshot=snap)
    assert not [r for r in RiskGateway().evaluate(ctx).reasons if "margin" in r]
