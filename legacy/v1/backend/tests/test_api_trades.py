"""Tests for trades API endpoints."""


class TestListTrades:
    def test_list_trades_empty(self, api_client, auth_headers):
        response = api_client.get("/api/v1/trades/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    def test_list_trades_requires_auth(self, api_client):
        response = api_client.get("/api/v1/trades/")
        assert response.status_code == 401


class TestGetTrade:
    def test_trade_not_found(self, api_client, auth_headers):
        response = api_client.get("/api/v1/trades/99999", headers=auth_headers)
        assert response.status_code == 404

    def test_get_trade_requires_auth(self, api_client):
        response = api_client.get("/api/v1/trades/1")
        assert response.status_code == 401
