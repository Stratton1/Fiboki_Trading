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
    ("HealthCheck", "latency_ms"),
    ("AuditEntryView", "sequence"),
    ("AuditIntegrityView", "first_broken_sequence"),
    ("PrincipalView", "expires_at"),
    ("SettingsView", "session_ttl_seconds"),
    ("SettingsView", "allowed_origin_count"),
    ("KillSwitchView", "open_positions"),
    ("ServiceRow", "latency_ms"),
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
