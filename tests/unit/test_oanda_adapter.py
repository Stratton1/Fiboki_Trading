"""OANDA v20 adapter, exercised entirely against recorded fixtures.

No credentials exist and none are needed: every response below is constructed
from the documented v20 REST shapes, and the transport is injected.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.broker.base import (
    BrokerRejected,
    BrokerUnavailable,
    DuplicateClientRef,
    OrderStatus,
)
from fiboki.broker.oanda import (
    OANDA_LIVE_HOST,
    OANDA_LIVE_HOST_COMPILED_IN,
    OANDA_LIVE_RUNTIME_TOKEN,
    OANDA_PRACTICE_HOST,
    HttpResponse,
    OandaAdapter,
    OandaConfig,
    OandaHostError,
    RateLimiter,
    RecordedTransport,
    from_oanda_instrument,
    to_oanda_instrument,
)
from fiboki.core.contracts import Order, Position
from fiboki.core.enums import Direction, ExecutionMode, OrderType

ACCOUNT = "001-004-1234567-001"
TOKEN = "fixture-token-not-a-credential"
NOW = pd.Timestamp("2024-06-03 12:00", tz="UTC")


def _path(suffix: str = "") -> str:
    return f"/v3/accounts/{ACCOUNT}{suffix}"


ACCOUNT_SUMMARY = HttpResponse(
    200,
    {
        "account": {
            "id": ACCOUNT,
            "currency": "GBP",
            "balance": "50123.4500",
            "NAV": "50350.1200",
            "marginUsed": "1200.0000",
            "unrealizedPL": "226.6700",
            "pl": "1123.4500",
            "openTradeCount": 2,
        },
        "lastTransactionID": "512",
    },
)

OPEN_TRADES = HttpResponse(
    200,
    {
        "trades": [
            {
                "id": "1471",
                "instrument": "EUR_USD",
                "price": "1.10235",
                "openTime": "2024-06-03T09:15:02.000000000Z",
                "currentUnits": "10000",
                "clientExtensions": {"id": "FBK-plan_abc"},
                "stopLossOrder": {"price": "1.09935"},
                "takeProfitOrder": {"price": "1.10835"},
            },
            {
                "id": "1472",
                "instrument": "XAU_USD",
                "price": "2310.55",
                "openTime": "2024-06-03T10:01:00.000000000Z",
                "currentUnits": "-5",
                "clientExtensions": {"id": "FBK-plan_def"},
            },
        ]
    },
)

FILL_RESPONSE = HttpResponse(
    201,
    {
        "orderCreateTransaction": {
            "id": "1480",
            "time": "2024-06-03T12:00:00.000000000Z",
            "type": "MARKET_ORDER",
            "instrument": "EUR_USD",
            "units": "10000",
        },
        "orderFillTransaction": {
            "id": "1481",
            "time": "2024-06-03T12:00:00.250000000Z",
            "type": "ORDER_FILL",
            "instrument": "EUR_USD",
            "units": "10000",
            "price": "1.10240",
            "commission": "0.0000",
            "tradeOpened": {"tradeID": "1482", "units": "10000"},
        },
        "lastTransactionID": "1482",
    },
)

CANCEL_RESPONSE = HttpResponse(
    201,
    {
        "orderCreateTransaction": {"id": "1490", "time": "2024-06-03T12:00:00.000000000Z"},
        "orderCancelTransaction": {"id": "1491", "reason": "MARKET_HALTED"},
    },
)

PENDING_ORDERS = HttpResponse(
    200,
    {
        "orders": [
            {
                "id": "1500",
                "createTime": "2024-06-03T11:00:00.000000000Z",
                "type": "LIMIT",
                "instrument": "GBP_USD",
                "clientExtensions": {"id": "FBK-plan_ghi"},
            }
        ]
    },
)

INSTRUMENTS = HttpResponse(
    200,
    {
        "instruments": [
            {
                "name": "EUR_USD",
                "type": "CURRENCY",
                "displayName": "EUR/USD",
                "pipLocation": -4,
                "displayPrecision": 5,
                "tradeUnitsPrecision": 0,
                "minimumTradeSize": "1",
                "marginRate": "0.0333",
            }
        ]
    },
)

CLOSE_RESPONSE = HttpResponse(
    200,
    {
        "orderFillTransaction": {
            "id": "1600",
            "time": "2024-06-03T13:00:00.000000000Z",
            "price": "1.10500",
            "units": "-10000",
        }
    },
)


def _fixtures(**overrides) -> dict[str, list[HttpResponse]]:
    base = {
        f"GET {_path('/summary')}": [ACCOUNT_SUMMARY],
        f"GET {_path('/openTrades')}": [OPEN_TRADES],
        f"GET {_path('/pendingOrders')}": [PENDING_ORDERS],
        f"GET {_path('/instruments')}": [INSTRUMENTS],
        f"POST {_path('/orders')}": [FILL_RESPONSE],
        f"PUT {_path('/trades/1471/close')}": [CLOSE_RESPONSE],
    }
    base.update(overrides)
    return base


def _adapter(fixtures=None, **cfg) -> tuple[OandaAdapter, RecordedTransport]:
    transport = RecordedTransport(fixtures or _fixtures())
    params = {
        "account_id": ACCOUNT,
        "api_token": TOKEN,
        "base_url": f"https://{OANDA_PRACTICE_HOST}",
        "mode": ExecutionMode.DEMO,
    }
    params.update(cfg)
    adapter = OandaAdapter(
        OandaConfig(**params), transport=transport, env={}, now_fn=lambda: NOW
    )
    return adapter, transport


def _order(**kwargs) -> Order:
    params = {
        "plan_id": "plan_abc",
        "instrument": "EURUSD",
        "direction": Direction.LONG,
        "size": 10_000.0,
        "order_type": OrderType.MARKET,
        "mode": ExecutionMode.DEMO,
        "client_ref": "FBK-plan_abc",
        "stop_loss": 1.09935,
        "take_profit": 1.10835,
    }
    params.update(kwargs)
    return Order(**params)


# ------------------------------------------------------------- host safety


def test_the_live_host_is_not_compiled_in() -> None:
    assert OANDA_LIVE_HOST_COMPILED_IN is False


def test_the_practice_host_is_accepted() -> None:
    adapter, _ = _adapter()
    assert adapter.config.host == OANDA_PRACTICE_HOST


@pytest.mark.parametrize("suffix", ["", "/", ":443/", "/v3"])
def test_the_live_host_is_refused_whatever_the_url_cosmetics(suffix: str) -> None:
    """V1's live gate was a string comparison a trailing slash defeated."""
    with pytest.raises(OandaHostError, match="LIVE host"):
        _adapter(base_url=f"https://{OANDA_LIVE_HOST}{suffix}")


@pytest.mark.parametrize(
    "host",
    [
        "api-fxpractice.oanda.com.attacker.example",
        "api-fxtrade.oanda.com.attacker.example",
        "oanda.com",
        "localhost",
    ],
)
def test_an_unknown_host_is_refused_outright(host: str) -> None:
    with pytest.raises(OandaHostError, match="neither"):
        _adapter(base_url=f"https://{host}")


def test_a_url_with_no_hostname_is_refused() -> None:
    with pytest.raises(OandaHostError):
        _adapter(base_url="not-a-url")


def test_the_build_constant_alone_does_not_unlock_the_live_host() -> None:
    with pytest.raises(OandaHostError) as excinfo:
        OandaAdapter(
            OandaConfig(
                account_id=ACCOUNT, api_token=TOKEN,
                base_url=f"https://{OANDA_LIVE_HOST}",
                mode=ExecutionMode.LIVE, live_host_compiled_in=True,
            ),
            transport=RecordedTransport({}),
            env={},
        )
    assert "FIBOKI_OANDA_LIVE_RUNTIME" in str(excinfo.value)


def test_the_runtime_token_alone_does_not_unlock_the_live_host() -> None:
    with pytest.raises(OandaHostError) as excinfo:
        OandaAdapter(
            OandaConfig(
                account_id=ACCOUNT, api_token=TOKEN,
                base_url=f"https://{OANDA_LIVE_HOST}", mode=ExecutionMode.LIVE,
            ),
            transport=RecordedTransport({}),
            env={"FIBOKI_OANDA_LIVE_RUNTIME": OANDA_LIVE_RUNTIME_TOKEN},
        )
    assert "OANDA_LIVE_HOST_COMPILED_IN" in str(excinfo.value)


def test_a_demo_mode_adapter_cannot_use_the_live_host_even_fully_armed() -> None:
    with pytest.raises(OandaHostError, match="serves LIVE only"):
        OandaAdapter(
            OandaConfig(
                account_id=ACCOUNT, api_token=TOKEN,
                base_url=f"https://{OANDA_LIVE_HOST}", mode=ExecutionMode.DEMO,
                live_host_compiled_in=True,
            ),
            transport=RecordedTransport({}),
            env={"FIBOKI_OANDA_LIVE_RUNTIME": OANDA_LIVE_RUNTIME_TOKEN},
        )


# ------------------------------------------------------------------- auth


def test_every_request_carries_the_bearer_token_and_datetime_format() -> None:
    adapter, transport = _adapter()
    adapter.connect()
    adapter.positions()
    assert transport.calls
    for call in transport.calls:
        assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert call["headers"]["Accept-Datetime-Format"] == "RFC3339"
        assert call["headers"]["Content-Type"] == "application/json"


def test_an_auth_failure_is_a_rejection_not_an_outage() -> None:
    adapter, _ = _adapter(_fixtures(**{f"POST {_path('/orders')}": [HttpResponse(401, {})]}))
    with pytest.raises(BrokerRejected, match="auth"):
        adapter.place_order(_order())


# ----------------------------------------------------- instrument mapping


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        ("EURUSD", "EUR_USD"),
        ("GBPJPY", "GBP_JPY"),
        ("XAUUSD", "XAU_USD"),
        ("US500", "SPX500_USD"),
        ("UK100", "UK100_GBP"),
        ("WTIUSD", "WTICO_USD"),
    ],
)
def test_instrument_mapping_round_trips(ours: str, theirs: str) -> None:
    assert to_oanda_instrument(ours) == theirs
    assert from_oanda_instrument(theirs) == ours


def test_an_unmapped_instrument_raises_rather_than_guessing() -> None:
    with pytest.raises(KeyError):
        to_oanda_instrument("NOTREAL")


def test_market_spec_returns_our_registry_and_cross_checks_the_venue() -> None:
    adapter, _ = _adapter()
    spec = adapter.market_spec("EURUSD")
    assert spec.symbol == "EURUSD"
    assert spec.pip_size == 0.0001


def test_a_pip_size_disagreement_raises_rather_than_picking_a_side() -> None:
    wrong = HttpResponse(200, {"instruments": [{"name": "EUR_USD", "pipLocation": -2}]})
    adapter, _ = _adapter(_fixtures(**{f"GET {_path('/instruments')}": [wrong]}))
    with pytest.raises(BrokerRejected, match="disagrees"):
        adapter.market_spec("EURUSD")


# ------------------------------------------------------------- account


def test_account_reads_nav_as_equity() -> None:
    adapter, _ = _adapter()
    account = adapter.account()
    assert account.balance == pytest.approx(50123.45)
    assert account.equity == pytest.approx(50350.12)
    assert account.currency == "GBP"
    assert account.margin_used == pytest.approx(1200.0)


def test_positions_carry_the_venue_trade_id_as_the_broker_reference() -> None:
    adapter, _ = _adapter()
    positions = adapter.positions()
    assert len(positions) == 2
    eurusd = positions[0]
    assert eurusd.instrument == "EURUSD"
    assert eurusd.direction is Direction.LONG
    assert eurusd.size == 10_000.0
    assert eurusd.venue_ref == "1471"
    assert eurusd.stop_loss == pytest.approx(1.09935)
    assert eurusd.take_profit_targets == [pytest.approx(1.10835)]
    assert eurusd.entry_time.tzinfo is not None


def test_negative_units_become_a_short_position() -> None:
    adapter, _ = _adapter()
    gold = adapter.positions()[1]
    assert gold.instrument == "XAUUSD"
    assert gold.direction is Direction.SHORT
    assert gold.size == 5.0


def test_working_orders_expose_the_client_reference() -> None:
    adapter, _ = _adapter()
    orders = adapter.orders()
    assert orders[0].broker_ref == "1500"
    assert orders[0].client_ref == "FBK-plan_ghi"


# ------------------------------------------------------- order placement


def test_the_client_ref_is_transmitted_as_the_client_extension_id() -> None:
    """This is the idempotency key. Without it, a lost response is unrecoverable."""
    adapter, transport = _adapter()
    adapter.place_order(_order())
    body = transport.calls[-1]["body"]
    assert body["order"]["clientExtensions"]["id"] == "FBK-plan_abc"
    assert body["order"]["clientExtensions"]["comment"] == "plan=plan_abc"


def test_stop_and_take_profit_are_attached_on_fill() -> None:
    adapter, transport = _adapter()
    adapter.place_order(_order())
    order = transport.calls[-1]["body"]["order"]
    assert order["stopLossOnFill"] == {"price": "1.09935", "timeInForce": "GTC"}
    assert order["takeProfitOnFill"] == {"price": "1.10835", "timeInForce": "GTC"}


def test_the_size_is_transmitted_unchanged_as_signed_units() -> None:
    adapter, transport = _adapter()
    adapter.place_order(_order())
    assert transport.calls[-1]["body"]["order"]["units"] == "10000"

    adapter2, transport2 = _adapter()
    adapter2.place_order(_order(direction=Direction.SHORT, take_profit=1.09, stop_loss=1.12))
    assert transport2.calls[-1]["body"]["order"]["units"] == "-10000"


def test_the_adapter_refuses_to_round_a_size_it_cannot_express() -> None:
    """Rounding here would be the adapter re-deciding size. That was V1's bug."""
    adapter, _ = _adapter()
    with pytest.raises(BrokerRejected, match="will not round"):
        adapter.place_order(_order(size=10_000.5))


def test_a_fill_returns_the_trade_id_as_the_broker_reference() -> None:
    adapter, _ = _adapter()
    ack = adapter.place_order(_order())
    assert ack.status is OrderStatus.FILLED
    # The TRADE id, not the transaction id: the transaction identifies an event,
    # the trade identifies the position we will later have to close.
    assert ack.broker_ref == "1482"
    assert ack.client_ref == "FBK-plan_abc"
    assert ack.fill.filled_size == 10_000.0
    assert ack.fill.filled_price == pytest.approx(1.10240)
    assert ack.fill.venue_ref == "1482"


def test_a_cancelled_order_is_a_rejection() -> None:
    adapter, _ = _adapter(_fixtures(**{f"POST {_path('/orders')}": [CANCEL_RESPONSE]}))
    with pytest.raises(BrokerRejected, match="MARKET_HALTED"):
        adapter.place_order(_order())


def test_a_duplicate_client_order_id_raises_DuplicateClientRef() -> None:
    dup = HttpResponse(400, {"errorCode": "CLIENT_ORDER_ID_ALREADY_EXISTS"})
    adapter, _ = _adapter(_fixtures(**{f"POST {_path('/orders')}": [dup]}))
    with pytest.raises(DuplicateClientRef):
        adapter.place_order(_order())


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_transport_level_failure_is_UNKNOWN_never_a_rejection(status: int) -> None:
    adapter, _ = _adapter(
        _fixtures(**{f"POST {_path('/orders')}": [HttpResponse(status, {})]})
    )
    with pytest.raises(BrokerUnavailable):
        adapter.place_order(_order())


def test_an_accepted_but_unfilled_order_is_ACCEPTED_not_FILLED() -> None:
    accepted = HttpResponse(
        201,
        {"orderCreateTransaction": {"id": "1700", "time": "2024-06-03T12:00:00.000000000Z"}},
    )
    adapter, _ = _adapter(_fixtures(**{f"POST {_path('/orders')}": [accepted]}))
    ack = adapter.place_order(_order())
    assert ack.status is OrderStatus.ACCEPTED
    assert ack.fill is None


def test_a_partial_fill_is_flagged() -> None:
    partial = HttpResponse(
        201,
        {
            "orderCreateTransaction": {"id": "1", "time": "2024-06-03T12:00:00.000000000Z"},
            "orderFillTransaction": {
                "id": "2", "time": "2024-06-03T12:00:00.000000000Z",
                "price": "1.1024",
                "tradeOpened": {"tradeID": "3", "units": "4000"},
            },
        },
    )
    adapter, _ = _adapter(_fixtures(**{f"POST {_path('/orders')}": [partial]}))
    ack = adapter.place_order(_order())
    assert ack.status is OrderStatus.PARTIALLY_FILLED
    assert ack.fill.partial is True
    assert ack.fill.filled_size == 4000.0


# ------------------------------------------------------------- closing


def test_closing_is_keyed_on_the_broker_reference() -> None:
    adapter, transport = _adapter()
    position = adapter.positions()[0]
    ack = adapter.close_position(position, client_ref="FBK-CLOSE-1")
    assert transport.calls[-1]["path"].endswith("/trades/1471/close")
    assert transport.calls[-1]["body"] == {"units": "ALL"}
    assert ack.broker_ref == "1471"


def test_closing_without_a_broker_reference_is_refused() -> None:
    adapter, _ = _adapter()
    orphan = Position(
        instrument="EURUSD", direction=Direction.LONG, size=1000.0,
        entry_price=1.1, entry_time=NOW, stop_loss=1.09, venue_ref=None,
    )
    with pytest.raises(BrokerRejected, match="broker reference"):
        adapter.close_position(orphan, client_ref="x")


# --------------------------------------------------------- rate limiting


def test_the_rate_limiter_paces_requests() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []

    def now():
        return clock["t"]

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(10.0, clock=now, sleeper=sleep)
    for _ in range(5):
        limiter.acquire()
    assert len(slept) == 4
    assert all(s == pytest.approx(0.1) for s in slept)


def test_requests_that_are_naturally_spaced_are_not_delayed() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []
    limiter = RateLimiter(
        10.0, clock=lambda: clock["t"], sleeper=lambda s: slept.append(s)
    )
    for _ in range(3):
        limiter.acquire()
        clock["t"] += 1.0
    assert slept == []


def test_the_adapter_paces_every_call() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    transport = RecordedTransport(_fixtures())
    adapter = OandaAdapter(
        OandaConfig(account_id=ACCOUNT, api_token=TOKEN),
        transport=transport,
        rate_limiter=RateLimiter(5.0, clock=lambda: clock["t"], sleeper=sleep),
        env={},
        now_fn=lambda: NOW,
    )
    for _ in range(4):
        adapter.account()
    assert len(slept) == 3


def test_a_rate_limit_response_is_treated_as_unknown_not_rejected() -> None:
    adapter, _ = _adapter(
        _fixtures(**{f"POST {_path('/orders')}": [HttpResponse(429, {})]})
    )
    with pytest.raises(BrokerUnavailable, match="rate limited"):
        adapter.place_order(_order())


def test_health_reports_disconnected_rather_than_raising() -> None:
    adapter, _ = _adapter(
        _fixtures(**{f"GET {_path('/summary')}": [HttpResponse(503, {})]})
    )
    health = adapter.health()
    assert health.connected is False
    assert health.score == 0.0
