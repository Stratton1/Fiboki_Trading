"""The llama.cpp provider against a real ``httpx.Client`` on a recorded transport.

No network: ``httpx.MockTransport`` answers every request from recorded
``llama-server`` responses, so the code under test is the production path down
to the client and only the socket is replaced.

Where the recorded shapes come from (read 2026-09-29, not recalled):

* ``GET /props`` and ``GET /v1/models``: the "Response format" examples in
  ``tools/server/README.md`` of llama.cpp master
  (https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
  cross-checked against ``get_res_props`` / ``get_res_model_info`` in
  ``tools/server/server-context.cpp``, which report
  ``default_generation_settings.n_ctx`` as the PER-SLOT context and add
  ``aliases`` and ``meta.n_ctx`` to the model entry.
* ``POST /v1/chat/completions`` body and the ``response_format`` forms:
  README "OpenAI-compatible Chat Completions API" and
  ``oaicompat_chat_params_parse`` in ``tools/server/server-common.cpp``,
  whose rejection message for an unknown type is
  ``response_format type must be one of "text" or "json_object", but got: ...``.
* Build numbers: first ``b`` tags containing PR #5978 (b2487), #9527 (b3782)
  and #12168 (b4820) in the llama.cpp git history.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from fiboki.agents import providers as providers_module
from fiboki.agents.audit import AuditLedger
from fiboki.agents.capabilities import CapabilityResolver
from fiboki.agents.providers import (
    LLAMA_CPP_JSON_SCHEMA_BUILD,
    LLAMA_CPP_MIN_SCHEMA_BUILD,
    LlamaCppProvider,
    LLMRequest,
    LocalHTTPProvider,
    ModelRouter,
    ProviderError,
    ProviderUnavailable,
    StructuredMode,
    TaskClass,
    gguf_shard_paths,
    gguf_weights_digest,
    parse_llama_cpp_build,
    smoke_test_provider,
    structured_mode_for_build,
)
from fiboki.agents.roles import AgentRole
from fiboki.agents.session import open_session
from fiboki.agents.tools import ToolContext
from fiboki.research.artefacts import ResearchStore
from fiboki.strategy.registry import StrategyRegistry

ALIAS = "qwen3-14b-q5_k_m"
BUILD = "b6358-c466abe1"


def _props(model_path: str, *, n_ctx: int = 16384, build_info: str | None = BUILD) -> dict[str, Any]:
    """The README ``GET /props`` example, trimmed to the keys the provider reads
    plus a few it does not, in the documented nesting."""
    body: dict[str, Any] = {
        "default_generation_settings": {
            "id": 0,
            "id_task": -1,
            "n_ctx": n_ctx,
            "speculative": False,
            "is_processing": False,
            "params": {"n_predict": -1, "seed": 4294967295, "temperature": 0.800000011920929,
                       "grammar": "", "stream": True},
            "prompt": "",
        },
        "total_slots": 1,
        "model_path": model_path,
        "chat_template": "...",
        "modalities": {"vision": False},
        "is_sleeping": False,
    }
    if build_info is not None:
        body["build_info"] = build_info
    return body


def _models(model_id: str, *, aliases: list[str] | None = None) -> dict[str, Any]:
    """The README ``GET /v1/models`` example (one element, ``meta`` block)."""
    entry: dict[str, Any] = {
        "id": model_id,
        "object": "model",
        "created": 1735142223,
        "owned_by": "llamacpp",
        "meta": {"vocab_type": 2, "n_vocab": 151936, "n_ctx_train": 40960,
                 "n_embd": 5120, "n_params": 14768307200, "size": 10509000000},
    }
    if aliases is not None:
        entry["aliases"] = aliases
    return {"object": "list", "data": [entry]}


def _completion(content: str, **extra: Any) -> dict[str, Any]:
    return {
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "created": 1757141666,
        "id": "chatcmpl-ecQULm0WqPrftUqjPZO1CFYeDjGZNbDu",
        "model": ALIAS,
        "object": "chat.completion",
        "usage": {"completion_tokens": 9, "prompt_tokens": 44, "total_tokens": 53},
        "timings": {"prompt_n": 44, "predicted_n": 9},
        **extra,
    }


class Recorded:
    """Route -> responses (the last one repeats); records every request."""

    def __init__(self, routes: dict[str, list[Any]]) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get(f"{request.method} {request.url.path}")
        if not queue:
            return httpx.Response(404, json={"error": {"code": 404, "message": "File Not Found"}})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body = item if isinstance(item, tuple) else (200, item)
        return httpx.Response(status, json=body)

    def posts(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


@pytest.fixture(autouse=True)
def _fresh_digest_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_module, "_GGUF_DIGESTS", {})


@pytest.fixture
def gguf(tmp_path: Path) -> Path:
    path = tmp_path / "Models" / "Qwen3-14B-GGUF" / "Qwen3-14B-Q5_K_M.gguf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"GGUF" + bytes(range(256)) * 64)
    return path


def _client(recorded: Recorded) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(recorded), trust_env=False)


def _routes(gguf: Path, chat: Any = None, **props_kw: Any) -> dict[str, list[Any]]:
    routes: dict[str, list[Any]] = {
        "GET /v1/models": [_models(ALIAS, aliases=[ALIAS])],
        "GET /props": [_props(str(gguf), **props_kw)],
    }
    if chat is not None:
        routes["POST /v1/chat/completions"] = chat if isinstance(chat, list) else [chat]
    return routes


def _provider(routes: dict[str, list[Any]], model: str | None = ALIAS, **kw: Any
              ) -> tuple[LlamaCppProvider, Recorded]:
    recorded = Recorded(routes)
    provider = LocalHTTPProvider.for_llama_cpp(
        "http://127.0.0.1:8080", model, client=_client(recorded), **kw
    )
    return provider, recorded


def _schema_request(**kw: Any) -> LLMRequest:
    return LLMRequest(
        system="s", prompt="p", task_class=TaskClass.EXTRACTION, max_tokens=64,
        json_schema={"type": "object", "properties": {"a": {"$ref": "#/$defs/A"}},
                     "required": ["a"], "$defs": {"A": {"type": "integer"}}},
        **kw,
    )


# ---------------------------------------------------------------- discovery


def test_discovery_reads_the_model_id_the_per_slot_context_and_the_build(gguf: Path) -> None:
    provider, recorded = _provider(_routes(gguf))
    (spec,) = provider.models()
    assert spec.model == ALIAS
    assert spec.context_window == 16384 and provider.num_ctx == 16384
    assert spec.local and spec.provider == "local"
    assert spec.version == f"llama.cpp {BUILD}"
    assert provider.build == 6358
    assert provider.structured_mode is StructuredMode.JSON_SCHEMA
    assert provider.model_meta["n_params"] == 14768307200
    assert [r.url.path for r in recorded.requests] == ["/v1/models", "/props"]
    assert all(r.method == "GET" for r in recorded.requests), "discovery never generates"


def test_without_a_name_the_single_loaded_model_is_used(gguf: Path) -> None:
    routes = _routes(gguf)
    routes["GET /v1/models"] = [_models(str(gguf))]  # no --alias: id is the -m path
    provider, _ = _provider(routes, model=None)
    assert provider.models()[0].model == str(gguf)


def test_a_name_the_server_does_not_serve_is_refused(gguf: Path) -> None:
    with pytest.raises(ProviderError, match="is not the model llama-server has loaded"):
        _provider(_routes(gguf), model="llama3.1-8b")


def test_more_than_one_model_without_a_name_is_refused(gguf: Path) -> None:
    routes = _routes(gguf)
    two = _models("a")
    two["data"].append(dict(two["data"][0], id="b"))
    routes["GET /v1/models"] = [two]
    with pytest.raises(ProviderError, match="name the model explicitly"):
        _provider(routes, model=None)


def test_props_without_n_ctx_or_model_path_is_refused(gguf: Path) -> None:
    routes = _routes(gguf)
    bad = _props(str(gguf))
    del bad["default_generation_settings"]["n_ctx"]
    routes["GET /props"] = [bad]
    with pytest.raises(ProviderError, match="n_ctx"):
        _provider(routes)
    routes["GET /props"] = [dict(_props(str(gguf)), model_path="")]
    with pytest.raises(ProviderError, match="model_path"):
        _provider(routes)


def test_no_client_and_no_server_are_unavailable_not_guesses(gguf: Path) -> None:
    with pytest.raises(ProviderUnavailable):
        LocalHTTPProvider.for_llama_cpp("http://127.0.0.1:8080", ALIAS, client=None)
    with pytest.raises(ProviderUnavailable):
        _provider({"GET /v1/models": [httpx.ConnectError("refused")]})


# ------------------------------------------------------- structured mode


@pytest.mark.parametrize(
    ("build_info", "expected"),
    [
        ("b6358-c466abe1", StructuredMode.JSON_SCHEMA),
        (f"b{LLAMA_CPP_JSON_SCHEMA_BUILD}-1a24c462", StructuredMode.JSON_SCHEMA),
        (f"b{LLAMA_CPP_JSON_SCHEMA_BUILD - 1}-x", StructuredMode.JSON_OBJECT_SCHEMA),
        ("b3782-8a308354", StructuredMode.JSON_OBJECT_SCHEMA),
        (None, StructuredMode.JSON_OBJECT_SCHEMA),
        ("unknown", StructuredMode.JSON_OBJECT_SCHEMA),
    ],
)
def test_the_wire_form_follows_the_build(build_info: str | None, expected: StructuredMode) -> None:
    assert structured_mode_for_build(parse_llama_cpp_build(build_info)) is expected


def test_a_build_that_cannot_constrain_to_a_schema_is_refused(gguf: Path) -> None:
    with pytest.raises(ProviderError, match="predates schema-constrained output"):
        _provider(_routes(gguf, build_info=f"b{LLAMA_CPP_MIN_SCHEMA_BUILD - 1}-abc"))


def test_json_schema_form_is_sent_with_refs_inlined(gguf: Path) -> None:
    provider, recorded = _provider(_routes(gguf, _completion('{"a": 1}')))
    response = provider.generate(_schema_request(), ALIAS)
    (payload,) = recorded.posts()
    rf = payload["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"]["properties"]["a"] == {"type": "integer"}
    assert "$defs" not in rf["json_schema"]["schema"]
    assert payload["seed"] == 0 and payload["temperature"] == 0.0
    assert payload["stream"] is False and payload["max_tokens"] == 64
    assert payload["model"] == ALIAS
    assert response.json_payload() == {"a": 1}
    assert response.prompt_tokens == 44 and response.completion_tokens == 9
    assert response.model_version == f"llama.cpp {BUILD}"
    assert response.raw["fiboki_structured_mode"] == "json_schema"


def test_an_old_build_gets_the_json_object_schema_form(gguf: Path) -> None:
    provider, recorded = _provider(_routes(gguf, _completion('{"a": 1}'), build_info="b4000-x"))
    provider.generate(_schema_request(), ALIAS)
    (payload,) = recorded.posts()
    assert payload["response_format"]["type"] == "json_object"
    assert payload["response_format"]["schema"]["required"] == ["a"]


def test_json_only_without_a_schema_sends_plain_json_object(gguf: Path) -> None:
    provider, recorded = _provider(_routes(gguf, _completion("{}")))
    provider.generate(
        LLMRequest(system="s", prompt="p", task_class=TaskClass.EXTRACTION, json_only=True), ALIAS
    )
    assert recorded.posts()[0]["response_format"] == {"type": "json_object"}


def test_a_rejected_json_schema_form_falls_back_once_and_stays_fallen_back(gguf: Path) -> None:
    rejected = (400, {"error": {"code": 400, "type": "invalid_request_error", "message":
                                'response_format type must be one of "text" or "json_object", '
                                "but got: json_schema"}})
    provider, recorded = _provider(
        _routes(gguf, [rejected, _completion('{"a": 1}'), _completion('{"a": 2}')])
    )
    assert provider.generate(_schema_request(), ALIAS).json_payload() == {"a": 1}
    first, second = recorded.posts()
    assert first["response_format"]["type"] == "json_schema"
    assert second["response_format"]["type"] == "json_object"
    assert provider.structured_mode is StructuredMode.JSON_OBJECT_SCHEMA
    assert provider.fell_back and "response_format" in provider.fell_back
    provider.generate(_schema_request(), ALIAS)
    assert len(recorded.posts()) == 3, "sticky: the third call is one POST in the fallback form"


def test_a_failed_generation_is_one_post_and_no_retry(gguf: Path) -> None:
    provider, recorded = _provider(
        _routes(gguf, (500, {"error": {"code": 500, "message": "slot crashed"}}))
    )
    with pytest.raises(ProviderError, match="slot crashed"):
        provider.generate(_schema_request(), ALIAS)
    assert len(recorded.posts()) == 1


def test_a_prompt_that_exceeds_the_slot_context_is_refused_before_sending(gguf: Path) -> None:
    provider, recorded = _provider(_routes(gguf, _completion("{}"), n_ctx=256))
    with pytest.raises(ProviderError, match="slot context is 256"):
        provider.generate(
            LLMRequest(system="s", prompt="x" * 2000, task_class=TaskClass.SUMMARISATION,
                       max_tokens=64), ALIAS,
        )
    assert recorded.posts() == []


def test_a_model_swapped_under_the_pin_is_refused_before_generating(gguf: Path, tmp_path: Path
                                                                    ) -> None:
    routes = _routes(gguf, _completion("{}"))
    routes["GET /props"] = [_props(str(gguf)), _props(str(tmp_path / "other.gguf"))]
    provider, recorded = _provider(routes)
    with pytest.raises(ProviderError, match="refusing to attribute"):
        provider.generate(_schema_request(), ALIAS)
    assert recorded.posts() == []


# ------------------------------------------------------------- pinning


def test_the_digest_is_the_sha256_of_the_gguf_bytes(gguf: Path) -> None:
    provider, _ = _provider(_routes(gguf))
    fp = provider.model_fingerprint(ALIAS)
    assert fp.digest == "sha256:" + hashlib.sha256(gguf.read_bytes()).hexdigest()
    assert fp.model_id == ALIAS
    assert fp.details["backend"] == "llama.cpp"
    assert fp.details["n_ctx"] == 16384 and fp.details["build_info"] == BUILD
    assert fp.details["structured_mode"] == "json_schema"
    assert "computed" in fp.source
    assert provider.model_fingerprint(ALIAS) is fp, "cached per provider instance"


def test_the_digest_cache_is_keyed_on_path_size_and_mtime(gguf: Path, tmp_path: Path) -> None:
    cache = tmp_path / "home" / "gguf-digests.json"
    first, info = gguf_weights_digest(gguf, cache_path=cache)
    assert info["cache"] == "computed" and cache.exists()
    assert gguf_weights_digest(gguf, cache_path=cache)[1]["cache"] == "memory"
    providers_module._GGUF_DIGESTS.clear()  # a new process
    again, info = gguf_weights_digest(gguf, cache_path=cache)
    assert (again, info["cache"]) == (first, "file")
    gguf.write_bytes(gguf.read_bytes() + b"changed")
    changed, info = gguf_weights_digest(gguf, cache_path=cache)
    assert info["cache"] == "computed" and changed != first


def test_a_corrupt_cache_file_is_ignored_not_trusted(gguf: Path, tmp_path: Path) -> None:
    cache = tmp_path / "c.json"
    stat = gguf.resolve().stat()
    key = json.dumps([[str(gguf.resolve()), stat.st_size, stat.st_mtime_ns]], separators=(",", ":"))
    cache.write_text(json.dumps({key: "sha256:not-a-digest"}))
    digest, info = gguf_weights_digest(gguf, cache_path=cache)
    assert info["cache"] == "computed"
    assert digest == "sha256:" + hashlib.sha256(gguf.read_bytes()).hexdigest()


def test_a_split_model_is_hashed_across_every_shard_in_order(tmp_path: Path) -> None:
    parts = [tmp_path / f"m-0000{i}-of-00003.gguf" for i in (1, 2, 3)]
    for i, part in enumerate(parts):
        part.write_bytes(bytes([i]) * 1000)
    assert gguf_shard_paths(parts[0]) == tuple(parts)
    digest, info = gguf_weights_digest(parts[0])
    assert digest == "sha256:" + hashlib.sha256(b"".join(p.read_bytes() for p in parts)).hexdigest()
    assert len(info["shards"]) == 3
    parts[2].unlink()
    with pytest.raises(ProviderError, match="not readable"):
        gguf_weights_digest(parts[0])


def test_a_relative_or_missing_model_path_is_refused_not_pinned(gguf: Path) -> None:
    routes = _routes(gguf)
    routes["GET /props"] = [_props("../models/Qwen3-14B-Q5_K_M.gguf")]
    provider, _ = _provider(routes)
    with pytest.raises(ProviderError, match="relative model_path"):
        provider.model_fingerprint(ALIAS)
    pinned, _ = _provider(routes, gguf_path=gguf)
    assert pinned.model_fingerprint(ALIAS).digest.startswith("sha256:")
    wrong = gguf.with_name("Other.gguf")
    wrong.write_bytes(b"x")
    mismatched, _ = _provider(routes, gguf_path=wrong)
    with pytest.raises(ProviderError, match="does not match"):
        mismatched.model_fingerprint(ALIAS)
    routes["GET /props"] = [_props(str(gguf.with_name("gone.gguf")))]
    missing, _ = _provider(routes)
    with pytest.raises(ProviderError, match="not readable"):
        missing.model_fingerprint(ALIAS)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod 000 file")
def test_an_unreadable_weights_file_is_refused(gguf: Path) -> None:
    provider, _ = _provider(_routes(gguf))
    gguf.chmod(0)
    try:
        with pytest.raises(ProviderError, match="not readable"):
            provider.model_fingerprint(ALIAS)
    finally:
        gguf.chmod(0o644)


# ------------------------------------------------ smoke test and session


def test_smoke_test_passes_on_a_pinned_llama_cpp_model(gguf: Path) -> None:
    provider, recorded = _provider(_routes(gguf, _completion('{"status": "ok", "sum": 5}')))
    report = smoke_test_provider(provider)
    assert report.ok, report.errors
    assert report.model_digest == "sha256:" + hashlib.sha256(gguf.read_bytes()).hexdigest()
    assert report.arithmetic_ok
    assert recorded.posts()[0]["response_format"]["json_schema"]["schema"]["required"] == [
        "status", "sum"]


def test_a_session_stamps_the_gguf_digest_and_an_unpinnable_model_never_generates(
    gguf: Path,
) -> None:
    provider, _ = _provider(_routes(gguf, _completion('{"question": "q"}')))
    ledger = AuditLedger()
    session = open_session(
        agent_id="dir", role=AgentRole.RESEARCH_DIRECTOR,
        context=ToolContext(research=ResearchStore(), strategies=StrategyRegistry()),
        resolver=CapabilityResolver(), ledger=ledger, router=ModelRouter([provider]),
    )
    session.think("frame it", reason="test", step="research_brief")
    (record,) = ledger.records()
    assert record.provider == "local" and record.model_id == ALIAS
    assert record.model_digest == "sha256:" + hashlib.sha256(gguf.read_bytes()).hexdigest()

    routes = _routes(gguf, _completion("{}"))
    routes["GET /props"] = [_props("relative.gguf")]
    unpinned, recorded = _provider(routes)
    ledger2 = AuditLedger()
    session2 = open_session(
        agent_id="dir", role=AgentRole.RESEARCH_DIRECTOR,
        context=ToolContext(research=ResearchStore(), strategies=StrategyRegistry()),
        resolver=CapabilityResolver(), ledger=ledger2, router=ModelRouter([unpinned]),
    )
    with pytest.raises(ProviderError):
        session2.think("frame it", reason="test")
    assert recorded.posts() == []


# --------------------------------------------------------- server detection


def test_for_local_server_asks_the_server_which_it_is(gguf: Path) -> None:
    llama = Recorded(_routes(gguf))
    provider = LocalHTTPProvider.for_local_server(
        ALIAS, client=_client(llama), base_url="http://127.0.0.1:8080"
    )
    assert isinstance(provider, LlamaCppProvider)

    ollama = Recorded({"GET /api/tags": [{"models": []}]})  # /props -> 404
    provider = LocalHTTPProvider.for_local_server(
        "qwen2.5:7b-instruct", client=_client(ollama), base_url="http://127.0.0.1:11434"
    )
    assert type(provider) is LocalHTTPProvider and provider.path == "/api/chat"

    down = Recorded({"GET /props": [httpx.ConnectError("refused")]})
    with pytest.raises(ProviderUnavailable):
        LocalHTTPProvider.for_local_server(ALIAS, client=_client(down))


# ------------------------------------------------- the research composition root


def test_the_research_runtime_accepts_a_llama_cpp_provider(gguf: Path, tmp_path: Path) -> None:
    """The runtime composes with an injected llama.cpp provider unchanged.

    What is NOT covered here, deliberately: ``_build_provider`` (the env path)
    still calls ``for_ollama``; switching it to ``for_local_server`` is a
    one-line change in a file outside this change's scope (see the report).
    """
    from fiboki.workers.research_runtime import ResearchRuntimeSettings, compose_research_runtime

    provider, _ = _provider(_routes(gguf))
    settings = ResearchRuntimeSettings(state_dir=tmp_path / "var", provider="local",
                                       local_url="http://127.0.0.1:8080", local_model=ALIAS)
    runtime = compose_research_runtime(settings, provider=provider)
    assert runtime.provider is provider
    decision = runtime.router.select(TaskClass.CRITIQUE, estimated_prompt_tokens=1000)
    assert decision.model == ALIAS and decision.capabilities.local
