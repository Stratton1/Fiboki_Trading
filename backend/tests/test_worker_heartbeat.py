"""Worker heartbeat: durable liveness visible without Railway access."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fibokei.db.models import Base, WorkerHeartbeatModel
from fibokei.db.repository import beat_worker_heartbeat, get_worker_heartbeats


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/hb.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_first_beat_creates_row(session_factory):
    with session_factory() as s:
        beat_worker_heartbeat(s, "railway-worker", hostname="abc", bots_active=21)
        rows = get_worker_heartbeats(s)
    assert len(rows) == 1
    assert rows[0].worker_id == "railway-worker"
    assert rows[0].bots_active == 21


def test_subsequent_beats_upsert_same_row(session_factory):
    with session_factory() as s:
        beat_worker_heartbeat(s, "railway-worker", loops_completed=1)
        first_started = get_worker_heartbeats(s)[0].started_at
        beat_worker_heartbeat(s, "railway-worker", loops_completed=2)
        rows = get_worker_heartbeats(s)
    assert len(rows) == 1
    assert rows[0].loops_completed == 2
    assert rows[0].started_at == first_started  # started_at preserved


def test_error_recorded_and_cleared(session_factory):
    with session_factory() as s:
        beat_worker_heartbeat(s, "w1", last_error="boom")
        assert get_worker_heartbeats(s)[0].last_error == "boom"
        beat_worker_heartbeat(s, "w1", last_error=None)
        assert get_worker_heartbeats(s)[0].last_error is None


def test_staleness_detectable(session_factory):
    with session_factory() as s:
        beat_worker_heartbeat(s, "w1", poll_interval_s=60)
        row = s.query(WorkerHeartbeatModel).first()
        row.last_beat_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        s.commit()
        hb = get_worker_heartbeats(s)[0]
        beat_at = hb.last_beat_at
        if beat_at.tzinfo is None:
            beat_at = beat_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - beat_at).total_seconds()
    assert age > hb.poll_interval_s * 3  # the freshness rule used by the API
