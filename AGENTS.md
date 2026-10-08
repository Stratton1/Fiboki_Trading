# Agent Charter — Fiboki V2

This is the working charter for any agent — or human — changing this repository. It applies to
every session, and it takes precedence over any summary, status document or prior conversation.

Read `docs/v2/PLATFORM_STATUS.md` for what is true today (`docs/v2/ROADMAP.md` is the 19 September
build snapshot, kept as a record) and `USER_ACTIONS.md` for what waits on the operator. Read `docs/v2/V1_FORENSIC_BASELINE.md`
for why these rules exist; every one of them is a defect that cost this project real time.

---

## 0. The prime directive

**Nothing may be stated as verified unless it was verified in this session.**

Not "the tests pass" — *"I ran `pytest tests/ -q` at 05:05Z and it printed 2683 passed, 2
skipped."* Not "the risk engine is called" — *"`tests/unit/test_no_gateway_bypass.py` parses
every file under `src/` and fails unless `Order` is constructed only in
`ExecutionService.submit`."*

This exists because it is the single most expensive thing that went wrong in V1. Four separate
documents asserted the backend test suite passed; the most recent said it could not complete
offline. A `LIVE_READINESS_REPORT.md` issued a **GO** verdict on 615 passing tests that had been
green only because rogue worker threads reached the live internet. The project's own
non-negotiables claimed deterministic backtests (dependencies were unpinned), mandatory
portfolio-aware risk controls (the risk engine had zero call sites), and an 80-trade minimum for
ranking (the leaderboard ranked a two-trade combination at position twelve).

Three corollaries:

- **A prior summary is evidence of intent, never authority.** Including one written by you, in
  this repository, an hour ago. Verify against the code.
- **A docstring that cites a test is a claim to check.** Two in this tree cite test files that do
  not exist (`docs/v2/ROADMAP.md` §5). Citations rot.
- **When you cannot verify, say so in those words.** "Designed but not wired", "implemented but
  unwired", "not tested". Never let a gap read as a feature.

## 1. Hard safety rules

These are not style preferences. Breaking one is a defect regardless of what it enables.

**Never enable live execution.** Reaching `ExecutionMode.LIVE` requires five independent controls
(`docs/v2/EXECUTION_ARCHITECTURE.md` §6), two of which are source constants —
`LIVE_EXECUTION_COMPILED_IN` and `OANDA_LIVE_HOST_COMPILED_IN`, both `False`. **Do not flip
either.** Do not add a code path that bypasses the mode guard. Do not weaken a parsed-hostname
assertion into a string comparison; a trailing slash defeating a string comparison is how V1's
single environment variable reached a live broker API. Do not remove the IG adapter's hardcoded
demo host, ever, even after live is enabled elsewhere.

**Never write a live-execution flag into committed config.** V1 shipped
`FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` in `render.yaml` and it sat there for months.
`scripts/check_live_flags.py` is a required CI job and is self-tested against a planted copy of
that exact line, misspelling included.

**Never commit a secret.** Credentials come from the environment, read once at process start in
`api/settings.py`.

**Never construct an `Order` outside `ExecutionService.submit`.** There is exactly one call site
and an AST test enforces it. If you need an order somewhere else, you have found a design
problem, not a test to relax.

**Never re-derive a position size.** `portfolio/sizing.size_trade` decides once. Adapters convert
units and may refuse; they may not re-decide. V1 sized the same signal three times and the ledger
disagreed with the broker by construction.

**Never let an agent module import the execution layer.** No import from `fiboki.broker`,
`fiboki.risk` or `fiboki.portfolio`, and no import of `Order`, `Fill`, `OrderType` or
`ExecutionMode`, anywhere under `agents/`. There is an AST test. There is also a capability enum
that cannot express execution and an import-time guard that turns adding one into an
`ImportError` for the whole package — **fix the capability, do not relax the check.**

**Never weaken a golden test.** `tests/golden/` holds hand-calculated financial values. The
marker's own description says never weaken them to make them pass. If a golden test goes red, the
code is wrong or the arithmetic in its docstring is wrong; work out which, in that order.

**Never repair data silently.** `validate` is pure and returns no frame. `repair` requires a
named actor and a written reason and produces a **new dataset version** whose lineage points at
the unrepaired one. No repair action invents a number — the six that exist all remove or reorder.

**Never make absence look like a value.** A missing dataset raises; it does not return an empty
frame. A metric that cannot be honestly computed raises; it does not return a cap. A gate whose
input is missing is `NOT_EVALUATED`, which blocks. A `null` heartbeat age means no worker has ever
beaten, which is not `0`. V1 conflated `no_data` with `done` and silently truncated 8% of a
research grid, then wrote `complete: true`.

**Never start a worker inside the API.** A worker is a process with its own pid, exit code and
supervisor.

**Never quote a number without its provenance.** Eight `Provenance` values exist, ordered by
trustworthiness. The API cannot emit an unlabelled float. Neither should you.

## 2. Research integrity rules

**Headline historical profit is never the ranking criterion.** It is the quantity the search
maximised and the one most sensitive to the luckiest three trades. Rank on properties of the
procedure: DSR, PBO, SPA/StepM membership, walk-forward efficiency, OOS window hit rate,
survival at 2× spread, plateau ratio. `docs/v2/QUANT_RESEARCH_STANDARD.md` §2.

**Selection and validation never share data.** The holdout registry claims *before* evaluating
and refuses a second look keyed on the strategy content hash. `assert_untouched` runs before any
rung. Do not add a path around either.

**Declare the honest trial count.** `LadderConfig.external_trial_count` is added to the clustered
effective count before deflation. The ladder can see this strategy's own sweep; it cannot see
that the campaign also searched eleven others over sixty instruments. Leaving it at zero deflates
against a floor. **Nothing can enforce this for you. It is the one place in the system where an
honest-looking flattering number is trivially available, and it is on you.**

**Do not add strategies to make the roster bigger.** Every one raises the statistical bar for all
the others. V2 starts at five seed documents deliberately. A new document must say why it is a
different bet rather than a reparameterisation — `research/structure.is_reparameterisation`
answers that mechanically.

**State the evidence against.** A strategy document's `hypothesis` must carry the published
evidence *against* the idea, not only for it. The Ichimoku seed states that Deng, Sakurai and
Ueda (2021) found no significant Ichimoku profitability in FX after data snooping, and describes
itself as a baseline: *if the pipeline cannot show this underperforming, the pipeline is broken.*

**Ask research memory first.** Exact hash, structural hash, and similarity. "We tried it and it
died at deflation with a DSR of 0.41" and "we tried it and it died at rung 0 because the data was
wrong" call for opposite decisions.

**Keep rejections.** Every `ValidationReport` is stored, pass or fail. V1 kept only winners, so
its population of results was conditioned on success.

**Name approximations.** Static spreads, static financing, no weekend triple swap, an
unknowable intrabar path, a single-lag Hurst estimator, an economic calendar with no dated
events. Each is stated in the code that owns it and in the relevant standard. Adding one is
allowed; hiding one is not.

## 3. Working method

**Orient before changing anything.** Read the actual code path. The package boundaries are in
`docs/v2/ARCHITECTURE.md` §2 and the dependency direction points downwards only.

**State the current behaviour, the root cause and the risk** before proposing a change. A
cosmetic fix that leaves the root cause is worse than none, because it removes the symptom that
would have led someone to the cause.

**Make the smallest change with the smallest blast radius.** Prefer deleting a wrong thing to
adding a right thing beside it.

**Back the change with a test** — and prefer the test that would have caught the defect over the
test that covers the fix. Unit tests for logic; golden tests for financial arithmetic, with the
arithmetic written out in the docstring; **AST tests for structural properties**, because no unit
test catches "nobody calls this". That last category is the one V1 most needed and least had.

**Run the suite and quote the real number.**

```bash
/home/claude/v2/.venv/bin/python -m pytest tests/ -q
make -f deploy/Makefile check     # lint, typecheck, live-flags, lock-check, golden, test
```

**Report in this shape:** what was wrong; what changed; files touched; tests run with their
actual output; caveats; and whether previously stored results must be re-run or ignored. That
last item is not optional — a change to the engine, the cost model, a metric definition or a gate
threshold **invalidates stored results**, and saying so is the difference between a correction
and a silent divergence.

**Prefer honest underperformance to flattering output.** If the evidence does not support a
conclusion, say what it does support instead.

## 4. Things that are load-bearing and look optional

Do not "tidy" any of these without reading why they are the way they are.

- **Exact dependency pins**, the constraints file and the lockfile. Drift in numpy, pandas, scipy
  or pyarrow is always an error. A result computed against a different numpy is not comparable.
- **`BacktestResult.LEDGER_COLUMNS` excludes UUIDs.** They are random by construction and would
  defeat the determinism test they are most often mistaken for evidence of.
- **`rng_for(seed, bar_index, sequence)` is counter-based, not a shared stream.** That is what
  makes a portfolio backtest reproducible when instruments are added or removed.
- **`DateWindow` is half-open.** An inclusive end puts the boundary bar in both train and test.
- **`unmeasured_correlation = 0.30`, not 0.0.** Assuming zero correlation for a pair you have not
  measured is maximally permissive exactly where you know least.
- **`regime_scalars["unknown"] = 0.6`, not 1.0.** Not knowing the regime reduces size.
- **`effective_trials_by_clustering` rejects the naive equicorrelation adjustment.** At 23,040
  trials that formula returns `N_eff = 3` and would halve the false-discovery threshold.
- **The DSR variance term takes the *larger* of two dispersions.** So feeding in the CPCV path
  distribution can only make deflation more demanding, never less.
- **`PurgedCVRung` re-selects inside every split.** Re-slicing one fixed series reassembles the
  identical sample on every path and produces a "distribution" that is one number repeated.
- **`GateStatus.NOT_EVALUATED` blocks promotion.** A gate nobody ran is not a gate that passed.
- **`HoldoutRegistry.claim` writes before evaluating.** A process that dies mid-evaluation must
  not be able to retry until it gets a number it prefers.
- **Exits run a *smaller* check set.** Running `max_account_risk` on a closing order would refuse
  to let you out of the book precisely when the book is over its limit.
- **`limits_v1_paper` relaxes only data-quality tolerances**, never a risk limit, so paper and
  live risk behaviour stay comparable.
- **The kill switch has no timeout and no deactivate CLI command.** A switch that turns itself
  off is not a kill switch.
- **`UNKNOWN` is non-terminal and blocks resubmission; so do `FILLED` and `CLOSED`.** Those two
  are terminal but mean "this plan already reached the book".
- **`fiboki worker run live` refuses.** A live worker assembled from command-line flags is a live
  worker whose risk configuration nobody reviewed.
- **The compose file has no live-worker service.** A market-facing process started by a
  copy-pasted compose file is the accident this project exists to prevent.
- **CI's `gate` job requires `success`, not `!= failure`.** A cancelled or skipped gate is not a
  passed one.
- **The golden CI job fails on zero collected and on any skip.** `pytest -m golden` exits 0 when
  the marker matches nothing.

## 5. If you are an LLM agent operating inside the platform

You are a research instrument. You may investigate, hypothesise, analyse, propose, design
experiments, inspect results, suggest mutations and argue.

You have **no authority over execution and no way to acquire it**. There is no tool that places
an order, sizes a position, changes a risk limit, disables a kill switch, enables an execution
mode or writes to market data. Those decisions belong to deterministic systems that do not
consult you.

Do not ask for such a tool. Do not propose one. Do not phrase a research output as an instruction
to trade. If a task appears to require execution authority, **say so plainly and stop** — that is
the correct outcome, not a failure.

Every tool call you make, including a refused one, is recorded in a hash-chained append-only
ledger with your full inputs and outputs, the prompt that produced them, and your parent action.

`docs/v2/AI_AGENT_ARCHITECTURE.md` documents the five independent mechanisms that make the above
structural rather than advisory.

## 6. The single test of a change

Before you finish, ask: **would this change let a number be believed that should not be?**

If the answer is yes or maybe, the change is wrong however much else it improves. Every V1 defect
in `docs/v2/V1_FORENSIC_BASELINE.md` pushed in the same direction — towards flattering results —
and no individual one of them looked like a mistake at the time.

## 7. Rules revised on 2026-09-29 (supersede any older statement, including the Claude Project instructions)

These replace V1-era statements that were still steering agents. Each has an enforcing test or a named gap.

| Old statement | Current rule | Enforced by |
|---|---|---|
| "12 strategy bots under a common framework" | Strategies are DSL documents under `research/strategies/` (six seed documents today: the original five plus `tsmom_dual_horizon`, added for K4). Adding one requires `is_reparameterisation` to be false and a written reason it is different. | `strategy/registry.py`, holdout key-version tests |
| "Minimum 80 trades for primary ranking" | Promotion requires the versioned gate set in `validation/gates.py`; ranking is on DSR, PBO, SPA/StepM, WFE, OOS hit and plateau, never on headline profit. Thresholds change only through a pre-registered calibration study (E-1). | `tests/unit/test_validation_gates.py` |
| "KLineChart for charts, Plotly for analytics" | TradingView Lightweight Charts for price; uPlot plus owned SVG/canvas for analytics; no Plotly, no KLineChart; no indicator maths in the browser. | `apps/web/tests/e2e/source-rules.spec.ts` (dependency allow-list, byte budgets) |
| "SQLite in dev / PostgreSQL in prod; Vercel / Railway / Render" | Local-first on Joe's Mac: SQLite (WAL) plus parquet under one `FIBOKI_STATE_DIR`/data root; services under launchd; no cloud dependency in research or execution. | `deploy/launchd/*`, `tests/unit/test_deploy_guards.py` |
| "fibokei_token cookie; SWR; 60/67 instruments" | `fiboki_session` cookie; TanStack Query; 123 registered instruments, every one OANDA practice offers, built from the recorded instruments endpoint (`core/instruments.py`, `core/instruments_oanda_table.py`; OANDA is the only broker, decided 2026-09-30). | `tests/api`, `tests/golden/test_golden_retail_leverage.py` |
| (new) One path resolver | Every persisted path is resolved by `core/paths.resolve_paths`; no module, CLI option or script may default a path on its own. | `tests/unit/test_core_paths.py` |
| (new) A safety input never defaults to a benign value | Every gateway input is `Optional`; `None` blocks in DEMO and LIVE; PAPER may excuse only with an explicit, recorded flag. | `tests/unit/test_risk_context_explicit_inputs.py` (AST) |
| (new) Durable means F_FULLFSYNC on Darwin and torn tails are quarantined, never fatal | `core/durable.py` is the only append path for intents, kill switch, audit and alerts. | `tests/unit/test_durable.py`, `test_intent_store_durable.py` |
| (new) Trial counts come from the ledger | `n_trials` and `external_trial_count` derive from the experiment ledger; a payload may only raise them. | `tests/integration/test_agents_validation_dsr.py` |
| (new) Engine inputs are UTC mid bars | The engine refuses non-UTC indices and non-mid `price_basis`; conversions are fingerprinted. | `tests/unit/test_price_basis_and_utc.py` |
| (new) Regulatory constants carry citations and golden tests | Leverage caps by the ESMA/FCA currency set; per-asset minimum stops. | `tests/golden/test_golden_retail_leverage.py` |
| (new) Every composition root is a reviewed, hashed file | Paper and live workers start only from a committed wiring file whose sha256 is stamped on every attempt row; `fiboki worker run live` refuses. | `tests/unit/test_paper_forward_compose.py`, `test_cli.py` |
| (new) Local models only; every call pinned | The agent backend is llama.cpp or Ollama on loopback; model id + GGUF/manifest digest + manifest hash on every audit record; remote providers only for research roles and only when explicitly configured. | `tests/unit/test_agents_llama_cpp_provider.py`, `test_agents_local_provider.py` |
| (new) Agent influence is a signed tier, not a toggle | `core/tier.py`: T0 observe, T1 annotate (shadow, default), T2 veto entries, T3 dampen size, T4 author candidates. A policy may be enabled only at or above its tier; raising a tier needs a reviewed constant change and a signed record. The never-list is unchanged: order origination, upsizing, risk-limit changes, kill-switch disarm, execution-mode changes, holdout selection. | `tests/unit/test_agent_tier.py`, `test_conviction_channel.py`, `test_event_veto_gateway.py` |
| (new) Research and paper size identically | Portfolio construction runs in both the engine (`portfolio/engine_policy.py`) and the paper runtime, byte-identical by test; the vol target never scales above 1.0. | `tests/integration/test_construction_parity.py` |
| (new) No scraped aggregator data enters a decision path | ForexFactory's feed is opt-in, off by default, comparison-only; Investing.com and undocumented endpoints are not built. | `tests/unit/test_source_registry.py` |
| "18/19 checks" | Say "the checks in `RiskGateway.CHECKS`" rather than a number in prose. | `tests/unit/test_risk_gateway.py` pins the count |
