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

Running against a real local model
----------------------------------
:func:`ollama_http_client` builds a real ``httpx.Client`` (no retries, no proxy
environment, explicit timeouts) and :meth:`LocalHTTPProvider.for_ollama`
declares one named Ollama model with a fixed context size.  Both are called
explicitly by the operator; nothing here builds a client on its own.
:meth:`LLMProvider.model_fingerprint` returns the model id and weights digest
that :class:`~fiboki.agents.session.AgentSession` stamps on every audit record,
and :func:`smoke_test_provider` is the one-call check that a configured
provider can be pinned and can return parseable JSON.
"""
from __future__ import annotations

import hashlib
import json
import re
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
    #: Optional JSON schema the output must satisfy.  Sent to providers that
    #: support schema-constrained decoding (Ollama ``format``); others fall
    #: back to plain JSON mode.  Implies ``json_only`` at the wire.
    json_schema: Mapping[str, Any] | None = None

    def digest(self) -> str:
        body: dict[str, Any] = {
            "system": self.system,
            "prompt": self.prompt,
            "task_class": self.task_class.value,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "json_only": self.json_only,
            "stop": list(self.stop),
        }
        # Only present when set, so the digest of every request that predates
        # the field is unchanged.
        if self.json_schema is not None:
            body["json_schema"] = self.json_schema
        blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
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


@dataclass(frozen=True, slots=True)
class ModelFingerprint:
    """Which weights answered.  Stamped on every audit record a session writes.

    ``digest`` is ``None`` when the provider cannot pin its weights (a hosted
    API).  That is recorded as ``None``, never as a made-up value, and the
    offline eval harness reports it as not evaluable rather than as pinned.
    """

    model_id: str
    digest: str | None
    source: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "digest": self.digest}


@runtime_checkable
class HTTPClient(Protocol):
    """The slice of ``httpx.Client`` these adapters use.  Injected, never built.

    ``get`` is optional: :meth:`LocalHTTPProvider.model_fingerprint` uses it
    when present to read Ollama's manifest digest from ``/api/tags``.
    """

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

    def model_fingerprint(self, model: str) -> ModelFingerprint:
        """The model id and weights digest, or ``digest=None`` if unknowable.

        Hosted providers cannot prove which weights served a request, so the
        default says so instead of inventing a digest.
        """
        spec = self.capabilities_for(model)
        return ModelFingerprint(
            model_id=spec.model,
            digest=None,
            source=f"unavailable: {self.name} does not expose a weights digest",
        )

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

    def model_fingerprint(self, model: str | None = None) -> ModelFingerprint:
        """A synthetic but stable digest, so offline runs exercise the pinning path."""
        spec = self.capabilities_for(model or self._model)
        digest = hashlib.sha256(f"echo:{spec.model}:{spec.version}".encode()).hexdigest()
        return ModelFingerprint(
            model_id=spec.model, digest=f"sha256:{digest}", source="synthetic:echo"
        )


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

    Production behaviour, each deliberate:

    * **No retries on generation.**  One POST per :meth:`generate`.  A failed
      generation is a failed step, recorded as such by the session; retrying
      until something parses would hide exactly the failures the ledger exists
      to show.  :func:`ollama_http_client` also builds its transport with
      ``retries=0``.
    * **Timeouts are explicit** (``timeout`` seconds per request).
    * **Structured output.**  ``json_schema`` on the request is sent as
      Ollama's ``format`` (with local ``$ref``\\ s inlined, so the server's
      grammar builder never has to resolve them); ``json_only`` alone sends
      ``format: "json"``.
    * **No silent prompt truncation.**  When ``num_ctx`` is set it is sent as
      ``options.num_ctx`` and a request whose estimated size exceeds it is
      refused before it is sent: Ollama otherwise drops the start of an
      over-long prompt, which is where the system prompt lives.
    * **Weights are pinned.**  :meth:`model_fingerprint` reads the manifest
      digest from ``/api/tags`` (falling back to the weights blob named in
      ``/api/show``'s modelfile) and refuses if neither yields a digest.  It is
      cached per model for the life of the provider instance.
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
        num_ctx: int | None = None,
        seed: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.timeout = timeout
        self.path = path
        self.num_ctx = num_ctx
        self.seed = seed
        self._fingerprints: dict[str, ModelFingerprint] = {}
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

    @classmethod
    def for_ollama(
        cls,
        model: str,
        *,
        client: HTTPClient | None = None,
        base_url: str = "http://127.0.0.1:11434",
        num_ctx: int = 8192,
        max_output_tokens: int = 2048,
        timeout: float = 300.0,
        seed: int | None = 0,
    ) -> LocalHTTPProvider:
        """Declare exactly one named Ollama model with a fixed context size.

        ``num_ctx`` is both what the server is asked to allocate and the
        context window the router is told, so a prompt the router accepts is a
        prompt the server will not truncate.  A fixed value also stops Ollama
        reloading the model when consecutive requests differ in size.
        """
        spec = ModelCapabilities(
            model=model,
            provider=cls.name,
            context_window=num_ctx,
            max_output_tokens=max_output_tokens,
            supports_tools=False,
            supports_json_mode=True,
            local=True,
            version="ollama",
            task_classes=frozenset(TaskClass),
        )
        return cls(
            base_url=base_url,
            models=(spec,),
            client=client,
            timeout=timeout,
            num_ctx=num_ctx,
            seed=seed,
        )

    def models(self) -> tuple[ModelCapabilities, ...]:
        return self._models

    def model_fingerprint(self, model: str) -> ModelFingerprint:
        """``{model_id, digest}`` for ``model``, from the server, cached.

        Order of evidence: the manifest digest ``/api/tags`` lists for this
        exact name (what ``ollama list`` shows; it changes when any layer --
        weights, template, parameters -- changes); a ``digest`` key on
        ``/api/show`` if the server provides one; the ``sha256-...`` weights
        blob named on the modelfile's ``FROM`` line.  None of those is a
        guess, and if none is available this raises rather than recording an
        unpinned run as pinned.
        """
        cached = self._fingerprints.get(model)
        if cached is not None:
            return cached
        spec = self.capabilities_for(model)
        client = self._require_client()
        show = _post_json(
            client, f"{self.base_url}/api/show", {"model": model}, {}, self.timeout
        )
        details = dict(show.get("details") or {})
        digest: str | None = None
        source = ""
        tags = _get_json(client, f"{self.base_url}/api/tags", self.timeout)
        wanted = {model} if ":" in model else {model, f"{model}:latest"}
        if tags is not None:
            for entry in tags.get("models") or ():
                if not isinstance(entry, Mapping):
                    continue
                names = {str(entry.get("name", "")), str(entry.get("model", ""))}
                if wanted & names and entry.get("digest"):
                    digest = _normalise_digest(str(entry["digest"]))
                    source = "ollama:/api/tags manifest digest"
                    break
        if digest is None and show.get("digest"):
            digest = _normalise_digest(str(show["digest"]))
            source = "ollama:/api/show digest"
        if digest is None:
            blob = _MODELFILE_BLOB.search(str(show.get("modelfile") or ""))
            if blob is not None:
                digest = f"sha256:{blob.group(1).lower()}"
                source = "ollama:/api/show modelfile weights blob"
        if digest is None:
            raise ProviderError(
                f"{self.base_url}: could not obtain a weights digest for {model!r} from "
                "/api/tags or /api/show; refusing to run an unpinned local model"
            )
        fingerprint = ModelFingerprint(
            model_id=spec.model,
            digest=digest,
            source=source,
            details={
                k: details[k]
                for k in ("family", "parameter_size", "quantization_level", "format")
                if k in details
            },
        )
        self._fingerprints[model] = fingerprint
        return fingerprint

    def generate(self, request: LLMRequest, model: str) -> LLMResponse:
        spec = self.capabilities_for(model)
        client = self._require_client()
        options: dict[str, Any] = {
            "temperature": request.temperature,
            "num_predict": request.max_tokens,
        }
        if self.num_ctx is not None:
            needed = (
                _approx_tokens(request.system) + _approx_tokens(request.prompt) + request.max_tokens
            )
            if needed > self.num_ctx:
                raise ProviderError(
                    f"{model}: request needs about {needed} tokens but num_ctx is "
                    f"{self.num_ctx}; refusing rather than letting the server truncate "
                    "the prompt"
                )
            options["num_ctx"] = self.num_ctx
        if self.seed is not None:
            options["seed"] = self.seed
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "options": options,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        }
        if spec.supports_json_mode:
            if request.json_schema is not None:
                payload["format"] = inline_json_schema_refs(request.json_schema)
            elif request.json_only:
                payload["format"] = "json"
        if request.stop:
            payload["options"]["stop"] = list(request.stop)
        started = time.perf_counter()
        # Exactly one POST.  No retry: see the class docstring.
        body = _post_json(client, f"{self.base_url}{self.path}", payload, {}, self.timeout)
        if body.get("error"):
            raise ProviderError(f"{self.base_url}{self.path}: {str(body['error'])[:400]}")
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
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError(f"{url}: response body is not JSON: {exc}") from exc
    if not isinstance(body, Mapping):
        raise ProviderError(f"{url}: expected a JSON object, got {type(body).__name__}")
    return dict(body)


def _get_json(client: HTTPClient, url: str, timeout: float) -> dict[str, Any] | None:
    """GET a JSON object if the client can GET at all; ``None`` otherwise.

    Used only for the fingerprint's preferred source.  A client without
    ``get`` (a minimal test double) or a failing GET falls back to the next
    source rather than failing the fingerprint outright.
    """
    get = getattr(client, "get", None)
    if not callable(get):
        return None
    try:
        response = get(url, headers={}, timeout=timeout)
        if getattr(response, "status_code", 200) >= 400:
            return None
        body = response.json()
    except Exception:
        return None
    return dict(body) if isinstance(body, Mapping) else None


#: The weights blob on a modelfile ``FROM`` line: ``.../blobs/sha256-<hex>``.
_MODELFILE_BLOB = re.compile(r"^FROM\s+\S*sha256[-:]([0-9a-fA-F]{64})\s*$", re.MULTILINE)


def _normalise_digest(value: str) -> str:
    value = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return f"sha256:{value}"
    return value.replace("sha256-", "sha256:", 1)


def inline_json_schema_refs(schema: Mapping[str, Any], *, max_depth: int = 32) -> dict[str, Any]:
    """Return ``schema`` with local ``#/$defs/...`` references substituted.

    Pydantic emits ``$ref`` for nested models and enums.  Inlining them means
    a server's schema-to-grammar step never has to resolve references, which
    is the least portable part of JSON Schema.  A recursive schema cannot be
    inlined and raises :class:`ValueError` rather than looping.
    """
    defs = dict(schema.get("$defs") or schema.get("definitions") or {})

    def resolve(node: Any, depth: int) -> Any:
        if depth > max_depth:
            raise ValueError("schema is recursive or too deep to inline")
        if isinstance(node, Mapping):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(("#/$defs/", "#/definitions/")):
                target = defs.get(ref.rsplit("/", 1)[-1])
                if target is None:
                    raise ValueError(f"unresolvable schema reference {ref!r}")
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return {**resolve(target, depth + 1), **resolve(merged, depth + 1)}
            return {
                k: resolve(v, depth + 1)
                for k, v in node.items()
                if k not in ("$defs", "definitions")
            }
        if isinstance(node, list):
            return [resolve(v, depth + 1) for v in node]
        return node

    resolved = resolve(schema, 0)
    assert isinstance(resolved, dict)
    return resolved


def ollama_http_client(*, timeout: float = 300.0, connect_timeout: float = 5.0) -> Any:
    """A real ``httpx.Client`` for a local Ollama server.  Called explicitly.

    * ``retries=0`` on the transport: a failed generation is a failed step.
    * ``trust_env=False``: proxy variables in the environment must not route a
      loopback call through a corporate or sandbox proxy.
    * A short connect timeout (a stopped server fails fast) and a long read
      timeout (a large model on CPU is slow, not broken).

    Imported lazily so that importing this module never imports a network
    library, let alone opens a socket.
    """
    import httpx

    return httpx.Client(
        transport=httpx.HTTPTransport(retries=0),
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        trust_env=False,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SmokeReport:
    """What one smoke test of one provider and model found.  Nothing inferred."""

    provider: str
    model: str
    ok: bool
    model_id: str = ""
    model_digest: str | None = None
    fingerprint_source: str = ""
    json_parsed: bool = False
    arithmetic_ok: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    finish_reason: str = ""
    response_text: str = ""
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "ok": self.ok,
            "model_id": self.model_id,
            "model_digest": self.model_digest,
            "fingerprint_source": self.fingerprint_source,
            "json_parsed": self.json_parsed,
            "arithmetic_ok": self.arithmetic_ok,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "finish_reason": self.finish_reason,
            "response_text": self.response_text,
            "errors": list(self.errors),
        }


_SMOKE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "sum": {"type": "integer"},
    },
    "required": ["status", "sum"],
    "additionalProperties": False,
}


def smoke_test_provider(provider: LLMProvider, model: str | None = None) -> SmokeReport:
    """Fingerprint the model, then ask for one small schema-constrained answer.

    ``ok`` requires a weights digest when the provider is local, a response,
    and JSON that parses to an object with ``status == "ok"``.  Whether the
    model also added 2 and 3 correctly is reported separately as
    ``arithmetic_ok``: a wrong sum is a statement about the model, not about
    the plumbing.  One generation, no retry.
    """
    specs = provider.models()
    if model is None:
        if not specs:
            return SmokeReport(provider.name, "", False, errors=("provider declares no models",))
        model = specs[0].model
    errors: list[str] = []
    fingerprint: ModelFingerprint | None = None
    try:
        fingerprint = provider.model_fingerprint(model)
    except Exception as exc:
        errors.append(f"fingerprint: {type(exc).__name__}: {exc}")
    local = any(s.model == model and s.local for s in specs)
    if fingerprint is not None and fingerprint.digest is None and local:
        errors.append("fingerprint: a local model returned no weights digest")

    request = LLMRequest(
        system="You are a connectivity check. Reply with one JSON object and nothing else.",
        prompt=(
            'Return a JSON object with exactly two keys: "status", whose value is the '
            'string "ok", and "sum", whose value is the integer result of 2 + 3.'
        ),
        task_class=TaskClass.EXTRACTION,
        max_tokens=64,
        temperature=0.0,
        json_only=True,
        json_schema=_SMOKE_SCHEMA,
        metadata={"step": "smoke_test"},
    )
    response: LLMResponse | None = None
    try:
        response = provider.generate(request, model)
    except Exception as exc:
        errors.append(f"generate: {type(exc).__name__}: {exc}")

    parsed: Any = None
    json_parsed = False
    if response is not None:
        try:
            parsed = response.json_payload()
            json_parsed = isinstance(parsed, Mapping)
            if not json_parsed:
                errors.append(f"response is JSON but not an object: {type(parsed).__name__}")
        except ValueError as exc:
            errors.append(f"response is not valid JSON: {exc}")
    status_ok = json_parsed and parsed.get("status") == "ok"
    if json_parsed and not status_ok:
        errors.append(f"unexpected status {parsed.get('status')!r}")
    arithmetic_ok = bool(
        json_parsed and isinstance(parsed.get("sum"), int) and parsed.get("sum") == 5
    )
    return SmokeReport(
        provider=provider.name,
        model=model,
        ok=not errors and status_ok,
        model_id=fingerprint.model_id if fingerprint else "",
        model_digest=fingerprint.digest if fingerprint else None,
        fingerprint_source=fingerprint.source if fingerprint else "",
        json_parsed=json_parsed,
        arithmetic_ok=arithmetic_ok,
        prompt_tokens=response.prompt_tokens if response else 0,
        completion_tokens=response.completion_tokens if response else 0,
        cost_usd=response.cost_usd if response else 0.0,
        latency_ms=response.latency_ms if response else 0.0,
        finish_reason=response.finish_reason if response else "",
        response_text=(response.text[:500] if response else ""),
        errors=tuple(errors),
    )


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
    "ModelFingerprint",
    "ModelRouter",
    "NoSuitableModel",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderUnavailable",
    "RoutingDecision",
    "SmokeReport",
    "TaskClass",
    "echo_router",
    "inline_json_schema_refs",
    "ollama_http_client",
    "smoke_test_provider",
]
