"""Research memory over HTTP: counts, a slim recall, and incomparable rows."""
from __future__ import annotations

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.research.experiment import ActorKind, ExperimentDraft, ExperimentLedger
from tests.api.conftest import ADMIN_PW, ORIGIN, login
from tests.validation_fixtures import seed_document

from fastapi.testclient import TestClient


def _draft(**kwargs: object) -> ExperimentDraft:
    base = {
        "actor_kind": ActorKind.AGENT,
        "actor_name": "agent:research-loop",
        "reason": "record a prior attempt before spending another look",
    }
    return ExperimentDraft(**{**base, **kwargs})


def _client(api_env: dict[str, str], db: str) -> TestClient:
    settings = load_settings({**api_env, "FIBOKI_EXPERIMENT_DB": db})
    app = create_app(settings, configure_logs=False)
    client = TestClient(app, base_url=ORIGIN)
    client.__enter__()
    response = login(client, "joe", ADMIN_PW)
    assert response.status_code == 200, response.text
    return client


def test_a_missing_ledger_stays_absent(admin_client) -> None:
    body = admin_client.get("/api/intelligence/research-memory").json()
    assert body["source"]["kind"] == "absent"
    assert body["data"]["available"] is False
    assert body["data"]["structures_recorded"]["value"] is None


def test_an_empty_recall_query_is_rejected(admin_client) -> None:
    response = admin_client.get("/api/intelligence/research-memory/recall?q=")
    assert response.status_code == 422
    assert response.json()["code"] == "recall_query_empty"


def test_a_comparable_structure_is_counted_and_recall_hides_the_report(
    api_env, tmp_path, monkeypatch
) -> None:
    db = tmp_path / "experiments.sqlite"
    doc = seed_document()
    with ExperimentLedger(db) as ledger:
        ledger.create(_draft(strategy_document=doc))
    from fiboki.research import experiment as experiment_mod

    monkeypatch.setattr(experiment_mod, "strategy_key_version", lambda: "stale-key-version")
    with ExperimentLedger(db) as ledger:
        ledger.create(_draft(strategy_document=seed_document(), strategy_id="stale_copy"))

    client = _client(api_env, str(db))
    try:
        summary = client.get("/api/intelligence/research-memory").json()
        assert summary["data"]["available"] is True
        assert summary["data"]["structures_recorded"]["value"] == 1
        assert summary["data"]["structures_recorded"]["provenance"] == "backtest"
        assert summary["data"]["rediscovery_rate"]["value"] is None
        recall = client.get(
            "/api/intelligence/research-memory/recall",
            params={"q": "donchian breakout"},
        ).json()
        assert recall["data"]["novel"] is False
        assert recall["data"]["incomparable"]
        blob = client.get(
            "/api/intelligence/research-memory/recall",
            params={"q": "donchian breakout"},
        ).text
        assert "validation_report" not in blob
        for match in recall["data"]["matches"]:
            assert isinstance(match["similarity"], dict)
            assert match["similarity"]["provenance"] == "backtest"
            assert match["similarity"]["estimated"] is True
    finally:
        client.close()
