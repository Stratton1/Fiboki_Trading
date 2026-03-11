"""Tests for the async job engine."""

import time

import pytest

from fibokei.jobs.engine import JobEngine, JobState


@pytest.fixture
def engine():
    e = JobEngine(max_workers=2)
    yield e
    e.shutdown(wait=True)


def _simple_job(value=42, progress_callback=None):
    if progress_callback:
        progress_callback(50)
    time.sleep(0.05)
    if progress_callback:
        progress_callback(100)
    return {"value": value}


def _failing_job(progress_callback=None):
    raise ValueError("Something went wrong")


def test_submit_and_complete(engine):
    info = engine.submit("test", "test job", _simple_job, value=99)
    assert info.job_id
    assert info.state in (JobState.PENDING, JobState.RUNNING)

    # Wait for completion
    info._future.result(timeout=5)
    time.sleep(0.05)

    updated = engine.get(info.job_id)
    assert updated.state == JobState.COMPLETED
    assert updated.progress == 100
    assert updated.result == {"value": 99}
    assert updated.completed_at is not None


def test_failed_job(engine):
    info = engine.submit("test", "failing job", _failing_job)
    info._future.result(timeout=5)
    time.sleep(0.05)

    updated = engine.get(info.job_id)
    assert updated.state == JobState.FAILED
    assert "Something went wrong" in updated.error


def test_list_jobs(engine):
    engine.submit("backtest", "bt1", _simple_job)
    engine.submit("research", "res1", _simple_job)

    all_jobs = engine.list_jobs()
    assert len(all_jobs) == 2

    bt_jobs = engine.list_jobs(job_type="backtest")
    assert len(bt_jobs) == 1
    assert bt_jobs[0].job_type == "backtest"


def test_list_jobs_by_state(engine):
    info = engine.submit("test", "job1", _simple_job)
    info._future.result(timeout=5)
    time.sleep(0.05)

    completed = engine.list_jobs(state=JobState.COMPLETED)
    assert len(completed) == 1


def test_cancel_job(engine):
    def slow_job(progress_callback=None):
        for i in range(100):
            time.sleep(0.01)
            if progress_callback:
                progress_callback(i)
        return {"done": True}

    info = engine.submit("test", "slow", slow_job)
    time.sleep(0.05)
    cancelled = engine.cancel(info.job_id)
    assert cancelled

    info._future.result(timeout=5)
    time.sleep(0.05)

    updated = engine.get(info.job_id)
    assert updated.state == JobState.CANCELLED


def test_cancel_completed_job_fails(engine):
    info = engine.submit("test", "fast", _simple_job)
    info._future.result(timeout=5)
    time.sleep(0.05)

    assert not engine.cancel(info.job_id)


def test_get_nonexistent_job(engine):
    assert engine.get("nonexistent") is None


def test_active_count(engine):
    def blocking_job(progress_callback=None):
        time.sleep(0.5)
        return {}

    engine.submit("test", "blocking", blocking_job)
    time.sleep(0.05)
    assert engine.active_count() >= 1
