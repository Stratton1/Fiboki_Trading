# Architecture

**Snapshot:** 2026-09-19T05:05Z, commit `46e815f` plus substantial uncommitted work (157 paths;
see `BUILD_LOG.md` for the moving-target caveat). Verified in this session: `pytest tests/ -q` →
**2683 passed, 2 skipped** in 287 s. Every structural claim below either cites a module or cites
the test that enforces it.

---

## 1. What Fiboki V2 is

One installable Python distribution, `fiboki` 2.0.0, containing a quantitative research
laboratory and the execution machinery to run what the laboratory approves. It is
research-first: the majority of the code exists to decide whether a result can be believed,
and the execution layer exists to make sure that the thing which eventually trades is the
same thing that was tested.

It is local-first. It runs on one machine — in practice a Mac — as a set of processes
started by hand or by `launchd`, against SQLite catalogues and a parquet store on local
disk. There is no cloud dependency in the research or execution path. That is a deliberate
reversal of V1's Vercel + Railway split, which produced three mutually contradictory deploy
configurations, an API that started a trading worker as a daemon thread, and a research
corpus nobody could reproduce.

## 2. Package boundaries and dependency direction

Dependencies point downwards only. A package may import from any package below it and from
none above it.

```
                    api/            HTTP surface, labelled figures, RBAC, health
                     |
          +----------+----------+----------------+
          |                     |                |
      agents/               validation/       obs/        research/
   LLM research fleet     promotion ladder   logging,     experiment ledger,
   (no execution reach)   + gate set         metrics,     memory, lineage
          |                     |            alerts
          |                     |
          |                  stats/          marketstate/
          |            DSR, PBO, SPA, CPCV,   features, regime,
          |            bootstrap, stress      cross-asset, calendar
          |                     |                |
          +---------------------+----------------+
                                |
        broker/  <---  risk/  <---  portfolio/  <---  backtest/
        adapters,      gateway,     sizing,           engine, metrics
        execution      kill switch, construction           |
        service,       limits                            sim/
        mode guard                                    fills, profiles
                                |
                          strategy/        indicators/
                       DSL, compiler,      20 causal indicators
                       primitives, registry
                                |
                             data/
              schema, integrity, versioning, store, resample,
              providers, recorder, telemetry, calendars
                                |
                             core/
              enums, contracts, instruments, money
```

`core/` imports nothing from Fiboki. It holds the enums that are persisted (and therefore
never renamed), the dataclass contracts every layer passes, the instrument registry, and the
money arithmetic. Everything else is built on those four modules.

Two edges deserve naming because they are the ones that were wrong in V1. **`backtest/` and
`broker/paper.py` share `sim/`**: the paper broker does not reimplement filling, it imports
`FillSimulator` and drives it with the same profile, the same intrabar policy, the same
calendar and the same seeded RNG derivation as the engine. And **`agents/` has no edge to
`broker/`, `risk/` or `portfolio/` at all** — see §7.

## 3. The ALPHA → PORTFOLIO → RISK → EXECUTION layering

The central contract, declared in `core/contracts.py`:

| Layer | Produces | Means |
|---|---|---|
| ALPHA | `Signal` | "I think price goes up here." Carries **no size**. |
| PORTFOLIO | `TradePlan` | "Therefore hold exactly this much." Carries **exactly one** size, decided once. |
| RISK | `RiskDecision` | "And you are / are not allowed to." Names every check that ran. |
| EXECUTION | `Order` → `Fill` | "Here is what the venue actually did." |

A `Signal` is frozen and validates itself: positive prices, a stop on the correct side of
entry, take-profits on the correct side, and a timezone-aware bar time. V1 allowed a
wrong-sided stop and relied on a downstream sanitiser; V2 refuses it at the source, so there
is no downstream code that needs to know about the case.

A `TradePlan` may only be produced by `portfolio.sizing.size_trade`. Its `size` is final.
Adapters convert units — lots, contracts, venue granularity — and may refuse a plan, but may
never re-derive its size. V1 sized the same signal three times (paper bot off fleet equity,
router off allocated capital, IG adapter off the live broker balance), so the internal
ledger and the broker disagreed by construction.

An `Order` cannot be constructed without a `client_ref`; the dataclass raises if one is
missing, because without a client-generated idempotency key a retry can double a position.

### How the layering is enforced

Four mechanisms, all mechanical:

1. **`Order` is constructed in exactly one place.**
   `tests/unit/test_no_gateway_bypass.py` parses every file under `src/fiboki/`, finds every
   `Order(...)` call site, and fails unless the only one is
   `broker/execution_service.py::ExecutionService.submit`. This is the test V1 could not have
   had: no unit test catches "nobody calls the risk engine", because every test that calls it
   passes.
2. **The gateway is called before the order exists.** The same test asserts that inside
   `submit`, a `self.gateway.evaluate*` call appears at a lower line number than the `Order`
   construction — permission precedes the instruction, in source order.
3. **The close path goes through the gateway too.** `ExecutionService.close` must call
   `gateway.evaluate_exit`; asserted by the same test. V1's kill switch blocked new orders and
   abandoned open positions, so exits were not first-class.
4. **Adapters may not size and may not decide risk.** The same test parses `broker/paper.py`,
   `broker/oanda.py`, `broker/ig.py` and `broker/simulated_venue.py` and fails if any of them
   calls `size_trade`, `size_for` or constructs a `PortfolioSizer`.

5. **Imports point downwards only.** `tests/unit/test_layering.py` — which `core/contracts.py`
   cited for a long time before it existed — parses every module under `src/fiboki/`, including
   imports deferred inside functions, and fails on any edge to a package of equal or higher
   rank. The ranks are explicit data in that file, its list of documented exceptions is empty,
   and it separately forbids `agents/ -> broker|risk|portfolio`, which rank alone would allow.
   When it was first run it found **no violations**: the convention had in fact been held.

One caveat remains, stated because the source claims otherwise. `broker/base.py` cites
`tests/unit/test_adapter_prohibitions.py`, which **does not exist**. Those prohibitions *are*
enforced, in `test_no_gateway_bypass.py`; the citation is stale, not the control.

## 4. Data flow: raw bytes to a validated candidate

```
  provider bytes                      (data/providers/: histdata, dukascopy, oanda)
        |   declares price_basis, native timezone, whether a forming bar can leak
        v
  canonical_frame()                   (data/schema.py) -- shape only, never cleans
        |   price_basis is a COLUMN, not an annotation
        v
  RAW dataset, immutable              (data/store.py) -- <root>/raw/<SYM>/<TF>/<version>/
        |   version_id = H(content_checksum || canonical(lineage))
        v
  validate() -> IntegrityReport       (data/integrity.py) -- pure; cannot mutate
        |   16 defect codes; ERROR and above block a clean read
        v
  repair(plan) -> new version         explicit, named actor, written reason, new lineage node
        |
        v
  CANONICAL dataset + resample        (data/resample.py) -- explicit epoch-anchored origin
        |
        v
  indicators/  (20, all proved causal)  +  marketstate/ (features, regime, cross-asset)
        |
        v
  strategy/ DSL document -> compile_strategy() -> CompiledStrategy
        |   warmup derived from the document; generate_signal() sees df.iloc[:idx+1] only
        v
  backtest/engine.py + sim/fills.py + sim/profiles.py
        |   signals become pending orders actionable no earlier than the NEXT bar's open
        v
  backtest/metrics.py  -- annualisation measured from the real elapsed span
        |
        v
  validation/ladder.py  -- 7 rungs, fail-fast, each able to reject
        |   rung 6 claims the holdout BEFORE evaluating; one look per content hash
        v
  ValidationReport  -- produced for rejected candidates too, names the binding constraint
```

Every artefact downstream of the store references a `DatasetVersion.version_id`, so any
stored result can re-resolve the exact bytes it was computed from. `research/lineage.py`
walks the chain in the other direction: from a live candidate, through the experiments that
produced it, through the strategy document's mutation ancestry, to the dataset version, its
transformation lineage, and the raw source bytes and their checksum. A gap in that chain is
reported as a gap rather than skipped, because a provenance chain that quietly omits a link
is worse than none.

## 5. Execution flow

```
  Signal  --size_trade()-->  TradePlan  --RiskGateway.evaluate()-->  RiskDecision
                                                                          |
                                             blocked: ExecutionAttempt row recorded, stop
                                                                          |
                                                                      allowed
                                                                          v
                                        write PENDING intent, fsynced, BEFORE dispatch
                                                                          |
                                                              adapter.place_order(Order)
                                                          /               |               \
                                                       ack          BrokerRejected     transport failure
                                                        |                 |                  |
                                              persist broker_ref      REJECTED           UNKNOWN
                                              ACKED/FILLED          (terminal)     (non-terminal; reconcile)
```

The `PENDING` record is flushed to durable storage before the dispatch call is made, so a
process killed at the worst possible instant leaves a record carrying the same client
reference the venue was given. Reconciliation is keyed on the **broker** reference; the one
exception is an intent that never learned its broker reference because the response was lost,
and for those only, the venue is searched by `client_ref` in order to *learn* the broker
reference, which is then persisted. An unconfirmed order is `UNKNOWN`, never a rejection —
V1's most dangerous simplification was assuming the safe outcome exactly when the outcome is
unknown.

## 6. Process topology

Four process roles are designed. Their implementation status at this snapshot is stated
plainly.

| Process | Role | Status |
|---|---|---|
| **API** (`fiboki.api`) | The only surface the operator workstation reaches. Serves labelled figures, RBAC, health, system state. Never starts a worker thread. | **Built and tested.** `app.py`, `settings`, `security`, `deps`, `errors`, `logging`, `provenance`, `models`, `platform`, `health`, `audit_trail`, `seed`, and routers for `auth`, `system`, `markets`, `research`, `trading`, `intelligence`. Covered by `tests/api/`. Run with `make run-api` (loopback). |
| **Research worker** | Drains the agent job queue: backtests, validation, walk-forward, ablation, sensitivity, data-quality scans. Consults no language model. | **Built and tested** (`workers/research_worker.py`, `workers/base.py`), with a single-writer lease, a heartbeat written on every cycle including failed ones, honest `DONE`/`NO_DATA` checkpointing, and real exit codes. `fiboki worker run research`. **Unwired**: invoked standalone it warns that the orchestrator has no handlers registered and idles — `agents/jobs.register_research_handlers` exists and nothing calls it. |
| **Live worker** | Feeds closed bars to compiled strategies, sizes once, calls the gateway, dispatches through the execution service, writes the heartbeat. | **Built and tested** (`workers/live_worker.py`). **Cannot be started**: `fiboki worker run live` deliberately refuses, because it needs a wired execution service, feed, evaluator and risk-context builder, and *"a live worker assembled from command line flags is a live worker whose risk configuration nobody reviewed."* |
| **Scheduler** | Admits work as a function of observed CPU, load, memory and battery state; fires recurring jobs. | **Built and tested** (`workers/scheduler.py`). The `HeartbeatWatchdog` and `ScheduledJob`/`EventTrigger` also exist and are tested; **no process starts a watchdog** outside a test. |

The single most important topology rule, learned from V1: **the API never starts a worker.**
V1's API spawned an in-process trading thread unless an environment variable was set, and that
variable was absent from the web service, so a web service and a worker service could both
trade, sharing a default `worker_id` that made the duplication invisible on the System page.
V2's API package has no worker-start path at all, and a worker takes a **single-writer lease**
before doing anything: the second one exits 75, `AlertEvent.WORKER_LEASE_CONTENDED` fires at
CRITICAL, and both supervisor unit files encode 75 as "do not restart-loop". The lease, the
heartbeat and the exit codes are implemented and tested
(`tests/unit/test_worker_base.py`, `tests/integration/test_worker_processes.py`).

## 7. The agent boundary

`agents/` is a peer of `validation/` and `research/`, not a layer above `broker/`. It has no
import edge to the execution stack, and that is enforced:
`tests/unit/test_agents_research_writes.py::test_the_agents_package_never_imports_an_execution_construct`
walks every AST under `agents/` and fails on any import from `fiboki.broker`, `fiboki.risk` or
`fiboki.portfolio`, or on any import of the names `Order`, `Fill`, `OrderType` or
`ExecutionMode`.

An agent's influence on anything executable ends at a JSON payload submitted to a queue that a
deterministic handler drains. `AI_AGENT_ARCHITECTURE.md` documents the five independent
mechanisms in full.

## 8. Storage

Everything is a file or a SQLite database on local disk. Nothing requires a server.

| Store | Format | Module | Property that matters |
|---|---|---|---|
| Market data | Parquet, partitioned `year=YYYY` | `data/store.py` | RAW is write-once; root is explicit (argument or `FIBOKI_DATA_ROOT`), never searched; a missing dataset raises rather than returning an empty frame. |
| Dataset catalogue | SQLite | `data/versioning.py` | Content-addressed ids; re-registering an id with different facts raises. |
| Experiment ledger | SQLite | `research/experiment.py` | Append-only enforced by **database triggers** on UPDATE and DELETE, not by the absence of a method. |
| Holdout registry | SQLite | `validation/holdout.py` | One consumption row per `(dataset_version, strategy_content_hash)`; the claim is written before the evaluation runs. |
| Quote recorder | CRC-framed append-only segments | `data/recorder.py` | A torn final line is skipped and reported; a corrupt line mid-segment raises. |
| Execution telemetry | Same segment format | `data/telemetry.py` | Signal→decision→submit→ack→fill timestamps, requested vs filled price and size. |
| Order intents | fsynced JSON lines | `broker/execution_service.py` | Append-only; last line per `client_ref` wins; written before dispatch. |
| Kill-switch journal | fsynced JSON lines | `risk/killswitch.py` | State is recovered from the journal on startup. An unflushed kill switch is not a kill switch. |
| Agent audit ledger | JSON lines, hash-chained | `agents/audit.py` | `append` and readers only; `verify_chain` detects an edited or removed record. |
| Operator API audit | JSON lines, hash-chained | `api/audit_trail.py` | Separate ledger for human HTTP actions; refusals recorded as loudly as successes. |
| Research artefacts | JSON | `agents/research_store.py` | The only destination an agent write can reach. |

Anything that must not be lost is in a ledger. Alerts are explicitly best-effort and
in-process, which is why the conditions that matter are level-triggered rather than
edge-triggered.

## 9. Determinism

Determinism is a property the repository has to be able to claim, because every statistical
argument downstream assumes that re-running a backtest reproduces it. V1 had determinism
within one environment and not across environments, because `pandas>=2.0` and `numpy>=1.24`
with no lockfile resolved differently on every machine.

V2 pins every dependency that can change a number to an exact version in `pyproject.toml`,
with the reason written above the block: numpy 2.2.6, pandas 2.2.3, scipy 1.14.1, pyarrow
18.1.0, and the rest. Python is constrained to `>=3.11,<3.13`.

Inside the engine, four rules hold. Every container iterated is a list in insertion order or a
sorted sequence; `BacktestEngine.symbols` is `tuple(sorted(data))` so the iteration order of
the caller's dict cannot matter. No position or instrument is ever ordered by a UUID; the
canonical ledger (`BacktestResult.LEDGER_COLUMNS`) deliberately excludes UUID fields, which are
random by construction and would defeat the very test they are most often mistaken for evidence
of. Stochastic fill models never create their own RNG: `sim/profiles.rng_for(seed, bar_index,
sequence)` derives a counter-based generator, so the draw for bar 900 order 2 is identical
whether or not bar 400 drew anything — which means adding a second instrument to a portfolio
does not silently change the fills of the first. And `BacktestResult.ledger_sha256()` hashes a
canonical text form built from `repr` of each float, which round-trips exactly in CPython.

`tests/unit/test_engine_determinism.py` pins this behaviour.

## 10. Why one distribution rather than twelve libraries

The obvious alternative was to publish `fiboki-core`, `fiboki-data`, `fiboki-sim`,
`fiboki-stats` and so on as separate installable packages, with the dependency direction
enforced by the package manager: `fiboki-data` simply could not import `fiboki-broker`, because
it would not be installed.

That was considered and rejected. The reasons are specific to this project, not general.

**The boundary this project keeps getting wrong is not between packages, it is inside one.**
V1's catastrophic coupling was the risk engine having zero call sites and the paper bot
reimplementing the backtester — both of which are *intra*-repository facts that a package
boundary does not touch. Twelve wheels would have permitted every one of V1's defects. The
enforcement that actually catches them is the AST test that reads the whole tree at once, and
that test is only possible when the whole tree is one tree.

**Version skew would become a correctness bug, not a packaging inconvenience.** A backtest
result is only reproducible if the exact code that produced it can be resolved. With one
distribution, `validation/report.code_version` resolves a single git sha and that sha
determines the behaviour of every layer. With twelve wheels, a stored result would need a
twelve-tuple of versions, and the realistic failure mode — `fiboki-sim` 2.1 against
`fiboki-backtest` 2.0 — produces different fills with no error at all. This project's entire
statistical argument rests on being able to say what code produced a number.

**There is one deployment and one operator team.** Separate packages pay off when independent
teams release on independent cadences, or when a consumer wants one component without the
rest. Neither applies: Fiboki is run by two operators on one machine, and nobody wants
`fiboki-stats` without `fiboki-core`. The cost — twelve changelogs, twelve release processes,
a compatibility matrix, and a cross-package refactor becoming a multi-repository operation —
would be paid every week for a benefit nobody collects.

**Internal layering can be enforced more strictly than pip can enforce it, and more cheaply.**
An AST test can assert that `data/` imports nothing from `broker/`, that `Order` is constructed
in one function, that adapters call no sizer, and that `agents/` touches no execution symbol.
pip can only assert the first of those. V2 currently enforces the last three and not the first;
closing that gap is one test file, not a repository split, and it is on the roadmap.

The honest cost of this decision: nothing *mechanically* stops a future edit adding
`from fiboki.broker import ...` to `data/schema.py`. That is a real risk and it is accepted
deliberately, with the mitigation named — write the import-direction test — rather than
absorbed.

## 11. Deployment model

Local-first, single machine, no cloud dependency. Configuration is read once at process start
from the environment (`api/settings.py`), and an unrecognised `FIBOKI_EXECUTION_MODE` is a
startup error rather than a silent fallback to paper, because a typo must not decide where
orders go. Mutable state lives under `FIBOKI_STATE_DIR` (default `var/`) and never inside the
source tree. Market data lives under `FIBOKI_DATA_ROOT`, which must be an explicitly marked
root — V1 walked up the filesystem looking for one, found a staging directory, and recorded
99% of a research batch as complete having tested nothing.

`deploy/launchd/` and `deploy/systemd/` directories exist and are empty at this snapshot;
`scripts/` and `.github/workflows/` likewise. `DEPLOYMENT.md` documents the intended
arrangement and marks what is not yet there.

## 12. Known architectural gaps at this snapshot

Recorded here so the diagram above is not read as a description of a running system.

- **No application wiring.** The processes exist; nothing composes them. Nothing registers the
  research job handlers, so `fiboki worker run research` idles and says so. Nothing starts the
  heartbeat watchdog. Nothing schedules reconciliation. `fiboki worker run live` refuses by
  design until an entrypoint owns its risk wiring.
- ~~**No import-direction test.**~~ Closed: `tests/unit/test_layering.py` now exists, parses
  every module under `src/fiboki/` and enforces the declared rank order. It found no
  violations on its first run.
- **No Alembic migrations.** The health check reads `alembic_version` and reports the revision as
  unknown, which is a degradation rather than a failure — but it will stay unknown until a
  baseline is written.
- **No market data and no broker credentials** in this repository, so the data platform and the
  OANDA adapter are proved against fixtures and have never touched a real store or a venue.
- **`ruff check src tests scripts` reports 101 findings**, and both the Makefile `check` target
  and the CI `lint` job treat ruff as a gate. The gate is currently red against the working tree.
- **157 paths are uncommitted or untracked**, including the whole of `api/`, `obs/`, `workers/`,
  `cli.py`, `deploy/`, `scripts/` and `.github/`.

`ROADMAP.md` is the authoritative list.
