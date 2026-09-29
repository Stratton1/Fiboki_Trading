"""The Ollama provider against a real ``httpx.Client`` on a recorded transport.

No network: every request is answered by ``httpx.MockTransport`` from recorded
Ollama responses, so the code path under test is the production one down to
the client, and only the socket is replaced.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest

from fiboki.agents.audit import NO_MODEL, ActionKind, AuditLedger, Outcome
from fiboki.agents.capabilities import CapabilityResolver
from fiboki.agents.providers import (
    EchoProvider,
    LLMRequest,
    LocalHTTPProvider,
    ModelCapabilities,
    ModelRouter,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderUnavailable,
    TaskClass,
    inline_json_schema_refs,
    ollama_http_client,
    smoke_test_provider,
)
from fiboki.agents.roles import AgentRole
from fiboki.agents.session import BudgetExceeded, open_session
from fiboki.agents.tools import REGISTRY, ToolContext
from fiboki.research.artefacts import ResearchStore
from fiboki.strategy.registry import StrategyRegistry

MODEL = "qwen2.5:7b-instruct"
MANIFEST_DIGEST = "a" * 64
BLOB = "b" * 64

#: Recorded shapes of Ollama 0.x responses (trimmed; values illustrative).
TAGS = {
    "models": [
        {"name": "llama3.1:latest", "model": "llama3.1:latest", "digest": "c" * 64},
        {"name": MODEL, "model": MODEL, "digest": MANIFEST_DIGEST,
         "details": {"family": "qwen2", "parameter_size": "7.6B"}},
    ]
}
SHOW = {
    "modelfile": f"# Modelfile\nFROM /Users/joe/.ollama/models/blobs/sha256-{BLOB}\nTEMPLATE x\n",
    "details": {"family": "qwen2", "parameter_size": "7.6B",
                "quantization_level": "Q4_K_M", "format": "gguf"},
}


def _chat(content: str, **extra: Any) -> dict[str, Any]:
    return {"model": MODEL, "message": {"role": "assistant", "content": content},
            "done": True, "done_reason": "stop", "prompt_eval_count": 42,
            "eval_count": 9, **extra}


class Recorded:
    """Route -> list of responses (or exceptions); records every request."""

    def __init__(self, routes: dict[str, list[Any]]) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get(f"{request.method} {request.url.path}")
        if not queue:
            return httpx.Response(404, json={"error": f"no recording for {request.url.path}"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body = item if isinstance(item, tuple) else (200, item)
        return httpx.Response(status, json=body)

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def body(self, index: int) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def _provider(routes: dict[str, list[Any]], **kwargs: Any) -> tuple[LocalHTTPProvider, Recorded]:
    recorded = Recorded(routes)
    client = httpx.Client(transport=httpx.MockTransport(recorded), trust_env=False)
    return LocalHTTPProvider.for_ollama(MODEL, client=client, **kwargs), recorded


def _full_routes(chat: Any) -> dict[str, list[Any]]:
    return {"GET /api/tags": [TAGS], "POST /api/show": [SHOW], "POST /api/chat": [chat]}


# ------------------------------------------------------------ fingerprint


def test_fingerprint_prefers_the_manifest_digest_from_tags_and_is_cached() -> None:
    provider, recorded = _provider(_full_routes(_chat("{}")))
    fp = provider.model_fingerprint(MODEL)
    assert fp.as_dict() == {"model_id": MODEL, "digest": f"sha256:{MANIFEST_DIGEST}"}
    assert fp.source == "ollama:/api/tags manifest digest"
    assert fp.details["quantization_level"] == "Q4_K_M"
    assert recorded.body(0) == {"model": MODEL}
    before = len(recorded.requests)
    assert provider.model_fingerprint(MODEL) is fp
    assert len(recorded.requests) == before  # cached: no second round trip


def test_fingerprint_falls_back_to_the_weights_blob_in_the_modelfile() -> None:
    provider, _ = _provider({"GET /api/tags": [{"models": []}], "POST /api/show": [SHOW]})
    fp = provider.model_fingerprint(MODEL)
    assert fp.digest == f"sha256:{BLOB}"
    assert "modelfile" in fp.source


def test_an_untagged_name_matches_latest() -> None:
    recorded = Recorded({"GET /api/tags": [TAGS], "POST /api/show": [SHOW]})
    client = httpx.Client(transport=httpx.MockTransport(recorded), trust_env=False)
    provider = LocalHTTPProvider.for_ollama("llama3.1", client=client)
    assert provider.model_fingerprint("llama3.1").digest == "sha256:" + "c" * 64


def test_a_model_that_cannot_be_pinned_is_refused() -> None:
    provider, _ = _provider(
        {"GET /api/tags": [{"models": []}], "POST /api/show": [{"modelfile": "FROM llama"}]}
    )
    with pytest.raises(ProviderError, match="refusing to run an unpinned local model"):
        provider.model_fingerprint(MODEL)


def test_a_missing_model_is_an_error_not_a_blank_digest() -> None:
    provider, _ = _provider(
        {"POST /api/show": [(404, {"error": f"model '{MODEL}' not found"})]}
    )
    with pytest.raises(ProviderError, match="HTTP 404"):
        provider.model_fingerprint(MODEL)


def test_hosted_providers_report_no_digest_rather_than_inventing_one() -> None:
    spec = ModelCapabilities(model="gpt-x", provider="openai_compatible", context_window=8000)
    fp = OpenAICompatibleProvider(models=(spec,)).model_fingerprint("gpt-x")
    assert fp.digest is None
    assert fp.source.startswith("unavailable")


# ------------------------------------------------------------- generation


def test_generation_sends_schema_num_ctx_and_seed_in_one_request() -> None:
    provider, recorded = _provider(_full_routes(_chat('{"a": 1}')), num_ctx=4096, seed=7)
    schema = REGISTRY.get("design_experiment").input_model.model_json_schema()
    assert "$defs" in schema  # the thing being inlined
    response = provider.generate(
        LLMRequest(system="s", prompt="p", task_class=TaskClass.EXPERIMENT_DESIGN,
                   json_only=True, json_schema=schema, max_tokens=256),
        MODEL,
    )
    assert recorded.paths() == ["POST /api/chat"]
    sent = recorded.body(0)
    assert sent["stream"] is False
    assert sent["options"] == {"temperature": 0.0, "num_predict": 256, "num_ctx": 4096, "seed": 7}
    assert isinstance(sent["format"], dict)
    assert "$ref" not in json.dumps(sent["format"])
    assert "$defs" not in sent["format"]
    item = sent["format"]["properties"]["success_criteria"]["items"]
    assert set(item["properties"]) >= {"metric", "comparator", "threshold"}
    assert response.json_payload() == {"a": 1}
    assert (response.prompt_tokens, response.completion_tokens) == (42, 9)


def test_json_only_without_a_schema_sends_format_json() -> None:
    provider, recorded = _provider(_full_routes(_chat("{}")))
    provider.generate(
        LLMRequest(system="s", prompt="p", task_class=TaskClass.CRITIQUE, json_only=True), MODEL
    )
    assert recorded.body(0)["format"] == "json"


def test_a_prompt_that_would_be_truncated_is_refused_before_sending() -> None:
    provider, recorded = _provider(_full_routes(_chat("{}")), num_ctx=512)
    with pytest.raises(ProviderError, match="refusing rather than letting the server truncate"):
        provider.generate(
            LLMRequest(system="s" * 1200, prompt="p" * 800, task_class=TaskClass.CRITIQUE,
                       max_tokens=64),
            MODEL,
        )
    assert recorded.requests == []


def test_a_server_error_is_one_request_and_no_retry() -> None:
    provider, recorded = _provider({"POST /api/chat": [(500, {"error": "CUDA out of memory"})]})
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.generate(LLMRequest(system="s", prompt="p", task_class=TaskClass.CRITIQUE), MODEL)
    assert recorded.paths() == ["POST /api/chat"]


def test_a_connection_failure_is_one_attempt_and_unavailable() -> None:
    provider, recorded = _provider(
        {"POST /api/chat": [httpx.ConnectError("connection refused")]}
    )
    with pytest.raises(ProviderUnavailable, match="connection refused"):
        provider.generate(LLMRequest(system="s", prompt="p", task_class=TaskClass.CRITIQUE), MODEL)
    assert len(recorded.requests) == 1


def test_an_error_body_with_a_200_is_still_an_error() -> None:
    provider, _ = _provider({"POST /api/chat": [{"error": "model is loading"}]})
    with pytest.raises(ProviderError, match="model is loading"):
        provider.generate(LLMRequest(system="s", prompt="p", task_class=TaskClass.CRITIQUE), MODEL)


def test_the_real_client_factory_has_no_retries_and_ignores_proxy_env() -> None:
    client = ollama_http_client(timeout=30.0, connect_timeout=2.0)
    try:
        assert isinstance(client, httpx.Client)
        assert client.trust_env is False
        assert client.timeout.connect == 2.0
        assert client.timeout.read == 30.0
    finally:
        client.close()


def test_schema_inlining_refuses_a_recursive_schema() -> None:
    recursive = {"$defs": {"N": {"type": "object", "properties": {"n": {"$ref": "#/$defs/N"}}}},
                 "$ref": "#/$defs/N"}
    with pytest.raises(ValueError, match="recursive"):
        inline_json_schema_refs(recursive)


def test_request_digest_is_unchanged_when_no_schema_is_given() -> None:
    """Digests recorded before ``json_schema`` existed must still reproduce."""
    request = LLMRequest(system="s", prompt="p", task_class=TaskClass.CRITIQUE, json_only=True)
    legacy = hashlib.sha256(
        json.dumps(
            {"system": "s", "prompt": "p", "task_class": "critique", "max_tokens": 1024,
             "temperature": 0.0, "json_only": True, "stop": []},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert request.digest() == legacy
    with_schema = LLMRequest(system="s", prompt="p", task_class=TaskClass.CRITIQUE,
                             json_only=True, json_schema={"type": "object"})
    assert with_schema.digest() != legacy


# --------------------------------------------------------------- smoke test


def test_smoke_test_passes_on_a_pinned_model_that_returns_the_json() -> None:
    provider, recorded = _provider(_full_routes(_chat('{"status": "ok", "sum": 5}')))
    report = smoke_test_provider(provider)
    assert report.ok, report.errors
    assert report.model_digest == f"sha256:{MANIFEST_DIGEST}"
    assert report.json_parsed and report.arithmetic_ok
    assert report.prompt_tokens == 42
    chat = [r for r in recorded.requests if r.url.path == "/api/chat"]
    assert len(chat) == 1
    assert json.loads(chat[0].content)["format"]["required"] == ["status", "sum"]
    assert report.as_dict()["model_id"] == MODEL


def test_smoke_test_reports_wrong_arithmetic_separately_from_plumbing() -> None:
    provider, _ = _provider(_full_routes(_chat('{"status": "ok", "sum": 6}')))
    report = smoke_test_provider(provider)
    assert report.ok and not report.arithmetic_ok


def test_smoke_test_fails_on_prose_and_on_an_unpinned_model() -> None:
    provider, _ = _provider(_full_routes(_chat("Sure! Here is the JSON you asked for")))
    report = smoke_test_provider(provider)
    assert not report.ok
    assert any("not valid JSON" in e for e in report.errors)

    unpinned, _ = _provider(
        {"GET /api/tags": [{"models": []}], "POST /api/show": [{"modelfile": ""}],
         "POST /api/chat": [_chat('{"status": "ok", "sum": 5}')]}
    )
    report = smoke_test_provider(unpinned)
    assert not report.ok
    assert report.model_digest is None
    assert any(e.startswith("fingerprint:") for e in report.errors)


# ------------------------------------------------- session stamps provenance


def _context() -> ToolContext:
    return ToolContext(research=ResearchStore(), strategies=StrategyRegistry())


def test_a_session_stamps_model_id_digest_and_manifest_on_every_record() -> None:
    provider, _ = _provider(_full_routes(_chat('{"question": "q"}')))
    ledger = AuditLedger()
    session = open_session(
        agent_id="dir", role=AgentRole.RESEARCH_DIRECTOR, context=_context(),
        resolver=CapabilityResolver(), ledger=ledger, router=ModelRouter([provider]),
        manifest_hash="m" * 64,
    )
    session.try_call("query_research_memory", {"query": "x"}, reason="before thinking")
    session.think("frame it", reason="test", step="research_brief")
    session.try_call("query_research_memory", {"query": "y"}, reason="after thinking")
    first, thought, second = ledger.records()
    assert (first.model_id, first.model_digest) == (NO_MODEL, None)
    assert thought.kind is ActionKind.MODEL_CALL
    assert thought.model_id == MODEL
    assert thought.model_digest == f"sha256:{MANIFEST_DIGEST}"
    assert (second.model_id, second.model_digest) == (MODEL, f"sha256:{MANIFEST_DIGEST}")
    assert {r.manifest_hash for r in ledger.records()} == {"m" * 64}
    assert ledger.verify_chain()


def test_an_unpinnable_model_fails_the_step_on_the_record_without_generating() -> None:
    provider, recorded = _provider(
        {"GET /api/tags": [{"models": []}], "POST /api/show": [{"modelfile": ""}],
         "POST /api/chat": [_chat("{}")]}
    )
    ledger = AuditLedger()
    session = open_session(
        agent_id="dir", role=AgentRole.RESEARCH_DIRECTOR, context=_context(),
        resolver=CapabilityResolver(), ledger=ledger, router=ModelRouter([provider]),
    )
    with pytest.raises(ProviderError):
        session.think("frame it", reason="test")
    (record,) = ledger.records()
    assert record.outcome is Outcome.ERROR
    assert record.model_id == MODEL and record.model_digest is None
    assert "/api/chat" not in [r.url.path for r in recorded.requests]


class _PricedEcho(EchoProvider):
    """An offline provider that charges, to exercise the budget accounting."""

    def models(self) -> tuple[ModelCapabilities, ...]:
        (spec,) = super().models()
        return (ModelCapabilities(
            model=spec.model, provider=self.name, context_window=spec.context_window,
            supports_json_mode=True, input_cost_per_1k_usd=0.0,
            output_cost_per_1k_usd=200.0, local=True, version=spec.version,
        ),)


def test_a_call_refused_by_the_budget_still_records_what_it_cost() -> None:
    """The router's estimate fits the budget; the model's actual output does not.

    The charge is post hoc, so the money is spent by the time the budget
    refuses.  The record must show it, or every budget eval under-counts.
    """
    ledger = AuditLedger()
    verbose = '{"x": "' + "a" * 40_000 + '"}'
    session = open_session(
        agent_id="dir", role=AgentRole.RESEARCH_DIRECTOR, context=_context(),
        resolver=CapabilityResolver(), ledger=ledger,
        router=ModelRouter([_PricedEcho({"research_director": verbose})]),
    )
    with pytest.raises(BudgetExceeded):
        session.think("frame it", reason="test", max_tokens=1)
    (record,) = ledger.records()
    assert record.outcome is Outcome.ERROR
    assert record.cost_usd > session.budget.max_cost_usd  # spent, and on the record
    assert record.prompt_tokens > 0
