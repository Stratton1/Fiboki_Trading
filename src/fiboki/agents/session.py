"""The one place a tool call can happen, and therefore the one place to check.

An agent does not hold tool functions.  It holds an :class:`AgentSession`, and
every call goes through :meth:`AgentSession.call`, which in order:

1.  checks the tool is in the ROLE's bundle -- a tool outside the bundle is
    refused before its capability is even consulted, because "my role does not
    do that" is a different and earlier failure from "I lack that permission";
2.  checks the capability against the deny-by-default resolver;
3.  validates the inputs against the tool's pydantic schema;
4.  enforces the tool's call budget;
5.  runs the handler;
6.  validates the OUTPUT against the tool's schema;
7.  appends exactly one audit record -- whether it succeeded, was denied, or
    raised.

There is no bypass.  A handler is never handed out, and nothing else in the
package calls a handler directly.

Model calls are recorded too, through :meth:`AgentSession.think`, so the
ledger shows the prompt that produced the tool call that produced the artefact.

Provenance on every record: a session stamps ``manifest_hash`` (the run
manifest it was opened under, if any) and ``model_id`` / ``model_digest`` on
every record it writes.  For a model call these name the model asked and its
weights digest; for a tool call they name the model of this session's most
recent thought, whose output the call acted on, or
:data:`~fiboki.agents.audit.NO_MODEL` if the session has not thought yet.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ValidationError

from fiboki.agents.audit import (
    NO_MODEL,
    ActionKind,
    AuditedAction,
    AuditLedger,
    AuditRecord,
    Outcome,
)
from fiboki.agents.capabilities import Capability, CapabilityResolver, PermissionDenied
from fiboki.agents.providers import (
    LLMRequest,
    LLMResponse,
    ModelRouter,
    RoutingDecision,
    TaskClass,
)
from fiboki.agents.roles import AgentRole, RoleSpec, get_role
from fiboki.agents.tools import REGISTRY, ToolContext, ToolRegistry, ToolSpec


class ToolNotInRole(PermissionDenied):
    """The tool exists, but this role does not do that."""


class BudgetExceeded(RuntimeError):
    """The session has spent its allowance for this tool or this run."""


@dataclass(slots=True)
class SessionBudget:
    """Per-session spend controls.  Deliberately blunt and easy to audit."""

    max_tool_calls: int = 60
    max_model_calls: int = 30
    max_cost_usd: float = 1.0
    tool_calls: int = 0
    model_calls: int = 0
    cost_usd: float = 0.0
    per_tool: dict[str, int] = field(default_factory=dict)

    def charge_tool(self, tool: ToolSpec) -> None:
        used = self.per_tool.get(tool.name, 0)
        if used >= tool.budget.max_calls_per_job:
            raise BudgetExceeded(
                f"{tool.name}: {used} calls already made, budget is "
                f"{tool.budget.max_calls_per_job} per job"
            )
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded(
                f"session tool-call budget exhausted ({self.max_tool_calls})"
            )
        self.per_tool[tool.name] = used + 1
        self.tool_calls += 1
        self.cost_usd = round(self.cost_usd + tool.budget.estimated_cost_usd, 10)

    def charge_model(self, cost_usd: float) -> None:
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded(
                f"session model-call budget exhausted ({self.max_model_calls})"
            )
        if self.cost_usd + cost_usd > self.max_cost_usd:
            raise BudgetExceeded(
                f"session cost budget exhausted: ${self.cost_usd:.4f} + "
                f"${cost_usd:.4f} > ${self.max_cost_usd:.4f}"
            )
        self.model_calls += 1
        self.cost_usd = round(self.cost_usd + cost_usd, 10)

    def declaration(self) -> dict[str, Any]:
        """The limits, as data, for a workflow's start record."""
        return {
            "max_tool_calls": self.max_tool_calls,
            "max_model_calls": self.max_model_calls,
            "max_cost_usd": self.max_cost_usd,
        }


def default_budget(role_spec: RoleSpec) -> SessionBudget:
    """The budget a session for this role gets when none is passed.

    One definition, used by :class:`AgentSession` and by the workflow start
    record that declares the limits the offline evals check against.
    """
    return SessionBudget(max_tool_calls=role_spec.max_tool_calls)


@dataclass(frozen=True, slots=True)
class ThoughtResult:
    """A model call, its parsed payload if any, and its audit anchor."""

    response: LLMResponse
    routing: RoutingDecision
    action_id: str
    payload: Any = None
    parse_error: str = ""

    @property
    def parsed(self) -> bool:
        return self.parse_error == ""


class AgentSession:
    """One agent instance, for the duration of one piece of work."""

    def __init__(
        self,
        *,
        agent_id: str,
        role: AgentRole | str,
        context: ToolContext,
        resolver: CapabilityResolver,
        ledger: AuditLedger,
        registry: ToolRegistry | None = None,
        router: ModelRouter | None = None,
        workflow_id: str | None = None,
        budget: SessionBudget | None = None,
        manifest_hash: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.role_spec: RoleSpec = get_role(role)
        self.registry = registry or REGISTRY
        self.resolver = resolver
        self.ledger = ledger
        self.router = router
        self.workflow_id = workflow_id
        self.budget = budget or default_budget(self.role_spec)
        self.manifest_hash = manifest_hash
        # The tool context carries the agent's identity so research records are
        # attributed without the handler having to be told twice.
        self.context = replace(context, agent_id=agent_id, role=self.role_spec.role.value)
        self.last_action_id: str | None = None
        #: The model (and its weights digest) of this session's latest thought.
        self.last_model_id: str = NO_MODEL
        self.last_model_digest: str | None = None

    # -- introspection ----------------------------------------------------

    @property
    def role(self) -> AgentRole:
        return self.role_spec.role

    def available_tools(self) -> tuple[str, ...]:
        return self.role_spec.tools

    def system_prompt(self) -> str:
        return self.role_spec.system_prompt(self.registry)

    def capabilities(self) -> frozenset[Capability]:
        return self.resolver.capabilities_for(self.agent_id)

    # -- tool calls -------------------------------------------------------

    def call(
        self,
        tool_name: str,
        inputs: Mapping[str, Any] | BaseModel,
        *,
        reason: str,
        parent_action_id: str | None = None,
        prompt: str = "",
    ) -> BaseModel:
        """Call a tool.  Every path through this method writes one audit record."""
        raw_inputs = (
            inputs.model_dump(mode="json") if isinstance(inputs, BaseModel) else dict(inputs)
        )
        spec = self.registry.get(tool_name) if tool_name in self.registry else None
        with AuditedAction(
            self.ledger,
            agent_id=self.agent_id,
            role=self.role_spec.role.value,
            kind=ActionKind.TOOL_CALL,
            tool=tool_name,
            inputs=raw_inputs,
            reason=reason,
            prompt=prompt,
            parent_action_id=parent_action_id or self.last_action_id,
            workflow_id=self.workflow_id,
            capability=spec.capability.value if spec else "",
            model_id=self.last_model_id,
            model_digest=self.last_model_digest,
            manifest_hash=self.manifest_hash,
        ) as action:
            if spec is None:
                raise ToolNotInRole(
                    self.agent_id,
                    "unknown_tool",
                    f"tool {tool_name!r} does not exist; registered: {self.registry.names()}",
                )
            if tool_name not in self.role_spec.tools:
                raise ToolNotInRole(
                    self.agent_id,
                    spec.capability,
                    (
                        f"the {self.role_spec.title} role does not include {tool_name!r}; "
                        f"its tools are {list(self.role_spec.tools)}"
                    ),
                )
            # Deny-by-default: the grant must exist, independently of the role.
            self.resolver.require(
                self.agent_id,
                spec.capability,
                detail=f"required by tool {tool_name!r}",
            )
            try:
                validated = spec.input_model.model_validate(raw_inputs)
            except ValidationError as exc:
                raise ValueError(f"{tool_name}: invalid inputs: {exc.errors()[:4]}") from exc
            self.budget.charge_tool(spec)

            result = spec.handler(self.context, validated)
            if not isinstance(result, spec.output_model):
                raise TypeError(
                    f"{tool_name} returned {type(result).__name__}, not "
                    f"{spec.output_model.__name__}; a tool that lies about its own "
                    "output schema is worse than one that fails"
                )
            action.outputs = result.model_dump(mode="json")
            action.cost_usd = spec.budget.estimated_cost_usd
            action.outcome = Outcome.OK
        assert action.record is not None
        self.last_action_id = action.record.action_id
        return result

    def try_call(
        self,
        tool_name: str,
        inputs: Mapping[str, Any] | BaseModel,
        *,
        reason: str,
        parent_action_id: str | None = None,
    ) -> tuple[BaseModel | None, str]:
        """Call a tool, returning ``(result, error)`` instead of raising.

        The audit record is written either way; this only changes control flow
        for a workflow that wants to continue after a refusal.
        """
        try:
            return self.call(
                tool_name, inputs, reason=reason, parent_action_id=parent_action_id
            ), ""
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    # -- model calls ------------------------------------------------------

    def think(
        self,
        prompt: str,
        *,
        reason: str,
        step: str = "",
        task_class: TaskClass | None = None,
        max_tokens: int = 1024,
        json_only: bool = True,
        parent_action_id: str | None = None,
        budget_usd: float = 0.25,
        json_schema: Mapping[str, Any] | None = None,
    ) -> ThoughtResult:
        """Ask the model, record the exchange, and parse JSON if asked for.

        Parsing uses ``json.loads`` via the sandbox; model output is never
        evaluated.  A parse failure is returned as data, not raised, so a
        workflow records the bad output rather than hiding it.

        The provider is asked for its model fingerprint inside the audited
        block, so a model whose weights cannot be pinned fails the step on the
        record instead of running unpinned.  ``json_schema`` asks a provider
        that supports it to constrain decoding to that schema; the output is
        still parsed and validated downstream exactly as before.
        """
        if self.router is None:
            raise RuntimeError(
                f"{self.agent_id} has no model router; a session that is expected to "
                "think must be constructed with one"
            )
        request = LLMRequest(
            system=self.system_prompt(),
            prompt=prompt,
            task_class=task_class or self.role_spec.task_class,
            max_tokens=max_tokens,
            temperature=0.0,
            json_only=json_only,
            metadata={"step": step or self.role_spec.role.value, "agent_id": self.agent_id},
            json_schema=json_schema if json_only else None,
        )
        started = time.perf_counter()
        decision = self.router.select(
            request.task_class,
            budget_usd=min(budget_usd, self.role_spec.budget_usd),
            estimated_prompt_tokens=(len(request.system) + len(prompt)) // 4 + 1,
            estimated_completion_tokens=max_tokens,
            needs_json=json_only,
        )
        with AuditedAction(
            self.ledger,
            agent_id=self.agent_id,
            role=self.role_spec.role.value,
            kind=ActionKind.MODEL_CALL,
            tool=f"model:{decision.model}",
            inputs={
                "task_class": request.task_class.value,
                "step": step,
                "max_tokens": max_tokens,
                "json_only": json_only,
                "prompt_digest": request.digest(),
            },
            reason=reason,
            prompt=prompt,
            parent_action_id=parent_action_id or self.last_action_id,
            workflow_id=self.workflow_id,
            model=decision.model,
            model_version=decision.capabilities.version,
            provider=decision.provider.name,
            model_id=decision.model,
            manifest_hash=self.manifest_hash,
        ) as action:
            fingerprint = decision.provider.model_fingerprint(decision.model)
            action.model_id = fingerprint.model_id
            action.model_digest = fingerprint.digest
            response = decision.provider.generate(request, decision.model)
            # Account for the call BEFORE charging the budget: if the charge
            # refuses, the money was still spent and the record must say so.
            action.prompt_tokens = response.prompt_tokens
            action.completion_tokens = response.completion_tokens
            action.cost_usd = response.cost_usd
            self.budget.charge_model(response.cost_usd)
            action.outputs = {
                "text": response.text,
                "finish_reason": response.finish_reason,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        assert action.record is not None
        self.last_action_id = action.record.action_id
        self.last_model_id = action.record.model_id or NO_MODEL
        self.last_model_digest = action.record.model_digest

        payload: Any = None
        parse_error = ""
        if json_only:
            try:
                payload = response.json_payload()
            except ValueError as exc:
                parse_error = f"model output is not valid JSON: {exc}"
        return ThoughtResult(
            response=response,
            routing=decision,
            action_id=action.record.action_id,
            payload=payload,
            parse_error=parse_error,
        )

    # -- reading the trail ------------------------------------------------

    def actions(self) -> tuple[AuditRecord, ...]:
        return tuple(r for r in self.ledger.records() if r.agent_id == self.agent_id)


def open_session(
    *,
    agent_id: str,
    role: AgentRole | str,
    context: ToolContext,
    resolver: CapabilityResolver,
    ledger: AuditLedger,
    router: ModelRouter | None = None,
    workflow_id: str | None = None,
    registry: ToolRegistry | None = None,
    grant: bool = True,
    manifest_hash: str | None = None,
) -> AgentSession:
    """Open a session, granting exactly the role's capabilities and no more.

    ``grant=False`` opens a session with NO grant at all, which is the
    deny-by-default case: every call it makes is refused and recorded.
    """
    spec = get_role(role)
    if grant:
        resolver.grant(agent_id, spec.role.value, spec.capabilities)
    return AgentSession(
        agent_id=agent_id,
        role=spec.role,
        context=context,
        resolver=resolver,
        ledger=ledger,
        registry=registry,
        router=router,
        workflow_id=workflow_id,
        manifest_hash=manifest_hash,
    )


__all__ = [
    "AgentSession",
    "BudgetExceeded",
    "SessionBudget",
    "ThoughtResult",
    "ToolNotInRole",
    "default_budget",
    "open_session",
]
