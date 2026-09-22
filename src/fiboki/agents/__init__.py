"""Fiboki V2 autonomous research agents.

THE CARDINAL RULE
=================
**An LLM is never the final authority on an executable order.**

AI may research, hypothesise, analyse, propose, generate strategy DSL
documents, design experiments, inspect results, suggest mutations, classify
qualitative information and investigate failures.  Deterministic systems own
signal calculation, market-data integrity, order construction, position sizing,
portfolio constraints, execution permission, broker routing, kill switches,
live-capital limits and the validation gates.

This is enforced structurally, in five independent places, so that breaking it
requires deleting code rather than forgetting a rule:

1.  ``capabilities.Capability`` contains no execution capability, and
    ``assert_no_execution_capability`` runs at import.  A capability that reads
    as a mutating verb applied to an execution noun -- ``PLACE_ORDER``,
    ``SET_RISK_LIMIT``, ``WRITE_MARKET_DATA``, ``DISABLE_KILL_SWITCH`` -- is an
    ImportError for the whole package.  An agent cannot be granted a permission
    that does not exist.
2.  ``tools.WriteDomain`` enumerates the only destinations a tool may write to.
    Every member is a research artefact or the job queue.
    ``ToolRegistry.register`` re-runs the capability guard and refuses any tool
    whose mutation flag disagrees with its write domain.
3.  ``session.AgentSession`` is the only path to a tool handler.  It checks the
    role bundle, then the deny-by-default resolver, then the input schema, then
    the budget -- and writes one audit record on every path, including refusals.
4.  ``orchestrator.JobType`` contains no execution job.  The engine-running
    tools submit a queued payload; a deterministic handler registered by the
    platform executes it.  The agent's influence ends at the payload.
5.  ``sandbox`` parses agent text with ``json.loads`` and validates it against
    a schema that forbids unknown fields.  ``assert_no_dynamic_execution``
    walks this package's ASTs and proves there is no ``eval``, ``exec``,
    ``compile``, ``__import__``, ``pickle`` or ``subprocess`` call anywhere in
    it.

The sixth defence is not a mechanism but a habit: ``audit`` records every
action -- agent, role, tool, full inputs and outputs, reason, prompt, parent
action, model, version, tokens, cost, wall time and outcome -- in an
append-only hash chain.  Nothing an agent does is invisible.

Module map::

    capabilities    fine-grained permissions, role bundles, deny-by-default
    roles           the twelve specialists, their limits and their prompts
    tools           the tool registry: 12 reads, 6 research writes, 5 queued
                    jobs, 2 external interfaces
    session         the single enforcement point for every tool call
    sandbox         the boundary between agent text and anything executable
    (artefacts)     an agent's writes land in fiboki.research.artefacts,
                    which is the PLATFORM ledger's database -- not a second store
    orchestrator    stateless named queues, idempotent keys, retries, events
    jobs            the deterministic worker that actually runs the engine
    providers       local-first LLM adapters plus an offline EchoProvider
    audit           the append-only ledger
    workflows       multi-agent cycles composed from all of the above
"""
from __future__ import annotations

from fiboki.agents.audit import (
    ActionKind,
    AuditLedger,
    AuditRecord,
    JsonlAuditLedger,
    Outcome,
)
from fiboki.agents.capabilities import (
    Capability,
    CapabilityResolver,
    ExecutionCapabilityError,
    Grant,
    PermissionDenied,
    assert_no_execution_capability,
)
from fiboki.agents.orchestrator import (
    JobSpec,
    JobStatus,
    JobType,
    ManualClock,
    Orchestrator,
    submitter_for,
)
from fiboki.agents.providers import (
    EchoProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ModelCapabilities,
    ModelRouter,
    TaskClass,
    echo_router,
)
from fiboki.agents.roles import ROLES, AgentRole, RoleSpec, get_role
from fiboki.agents.sandbox import (
    SandboxRejection,
    assert_no_dynamic_execution,
    validate_strategy_payload,
)
from fiboki.agents.session import AgentSession, open_session
from fiboki.agents.tools import (
    REGISTRY,
    InMemoryBarSource,
    ToolContext,
    ToolRegistry,
    WriteDomain,
)
from fiboki.agents.workflows import (
    WorkflowDeps,
    WorkflowResult,
    offline_research_script,
    run_research_cycle,
)
from fiboki.research.artefacts import ResearchStore

__all__ = [
    "REGISTRY",
    "ROLES",
    "ActionKind",
    "AgentRole",
    "AgentSession",
    "AuditLedger",
    "AuditRecord",
    "Capability",
    "CapabilityResolver",
    "EchoProvider",
    "ExecutionCapabilityError",
    "Grant",
    "InMemoryBarSource",
    "JobSpec",
    "JobStatus",
    "JobType",
    "JsonlAuditLedger",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "ManualClock",
    "ModelCapabilities",
    "ModelRouter",
    "Orchestrator",
    "Outcome",
    "PermissionDenied",
    "ResearchStore",
    "RoleSpec",
    "SandboxRejection",
    "TaskClass",
    "ToolContext",
    "ToolRegistry",
    "WorkflowDeps",
    "WorkflowResult",
    "WriteDomain",
    "assert_no_dynamic_execution",
    "assert_no_execution_capability",
    "echo_router",
    "get_role",
    "offline_research_script",
    "open_session",
    "run_research_cycle",
    "submitter_for",
    "validate_strategy_payload",
]
