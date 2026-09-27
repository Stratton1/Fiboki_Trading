"""The Experiments page must show what the ledger actually holds.

The defect this file exists to catch
------------------------------------
``ExperimentView`` was built from ``getattr(r, "experiment_id", "")``,
``getattr(r, "actor", "")`` and ``getattr(r, "hypothesis", "")``.
:class:`fiboki.research.experiment.Experiment` names those three fields ``id``,
``actor_name`` and ``hypothesis_id``. Because every read went through a
``getattr`` default, three wrong attribute names produced empty strings instead
of an ``AttributeError`` -- so a ledger holding 604 real research records
rendered 604 rows whose id, actor and hypothesis columns were all blank. The
page looked populated and identified nothing.

A row that cannot be identified cannot be followed up, which is the whole point
of an append-only ledger, so this is asserted against a ledger with real content
rather than against the view's field names.
"""
from __future__ import annotations

import pytest

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)
from tests.api.conftest import ADMIN_PW, login

pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def ledger_env(api_env, tmp_path):
    """An API environment whose experiment ledger holds two known rows."""
    db = tmp_path / "experiments.sqlite"
    with ExperimentLedger(db) as ledger:
        first = ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.AGENT,
                actor_name="script:build_research_ledger",
                reason="a row whose identity the page must be able to show",
                hypothesis_id="gold_range_breakout_persists",
                strategy_id="donchian_breakout_atr_m3ecf77ae",
                dataset_version_id="ds_3c1473cbf51e5a641ef41cbf",
                outcome=Outcome.REJECTED,
                conclusion="died at RUNG 2 WALK_FORWARD",
            )
        )
        second = ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.HUMAN,
                actor_name="joe",
                reason="a second row, by a different actor, in a different state",
                hypothesis_id="kumo_state_conditions_trend",
                strategy_id="ichimoku_kumo_trend",
                outcome=Outcome.INCONCLUSIVE,
            )
        )
    return {**api_env, "FIBOKI_EXPERIMENT_DB": str(db)}, first, second


@pytest.fixture
def ledger_client(ledger_env):
    env, first, second = ledger_env
    settings = load_settings(env)
    app = create_app(settings, configure_logs=False)
    with TestClient(app, base_url=env["FIBOKI_ALLOWED_ORIGINS"]) as client:
        assert login(client, "joe", ADMIN_PW).status_code == 200
        yield client, first, second


def test_every_experiment_row_carries_its_own_id(ledger_client):
    client, first, second = ledger_client
    body = client.get("/api/research/experiments?limit=50").json()
    assert body["items"], "the ledger has rows; the page returned none"
    ids = [row["experiment_id"] for row in body["items"]]
    assert "" not in ids, (
        "a row was rendered with no experiment id: the view is reading an "
        "attribute the ledger does not have"
    )
    assert set(ids) == {first.id, second.id}


def test_every_experiment_row_names_its_actor(ledger_client):
    client, first, second = ledger_client
    body = client.get("/api/research/experiments?limit=50").json()
    by_id = {row["experiment_id"]: row for row in body["items"]}
    assert by_id[first.id]["actor"] == "script:build_research_ledger"
    assert by_id[second.id]["actor"] == "joe"
    assert by_id[first.id]["actor_kind"] == "agent"
    assert by_id[second.id]["actor_kind"] == "human"


def test_an_experiment_row_names_the_hypothesis_it_tested(ledger_client):
    client, first, second = ledger_client
    body = client.get("/api/research/experiments?limit=50").json()
    by_id = {row["experiment_id"]: row for row in body["items"]}
    assert by_id[first.id]["hypothesis"] == "gold_range_breakout_persists"
    assert by_id[second.id]["hypothesis"] == "kumo_state_conditions_trend"


def test_the_remaining_columns_still_come_through(ledger_client):
    """Guard the fields that were already correct against a careless fix."""
    client, first, second = ledger_client
    body = client.get("/api/research/experiments?limit=50").json()
    by_id = {row["experiment_id"]: row for row in body["items"]}
    row = by_id[first.id]
    assert row["strategy_id"] == "donchian_breakout_atr_m3ecf77ae"
    assert row["outcome"] == "rejected"
    assert row["verdict"] == "rejected"
    assert row["dataset_version_id"] == "ds_3c1473cbf51e5a641ef41cbf"
    assert row["created_at"], "a record with no timestamp is not a history"
    assert body["source"]["kind"] == "live"
