"""Provider adapters: local-first routing, declared capabilities, no network."""
from __future__ import annotations

import json
from typing import Any

import pytest

from fiboki.agents.providers import (
    AnthropicProvider,
    EchoProvider,
    LLMRequest,
    LocalHTTPProvider,
    ModelCapabilities,
    ModelRouter,
    NoSuitableModel,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderUnavailable,
    TaskClass,
    echo_router,
)


def _req(task: TaskClass = TaskClass.CRITIQUE, **overrides: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "system": "you are a critic",
        "prompt": "attack this",
        "task_class": task,
    }
    base.update(overrides)
    return LLMRequest(**base)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _RecordingClient:
    """A stand-in transport. Records calls; opens no socket."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json: Any, headers: Any, timeout: float) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(headers)})
        return _FakeResponse(self.payload, self.status_code)


# --------------------------------------------------------- no network


@pytest.mark.parametrize(
    "provider",
    [
        LocalHTTPProvider(),
        OpenAICompatibleProvider(
            models=(ModelCapabilities(model="m", provider="openai_compatible",
                                      context_window=128_000),)
        ),
        AnthropicProvider(
            models=(ModelCapabilities(model="m", provider="anthropic",
                                      context_window=200_000),)
        ),
    ],
)
def test_a_provider_without_a_client_is_inert(provider: Any) -> None:
    """Constructing an adapter must never be able to reach the network."""
    model = provider.models()[0].model
    with pytest.raises(ProviderUnavailable, match="no HTTP client"):
        provider.generate(_req(TaskClass.SUMMARISATION), model)


def test_echo_provider_is_deterministic() -> None:
    provider = EchoProvider()
    a = provider.generate(_req())
    b = provider.generate(_req())
    assert a.text == b.text
    assert a.cost_usd == 0.0
    assert a.model_version == "echo-1.0.0"


def test_echo_provider_serves_a_script_by_step() -> None:
    provider = EchoProvider({"critique": json.dumps({"verdict": "reject"})})
    response = provider.generate(_req(metadata={"step": "critique"}))
    assert response.json_payload() == {"verdict": "reject"}
    assert response.raw["scripted"] is True


def test_a_missing_script_entry_fails_loudly_rather_than_quietly() -> None:
    """An unscripted step must not silently produce plausible-looking JSON."""
    response = EchoProvider({"other": "{}"}).generate(_req(metadata={"step": "critique"}))
    assert response.raw["scripted"] is False
    with pytest.raises(ValueError):
        response.json_payload()


def test_request_digest_is_stable_and_sensitive() -> None:
    assert _req().digest() == _req().digest()
    assert _req().digest() != _req(prompt="something else").digest()


# ----------------------------------------------------------- wire format


def test_local_provider_sends_an_ollama_shaped_payload() -> None:
    client = _RecordingClient(
        {"message": {"content": "hello"}, "prompt_eval_count": 11, "eval_count": 3,
         "model": "llama3.1:8b-instruct", "done_reason": "stop"}
    )
    provider = LocalHTTPProvider(client=client)
    response = provider.generate(_req(TaskClass.SUMMARISATION, json_only=True), "llama3.1:8b-instruct")
    sent = client.calls[0]
    assert sent["url"].endswith("/api/chat")
    assert sent["json"]["stream"] is False
    assert sent["json"]["format"] == "json"
    assert [m["role"] for m in sent["json"]["messages"]] == ["system", "user"]
    assert response.text == "hello"
    assert response.prompt_tokens == 11
    assert response.completion_tokens == 3


def test_openai_provider_sends_chat_completions() -> None:
    client = _RecordingClient(
        {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            "model": "gpt-x-2026",
        }
    )
    spec = ModelCapabilities(
        model="gpt-x", provider="openai_compatible", context_window=128_000,
        supports_json_mode=True, input_cost_per_1k_usd=0.001,
        output_cost_per_1k_usd=0.003,
    )
    provider = OpenAICompatibleProvider(api_key="k", models=(spec,), client=client)
    response = provider.generate(_req(TaskClass.CRITIQUE, json_only=True), "gpt-x")
    sent = client.calls[0]
    assert sent["url"].endswith("/v1/chat/completions")
    assert sent["headers"]["Authorization"] == "Bearer k"
    assert sent["json"]["response_format"] == {"type": "json_object"}
    assert response.model_version == "gpt-x-2026"
    assert response.cost_usd == pytest.approx(20 / 1000 * 0.001 + 5 / 1000 * 0.003)


def test_anthropic_provider_sends_messages() -> None:
    client = _RecordingClient(
        {
            "content": [{"type": "text", "text": "an answer"}],
            "usage": {"input_tokens": 30, "output_tokens": 8},
            "model": "claude-x-2026",
            "stop_reason": "end_turn",
        }
    )
    spec = ModelCapabilities(model="claude-x", provider="anthropic", context_window=200_000)
    provider = AnthropicProvider(api_key="k", models=(spec,), client=client)
    response = provider.generate(_req(TaskClass.CRITIQUE), "claude-x")
    sent = client.calls[0]
    assert sent["url"].endswith("/v1/messages")
    assert sent["headers"]["x-api-key"] == "k"
    assert sent["json"]["system"] == "you are a critic"
    assert response.text == "an answer"
    assert response.finish_reason == "end_turn"


def test_an_http_error_is_surfaced_not_swallowed() -> None:
    client = _RecordingClient({"error": "nope"}, status_code=500)
    spec = ModelCapabilities(model="m", provider="anthropic", context_window=200_000)
    provider = AnthropicProvider(models=(spec,), client=client)
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.generate(_req(), "m")


# -------------------------------------------------------------- routing


def _router() -> ModelRouter:
    local = LocalHTTPProvider(
        models=(
            ModelCapabilities(
                model="local-8b", provider="local", context_window=32_000,
                supports_json_mode=True, supports_tools=True, local=True,
                task_classes=frozenset({TaskClass.CRITIQUE, TaskClass.SUMMARISATION}),
            ),
        )
    )
    remote = OpenAICompatibleProvider(
        models=(
            ModelCapabilities(
                model="big-remote", provider="openai_compatible",
                context_window=400_000, supports_json_mode=True, supports_tools=True,
                input_cost_per_1k_usd=0.01, output_cost_per_1k_usd=0.03,
                task_classes=frozenset(TaskClass),
            ),
        )
    )
    return ModelRouter([local, remote])


def test_router_prefers_the_free_local_model() -> None:
    decision = _router().select(TaskClass.CRITIQUE, budget_usd=1.0,
                                estimated_prompt_tokens=1000, estimated_completion_tokens=500)
    assert decision.model == "local-8b"
    assert decision.estimated_cost_usd == 0.0
    assert "local" in decision.reason


def test_router_falls_back_when_the_context_is_too_large() -> None:
    decision = _router().select(TaskClass.CRITIQUE, budget_usd=10.0,
                                estimated_prompt_tokens=100_000,
                                estimated_completion_tokens=4_000)
    assert decision.model == "big-remote"


def test_router_falls_back_when_the_local_model_does_not_serve_the_task() -> None:
    decision = _router().select(TaskClass.STRATEGY_DRAFTING, budget_usd=10.0)
    assert decision.model == "big-remote"


def test_router_refuses_when_nothing_fits_the_budget() -> None:
    with pytest.raises(NoSuitableModel, match="exceeds budget"):
        _router().select(
            TaskClass.STRATEGY_DRAFTING, budget_usd=0.0001,
            estimated_prompt_tokens=50_000, estimated_completion_tokens=2_000,
        )


def test_router_refuses_with_no_providers() -> None:
    with pytest.raises(NoSuitableModel, match="no providers"):
        ModelRouter().select(TaskClass.CRITIQUE)


def test_routing_is_deterministic() -> None:
    router = _router()
    a = router.select(TaskClass.CRITIQUE)
    b = router.select(TaskClass.CRITIQUE)
    assert a.model == b.model


def test_echo_router_generates_offline() -> None:
    router = echo_router({"summarisation": json.dumps({"x": 1})})
    response, decision = router.generate(_req(TaskClass.SUMMARISATION, json_only=True))
    assert response.json_payload() == {"x": 1}
    assert decision.capabilities.local is True


def test_capability_declarations_price_a_call() -> None:
    spec = ModelCapabilities(
        model="m", provider="p", context_window=1000,
        input_cost_per_1k_usd=2.0, output_cost_per_1k_usd=6.0,
    )
    assert spec.estimated_cost(1000, 500) == pytest.approx(2.0 + 3.0)
