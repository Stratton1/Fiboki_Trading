"""Tests for the IG demo execution repairs (June 2026 forensic audit).

Root causes found in deployed Railway logs:
1. get_prices sent VERSION 3 against the v2 path-style endpoint
   /prices/{epic}/{resolution}/{numPoints} → 404 for every epic →
   live feed silently fell back to yfinance.
2. Static gold/silver epics (CS.D.USCGC/USCSI.TODAY.IP) are spread-bet
   epics; the configured apiUser is CFD-only → POST /positions/otc 403
   'no access to the relevant exchange' (exchangeId=FX_BET_ALL).
3. Naked orders (stop=0.0, size at cap) were reaching IG.
"""

import os
from unittest import mock
from unittest.mock import MagicMock

from fibokei.execution.ig_adapter import IGExecutionAdapter
from fibokei.execution.ig_client import IGClient, IGClientError


def _make_adapter():
    client = MagicMock(spec=IGClient)
    adapter = IGExecutionAdapter(client=client)
    return adapter, client


def _base_order(**overrides):
    order = {
        "instrument": "EURUSD",
        "direction": "BUY",
        "size": 1.0,
        "currency": "GBP",
        "stop_distance": 0.0045,
        "limit_distance": 0.0090,
        "requested_price": 1.0850,
        "risk_pct": 1.0,
        "bot_id": "test-bot",
    }
    order.update(overrides)
    return order


def _wire_happy_market(client):
    client.get_market.return_value = {
        "instrument": {
            "valueOfOnePoint": "1",
            "onePipMeans": "0.0001",
            "lotSize": 1,
            "currencies": [{"code": "GBP"}],
        },
        "dealingRules": {"minDealSize": {"value": 0.5}},
        "snapshot": {},
    }
    client.fetch_account_balance = MagicMock(return_value=1000.0)
    client.get_accounts = MagicMock(return_value=[])


class TestPricesVersionHeader:
    def test_get_prices_uses_version_2(self):
        client = IGClient()
        with mock.patch.object(client, "_request", return_value={"prices": []}) as req:
            client.get_prices("CS.D.EURUSD.CFD.IP", "HOUR", 200)
        req.assert_called_once_with(
            "GET", "/prices/CS.D.EURUSD.CFD.IP/HOUR/200", version="2"
        )

    def test_search_markets_uses_version_1(self):
        client = IGClient()
        with mock.patch.object(
            client, "_request", return_value={"markets": [{"epic": "X"}]}
        ) as req:
            result = client.search_markets("Gold")
        req.assert_called_once_with("GET", "/markets?searchTerm=Gold", version="1")
        assert result == [{"epic": "X"}]

    def test_search_markets_empty(self):
        client = IGClient()
        with mock.patch.object(client, "_request", return_value={}):
            assert client.search_markets("Nothing") == []


class TestStopRequired:
    def test_order_without_stop_rejected_by_default(self):
        adapter, client = _make_adapter()
        _wire_happy_market(client)
        result = adapter.place_order(_base_order(stop_distance=None))
        assert result["status"] == "rejected"
        assert result["error_code"] == "MISSING_STOP"
        client.open_position.assert_not_called()

    def test_order_without_stop_allowed_when_flag_off(self):
        adapter, client = _make_adapter()
        _wire_happy_market(client)
        client.open_position.return_value = {"dealReference": "REF1"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED", "dealId": "D1", "level": 1.0851, "reason": "",
        }
        with mock.patch.dict(os.environ, {"FIBOKEI_IG_REQUIRE_STOP": "false"}):
            result = adapter.place_order(_base_order(stop_distance=None))
        assert result["status"] == "ACCEPTED"
        client.open_position.assert_called_once()

    def test_order_with_stop_submitted(self):
        adapter, client = _make_adapter()
        _wire_happy_market(client)
        client.open_position.return_value = {"dealReference": "REF2"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED", "dealId": "D2", "level": 1.0851, "reason": "",
        }
        result = adapter.place_order(_base_order())
        assert result["status"] == "ACCEPTED"
        params = client.open_position.call_args[0][0]
        assert params["stopDistance"] > 0


class TestEpicResolutionRetry:
    def test_exchange_403_triggers_resolution_and_retry(self):
        adapter, client = _make_adapter()
        _wire_happy_market(client)
        err = IGClientError(
            "IG API error: POST /positions/otc → 403 | unauthorised access, "
            "apiUser has no access to the relevant exchange. "
            "Epic=CS.D.USCGC.TODAY.IP exchangeId=FX_BET_ALL",
            status_code=403,
        )
        client.open_position.side_effect = [err, {"dealReference": "REF3"}]
        client.search_markets.return_value = [
            {"epic": "CS.D.CFDGOLD.CFM.IP", "marketStatus": "TRADEABLE",
             "instrumentName": "Spot Gold"},
        ]
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED", "dealId": "D3", "level": 4231.5, "reason": "",
        }
        result = adapter.place_order(_base_order(instrument="XAUUSD", stop_distance=30.0))
        assert result["status"] == "ACCEPTED"
        assert client.open_position.call_count == 2
        retry_params = client.open_position.call_args_list[1][0][0]
        assert retry_params["epic"] == "CS.D.CFDGOLD.CFM.IP"
        # Resolution cached for subsequent orders
        assert adapter._resolved_epics["XAUUSD"] == "CS.D.CFDGOLD.CFM.IP"

    def test_no_retry_loop_when_resolution_fails(self):
        adapter, client = _make_adapter()
        _wire_happy_market(client)
        err = IGClientError(
            "unauthorised access, apiUser has no access to the relevant exchange.",
            status_code=403,
        )
        client.open_position.side_effect = err
        client.search_markets.return_value = []
        result = adapter.place_order(_base_order(instrument="XAUUSD", stop_distance=30.0))
        assert result["status"] == "rejected"
        assert client.open_position.call_count == 1

    def test_unrelated_errors_do_not_trigger_resolution(self):
        adapter, client = _make_adapter()
        _wire_happy_market(client)
        client.open_position.side_effect = IGClientError("validation error", status_code=400)
        result = adapter.place_order(_base_order())
        assert result["status"] == "rejected"
        client.search_markets.assert_not_called()
