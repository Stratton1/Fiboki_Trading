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


class TestMarketSpecSafety:
    """The adapter must never size or convert stops on default specs.

    Deployed evidence: with fallback onePipMeans=1.0, a valid 45-pip FX stop
    converted to 0.0045 'pips', rounded to stopDistance 0.0 and went to IG
    as a naked order at the size cap (bots e01595a5, d62888c1, 49f9b893).
    """

    def test_market_details_failure_rejects_order(self):
        adapter, client = _make_adapter()
        client.get_market.side_effect = IGClientError("boom", status_code=500)
        result = adapter.place_order(_base_order())
        assert result["status"] == "rejected"
        assert result["error_code"] == "MARKET_DETAILS_UNAVAILABLE"
        client.open_position.assert_not_called()

    def test_market_details_failure_not_cached(self):
        adapter, client = _make_adapter()
        client.get_market.side_effect = [
            IGClientError("boom", status_code=500),
            {
                "instrument": {"valueOfOnePip": "1", "onePipMeans": "0.0001"},
                "dealingRules": {"minDealSize": {"value": 0.5}},
                "snapshot": {},
            },
        ]
        first = adapter._get_market_details("CS.D.EURUSD.CFD.IP")
        assert first["is_default"] is True
        second = adapter._get_market_details("CS.D.EURUSD.CFD.IP")
        assert second["is_default"] is False

    def test_stop_rounding_to_zero_rejected(self):
        """Wrong onePipMeans (1.0) must not let a stop round to 0.0."""
        adapter, client = _make_adapter()
        client.get_market.return_value = {
            "instrument": {"valueOfOnePip": "1", "onePipMeans": "1"},  # bad spec
            "dealingRules": {"minDealSize": {"value": 0.5}},
            "snapshot": {},
        }
        client.fetch_account_balance = MagicMock(return_value=1000.0)
        result = adapter.place_order(_base_order(stop_distance=0.0045))
        assert result["status"] == "rejected"
        assert result["error_code"] == "STOP_TOO_TIGHT"
        client.open_position.assert_not_called()

    def test_broker_min_stop_distance_enforced(self):
        adapter, client = _make_adapter()
        client.get_market.return_value = {
            "instrument": {"valueOfOnePip": "1", "onePipMeans": "0.0001"},
            "dealingRules": {
                "minDealSize": {"value": 0.5},
                "minNormalStopOrLimitDistance": {"value": 50},
            },
            "snapshot": {},
        }
        # 0.0045 / 0.0001 = 45 pips < broker minimum 50
        result = adapter.place_order(_base_order(stop_distance=0.0045))
        assert result["status"] == "rejected"
        assert result["error_code"] == "STOP_TOO_TIGHT"

    def test_valid_stop_passes_min_distance(self):
        adapter, client = _make_adapter()
        _wire_happy_market(client)
        client.open_position.return_value = {"dealReference": "REF9"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED", "dealId": "D9", "level": 1.0851, "reason": "",
        }
        result = adapter.place_order(_base_order())
        assert result["status"] == "ACCEPTED"
        assert client.open_position.call_args[0][0]["stopDistance"] == 45.0


class TestPriceScaleNormalisation:
    """IG FX CFDs quote in points (EURUSD ≈ 13050.9) while strategies use
    classic scale (≈ 1.3051). Verified on real IG demo 2026-06-12."""

    def _ig_points_market(self, client, bid=13050.9):
        client.get_market.return_value = {
            "instrument": {"valueOfOnePip": "1", "onePipMeans": "1"},
            "dealingRules": {
                "minDealSize": {"value": 1.0},
                "minNormalStopOrLimitDistance": {"value": 2},
            },
            "snapshot": {"bid": bid},
        }

    def test_classic_stop_scaled_to_ig_points(self):
        adapter, client = _make_adapter()
        self._ig_points_market(client)
        client.open_position.return_value = {"dealReference": "REFS"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED", "dealId": "DS", "level": 13051.2, "reason": "",
        }
        result = adapter.place_order(_base_order(
            requested_price=1.30509, stop_distance=0.0045, limit_distance=0.0090,
        ))
        assert result["status"] == "ACCEPTED"
        params = client.open_position.call_args[0][0]
        # 0.0045 classic × 10^4 = 45 IG points
        assert params["stopDistance"] == 45.0
        assert params["limitDistance"] == 90.0
        # Slippage computed on IG scale: |13051.2 - 13050.9| / opm(1) = 0.3
        assert result["slippage_pips"] == 0.3

    def test_matching_scale_untouched(self):
        adapter, client = _make_adapter()
        # Index-style: feed and IG agree (DAX ≈ 24600 both sides)
        client.get_market.return_value = {
            "instrument": {"valueOfOnePip": "1", "onePipMeans": "1"},
            "dealingRules": {"minDealSize": {"value": 1.0}},
            "snapshot": {"bid": 24611.2},
        }
        client.open_position.return_value = {"dealReference": "REFD"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED", "dealId": "DD", "level": 24611.5, "reason": "",
        }
        result = adapter.place_order(_base_order(
            instrument="DE40", requested_price=24611.0,
            stop_distance=60.0, limit_distance=120.0,
        ))
        assert result["status"] == "ACCEPTED"
        params = client.open_position.call_args[0][0]
        assert params["stopDistance"] == 60.0
        assert params["limitDistance"] == 120.0

    def test_non_power_of_ten_ratio_not_scaled(self):
        adapter, client = _make_adapter()
        # Ratio ~4.2: disagreement, but not a clean scale — leave distances
        # alone; the STOP_TOO_TIGHT gate then rejects rather than guessing.
        self._ig_points_market(client, bid=4.2)
        result = adapter.place_order(_base_order(
            requested_price=1.0, stop_distance=0.0045,
        ))
        assert result["status"] == "rejected"
        assert result["error_code"] == "STOP_TOO_TIGHT"
        client.open_position.assert_not_called()


class TestAuth401Retry:
    def test_401_triggers_single_reauth_retry(self):
        client = IGClient()
        ok = MagicMock(status_code=200, content=b"{}")
        ok.json.return_value = {"ok": True}
        unauth = MagicMock(status_code=401, content=b"{}")
        unauth.json.return_value = {"errorCode": "error.security.client-token-invalid"}
        with mock.patch.object(client, "ensure_session") as ses, \
             mock.patch.object(client._http, "request", side_effect=[unauth, ok]) as req:
            ses.return_value = MagicMock(headers={})
            result = client._request("GET", "/markets/X", version="3")
        assert result == {"ok": True}
        assert req.call_count == 2
        assert ses.call_count == 2  # fresh session forced before retry

    def test_second_401_raises(self):
        client = IGClient()
        unauth = MagicMock(status_code=401, content=b"{}")
        unauth.json.return_value = {"errorCode": "error.security.client-token-invalid"}
        with mock.patch.object(client, "ensure_session") as ses, \
             mock.patch.object(client._http, "request", side_effect=[unauth, unauth]):
            ses.return_value = MagicMock(headers={})
            try:
                client._request("GET", "/markets/X", version="3")
                raised = False
            except IGClientError as e:
                raised = True
                assert e.status_code == 401
        assert raised


class TestUnitSuffixedMarketFields:
    """CFD-account market fields carry unit suffixes (real IG demo 2026-06-12):
    onePipMeans='0.0001 USD/EUR', valueOfOnePip='10 USD'."""

    def test_unit_suffixed_spec_parsed(self):
        adapter, client = _make_adapter()
        client.get_market.return_value = {
            "instrument": {"valueOfOnePip": "10 USD", "onePipMeans": "0.0001 USD/EUR"},
            "dealingRules": {
                "minDealSize": {"value": 1.0},
                "minNormalStopOrLimitDistance": {"value": 2},
            },
            "snapshot": {"bid": 1.16505},
        }
        spec = adapter._get_market_details("CS.D.EURUSD.CFD.IP")
        assert spec["is_default"] is False
        assert spec["one_pip_means"] == 0.0001
        assert spec["value_per_pip"] == 10.0

    def test_garbage_field_falls_back_not_crash(self):
        adapter, client = _make_adapter()
        client.get_market.return_value = {
            "instrument": {"valueOfOnePip": "n/a", "onePipMeans": None},
            "dealingRules": {"minDealSize": {"value": 1.0}},
            "snapshot": {"bid": 100.0},
        }
        spec = adapter._get_market_details("X.TEST.IP")
        assert spec["is_default"] is False
        assert spec["one_pip_means"] == 1.0
        assert spec["value_per_pip"] == 1.0
