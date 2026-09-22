"""Concrete multi-agent workflows, composed from everything above.

The headline one is :func:`run_research_cycle`:

    Research Director   frames the question
    Quant Researcher    records a falsifiable hypothesis
    Strategy Engineer   proposes a mutation of a seed strategy
    Statistical Auditor pre-registers an experiment, then QUEUES a backtest
                        and a validation run
    (deterministic worker drains the queue -- no model is consulted)
    Adversarial Critic  reads the validation report and tries to disprove it
    Research Librarian  files what was learned, linked to every artefact

Every step is an ordinary :class:`~fiboki.agents.session.AgentSession`, so every
model call and every tool call lands in the audit ledger with its parent action.
Reading the ledger for one workflow id reconstructs the entire cycle: what was
asked, what was answered, what was run and what was concluded.

The whole thing runs offline against :class:`~fiboki.agents.providers.EchoProvider`,
which is why it is testable at all.  A step whose model output does not parse,
or whose tool call is refused, is recorded as a failed step and the workflow
carries on to the next one -- a research cycle that hides its own failures is
the thing this architecture exists to prevent.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from fiboki.agents.audit import AuditLedger
from fiboki.agents.capabilities import CapabilityResolver
from fiboki.agents.orchestrator import JobRecord, JobStatus, Orchestrator
from fiboki.agents.providers import ModelRouter
from fiboki.agents.roles import AgentRole
from fiboki.agents.session import AgentSession, open_session
from fiboki.agents.tools import ToolContext, ToolRegistry


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What one workflow step did, in a form that survives the process."""

    step: str
    role: str
    ok: bool
    action_ids: tuple[str, ...] = ()
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    model: str = ""
    cost_usd: float = 0.0


@dataclass(slots=True)
class WorkflowResult:
    """The trail of one research cycle."""

    workflow_id: str
    steps: list[StepOutcome] = field(default_factory=list)
    hypothesis_id: str | None = None
    strategy_id: str | None = None
    experiment_id: str | None = None
    backtest_id: str | None = None
    validation_report_id: str | None = None
    critique_id: str | None = None
    note_id: str | None = None
    job_records: list[JobRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    @property
    def failed_steps(self) -> tuple[str, ...]:
        return tuple(s.step for s in self.steps if not s.ok)

    def total_cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.steps), 10)

    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.ok)
        return (
            f"{self.workflow_id}: {done}/{len(self.steps)} steps ok, "
            f"${self.total_cost_usd():.6f}"
            + (f", failed: {list(self.failed_steps)}" if self.failed_steps else "")
        )


@dataclass(frozen=True, slots=True)
class WorkflowDeps:
    """Everything a workflow needs, injected.  No globals, no hidden state."""

    context: ToolContext
    resolver: CapabilityResolver
    ledger: AuditLedger
    router: ModelRouter
    orchestrator: Orchestrator
    registry: ToolRegistry | None = None
    queue: str = "research"


def _session(deps: WorkflowDeps, role: AgentRole, workflow_id: str) -> AgentSession:
    return open_session(
        agent_id=f"{role.value}@{workflow_id}",
        role=role,
        context=deps.context,
        resolver=deps.resolver,
        ledger=deps.ledger,
        router=deps.router,
        workflow_id=workflow_id,
        registry=deps.registry,
    )


def _as_mapping(payload: Any, step: str) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    raise ValueError(f"{step}: model returned {type(payload).__name__}, expected an object")


def _out(result: BaseModel) -> dict[str, Any]:
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# The research cycle
# ---------------------------------------------------------------------------


def run_research_cycle(
    deps: WorkflowDeps,
    *,
    seed_strategy_id: str,
    instrument: str,
    timeframe: str,
    question: str = "",
    workflow_id: str | None = None,
    backtest_overrides: Mapping[str, Any] | None = None,
) -> WorkflowResult:
    """Hypothesis -> strategy -> experiment -> queued validation -> critique -> filing.

    ``seed_strategy_id`` must already be registered; the cycle mutates it rather
    than drafting from nothing, because a mutation with a stated prediction is a
    sharper experiment than a fresh document with none.
    """
    wid = workflow_id or f"wf_{uuid.uuid4().hex[:12]}"
    out = WorkflowResult(workflow_id=wid)
    overrides = dict(backtest_overrides or {})

    # -- 1. Research Director: frame the question ------------------------
    director = _session(deps, AgentRole.RESEARCH_DIRECTOR, wid)
    brief: dict[str, Any] = {}
    try:
        thought = director.think(
            _director_prompt(question, seed_strategy_id, instrument, timeframe),
            reason="prioritise the next research question",
            step="research_brief",
        )
        if thought.parse_error:
            raise ValueError(thought.parse_error)
        brief = dict(_as_mapping(thought.payload, "research_brief"))
        out.steps.append(
            StepOutcome(
                "research_brief", director.role.value, True, (thought.action_id,),
                brief, model=thought.response.model, cost_usd=thought.response.cost_usd,
            )
        )
    except Exception as exc:
        out.steps.append(
            StepOutcome("research_brief", AgentRole.RESEARCH_DIRECTOR.value, False,
                        error=f"{type(exc).__name__}: {exc}")
        )

    # -- 2. Quant Researcher: record a falsifiable hypothesis -------------
    researcher = _session(deps, AgentRole.QUANT_RESEARCHER, wid)
    try:
        thought = researcher.think(
            _hypothesis_prompt(brief, instrument, timeframe),
            reason="state a falsifiable hypothesis before any test is run",
            step="hypothesis",
        )
        if thought.parse_error:
            raise ValueError(thought.parse_error)
        payload = dict(_as_mapping(thought.payload, "hypothesis"))
        payload.setdefault("instruments", [instrument.upper()])
        payload.setdefault("timeframes", [timeframe])
        result = researcher.call(
            "create_hypothesis", payload,
            reason="pre-register the hypothesis", parent_action_id=thought.action_id,
        )
        out.hypothesis_id = _out(result)["hypothesis_id"]
        out.steps.append(
            StepOutcome("hypothesis", researcher.role.value, True,
                        (thought.action_id, researcher.last_action_id or ""),
                        _out(result), model=thought.response.model,
                        cost_usd=thought.response.cost_usd)
        )
    except Exception as exc:
        out.steps.append(
            StepOutcome("hypothesis", AgentRole.QUANT_RESEARCHER.value, False,
                        error=f"{type(exc).__name__}: {exc}")
        )

    # -- 3. Strategy Engineer / Mutation Agent: propose a variant ---------
    mutator = _session(deps, AgentRole.STRATEGY_MUTATION_AGENT, wid)
    try:
        thought = mutator.think(
            _mutation_prompt(seed_strategy_id, out.hypothesis_id),
            reason="propose one named mutation with a stated prediction",
            step="mutation",
        )
        if thought.parse_error:
            raise ValueError(thought.parse_error)
        payload = dict(_as_mapping(thought.payload, "mutation"))
        payload.setdefault("parent_strategy_id", seed_strategy_id)
        result = mutator.call(
            "mutate_strategy", payload,
            reason="register the proposed variant", parent_action_id=thought.action_id,
        )
        out.strategy_id = _out(result)["strategy_id"]
        out.steps.append(
            StepOutcome("mutation", mutator.role.value, True,
                        (thought.action_id, mutator.last_action_id or ""),
                        _out(result), model=thought.response.model,
                        cost_usd=thought.response.cost_usd)
        )
    except Exception as exc:
        out.steps.append(
            StepOutcome("mutation", AgentRole.STRATEGY_MUTATION_AGENT.value, False,
                        error=f"{type(exc).__name__}: {exc}")
        )

    auditor = _session(deps, AgentRole.STATISTICAL_AUDITOR, wid)

    # -- 4. Statistical Auditor: pre-register the experiment --------------
    if out.hypothesis_id and out.strategy_id:
        try:
            thought = auditor.think(
                _experiment_prompt(out.hypothesis_id, out.strategy_id, instrument, timeframe),
                reason="pre-register windows, folds and success criteria",
                step="experiment_design",
            )
            if thought.parse_error:
                raise ValueError(thought.parse_error)
            payload = dict(_as_mapping(thought.payload, "experiment_design"))
            payload["hypothesis_id"] = out.hypothesis_id
            payload["strategy_ids"] = [out.strategy_id]
            payload.setdefault("instruments", [instrument.upper()])
            payload.setdefault("timeframes", [timeframe])
            result = auditor.call(
                "design_experiment", payload,
                reason="pre-register the experiment", parent_action_id=thought.action_id,
            )
            out.experiment_id = _out(result)["experiment_id"]
            out.steps.append(
                StepOutcome("experiment_design", auditor.role.value, True,
                            (thought.action_id, auditor.last_action_id or ""),
                            _out(result), model=thought.response.model,
                            cost_usd=thought.response.cost_usd)
            )
        except Exception as exc:
            out.steps.append(
                StepOutcome("experiment_design", AgentRole.STATISTICAL_AUDITOR.value, False,
                            error=f"{type(exc).__name__}: {exc}")
            )
    else:
        out.steps.append(
            StepOutcome("experiment_design", AgentRole.STATISTICAL_AUDITOR.value, False,
                        error="no hypothesis or no strategy to design an experiment for")
        )

    # -- 5. Queue the backtest; a deterministic worker runs it ------------
    if out.strategy_id:
        backtest_inputs: dict[str, Any] = {
            "strategy_id": out.strategy_id,
            "instrument": instrument.upper(),
            "timeframe": timeframe,
            "experiment_id": out.experiment_id,
            "label": f"{wid}:cycle",
            **overrides,
        }
        ticket, error = auditor.try_call(
            "run_backtest", backtest_inputs, reason="queue the backtest for the variant"
        )
        out.steps.append(
            StepOutcome("queue_backtest", auditor.role.value, error == "",
                        (auditor.last_action_id or "",),
                        _out(ticket) if ticket else {}, error=error)
        )
        drained = deps.orchestrator.drain(deps.queue)
        out.job_records.extend(drained)
        for record in drained:
            if record.status is JobStatus.SUCCEEDED and record.result:
                out.backtest_id = out.backtest_id or record.result.get("backtest_id")
        out.steps.append(
            StepOutcome(
                "worker_backtest", "deterministic_worker",
                any(r.status is JobStatus.SUCCEEDED for r in drained),
                output={
                    "jobs": [
                        {"job_id": r.job_id, "status": r.status.value, "error": r.error}
                        for r in drained
                    ]
                },
                error="" if drained else "nothing was queued",
            )
        )

    # -- 6. Queue validation of the recorded backtest ---------------------
    if out.backtest_id:
        ticket, error = auditor.try_call(
            "run_validation",
            {
                "backtest_id": out.backtest_id,
                "experiment_id": out.experiment_id,
                "n_trials_in_search": int(overrides.get("n_trials_in_search", 1)),
            },
            reason="queue the platform's promotion gates",
        )
        out.steps.append(
            StepOutcome("queue_validation", auditor.role.value, error == "",
                        (auditor.last_action_id or "",),
                        _out(ticket) if ticket else {}, error=error)
        )
        drained = deps.orchestrator.drain(deps.queue)
        out.job_records.extend(drained)
        for record in drained:
            if record.status is JobStatus.SUCCEEDED and record.result:
                out.validation_report_id = (
                    out.validation_report_id or record.result.get("report_id")
                )
        out.steps.append(
            StepOutcome(
                "worker_validation", "deterministic_worker",
                any(r.status is JobStatus.SUCCEEDED for r in drained),
                output={
                    "jobs": [
                        {"job_id": r.job_id, "status": r.status.value, "error": r.error}
                        for r in drained
                    ]
                },
                error="" if drained else "nothing was queued",
            )
        )

    # -- 7. Adversarial Quant Critic: try to disprove it ------------------
    critic = _session(deps, AgentRole.ADVERSARIAL_QUANT_CRITIC, wid)
    report_payload: dict[str, Any] = {}
    if out.validation_report_id:
        read, error = critic.try_call(
            "inspect_validation_report", {"report_id": out.validation_report_id},
            reason="read the gates before attacking the result",
        )
        if read is not None:
            report_payload = _out(read)
    try:
        thought = critic.think(
            _critique_prompt(out.strategy_id, report_payload),
            reason="attempt to disprove the candidate",
            step="critique",
        )
        if thought.parse_error:
            raise ValueError(thought.parse_error)
        payload = dict(_as_mapping(thought.payload, "critique"))
        payload.setdefault("target_kind", "validation_report" if out.validation_report_id else "strategy")
        payload.setdefault("target_id", out.validation_report_id or out.strategy_id or "unknown")
        result = critic.call(
            "record_critique", payload,
            reason="file the objections", parent_action_id=thought.action_id,
        )
        out.critique_id = _out(result)["critique_id"]
        out.steps.append(
            StepOutcome("critique", critic.role.value, True,
                        (thought.action_id, critic.last_action_id or ""),
                        _out(result), model=thought.response.model,
                        cost_usd=thought.response.cost_usd)
        )
    except Exception as exc:
        out.steps.append(
            StepOutcome("critique", AgentRole.ADVERSARIAL_QUANT_CRITIC.value, False,
                        error=f"{type(exc).__name__}: {exc}")
        )

    # -- 8. Research Librarian: file it -----------------------------------
    librarian = _session(deps, AgentRole.RESEARCH_LIBRARIAN, wid)
    try:
        thought = librarian.think(
            _filing_prompt(out, report_payload),
            reason="file the cycle into institutional memory",
            step="filing",
        )
        if thought.parse_error:
            raise ValueError(thought.parse_error)
        payload = dict(_as_mapping(thought.payload, "filing"))
        links = dict(payload.get("links") or {})
        for key, value in (
            ("hypothesis_id", out.hypothesis_id),
            ("strategy_id", out.strategy_id),
            ("experiment_id", out.experiment_id),
            ("backtest_id", out.backtest_id),
            ("validation_report_id", out.validation_report_id),
            ("critique_id", out.critique_id),
            ("workflow_id", wid),
        ):
            if value:
                links[key] = value
        payload["links"] = links
        result = librarian.call(
            "file_research_note", payload,
            reason="record what was learned", parent_action_id=thought.action_id,
        )
        out.note_id = _out(result)["note_id"]
        out.steps.append(
            StepOutcome("filing", librarian.role.value, True,
                        (thought.action_id, librarian.last_action_id or ""),
                        _out(result), model=thought.response.model,
                        cost_usd=thought.response.cost_usd)
        )
    except Exception as exc:
        out.steps.append(
            StepOutcome("filing", AgentRole.RESEARCH_LIBRARIAN.value, False,
                        error=f"{type(exc).__name__}: {exc}")
        )
    return out


# ---------------------------------------------------------------------------
# A second, narrower workflow
# ---------------------------------------------------------------------------


def run_failure_investigation(
    deps: WorkflowDeps,
    *,
    backtest_id: str,
    workflow_id: str | None = None,
) -> WorkflowResult:
    """Read the damage, then say what most likely caused it.

    Deliberately read-only: the investigator holds no write capability at all,
    so the worst it can produce is a wrong diagnosis in the ledger.
    """
    wid = workflow_id or f"wf_{uuid.uuid4().hex[:12]}"
    out = WorkflowResult(workflow_id=wid)
    out.backtest_id = backtest_id
    investigator = _session(deps, AgentRole.FAILURE_INVESTIGATOR, wid)

    evidence: dict[str, Any] = {}
    for tool, inputs, label in (
        ("inspect_drawdown", {"backtest_id": backtest_id, "top_n": 3}, "drawdown"),
        ("inspect_trade", {"backtest_id": backtest_id, "worst_n": 5}, "worst_trades"),
    ):
        result, error = investigator.try_call(
            tool, inputs, reason=f"gather {label} evidence"
        )
        out.steps.append(
            StepOutcome(label, investigator.role.value, error == "",
                        (investigator.last_action_id or "",),
                        _out(result) if result else {}, error=error)
        )
        if result is not None:
            evidence[label] = _out(result)

    try:
        thought = investigator.think(
            _investigation_prompt(backtest_id, evidence),
            reason="state the most likely cause with its supporting evidence",
            step="diagnosis",
        )
        if thought.parse_error:
            raise ValueError(thought.parse_error)
        out.steps.append(
            StepOutcome("diagnosis", investigator.role.value, True, (thought.action_id,),
                        dict(_as_mapping(thought.payload, "diagnosis")),
                        model=thought.response.model, cost_usd=thought.response.cost_usd)
        )
    except Exception as exc:
        out.steps.append(
            StepOutcome("diagnosis", AgentRole.FAILURE_INVESTIGATOR.value, False,
                        error=f"{type(exc).__name__}: {exc}")
        )
    return out


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def _director_prompt(question: str, seed: str, instrument: str, timeframe: str) -> str:
    return (
        f"Seed strategy: {seed}. Instrument: {instrument}. Timeframe: {timeframe}.\n"
        f"Operator's question, if any: {question or '(none given)'}\n\n"
        "Return ONE JSON object with keys: question (string), why_now (string), "
        "decisive_answer (string, what a conclusive result would look like), "
        "priority (integer 1-5)."
    )


def _hypothesis_prompt(brief: Mapping[str, Any], instrument: str, timeframe: str) -> str:
    return (
        f"Research brief: {json.dumps(dict(brief), sort_keys=True)}\n"
        f"Instrument: {instrument}. Timeframe: {timeframe}.\n\n"
        "Return ONE JSON object matching the create_hypothesis schema: title, "
        "statement, rationale, testable_prediction, falsifier, instruments, "
        "timeframes, prior_belief, tags. The falsifier must name the observation "
        "that would make you abandon the idea."
    )


def _mutation_prompt(seed: str, hypothesis_id: str | None) -> str:
    return (
        f"Parent strategy: {seed}. Hypothesis: {hypothesis_id or '(none)'}.\n\n"
        "Propose ONE mutation. Return a JSON object matching the mutate_strategy "
        "schema: parent_strategy_id, new_strategy_id (a lowercase slug), operator "
        "(one of the enumerated operators), arguments (object), rationale (state what "
        "you predict will change and why), name_suffix."
    )


def _experiment_prompt(
    hypothesis_id: str, strategy_id: str, instrument: str, timeframe: str
) -> str:
    return (
        f"Hypothesis: {hypothesis_id}. Strategy: {strategy_id}. "
        f"Instrument: {instrument}. Timeframe: {timeframe}.\n\n"
        "Pre-register the experiment. Return a JSON object matching the "
        "design_experiment schema: train_start, train_end, test_start, test_end "
        "(ISO dates; the test window must start at or after the train window ends), "
        "embargo_bars, n_folds, seed, n_trials_in_search, success_criteria (a list of "
        "{metric, comparator, threshold, rationale}), notes."
    )


def _critique_prompt(strategy_id: str | None, report: Mapping[str, Any]) -> str:
    return (
        f"Candidate: {strategy_id or '(unknown)'}\n"
        f"Validation report: {json.dumps(dict(report), sort_keys=True, default=str)[:6000]}\n\n"
        "Try to DISPROVE this. Return a JSON object matching the record_critique "
        "schema: target_kind, target_id, objections (at least three, each with claim, "
        "decisive_test, severity, evidence), verdict, summary. Every decisive_test "
        "must name a measurement, not a sentiment. Generic caution will be rejected."
    )


def _filing_prompt(result: WorkflowResult, report: Mapping[str, Any]) -> str:
    return (
        f"Cycle {result.workflow_id}.\n"
        f"Hypothesis {result.hypothesis_id}, strategy {result.strategy_id}, "
        f"experiment {result.experiment_id}, backtest {result.backtest_id}, "
        f"validation report {result.validation_report_id}, critique {result.critique_id}.\n"
        f"Verdict: {report.get('verdict', 'unknown')}. "
        f"Failed checks: {report.get('failed_checks', [])}.\n\n"
        "File the note. Return a JSON object matching the file_research_note schema: "
        "title, body, tags, links. The body states what was asked, what was run, what "
        "the numbers were, what the critic said, and what should NOT be retried. Do "
        "not upgrade the verdict."
    )


def _investigation_prompt(backtest_id: str, evidence: Mapping[str, Any]) -> str:
    return (
        f"Backtest {backtest_id} underperformed.\n"
        f"Evidence: {json.dumps(dict(evidence), sort_keys=True, default=str)[:8000]}\n\n"
        "Return a JSON object with: most_likely_cause (one of 'always_this_bad', "
        "'market_changed', 'execution_changed', 'data_defect'), reasoning, "
        "supporting_evidence (list), contradicting_evidence (list), "
        "next_measurement (what would settle it), confidence (0-1)."
    )


# ---------------------------------------------------------------------------
# An offline script for EchoProvider
# ---------------------------------------------------------------------------


def offline_research_script(
    *,
    instrument: str,
    timeframe: str,
    new_strategy_id: str,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    operator: str = "scale_stop",
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Canned, deterministic model responses for every step of the cycle.

    This is a TEST DOUBLE, not a model.  It exists so the whole loop can be
    exercised with no network and no non-determinism; a step whose script entry
    is missing fails loudly rather than silently degrading.
    """
    args = dict(arguments or {"factor": 1.5})
    return {
        "research_brief": json.dumps(
            {
                "question": (
                    f"Does widening the stop on the seed strategy change its "
                    f"expectancy on {instrument} {timeframe}, or only its trade count?"
                ),
                "why_now": (
                    "The seed strategy's exits have never been isolated from its "
                    "entries, so nothing is known about which side carries the result."
                ),
                "decisive_answer": (
                    "A variant with a materially different stop that leaves expectancy "
                    "within the bootstrap CI of the parent would show the stop is not "
                    "where the edge lives."
                ),
                "priority": 2,
            }
        ),
        "hypothesis": json.dumps(
            {
                "title": f"Stop width is not the source of edge on {instrument}",
                "statement": (
                    "Widening the initial stop changes the win rate and the average "
                    "loss in offsetting directions, leaving per-trade expectancy "
                    "statistically indistinguishable from the parent strategy."
                ),
                "rationale": (
                    "If the entry carries genuine directional information, expectancy "
                    "should be first-order insensitive to where the stop sits, because "
                    "a wider stop buys a higher win rate at the price of a larger loss "
                    "per losing trade. If instead expectancy moves sharply with the "
                    "stop, the apparent edge is an artefact of stop placement "
                    "interacting with intrabar path, which does not survive contact "
                    "with a real spread."
                ),
                "testable_prediction": (
                    "Expectancy of the widened variant falls inside the parent's "
                    "bootstrap 95% CI for mean trade return."
                ),
                "falsifier": (
                    "Expectancy moves outside that CI in either direction, which would "
                    "mean the stop, not the entry, is doing the work."
                ),
                "instruments": [instrument.upper()],
                "timeframes": [timeframe],
                "prior_belief": 0.35,
                "tags": ["exits", "stop_width", "ablation"],
            }
        ),
        "mutation": json.dumps(
            {
                "new_strategy_id": new_strategy_id,
                "operator": operator,
                "arguments": args,
                "rationale": (
                    "Predict: win rate rises, average loss rises by roughly the same "
                    "proportion, expectancy roughly unchanged, trade count falls "
                    "slightly as fewer positions are stopped out early."
                ),
                "name_suffix": "wide stop",
            }
        ),
        "experiment_design": json.dumps(
            {
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "embargo_bars": 0,
                "n_folds": 1,
                "seed": 7,
                "n_trials_in_search": 2,
                "success_criteria": [
                    {
                        "metric": "n_trades",
                        "comparator": ">=",
                        "threshold": 80.0,
                        "rationale": "below the house floor nothing is rankable",
                    },
                    {
                        "metric": "deflated_sharpe",
                        "comparator": ">=",
                        "threshold": 0.95,
                        "rationale": "must beat the expected maximum of the search",
                    },
                ],
                "notes": "Pre-registered before the run; the bar does not move afterwards.",
            }
        ),
        "critique": json.dumps(
            {
                "verdict": "reject",
                "summary": (
                    "The result rests on too few trades for any of its statistics to "
                    "mean anything, and the cost model is the optimistic one."
                ),
                "objections": [
                    {
                        "claim": (
                            "The trade count is below the minimum that supports a "
                            "Sharpe estimate, so the headline metric is a statistic of "
                            "noise rather than of the strategy."
                        ),
                        "decisive_test": (
                            "Re-run over a window long enough to produce at least 80 "
                            "closed trades and check whether the per-trade Sharpe stays "
                            "within the original bootstrap confidence interval."
                        ),
                        "severity": "fatal",
                        "evidence": "minimum_trade_count gate in the validation report",
                    },
                    {
                        "claim": (
                            "Costs are modelled with a static spread and zero slippage, "
                            "so the net result is an upper bound rather than an estimate."
                        ),
                        "decisive_test": (
                            "Re-run the sensitivity suite and read the spread multiplier "
                            "at which median net profit crosses zero; if it is below 2x "
                            "the modelled spread the result is a cost artefact."
                        ),
                        "severity": "major",
                        "evidence": "execution profile recorded in the config fingerprint",
                    },
                    {
                        "claim": (
                            "The result may live entirely in one contiguous stretch of "
                            "the sample rather than being distributed across it."
                        ),
                        "decisive_test": (
                            "Split the equity curve into four sequential quarters and "
                            "compare net profit in each; concentration in one quarter "
                            "refutes the claim of a persistent effect."
                        ),
                        "severity": "major",
                        "evidence": "equity curve recorded with the backtest",
                    },
                ],
            }
        ),
        "filing": json.dumps(
            {
                "title": f"Stop-width variant on {instrument} {timeframe}",
                "body": (
                    "Asked whether stop width, rather than entry timing, carries the "
                    "seed strategy's result. Mutated the parent with a single stop "
                    "scaling operator, pre-registered the windows and criteria, ran one "
                    "backtest and the deterministic validation gates, then attacked the "
                    "result. The critic's fatal objection is the trade count: the sample "
                    "does not support the statistics computed from it. Do not re-run "
                    "this variant on the same window; extend the window or drop the line "
                    "of enquiry."
                ),
                "tags": ["exits", "stop_width", "negative_result"],
                "links": {},
            }
        ),
        "diagnosis": json.dumps(
            {
                "most_likely_cause": "always_this_bad",
                "reasoning": (
                    "The drawdown is not concentrated in one regime and the worst trades "
                    "are spread across the sample, which is the signature of a strategy "
                    "with no edge rather than one whose market moved."
                ),
                "supporting_evidence": ["drawdown episodes spread across the sample"],
                "contradicting_evidence": ["telemetry was not available to rule out execution"],
                "next_measurement": "compare realised slippage against the modelled profile",
                "confidence": 0.55,
            }
        ),
    }


def echo_script_for(steps: Sequence[str], payloads: Mapping[str, Any]) -> dict[str, str]:
    """Build an EchoProvider script from step -> payload, JSON-encoded."""
    return {step: json.dumps(payloads[step], sort_keys=True) for step in steps}


__all__ = [
    "StepOutcome",
    "WorkflowDeps",
    "WorkflowResult",
    "echo_script_for",
    "offline_research_script",
    "run_failure_investigation",
    "run_research_cycle",
]
