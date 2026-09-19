"""Observability: structured logs, metrics, alerts, health.

Import order matters slightly -- :mod:`fiboki.obs.alerts` uses the metrics
registry -- so this module re-exports the small surface most callers want and
leaves the rest to explicit imports.
"""
from __future__ import annotations

from fiboki.obs.alerts import (
    Alert,
    AlertChannel,
    AlertDispatcher,
    AlertEvent,
    ConsoleChannel,
    FileChannel,
    HeartbeatView,
    HeartbeatWatchdog,
    MemoryChannel,
    Severity,
    TelegramChannel,
    WatchdogThresholds,
    WebhookChannel,
    build_default_dispatcher,
)
from fiboki.obs.health import (
    BrokerReachableCheck,
    DatabaseCheck,
    DataFreshnessCheck,
    HealthReport,
    HealthResult,
    HealthStatus,
    MigrationCheck,
    QueueDepthCheck,
    WorkerHeartbeatCheck,
    run_checks,
)
from fiboki.obs.logging import (
    JsonFormatter,
    bind,
    configure_logging,
    correlation_id,
    get_logger,
    new_correlation_id,
)
from fiboki.obs.metrics import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    MetricRegistry,
    render_prometheus,
    timer,
)

__all__ = [
    "REGISTRY",
    "Alert",
    "AlertChannel",
    "AlertDispatcher",
    "AlertEvent",
    "BrokerReachableCheck",
    "ConsoleChannel",
    "Counter",
    "DataFreshnessCheck",
    "DatabaseCheck",
    "FileChannel",
    "Gauge",
    "HealthReport",
    "HealthResult",
    "HealthStatus",
    "HeartbeatView",
    "HeartbeatWatchdog",
    "Histogram",
    "JsonFormatter",
    "MemoryChannel",
    "MetricRegistry",
    "MigrationCheck",
    "QueueDepthCheck",
    "Severity",
    "TelegramChannel",
    "WatchdogThresholds",
    "WebhookChannel",
    "WorkerHeartbeatCheck",
    "bind",
    "build_default_dispatcher",
    "configure_logging",
    "correlation_id",
    "get_logger",
    "new_correlation_id",
    "render_prometheus",
    "run_checks",
    "timer",
]
