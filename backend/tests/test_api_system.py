"""Tests for system API endpoints."""


class TestSystemHealth:
    def test_system_health(self, api_client):
        response = api_client.get("/api/v1/system/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"


class TestSystemStatus:
    def test_system_status(self, api_client, auth_headers):
        response = api_client.get("/api/v1/system/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "api_version" in data
        assert "database" in data
        assert "strategies_loaded" in data
        assert data["database"] == "connected"
        assert data["strategies_loaded"] >= 1

    def test_system_status_requires_auth(self, api_client):
        response = api_client.get("/api/v1/system/status")
        assert response.status_code == 401
