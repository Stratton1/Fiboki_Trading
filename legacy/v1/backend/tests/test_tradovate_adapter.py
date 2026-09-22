"""Tests for the Tradovate execution adapter (Phase 1)."""

from __future__ import annotations

from unittest.mock import MagicMock

from fibokei.execution.broker_symbols import (
    TradovateContractResolver,
    _ContractMapping,
)
from fibokei.execution.tradovate_adapter import TradovateExecutionAdapter
from fibokei.execution.tradovate_client import (
    TradovateClient,
    TradovateClientError,
    TradovateOrderResult,
)


def _make_adapter(
    *,
    place_result: TradovateOrderResult | None = None,
    place_side_effect: Exception | None = None,
    has_creds: bool = True,
    accounts: list | None = None,
) -> tuple[TradovateExecutionAdapter, MagicMock]:
    client = MagicMock(spec=TradovateClient)
    client.has_credentials = has_creds
    client.env = "demo"
    client.base_url = "https://demo.tradovateapi.com/v1"
    if accounts is None:
        from fibokei.execution.tradovate_client import TradovateAccount

        accounts = [
            TradovateAccount(account_id=42, name="DEMO42", account_type="Live", user_id=7)
        ]
    client.list_accounts.return_value = accounts
    client.authenticate.return_value = None
    if place_side_effect is not None:
        client.place_order.side_effect = place_side_effect
    else:
        client.place_order.return_value = place_result or TradovateOrderResult(
            success=True, order_id=99, raw={"orderId": 99}
        )
    resolver = TradovateContractResolver(
        front_month_suffix="M6",
        symbol_map={
            "US500": _ContractMapping(product_code="ES"),
            "US100": _ContractMapping(product_code="NQ"),
        },
    )
    adapter = TradovateExecutionAdapter(client=client, resolver=resolver)
    return adapter, client


class TestPlaceOrder:
    def test_happy_path(self):
        adapter, client = _make_adapter()
        result = adapter.place_order(
            {
                "instrument": "US500",
                "direction": "BUY",
                "size": 2,
                "requested_price": 5000.0,
            }
        )
        assert result["status"] == "ACCEPTED"
        assert result["deal_id"] == "99"
        assert result["broker_symbol"] == "ESM6"
        # Adapter sent the right payload
        sent = client.place_order.call_args[0][0]
        assert sent["accountId"] == 42
        # accountSpec (account NAME) is required by Tradovate alongside accountId
        assert sent["accountSpec"] == "DEMO42"
        assert sent["action"] == "Buy"
        assert sent["symbol"] == "ESM6"
        assert sent["orderQty"] == 2
        assert sent["orderType"] == "Market"
        assert sent["isAutomated"] is True

    def test_unsupported_instrument(self):
        adapter, client = _make_adapter()
        result = adapter.place_order(
            {"instrument": "EURUSD", "direction": "BUY", "size": 1}
        )
        assert result["status"] == "rejected"
        assert result["error_code"] == "UNSUPPORTED_INSTRUMENT_TRADOVATE"
        # Client was never asked to place
        client.place_order.assert_not_called()

    def test_zero_size_rejected(self):
        adapter, client = _make_adapter()
        result = adapter.place_order(
            {"instrument": "US500", "direction": "BUY", "size": 0}
        )
        assert result["status"] == "rejected"
        assert result["error_code"] == "INVALID_SIZE"
        client.place_order.assert_not_called()

    def test_credentials_missing(self):
        adapter, client = _make_adapter(has_creds=False)
        client.authenticate.side_effect = TradovateClientError(
            "Tradovate credentials not configured", error_code="MISSING_CREDENTIALS"
        )
        result = adapter.place_order(
            {"instrument": "US500", "direction": "BUY", "size": 1}
        )
        assert result["status"] == "rejected"
        assert result["error_code"] == "MISSING_CREDENTIALS"

    def test_broker_rejection(self):
        adapter, client = _make_adapter(
            place_result=TradovateOrderResult(
                success=False, failure_reason="Account suspended"
            )
        )
        result = adapter.place_order(
            {"instrument": "US500", "direction": "BUY", "size": 1}
        )
        assert result["status"] == "rejected"
        assert result["error_code"] == "BROKER_REJECTED"
        assert "Account suspended" in result["reason"]

    def test_sell_maps_to_sell_action(self):
        adapter, client = _make_adapter()
        adapter.place_order(
            {"instrument": "US500", "direction": "SELL", "size": 1}
        )
        sent = client.place_order.call_args[0][0]
        assert sent["action"] == "Sell"


class TestClosePosition:
    def test_close_unknown_deal_rejected(self):
        adapter, client = _make_adapter()
        result = adapter.close_position("UNKNOWN-DEAL-ID")
        assert result["status"] == "failed"
        assert result["error_code"] == "UNKNOWN_DEAL_ID"

    def test_close_after_open_inverts_action(self):
        adapter, client = _make_adapter()
        # Open
        opened = adapter.place_order(
            {"instrument": "US500", "direction": "BUY", "size": 3}
        )
        deal = opened["deal_id"]
        # Close
        client.place_order.reset_mock()
        client.place_order.return_value = TradovateOrderResult(
            success=True, order_id=100, raw={}
        )
        result = adapter.close_position(deal)
        assert result["status"] == "ACCEPTED"
        sent = client.place_order.call_args[0][0]
        # Original was Buy → close action is Sell. accountSpec must persist
        # on close payloads too (required by Tradovate /order/placeOrder).
        assert sent["action"] == "Sell"
        assert sent["accountSpec"] == "DEMO42"
        assert sent["orderQty"] == 3
        assert sent["symbol"] == "ESM6"


class TestHealthcheck:
    def test_no_credentials(self):
        adapter, client = _make_adapter(has_creds=False)
        info = adapter.healthcheck()
        assert info["configured"] is False
        assert info["reachable"] is False
        assert info["error"]

    def test_reachable_with_account(self):
        adapter, client = _make_adapter()
        info = adapter.healthcheck()
        assert info["configured"] is True
        assert info["reachable"] is True
        assert info["account_id"] == "42"
        assert info["account_name"] == "DEMO42"
        # Resolver has 2 mappings (US500, US100)
        assert info["supported_symbols_count"] == 2
