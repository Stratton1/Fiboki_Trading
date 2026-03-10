"""Tests for chart drawing persistence."""

import pytest
from sqlalchemy import create_engine

from fibokei.db.database import get_session_factory, init_db
from fibokei.db.repository import (
    delete_all_drawings,
    delete_drawing,
    get_drawings,
    save_drawing,
    update_drawing,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database and session for testing."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    factory = get_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _drawing_data(user_id=1, instrument="EURUSD", timeframe="H1", tool_type="straightLine"):
    return {
        "user_id": user_id,
        "instrument": instrument,
        "timeframe": timeframe,
        "tool_type": tool_type,
        "points_json": [
            {"timestamp": 1700000000, "value": 1.1000},
            {"timestamp": 1700003600, "value": 1.1050},
        ],
    }


class TestSaveAndGetDrawings:
    def test_save_and_get_drawings(self, db_session):
        d1 = save_drawing(db_session, _drawing_data(tool_type="straightLine"))
        d2 = save_drawing(db_session, _drawing_data(tool_type="horizontalStraightLine"))

        assert d1.id is not None
        assert d2.id is not None

        drawings = get_drawings(db_session, user_id=1, instrument="EURUSD", timeframe="H1")
        assert len(drawings) == 2
        assert drawings[0].tool_type == "straightLine"
        assert drawings[1].tool_type == "horizontalStraightLine"

    def test_defaults(self, db_session):
        d = save_drawing(db_session, _drawing_data())
        assert d.lock is False
        assert d.visible is True
        assert d.created_at is not None
        assert d.updated_at is not None
        assert d.styles_json is None


class TestUpdateDrawing:
    def test_update_drawing(self, db_session):
        d = save_drawing(db_session, _drawing_data())
        updated = update_drawing(db_session, d.id, user_id=1, updates={
            "lock": True,
            "styles_json": {"color": "#ff0000"},
        })
        assert updated is not None
        assert updated.lock is True
        assert updated.styles_json == {"color": "#ff0000"}

    def test_update_drawing_wrong_user(self, db_session):
        d = save_drawing(db_session, _drawing_data(user_id=1))
        result = update_drawing(db_session, d.id, user_id=999, updates={"lock": True})
        assert result is None

    def test_update_drawing_not_found(self, db_session):
        result = update_drawing(db_session, drawing_id=9999, user_id=1, updates={"lock": True})
        assert result is None


class TestDeleteDrawing:
    def test_delete_drawing(self, db_session):
        d = save_drawing(db_session, _drawing_data())
        assert delete_drawing(db_session, d.id, user_id=1) is True
        assert get_drawings(db_session, user_id=1, instrument="EURUSD", timeframe="H1") == []

    def test_delete_drawing_wrong_user(self, db_session):
        d = save_drawing(db_session, _drawing_data(user_id=1))
        assert delete_drawing(db_session, d.id, user_id=999) is False

    def test_delete_drawing_not_found(self, db_session):
        assert delete_drawing(db_session, drawing_id=9999, user_id=1) is False


class TestDeleteAllDrawings:
    def test_delete_all_drawings(self, db_session):
        save_drawing(db_session, _drawing_data())
        save_drawing(db_session, _drawing_data(tool_type="fibonacciLine"))
        save_drawing(db_session, _drawing_data(instrument="GBPUSD"))  # different instrument

        count = delete_all_drawings(db_session, user_id=1, instrument="EURUSD", timeframe="H1")
        assert count == 2

        remaining = get_drawings(db_session, user_id=1, instrument="EURUSD", timeframe="H1")
        assert len(remaining) == 0

        # GBPUSD drawing should still exist
        other = get_drawings(db_session, user_id=1, instrument="GBPUSD", timeframe="H1")
        assert len(other) == 1


class TestGetDrawingsFiltersByUser:
    def test_get_drawings_filters_by_user(self, db_session):
        save_drawing(db_session, _drawing_data(user_id=1))
        save_drawing(db_session, _drawing_data(user_id=2))

        user1 = get_drawings(db_session, user_id=1, instrument="EURUSD", timeframe="H1")
        user2 = get_drawings(db_session, user_id=2, instrument="EURUSD", timeframe="H1")

        assert len(user1) == 1
        assert len(user2) == 1
        assert user1[0].user_id == 1
        assert user2[0].user_id == 2
