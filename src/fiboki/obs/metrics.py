"""In-process metrics with a Prometheus text-exposition rendering.

Why in-process and dependency-free
----------------------------------
Fiboki V2 runs local-first: on a Mac, in the foreground, often with no
Prometheus anywhere near it.  A metrics layer that only works when a scrape
target exists is a metrics layer nobody enables during development, which is
precisely when the numbers matter most.  So the registry is a plain object you
can read in a test (``registry.value("fiboki_jobs_total", queue="research")``)
and render to text when something asks.

The exposition format is implemented here rather than pulled from
``prometheus_client`` for one reason: this project pins every dependency that
can change a number, and an observability library is not worth a pin.  The
format is small and stable; the renderer is ~60 lines and is tested against
the escaping rules in the spec.

What is measured
----------------
The named helpers at the bottom cover the operational questions V1 could not
answer:

===========================  ===================================================
job throughput               are jobs actually completing, or just starting?
queue depth                  is work piling up behind a stuck worker?
worker liveness              is the worker process alive, and how stale?
backtest wall time           has a sweep cell got 40x slower since the change?
data freshness               how old is the newest bar per instrument?
broker health                is the venue reachable, and how slow?
reconciliation divergence    how many intents disagree with the venue RIGHT NOW?
realised-vs-modelled spread  is the backtest's cost model still telling the truth?
===========================  ===================================================

The last one is the one that decides whether a backtest may still be believed,
which is why it is a first-class metric and not a research notebook.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "REGISTRY",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricKind",
    "MetricRegistry",
    "Sample",
    "escape_label_value",
    "observe_backtest_wall_time",
    "record_broker_health",
    "record_data_freshness",
    "record_job_finished",
    "record_job_started",
    "record_queue_depth",
    "record_reconciliation_divergence",
    "record_spread_divergence",
    "record_worker_liveness",
    "render_prometheus",
    "timer",
]

#: Default histogram buckets, seconds.  Chosen to straddle the things that
#: actually happen here: a fast cell (10ms), a normal backtest (1-30s) and a
#: pathological one (10min+).
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.05,
    0.1,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    300.0,
    600.0,
)


class MetricKind(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


LabelKey = tuple[tuple[str, str], ...]


def _labels_key(labels: Mapping[str, Any]) -> LabelKey:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass(frozen=True, slots=True)
class Sample:
    """One rendered line: a metric name, its labels and its value."""

    name: str
    labels: LabelKey
    value: float

    def label_dict(self) -> dict[str, str]:
        return dict(self.labels)


# ---------------------------------------------------------------------------
# Metric types
# ---------------------------------------------------------------------------


class _Metric:
    __slots__ = ("_lock", "help", "labelnames", "name")

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        if not name or not name[0].isalpha():
            raise ValueError(f"metric name {name!r} must start with a letter")
        self.name = name
        self.help = help_text
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()

    def _check(self, labels: Mapping[str, Any]) -> LabelKey:
        if self.labelnames:
            missing = [n for n in self.labelnames if n not in labels]
            extra = [n for n in labels if n not in self.labelnames]
            if missing or extra:
                raise ValueError(
                    f"{self.name}: expected labels {list(self.labelnames)}, "
                    f"got {sorted(labels)} (missing={missing}, unexpected={extra})"
                )
        return _labels_key(labels)


class Counter(_Metric):
    """Monotonically increasing.  Refuses a negative increment, loudly."""

    __slots__ = ("_values",)

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[LabelKey, float] = {}

    def inc(self, amount: float = 1.0, **labels: Any) -> float:
        if amount < 0:
            raise ValueError(f"{self.name}: a counter cannot decrease (got {amount})")
        key = self._check(labels)
        with self._lock:
            new = self._values.get(key, 0.0) + amount
            self._values[key] = new
        return new

    def value(self, **labels: Any) -> float:
        return self._values.get(self._check(labels), 0.0)

    def samples(self) -> list[Sample]:
        with self._lock:
            return [Sample(self.name, k, v) for k, v in sorted(self._values.items())]


class Gauge(_Metric):
    """Goes up and down.  ``set`` is the normal call; ``inc``/``dec`` exist too."""

    __slots__ = ("_values",)

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: dict[LabelKey, float] = {}

    def set(self, value: float, **labels: Any) -> float:
        key = self._check(labels)
        with self._lock:
            self._values[key] = float(value)
        return float(value)

    def inc(self, amount: float = 1.0, **labels: Any) -> float:
        key = self._check(labels)
        with self._lock:
            new = self._values.get(key, 0.0) + amount
            self._values[key] = new
        return new

    def dec(self, amount: float = 1.0, **labels: Any) -> float:
        return self.inc(-amount, **labels)

    def value(self, **labels: Any) -> float:
        return self._values.get(self._check(labels), 0.0)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def samples(self) -> list[Sample]:
        with self._lock:
            return [Sample(self.name, k, v) for k, v in sorted(self._values.items())]


class Histogram(_Metric):
    """Cumulative buckets plus ``_sum`` and ``_count``, per the exposition spec."""

    __slots__ = ("_counts", "_sum", "_total", "buckets")

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labelnames)
        ordered = tuple(sorted(float(b) for b in buckets))
        if not ordered:
            raise ValueError(f"{name}: a histogram needs at least one bucket")
        self.buckets = ordered
        self._counts: dict[LabelKey, list[int]] = {}
        self._sum: dict[LabelKey, float] = {}
        self._total: dict[LabelKey, int] = {}

    def observe(self, value: float, **labels: Any) -> None:
        if math.isnan(value):
            raise ValueError(f"{self.name}: refusing to observe NaN")
        key = self._check(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for i, upper in enumerate(self.buckets):
                if value <= upper:
                    counts[i] += 1
            self._sum[key] = self._sum.get(key, 0.0) + float(value)
            self._total[key] = self._total.get(key, 0) + 1

    def count(self, **labels: Any) -> int:
        return self._total.get(self._check(labels), 0)

    def sum(self, **labels: Any) -> float:
        return self._sum.get(self._check(labels), 0.0)

    def mean(self, **labels: Any) -> float:
        key = self._check(labels)
        n = self._total.get(key, 0)
        return self._sum.get(key, 0.0) / n if n else 0.0

    def samples(self) -> list[Sample]:
        out: list[Sample] = []
        with self._lock:
            for key in sorted(self._counts):
                counts = self._counts[key]
                for upper, count in zip(self.buckets, counts, strict=True):
                    out.append(
                        Sample(f"{self.name}_bucket", (*key, ("le", _fmt(upper))), float(count))
                    )
                out.append(
                    Sample(f"{self.name}_bucket", (*key, ("le", "+Inf")), float(self._total[key]))
                )
                out.append(Sample(f"{self.name}_sum", key, self._sum[key]))
                out.append(Sample(f"{self.name}_count", key, float(self._total[key])))
        return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class MetricRegistry:
    """A named collection.  Re-registering the same name/kind returns the same
    object, so module-level helpers are safe to import twice."""

    metrics: dict[str, _Metric] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def counter(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> Counter:
        return self._get_or_create(Counter, name, help_text, labelnames)

    def gauge(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> Gauge:
        return self._get_or_create(Gauge, name, help_text, labelnames)

    def histogram(
        self,
        name: str,
        help_text: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> Histogram:
        with self._lock:
            existing = self.metrics.get(name)
            if existing is not None:
                if not isinstance(existing, Histogram):
                    raise ValueError(f"{name} is already registered as {type(existing).__name__}")
                return existing
            created = Histogram(name, help_text, labelnames, buckets)
            self.metrics[name] = created
            return created

    def _get_or_create(
        self, cls: type, name: str, help_text: str, labelnames: Sequence[str]
    ) -> Any:
        with self._lock:
            existing = self.metrics.get(name)
            if existing is not None:
                if not isinstance(existing, cls):
                    raise ValueError(f"{name} is already registered as {type(existing).__name__}")
                return existing
            created = cls(name, help_text, labelnames)
            self.metrics[name] = created
            return created

    def get(self, name: str) -> _Metric | None:
        return self.metrics.get(name)

    def value(self, name: str, **labels: Any) -> float:
        metric = self.metrics.get(name)
        if metric is None:
            raise KeyError(f"no metric named {name!r}")
        if isinstance(metric, Histogram):
            return float(metric.count(**labels))
        if isinstance(metric, Counter | Gauge):
            return metric.value(**labels)
        raise TypeError(f"{name!r} is a {type(metric).__name__}, which has no scalar value")

    def reset(self) -> None:
        """Drop every metric.  Tests only -- a live process never calls this."""
        with self._lock:
            self.metrics.clear()

    def collect(self) -> list[tuple[_Metric, list[Sample]]]:
        with self._lock:
            metrics = [self.metrics[n] for n in sorted(self.metrics)]
        return [(m, m.samples()) for m in metrics]  # type: ignore[attr-defined]

    def render(self) -> str:
        return render_prometheus(self)


#: The process-wide registry.  A test that needs isolation builds its own.
REGISTRY = MetricRegistry()


# ---------------------------------------------------------------------------
# Exposition
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    if value == math.inf:
        return "+Inf"
    if value == -math.inf:
        return "-Inf"
    if math.isnan(value):
        return "NaN"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def escape_label_value(value: str) -> str:
    """Per the exposition spec: backslash, double-quote and newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(value: str) -> str:
    """HELP escapes backslash and newline only -- NOT the double quote."""
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _render_labels(labels: LabelKey) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{escape_label_value(v)}"' for k, v in labels)
    return "{" + inner + "}"


def render_prometheus(registry: MetricRegistry | None = None) -> str:
    """Render the registry as a Prometheus ``text/plain; version=0.0.4`` body."""
    reg = registry if registry is not None else REGISTRY
    lines: list[str] = []
    for metric, samples in reg.collect():
        kind = (
            MetricKind.HISTOGRAM
            if isinstance(metric, Histogram)
            else MetricKind.COUNTER
            if isinstance(metric, Counter)
            else MetricKind.GAUGE
        )
        lines.append(f"# HELP {metric.name} {_escape_help(metric.help)}")
        lines.append(f"# TYPE {metric.name} {kind.value}")
        for sample in samples:
            lines.append(f"{sample.name}{_render_labels(sample.labels)} {_fmt(sample.value)}")
    return "\n".join(lines) + "\n"


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def exposition_response(registry: MetricRegistry | None = None) -> tuple[str, str]:
    """``(body, content_type)`` -- whatever serves ``/metrics`` just returns this."""
    return render_prometheus(registry), CONTENT_TYPE


# ---------------------------------------------------------------------------
# The named metrics
# ---------------------------------------------------------------------------

JOBS_STARTED = REGISTRY.counter(
    "fiboki_jobs_started_total", "Jobs dequeued and started.", ("queue", "job_type")
)
JOBS_FINISHED = REGISTRY.counter(
    "fiboki_jobs_finished_total",
    "Jobs that reached a terminal state.",
    ("queue", "job_type", "status"),
)
JOB_DURATION = REGISTRY.histogram(
    "fiboki_job_duration_seconds", "Wall time per job.", ("queue", "job_type")
)
QUEUE_DEPTH = REGISTRY.gauge(
    "fiboki_queue_depth", "Jobs waiting on a queue.", ("queue",)
)
WORKER_UP = REGISTRY.gauge(
    "fiboki_worker_up", "1 when the worker heartbeat is fresh, 0 when stale.", ("worker", "kind")
)
WORKER_HEARTBEAT_AGE = REGISTRY.gauge(
    "fiboki_worker_heartbeat_age_seconds",
    "Seconds since this worker last wrote a heartbeat.",
    ("worker", "kind"),
)
WORKER_CYCLES = REGISTRY.counter(
    "fiboki_worker_cycles_total", "Worker cycles, by outcome.", ("worker", "outcome")
)
BACKTEST_WALL_TIME = REGISTRY.histogram(
    "fiboki_backtest_wall_seconds",
    "Wall time of one backtest run.",
    ("strategy", "instrument"),
)
DATA_FRESHNESS = REGISTRY.gauge(
    "fiboki_data_age_seconds",
    "Age of the newest stored bar, per instrument and timeframe.",
    ("instrument", "timeframe"),
)
BROKER_UP = REGISTRY.gauge(
    "fiboki_broker_up", "1 when the broker answered its last health probe.", ("broker", "mode")
)
BROKER_LATENCY = REGISTRY.histogram(
    "fiboki_broker_latency_seconds",
    "Round-trip latency of a broker probe or call.",
    ("broker", "operation"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
RECONCILIATION_DIVERGENCE = REGISTRY.gauge(
    "fiboki_reconciliation_divergence",
    "Count of intents that disagree with the venue, by divergence kind.",
    ("kind",),
)
SPREAD_REALISED = REGISTRY.gauge(
    "fiboki_spread_realised_points",
    "Most recent realised spread, in instrument points.",
    ("instrument",),
)
SPREAD_MODELLED = REGISTRY.gauge(
    "fiboki_spread_modelled_points",
    "Spread the cost model assumed, in instrument points.",
    ("instrument",),
)
SPREAD_DIVERGENCE = REGISTRY.gauge(
    "fiboki_spread_divergence_ratio",
    "realised / modelled spread. Above 1 means the backtest is optimistic.",
    ("instrument",),
)
ALERTS_DISPATCHED = REGISTRY.counter(
    "fiboki_alerts_total", "Alerts dispatched, by event and severity.", ("event", "severity")
)
ALERT_CHANNEL_FAILURES = REGISTRY.counter(
    "fiboki_alert_channel_failures_total",
    "Alert deliveries a channel could not complete.",
    ("channel",),
)


def record_job_started(queue: str, job_type: str) -> None:
    JOBS_STARTED.inc(queue=queue, job_type=job_type)


def record_job_finished(queue: str, job_type: str, status: str, seconds: float) -> None:
    JOBS_FINISHED.inc(queue=queue, job_type=job_type, status=status)
    JOB_DURATION.observe(seconds, queue=queue, job_type=job_type)


def record_queue_depth(queue: str, depth: int) -> None:
    QUEUE_DEPTH.set(depth, queue=queue)


def record_worker_liveness(worker: str, kind: str, *, age_seconds: float, fresh: bool) -> None:
    WORKER_UP.set(1.0 if fresh else 0.0, worker=worker, kind=kind)
    WORKER_HEARTBEAT_AGE.set(age_seconds, worker=worker, kind=kind)


def observe_backtest_wall_time(seconds: float, *, strategy: str, instrument: str) -> None:
    BACKTEST_WALL_TIME.observe(seconds, strategy=strategy, instrument=instrument)


def record_data_freshness(instrument: str, timeframe: str, age_seconds: float) -> None:
    DATA_FRESHNESS.set(age_seconds, instrument=instrument, timeframe=timeframe)


def record_broker_health(
    broker: str, mode: str, *, reachable: bool, latency_seconds: float | None = None
) -> None:
    BROKER_UP.set(1.0 if reachable else 0.0, broker=broker, mode=mode)
    if latency_seconds is not None:
        BROKER_LATENCY.observe(latency_seconds, broker=broker, operation="health")


def record_reconciliation_divergence(**counts: int) -> None:
    """``record_reconciliation_divergence(unknown=1, orphan=0, size_mismatch=2)``."""
    for kind, count in counts.items():
        RECONCILIATION_DIVERGENCE.set(float(count), kind=kind)


def record_spread_divergence(instrument: str, *, realised: float, modelled: float) -> float:
    """Track realised vs modelled spread, and return the ratio.

    A ratio persistently above 1 is the single clearest signal that stored
    backtest results have stopped describing reality, so it is a metric rather
    than a research finding somebody might notice.
    """
    SPREAD_REALISED.set(realised, instrument=instrument)
    SPREAD_MODELLED.set(modelled, instrument=instrument)
    ratio = realised / modelled if modelled else math.inf
    SPREAD_DIVERGENCE.set(ratio, instrument=instrument)
    return ratio


class timer:
    """``with timer(HISTOGRAM, queue="research"):`` -- observes on exit, always."""

    __slots__ = ("_hist", "_labels", "_start", "elapsed")

    def __init__(self, histogram: Histogram, **labels: Any) -> None:
        self._hist = histogram
        self._labels = labels
        self._start = 0.0
        self.elapsed = 0.0

    def __enter__(self) -> timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.perf_counter() - self._start
        self._hist.observe(self.elapsed, **self._labels)


def snapshot(registry: MetricRegistry | None = None) -> dict[str, Any]:
    """A plain-dict view, for a health endpoint or a CLI table."""
    reg = registry if registry is not None else REGISTRY
    out: dict[str, Any] = {}
    for metric, samples in reg.collect():
        out[metric.name] = [
            {"labels": s.label_dict(), "value": s.value, "name": s.name} for s in samples
        ]
    return out


def iter_metric_names(registry: MetricRegistry | None = None) -> Iterable[str]:
    reg = registry if registry is not None else REGISTRY
    return sorted(reg.metrics)
