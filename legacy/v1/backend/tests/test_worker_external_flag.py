"""FIBOKEI_WORKER_EXTERNAL must disable the in-process worker thread.

When a dedicated worker service (``python -m fibokei.worker``) is deployed
on Railway, the API must not also run its own worker thread — otherwise two
workers evaluate the same bots concurrently and duplicate execution attempts.
"""

import os
from unittest import mock

from fibokei.api.app import _start_worker_thread


def test_external_flag_disables_in_process_worker():
    with mock.patch.dict(os.environ, {"FIBOKEI_WORKER_EXTERNAL": "true"}):
        thread, worker = _start_worker_thread(session_factory=None)
    assert thread is None
    assert worker is None


def test_external_flag_variants_accepted():
    for value in ("1", "TRUE", "yes", "on"):
        with mock.patch.dict(os.environ, {"FIBOKEI_WORKER_EXTERNAL": value}):
            thread, worker = _start_worker_thread(session_factory=None)
        assert thread is None, f"value={value!r} should disable worker"


def test_default_still_starts_worker(tmp_path):
    """Without the flag the legacy in-process path is preserved."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from fibokei.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    env = {k: v for k, v in os.environ.items() if k != "FIBOKEI_WORKER_EXTERNAL"}
    with mock.patch.dict(os.environ, env, clear=True):
        thread, worker = _start_worker_thread(session_factory)
    assert worker is not None
    assert thread is not None
    worker.stop()
    thread.join(timeout=10)
