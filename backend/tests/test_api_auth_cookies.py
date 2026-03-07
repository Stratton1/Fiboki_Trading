"""Tests for cookie-based authentication."""


class TestCookieAuth:
    def test_login_sets_cookie(self, api_client):
        response = api_client.post(
            "/api/v1/auth/login",
            data={"username": "joe", "password": "testpass123"},
        )
        assert response.status_code == 200
        assert "fibokei_token" in response.cookies

    def test_cookie_auth_protects_routes(self, api_client):
        response = api_client.get("/api/v1/instruments")
        assert response.status_code == 401

    def test_cookie_auth_allows_access(self, auth_client):
        response = auth_client.get("/api/v1/instruments")
        assert response.status_code == 200

    def test_logout_clears_cookie(self, auth_client):
        response = auth_client.post("/api/v1/auth/logout")
        assert response.status_code == 200
        assert response.json()["detail"] == "Logged out"

    def test_auth_me(self, auth_client):
        response = auth_client.get("/api/v1/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "joe"
        assert "user_id" in data
        assert "role" in data
