"""Tests for API authentication."""

import pytest


class TestHealthCheck:
    def test_health_check(self, api_client):
        response = api_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"


class TestLogin:
    def test_login_valid_credentials(self, api_client):
        response = api_client.post(
            "/api/v1/auth/login",
            data={"username": "joe", "password": "testpass123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_invalid_password(self, api_client):
        response = api_client.post(
            "/api/v1/auth/login",
            data={"username": "joe", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_login_invalid_username(self, api_client):
        response = api_client.post(
            "/api/v1/auth/login",
            data={"username": "unknown_user", "password": "testpass123"},
        )
        assert response.status_code == 401


class TestProtectedEndpoints:
    def test_no_token_returns_401(self, api_client):
        response = api_client.get("/api/v1/instruments")
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, api_client):
        response = api_client.get(
            "/api/v1/instruments",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401

    def test_valid_token_returns_200(self, api_client, auth_headers):
        response = api_client.get("/api/v1/instruments", headers=auth_headers)
        assert response.status_code == 200
