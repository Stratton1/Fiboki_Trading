"""Tests for IG execution adapter with mocked API responses."""

import time
from unittest.mock import MagicMock, patch

import pytest

from fibokei.core.instruments import (
    get_ig_epic,
    get_ig_supported_instruments,
    get_symbol_by_epic,
)
from fibokei.execution.ig_adapter import IGExecutionAdapter
from fibokei.execution.ig_client import (
    IGClient,
    IGClientError,
    IGSession,
    IG_DEMO_BASE,
    IG_PROD_BASE,
)


# ---------- Epic mapping tests ----------


class TestEpicMapping:
    def test_get_ig_epic_forex_major(self):
        assert get_ig_epic("EURUSD") == "CS.D.EURUSD.CFD.IP"

    def test_get_ig_epic_commodity(self):
        assert get_ig_epic("XAUUSD") == "CS.D.USCGC.TODAY.IP"

    def test_get_ig_epic_index(self):
        assert get_ig_epic("US500") == "IX.D.SPTRD.IFD.IP"

    def test_get_ig_epic_unknown_raises(self):
        with pytest.raises(KeyError, match="No IG epic mapping"):
            get_ig_epic("DXY")  # DXY has no IG epic

    def test_get_ig_epic_nonexistent_raises(self):
        with pytest.raises(KeyError, match="Unknown instrument"):
            get_ig_epic("FAKEPAIR")

    def test_reverse_lookup_by_epic(self):
        assert get_symbol_by_epic("CS.D.EURUSD.CFD.IP") == "EURUSD"
        assert get_symbol_by_epic("IX.D.SPTRD.IFD.IP") == "US500"

    def test_reverse_lookup_unknown_epic(self):
        with pytest.raises(KeyError, match="Unknown IG epic"):
            get_symbol_by_epic("XX.D.FAKE.IP")

    def test_ig_supported_count(self):
        supported = get_ig_supported_instruments()
        # Most instruments have epics; DXY, SOLUSD, XRPUSD don't
        assert len(supported) >= 60
        symbols = {i.symbol for i in supported}
        assert "EURUSD" in symbols
        assert "DXY" not in symbols
        assert "SOLUSD" not in symbols


# ---------- IGSession tests ----------


class TestIGSession:
    def test_empty_session_is_invalid(self):
        s = IGSession()
        assert not s.is_valid

    def test_fresh_session_is_valid(self):
        s = IGSession(cst="token", x_security_token="sec", created_at=time.time())
        assert s.is_valid

    def test_expired_session_is_invalid(self):
        s = IGSession(
            cst="token",
            x_security_token="sec",
            created_at=time.time() - 6 * 3600,  # 6 hours ago
        )
        assert not s.is_valid

    def test_session_headers(self):
        s = IGSession(cst="my-cst", x_security_token="my-sec")
        h = s.headers
        assert h["CST"] == "my-cst"
        assert h["X-SECURITY-TOKEN"] == "my-sec"


# ---------- IGClient tests ----------


class TestIGClientSafety:
    def test_production_url_blocked(self):
        client = IGClient()
        client._base_url = IG_PROD_BASE
        with pytest.raises(IGClientError, match="Production IG API is blocked"):
            client.authenticate()

    def test_missing_credentials_raises(self):
        client = IGClient()
        # Ensure env vars are not set
        with patch.dict("os.environ", {}, clear=True):
            client._api_key = ""
            client._username = ""
            client._password = ""
            with pytest.raises(IGClientError, match="credentials not configured"):
                client.authenticate()

    def test_demo_base_url_default(self):
        client = IGClient()
        assert client._base_url == IG_DEMO_BASE


# ---------- IGExecutionAdapter tests ----------


def _make_mock_client(positions=None, accounts=None):
    """Create a mock IGClient with preset responses."""
    client = MagicMock(spec=IGClient)
    client.authenticate.return_value = IGSession(
        cst="test-cst", x_security_token="test-sec",
        account_id="TEST123", created_at=time.time(),
    )
    client.get_positions.return_value = positions or []
    client.get_accounts.return_value = accounts or []
    client.get_account_info.return_value = accounts[0] if accounts else {}
    return client


class TestIGAdapterPlaceOrder:
    def test_place_order_success(self):
        client = _make_mock_client()
        client.open_position.return_value = {"dealReference": "REF123"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED",
            "dealId": "DEAL456",
            "level": 1.1050,
            "reason": "SUCCESS",
        }
        adapter = IGExecutionAdapter(client=client)
        result = adapter.place_order({
            "instrument": "EURUSD",
            "direction": "BUY",
            "size": 1.0,
        })
        assert result["status"] == "ACCEPTED"
        assert result["deal_id"] == "DEAL456"
        assert result["epic"] == "CS.D.EURUSD.CFD.IP"

    def test_place_order_no_epic(self):
        client = _make_mock_client()
        adapter = IGExecutionAdapter(client=client)
        result = adapter.place_order({
            "instrument": "DXY",  # No IG epic
            "direction": "BUY",
            "size": 1.0,
        })
        assert result["status"] == "rejected"
        assert "No IG epic" in result["reason"]

    def test_place_order_api_error(self):
        client = _make_mock_client()
        client.open_position.side_effect = IGClientError("Market closed", status_code=400)
        adapter = IGExecutionAdapter(client=client)
        result = adapter.place_order({
            "instrument": "EURUSD",
            "direction": "BUY",
            "size": 1.0,
        })
        assert result["status"] == "rejected"

    def test_place_order_with_stops(self):
        client = _make_mock_client()
        client.open_position.return_value = {"dealReference": "REF789"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED",
            "dealId": "DEAL789",
            "reason": "SUCCESS",
        }
        adapter = IGExecutionAdapter(client=client)
        result = adapter.place_order({
            "instrument": "GBPUSD",
            "direction": "SELL",
            "size": 2.0,
            "stop_distance": 50,
            "limit_distance": 100,
        })
        assert result["status"] == "ACCEPTED"
        call_args = client.open_position.call_args[0][0]
        assert call_args["stopDistance"] == 50
        assert call_args["limitDistance"] == 100


class TestIGAdapterPositions:
    def test_get_positions_maps_to_fiboki_symbols(self):
        client = _make_mock_client(positions=[
            {
                "market": {"epic": "CS.D.EURUSD.CFD.IP", "bid": 1.1050},
                "position": {
                    "dealId": "DEAL1",
                    "direction": "BUY",
                    "size": 1.0,
                    "openLevel": 1.1000,
                    "stopLevel": 1.0950,
                    "limitLevel": 1.1100,
                    "currency": "USD",
                    "upl": 50.0,
                    "createdDateUTC": "2024-01-15T10:00:00",
                },
            }
        ])
        adapter = IGExecutionAdapter(client=client)
        positions = adapter.get_positions()
        assert len(positions) == 1
        assert positions[0]["instrument"] == "EURUSD"
        assert positions[0]["deal_id"] == "DEAL1"
        assert positions[0]["direction"] == "BUY"

    def test_get_positions_unknown_epic_passes_through(self):
        client = _make_mock_client(positions=[
            {
                "market": {"epic": "XX.D.UNKNOWN.IP", "bid": 100},
                "position": {
                    "dealId": "DEAL2",
                    "direction": "SELL",
                    "size": 5.0,
                    "openLevel": 105,
                    "currency": "GBP",
                    "upl": -25.0,
                    "createdDateUTC": "2024-01-15T11:00:00",
                },
            }
        ])
        adapter = IGExecutionAdapter(client=client)
        positions = adapter.get_positions()
        assert len(positions) == 1
        assert positions[0]["instrument"] == "XX.D.UNKNOWN.IP"  # Pass-through

    def test_get_positions_api_error_returns_empty(self):
        client = _make_mock_client()
        client.get_positions.side_effect = IGClientError("Network error")
        adapter = IGExecutionAdapter(client=client)
        assert adapter.get_positions() == []


class TestIGAdapterAccount:
    def test_get_account_info(self):
        client = _make_mock_client(accounts=[{
            "accountId": "TEST123",
            "accountName": "Demo CFD",
            "balance": {"balance": 10000, "deposit": 500, "available": 9500, "profitLoss": 150},
            "currency": "GBP",
        }])
        adapter = IGExecutionAdapter(client=client)
        info = adapter.get_account_info()
        assert info["account_id"] == "TEST123"
        assert info["balance"] == 10000
        assert info["equity"] == 10150  # balance + pnl
        assert info["pnl"] == 150

    def test_get_account_info_error(self):
        client = _make_mock_client()
        client.get_account_info.side_effect = IGClientError("Auth expired")
        adapter = IGExecutionAdapter(client=client)
        info = adapter.get_account_info()
        assert "error" in info


class TestIGAdapterClosePosition:
    def test_close_position_success(self):
        client = _make_mock_client(positions=[
            {
                "market": {"epic": "CS.D.EURUSD.CFD.IP"},
                "position": {"dealId": "DEAL1", "direction": "BUY", "size": 1.0},
            }
        ])
        client.close_position.return_value = {"dealReference": "CLOSE_REF"}
        client.get_deal_confirmation.return_value = {
            "dealStatus": "ACCEPTED",
            "dealId": "CLOSE_DEAL",
            "reason": "SUCCESS",
        }
        adapter = IGExecutionAdapter(client=client)
        result = adapter.close_position("DEAL1")
        assert result["status"] == "ACCEPTED"
        # Verify close direction is opposite
        client.close_position.assert_called_once_with("DEAL1", "SELL", 1.0)

    def test_close_position_not_found(self):
        client = _make_mock_client(positions=[])
        adapter = IGExecutionAdapter(client=client)
        result = adapter.close_position("NONEXISTENT")
        assert result["status"] == "failed"
        assert "not found" in result["reason"]


class TestIGAdapterOrders:
    def test_cancel_order_success(self):
        client = _make_mock_client()
        client.delete_working_order.return_value = {}
        adapter = IGExecutionAdapter(client=client)
        assert adapter.cancel_order("ORDER1") is True

    def test_cancel_order_failure(self):
        client = _make_mock_client()
        client.delete_working_order.side_effect = IGClientError("Not found", status_code=404)
        adapter = IGExecutionAdapter(client=client)
        assert adapter.cancel_order("ORDER1") is False

    def test_modify_order_success(self):
        client = _make_mock_client()
        client.update_working_order.return_value = {"status": "ok"}
        adapter = IGExecutionAdapter(client=client)
        result = adapter.modify_order("ORDER1", {"level": 1.1050})
        assert result["status"] == "modified"
