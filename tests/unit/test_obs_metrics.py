"""Metrics and their Prometheus rendering.

The exposition format is implemented here rather than taken from a library, so
the escaping rules it must obey are asserted rather than assumed.
"""
from __future__ import annotations

import math

import pytest

from fiboki.obs.metrics import (
    DEFAULT_BUCKETS,
    MetricRegistry,
    escape_label_value,
    record_broker_health,
    record_data_freshness,
    record_job_finished,
    record_job_started,
    record_queue_depth,
    record_reconciliation_divergence,
    record_spread_divergence,
    record_worker_liveness,
    render_prometheus,
    timer,
)


@pytest.fixture
def registry():
    return MetricRegistry()


# ------------------------------------------------------------- metric types


def test_a_counter_refuses_to_decrease(registry):
    counter = registry.counter("c_total", "help", ("queue",))
    counter.inc(2, queue="research")
    assert counter.value(queue="research") == 2
    with pytest.raises(ValueError, match="cannot decrease"):
        counter.inc(-1, queue="research")


def test_wrong_labels_are_a_loud_error_not_a_new_series(registry):
    """A typo'd label silently creating a second series is how a dashboard
    goes quiet while the metric 'still exists'."""
    counter = registry.counter("c_total", "help", ("queue",))
    with pytest.raises(ValueError, match="expected labels"):
        counter.inc(queue="research", typo="x")
    with pytest.raises(ValueError, match="expected labels"):
        counter.inc()


def test_histogram_buckets_are_cumulative(registry):
    hist = registry.histogram("h_seconds", "help", (), buckets=(1.0, 5.0, 10.0))
    for value in (0.5, 2.0, 7.0, 20.0):
        hist.observe(value)
    samples = {(s.name, dict(s.labels).get("le")): s.value for s in hist.samples()}
    assert samples[("h_seconds_bucket", "1")] == 1
    assert samples[("h_seconds_bucket", "5")] == 2
    assert samples[("h_seconds_bucket", "10")] == 3
    assert samples[("h_seconds_bucket", "+Inf")] == 4
    assert hist.count() == 4
    assert hist.sum() == pytest.approx(29.5)


def test_histogram_refuses_nan(registry):
    hist = registry.histogram("h", "help")
    with pytest.raises(ValueError, match="NaN"):
        hist.observe(float("nan"))


def test_re_registering_the_same_name_returns_the_same_object(registry):
    a = registry.counter("x_total", "help")
    b = registry.counter("x_total", "help")
    assert a is b
    with pytest.raises(ValueError, match="already registered"):
        registry.gauge("x_total", "help")


def test_timer_observes_even_when_the_block_raises(registry):
    hist = registry.histogram("t_seconds", "help")
    with pytest.raises(RuntimeError), timer(hist):
        raise RuntimeError("boom")
    assert hist.count() == 1


# ---------------------------------------------------------------- exposition


def test_label_values_are_escaped_per_the_spec():
    assert escape_label_value('a "quoted" value') == 'a \\"quoted\\" value'
    assert escape_label_value("back\\slash") == "back\\\\slash"
    assert escape_label_value("two\nlines") == "two\\nlines"


def test_rendered_output_has_help_type_and_samples(registry):
    registry.counter("fiboki_jobs_total", "Jobs done.", ("queue",)).inc(3, queue="research")
    text = render_prometheus(registry)
    assert "# HELP fiboki_jobs_total Jobs done." in text
    assert "# TYPE fiboki_jobs_total counter" in text
    assert 'fiboki_jobs_total{queue="research"} 3' in text
    assert text.endswith("\n")


def test_a_label_value_containing_a_quote_does_not_break_the_line(registry):
    gauge = registry.gauge("g", "A gauge.", ("name",))
    gauge.set(1, name='weird "name" \\ here')
    line = next(ln for ln in render_prometheus(registry).splitlines() if ln.startswith("g{"))
    # One line, balanced quoting.
    assert line.count('"') % 2 == 0
    assert '\\"name\\"' in line


def test_help_text_escapes_backslash_and_newline_but_not_quotes(registry):
    registry.counter("c_total", 'Says "hello"\nand\\more').inc()
    text = render_prometheus(registry)
    help_line = next(ln for ln in text.splitlines() if ln.startswith("# HELP"))
    assert '"hello"' in help_line  # quotes are NOT escaped in HELP
    assert "\\n" in help_line
    assert "and\\\\more" in help_line


def test_infinity_renders_as_the_spec_token(registry):
    registry.gauge("g", "help").set(math.inf)
    assert "g +Inf" in render_prometheus(registry)


# ------------------------------------------------------ the required metrics


def test_every_required_operational_metric_exists_and_records():
    """The eight questions V1's observability could not answer."""
    from fiboki.obs.metrics import REGISTRY

    record_job_started("research", "backtest")
    record_job_finished("research", "backtest", "succeeded", 1.5)
    record_queue_depth("research", 42)
    record_worker_liveness("research@mac:1", "research", age_seconds=3.0, fresh=True)
    record_data_freshness("EURUSD", "H1", 120.0)
    record_broker_health("ig", "paper", reachable=True, latency_seconds=0.12)
    record_reconciliation_divergence(unknown=1, orphan=0, size_mismatch=2)
    ratio = record_spread_divergence("EURUSD", realised=1.8, modelled=1.2)

    text = render_prometheus(REGISTRY)
    for name in (
        "fiboki_jobs_started_total",
        "fiboki_jobs_finished_total",
        "fiboki_queue_depth",
        "fiboki_worker_up",
        "fiboki_worker_heartbeat_age_seconds",
        "fiboki_backtest_wall_seconds",
        "fiboki_data_age_seconds",
        "fiboki_broker_up",
        "fiboki_reconciliation_divergence",
        "fiboki_spread_divergence_ratio",
    ):
        assert f"# TYPE {name}" in text, f"{name} is not exposed"

    assert REGISTRY.value("fiboki_queue_depth", queue="research") == 42
    assert REGISTRY.value("fiboki_worker_up", worker="research@mac:1", kind="research") == 1.0
    assert REGISTRY.value("fiboki_reconciliation_divergence", kind="size_mismatch") == 2
    assert ratio == pytest.approx(1.5)


def test_spread_divergence_above_one_means_the_backtest_is_optimistic():
    """The metric that decides whether stored backtests may still be believed."""
    from fiboki.obs.metrics import REGISTRY

    assert record_spread_divergence("GBPUSD", realised=2.4, modelled=1.2) == pytest.approx(2.0)
    assert REGISTRY.value("fiboki_spread_divergence_ratio", instrument="GBPUSD") == 2.0
    # A zero modelled spread is infinite optimism, not a division error.
    assert record_spread_divergence("XAUUSD", realised=5.0, modelled=0.0) == math.inf


def test_worker_liveness_records_zero_when_stale():
    from fiboki.obs.metrics import REGISTRY

    record_worker_liveness("w2", "live", age_seconds=999.0, fresh=False)
    assert REGISTRY.value("fiboki_worker_up", worker="w2", kind="live") == 0.0
    assert REGISTRY.value("fiboki_worker_heartbeat_age_seconds", worker="w2", kind="live") == 999.0


def test_default_buckets_straddle_a_fast_cell_and_a_pathological_one():
    assert min(DEFAULT_BUCKETS) <= 0.01
    assert max(DEFAULT_BUCKETS) >= 600.0
