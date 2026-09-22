"""LLM provider adapters behind one interface, designed local-first.

Four adapters share one :class:`LLMProvider` contract:

* :class:`LocalHTTPProvider`   -- Ollama / llama.cpp style HTTP server.
* :class:`OpenAICompatibleProvider` -- anything speaking ``/v1/chat/completions``.
* :class:`AnthropicProvider`   -- the Messages API.
* :class:`EchoProvider`        -- deterministic, offline, for tests.

Local-first is a real preference, not a slogan: :class:`ModelRouter` sorts
candidates by (cost, non-local) so a local model that satisfies the task class
wins whenever one is registered.

**No network in tests.**  The three remote adapters take an injected HTTP
client.  Constructed without one they are inert: they hold their model
declarations and raise :class:`ProviderUnavailable` if asked to generate.
Nothing in this module opens a socket at import, and nothing constructs a
default client for you.

Model capability declarations are explicit rather than inferred: context
window, tool-use support, JSON-mode support and per-token cost.  A router that
guesses these is a router that silently sends a 200k-token prompt to an 8k
model.
"""
from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class TaskClass(str, Enum):
    """What kind of thinking a step needs.  Drives model selection."""

    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARISATION = "summarisation"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    STRATEGY_DRAFTING = "strategy_drafting"
    EXPERIMENT_DESIGN = "experiment_design"
    CRITIQUE = "critique"
    FAILURE_INVESTIGATION = "failure_investigation"
    LONG_CONTEXT_ANALYSIS = "long_context_analysis"
    FILING = "filing"


#: Task classes that need structured tool/JSON output from the model.
STRUCTURED_TASKS: frozenset[TaskClass] = frozenset(
    {
        TaskClass.EXTRACTION,
        TaskClass.HYPOTHESIS_GENERATION,
        TaskClass.STRATEGY_DRAFTING,
        TaskClass.EXPERIMENT_DESIGN,
        TaskClass.CRITIQUE,
        TaskClass.FILING,
    }
)


class ProviderError(RuntimeError):
    """Any provider-level failure."""


class ProviderUnavailable(ProviderError):
    """The provider has no transport configured, or the endpoint is unreachable."""


class NoSuitableModel(ProviderError):
    """No registered model satisfies the task's requirements and budget."""


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Everything the router needs to know, declared rather than guessed."""

    model: str
    provider: str
    context_window: int
    max_output_tokens: int = 4096
    supports_tools: bool = False
    supports_json_mode: bool = False
    input_cost_per_1k_usd: float = 0.0
    output_cost_per_1k_usd: float = 0.0
    local: bool = False
    version: str = "unknown"
    task_classes: frozenset[TaskClass] = field(default_factory=lambda: frozenset(TaskClass))

    def estimated_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(
            prompt_tokens / 1000.0 * self.input_cost_per_1k_usd
            + completion_tokens / 1000.0 * self.output_cost_per_1k_usd,
            10,
        )

    def supports(self, task: TaskClass) -> bool:
        return task in self.task_classes


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One completion request.  Temperature defaults to 0 for reproducibility."""

    system: str
    prompt: str
    task_class: TaskClass
    max_tokens: int = 1024
    temperature: float = 0.0
    json_only: bool = False
    stop: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        blob = json.dumps(
            {
                "system": self.system,
                "prompt": self.prompt,
                "task_class": self.task_class.value,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "json_only": self.json_only,
                "stop": list(self.stop),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """What came back, plus the accounting the audit ledger requires."""

    text: str
    model: str
    model_version: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    raw: Mapping[str, Any] = field(default_factory=dict)

    def json_payload(self) -> Any:
        """Parse the response as JSON.  Parses; never evaluates."""
        return json.loads(self.text)


@runtime_checkable
class HTTPClient(Protocol):
    """The slice of ``httpx.Client`` these adapters use.  Injected, never built."""

    def post(self, url: str, *, json: Any, headers: Mapping[str, str], timeout: float) -> Any: ...


class LLMProvider(ABC):
    """One interface over local and remote models."""

    name: str = "provider"

    @abstractmethod
    def models(self) -> tuple[ModelCapabilities, ...]:
        """Declared capabilities of every model this provider can serve."""

    @abstractmethod
    def generate(self, request: LLMRequest, model: str) -> LLMResponse:
        """Run one completion."""

    # -- shared helpers ---------------------------------------------------

    def capabilities_for(self, model: str) -> ModelCapabilities:
        for spec in self.models():
            if spec.model == model:
                return spec
        raise NoSuitableModel(f"{self.name} does not serve model {model!r}")

    def _require_client(self) -> HTTPClient:
        client = getattr(self, "client", None)
        if client is None:
            raise ProviderUnavailable(
                f"{self.name} has no HTTP client configured. Adapters take an "
                "injected client so that constructing one in a test cannot "
                "accidentally reach the network."
            )
        return client


# ---------------------------------------------------------------------------
# Deterministic, offline provider
# ---------------------------------------------------------------------------


class EchoProvider(LLMProvider):
    """A deterministic test double with no I/O of any kind.

    Two modes, both reproducible:

    * ``script`` maps a routing key to a canned response.  The key is taken
      from ``request.metadata['step']`` if present, otherwise the task class
      value.  This is how a whole multi-agent workflow is exercised offline.
    * With no matching script entry, it echoes a stable digest of the request,
      which is useful for asserting that a step was reached but will fail JSON
      parsing -- deliberately, so a missing script entry is loud.
    """

    name = "echo"

    def __init__(
        self,
        script: Mapping[str, str] | None = None,
        *,
        model: str = "echo-1",
        version: str = "echo-1.0.0",
        context_window: int = 32_768,
    ) -> None:
        self.script: dict[str, str] = dict(script or {})
        self._model = model
        self._version = version
        self._context_window = context_window
        self.calls: list[LLMRequest] = []

    def models(self) -> tuple[ModelCapabilities, ...]:
        return (
            ModelCapabilities(
                model=self._model,
                provider=self.name,
                context_window=self._context_window,
                max_output_tokens=4096,
                supports_tools=True,
                supports_json_mode=True,
                input_cost_per_1k_usd=0.0,
                output_cost_per_1k_usd=0.0,
                local=True,
                version=self._version,
            ),
        )

    def route_key(self, request: LLMRequest) -> str:
        step = request.metadata.get("step")
        return str(step) if step else request.task_class.value

    def generate(self, request: LLMRequest, model: str | None = None) -> LLMResponse:
        self.calls.append(request)
        spec = self.capabilities_for(model or self._model)
        key = self.route_key(request)
        text = self.script.get(key)
        if text is None:
            text = f"ECHO[{key}] {request.digest()[:32]}"
        prompt_tokens = _approx_tokens(request.system) + _approx_tokens(request.prompt)
        completion_tokens = _approx_tokens(text)
        return LLMResponse(
            text=text,
            model=spec.model,
            model_version=spec.version,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=spec.estimated_cost(prompt_tokens, completion_tokens),
            latency_ms=0.0,
            finish_reason="stop",
            raw={"scripted": key in self.script, "route_key": key},
        )

    def script_step(self, step: str, payload: Any) -> None:
        """Register a canned JSON response for a workflow step."""
        self.script[step] = payload if isinstance(payload, str) else json.dumps(payload)


def _approx_tokens(text: str) -> int:
    """Deterministic token estimate: 4 characters per token, rounded up."""
    return (len(text) + 3) // 4


# ---------------------------------------------------------------------------
# Real adapters (interface + wire format; transport injected)
# ---------------------------------------------------------------------------


class LocalHTTPProvider(LLMProvider):
    """Ollama / llama.cpp style local server.

    Preferred by the router because its declared cost is zero and it keeps
    research prompts -- which contain the firm's actual strategy ideas -- on
    the machine that generated them.
    """

    name = "local"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        models: Sequence[ModelCapabilities] = (),
        client: HTTPClient | None = None,
        timeout: float = 120.0,
        path: str = "/api/chat",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.timeout = timeout
        self.path = path
        self._models = tuple(models) or (
            ModelCapabilities(
                model="llama3.1:8b-instruct",
                provider=self.name,
                context_window=131_072,
                max_output_tokens=4096,
                supports_tools=True,
                supports_json_mode=True,
                local=True,
                version="unpinned-local",
                task_classes=frozenset(TaskClass),
            ),
        )

    def models(self) -> tuple[ModelCapabilities, ...]:
        return self._models

    def generate(self, request: LLMRequest, model: str) -> LLMResponse:
        spec = self.capabilities_for(model)
        client = self._require_client()
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        }
        if request.json_only and spec.supports_json_mode:
            payload["format"] = "json"
        if request.stop:
            payload["options"]["stop"] = list(request.stop)
        started = time.perf_counter()
        body = _post_json(client, f"{self.base_url}{self.path}", payload, {}, self.timeout)
        text = str(((body.get("message") or {}).get("content")) or body.get("response") or "")
        prompt_tokens = int(body.get("prompt_eval_count") or _approx_tokens(request.prompt))
        completion_tokens = int(body.get("eval_count") or _approx_tokens(text))
        return LLMResponse(
            text=text,
            model=spec.model,
            model_version=str(body.get("model") or spec.version),
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=spec.estimated_cost(prompt_tokens, completion_tokens),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            finish_reason=str(body.get("done_reason") or "stop"),
            raw=body,
        )


class OpenAICompatibleProvider(LLMProvider):
    """Anything exposing ``POST /v1/chat/completions``."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com",
        api_key: str = "",
        models: Sequence[ModelCapabilities] = (),
        client: HTTPClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client
        self.timeout = timeout
        self._models = tuple(models)

    def models(self) -> tuple[ModelCapabilities, ...]:
        return self._models

    def generate(self, request: LLMRequest, model: str) -> LLMResponse:
        spec = self.capabilities_for(model)
        client = self._require_client()
        payload: dict[str, Any] = {
            "model": model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        }
        if request.json_only and spec.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if request.stop:
            payload["stop"] = list(request.stop)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        started = time.perf_counter()
        body = _post_json(
            client, f"{self.base_url}/v1/chat/completions", payload, headers, self.timeout
        )
        choices = body.get("choices") or []
        text = str((choices[0].get("message") or {}).get("content", "")) if choices else ""
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or _approx_tokens(request.prompt))
        completion_tokens = int(usage.get("completion_tokens") or _approx_tokens(text))
        return LLMResponse(
            text=text,
            model=spec.model,
            model_version=str(body.get("model") or spec.version),
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=spec.estimated_cost(prompt_tokens, completion_tokens),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            finish_reason=str(choices[0].get("finish_reason", "stop")) if choices else "stop",
            raw=body,
        )


class AnthropicProvider(LLMProvider):
    """The Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
        api_key: str = "",
        api_version: str = "2023-06-01",
        models: Sequence[ModelCapabilities] = (),
        client: HTTPClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version
        self.client = client
        self.timeout = timeout
        self._models = tuple(models)

    def models(self) -> tuple[ModelCapabilities, ...]:
        return self._models

    def generate(self, request: LLMRequest, model: str) -> LLMResponse:
        spec = self.capabilities_for(model)
        client = self._require_client()
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.stop:
            payload["stop_sequences"] = list(request.stop)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
        }
        started = time.perf_counter()
        body = _post_json(client, f"{self.base_url}/v1/messages", payload, headers, self.timeout)
        blocks = body.get("content") or []
        text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("input_tokens") or _approx_tokens(request.prompt))
        completion_tokens = int(usage.get("output_tokens") or _approx_tokens(text))
        return LLMResponse(
            text=text,
            model=spec.model,
            model_version=str(body.get("model") or spec.version),
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=spec.estimated_cost(prompt_tokens, completion_tokens),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            finish_reason=str(body.get("stop_reason") or "stop"),
            raw=body,
        )


def _post_json(
    client: HTTPClient,
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: float,
) -> dict[str, Any]:
    try:
        response = client.post(url, json=dict(payload), headers=dict(headers), timeout=timeout)
    except Exception as exc:
        raise ProviderUnavailable(f"{url}: {exc}") from exc
    status = getattr(response, "status_code", 200)
    if status >= 400:
        raise ProviderError(f"{url}: HTTP {status}: {getattr(response, 'text', '')[:400]}")
    body = response.json()
    if not isinstance(body, Mapping):
        raise ProviderError(f"{url}: expected a JSON object, got {type(body).__name__}")
    return dict(body)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    provider: LLMProvider
    capabilities: ModelCapabilities
    estimated_cost_usd: float
    reason: str

    @property
    def model(self) -> str:
        return self.capabilities.model


class ModelRouter:
    """Pick a model by task class and budget.  Local-first, then cheapest.

    Selection is deterministic: candidates are filtered on hard requirements
    (task class, context window, tool support, budget) and sorted by
    ``(estimated cost, not local, model name)``.  A tie is broken by name, so
    two runs of the same research cycle route identically.
    """

    def __init__(self, providers: Sequence[LLMProvider] = ()) -> None:
        self._providers: list[LLMProvider] = list(providers)

    def register(self, provider: LLMProvider) -> None:
        self._providers.append(provider)

    def providers(self) -> tuple[LLMProvider, ...]:
        return tuple(self._providers)

    def candidates(self) -> tuple[tuple[LLMProvider, ModelCapabilities], ...]:
        out: list[tuple[LLMProvider, ModelCapabilities]] = []
        for provider in self._providers:
            for spec in provider.models():
                out.append((provider, spec))
        return tuple(out)

    def select(
        self,
        task_class: TaskClass,
        *,
        budget_usd: float = 1.0,
        estimated_prompt_tokens: int = 2_000,
        estimated_completion_tokens: int = 800,
        needs_tools: bool = False,
        needs_json: bool = False,
        prefer_local: bool = True,
    ) -> RoutingDecision:
        if not self._providers:
            raise NoSuitableModel("no providers registered")
        required_context = estimated_prompt_tokens + estimated_completion_tokens
        scored: list[tuple[tuple[float, int, str], LLMProvider, ModelCapabilities, float]] = []
        rejected: list[str] = []
        for provider, spec in self.candidates():
            if not spec.supports(task_class):
                rejected.append(f"{spec.model}: does not serve {task_class.value}")
                continue
            if spec.context_window < required_context:
                rejected.append(
                    f"{spec.model}: context {spec.context_window} < required {required_context}"
                )
                continue
            if needs_tools and not spec.supports_tools:
                rejected.append(f"{spec.model}: no tool support")
                continue
            if needs_json and not spec.supports_json_mode:
                rejected.append(f"{spec.model}: no JSON mode")
                continue
            cost = spec.estimated_cost(estimated_prompt_tokens, estimated_completion_tokens)
            if cost > budget_usd:
                rejected.append(f"{spec.model}: ${cost:.4f} exceeds budget ${budget_usd:.4f}")
                continue
            local_rank = 0 if (spec.local and prefer_local) else 1
            scored.append(((cost, local_rank, spec.model), provider, spec, cost))
        if not scored:
            raise NoSuitableModel(
                f"no model satisfies {task_class.value} within ${budget_usd:.4f}: "
                + "; ".join(rejected[:8])
            )
        scored.sort(key=lambda row: row[0])
        _key, provider, spec, cost = scored[0]
        return RoutingDecision(
            provider=provider,
            capabilities=spec,
            estimated_cost_usd=cost,
            reason=(
                f"{spec.model} ({'local' if spec.local else 'remote'}) at an estimated "
                f"${cost:.6f} for {task_class.value}; {len(scored)} candidates considered"
            ),
        )

    def generate(
        self,
        request: LLMRequest,
        *,
        budget_usd: float = 1.0,
        needs_json: bool = False,
    ) -> tuple[LLMResponse, RoutingDecision]:
        decision = self.select(
            request.task_class,
            budget_usd=budget_usd,
            estimated_prompt_tokens=_approx_tokens(request.system) + _approx_tokens(request.prompt),
            estimated_completion_tokens=request.max_tokens,
            needs_json=needs_json or request.json_only,
        )
        return decision.provider.generate(request, decision.model), decision


def echo_router(script: Mapping[str, str] | None = None) -> ModelRouter:
    """A router with exactly one offline, deterministic provider."""
    return ModelRouter([EchoProvider(script)])


__all__ = [
    "STRUCTURED_TASKS",
    "AnthropicProvider",
    "EchoProvider",
    "HTTPClient",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LocalHTTPProvider",
    "ModelCapabilities",
    "ModelRouter",
    "NoSuitableModel",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderUnavailable",
    "RoutingDecision",
    "TaskClass",
    "echo_router",
]
