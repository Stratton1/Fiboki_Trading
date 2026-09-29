"""LLM provider adapters behind one interface, designed local-first.

Four adapters share one :class:`LLMProvider` contract:

* :class:`LocalHTTPProvider`   -- Ollama; its subclass :class:`LlamaCppProvider`
  speaks to llama.cpp's ``llama-server``.
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
declares one named Ollama model with a fixed context size.
:meth:`LocalHTTPProvider.for_llama_cpp` instead DISCOVERS the model a running
``llama-server`` has loaded (``/v1/models``, ``/props``) and pins it by the
SHA-256 of its GGUF file; :meth:`LocalHTTPProvider.for_local_server` asks the
server which of the two it is.  All are called explicitly by the operator (the
same client suits both servers); nothing here builds a client on its own.
:meth:`LLMProvider.model_fingerprint` returns the model id and weights digest
that :class:`~fiboki.agents.session.AgentSession` stamps on every audit record,
and :func:`smoke_test_provider` is the one-call check that a configured
provider can be pinned and can return parseable JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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


_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def _strip_think_block(text: str) -> str:
    """Drop one leading ``<think>...</think>`` block, leaving the answer.

    Only a LEADING block is removed and only when it is closed: an unclosed
    block means the model ran out of tokens while thinking, and that text is
    returned as-is so the caller's JSON parse fails loudly on it.
    """
    return _THINK_RE.sub("", text, count=1)


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

    @classmethod
    def for_llama_cpp(
        cls,
        base_url: str = "http://127.0.0.1:8080",
        model: str | None = None,
        *,
        client: HTTPClient | None = None,
        max_output_tokens: int = 2048,
        timeout: float = 300.0,
        seed: int | None = 0,
        gguf_path: str | Path | None = None,
        digest_cache_path: str | Path | None = None,
    ) -> LlamaCppProvider:
        """Discover the model a running ``llama-server`` has loaded and declare it.

        See :class:`LlamaCppProvider`.  Unlike :meth:`for_ollama` this reads
        the server at construction (``/v1/models`` and ``/props``), because
        the model id, the per-slot context and the weights file are facts of
        the running server, not of the operator's configuration.
        """
        return LlamaCppProvider.discover(
            base_url,
            model,
            client=client,
            max_output_tokens=max_output_tokens,
            timeout=timeout,
            seed=seed,
            gguf_path=gguf_path,
            digest_cache_path=digest_cache_path,
        )

    @classmethod
    def for_local_server(
        cls,
        model: str | None,
        *,
        client: HTTPClient | None = None,
        base_url: str = "http://127.0.0.1:11434",
        **kwargs: Any,
    ) -> LocalHTTPProvider:
        """llama.cpp or Ollama at ``base_url``, decided by asking the server.

        ``GET /props`` answering with ``default_generation_settings`` is
        llama.cpp's ``llama-server`` (Ollama has no ``/props``); anything else
        is treated as Ollama.  An unreachable server raises
        :class:`ProviderUnavailable` rather than guessing which one it would
        have been.  For llama.cpp ``model`` must be the id or an alias the
        server reports in ``/v1/models`` (``None`` accepts the single model it
        has loaded); for Ollama it is mandatory, because an Ollama server
        holds many models and nothing here picks one for the operator.
        """
        if client is None:
            raise ProviderUnavailable(
                f"{base_url}: no HTTP client configured; cannot tell llama.cpp from Ollama"
            )
        props = _probe_llama_cpp_props(client, base_url.rstrip("/"), 10.0)
        if props is not None:
            allowed = {"max_output_tokens", "timeout", "seed", "gguf_path", "digest_cache_path"}
            return cls.for_llama_cpp(
                base_url, model, client=client, **{k: v for k, v in kwargs.items() if k in allowed}
            )
        if not model:
            raise ProviderError(
                f"{base_url} is not llama.cpp (no /props); as Ollama it needs an explicit "
                "model name. There is no default model."
            )
        allowed = {"num_ctx", "max_output_tokens", "timeout", "seed"}
        return cls.for_ollama(
            model, client=client, base_url=base_url,
            **{k: v for k, v in kwargs.items() if k in allowed},
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
            # Reasoning models (Qwen3, DeepSeek-R1 families) emit a hidden
            # "thinking" pass before the answer. Under ``format`` that pass can
            # consume the whole output budget and leave ``content`` empty, so
            # thinking is switched off: every Fiboki role wants a schema-bound
            # answer, and the reasoning belongs in the JSON's own fields.
            # Servers that predate the field ignore it (Ollama < 0.9).
            "think": False,
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
        # A server that ignored ``think: false`` may still prefix the answer
        # with a <think>...</think> block; the JSON is what follows it.
        text = _strip_think_block(text)
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


#: ``llama-server`` builds (the ``bNNNN`` in ``/props`` ``build_info`` and in
#: ``llama-server --version``) at which the structured-output wire format
#: changed.  Each was read from the llama.cpp git history, not inferred:
#:
#: * b2487 (PR #5978, 2024-03-21): ``/v1/chat/completions`` accepts
#:   ``response_format: {"type": "json_object", "schema": {...}}`` and turns
#:   the schema into a GBNF grammar server-side.  Any other ``type`` is an
#:   error.
#: * b3782 (PR #9527, 2024-09-18): ``{"type": "json_schema", "json_schema":
#:   {"schema": {...}}}`` (the OpenAI shape) is also accepted.
#: * b4820 (PR #12168, 2025-03-04): fixes a variable-shadowing bug in that
#:   branch.  Issues #10732 and #11988 report builds in between on which the
#:   ``json_schema`` form was accepted and then IGNORED (unconstrained output,
#:   no error), so this provider only sends it from b4820.
#:
#: The ``json_object`` + ``schema`` form still works on current master
#: (``tools/server/server-common.cpp``), so it is the fallback for older
#: builds and for a build whose number the server does not report.
LLAMA_CPP_MIN_SCHEMA_BUILD = 2487
LLAMA_CPP_JSON_SCHEMA_BUILD = 4820


class StructuredMode(str, Enum):
    """How a JSON schema is put on the wire to ``llama-server``."""

    #: ``response_format: {"type": "json_schema", "json_schema": {...}}``.
    JSON_SCHEMA = "json_schema"
    #: ``response_format: {"type": "json_object", "schema": {...}}``: the
    #: server compiles the schema to GBNF with its own converter.
    JSON_OBJECT_SCHEMA = "json_object_schema"


def parse_llama_cpp_build(build_info: str | None) -> int | None:
    """``b6358-c466abe1`` -> 6358.  ``None`` when the server does not say."""
    if not build_info:
        return None
    match = re.match(r"^\s*b(\d+)", str(build_info))
    return int(match.group(1)) if match else None


def structured_mode_for_build(build: int | None) -> StructuredMode:
    """The schema wire form to use for a given ``llama-server`` build.

    An unknown build gets the ``json_object`` form, which every build since
    b2487 honours, rather than the ``json_schema`` form that some builds
    before b4820 silently ignored.  A known build older than b2487 cannot
    constrain output to a schema at all and raises.
    """
    if build is None:
        return StructuredMode.JSON_OBJECT_SCHEMA
    if build < LLAMA_CPP_MIN_SCHEMA_BUILD:
        raise ProviderError(
            f"llama.cpp build b{build} predates schema-constrained output "
            f"(b{LLAMA_CPP_MIN_SCHEMA_BUILD}); upgrade llama.cpp (brew upgrade llama.cpp)"
        )
    if build < LLAMA_CPP_JSON_SCHEMA_BUILD:
        return StructuredMode.JSON_OBJECT_SCHEMA
    return StructuredMode.JSON_SCHEMA


#: Split GGUF shards: ``<stem>-00001-of-00003.gguf``.
_GGUF_SHARD = re.compile(r"^(?P<stem>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$")

#: In-process digest cache: key (paths, sizes, mtimes) -> ``sha256:<hex>``.
_GGUF_DIGESTS: dict[str, str] = {}


def gguf_shard_paths(path: str | Path) -> tuple[Path, ...]:
    """Every file that makes up the model ``path`` names, in load order."""
    first = Path(path)
    match = _GGUF_SHARD.match(first.name)
    if match is None:
        return (first,)
    count = int(match.group("count"))
    stem = match.group("stem")
    return tuple(first.with_name(f"{stem}-{i:05d}-of-{count:05d}.gguf") for i in range(1, count + 1))


def gguf_weights_digest(
    path: str | Path, *, cache_path: str | Path | None = None, chunk_bytes: int = 8 << 20
) -> tuple[str, dict[str, Any]]:
    """``sha256:<hex>`` of the GGUF weights at ``path``, and how it was obtained.

    The digest is the SHA-256 of the file's bytes, so for a single-file model
    it equals ``shasum -a 256 <file>`` and the ``lfs.sha256`` Hugging Face
    publishes for that file.  A split model (``-00001-of-0000N.gguf``) is
    hashed as the concatenation of its shards in order.

    Hashing tens of gigabytes takes a while, so the result is cached keyed on
    each shard's resolved path, size and ``mtime_ns``: in memory for the life
    of the process and, when ``cache_path`` is given, in a small JSON file.  A
    changed size or modification time is a cache miss.  A file replaced with
    one of identical size AND a forged mtime would be missed; that is the
    stated limit of the cache, and deleting the cache file forces a re-hash.
    """
    shards = gguf_shard_paths(path)
    stats: list[tuple[str, int, int]] = []
    for shard in shards:
        try:
            resolved = shard.expanduser().resolve(strict=True)
            st = resolved.stat()
        except OSError as exc:
            raise ProviderError(f"GGUF weights file {shard} is not readable: {exc}") from exc
        stats.append((str(resolved), int(st.st_size), int(st.st_mtime_ns)))
    key = json.dumps(stats, separators=(",", ":"))
    info: dict[str, Any] = {"shards": [s[0] for s in stats], "bytes": sum(s[1] for s in stats)}
    cached = _GGUF_DIGESTS.get(key)
    if cached is not None:
        return cached, {**info, "cache": "memory"}
    disk: dict[str, str] = {}
    cache_file = Path(cache_path).expanduser() if cache_path is not None else None
    if cache_file is not None and cache_file.exists():
        try:
            loaded = json.loads(cache_file.read_text(encoding="utf-8"))
            disk = {str(k): str(v) for k, v in dict(loaded).items()}
        except (OSError, ValueError, TypeError):
            disk = {}  # a corrupt cache is only a cache: re-hash
    if key in disk and re.fullmatch(r"sha256:[0-9a-f]{64}", disk[key]):
        _GGUF_DIGESTS[key] = disk[key]
        return disk[key], {**info, "cache": "file"}
    hasher = hashlib.sha256()
    for resolved_path, _size, _mtime in stats:
        try:
            with open(resolved_path, "rb") as handle:
                while True:
                    block = handle.read(chunk_bytes)
                    if not block:
                        break
                    hasher.update(block)
        except OSError as exc:
            raise ProviderError(f"GGUF weights file {resolved_path} is not readable: {exc}") from exc
    digest = f"sha256:{hasher.hexdigest()}"
    _GGUF_DIGESTS[key] = digest
    if cache_file is not None:
        disk[key] = digest
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
            tmp.write_text(json.dumps(disk, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, cache_file)
        except OSError:
            pass  # the digest is correct either way; only the cache failed
    return digest, {**info, "cache": "computed"}


def _get_json_strict(client: HTTPClient, url: str, timeout: float) -> dict[str, Any]:
    """GET a JSON object or raise.  Discovery must not fall back to a guess."""
    get = getattr(client, "get", None)
    if not callable(get):
        raise ProviderUnavailable(f"{url}: the HTTP client cannot GET; discovery needs it")
    try:
        response = get(url, headers={}, timeout=timeout)
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


def _probe_llama_cpp_props(client: HTTPClient, base_url: str, timeout: float) -> dict[str, Any] | None:
    """``/props`` if the server is llama.cpp, ``None`` if it answers but is not.

    Raises :class:`ProviderUnavailable` when nothing answers at all.
    """
    get = getattr(client, "get", None)
    if not callable(get):
        raise ProviderUnavailable(f"{base_url}: the HTTP client cannot GET; cannot probe")
    try:
        response = get(f"{base_url}/props", headers={}, timeout=timeout)
    except Exception as exc:
        raise ProviderUnavailable(f"{base_url}/props: {exc}") from exc
    if getattr(response, "status_code", 200) >= 400:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, Mapping) and isinstance(body.get("default_generation_settings"), Mapping):
        return dict(body)
    return None


def _error_message(response: Any) -> str:
    """The ``error.message`` of an OpenAI-shaped error body, else the raw text."""
    try:
        body = response.json()
    except Exception:
        return str(getattr(response, "text", ""))[:400]
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping):
            return str(err.get("message") or err)[:400]
        if err:
            return str(err)[:400]
    return str(getattr(response, "text", ""))[:400]


class LlamaCppProvider(LocalHTTPProvider):
    """``llama-server`` (llama.cpp) through its OpenAI-compatible endpoint.

    Built by :meth:`LocalHTTPProvider.for_llama_cpp`, which discovers:

    * the model id from ``GET /v1/models`` (the ``--alias`` if one was given,
      otherwise the ``-m`` path; ``aliases`` is honoured when present);
    * the per-slot context ``default_generation_settings.n_ctx``, the weights
      path ``model_path`` and ``build_info`` from ``GET /props``.  Shapes as
      documented in ``tools/server/README.md`` of llama.cpp and produced by
      ``get_res_props`` in ``tools/server/server-context.cpp``.

    ``name`` stays ``"local"`` on purpose: the offline evals FAIL a local
    record with no weights digest and only mark other providers not
    evaluable, and a llama.cpp model is exactly as pinnable as an Ollama one.
    The backend is recorded in the fingerprint ``source`` and in
    ``model_version`` (``llama.cpp b<build>``).

    The same production rules as the Ollama path, each deliberate:

    * **Pinned or refused.**  :meth:`model_fingerprint` hashes the GGUF file
      ``/props`` names (all shards of a split model).  A relative path, a path
      on another machine or an unreadable file raises; nothing is recorded as
      pinned that was not hashed.
    * **The model cannot change underneath the pin.**  Every :meth:`generate`
      re-reads ``/props`` (a GET, not a generation) and refuses if the
      weights path or the context differs from what was discovered.
    * **No silent truncation.**  A request whose estimated size exceeds the
      per-slot ``n_ctx`` is refused before it is sent.
    * **One generation per call.**  The only second POST is protocol
      negotiation: if the server rejects the ``json_schema`` response format
      itself (HTTP error naming ``response_format``, so nothing was
      generated), the provider switches to the ``json_object`` + ``schema``
      form for the rest of its life and sends once more.  A generation that
      ran and produced bad JSON is never re-sent.
    * **Schema-constrained output.**  ``request.json_schema`` (local ``$ref``
      inlined) goes out in the wire form :func:`structured_mode_for_build`
      picks; ``json_only`` alone sends ``{"type": "json_object"}``.  There is
      no client-side JSON-Schema-to-GBNF converter: the fallback form makes
      the server compile the schema with its own, upstream-tested converter,
      and a second converter here would be a second place for the constraint
      to be wrong.
    """

    name = "local"

    def __init__(
        self,
        *,
        base_url: str,
        spec: ModelCapabilities,
        client: HTTPClient,
        props: Mapping[str, Any],
        n_ctx: int,
        timeout: float = 300.0,
        seed: int | None = 0,
        gguf_path: str | Path | None = None,
        digest_cache_path: str | Path | None = None,
        structured_mode: StructuredMode = StructuredMode.JSON_SCHEMA,
    ) -> None:
        super().__init__(
            base_url=base_url,
            models=(spec,),
            client=client,
            timeout=timeout,
            path="/v1/chat/completions",
            num_ctx=n_ctx,
            seed=seed,
        )
        self.props = dict(props)
        self.model_path = str(props.get("model_path") or "")
        self.build_info = str(props.get("build_info") or "")
        self.build = parse_llama_cpp_build(self.build_info)
        self.gguf_path = Path(gguf_path).expanduser() if gguf_path is not None else None
        self.digest_cache_path = digest_cache_path
        self.structured_mode = structured_mode
        #: Set when the server rejected the json_schema form and the provider
        #: fell back; recorded so the audit can see which form constrained it.
        self.fell_back: str | None = None
        #: ``meta`` from ``/v1/models`` (n_params, n_ctx_train, size), if given.
        self.model_meta: dict[str, Any] = {}

    @classmethod
    def discover(
        cls,
        base_url: str = "http://127.0.0.1:8080",
        model: str | None = None,
        *,
        client: HTTPClient | None = None,
        max_output_tokens: int = 2048,
        timeout: float = 300.0,
        seed: int | None = 0,
        gguf_path: str | Path | None = None,
        digest_cache_path: str | Path | None = None,
    ) -> LlamaCppProvider:
        base = base_url.rstrip("/")
        if client is None:
            raise ProviderUnavailable(
                f"{base}: no HTTP client configured. Discovery reads the running server; "
                "pass client=ollama_http_client() (it suits llama-server too)."
            )
        listing = _get_json_strict(client, f"{base}/v1/models", timeout)
        entries = [e for e in (listing.get("data") or ()) if isinstance(e, Mapping)]
        if not entries:
            raise ProviderError(f"{base}/v1/models lists no model; is llama-server still loading?")
        chosen: Mapping[str, Any] | None = None
        if model is None:
            if len(entries) != 1:
                raise ProviderError(
                    f"{base}/v1/models lists {len(entries)} models (router mode?); name the "
                    "model explicitly. Fiboki pins one model per server."
                )
            chosen = entries[0]
        else:
            for entry in entries:
                names = {str(entry.get("id", ""))} | {str(a) for a in (entry.get("aliases") or ())}
                if model in names:
                    chosen = entry
                    break
            if chosen is None:
                served = sorted(str(e.get("id", "")) for e in entries)
                raise ProviderError(
                    f"{base}: model {model!r} is not the model llama-server has loaded "
                    f"({served}); refusing to attribute its answers to {model!r}"
                )
        model_id = model if model is not None else str(chosen.get("id", ""))
        props = _get_json_strict(client, f"{base}/props", timeout)
        settings = props.get("default_generation_settings")
        n_ctx = settings.get("n_ctx") if isinstance(settings, Mapping) else None
        if not isinstance(n_ctx, int) or isinstance(n_ctx, bool) or n_ctx <= 0:
            raise ProviderError(
                f"{base}/props has no usable default_generation_settings.n_ctx ({n_ctx!r}); "
                "without the per-slot context the prompt-size refusal cannot work"
            )
        if not props.get("model_path"):
            raise ProviderError(f"{base}/props names no model_path; the weights cannot be pinned")
        build_info = str(props.get("build_info") or "")
        mode = structured_mode_for_build(parse_llama_cpp_build(build_info))
        meta = chosen.get("meta") if isinstance(chosen.get("meta"), Mapping) else {}
        spec = ModelCapabilities(
            model=model_id,
            provider=cls.name,
            context_window=n_ctx,
            max_output_tokens=min(max_output_tokens, n_ctx),
            supports_tools=False,
            supports_json_mode=True,
            local=True,
            version=f"llama.cpp {build_info or 'build-unknown'}",
            task_classes=frozenset(TaskClass),
        )
        provider = cls(
            base_url=base,
            spec=spec,
            client=client,
            props=props,
            n_ctx=n_ctx,
            timeout=timeout,
            seed=seed,
            gguf_path=gguf_path,
            digest_cache_path=digest_cache_path,
            structured_mode=mode,
        )
        provider.model_meta = dict(meta or {})
        return provider

    # -- pinning ----------------------------------------------------------

    def weights_file(self) -> Path:
        """The local GGUF file the server loaded, or raise.  Never a guess."""
        reported = Path(self.model_path).expanduser()
        if self.gguf_path is not None:
            if self.gguf_path.name != reported.name:
                raise ProviderError(
                    f"gguf_path {self.gguf_path} does not match the server's model_path "
                    f"{self.model_path!r}; refusing to pin the wrong file"
                )
            return self.gguf_path
        if not reported.is_absolute():
            raise ProviderError(
                f"llama-server reports a relative model_path {self.model_path!r}; start it "
                "with an absolute -m path (scripts/llama-server.sh does) or pass gguf_path. "
                "Refusing to run an unpinned local model."
            )
        return reported

    def model_fingerprint(self, model: str | None = None) -> ModelFingerprint:
        """SHA-256 of the GGUF weights ``/props`` names, cached by path+size+mtime."""
        spec = self.capabilities_for(model or self._models[0].model)
        cached = self._fingerprints.get(spec.model)
        if cached is not None:
            return cached
        path = self.weights_file()
        digest, how = gguf_weights_digest(path, cache_path=self.digest_cache_path)
        fingerprint = ModelFingerprint(
            model_id=spec.model,
            digest=digest,
            source=f"llama.cpp: sha256 of GGUF bytes at {path} ({how['cache']})",
            details={
                "backend": "llama.cpp",
                "model_path": self.model_path,
                "build_info": self.build_info,
                "n_ctx": self.num_ctx,
                "total_slots": self.props.get("total_slots"),
                "structured_mode": self.structured_mode.value,
                "shards": len(how["shards"]),
                "bytes": how["bytes"],
                **{k: self.model_meta[k] for k in ("n_params", "n_ctx_train") if k in self.model_meta},
            },
        )
        self._fingerprints[spec.model] = fingerprint
        return fingerprint

    def _assert_same_model_loaded(self, client: HTTPClient) -> None:
        props = _get_json_strict(client, f"{self.base_url}/props", self.timeout)
        path = str(props.get("model_path") or "")
        settings = props.get("default_generation_settings")
        n_ctx = settings.get("n_ctx") if isinstance(settings, Mapping) else None
        if path != self.model_path or n_ctx != self.num_ctx:
            raise ProviderError(
                f"{self.base_url}: llama-server now serves {path!r} with n_ctx={n_ctx!r}, "
                f"not the {self.model_path!r} with n_ctx={self.num_ctx} this provider pinned; "
                "refusing to attribute the answer to the pinned weights. Rebuild the provider."
            )

    # -- generation -------------------------------------------------------

    def _response_format(self, request: LLMRequest) -> dict[str, Any] | None:
        if request.json_schema is not None:
            schema = inline_json_schema_refs(request.json_schema)
            if self.structured_mode is StructuredMode.JSON_SCHEMA:
                return {
                    "type": "json_schema",
                    "json_schema": {"name": "fiboki_output", "strict": True, "schema": schema},
                }
            return {"type": "json_object", "schema": schema}
        if request.json_only:
            return {"type": "json_object"}
        return None

    def _post(self, client: HTTPClient, payload: Mapping[str, Any]) -> tuple[int, Any]:
        url = f"{self.base_url}{self.path}"
        try:
            response = client.post(url, json=dict(payload), headers={}, timeout=self.timeout)
        except Exception as exc:
            raise ProviderUnavailable(f"{url}: {exc}") from exc
        return int(getattr(response, "status_code", 200)), response

    def generate(self, request: LLMRequest, model: str) -> LLMResponse:
        spec = self.capabilities_for(model)
        client = self._require_client()
        assert self.num_ctx is not None  # set by discovery
        needed = _approx_tokens(request.system) + _approx_tokens(request.prompt) + request.max_tokens
        if needed > self.num_ctx:
            raise ProviderError(
                f"{model}: request needs about {needed} tokens but the llama-server slot "
                f"context is {self.num_ctx}; refusing rather than letting the server "
                "truncate or reject the prompt"
            )
        self._assert_same_model_loaded(client)
        payload: dict[str, Any] = {
            "model": spec.model,
            "stream": False,
            "temperature": request.temperature,
            "max_tokens": min(request.max_tokens, spec.max_output_tokens),
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if request.stop:
            payload["stop"] = list(request.stop)
        response_format = self._response_format(request)
        if response_format is not None:
            payload["response_format"] = response_format
        url = f"{self.base_url}{self.path}"
        started = time.perf_counter()
        status, response = self._post(client, payload)
        if (
            status >= 400
            and request.json_schema is not None
            and self.structured_mode is StructuredMode.JSON_SCHEMA
            and "response_format" in _error_message(response)
        ):
            # The server refused the wire form before generating anything:
            # protocol negotiation, not a retry of a failed generation.
            self.fell_back = _error_message(response)
            self.structured_mode = StructuredMode.JSON_OBJECT_SCHEMA
            self._fingerprints.clear()  # the fingerprint records the mode
            payload["response_format"] = self._response_format(request)
            status, response = self._post(client, payload)
        if status >= 400:
            raise ProviderError(f"{url}: HTTP {status}: {_error_message(response)}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"{url}: response body is not JSON: {exc}") from exc
        if not isinstance(body, Mapping):
            raise ProviderError(f"{url}: expected a JSON object, got {type(body).__name__}")
        if body.get("error"):
            raise ProviderError(f"{url}: {str(body['error'])[:400]}")
        choices = body.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        text = str(message.get("content") or "")
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or _approx_tokens(request.prompt))
        completion_tokens = int(usage.get("completion_tokens") or _approx_tokens(text))
        raw = dict(body)
        raw["fiboki_structured_mode"] = (
            self.structured_mode.value if response_format is not None else None
        )
        return LLMResponse(
            text=text,
            model=spec.model,
            model_version=spec.version,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=spec.estimated_cost(prompt_tokens, completion_tokens),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            finish_reason=str(choices[0].get("finish_reason") or "stop") if choices else "stop",
            raw=raw,
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
    "LLAMA_CPP_JSON_SCHEMA_BUILD",
    "LLAMA_CPP_MIN_SCHEMA_BUILD",
    "STRUCTURED_TASKS",
    "AnthropicProvider",
    "EchoProvider",
    "HTTPClient",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LlamaCppProvider",
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
    "StructuredMode",
    "TaskClass",
    "echo_router",
    "gguf_shard_paths",
    "gguf_weights_digest",
    "inline_json_schema_refs",
    "ollama_http_client",
    "parse_llama_cpp_build",
    "smoke_test_provider",
    "structured_mode_for_build",
]
