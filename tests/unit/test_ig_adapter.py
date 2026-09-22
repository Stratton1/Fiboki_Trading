"""The IG adapter is demo-only by construction, and stays that way.

This was V1's single strongest control. A rewrite that quietly dropped it would
be a regression however much else improved.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from fiboki.broker.base import BrokerRejected, BrokerUnavailable, DuplicateClientRef
from fiboki.broker.ig import (
    IG_DEMO_BASE_URL,
    IG_DEMO_HOST,
    IG_LIVE_HOSTS,
    IgAdapter,
    IgConfig,
    IgLiveHostRefused,
    _assert_demo_only,
    live_execution_available,
)
from fiboki.broker.oanda import HttpResponse, RecordedTransport
from fiboki.core.contracts import Order, Position
from fiboki.core.enums import Direction, ExecutionMode, OrderType

NOW = pd.Timestamp("2024-06-03 12:00", tz="UTC")

SESSION = HttpResponse(
    200,
    {"currentAccountId": "ABC123", "accounts": []},
    {"CST": "cst-token", "X-SECURITY-TOKEN": "xst-token"},
)
ACCOUNTS = HttpResponse(
    200,
    {
        "accounts": [
            {
                "accountId": "ABC123",
                "currency": "GBP",
                "balance": {"balance": 10000.0, "profitLoss": 250.0, "deposit": 500.0},
            }
        ]
    },
)
POSITIONS = HttpResponse(
    200,
    {
        "positions": [
            {
                "position": {
                    "dealId": "DIAAAAM5K3XYZ",
                    "direction": "BUY",
                    "size": 2.0,
                    "level": 1.1024,
                    "stopLevel": 1.0994,
                    "limitLevel": 1.1084,
                    "createdDateUTC": "2024-06-03T09:15:02",
                },
                "market": {"epic": "EURUSD"},
            }
        ]
    },
)
WORKING_ORDERS = HttpResponse(
    200,
    {
        "workingOrders": [
            {"workingOrderData": {"dealId": "DIWORK1", "dealReference": "FBK-plan_x"}}
        ]
    },
)
OTC_ACCEPTED = HttpResponse(200, {"dealReference": "FBK-plan_abc"})
CONFIRM = HttpResponse(
    200,
    {
        "dealStatus": "ACCEPTED",
        "dealId": "DIAAAAM5K3ABC",
        "dealReference": "FBK-plan_abc",
        "level": 1.1024,
        "size": 2.0,
    },
)


def _fixtures(**overrides):
    base = {
        "POST /gateway/deal/session": [SESSION],
        "GET /gateway/deal/accounts": [ACCOUNTS],
        "GET /gateway/deal/positions": [POSITIONS],
        "GET /gateway/deal/workingorders": [WORKING_ORDERS],
        "POST /gateway/deal/positions/otc": [OTC_ACCEPTED],
        "GET /gateway/deal/confirms/FBK-plan_abc": [CONFIRM],
    }
    base.update(overrides)
    return base


def _adapter(fixtures=None):
    transport = RecordedTransport(fixtures or _fixtures())
    adapter = IgAdapter(
        IgConfig(identifier="joe", password="pw", api_key="key", account_id="ABC123"),
        transport=transport,
        now_fn=lambda: NOW,
    )
    return adapter, transport


def _order(**kwargs) -> Order:
    params = {
        "plan_id": "plan_abc",
        "instrument": "EURUSD",
        "direction": Direction.LONG,
        "size": 2.0,
        "order_type": OrderType.MARKET,
        "mode": ExecutionMode.DEMO,
        "client_ref": "FBK-plan_abc",
        "stop_loss": 1.0994,
        "take_profit": 1.1084,
    }
    params.update(kwargs)
    return Order(**params)


# ----------------------------------------------- the compile-time control


def test_the_base_url_constant_is_the_demo_host() -> None:
    assert IG_DEMO_BASE_URL.startswith(f"https://{IG_DEMO_HOST}")


def test_the_config_exposes_no_base_url_override() -> None:
    """There is no parameter to point this adapter anywhere else."""
    fields = {f for f in IgConfig.__dataclass_fields__}
    assert "base_url" not in fields
    assert "host" not in fields
    config = IgConfig(identifier="a", password="b", api_key="c")
    assert config.base_url == IG_DEMO_BASE_URL


def test_the_adapter_mode_is_pinned_to_demo() -> None:
    assert IgAdapter.mode is ExecutionMode.DEMO
    assert live_execution_available() is False


@pytest.mark.parametrize("host", sorted(IG_LIVE_HOSTS))
def test_a_live_ig_host_is_refused(host: str) -> None:
    with pytest.raises(IgLiveHostRefused, match="demo-only"):
        _assert_demo_only(f"https://{host}/gateway/deal")


@pytest.mark.parametrize(
    "url",
    [
        "https://demo-api.ig.com.attacker.example/gateway/deal",
        "https://not-demo-api.ig.com/gateway/deal",
        "https://evil.example/demo-api.ig.com",
        "https://ig.com/",
    ],
)
def test_look_alike_hosts_are_refused(url: str) -> None:
    with pytest.raises(IgLiveHostRefused):
        _assert_demo_only(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://demo-api.ig.com/gateway/deal",
        "https://demo-api.ig.com/gateway/deal/",
        "https://demo-api.ig.com:443/gateway/deal",
        "HTTPS://DEMO-API.IG.COM/gateway/deal",
    ],
)
def test_the_demo_host_is_accepted_whatever_the_url_cosmetics(url: str) -> None:
    _assert_demo_only(url)


def test_the_host_assertion_runs_on_every_request_not_only_at_construction() -> None:
    adapter, _ = _adapter()
    adapter.connect()
    # Swap the config for one whose base_url is a live host, as a runtime
    # mutation would. The next request must still refuse.
    class _LiveConfig(IgConfig):
        @property
        def base_url(self) -> str:
            return "https://api.ig.com/gateway/deal"

    adapter.config = _LiveConfig(identifier="a", password="b", api_key="c")
    with pytest.raises(IgLiveHostRefused):
        adapter.account()


def test_no_live_ig_host_appears_anywhere_in_the_module_as_a_request_target() -> None:
    """A structural check: the live hosts exist only in the refusal list."""
    source = Path(IgAdapter.__module__.replace(".", "/"))
    path = Path(__file__).resolve().parents[2] / "src" / f"{source}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    string_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for host in IG_LIVE_HOSTS:
        occurrences = [s for s in string_literals if s == host]
        # Present exactly once each, in IG_LIVE_HOSTS -- i.e. only to be refused.
        assert len(occurrences) == 1, f"{host} appears {len(occurrences)} times"
    assert not any(s.startswith("https://api.ig.com") for s in string_literals)


# ----------------------------------------------------------- behaviour


def test_connect_stores_the_session_tokens() -> None:
    adapter, transport = _adapter()
    health = adapter.connect()
    assert health.connected
    assert health.detail["dormant"] is True
    adapter.account()
    headers = transport.calls[-1]["headers"]
    assert headers["CST"] == "cst-token"
    assert headers["X-SECURITY-TOKEN"] == "xst-token"
    assert headers["X-IG-API-KEY"] == "key"


def test_account_maps_balance_and_profit_to_equity() -> None:
    adapter, _ = _adapter()
    adapter.connect()
    account = adapter.account()
    assert account.balance == pytest.approx(10_000.0)
    assert account.equity == pytest.approx(10_250.0)
    assert account.currency == "GBP"


def test_positions_carry_the_ig_deal_id_as_the_broker_reference() -> None:
    adapter, _ = _adapter()
    adapter.connect()
    position = adapter.positions()[0]
    assert position.venue_ref == "DIAAAAM5K3XYZ"
    assert position.direction is Direction.LONG
    assert position.stop_loss == pytest.approx(1.0994)


def test_the_client_ref_is_transmitted_as_the_deal_reference() -> None:
    adapter, transport = _adapter()
    adapter.connect()
    adapter.place_order(_order())
    body = next(c["body"] for c in transport.calls if c["path"].endswith("/positions/otc"))
    assert body["dealReference"] == "FBK-plan_abc"
    assert body["size"] == 2.0
    assert body["stopLevel"] == pytest.approx(1.0994)
    assert body["limitLevel"] == pytest.approx(1.1084)


def test_a_fill_returns_the_deal_id() -> None:
    adapter, _ = _adapter()
    adapter.connect()
    ack = adapter.place_order(_order())
    assert ack.broker_ref == "DIAAAAM5K3ABC"
    assert ack.fill.filled_size == 2.0


def test_a_rejected_deal_raises() -> None:
    rejected = HttpResponse(
        200, {"dealStatus": "REJECTED", "reason": "MARKET_CLOSED", "dealId": ""}
    )
    adapter, _ = _adapter(
        _fixtures(**{"GET /gateway/deal/confirms/FBK-plan_abc": [rejected]})
    )
    adapter.connect()
    with pytest.raises(BrokerRejected, match="MARKET_CLOSED"):
        adapter.place_order(_order())


def test_a_missing_confirmation_is_UNKNOWN_not_a_rejection() -> None:
    adapter, _ = _adapter(
        _fixtures(**{"GET /gateway/deal/confirms/FBK-plan_abc": [HttpResponse(404, {})]})
    )
    adapter.connect()
    ack = adapter.place_order(_order())
    assert ack.status.value == "unknown"
    assert "reconciliation" in ack.message


def test_a_duplicate_deal_reference_raises() -> None:
    adapter, _ = _adapter(
        _fixtures(**{"POST /gateway/deal/positions/otc": [HttpResponse(409, {})]})
    )
    adapter.connect()
    with pytest.raises(DuplicateClientRef):
        adapter.place_order(_order())


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transport_failures_are_unavailable_not_rejections(status: int) -> None:
    adapter, _ = _adapter(
        _fixtures(**{"POST /gateway/deal/positions/otc": [HttpResponse(status, {})]})
    )
    adapter.connect()
    with pytest.raises(BrokerUnavailable):
        adapter.place_order(_order())


def test_closing_without_a_deal_id_is_refused() -> None:
    adapter, _ = _adapter()
    adapter.connect()
    orphan = Position(
        instrument="EURUSD", direction=Direction.LONG, size=1.0,
        entry_price=1.1, entry_time=NOW, stop_loss=1.09, venue_ref=None,
    )
    with pytest.raises(BrokerRejected, match="dealId"):
        adapter.close_position(orphan, client_ref="x")


def test_working_orders_expose_the_deal_reference() -> None:
    adapter, _ = _adapter()
    adapter.connect()
    order = adapter.orders()[0]
    assert order.broker_ref == "DIWORK1"
    assert order.client_ref == "FBK-plan_x"
