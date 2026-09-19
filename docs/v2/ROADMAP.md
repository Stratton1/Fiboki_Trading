# Roadmap

**Verified snapshot: 2026-09-19T05:05Z.** `/home/claude/v2/.venv/bin/python -m pytest tests/ -q`
→ **2683 passed, 2 skipped** in 287 s. 125 source files / 51,937 lines; 102 test files / 28,716
lines. `ruff check src tests scripts` → **101 findings**.

**The tree was still being written during this documentation pass.** A second run at 05:18Z gave
**2691 passed, 2 skipped** and 57 ruff findings. Both are real measurements; they are shown as a
pair rather than as one number because the repository was not stable at the time. Re-run the
suite before relying on either — that is the rule this whole document set exists to enforce.

This document replaces V1's contradictory status files. It has four sections and an item appears
in exactly one of them:

1. **Implemented and verified** — the code exists and a test in this repository exercises it.
2. **Implemented but unwired** — the code exists and nothing calls it in a running system.
3. **Designed but not built** — a contract, an enum or a docstring names it; there is no
   implementation.
4. **Blocked on an external dependency** — cannot be finished from inside this repository.

Nothing appears in section 1 unless the test suite run above covers it. Where a source docstring
claims a test that does not exist, that is recorded in §5.

---

## 1. Implemented and verified

### Core and money

Contracts for the ALPHA → PORTFOLIO → RISK → EXECUTION layering, with self-validating `Signal`
(positive prices, stop on the correct side, tz-aware bar time) and an `Order` that refuses to
exist without a `client_ref`. 41 registered instruments (7 FX majors, 20 FX crosses, 2 metals,
2 energy, 10 indices) with FCA/ESMA retail leverage caps as market facts. Mandatory, explicit FX
conversion: `IdentityFxSource` raises on a currency mismatch unless `allow_mismatch=True` was
set deliberately, and `SeriesFxSource` does as-of lookup with a staleness ceiling.

### Simulator

`FillSimulator` with costs on **both** legs, gap-aware fills via one `_worse_exit` rule, an
explicit `IntrabarPolicy` (`STOP_FIRST` default, `TARGET_FIRST` and `PROPORTIONAL` available so
the assumption can be measured), staleness detection that widens the spread symmetrically on
entry and exit, minimum-stop policy (`REJECT`/`WIDEN`/`ALLOW`), guaranteed stops, partial fills,
rejections, and an FX session calendar. Five named, fingerprinted execution profiles:
`IDEALISED_RESEARCH`, `IG_REALISTIC`, `OANDA_REALISTIC`, `IBKR_REALISTIC`, `SEVERE_STRESS`.
Counter-based `rng_for(seed, bar_index, sequence)` so adding an instrument does not change
another instrument's fills.

### Backtest engine and metrics

Event-driven, multi-position, marked to market every bar, with financing charged per rollover
crossing, a bankruptcy guard, and signals becoming pending orders actionable no earlier than the
next bar's open. Canonical ledger text and SHA-256, excluding UUIDs. `compute_metrics` derives
annualisation from the **measured** elapsed span and raises `DegenerateMetricError` rather than
substituting a flattering value.

Tests: `test_engine_behaviour`, `test_engine_determinism`, `test_no_lookahead`,
`test_metrics_honesty`, `test_profiles`, `tests/golden/test_golden_fills`,
`test_golden_pnl`, `tests/integration/test_both_leg_cost_impact`.

### Data platform

Canonical schema with `price_basis` as a **required column**; content-addressed dataset
versioning with lineage; an explicit, marked data root that raises rather than returning an
empty frame; 16 defect codes with validate/repair split so repair is always explicit, versioned
and actor-named; six repair actions, none of which invents a number; resampling with an explicit
epoch-anchored origin and nesting assertion; session calendars via a real tz database;
CRC-framed crash-safe quote recorder and execution telemetry; three provider implementations
whose defects are declared (HistData bid/EST-no-DST/zero-volume, Dukascopy as another venue's
prices, OANDA tick-count volume and forming-candle filtering); and the recorded V1 migration
that corrects and declares rather than repairing.

### Indicators and strategy DSL

20 indicators, **every one proved causal** by a suite the registry generates, so adding one
without a proof is impossible. Non-mutating `compute`. `VolumeUnavailableError` rather than a
silent fallback. Versioned DSL with a mandatory hypothesis, a mandatory stop, a list of
take-profit legs with allocations, explicit parameter domains, derived warmup, derived
complexity score, semantic content hash, and `extra="forbid"` throughout. **Parameter binding
works** (`StrategyDocument.bind`, `bind_defaults`). Registry with content-hash deduplication and
a nine-code health check.

### Statistics and validation

DSR, PSR, expected-max-Sharpe and MinTRL; PBO via CSCV; Hansen SPA, White Reality Check and
Romano-Wolf StepM on a shared stationary-bootstrap draw; purged and combinatorial purged CV;
Bonferroni, Holm, Benjamini-Hochberg and clustering-based effective trial count; block bootstrap
with Politis-White/Patton block length and compounding-aware ruin simulation; seven stress
curves; parameter plateau analysis.

The seven-rung ladder with a versioned nine-gate set, a holdout registry that claims **before**
evaluating, `assert_untouched` before any rung runs, walk-forward that actually transfers
selected parameters, selection repeated inside every CV split, and a `ValidationReport`
produced for rejected candidates that names the binding constraint and serialises
deterministically. `tests/integration/test_validation_ladder_real_engine.py` runs the ladder
against the real backtest engine.

### Risk, portfolio, execution

One sizing authority, proved to survive unchanged to the venue. 18-check mandatory gateway that
fails closed, verifies its own coverage, records every attempt, and runs a smaller named set for
exits. Kill switch with `PAUSE`/`FLATTEN` semantics decided at activation, an fsynced journal
and no self-clearing. Versioned, fingerprinted limit sets. Thirteen-step portfolio construction
pipeline recording every scaling factor. Execution service writing a fsynced PENDING intent
before dispatch, with UNKNOWN as a first-class non-terminal state and reconciliation keyed on
the broker reference. Paper broker driving the backtester's own fill model, with byte-identical
parity asserted across four profiles.

AST proofs: `Order` constructed in exactly one function; the gateway called before it in source
order; the close path consulting the gateway; no adapter calling a sizer.

### Execution-mode isolation

Five independent live controls in the mode guard, three more in the OANDA adapter, and the IG
adapter's compile-time demo-only impossibility. Each tested on its own with live still blocked.

### Agents

12 roles, 25 tools, 20 capabilities containing no execution capability, an import-time guard
making one an `ImportError`, an AST test forbidding any agent import of the execution layer, an
enumerated write surface, a job queue that cannot express an execution job, a single enforcement
point recording one audit row on every path including refusal, a hash-chained append-only
ledger, deny-by-default resolution, a sandbox with an AST proof of no dynamic execution, and a
stateless orchestrator with idempotent keys, bounded retries and dead-lettering. Offline
multi-agent workflows against a deterministic `EchoProvider`.

### Market state

Causal feature engine with expanding — never full-sample — percentiles, declared warmups,
absence published as `NaN` with an availability flag, and truncation equivalence proved.
Five-axis regime vector with expanding-quantile thresholds and dwell-based confirmation.
Cross-asset panel that inner-joins on common timestamps only and reports coverage. Economic
calendar interface with causal-safe `actual_as_of`. A state engine whose bulk and streaming
paths agree bar for bar.

### Observability, API, workers, deploy

Structured JSON logging with correlation ids; an in-process Prometheus-rendering metric
registry; a 29-event alert taxonomy including the 17 failure events V1's taxonomy lacked, with
per-channel isolation and a `HeartbeatWatchdog` that evaluates on a timer; a health endpoint
whose every field is measured and whose status is the worst component; an HTTP API with
authentication, server-side revocation, login rate limiting, exact-Origin validation where
absence is refusal, double-submit CSRF, ranked RBAC, hash-chained operator audit, and a
`Figure` type that cannot be constructed without a `Provenance`; worker **processes** with a
single-writer lease, a heartbeat written on every cycle including failed ones, real exit codes,
and a resource-aware local scheduler; a `fiboki` CLI with seven command groups; a CI workflow
whose `deploy` job needs a `gate` job and runs only on a tag or an explicit dispatch; a
three-layer dependency pinning scheme (exact direct pins, a constraints file for transitive
incidents, and a verified lockfile); and a live-flag scanner that is self-tested against a
planted copy of V1's exact committed line, misspelling included.

Tests: `tests/api/*`, `test_obs_*`, `test_worker_*`, `test_cli`, `test_deploy_guards`,
`tests/integration/test_worker_processes`.

## 2. Implemented but unwired

These have code and tests. Nothing composes them into a running system.

| Item | State | What is missing |
|---|---|---|
| **Live worker** | `workers/live_worker.py` exists and is tested | `fiboki worker run live` deliberately **refuses**: the live worker needs a wired execution service, feed, evaluator and risk-context builder, and an application entrypoint that owns that wiring does not exist |
| **Research job handlers** | `agents/jobs.register_research_handlers` exists and is tested | `fiboki worker run research` warns that the orchestrator has no handlers registered and will idle. The wiring that registers them is not written |
| **Reconciliation on a schedule** | `ExecutionService.reconcile` and `fiboki broker reconcile` exist | nothing runs it on startup or on a timer |
| **Heartbeat watchdog in a process** | `HeartbeatWatchdog.start()` exists | nothing starts one outside a test |
| **Quote recorder** | recorder, replay reader and `spread_profile` all work | no feed is attached, so nothing is being recorded. **Its value is proportional to elapsed time, which makes this the highest-value unwired item** |
| **Execution telemetry** | store, reader, `slippage_summary`, `divergence_report` | no execution happens, so there is nothing to record |
| **Observed spread replacing the static assumption** | `spread_profile` produces exactly the input a profile needs | nothing consumes it; research still uses `Instrument.typical_spread_pips` |
| **V1 data migration** | `data/migrate_v1.py` and `fiboki data migrate-v1`, tested against a fixture reproducing the real defects | **never run against the real 7.2 GB store.** `data/` in this repository is empty |
| **Strategy lifecycle** | the gateway blocks seven states and the portfolio constructor allocates to six; both tested | **no module owns transitions.** `StrategyDocument` has no lifecycle field, nothing validates a transition, and nothing makes one |
| **Strategy degradation** | `StrategyView.degraded` blocks at the gateway; `AlertEvent.STRATEGY_DEGRADED` exists | nothing computes it |
| **`obs/logging.py` and `api/logging.py`** | both implemented and tested | two implementations of one concern; one should absorb the other |

## 3. Designed but not built

Named somewhere in the source or in the standards; no implementation.

| Item | Where it is named | Note |
|---|---|---|
| **Adapter-prohibition test file** | `broker/base.py` cites `tests/unit/test_adapter_prohibitions.py` | **the file does not exist**, but the enforcement is real — it lives in `test_no_gateway_bypass.py`. A stale citation, not a missing control |
| **Backtest regression pins** | `QUANT_RESEARCH_STANDARD.md` §3 | no committed ledger hash for a named strategy on a named dataset version. The golden tests pin the arithmetic; nothing pins a realistic run |
| **Demotion and stopping rules** | `VALIDATION_STANDARD.md` §7 | PSR-below-0.50 halt, bootstrap-95th-percentile drawdown halt, and a CUSUM on excess return. The primitives exist; the monitors do not |
| **Automatic drawdown-triggered flatten** | `PORTFOLIO_RISK_STANDARD.md` §5 | three drawdown controls block *new* risk; none flattens an existing book automatically |
| **Alembic migrations** | health check reads `alembic_version`; `AlertEvent.MIGRATION_DRIFT` exists | **no `alembic/` directory.** The revision is always unknown, reported as a degradation |
| **Backup and restore** | `OPERATIONS.md` §9 | no command, no rehearsal. The procedure there is derived from the storage design, not from an exercise |
| **Chaos test for a killed worker** | `DEPLOYMENT.md` §10, Gate B | *until a chaos test proves the alert fires, demo promotion is not safe* |
| **Login-failure alert event** | absent from `AlertEvent` | the limiter records failures; nothing alerts on a burst |
| **Log retention / rotation** | — | every ledger grows without bound |
| **`regime_scan` and `librarian_filing` job handlers** | `JobType` declares both | no agent tool and no registered handler |
| **Fibonacci-versus-random-levels test** | `STRATEGY_STANDARD.md` §3 | the V1 audit's cheap falsification of the platform's second namesake. Not run |
| **Frontend** | `apps/web/` exists and is empty | V2 has no operator UI. The API's labelled-`Figure` contract exists for one |
| **Real deployment step** | `ci.yml` `deploy` job | the gating is real; the final step echoes what it would do |

## 4. Blocked on an external dependency

| Item | Blocked on | What can proceed meanwhile |
|---|---|---|
| **OANDA adapter against the real venue** | an OANDA practice account and credentials | the adapter is fully tested against recorded fixtures; nothing has left the process |
| **Two OANDA unknowns** | a practice account | (a) whether the v20 REST API can place orders on a **spread-betting** sub-account, which decides UK tax treatment; (b) how the candle endpoint's pricing compares to the account's own executable stream. Both resolve on a free demo in days. **Resolve them before writing more adapter code, not after** |
| **Real market data** | downloading HistData or Dukascopy, or an OANDA key | the migration, integrity and versioning paths are all tested against fixtures |
| **Dukascopy fetch** | network access and a decision to use it | URL construction, record decoding and tick-to-bar aggregation are pure, implemented and tested; only the HTTP fetch is a stub that raises |
| **Agent research against a real model** | a local model or an API key | every workflow runs offline against `EchoProvider`. `search_web`/`fetch_research` are interfaces that say so in their output |
| **`pip-audit` in CI** | network on the runner | the job exists; `make audit` says so when offline |
| **Dockerfile base digest** | `docker buildx imagetools inspect` against a registry | the placeholder digest must be replaced before the image builds |
| **Any claim about live behaviour** | Gates A–D in `DEPLOYMENT.md` §10 | nothing here has ever placed an order |
| **`EventRestriction` blackouts in the backtest engine** | a dated economic-calendar feed | the wiring is DONE: every seed document's `EventRestriction` is now compiled into an `ExitPolicy` and the engine declines an entry whose bar is in blackout. But `marketstate/calendar.py` ships NO dated events, so **every blackout query returns False and every backtest and paper bot trades straight through FOMC and NFP.** The two restrictions that need no feed — `avoid_rollover_hour` and `avoid_month_end` — are live and do bite. **USER ACTION REQUIRED:** load a real feed as `fiboki.marketstate.calendar.USER_ACTION_NOTE` sets out, and pass the resulting calendar to `EngineEvaluator(blackout=...)` or `run_backtest(blackout=...)`. Asserted, not assumed, by `tests/unit/test_engine_exits.py::test_with_no_calendar_the_blackout_is_inert` |

## 5. Contradictions between the source and itself

Recorded rather than smoothed over, because a stale citation is how V1's documentation drifted.

1. ~~**`core/contracts.py` cites `tests/unit/test_layering.py`, which does not exist.**~~
   RESOLVED. The file was written, with the package ranks from `ARCHITECTURE.md` §2 as explicit
   data, an AST walk that also catches imports deferred inside functions, and an empty
   exceptions list. Run against the tree it found **no violations** — the import direction had
   been held by review after all, which is the good outcome and not an argument for leaving it
   unenforced.
2. **`broker/base.py` cites `tests/unit/test_adapter_prohibitions.py`, which does not exist.**
   The prohibitions are enforced in `tests/unit/test_no_gateway_bypass.py`.
3. **`ruff check src tests scripts` is not clean**, and it is a **gate** in both the Makefile
   `check` target and the CI `lint` job — so the gate is red against the working tree. Measured
   at 05:05Z: 101 findings, concentrated in `cli.py` (24), `api/routers/research.py` (9),
   `workers/base.py` (6), `workers/scheduler.py` (4), `api/platform.py` (4). Re-measured at
   05:18Z: 57. It is falling as the build finishes, and it must reach zero. A lint signal that is
   not green is a lint signal nobody reads — which is exactly how V1 normalised 17 errors and 33
   warnings until two `react-hooks/purity` violations went unnoticed.
4. **157 paths are uncommitted or untracked**, including the whole of `api/`, `obs/`,
   `workers/`, `cli.py`, `deploy/`, `scripts/` and `.github/`. `git log` shows three commits, the
   most recent covering validation, agents, market intelligence and portfolio/risk. The V1 audit's
   very first finding was a critical fix that existed only on one laptop. **Commit the tree.**
5. **The V1 audit's MinTRL figures do not reproduce.** It quotes 2.8 years to distinguish an
   observed Sharpe of 1.0 from zero and 11.2 years to distinguish it from 0.5. Computed in this
   session against `stats/sharpe.minimum_track_record_length` at Gaussian moments and 95%
   confidence: **5.1 and 17.2 years**, and 4.4–6.6 / 14.5–23.2 under plausible non-Gaussian
   moments. The implemented values are more demanding, which is the safe direction, but the
   discrepancy should be reconciled before either number is quoted to an operator.

## 6. Priority order

Sequenced by what unblocks the most, and by the principle that nothing later matters until the
numbers can be trusted.

**Now, and it is one command.** Commit the working tree. Then fix the 101 ruff findings so the
gate is green, because a red gate that everyone steps over stops being a gate.

**Next, because its value grows with elapsed time and nothing else does.** Open an OANDA
practice account, resolve the two unknowns, and **start the quote recorder the same day**.
Until it is running, every backtest is using a static spread assumption that nothing can check.

**Then, because without it nothing has been measured.** Run the V1 migration against the real
store; ingest a real dataset; run the ladder end to end against real bars for one seed strategy;
read the `ValidationReport`. Expect it to fail. The Ichimoku seed states in its own hypothesis
that if the pipeline cannot show it underperforming, the pipeline is broken.

**Then wire the processes.** The application entrypoint that registers the research handlers,
starts the watchdog, schedules reconciliation and writes the heartbeat. That is what turns
§2 into §1.

**Then close the three named gaps that are one file each.** The import-direction layering test.
A backtest regression pin. The Alembic baseline migration.

**Then the monitors.** Lifecycle transitions with an audited ledger, degradation detection, and
the three pre-registered stopping rules. Nothing should reach paper allocation without a
mechanism that can take it back out.

**Do not** run a large research grid before the above. **Do not** add strategies — every one
raises the statistical bar for all the others, and V2 starts at five seed documents
deliberately. **Do not** enable live execution on any path until Gate C, and keep the IG
adapter's hardcoded demo host permanently even after live is enabled elsewhere.

## 7. The largest risk

Unchanged from the V1 audit, and worth restating because V2 has made it cheaper to discover
rather than less likely: **the corrected engine may show no edge anywhere.**

76% of V1's combinations already lost money before any cost correction. The academic evidence on
these specific rule families in FX after data-snooping correction is largely negative, and
Ichimoku in currencies has an explicitly negative published result. V2's gates are strictly
harder than V1's, its costs are strictly higher, and its data will be correct for the first time.

That is a realistic outcome and the whole apparatus exists to find it out in weeks rather than
with capital.
