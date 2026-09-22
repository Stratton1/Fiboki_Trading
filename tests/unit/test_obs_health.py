"""Health checks must perform I/O and must report FAIL when they cannot.

V1's /health returned {"status": "ok"} from a handler that did no work, so it
tested whether the web server could serve a route -- never the thing in doubt.
"""
from __future__ import annotations

from fiboki.obs.health import (
    BrokerReachableCheck,
    DatabaseCheck,
    DataFreshnessCheck,
    HealthStatus,
    MigrationCheck,
    QueueDepthCheck,
    WorkerHeartbeatCheck,
    run_checks,
)


class View:
    def __init__(self, worker_id, age_seconds):
        self.worker_id = worker_id
        self.age_seconds = age_seconds


# ------------------------------------------------------------------ database


def test_database_check_fails_when_the_probe_raises():
    def probe():
        raise OSError("unable to open database file")

    result = DatabaseCheck(probe=probe)()
    assert result.status is HealthStatus.FAIL
    assert "unable to open" in result.detail
    # A failure that does not say what to do costs the operator the same
    # diagnosis every single time.
    assert result.remedy


def test_database_check_passes_when_the_probe_returns():
    result = DatabaseCheck(probe=lambda: 1)()
    assert result.status is HealthStatus.OK


# ----------------------------------------------------------------- migration


def test_a_schema_behind_the_code_is_FAIL_not_degraded():
    """'column does not exist' at 03:00 is not a degradation."""
    result = MigrationCheck(current=lambda: "abc123", expected=lambda: "def456")()
    assert result.status is HealthStatus.FAIL
    assert "alembic upgrade head" in result.remedy
    assert result.data["applied"] == "abc123"


def test_a_matching_revision_is_ok():
    assert MigrationCheck(current=lambda: "x", expected=lambda: "x")().status is HealthStatus.OK


def test_an_unreadable_migration_state_is_FAIL_not_ok():
    def boom():
        raise RuntimeError("no alembic_version table")

    assert MigrationCheck(current=boom, expected=lambda: "x")().status is HealthStatus.FAIL


# ----------------------------------------------------------------- heartbeat


def test_a_down_worker_is_FAIL():
    check = WorkerHeartbeatCheck(
        heartbeats=lambda: [View("research@mac:1", 400.0)],
        stale_after_seconds=120,
        down_after_seconds=300,
    )
    result = check()
    assert result.status is HealthStatus.FAIL
    # The remedy must say the thing V1 got wrong.
    assert "not a thread inside the API" in result.remedy


def test_a_stale_worker_is_DEGRADED():
    check = WorkerHeartbeatCheck(
        heartbeats=lambda: [View("w1", 150.0)], stale_after_seconds=120, down_after_seconds=300
    )
    assert check().status is HealthStatus.DEGRADED


def test_an_expected_worker_with_no_row_at_all_is_FAIL():
    check = WorkerHeartbeatCheck(heartbeats=lambda: [], expected_workers=("research@mac:1",))
    result = check()
    assert result.status is HealthStatus.FAIL
    assert "research@mac:1" in result.detail


def test_fresh_workers_are_ok():
    check = WorkerHeartbeatCheck(heartbeats=lambda: [View("w1", 5.0), View("w2", 10.0)])
    assert check().status is HealthStatus.OK


# ---------------------------------------------------------------- freshness


def test_data_freshness_uses_per_instrument_budgets():
    """A closed market is EXPECTED to be stale; one global number is a lie."""
    check = DataFreshnessCheck(
        ages=lambda: {"EURUSD": 200.0, "XAUUSD": 5000.0},
        budgets={"EURUSD": 3600.0, "XAUUSD": 86400.0},
    )
    assert check().status is HealthStatus.OK


def test_badly_stale_data_is_FAIL():
    check = DataFreshnessCheck(ages=lambda: {"EURUSD": 100_000.0}, default_budget_seconds=3600)
    result = check()
    assert result.status is HealthStatus.FAIL
    assert "EURUSD" in result.detail


def test_mildly_stale_data_is_DEGRADED():
    check = DataFreshnessCheck(ages=lambda: {"EURUSD": 5000.0}, default_budget_seconds=3600)
    assert check().status is HealthStatus.DEGRADED


def test_an_empty_store_is_FAIL_not_ok():
    """No instruments reported is not 'everything is fresh'."""
    assert DataFreshnessCheck(ages=lambda: {})().status is HealthStatus.FAIL


# ------------------------------------------------------------------- broker


def test_an_unreachable_broker_is_FAIL_when_required():
    def probe():
        raise ConnectionError("connection refused")

    assert BrokerReachableCheck(probe=probe)().status is HealthStatus.FAIL


def test_an_unreachable_optional_broker_is_only_DEGRADED():
    """Paper mode does not need a venue."""

    def probe():
        raise ConnectionError("refused")

    assert (
        BrokerReachableCheck(probe=probe, required=False)().status is HealthStatus.DEGRADED
    )


def test_a_probe_returning_false_is_not_treated_as_healthy():
    assert BrokerReachableCheck(probe=lambda: False)().status is HealthStatus.FAIL


# -------------------------------------------------------------------- queue


def test_a_backed_up_queue_is_FAIL():
    result = QueueDepthCheck(depths=lambda: {"research": 5000}, warn_at=100, fail_at=1000)()
    assert result.status is HealthStatus.FAIL
    assert "fiboki worker status" in result.remedy


def test_a_growing_queue_is_DEGRADED():
    assert (
        QueueDepthCheck(depths=lambda: {"research": 200}, warn_at=100, fail_at=1000)().status
        is HealthStatus.DEGRADED
    )


# ---------------------------------------------------------------- aggregate


def test_a_check_that_raises_is_reported_as_FAIL_not_dropped():
    class Exploding:
        name = "exploding"

        def __call__(self):
            raise RuntimeError("the check itself is broken")

    report = run_checks([Exploding()])
    assert report.status is HealthStatus.FAIL
    assert "check raised" in report.results[0].detail


def test_one_failing_check_does_not_hide_the_others():
    class Exploding:
        name = "exploding"

        def __call__(self):
            raise RuntimeError("boom")

    report = run_checks([Exploding(), DatabaseCheck(probe=lambda: 1)])
    assert len(report.results) == 2
    assert report.get("database").status is HealthStatus.OK


def test_the_aggregate_is_the_WORST_state_not_an_average():
    report = run_checks(
        [
            DatabaseCheck(probe=lambda: 1),
            DatabaseCheck(probe=lambda: 1, name="db2"),
            QueueDepthCheck(depths=lambda: {"q": 99999}, fail_at=10, name="queue"),
        ]
    )
    assert report.status is HealthStatus.FAIL
    assert report.healthy is False
    assert len(report.failures()) == 1


def test_http_status_is_503_only_on_fail():
    ok = run_checks([DatabaseCheck(probe=lambda: 1)])
    assert ok.http_status == 200

    degraded = run_checks([DataFreshnessCheck(ages=lambda: {"EURUSD": 5000.0})])
    assert degraded.status is HealthStatus.DEGRADED
    # A degraded-but-serving instance must not be pulled from a load balancer;
    # the BODY still carries the truth.
    assert degraded.http_status == 200

    failed = run_checks([DatabaseCheck(probe=lambda: (_ for _ in ()).throw(OSError("x")))])
    assert failed.http_status == 503


def test_the_report_serialises_for_an_endpoint():
    report = run_checks([DatabaseCheck(probe=lambda: 1)])
    payload = report.to_dict()
    assert payload["status"] == "ok"
    assert payload["checks"][0]["name"] == "database"
    assert "duration_ms" in payload["checks"][0]
