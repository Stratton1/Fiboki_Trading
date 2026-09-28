# AI Agent Architecture

**Snapshot:** 2026-09-19T05:05Z (`pytest tests/ -q` → 2683 passed, 2 skipped). Package: `src/fiboki/agents/` (11 modules including
`__init__.py`; the largest is `tools.py`, 2,599 lines on 2026-09-28). Tests: `tests/unit/test_agents_{capabilities,roles,
tool_registry,tool_as_of,sandbox,audit,audit_lock,orchestrator,providers,research_writes}.py`,
`tests/integration/test_agents_{permissions,jobs,workflow}.py`. Counts in this document (19
capabilities, 25 tools, 12 roles, 7 write domains, 8 job types) were re-checked against the code
on 2026-09-28.

---

## 1. The cardinal rule

**An LLM is never the final authority on an executable order.**

AI may research, hypothesise, analyse, propose, generate strategy DSL documents, design
experiments, inspect results, suggest mutations, classify qualitative information and
investigate failures.

Deterministic systems own: signal calculation, market-data integrity, order construction,
position sizing, portfolio constraints, execution permission, broker routing, kill switches,
live-capital limits, and the validation gates.

Everything below is the machinery that turns that sentence from a policy into a property.

## 2. How the rule is structurally enforced

Five independent mechanisms, so that breaking the rule requires deleting code rather than
forgetting a paragraph. Each is separately verifiable.

### Mechanism 1 — the enum that cannot express execution

`agents/capabilities.py` defines `Capability`, and the guarantee is an **absence**: there is no
member authorising order placement, position sizing, risk-limit changes, kill-switch operation,
execution-mode changes, broker routing or writes to market data. An agent cannot be granted a
capability that does not exist, and a tool cannot require one either — `tools.ToolRegistry`
refuses to register a tool whose declared capability is not a member of this enum.

The complete set — 19 members, and reading the list *is* the statement of what an autonomous
researcher may touch:

```
READ_MARKET_DATA          READ_DATA_QUALITY         READ_REGIME
READ_EXPERIMENTS          READ_RESEARCH_MEMORY      READ_STRATEGY
READ_VALIDATION_REPORT    READ_TRADE_LEDGER         READ_PORTFOLIO
READ_EXECUTION_TELEMETRY  READ_AUDIT_LEDGER         READ_EXTERNAL_WEB

WRITE_HYPOTHESIS          WRITE_STRATEGY_PROPOSAL   WRITE_STRATEGY_MUTATION
WRITE_EXPERIMENT_DESIGN   WRITE_CRITIQUE            WRITE_RESEARCH_NOTE

SUBMIT_JOB
```

Twelve reads, six research-domain writes, one job submission. Note what is not there. Note in
particular that `READ_PORTFOLIO` and `READ_TRADE_LEDGER` exist — reading about execution is not
authority over it — while nothing that *changes* any of it does.

### Mechanism 2 — the import-time assertion

Because an absence is easy to erode by accident, it is also enforced.
`assert_no_execution_capability()` **runs at module import** and mechanically rejects any member
whose name reads as a mutating verb applied to an execution noun.

The rule is a parse, not a blocklist. An identifier is upper-cased and its separators
normalised; if it contains any of the always-forbidden tokens — `EXECUTE`, `KILL_SWITCH`,
`PLACE_ORDER`, `SUBMIT_ORDER`, `GO_LIVE`, `LIVE_TRADING`, `BROKER_ROUTE` — it is rejected
outright. Otherwise its first token is taken as a verb, and if that verb is a mutating one the
remainder is checked, at token boundaries, against an execution-noun set that includes `ORDER`,
`POSITION`, `POSITION_SIZE`, `SIZING`, `BROKER`, `VENUE`, `FILL`, `KILL_SWITCH`, `RISK_LIMIT`,
`RISK`, `EXECUTION`, `EXECUTION_MODE`, `LIVE`, `LIVE_CAPITAL`, `CAPITAL`, `MARKET_DATA`,
`ACCOUNT`, `MARGIN`, `LEVERAGE`, `STOP_LOSS`, `TRADE`, `MONEY`, `FUNDS`, `LIFECYCLE` and
`PROMOTION`.

Both the member **name** and its **value** are checked, so neither spelling can smuggle one past.
Token-boundary matching means `WRITE_MARKET_DATA` is caught while `READ_MARKET_DATA` is not,
because only the verb differs.

The consequence: adding `PLACE_ORDER`, `SET_RISK_LIMIT`, `WRITE_MARKET_DATA` or
`DISABLE_KILL_SWITCH` is **an `ImportError` for the entire `fiboki.agents` package**. It is not
a lint warning, not a review comment and not a silent policy drift — nothing that imports agents
starts. `ExecutionCapabilityError`'s own docstring says: *if you are reading this in a
traceback, someone added a capability that breaks the cardinal rule; the fix is to delete it,
not to relax the check.*

The same function is called again in `CapabilityResolver.grant`, defensively, because a grant is
another place a forbidden capability could be introduced — for instance by a dynamically
constructed enum. `tests/unit/test_agents_capabilities.py` exercises the parser directly,
including the case that an allowed read-shaped name is *not* flagged.

### Mechanism 3 — the AST test that no agent module reaches the execution layer

`tests/unit/test_agents_research_writes.py::test_the_agents_package_never_imports_an_execution_construct`
walks every `*.py` file under `src/fiboki/agents/`, parses it, and inspects every `Import` and
`ImportFrom` node. It fails on:

- any import **from** a module whose dotted path starts with `fiboki.broker`, `fiboki.risk` or
  `fiboki.portfolio`;
- any import **of** the names `Order`, `Fill`, `OrderType` or `ExecutionMode`, from anywhere.

The assertion message is *"agents may not reach the execution layer"* and it lists every
offending file and line.

This is the mechanism that catches the case the capability enum cannot: an agent module that
does not *declare* an execution capability but simply imports `ExecutionService` and calls it.
There is no way to write that line and keep the test suite green.

### Mechanism 4 — the write surface, and the job queue that cannot express execution

`tools.WriteDomain` enumerates the only destinations a tool may write to (seven members, one of
which, `NONE`, marks a read tool):

```
NONE                        (a read tool)
RESEARCH_HYPOTHESIS         RESEARCH_STRATEGY_PROPOSAL
RESEARCH_EXPERIMENT         RESEARCH_CRITIQUE
RESEARCH_NOTE               JOB_QUEUE
```

Every member is a research artefact or the job queue. There is no `MARKET_DATA`, no
`RISK_CONFIG`, no `EXECUTION_STATE`, no `BROKER`. **A tool that wants to write anywhere else
cannot describe itself.** `ToolRegistry.register` re-runs the capability guard on the tool's
declared capability and refuses any tool whose `mutates` flag disagrees with its write domain.

`fiboki.research.artefacts.ResearchStore` is the concrete destination: hypotheses, strategy proposals,
mutations, experiment designs, critiques and filed notes. It has no `update` method — a revised
hypothesis is a *new* hypothesis with `supersedes` pointing at the old one, so the lineage of a
research programme reads end to end. Job results are appended, never overwritten, because a
re-run that disagrees with an earlier run is a finding, not a correction.

`orchestrator.JobType` is the second half of the same constraint:

```
backtest   validation   walkforward   ablation   sensitivity
data_quality_scan   regime_scan   librarian_filing
```

There is no `ORDER`, no `EXECUTION`, no `PROMOTION`, no `LIMIT_CHANGE`. **A queue that cannot
express an execution job cannot be talked into running one.** Agents can submit five of those
eight (the other three are platform-internal); the tools that appear to "run a backtest" do not
run anything — they submit a payload, and a deterministic handler in `agents/jobs.py` drains it.
The agent's influence ends at the payload: it cannot alter the engine, the cost model, the
metric definitions or the verdict.

`RunValidationIn` makes this concrete, and its docstring names the point: there is no
`min_trades` field and no `dsr_threshold` field, because *the bars are the platform's
(`GATE_SET_V2`) and moving them is not a research decision.* An agent supplies the honest trial
count and the seed; the gates decide.

### Mechanism 5 — the single enforcement point

**An agent does not hold tool functions.** It holds an `AgentSession`, and every call goes
through `AgentSession.call`, which in order:

1. checks the tool is in the **role's** bundle — a tool outside the bundle is refused before its
   capability is consulted, because "my role does not do that" is a different and earlier failure
   from "I lack that permission";
2. checks the capability against the **deny-by-default** resolver;
3. validates the inputs against the tool's pydantic schema;
4. enforces the tool's call and cost budget;
5. runs the handler;
6. validates the **output** against the tool's schema — a handler returning something off-schema
   is a bug caught here, not a hallucination passed downstream;
7. appends **exactly one** audit record, whether the call succeeded, was denied, or raised.

There is no bypass. A handler is never handed out, and nothing else in the package calls a
handler directly. Model calls go through `AgentSession.think`, recorded the same way, so the
ledger shows the prompt that produced the tool call that produced the artefact.

`CapabilityResolver` denies by default: an unknown principal resolves to the empty set, and
there is no wildcard, no "admin" short-circuit and no inheritance.
`tests/integration/test_agents_permissions.py` opens a session with `grant=False`, iterates
every tool the role has, asserts `PermissionDenied` on each, and then asserts that the ledger
contains exactly that many records, all with outcome `DENIED` — *"not 'we documented that agents
cannot place orders' but 'here is an agent trying, and here is the refusal'."*

### The sixth defence, which is a habit rather than a mechanism

`agents/audit.py` records every agent action — every tool call, every model call, every refusal —
with the agent and its role, the tool, the **full** inputs and outputs, the reason and prompt
that produced it, its parent action, the model and model version, token and cost accounting,
wall time, and the outcome.

Append-only is enforced three ways: `AuditRecord` is a frozen dataclass; the ledger interface has
`append` and readers and **no** `update`, `delete` or `truncate` (a test enumerates the public
surface and fails if one appears); and records are hash-chained, each committing to the
previous record's hash, so a record edited or removed from a persisted ledger is *detectable* by
`verify_chain` rather than merely discouraged. The JSONL implementation only ever opens its file
in append mode, and reads through a separate read-only handle, so there is no code path in the
class that can rewrite it.

`Outcome.DENIED` is as important a record as `OK`.

#### Several writers, one chain: the ledger lock

The API process and the research worker can each hold a `JsonlAuditLedger` on the same file.
Each instance caches the chain it has seen, so two unco-ordinated writers would each seal a
record on the same tail and fork the chain. `JsonlAuditLedger.append` therefore:

1. takes an exclusive `fcntl.flock` on a sidecar `<path>.lock` (never written) and holds it
   across read-tail, append and `fsync`;
2. checks that the line ending where this instance stopped reading still carries its cached
   tail hash, then adopts any records another writer appended since, verifying that each one
   continues the chain (sequence, previous hash, own hash);
3. refuses with `AuditChainForkError` (a `ChainError`) if either check fails: the file was
   truncated, replaced, edited or extended by a record that does not continue the chain. A
   refused append writes nothing;
4. seals, appends, `fsync`s, and on the first write also `fsync`s the parent directory, and only
   then adds the record to the in-memory chain.

`reload()` runs the full `verify_chain()`; a ledger whose loaded chain fails verification still
loads, so it can be inspected, but refuses every append rather than burying the break under
valid records. Steady-state appends cost O(1) in the ledger length. `fcntl` makes this POSIX
only (macOS and Linux). `tests/unit/test_agents_audit_lock.py` runs four spawned processes
writing 25 records each and requires exactly 100 records in one valid chain; with the lock
removed the same run forks the chain and loses records.

## 3. The text/behaviour boundary

`agents/sandbox.py` is where agent-produced text meets anything the platform runs. A strategy
proposed by an agent is a `StrategyDocument` — that is, **data**. It is parsed, never
interpreted.

1. **Agent text enters only through `json.loads`.** There is no `eval`, `exec`, `compile`,
   `pickle.loads` or `__import__` anywhere in `fiboki.agents`, and
   `assert_no_dynamic_execution()` walks the package's ASTs and proves it. The check is
   AST-based rather than text-based, so this module's own pattern strings do not produce false
   positives — it looks for *call sites*, not words. `tests/unit/test_agents_sandbox.py` runs
   the proof.
2. **Schemas forbid unknown fields.** `StrategyDocument` and every model beneath it are declared
   with `extra="forbid"`, so a document carrying a field the schema does not know about cannot be
   constructed at all.
3. **Strings are scanned for code-bearing syntax** before validation. Deliberately conservative:
   a hypothesis that happens to contain `exec(` is rejected, because the cost of a false positive
   is a rewrite and the cost of a false negative is a code path we swore did not exist.

Structural-shape limits — depth, node count, string length — are applied **first**, so a hostile
payload cannot exhaust memory before validation rejects it. Order is: shape limits → code scan →
schema validation → compilation. The first failure wins and carries a machine-readable
`SandboxRejection.code`, so the audit ledger records *why* a proposal was refused, not merely
that it was.

There is **no "validate but do not compile" mode**: a proposal nobody can realise is a proposal
nobody can reason about, so compilation is part of acceptance rather than a later, skippable
step.

Mutations are similarly constrained. `MutationOperator` enumerates the complete set of edits a
mutation agent may propose — a mutation is a **named transformation with typed arguments**, not
a free-form patch. An agent cannot write an arbitrary field, because no operator accepts a field
path.

## 4. The roles

Twelve specialists. A role binds three things: the **tools** it may call, the **capabilities**
those tools require (computed as exactly the union of its tools' capabilities — nothing more, so
a role that never reads telemetry cannot read telemetry), and a **system prompt** stating its
remit and, just as explicitly, its limits.

| Role | Tools | Max calls | Task class |
|---|---:|---:|---|
| `research_director` | 4 | 30 | summarisation |
| `quant_researcher` | 7 | 30 | hypothesis generation |
| `strategy_engineer` | 4 | 30 | strategy drafting |
| `strategy_mutation_agent` | 4 | 30 | strategy drafting |
| `statistical_auditor` | 11 | 60 | experiment design |
| `adversarial_quant_critic` | 8 | 40 | critique |
| `market_regime_analyst` | 3 | 30 | classification |
| `execution_analyst` | 4 | 30 | long-context analysis |
| `portfolio_analyst` | 3 | 30 | summarisation |
| `data_quality_analyst` | 2 | 30 | classification |
| `failure_investigator` | 8 | 50 | failure investigation |
| `research_librarian` | 5 | 30 | filing |

Every role carries a per-session budget cap (USD 0.25 by default) alongside its call cap.

The `statistical_auditor` is the only role holding `SUBMIT_JOB`, and even then all five of its
job tools submit payloads to a deterministic worker. The `failure_investigator` holds **no write
capability at all**, so the worst it can produce is a wrong diagnosis in the ledger.

Bundles are validated at import: `validate_role_bundles()` fails the whole package if a role
names a tool the registry does not have, or lacks the capability one of its tools requires.
Roles therefore cannot drift out of step with the registry.

### The standing prompt

Every role prompt is prefixed with the same paragraph, `CARDINAL_RULE`, stated once and stated
plainly:

> You are a research instrument. You may investigate, hypothesise, analyse, propose, design
> experiments, inspect results, suggest mutations and argue. You have no authority over
> execution and no way to acquire it. There is no tool available to you that places an order,
> sizes a position, changes a risk limit, disables a kill switch, enables an execution mode or
> writes to market data. Those decisions belong to deterministic systems that do not consult
> you. Do not ask for such a tool, do not propose one, and do not phrase a research output as an
> instruction to trade. If a task appears to require execution authority, say so plainly and
> stop: that is the correct outcome, not a failure.

This is not decoration. A model told plainly that it cannot reach execution is less likely to
waste a turn trying, and the ledger records the refusal when it does. It is the **weakest** of
the six defences and it is the one an operator can read.

A second standing paragraph, `EPISTEMIC_STANDARD`, carries the honesty clause: prefer honest
underperformance to flattering output; state assumptions; name approximations rather than
letting them pass; *an absent result is not a zero result and an empty tool response is not
evidence of absence*; if the evidence does not support a conclusion, say what it does support
instead.

## 5. The tool surface

25 tools. Read `build_registry()` as the answer to "what can an autonomous agent in Fiboki
actually do".

**Twelve reads** — `query_market_data`, `query_regime`, `query_experiment_history`,
`query_research_memory`, `query_strategy`, `compare_candidates`, `inspect_trade`,
`inspect_drawdown`, `inspect_validation_report`, `query_portfolio`,
`query_execution_telemetry`, `query_data_quality`.

**Two external interfaces** — `search_web`, `fetch_research`. Both are declared as interfaces
with a stub implementation. `StubWebSearch` performs no I/O and **says so in its output**, so a
model receiving an empty result cannot mistake it for "nothing has been published on this".
Wiring a real provider is a deployment decision, not a test one; no test touches a network.

**Six research writes** — `create_hypothesis`, `create_strategy`, `mutate_strategy`,
`design_experiment`, `record_critique`, `file_research_note`.

**Five job submissions** — `run_backtest`, `run_validation`, `run_walkforward`, `run_ablation`,
`run_sensitivity`. All carry `WriteDomain.JOB_QUEUE` and return a `JobTicket`.

Every tool declares, as data: a name and a description the model actually sees; a pydantic input
schema **and** a pydantic output schema, both validated; the single capability it requires;
whether it mutates and to which research domain; and a cost and rate budget.
`ToolBudget.max_rows_returned` is a correctness control as much as a cost one — a tool that
hands a model 40,000 bars has not given it information, it has given it a context-window
problem.

### Pinning the agent's clock: `ToolContext.as_of`

`ToolContext.as_of` is a timezone-aware datetime (a naive one is refused at construction;
any offset is normalised to UTC). When it is set, every dated read tool treats it as "now":

- `query_market_data`, `query_regime` and `query_data_quality` load bars no later than
  `as_of` and keep only candles **closed** by it. Bars are left-labelled, so a bar stamped
  `t` closes at `t + timeframe` and the bar that opens at or before `as_of` but closes after it
  is not visible.
- A model-supplied `start`, `end` or (for `query_regime`) `as_of` later than the pin is
  **clamped** to it, not refused, and the output carries `as_of_clamped: true` with
  `effective_as_of`. A model that supplies no date gets the pin.
- `query_execution_telemetry` hides records signalled or filled after the pin and says so in
  its caveats.

`None` means "now, unpinned": every tool behaves exactly as it did before the pin existed.
Workflows that reason about a point in history should set it; `AgentSession` rebinds the context
with `dataclasses.replace`, which carries the pin through. Nothing in `src/` constructs a
`ToolContext` today: it arrives through `WorkflowDeps.context`, so setting the pin is the
caller's job.

Not pinned: `query_portfolio` returns the provider's snapshot as it is, the research-memory and
experiment-history reads have no time filter, and the job-submission tools that take a window
(`run_backtest`, `run_walkforward`, `run_ablation`) pass their
`start`/`end` to the worker unclamped (see §10).

`BarSource` is a read-only protocol with no `write`. `InMemoryBarSource` raises on a missing key
rather than returning an empty frame, inheriting the data platform's refusal to conflate
"absent" with "empty". `PositionView` and `PortfolioSnapshot` are read-only projections carrying
no mutators, and the default `StaticPortfolioProvider` is deliberately inert.

## 6. The orchestrator is stateless

The obvious design for an autonomous research system is a long-running conductor that holds the
plan, remembers what it has tried, and decides what to do next. That design is the single largest
source of audit drift in these systems: the thing that decided is a process that no longer
exists, its reasoning lived in a context window nobody wrote down, and a re-run produces
something different because the accumulated state differed.

So there is no conductor. There are **named work queues**, and a job is a **pure function of its
recorded inputs** — its type, its payload, and the deterministic services injected into its
handler. Any job can be replayed from its record alone and must produce the same answer.

What the orchestrator *does* hold is a durable job ledger of submitted specs and their outcomes.
That is records, not state: deleting the process and rebuilding it from the ledger changes
nothing.

Guarantees: **idempotent job keys** (submitting the same key twice returns the first record, so a
re-triggered schedule, a retried agent call and a duplicated event all collapse to one
execution); **retries with exponential backoff** bounded by `max_attempts`, after which the job
is dead-lettered rather than retried forever; **scheduled and event-triggered jobs**, both
routing through the same idempotent submit path; and **narrow permissions**, since `JobType`
contains no execution job.

`JobContext` carries no back-reference to the orchestrator. A handler cannot enqueue follow-up
work, read another job's state, or reach the agent that submitted it. It sees its own recorded
inputs and the services injected when the handler was registered.

`register_research_handlers` is called by the platform at start-up, never by an agent, and an
agent can only submit a job for a type registered there — so the set of things the research fleet
can cause to happen is fixed at wiring time.

## 7. Model providers, local-first

Four adapters behind one `LLMProvider` contract: `LocalHTTPProvider` (Ollama / llama.cpp style),
`OpenAICompatibleProvider`, `AnthropicProvider`, and `EchoProvider` (deterministic, offline).

Local-first is a real preference rather than a slogan: `ModelRouter` sorts candidates by
`(estimated cost, not local, model name)`, so a local model satisfying the task class wins
whenever one is registered — and research prompts, which contain the actual strategy ideas, stay
on the machine that generated them. Selection is deterministic; a tie breaks by name, so two runs
of the same research cycle route identically. Temperature defaults to 0.

**No network in tests.** The three remote adapters take an injected HTTP client. Constructed
without one they are inert: they hold their model declarations and raise `ProviderUnavailable` if
asked to generate. Nothing in the module opens a socket at import and nothing constructs a
default client.

Model capabilities — context window, tool-use support, JSON-mode support, per-token cost — are
declared explicitly rather than inferred, because a router that guesses them is a router that
silently sends a 200k-token prompt to an 8k model.

`EchoProvider` has two reproducible modes. `script` maps a routing key to a canned response, which
is how a whole multi-agent workflow is exercised offline. With no matching entry it echoes a
stable digest of the request, which will fail JSON parsing — deliberately, so a missing script
entry is loud rather than a silent degradation.

## 8. Workflows

`run_research_cycle`, the headline one:

```
Research Director    frames the question
Quant Researcher     records a falsifiable hypothesis
Strategy Engineer    proposes a mutation of a seed strategy
Statistical Auditor  pre-registers an experiment, then QUEUES a backtest and a validation run
   (deterministic worker drains the queue — no model is consulted)
Adversarial Critic   reads the validation report and tries to disprove it
Research Librarian   files what was learned, linked to every artefact
```

Every step is an ordinary `AgentSession`, so every model call and every tool call lands in the
audit ledger with its parent action. Reading the ledger for one workflow id reconstructs the
entire cycle: what was asked, what was answered, what was run and what was concluded.

A step whose model output does not parse, or whose tool call is refused, is recorded as a failed
step and the workflow **carries on** — a research cycle that hides its own failures is the thing
this architecture exists to prevent.

`run_failure_investigation` is deliberately read-only: read the damage, then say what most likely
caused it. The investigator holds no write capability at all.

`seed_strategy_id` must already be registered, because the cycle mutates an existing strategy
rather than drafting from nothing: a mutation with a stated prediction is a sharper experiment
than a fresh document with none.

## 9. Documented approximations in the job handlers

Stated in `agents/jobs.py` rather than discovered later:

- FX conversion is identity unless the account currency equals the instrument's quote currency.
  A mismatch must be acknowledged explicitly in the payload, and the acknowledgement is recorded
  as a caveat on the result.
- `run_sensitivity` perturbs **execution** assumptions (spread, slippage, delay, missed fills,
  sample window), not strategy parameters. DSL parameters are declarative and are not substituted
  into rules, so a parameter sweep would be measuring nothing. Saying so is better than shipping
  a sweep that does not sweep.
- The validation gates in `validation_handler` use trade-level returns, which are not
  calendar-spaced, so the Sharpe they produce is per-trade rather than annualised — and the
  report says so.
- `validation_handler` reports everything it cannot compute from a single backtest —
  walk-forward efficiency, PBO, SPA, StepM membership, parameter plateau — as `NOT_EVALUATED`,
  which **blocks** promotion. A gate nobody ran is not a gate that passed; the full ladder in
  `validation/ladder.py` is what produces those numbers.
- `CompiledStrategyRunner` computes indicators **once** over each full frame at construction.
  That is safe only because every indicator in the library is proved causal by
  `tests/unit/test_indicator_causality.py`. Without that proof it would be look-ahead, so the
  shortcut is taken knowingly and cited rather than assumed.

## 10. Gaps at this snapshot

- **No agent has ever run against a real model** in this repository. Every workflow test uses
  `EchoProvider`; no credentials exist and no local model is configured.
- **`search_web` and `fetch_research` are stubs.** They are interfaces, and they say so in their
  own output.
- **No process drains the job queue.** `workers/research_worker.py` exists, is tested, holds a
  single-writer lease and checkpoints honestly — but `register_research_handlers` is never
  called, so `fiboki worker run research` warns that the orchestrator has no handlers and idles.
  The wiring is one function that does not exist.
- **`regime_scan` and `librarian_filing` job types have no agent-facing tool** and no registered
  handler at this snapshot; they are declared for a future wiring.
- **The audit ledger has no retention or rotation policy.** It grows without bound.
- **`ToolContext.as_of` does not reach job submissions.** `run_backtest`, `run_walkforward` and
  `run_ablation` accept a `start`/`end` that is passed to the worker as given, so an agent pinned
  to a historical `as_of` can still queue a backtest over later data and read its result.
  `query_portfolio` is likewise unpinned.
