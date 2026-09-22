"""The lifecycle routes: labelled figures, honest absence, and no write verbs.

``StrategyStatus.to_dict()`` and ``LifecycleEvaluation.to_dict()`` were written
API-shaped and served by nothing, so an operator could not ask the platform why
a strategy had stopped trading. These tests drive the real app.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.routing import APIRoute

from fiboki.core.enums import Provenance, StrategyLifecycle
from fiboki.lifecycle.monitor import ForwardMonitor, MonitorConfig
from fiboki.lifecycle.service import LifecycleService
from tests.lifecycle_fixtures import (
    HASH_A,
    T0,
    expectation,
    machine_at,
    observation,
    rule_parameters,
)

PREFIX = "/api/trading/lifecycle"


def _install(client, *, evaluate: bool = True) -> LifecycleService:
    """Put a real, populated lifecycle service behind the routes."""
    service = LifecycleService(
        machine=machine_at(StrategyLifecycle.PAPER),
        monitor=ForwardMonitor(MonitorConfig()),
        clock=lambda: T0,
    )
    service.pre_register_rules(
        HASH_A,
        parameters=rule_parameters(backtest_sharpe=0.40, drawdown_threshold=0.25),
        registered_by="joe",
        reason="pre-registered before the first paper trade",
        at=T0,
    )
    if evaluate:
        service.evaluate(
            HASH_A,
            expectation=expectation(),
            observation=observation(
                returns=np.random.default_rng(9).normal(-0.004, 0.010, 250)
            ),
        )
    client.app.state.platform._lifecycle = service
    return service


def _figures(node) -> list[dict]:
    out: list[dict] = []
    if isinstance(node, dict):
        if "provenance" in node and "value" in node and "unit" in node:
            out.append(node)
        for value in node.values():
            out.extend(_figures(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_figures(item))
    return out


# --------------------------------------------------------------------------


def test_the_strategy_list_is_served_with_provenance_on_every_number(admin_client):
    _install(admin_client)
    response = admin_client.get(f"{PREFIX}/strategies")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["source"]["kind"] == "live"
    figures = _figures(body)
    assert figures, "the response carried no labelled figures at all"
    valid = {p.value for p in Provenance}
    for item in figures:
        assert item["provenance"] in valid, item
    row = body["items"][0]
    assert row["lifecycle"] == StrategyLifecycle.QUARANTINED.value
    assert row["degraded"] is True
    assert row["latched_halts"], "a latched halt is not reported"
    assert row["health"]["value"] < 1.0


def test_one_strategy_is_served_by_content_hash(admin_client):
    _install(admin_client)
    response = admin_client.get(f"{PREFIX}/strategies/{HASH_A}")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["strategy_content_hash"] == HASH_A
    assert data["ever_evaluated"] is True
    assert data["last_score"]["value"] is not None


def test_the_last_evaluation_is_served_with_its_rule_evaluations(admin_client):
    _install(admin_client)
    response = admin_client.get(f"{PREFIX}/strategies/{HASH_A}/evaluation")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["demoted"] is True
    assert data["state_after"] == StrategyLifecycle.QUARANTINED.value
    assert data["rule_evaluations"], "no stopping rule was reported"
    assert any(item["fired"] for item in data["rule_evaluations"])
    assert data["score"]["value"] is not None
    for item in _figures(data):
        assert "provenance" in item


def test_an_unevaluated_strategy_is_a_404_not_an_empty_evaluation(admin_client):
    """"Nothing has looked" and "healthy" are different answers."""
    _install(admin_client, evaluate=False)
    response = admin_client.get(f"{PREFIX}/strategies/{HASH_A}/evaluation")
    assert response.status_code == 404, response.text
    assert "never evaluated" in response.text

    status_response = admin_client.get(f"{PREFIX}/strategies/{HASH_A}")
    data = status_response.json()["data"]
    assert data["ever_evaluated"] is False
    # The score is explicitly absent rather than zero.
    assert data["last_score"]["value"] is None
    assert data["last_score"]["caveats"][0]["code"] == "value_unavailable"


def test_an_unknown_strategy_is_a_404(admin_client):
    _install(admin_client)
    response = admin_client.get(f"{PREFIX}/strategies/{'f' * 64}")
    assert response.status_code == 404


def test_degraded_only_filters(admin_client):
    _install(admin_client)
    healthy = admin_client.get(f"{PREFIX}/strategies?degraded_only=true").json()
    assert healthy["total"] == 1


def test_the_lifecycle_surface_is_read_only(client):
    """No HTTP verb may put risk back on. Promotion is a human act, on the record."""
    offenders = [
        (route.path, sorted(route.methods))
        for route in client.app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith(PREFIX)
        and set(route.methods) - {"GET", "HEAD", "OPTIONS"}
    ]
    assert not offenders, f"the lifecycle routes expose a write verb: {offenders}"


def test_the_lifecycle_store_is_reported_as_a_named_data_source(admin_client):
    _install(admin_client)
    response = admin_client.get("/api/system/services")
    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()["items"]}
    assert "lifecycle" in names, names


@pytest.mark.parametrize(
    "path",
    [f"{PREFIX}/strategies", f"{PREFIX}/strategies/" + HASH_A],
)
def test_every_figure_in_a_live_response_carries_a_provenance(admin_client, path):
    _install(admin_client)
    found = _figures(admin_client.get(path).json())
    assert found, f"{path} returned no figures at all"
    valid = {p.value for p in Provenance}
    for item in found:
        assert item["provenance"] in valid
