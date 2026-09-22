"""Tests for chart drawing CRUD API endpoints."""

import pytest


DRAWING_PAYLOAD = {
    "instrument": "EUR_USD",
    "timeframe": "H1",
    "tool_type": "trendline",
    "points": [
        {"timestamp": 1700000000000, "value": 1.0950},
        {"timestamp": 1700003600000, "value": 1.1000},
    ],
    "styles": {"color": "#ff0000", "lineWidth": 2},
    "lock": False,
    "visible": True,
}


class TestCreateDrawing:
    def test_create_drawing(self, api_client, auth_headers):
        response = api_client.post(
            "/api/v1/drawings",
            json=DRAWING_PAYLOAD,
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["instrument"] == "EUR_USD"
        assert data["timeframe"] == "H1"
        assert data["tool_type"] == "trendline"
        assert len(data["points"]) == 2
        assert data["points"][0]["timestamp"] == 1700000000000
        assert data["points"][0]["value"] == 1.0950
        assert data["styles"] == {"color": "#ff0000", "lineWidth": 2}
        assert data["lock"] is False
        assert data["visible"] is True
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data


class TestListDrawings:
    def test_list_drawings(self, api_client, auth_headers):
        # Create two drawings on same chart
        api_client.post("/api/v1/drawings", json=DRAWING_PAYLOAD, headers=auth_headers)
        payload2 = {**DRAWING_PAYLOAD, "tool_type": "fibonacci"}
        api_client.post("/api/v1/drawings", json=payload2, headers=auth_headers)

        response = api_client.get(
            "/api/v1/drawings",
            params={"instrument": "EUR_USD", "timeframe": "H1"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        tool_types = {d["tool_type"] for d in data}
        assert tool_types == {"trendline", "fibonacci"}

    def test_list_drawings_empty(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/drawings",
            params={"instrument": "GBP_USD", "timeframe": "D1"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_list_drawings_filters_by_chart(self, api_client, auth_headers):
        # Create drawing on EUR_USD H1
        api_client.post("/api/v1/drawings", json=DRAWING_PAYLOAD, headers=auth_headers)

        # Query different instrument — should be empty
        response = api_client.get(
            "/api/v1/drawings",
            params={"instrument": "GBP_USD", "timeframe": "H1"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []


class TestUpdateDrawing:
    def test_update_drawing_points(self, api_client, auth_headers):
        # Create
        create_resp = api_client.post(
            "/api/v1/drawings", json=DRAWING_PAYLOAD, headers=auth_headers
        )
        drawing_id = create_resp.json()["id"]

        # Update points
        new_points = [
            {"timestamp": 1700010000000, "value": 1.1050},
            {"timestamp": 1700020000000, "value": 1.1100},
        ]
        response = api_client.put(
            f"/api/v1/drawings/{drawing_id}",
            json={"points": new_points},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["points"]) == 2
        assert data["points"][0]["timestamp"] == 1700010000000
        assert data["points"][1]["value"] == 1.1100

    def test_update_drawing_lock(self, api_client, auth_headers):
        create_resp = api_client.post(
            "/api/v1/drawings", json=DRAWING_PAYLOAD, headers=auth_headers
        )
        drawing_id = create_resp.json()["id"]

        response = api_client.put(
            f"/api/v1/drawings/{drawing_id}",
            json={"lock": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["lock"] is True

    def test_update_nonexistent_drawing(self, api_client, auth_headers):
        response = api_client.put(
            "/api/v1/drawings/99999",
            json={"lock": True},
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestDeleteDrawing:
    def test_delete_drawing(self, api_client, auth_headers):
        create_resp = api_client.post(
            "/api/v1/drawings", json=DRAWING_PAYLOAD, headers=auth_headers
        )
        drawing_id = create_resp.json()["id"]

        response = api_client.delete(
            f"/api/v1/drawings/{drawing_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify it's gone
        list_resp = api_client.get(
            "/api/v1/drawings",
            params={"instrument": "EUR_USD", "timeframe": "H1"},
            headers=auth_headers,
        )
        assert list_resp.json() == []

    def test_delete_nonexistent_drawing(self, api_client, auth_headers):
        response = api_client.delete(
            "/api/v1/drawings/99999",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestClearDrawings:
    def test_clear_drawings_for_chart(self, api_client, auth_headers):
        # Create two drawings
        api_client.post("/api/v1/drawings", json=DRAWING_PAYLOAD, headers=auth_headers)
        payload2 = {**DRAWING_PAYLOAD, "tool_type": "horizontal_line"}
        api_client.post("/api/v1/drawings", json=payload2, headers=auth_headers)

        response = api_client.delete(
            "/api/v1/drawings",
            params={"instrument": "EUR_USD", "timeframe": "H1"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 2

        # Verify empty
        list_resp = api_client.get(
            "/api/v1/drawings",
            params={"instrument": "EUR_USD", "timeframe": "H1"},
            headers=auth_headers,
        )
        assert list_resp.json() == []

    def test_clear_drawings_empty_chart(self, api_client, auth_headers):
        response = api_client.delete(
            "/api/v1/drawings",
            params={"instrument": "GBP_USD", "timeframe": "D1"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 0


class TestUnauthorizedAccess:
    def test_unauthorized_access(self, api_client):
        """All drawing endpoints require authentication."""
        # GET
        resp = api_client.get(
            "/api/v1/drawings",
            params={"instrument": "EUR_USD", "timeframe": "H1"},
        )
        assert resp.status_code == 401

        # POST
        resp = api_client.post("/api/v1/drawings", json=DRAWING_PAYLOAD)
        assert resp.status_code == 401

        # PUT
        resp = api_client.put("/api/v1/drawings/1", json={"lock": True})
        assert resp.status_code == 401

        # DELETE single
        resp = api_client.delete("/api/v1/drawings/1")
        assert resp.status_code == 401

        # DELETE clear
        resp = api_client.delete(
            "/api/v1/drawings",
            params={"instrument": "EUR_USD", "timeframe": "H1"},
        )
        assert resp.status_code == 401
