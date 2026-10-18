"""Structural proof that no unlabelled number can leave the API.

This walks every response model registered on the app and fails on a bare
numeric field. It is the machine-checkable version of "a ProvenanceChip renders
beside EVERY number": if the server cannot emit an unlabelled figure, the UI
cannot render one.
"""
from __future__ import annotations

import typing

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from fiboki.api.provenance import Figure
from fiboki.core.enums import Provenance

NUMERIC = (int, float)

#: Fields that are counts of rows, page offsets or identifiers rather than
#: measurements of the market or the book. Each is named, never pattern-matched.
#: Model names are compared with any generic parameter stripped, so ``Page`` here
#: covers ``Page[TradeRowView]``.
ALLOWED_BARE_NUMERIC = {
    # Figure and Series ARE the labelling wrappers; their payload fields are the
    # numbers being labelled, so exempting them is not a loophole.
    ("Figure", "value"),
    ("Figure", "sample_size"),
    ("SeriesPoint", "v"),
    ("Page", "total"),
    ("Page", "offset"),
    ("Page", "limit"),
    ("HealthReport", "uptime_seconds"),
    ("HealthReport", "worker_heartbeat_age_seconds"),
    # The configured threshold the worker verdict used (settings, like
    # SettingsView.session_ttl_seconds), not a measurement.
    ("HealthReport", "worker_stale_after_seconds"),
    # A display step (0 to 5) of the utilisation Figure on the same row, so the
    # exposure matrix bins nothing itself; the number it steps is labelled.
    ("LimitRowView", "heat"),
    # A search-match rank (0 exact to 3 substring): an ordinal of string
    # matching, not a figure about the market or the book.
    ("SearchHit", "tier"),
    ("HealthCheck", "latency_ms"),
    ("AuditEntryView", "sequence"),
    ("AuditIntegrityView", "first_broken_sequence"),
    ("PrincipalView", "expires_at"),
    ("SettingsView", "session_ttl_seconds"),
    ("SettingsView", "allowed_origin_count"),
    ("KillSwitchView", "open_positions"),
    ("ServiceRow", "latency_ms"),
    # Operator drawings (routers/chart.py): an anchor is a point an operator
    # placed by hand, an annotation and not a measured or computed figure; the
    # versions are the set's optimistic-concurrency counters; the maximum is a
    # configured limit.
    ("DrawingPoint", "price"),
    ("DrawingSetView", "version"),
    ("DrawingSetView", "max_drawings"),
    ("OtherDrawingSet", "version"),
    # Research analytics (routers/research_analytics.py). Every MEASURED number
    # there is a Figure, or a list inside a container that declares its
    # provenance and unit (Series, HistogramView, ExcursionTable, the fan).
    # These are indices, calendar keys, sample sizes, counts of rows or runs,
    # and configuration constants (a gate threshold, a seed, a window), not
    # measurements of the market or the book.
    ("GateCheckView", "threshold"),  # the gate set's declared threshold
    ("GateCheckView", "rung"),  # ladder rung index
    ("PlateauGateView", "threshold"),  # the gate set's declared threshold
    ("DrawdownPeriodView", "rank"),  # 1..5, ordinal
    ("MonthlyCellView", "year"),  # calendar key
    ("MonthlyCellView", "month"),  # calendar key
    ("YearReturnView", "year"),  # calendar key
    ("HistogramView", "n"),  # sample size of the binned trades
    ("ComparedRunView", "daily_returns"),  # sample size (days)
    ("ComparisonView", "min_common_days"),  # configured minimum overlap
    ("CorrelationCellView", "n_common"),  # sample size, also on rho.sample_size
    ("CorrelationMatrixView", "window"),  # requested window, configuration
    ("CorrelationMatrixView", "bars_used"),  # sample size of the matrix
    ("MonteCarloView", "seed"),  # RNG seed, shown so a re-draw is exact
    ("MonteCarloView", "n_paths"),  # requested path count, configuration
    ("MonteCarloView", "n_trades"),  # sample size resampled
    ("MonteCarloView", "risk_fraction"),  # sizing configuration of the run
    ("MonteCarloView", "ruin_fraction"),  # requested ruin definition
    ("RungView", "index"),  # ladder rung index
    ("FoldView", "fold"),  # fold index
    ("SurfaceCellView", "x"),  # declared parameter value (grid coordinate)
    ("SurfaceCellView", "y"),  # declared parameter value (grid coordinate)
    ("SurfaceCellView", "n_runs"),  # count of stored runs in the cell
    ("ParameterSurfaceView", "radius"),  # ladder configuration
    ("ParameterSurfaceView", "runs_considered"),  # row counts
    ("ParameterSurfaceView", "runs_used"),
    ("ParameterSurfaceView", "runs_other_params"),
    ("ParameterSurfaceView", "runs_unknown_params"),
    ("ProvenanceBlock", "initial_balance"),  # run configuration, as stored
    ("StrategyOverviewView", "runs_total"),  # row count
    ("TearsheetView", "rolling_window_days"),  # configured window
    ("WalkForwardView", "n_folds"),  # fold count
    ("WalkForwardView", "parameter_repeats"),  # count of folds
    ("JobView", "attempts"),  # job ledger counter
    ("JobView", "max_attempts"),  # job configuration
    ("ShardView", "done"),  # rows written by the shard
    ("ShardView", "total"),  # rows the shard declares
}


def _models(app):
    seen: dict[str, type[BaseModel]] = {}

    def visit(annotation) -> None:
        if annotation is None:
            return
        origin = typing.get_origin(annotation)
        if origin is not None:
            for arg in typing.get_args(annotation):
                visit(arg)
            return
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation.__name__ in seen:
                return
            seen[annotation.__name__] = annotation
            for field in annotation.model_fields.values():
                visit(field.annotation)

    for route in app.routes:
        if isinstance(route, APIRoute):
            visit(route.response_model)
    return seen


def _is_bare_numeric(annotation) -> bool:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        return any(_is_bare_numeric(a) for a in typing.get_args(annotation))
    return annotation in NUMERIC


def test_no_response_model_exposes_an_unlabelled_number(client):
    offenders = []
    for name, model in _models(client.app).items():
        base = name.split("[", 1)[0]
        for field_name, field in model.model_fields.items():
            if (base, field_name) in ALLOWED_BARE_NUMERIC:
                continue
            if field.annotation in (bool,):
                continue
            if _is_bare_numeric(field.annotation):
                offenders.append(f"{name}.{field_name}")
    assert not offenders, (
        "These response fields are bare numbers with no Provenance. Wrap them in "
        "fiboki.api.provenance.Figure, or name them in ALLOWED_BARE_NUMERIC with "
        "a reason: " + ", ".join(sorted(offenders))
    )


def test_figure_cannot_be_built_without_a_provenance():
    # Pydantic refuses the construction, so there is no code path anywhere in
    # the API that can emit a number whose origin is unstated.
    with pytest.raises(ValidationError):
        Figure(value=1.0)  # type: ignore[call-arg]


def test_missing_renders_as_none_not_zero():
    figure = Figure.missing(Provenance.PAPER, unit="GBP", reason="no broker session")
    assert figure.value is None
    assert figure.caveats[0].code == "value_unavailable"


@pytest.mark.parametrize(
    "path",
    [
        "/api/trading/trades",
        "/api/trading/positions",
        "/api/trading/candidates",
        "/api/trading/exposure",
        "/api/markets/instruments",
        "/api/research/strategies",
    ],
)
def test_every_figure_in_a_live_response_carries_a_provenance(admin_client, path):
    body = admin_client.get(path).json()
    found = _walk_figures(body)
    assert found, f"{path} returned no figures at all"
    valid = {p.value for p in Provenance}
    for figure in found:
        assert figure["provenance"] in valid, figure


def _walk_figures(node) -> list[dict]:
    out: list[dict] = []
    if isinstance(node, dict):
        if "provenance" in node and "value" in node and "unit" in node:
            out.append(node)
        for value in node.values():
            out.extend(_walk_figures(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_figures(item))
    return out
