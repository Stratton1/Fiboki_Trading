# F. Fiboki V2 backend audit: fresh-eyes review, critique and improvement plan

**Scope.** `src/fiboki` (168 modules, 83,062 lines at the start of the session), `tests/`, `scripts/`, `deploy/`, `.github/`, `pyproject.toml`, `docs/v2`. Read-only: no source file was edited. **Date:** 2026-09-29, roughly 00:15Z to 01:15Z. **Tree:** `/home/claude/fiboki-mac`, branch `v2/integration`, HEAD `a10d428` plus uncommitted work.

**Moving-target warning.** Other agents were editing this tree while the audit ran. Between about 00:20Z and 01:05Z the collected test count rose from 3,946 to 4,180. New files appeared: `data/positioning/`, `data/sources/`, `api/routers/{command,stream,incidents}.py`, `data/providers/forexfactory_feed.py`, `deploy/launchd/uk.fiboki.*.plist`, `scripts/{backup,fiboki-service,launchd-install,llama-server}.sh`. `risk/gateway.py` also gained a 20th check, `event_veto`. Each finding gives the evidence as it stood when I read it. Where concurrent work may already be closing a gap, the finding says so.

---

## 0. Executive summary

The V2 backend is far more disciplined than a typical retail trading codebase. The central mechanisms are real and tested:

- one `Order` construction site;
- a fail-closed, named-check gateway;
- causal indicators with a corrupt-the-future harness;
- next-bar-open fills with costs on both legs and gap-through stops;
- counter-based RNG;
- a claim-before-evaluate holdout;
- an agent package with no import edge to execution.

The layering test passes in spirit: there are no upward imports.

The weaknesses are not in the core arithmetic. They sit at the seams: between processes, between the research and paper configurations, and between what a docstring promises and what a composition root actually wires. In order:

1. **The kill switch is split-brained (P0).** The CLI, the API and the paper runtime each resolve a different kill switch: `~/.fiboki/killswitch.jsonl`, `<FIBOKI_STATE_DIR>/killswitch.jsonl` and an in-memory one. None of them re-reads the journal after construction. An operator who runs `fiboki killswitch flatten` is told that "the live worker's next cycle" will close positions. No process reads that file.
2. **Several numbers can still be believed that should not be (P1):**
   - The agent `validation_handler` computes a DSR against the wrong variance. It also takes the trial count from the job payload, defaulting to 1.
   - The engine ignores `price_basis`, so HistData BID bars are traded as mid.
   - The FCA leverage table is wrong for AUDUSD, NZDUSD, XAGUSD and HK50, in the permissive direction.
   - `RiskContext` defaults `daily_pnl`, `weekly_pnl`, `open_risk_amount` and `correlated_exposure` to 0, and `market_open` to `True`. This is the "absence looks like a value" pattern AGENTS.md forbids.
3. **Continuous operation is not yet supported by the plumbing (P1):**
   - The orchestrator's "durable job ledger" is an in-memory dict.
   - Telegram and webhook alert channels are constructed without a transport, so every send raises.
   - No process starts the heartbeat watchdog.
   - Long deterministic jobs run outside the heartbeat pulse, while the lease TTL is 60 s.
   - Intent, kill-switch and audit journals use `os.fsync`, which on macOS does not flush the drive cache (`F_FULLFSYNC` is needed). A torn last line makes the intent store refuse to load.
   - No forward-running paper trader exists: paper means historical replay.
4. **Portfolio construction is designed and tested but unwired (P1).** Regime scalars, the drawdown throttle, vol targeting, `unmeasured_correlation = 0.30` and the planned conviction dampener live in `portfolio/construction.py`, and no runtime path calls it.
5. **CI would be red on this tree (fact, measured):**
   - ruff on `scripts/` has 5 findings.
   - `mypy` in CI mode reports 4 errors, while `pyproject.toml` says 1.
   - pip-audit found 11 known vulnerabilities in 5 packages, 7 of them in starlette 0.41.3.
   - The lockfile check fails, including an undeclared `sse-starlette` imported by a new router.
   - 10 unit tests fail, all traceable to concurrent in-flight edits.
6. **Several gate thresholds are defensible in direction but badly calibrated, and should be recalibrated by a power study, not by opinion.** `min_trades = 400` ignores effect size. WFE is biased by compounding across anchored folds. The plateau ratio is scale-dependent. The OOS hit rate is 3 of 5 folds. SPA runs on 500 bootstraps. §3 proposes a pre-registered calibration experiment.

§8 has 52 backlog items. The first wave (W0 and W1) is about 12 to 15 engineer-days and closes every P0 and P1 finding.

---

## 1. Architecture health

### 1.1 Does the code match `docs/v2/ARCHITECTURE.md`?

**Mostly, with documentation drift.**

| Claim in ARCHITECTURE.md | Code reality | Evidence |
|---|---|---|
| Dependencies point downwards only | True. `tests/unit/test_layering.py::test_imports_point_downwards_only` passed in this session, with no documented exceptions. | `tests/unit/test_layering.py:52-110` (`RANKS`, `DOCUMENTED_EXCEPTIONS = {}`) |
| Diagram §2 shows `marketstate/` and `stats/` beside `validation/`, and does not show `lifecycle/`, `discovery/` or `workers/` | The authoritative order is `RANKS` in the test. `marketstate`, `stats` and `obs` share rank 85, above `broker` (80), so rank alone would let `marketstate` import `broker`. `lifecycle` is 96, `discovery` 97, `workers` 105. | `tests/unit/test_layering.py:60-100` |
| "18-check" gateway (`AGENTIC_INTEGRATION_PLAN.md` §1 and §4), "nineteen" (`risk/gateway.py` docstring) | 20 checks at 01:00Z. `event_veto` was added concurrently. | `risk/gateway.py:368-389` |
| `deploy/launchd/` and `deploy/systemd/` are empty (§11) | Not empty. `com.fiboki.research-worker.plist` plus five new `uk.fiboki.*.plist` files. | `deploy/launchd/` |
| "no process starts a watchdog" (§6) | Still true. `HeartbeatWatchdog(` is constructed only in tests. | grep over `src/` |
| Research worker "unwired" unless `FIBOKI_AGENT_CYCLES` is on | True. Both launchd plists start `worker run research` without setting it, so the supervised worker idles (`cli.py:896-906` warns). | `deploy/launchd/com.fiboki.research-worker.plist` |
| Experiment ledger append-only by triggers | True. | `research/experiment.py:278-290` |
| Holdout registry "one consumption row per (dataset_version, hash)" | True, but no append-only trigger and no durable instance in any composition root (see P1-7). | `validation/holdout.py:345-365`, `validation/__init__.py:18` |
| "`ruff check src tests scripts` reports 101 findings" | Now 5, all in `scripts/`. `src` and `tests` are clean. | measured, below |
| Engine snapshot "2683 passed" | Stale. 3,946 collected at about 00:20Z and 4,180 at about 01:05Z. | measured |

### 1.2 Documented invariants and their enforcement

Sources: AGENTS.md §1, §2 and §4, ARCHITECTURE.md §3 and §9, and COMMON.md.

| # | Invariant | Enforced by | Status |
|---|---|---|---|
| 1 | `Order` constructed only in `ExecutionService.submit` | `tests/unit/test_no_gateway_bypass.py::test_order_is_constructed_in_exactly_one_place` | Enforced |
| 2 | Gateway call precedes `Order` in source order | same file | Enforced |
| 3 | `close` goes through `evaluate_exit` | same file | Enforced |
| 4 | Adapters never size | same file | Enforced |
| 5 | Imports point downwards; `core` imports nothing above it | `tests/unit/test_layering.py::test_imports_point_downwards_only`, `::test_core_imports_nothing_from_fiboki_above_it` | Enforced |
| 6 | `agents/` never imports broker, risk, portfolio or execution names | `tests/unit/test_agents_research_writes.py::test_the_agents_package_never_imports_an_execution_construct`; `test_layering.py::test_forbidden_edges_do_not_exist[...]` | Enforced |
| 7 | No `eval`/`exec`/`compile`/`pickle` in `agents/` | `tests/unit/test_agents_sandbox.py::test_the_agents_package_contains_no_dynamic_execution` | Enforced |
| 8 | Capability enum cannot express execution | `tests/unit/test_agents_capabilities.py`, import-time guard | Enforced. `test_agents_forecasts.py::test_capability_set_is_pinned_at_21` failed at 00:40Z because concurrent work added a capability. |
| 9 | Every mutating agent tool writes only research or queue domains | `tests/unit/test_agents_tool_registry.py::test_every_mutating_tool_writes_only_to_research_or_the_queue` | Enforced. Failing at 00:40Z: `record_event_annotations` writes `RESEARCH_EVENT_ANNOTATION`, not yet allow-listed. The guard is working. |
| 10 | `LIVE_EXECUTION_COMPILED_IN is False` | `tests/unit/test_mode_guard.py:63-65` | Enforced |
| 11 | `OANDA_LIVE_HOST_COMPILED_IN is False` | `tests/unit/test_oanda_adapter.py:212` | Enforced |
| 12 | Production code never passes `ModeGuard(compiled_in=...)` or `live_host_compiled_in=` | none | **Gap.** The constructor parameter exists "only so a test can prove..." (`broker/mode_guard.py:238-242`), but no AST test forbids its use under `src/`. |
| 13 | No committed live flag | `scripts/check_live_flags.py`, `tests/unit/test_deploy_guards.py`, CI self-test | Enforced |
| 14 | IG adapter keeps its hard-coded demo host | `tests/unit/test_ig_adapter.py` (AST) | Enforced |
| 15 | Size decided once | `tests/unit/test_sizing_authority.py` | Enforced |
| 16 | No silent repair | `tests/integration/test_no_silent_repair.py` | Enforced |
| 17 | Missing data raises, never returns empty | `tests/integration/test_data_store.py` | Enforced |
| 18 | `NOT_EVALUATED` blocks promotion | `tests/unit/test_validation_gates.py`, `test_lifecycle_promotion.py` | Enforced |
| 19 | A never-beaten heartbeat reports `null`, not 0 | `tests/unit/test_api_worker_heartbeat.py::test_api_reports_never_started_with_a_null_age` | Enforced |
| 20 | API never starts a worker | `tests/api/test_stream.py::test_the_stream_never_starts_a_worker` covers one router only | **Partial gap.** No package-wide AST test forbids `Thread(...).start()` or `workers.*.run` under `api/`. |
| 21 | Every API number carries provenance | `tests/api/test_provenance_contract.py` | Enforced |
| 22 | Holdout: claim before evaluate, one look per content hash | `tests/unit/test_holdout_registry.py`, `test_holdout_key_versions.py` | Enforced. By design, `test_a_new_dataset_version_is_a_new_holdout` pins that a data refresh re-opens the holdout (critique in P1-7). |
| 23 | Holdout consumption rows cannot be edited or deleted | none | **Gap.** There are no triggers, unlike `research/experiment.py:278-290` and `research/artefacts.py:449-460`. |
| 24 | Honest `external_trial_count` | "Nothing can enforce this" (AGENTS.md §2) | **Gap that can be closed.** Derive it from the experiment ledger (§8, B-14). |
| 25 | Determinism within one process and across processes | `tests/unit/test_engine_determinism.py::test_a_third_process_agrees_with_the_first_two`, `test_engine_evaluator.py::test_the_result_is_identical_in_a_FRESH_PROCESS` | Enforced on one platform |
| 26 | Determinism across machines and architectures (Linux x86_64 CI against Mac arm64) | none. `deploy/requirements.lock` header says `platform: Linux-x86_64` | **Gap**, material for the Mac move |
| 27 | `LEDGER_COLUMNS` excludes UUIDs | none references `LEDGER_COLUMNS` | **Gap.** Cheap to assert. |
| 28 | `rng_for` is counter-based | `tests/unit/test_profiles.py`, golden fills | Enforced |
| 29 | `DateWindow` is half-open | `tests/unit/test_validation_evaluation.py` | Enforced. `DataStore.read(end=...)` is inclusive (`data/store.py`, `_read_frame` uses `<=`); see P3-9. |
| 30 | `unmeasured_correlation = 0.30`; `regime_scalars["unknown"] = 0.6` | `tests/unit/test_portfolio_construction.py:316` and others | Enforced in the unit, but **the unit is not called by any runtime** (P1-11) |
| 31 | `effective_trials_by_clustering` rejects equicorrelation | `tests/unit/test_multiple_testing.py` | Enforced |
| 32 | DSR variance takes the larger dispersion | `tests/integration/test_validation_ladder.py` | Enforced |
| 33 | `PurgedCVRung` re-selects in every split | `tests/integration/test_validation_ladder.py::test_the_purged_cv_rung_produced_a_real_distribution` (indirect) | Weakly enforced |
| 34 | Exits run a smaller check set | `test_no_gateway_bypass.py`, `test_risk_gateway.py` | Enforced |
| 35 | `limits_v1_paper` relaxes only data-quality limits | `tests/unit/test_limits_and_venue.py` | Enforced |
| 36 | Kill switch has no timeout | `tests/unit/test_killswitch.py::test_there_is_no_automatic_expiry` | Enforced |
| 37 | Kill switch has no deactivate CLI command | none | **Gap.** The API does have a disarm route (`api/routers/system.py:458-479`), which is fine and deliberate. |
| 38 | Every trading process honours the same kill switch, promptly | none | **Gap, P0-1** |
| 39 | `UNKNOWN` is non-terminal and blocks resubmission, as do `FILLED` and `CLOSED` | `tests/integration/test_execution_lifecycle.py` | Enforced |
| 40 | `fiboki worker run live` refuses | `tests/unit/test_cli.py` | Enforced |
| 41 | Compose file has no live worker; CI gate requires success; golden job fails on zero or any skip | `tests/unit/test_deploy_guards.py` | Enforced |
| 42 | Every `FIBOKI_*` variable is declared | `tests/unit/test_api_settings_hygiene.py::test_every_env_name_used_as_a_literal_in_src_is_declared` | Enforced. Failing at 00:40Z on `FIBOKI_GDELT_ENABLED` (concurrent). Blind to scripts: `FIBOKI_LLM_URL`, `FIBOKI_INCIDENT_LOG` and `FIBOKI_DEV_PASSWORD` are used in `scripts/` and never read by `src/` (P1-14). |
| 43 | Signals on closed candles only | `tests/unit/test_no_lookahead.py`, `test_compiler_causality.py`, `test_indicator_causality.py` | Enforced, and the causality harness has a negative control |
| 44 | Timestamps UTC | Engine checks tz-aware only (`backtest/engine.py:1023-1024`) | **Partial gap.** A `Europe/London`-indexed frame is accepted. |
| 45 | Bars fed to the engine are mid | none | **Gap, P1-3** |
| 46 | Sizing leverage equals FCA retail limits | none (values are data) | **Gap, P1-1** |

---

## 2. Correctness risks, ranked

P0: a safety control that silently does not work. P1: a number can be believed that should not be, or continuous operation breaks. P2: material realism, parity or operability defect. P3: hygiene.

### P0

**P0-1. The kill switch resolves three different ways and is cached per process.**

Where:
- `cli.py:1415`, `1430` and `1455` default `--journal` to `~/.fiboki/killswitch.jsonl`.
- `api/settings.py:332-333` uses `state_dir / "killswitch.jsonl"`, where `state_dir` defaults to the relative path `var` (`api/settings.py:287`, `393`).
- `workers/runtime.py:1025` does `gateway = gateway or RiskGateway(limits=limits)`, and `risk/gateway.py` (`RiskGateway.__init__`) does `self.kill_switch = kill_switch or KillSwitch()`, which is an **in-memory** journal.
- `risk/killswitch.py:193` replays the journal once, in `__init__`, and never again.

What goes wrong:
- The operator runs `fiboki killswitch flatten --reason ...` from a terminal. That writes to `~/.fiboki`. The API reads `<repo>/var`, and the paper session's gateway reads memory.
- The CLI then prints "Positions are closed by the live worker's next cycle" (`cli.py:1446-1450`), which is false.
- Even if every process pointed at one file, a process that constructed its `KillSwitch` before the activation would keep trading until restarted.
- The same applies to the API page, which shows the state as of API start.

Fix:
1. Add one path resolver, for example `fiboki.api.settings.resolve_paths()` or a small `core/paths.py` holding data only. All of CLI, API, workers and scripts use it. The CLI must not have its own default.
2. Add `KillSwitch.refresh()`, which re-reads the journal when its size or mtime changes (cheap `stat`). Call it from `allows()`. The file is tiny.
3. Make `RiskGateway` refuse an in-memory kill switch when `mode` is not `BACKTEST`: require an explicit `FileKillSwitchJournal`.
4. Add an integration test in which process A activates PAUSE through the CLI and process B's gateway blocks the very next `evaluate`.

Stored results: none are invalidated.

### P1

**P1-1. The FCA and ESMA retail leverage table is wrong in both directions.**

Where: `core/instruments.py:47-57` (`_MAJORS = {"EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD"}`), `:97` (XAGUSD 20.0) and `:113-117` (HK50 20.0).

What goes wrong: under ESMA Decision (EU) 2018/796, retained by the FCA in PS19/18, a "major currency pair" is any pair made of two of USD, EUR, JPY, GBP, CAD and CHF.
- AUDUSD and NZDUSD are therefore **non-major**, so 20:1. The code allows 30:1: permissive.
- EURGBP, EURJPY, GBPJPY, EURCHF, CADJPY, CHFJPY, GBPCAD, GBPCHF, EURCAD and CADCHF are **major**, so 30:1. The code allows 20:1: conservative.
- Gold is 20:1, which is correct. Silver is "commodity other than gold", so 10:1. The code allows 20:1: permissive.
- "Major indices" are a closed list: FTSE 100, CAC 40, DAX, DJIA, S&P 500, NASDAQ Composite, NASDAQ 100, Nikkei 225, ASX 200 and EURO STOXX 50. Hang Seng (HK50) is not on it, so 10:1. The code allows 20:1: permissive.
- The leverage cap binds in `FixedFractionalSizer` (`backtest/engine.py` `size_for`) and `portfolio/sizing.size_trade`. A tight-stop AUDUSD, NZDUSD, XAGUSD or HK50 trade can therefore be sized 1.5x to 2x above what a UK retail broker would margin.

Fix: define majors by the currency set, not a symbol list, and set XAG and HK50 to 10. Add a golden test listing all 41 instruments with the expected cap and the regulatory citation.

Stored results: any backtest on those four instruments where the leverage cap bound is invalidated. So is `ENGINE_VERSION`: bump it.

**P1-2. The agent `validation_handler` DSR is inflated.**

Where: `agents/jobs.py:453` (`n_trials = int(payload.get("n_trials_in_search", 1))`) and `:485-494` (`sr_variance = float(np.var(returns, ddof=1))`).

What goes wrong:
- `deflated_sharpe_ratio` takes the variance of *trial Sharpe ratios* (`stats/sharpe.py:172-174`). The handler passes the variance of *per-trade returns*.
- With 1% risk per trade, per-trade returns have a standard deviation of about 0.01, so the handler's expected-maximum benchmark `SR_0` is about 0.01 × 3.3 = 0.033 at N = 1,000.
- The null dispersion of a per-trade Sharpe estimated on T = 400 trades is about 1/sqrt(T) = 0.05 (Lo 2002), giving `SR_0` of about 0.165.
- For a strategy with a per-trade SR of 0.1, the handler reports a DSR of about 0.91 where the correct value is about 0.05.
- With `n_trials` absent it returns the PSR, which is no deflation at all, and the trial count is a payload field an agent composes.
- Promotion is still blocked, because the other gates come back `NOT_EVALUATED`. But the DSR figure is recorded, shown, and can anchor a human reviewer.

Fix:
- Use `var(SR_hat) ≈ (1 - γ3·SR + (γ4 - 1)/4·SR²)/(T - 1)` as the null trial variance when no cross-section exists.
- Take N from the experiment ledger (B-14), never from the payload. Refuse when N is unknown, returning `NOT_EVALUATED`.

Stored results: every stored `validation_handler` DSR is invalid.

**P1-3. The engine ignores `price_basis`, so BID bars are traded as mid.**

Where: `backtest/engine.py:1017-1035` (`_validate_frame` keeps only OHLC and never looks at `price_basis`). `data/providers/histdata.py:414` stamps HistData as `BID`. `histdata.bid_to_mid` (`:418`) is never called anywhere. No module above `data/` reads `price_basis` (grep).

What goes wrong: the fill model assumes mid bars (`sim/fills.py` module docstring). On bid bars:
- A long enters at bid + half spread = true mid. That understates the long entry cost by half a spread.
- A short's stop is triggered on bid highs, where the real trigger is the ask. That understates short stop-outs.
- The error is directional. It flatters long-biased strategies and changes stop-hit frequencies.

Fix: `_validate_frame` must accept `price_basis in {MID, SYNTHETIC_MID}` (or the column's absence, with an explicit `assume_mid=True` recorded in the fingerprint). Otherwise it raises. The research bar source then calls `bid_to_mid` with the instrument's typical spread and records it in the lineage.

Stored results: every research result computed on HistData-derived frames is affected, and the magnitude is about half a spread per round trip, per direction. Re-run.

**P1-4. `RiskContext` and `RiskContextBuilder` default to benign values.**

Where:
- `risk/gateway.py:107` (`health: float = 1.0`) and `:153-163` (`open_risk_amount`, `correlated_exposure`, `daily_pnl`, `weekly_pnl` default to `0.0`, `fx_quote_to_account = 1.0`).
- `workers/runtime.py:464` (`market_open: bool = True`), `:476` (`daily_pnl: float = 0.0`), `:486` and `:705` (no correlation matrix means `correlated = 0.0`), and `:580` (no strategy source means `StrategyView(lifecycle=default_lifecycle, health=1.0)`).

What goes wrong: a composition that forgets the P&L ledger passes `daily_loss` and `weekly_loss` against 0 on every order. `risk_inputs_live` (`workers/runtime.py:655-669`) stamps this onto the attempt row, which is honest, but the check still passes. This is exactly the "absence looks like a value" pattern AGENTS.md §1 forbids.

Fix: make these `float | None = None` and block on `None` in DEMO and LIVE. PAPER may keep a recorded pass for research replays. Remove the `market_open=True` default and require `market_open_source`. Add an AST test that `RiskContext(...)` is only constructed with every loss and exposure input passed explicitly.

Stored results: none.

**P1-5. No real FX conversion is wired anywhere, and research and paper run in different account currencies.**

Where: `core/money.py:52-90` (`SeriesFxSource`) is never constructed outside tests (grep; `core/money.py` has 48% unit coverage). Research defaults to USD (`validation/engine_evaluator.py:120`, `validation/run.py:101`, `discovery/campaign.py:145`, `agents/jobs.py:353`). Paper and live default to GBP (`workers/runtime.py:444`, `broker/paper.py:157`, `backtest/engine.py:268`) with `IdentityFxSource()` (`workers/runtime.py:1009`).

What goes wrong: Joe's account is GBP. Research on USD-quoted instruments is computed in USD. A paper session in GBP on a USD-quoted instrument either raises (strict identity) or has to be run under `allow_mismatch`. Monetary limits such as daily loss %, and sizing, therefore differ between research and paper for 30 of 41 instruments.

Fix: build a `SeriesFxSource` from stored daily closes for the GBP crosses, using `max_staleness` of about 4 days, not 7 (`core/money.py:62`). Run all research in GBP. Add a golden P&L test for XAUUSD in a GBP account.

Stored results: USD-denominated research results remain valid as USD figures, but cannot be compared with paper figures.

**P1-6. Journal durability on macOS, and torn-tail handling.**

Where:
- `broker/execution_service.py:299-304` (`JsonlIntentStore.write` uses `os.fsync`) and `:291-297` (`_replay` calls `json.loads` on every line and raises on a partial one).
- `risk/killswitch.py` (`FileKillSwitchJournal.append` uses `os.fsync`; `events()` raises on a partial line).
- The agent audit ledger and alert `FileChannel` are the same pattern.

What goes wrong:
- On macOS, `fsync(2)` does not force the drive's write cache to stable storage. Apple's man page directs callers to `fcntl(fd, F_FULLFSYNC)`. A power cut on the Mac can therefore lose a `PENDING` intent that the code believes is durable, which is the exact crash the intent store exists for.
- A torn final line then makes `JsonlIntentStore(...)` raise at startup, so the worker cannot start until someone hand-edits a ledger. The quote recorder already solves this with CRC framing and torn-tail tolerance (`data/recorder.py`).
- There is also no directory `fsync` after `touch`, so file creation itself is not durable.

Fix: add a shared `durable_append(path, line)` helper that uses `F_FULLFSYNC` on Darwin, plus CRC-framed lines and a torn-tail quarantine to `<file>.torn-<ts>` with a CRITICAL alert. Test it by writing a partial line and asserting that the store loads, reports and alerts.

Stored results: none.

**P1-7. The holdout can be re-spent through data refreshes and reparameterisations, and its table is mutable.**

Where: `validation/holdout.py:387-445` (`define` reserves the final 20% *of each dataset version*), `:618-660` (`claim` is keyed on `(dataset_version_id, strategy_content_hash)`), `:87` (`DEFAULT_HOLDOUT_FRACTION = 0.20`). There are no `CREATE TRIGGER` statements in `holdout.py`. The pinned test `tests/unit/test_holdout_registry.py:76` is `test_a_new_dataset_version_is_a_new_holdout`.

What goes wrong:
1. Appending a month of bars, or repairing a defect, creates a new version and therefore a fresh holdout. Every strategy then gets another look at a segment that overlaps the one it already saw.
2. A mutation or agent loop can emit K reparameterisations with distinct content hashes, and each gets one look. The holdout becomes a selection set, which is the V1 failure mode, only slower.
3. A `DELETE FROM holdout_consumption` resets the registry.
4. No composition root constructs a durable registry. It is only a docstring example (`validation/__init__.py:18`), so every caller chooses its own path.

Fix:
- Define holdout segments on `(instrument, timeframe, calendar span)`, independent of version. A refresh only extends a new forward segment from the last consumed end.
- Key a second consumption table on `research/structure` structural hash, with a per-family look budget (for example 3), and deflate the holdout test by the family's looks (Bonferroni is adequate at small K).
- Add UPDATE and DELETE triggers, with the outcome written in a second append-only table.
- Add one resolved path, `<FIBOKI_HOME>/holdout.sqlite`.

Stored results: holdout verdicts issued after any dataset refresh should be re-examined for double looks.

**P1-8. The orchestrator's "durable job ledger" is in memory.**

Where: `agents/orchestrator.py:297-305` (`self._records: dict`, `self._queues: dict`) against the module docstring ("a durable job LEDGER", lines 18-20) and `ResearchWorker.resume` ("the orchestrator's ledger already holds every job's state").

What goes wrong: a restart, including a launchd restart after a crash, loses every queued job and every idempotency key. A re-fired schedule then re-submits work, and a re-run validation hits `HoldoutAlreadyConsumed`, which is fail-closed but confusing. The API and CLI also cannot submit jobs to a worker in another process at all.

Fix: a SQLite job table (WAL, `busy_timeout`) holding submitted specs, status, attempts and `idempotency_key UNIQUE`. `run_next` claims atomically with the lease fence.

**P1-9. Alerts do not leave the machine, and nothing watches the workers.**

Where: `obs/alerts.py:338-345` and `:407-415` (`from_env` builds a channel with `transport=None`), `:350-355` and `:444` (`send` raises when the transport is missing). `cli.py:890` is the only production call to `build_default_dispatcher` and passes no transport. `HeartbeatWatchdog(` is never constructed outside tests.

What goes wrong: set `FIBOKI_TELEGRAM_BOT_TOKEN` and `FIBOKI_TELEGRAM_CHAT_ID`, and `doctor` lists "telegram" as a channel. Every send then raises, and the error is isolated and logged. A dead worker produces no alert at all, because no watchdog runs. This is the V1 failure the module's own docstring describes.

Fix: inject an `httpx`-based transport in `build_default_dispatcher` when the env is present, and run the watchdog in its own supervised `fiboki watchdog run`. Add a persistent outbox for at-least-once delivery of CRITICAL alerts, and have doctor send a test alert.

**P1-10. Long deterministic jobs run outside the heartbeat pulse.**

Where: `workers/research_worker.py:772-792` (`_run_one` runs without a pulse; only agent work is wrapped, `:794-810`). Lease TTL is 60 s (`workers/base.py` `WorkerConfig.lease_ttl_seconds`; `cli.py:860`). Staleness thresholds are 120 s and 300 s (`obs/alerts.py:628-630`).

What goes wrong: a ladder run takes minutes. During it the lease expires, so a second supervised worker, which exists, see P2-17, can take it and fence out the first. The heartbeat ages past 300 s and the worker reads as "down".

Fix: wrap `_run_one` in `HeartbeatPulse` as well. Also renew between jobs inside the `jobs_per_cycle` loop.

**P1-11. Portfolio construction is not called by any runtime.**

Where: `portfolio/construction.py:225-262` holds `unmeasured_correlation 0.30`, `target_portfolio_vol 0.12`, `vol_target_max_scale 1.50`, `drawdown_derisk_start_pct 5.0`, `drawdown_zero_pct 20.0` and `regime_scalars`. Grep finds no `ConstructionConfig(`, `PortfolioSizer(` or allocator call outside `portfolio/`. The paper path calls `size_trade` directly (`workers/runtime.py:368-376`).

What goes wrong:
- AGENTS.md §4 lists two of these constants as load-bearing, but they are decorative.
- The Wave 4 conviction dampener (`AGENTIC_INTEGRATION_PLAN.md` §4) targets `_step_conviction`, a step that nothing runs.
- `vol_target_max_scale = 1.50` *increases* risk when the vol target is under-shot. Once wired, that fights the "down-only" principle and the 1% per-trade gateway cap: trades get blocked rather than capped.

Fix: wire the allocator into `SignalEvaluator` behind a versioned policy. Cap the scale at 1.0, and add a property test that the final weight is ≤ 1. Mirror it in the backtest (B-24) so research and paper size identically.

**P1-12. The economic calendar has coverage cliffs.**

Where: `src/fiboki/marketstate/fixtures/scheduled_events_official.json`. `coverage.declared_start` is 2024-01-01 and `declared_end` is 2026-12-04T13:30Z. The currencies are EUR, GBP, JPY and USD only. For USD the events are NFP, CPI and FOMC only (read in this session).

What goes wrong:
- Replays and paper sessions refuse ranges not covered (`validation/run.py:145-160`; `workers/runtime.py` `_resolve_calendar`). From 2026-12-05, about 9 weeks from today, a paper session will refuse to start unless the fixture is refreshed.
- Research windows before 2024 run with no blackout, while paper applies one. That is a research/paper parity gap for any document that declares a blackout.
- AUD, CAD, CHF and NZD pairs have no events at all.

Fix: a scheduled calendar-refresh job. Doctor warns 30 days before `declared_end`. Backfill 2010-2023 from the same official archives (the FOMC, BoE, ECB and BLS archives are public). Add RBA, BoC, SNB and RBNZ, plus US retail sales, PCE, ISM and GDP.

**P1-13. The CI gate is red on this tree.**

Measured in this session:
- `ruff check src tests scripts` gives 5 findings, all in `scripts/render_migration_report.py` and `scripts/run_paper_session.py`. CI lints `scripts` (`.github/workflows/ci.yml`, lint job).
- `mypy --ignore-missing-imports --no-strict-optional src/fiboki` gives 4 errors (`api/platform.py:553`, `workers/runtime.py:221`, `cli.py:1873` twice). `pyproject.toml` records 1. The default-mode `mypy` gives 19, including 6 `float(float | None)` in `sim/fills.py:457-521`.
- pip-audit 2.7.3 (run from the scratchpad, not the venv) found 11 vulnerabilities: click 8.1.8 (1), starlette 0.41.3 (7), orjson 3.10.13 (1), pyarrow 18.1.0 (1) and pytest 8.3.4 (1). CI runs `pip-audit --strict`.
- `scripts/lockfile.py verify` exits 1: coverage, mako and pytz changed, plus an extra `sse-starlette` that `api/routers/stream.py:67` imports but `pyproject.toml` does not declare.
- Unit tests: 3,223 passed, 10 failed, 21 skipped. Every failure traced to concurrent edits (agent capabilities and roles, tool write domains, news sources and GDELT, env hygiene).

Fix: W0 items B-01 to B-05.

**P1-14. The local-LLM path does not compose from the desktop launcher.**

Where:
- `scripts/desktop/Start Fiboki.command:45-54` exports `FIBOKI_LLM_URL`, which nothing in `src/` reads (the registry uses `FIBOKI_AGENT_LOCAL_URL`, `api/settings.py:188`). It sets `FIBOKI_AGENT_PROVIDER=local` and `FIBOKI_AGENT_CYCLES=true` but not `FIBOKI_AGENT_LOCAL_MODEL`.
- `workers/research_runtime.py:759-772` builds `LocalHTTPProvider.for_ollama` only, and raises `RuntimeConfigError` without a model.

What goes wrong: with llama.cpp listening on :8080, the launcher turns agent cycles on, and the research worker raises at setup. Unless `~/.fiboki/env` supplies a model, it would also be pointed at :11434, Ollama's port, speaking `/api/chat`. A `LlamaCppProvider` was being added to `agents/providers.py` concurrently (file mtime about 01:02Z), but `_build_provider` did not select it at 01:05Z.

Fix: `FIBOKI_AGENT_LOCAL_BACKEND=llama_cpp|ollama`, declared in `ENV_REGISTRY`. `_build_provider` dispatches on it, and the launcher writes the declared names only. Doctor probes `/v1/models` and `/props` and records the GGUF digest.

**P1-15. Nothing trades forward.**

Where: `cli.py:874-881` (`worker run live` refuses, by design). `scripts/fiboki-service.sh` services are `api|worker|web|news|llama`, with no paper trader. `scripts/run_paper_session.py` is a replay over stored bars.

What goes wrong: the operator intent "the deterministic system trades" is not met. There is no process that polls OANDA practice candles through `OandaPollingBarFeed` (`workers/feeds.py`) into `LiveWorker` in PAPER mode. The pieces exist and are tested; the composition root does not.

Fix: a reviewed `fiboki/entrypoints/paper_forward.py` with a committed, versioned wiring file (strategy set, `LimitSet`, profile, kill-switch path, P&L ledger, correlation, calendar) plus a launchd plist. It stays PAPER-only through `allowed_modes`. That keeps the rule that a live worker is never assembled from CLI flags, while giving paper a reviewed entrypoint.

**P1-16. Sizing ignores costs in the risk-per-unit.**

Where: `backtest/engine.py` `FixedFractionalSizer.size_for` (`risk_per_unit_account = stop_distance * contract_size * fx`) and `portfolio/sizing.py` (the same rule).

What goes wrong: the realised loss at the stop is `stop_distance + half_spread×2 + slippage`. On a 5-pip M15 stop with a 1-pip spread and 0.24 pips of expected slippage, realised risk is about 25% above the nominal 1%. The `max_per_trade_risk` gateway check uses the same understated `plan.risk_amount`.

Fix: include `profile.spread_price + expected_slippage` in the risk-per-unit, as a versioned sizing policy (`fixed_fractional_v2`), and update the golden tests with the arithmetic written out.

Stored results: position sizes change, so P&L scale changes. Sharpe and DSR are approximately invariant.

### P2

| # | Finding | Where | Failure scenario | Fix |
|---|---|---|---|---|
| P2-1 | Blackout window is symmetric ±15 min, measured from the replay bar's *open* | `risk/gateway.py:660-669`; `risk/limits.py` `event_blackout_minutes=15.0`; `ARCHITECTURE.md` §12 admits the H4 case | An H1 entry at 12:10 with NFP at 12:30 passes, and the position is held through the release. In H4 replay the check runs about 4 h early. | Asymmetric window `[now - post, now + max(pre, bar_interval)]` with `pre=30`, `post=15` as a new `LimitSet` version. In replay, `now` = bar close. |
| P2-2 | `margin_utilisation` ignores the plan's own margin | `risk/gateway.py:816-821` | Utilisation of 45% plus a new position worth 15% gives 60% with no block | `util_after = (margin_used + notional/leverage)/equity` |
| P2-3 | Snapshot regime is taken from the first open position's instrument | `workers/runtime.py:615-618` | A GBPJPY plan is sized off EURUSD's regime | Regime per plan instrument |
| P2-4 | `abnormal_spread` is tautological when the spread comes from the profile, and passes when `typical <= 0` | `workers/runtime.py` `_spread`; `risk/gateway.py:641-642` | Paper can never block on spread (5.0x limit against a profile of at most 3x) | Live spread source from OANDA pricing, or block on `estimated_spread` in DEMO |
| P2-5 | DSL `session_window` uses fixed UTC hours, an inclusive end, and the bar-*open* stamp | `strategy/primitives.py:525-556` | A "London 08-11" rule trades 09-12 local half the year. On H4 it gates on the bar's open, not the decision time. Other models in the codebase use half-open windows. | Add a `tz` field (zoneinfo) and a `decision_time="close"` default under a new DSL schema version. The marketstate sessions are already DST-aware (`marketstate/features.py:486-487`). |
| P2-6 | `FxSessionCalendar` uses fixed 22:00 UTC, ignoring New York DST | `sim/fills.py:194-216` | In summer, bars from 21:00 to 22:00 UTC on Sunday exist but are treated as closed | Anchor to 17:00 America/New_York |
| P2-7 | Financing docstring is wrong. The model charges every calendar night (7 a week) with no Wednesday triple and no weekend exemption. | `backtest/position.py:1237-1251` | The weekly total happens to match (5 + 2), but weekend-only holds pay 3x and Wednesday holds 1/3 | Business-day rollover with a per-asset-class triple day. Fix the docstring now. |
| P2-8 | `Instrument.annual_financing_bps` is shown by the API and never used by the engine. Financing is per profile (290/110 bps) for every instrument and ignores carry sign. | `api/routers/markets.py:122`; `sim/profiles.py:480` | The operator reads a per-instrument rate the engine never used (a provenance lie). Long high-yield FX carry is charged when it would earn. | Rate-history financing from policy-rate series (macro providers exist). Remove or relabel the API field. |
| P2-9 | `min_stop_distance_pips=4.0` is flat across asset classes | `sim/profiles.py:482` | XAUUSD minimum stop of $0.04; index minimum of 4 points | Per-asset-class or per-instrument minimum stop from venue specs |
| P2-10 | Validation uses `IG_REALISTIC` while the plan's venue is OANDA practice | `validation/engine_evaluator.py:122`; `AGENTIC_INTEGRATION_PLAN.md` §7 | Research and paper frictions diverge | Gate on the worse of the IG and OANDA profiles, or on the target venue's measured profile |
| P2-11 | WFE is biased by compounding | `validation/ladder.py:1187-1197` (money profit per day); `FixedFractionalSizer` compounds; anchored train windows are 1 to 5 slices long against 1-slice tests | For profitable strategies IS profit per day is inflated by longer compounding, biasing WFE down (not flattering, but miscalibrated) | Compute WFE on log-growth per day or on Sharpe |
| P2-12 | Plateau ratio is scale-dependent and includes the point itself | `stats/stability.py:202` | A score of 0.12 against a neighbourhood mean of 0.09 gives a ratio of 1.33, a FAIL, on noise. Near zero the ratio explodes. | See §3 |
| P2-13 | Sharpe on bar returns with no autocorrelation adjustment | `backtest/metrics.py:127-140`, `:257` | Persistent positions give autocorrelated bar returns, and `sqrt(n)` annualisation is biased (Lo 2002) | Sharpe on daily (17:00 NY) resampled equity, plus Lo's adjustment. Report both. |
| P2-14 | SQLite stores without WAL or `busy_timeout` | `validation/holdout.py:355`, `research/experiment.py:463`, `data/versioning.py:333` (plain `create_engine`); `workers/base.py:227-233` applies pragmas to one pooled connection only | With continuous agents plus campaigns plus the API, you get `database is locked` after 5 s, and holdout claims fail | A `connect` event listener setting `journal_mode=WAL`, `busy_timeout=30000` and `synchronous=FULL` on every connection |
| P2-15 | Absolute `storage_path` in the dataset catalogue | `data/store.py:358` | Moving the data root, or the Mac, breaks every read | Store paths relative to the marked root, and resolve at read |
| P2-16 | Secret hygiene | `broker/oanda.py:295` (`api_token` in the dataclass repr); `api/routers/auth.py:101` (unsalted SHA-256 passwords); `scripts/dev-up.sh:8,20` (default password `fiboki-dev` shared by joe and tom) | A traceback or log of `OandaConfig` leaks the token. Weak hashes. | `field(repr=False)` with a SecretStr type; `hashlib.scrypt` with a salt; no default password |
| P2-17 | Two launchd definitions for one worker; `dev-up.sh` kills supervised workers | `deploy/launchd/com.fiboki.research-worker.plist` and `uk.fiboki.worker.plist`; `scripts/dev-up.sh:66,72` (`pkill -f "fiboki worker run"`) | Two supervisors: the lease loser exits 75 and is restarted every 60 s indefinitely. dev-up kills launchd's worker and launchd restarts it. | Delete the legacy plist. dev-up refuses when launchd services are loaded. |
| P2-18 | `ProcessType=Background` for continuous services, with no sleep prevention | both plists | macOS throttles Background QoS, and a sleeping Mac stops feeds and agents | `ProcessType=Standard` for worker, feed and paper; `caffeinate -is -w <pid>` or a power assertion; doctor checks `pmset -g` |
| P2-19 | Launcher installs without constraints; `requires-python <3.13` against Homebrew's default `python3` | `scripts/desktop/Start Fiboki.command:32-35`; `pyproject.toml` | Unpinned transitive dependencies, or an install failure on 3.13 or later | Use `uv` with Python 3.12 pinned and `-c deploy/constraints.txt` |
| P2-20 | Heartbeat thresholds are duplicated in four places and live-worker staleness is inconsistent | `cli.py:929-930`, `obs/health.py:274-275`, `obs/alerts.py:628-630`, `api/settings` (`FIBOKI_WORKER_STALE_SECONDS`); `workers/live_worker.py:295` (900 s) against `risk/limits.py` `max_data_age_seconds` (300 s, or 1,800 s for paper) | Different surfaces disagree about the same worker | One `HealthThresholds` value in settings |
| P2-21 | No AST test that `src/` never passes `compiled_in=` or `live_host_compiled_in=` | `broker/mode_guard.py:238-242` | A future refactor passes `compiled_in=True` from a config value | An AST test only. Mode-guard code is untouched. |
| P2-22 | Agent budgets are in USD, which is meaningless for local models; the docs say USD 0.25 and the code says 1.0 | `agents/session.py:71-73`; `AGENTIC_INTEGRATION_PLAN.md` D-A6 | Local sessions are unbounded in wall time and tokens | Budgets in tokens, seconds and model calls, plus a daily global GPU-seconds cap |
| P2-23 | `num_ctx=8192` fixed for Ollama | `agents/providers.py:421` | Tool outputs (validation reports) exceed 8k, and requests are refused before sending | Per-role context from measured prompt sizes in the audit ledger. 16k to 32k is feasible on a 64 GB or larger Mac with a 20B to 30B Q4 model. |
| P2-24 | Datastore reads materialise per-row string identity columns | `data/schema.py:79` (`instrument`, `timeframe` and `price_basis` are columns); `frame_from_arrow` → `table.to_pandas()` | M1 over 20 years is about 7.3 million rows × 3 Python strings, roughly 1 GB of object overhead | `to_pandas(strings_to_categorical=True)`, or drop the constant columns into metadata on read. Pin `content_checksum`. |

### P3

1. The `rolling_ols_stats` cumulative-sum difference suffers catastrophic cancellation on long, high-priced series (`marketstate/features.py:378-392`). Fix it with a demeaned or sliding-window form, plus a property test against `np.polyfit` on XAUUSD-scale prices.
2. Stats functions default to `rng=None`, meaning OS entropy (`stats/bootstrap.py:78-81`, `stats/spa.py:172`, `stats/stress.py:214-477`). Every current caller seeds, but the default invites a future unseeded call. Make `rng` required, with an AST test.
3. `SeriesFxSource.max_staleness = 7 days` (`core/money.py:62`) is too permissive for daily FX. 4 days covers weekends and holidays.
4. The engine accepts any timezone-aware index (`backtest/engine.py:1023-1024`). Financing uses `.normalize()` (`backtest/position.py:1246`), so a non-UTC index shifts the rollover hour. Require UTC.
5. `abnormal_spread` returns silently when `typical <= 0` (`risk/gateway.py:641`). Block with `typical_spread_unknown`.
6. `OandaConfig.max_requests_per_second=90` (`broker/oanda.py`) is the documented v20 ceiling. Run well under it (10 to 20) on a shared practice token.
7. `cli.py` (2,029 lines) and `agents/tools.py` (2,900 lines) have a radon maintainability index of 0.00 (grade C). `_research_cycle` has cyclomatic complexity 48 (`agents/workflows.py:320`), `data/integrity.validate` 39, `agents/tools._mutate_document` 37 and `cli._diagnose` 36. Split them by command group and by tool.
8. `tests/unit/test_layering.py` and `test_no_gateway_bypass.py` re-parse the whole tree per test, at about 2 s each (measured). A session-scoped cached parse would cut about 20 s.
9. `DataStore.read(end=...)` is inclusive (`data/store.py`, `_read_frame`, `<=`) while `DateWindow` is half-open. Callers must remember this. Rename the argument to `end_inclusive` or switch to half-open.
10. The ForexFactory feed module (`data/providers/forexfactory_feed.py`, added concurrently) cites ForexFactory's own notices that prohibit copying. It is opt-in and comparison-only, which is careful, but it contradicts decision D-A4. The decision should be made by Joe explicitly and recorded; see §7.

---

## 3. Parameters and rules that look outdated or arbitrary

### 3.1 Promotion gates (`validation/gates.py`, `GATE_SET_V2`, `version="v2.0.0-audit"`, lines 293-407)

Principle: a gate set is a statistical procedure with a false-discovery rate and a power. Neither has been measured for this pipeline. Before changing any threshold, run **E-1, a pipeline power and size study**:

- Inject synthetic edges of known size, per-trade SR ∈ {0, 0.03, 0.05, 0.08, 0.12}, into block-bootstrapped real returns and into perturbed price paths.
- Run the full ladder at the real campaign search size.
- Measure P(promote | SR = 0), which is the size, and P(promote | SR = x), which is the power.
- Choose thresholds that give a size ≤ 5% at the campaign level and the best achievable power at SR 0.08.
- Publish the result as `v2.1.0-calibrated`, pre-registered before any real strategy is re-scored.

The per-gate views below are what I would test in E-1.

| Gate | Current | Assessment | Proposal (for E-1 to confirm) |
|---|---|---|---|
| `min_trades` | ≥ 400, rung 0 (`gates.py:303`; `LadderConfig.min_trades`, `ladder.py`) | The rationale ("0.1 per-trade effect separable at this search scale") is a single-test t of 2.0 with no deflation. The gate ignores effect size and higher moments. Minimum track record length (Bailey and López de Prado 2012) is `MinTRL = 1 + [1 - γ3·SR + (γ4-1)/4·SR²]·(Z/SR)²`, which gives 70 trades at SR 0.2, 273 at 0.1 and 1,084 at 0.05 (γ3 = 0, γ4 = 3, 95%). A fixed 400 is lax for weak edges and needlessly strict for strong ones. It also pushes the search towards M15 and H1 strategies, where costs dominate, because D1 rarely reaches 400 per instrument. | `n_trades ≥ max(150, MinTRL_95(SR_hat, γ3, γ4))`, plus a span floor of at least 6 years and at least 2 vol regimes (by `marketstate` regime labels). Keep 400 as the default until E-1 reports. |
| `deflated_sharpe_ratio` | > 0.95 (`gates.py:343`) | Well founded (Bailey and López de Prado 2014), **provided N is honest**. N is the weak point (P1-2, invariant 24). | Keep 0.95. Take N from the ledger (B-14). Compute on daily returns (P2-13). |
| `pbo` | < 0.20 (`gates.py:355`); `cscv_splits=8` | 0.20 is stricter than the usual "PBO > 0.5 means overfit" reading (Bailey, Borwein, López de Prado and Zhu 2017). That is fine. But with 8 splits (70 combinations) and small families the estimate is coarse, and a family of fewer than about 10 configurations makes it close to meaningless. | Keep 0.20. Raise `cscv_splits` to 16. Make it `NOT_APPLICABLE` below 16 configurations, compensated by DSR > 0.975. |
| `spa_consistent_p` | < 0.05; `spa_bootstraps=500` (`ladder.py`) | Hansen (2005) is right for "best of family against a benchmark". At 500 draws the Monte Carlo standard error at p = 0.05 is about 0.0097, so a p of 0.045 is a coin toss. Requiring SPA, StepM **and** DSR conjunctively on correlated statistics costs power. | 2,000 bootstraps, or adaptive draws until the SE is below 0.005. E-1 decides whether SPA and StepM stay conjunctive or become corroborative when DSR ≥ 0.975. |
| `stepm_member` | = 1 | Romano and Wolf (2005). Sound. | Keep. Report the survivor count. |
| `walk_forward_efficiency` | ≥ 50% (`gates.py:318`) | Pardo's (2008) rule of thumb. The implementation's profit-per-day on a compounding sizer with anchored folds is biased (P2-11). Undefined when IS ≤ 0, which blocks: correct. | Compute on log-growth or Sharpe. Keep 50%. Require at least 30 OOS trades per fold, otherwise `NOT_EVALUATED`. |
| `oos_profitable_fraction` | ≥ 0.60 with `walk_forward_folds=5` | With 5 folds, 0.6 means 3 of 5. For a strategy with a true 55% chance of a profitable window, P(≥ 3 of 5) is about 0.59, so this gate has little discriminating power. | 8 folds with ≥ 5 of 8, or require the one-sided Wilson lower bound of the hit rate > 0.5. |
| `point_plateau_ratio` | ≤ 1.25 (`gates.py:404`); the ratio includes the point in its own mean (`stats/stability.py:202`) | Scale-dependent: near-zero scores make the ratio explode. Negative neighbourhoods give NaN and so `NOT_EVALUATED`. Mixing the point into the denominator dilutes the test. | Replace with two scale-free conditions: the median of the neighbourhood excluding the point is ≥ 0.6 × the point score, and the neighbourhood minimum is > 0. |
| `net_profit_at_2x_spread` | > 0 | Tests spread only. | Net > 0 at 2x spread **plus** 1 tick of slippage **plus** financing +200 bps, and Sharpe at 2x ≥ 0.5 × base. |
| (missing) | none | No tail, drawdown or diversification gate | Add: bootstrap 95th-percentile MaxDD ≤ 25% at 1% risk; correlation with the paper book ≤ 0.5; regime coverage (positive expectancy in at least 2 regimes). |

Ladder procedure constants (`validation/ladder.py`, `LadderConfig`): `cv_groups=6`, `cv_k=2` (15 splits, 5 paths), `embargo_pct=0.01`, `stress_samples=100`, `stability_radius=1`, `corr_threshold=0.7`, `seed=20260919`. Proposed: 8 or 10 CPCV groups for more paths; an embargo of at least the maximum holding period in bars, not a fixed 1%; `stress_samples` of 500. `DEFAULT_HOLDOUT_FRACTION = 0.20` (`validation/holdout.py:87`) is reasonable. Combine it with the calendar-span definition in P1-7.

### 3.2 Risk limits (`risk/limits.py`)

| Constant | Current | View | Recommendation or experiment |
|---|---|---|---|
| `max_per_trade_risk_pct` | 1.0 (0.5 in conservative) | For per-trade R with mean 0.1 and sd 1, full Kelly is about 10% of equity per trade, so 1% is about 0.1 Kelly. After a DSR-style haircut, about 0.2 Kelly. Defensible. | Keep 1% for paper and 0.5% for the first demo period. **E-2**: block-bootstrap drawdown distribution of the 5 seeds under each LimitSet. |
| `max_account_risk_pct` | 5.0 | With FX correlation, five 1% positions in USD pairs are one bet | Keep, but make correlation measured (P1-4 and P1-11) |
| `max_daily_loss_pct` and `max_weekly_loss_pct` | 3 and 6 | Measured against current equity, which is conservative. Evaluates against 0 without a ledger (P1-4). | Keep the values and fix the input |
| `max_total_drawdown_pct` | 20 (10 in conservative) | Inconsistent with the construction de-risk ramp (5 to 20), which is unwired | 15 hard stop, with the ramp from 5 wired (P1-11) |
| `max_margin_utilisation_pct` | 50 | At 30:1 that is about 15x notional. Rarely binding. | 30 for paper and demo. **E-2** measures the peak. |
| Notional caps | 1,000% to 2,000% of equity | The code comment explains them as gap backstops. OK. | Keep |
| `correlation_threshold` | 0.60 | Reasonable | Keep. Measure on 60-day daily returns. |
| `max_price_age_seconds` and `max_data_age_seconds` | 90 and 300 (600 and 1,800 for paper) | The live worker uses 900 (`live_worker.py:295`) | Unify (P2-20) |
| `max_spread_multiple` | 3.0 (5.0 paper) | Tautological in paper (P2-4) | Measure spread by hour of week from OANDA practice quotes (the recorder exists) and set to the 99th percentile |
| `min_broker_health` | 0.50 (0.10 paper) | Arbitrary scale | Define health from measured request error rate and latency |
| `event_blackout_minutes` | 15, symmetric, from bar open | P2-1 | pre 30 / post 15, plus at least one bar ahead. **E-3**: 1-minute OANDA data around 2024-26 NFP, CPI and FOMC; measure spread and gap by minute. |

### 3.3 Cost model (`sim/profiles.py`, IG_REALISTIC at lines 466-495)

| Constant | Current | Recommendation |
|---|---|---|
| Spread floor | 0.6 pips; `TypicalMultiplierSpread(1.0)` | A measured `HourOfWeekSpread` table per instrument, versioned (**E-4**, from the quote recorder over 4 or more weeks of OANDA practice) |
| Rollover window | (21, 23) ×3.0; (23, 6) ×1.8 | Anchor rollover to 17:00 New York (DST-aware) and derive the multipliers from E-4 |
| Slippage | p = 0.30, tick 0.4 pips, 1 to 3 ticks (mean 0.24 pips) | Add an event-conditional slippage term (E-3). Scale stop-order slippage by bar range. |
| Financing | long 290, short 110 bps, static, all instruments (`:480`) | Per instrument: benchmark rate differential ± broker markup (OANDA publishes financing rates; IG publishes admin fees). Rate history from ALFRED or the BoE for 2010 onwards. |
| Min stop | 4 pips flat (`:482`) | Per asset class |
| Rejection probability | 0.005 | Measure on OANDA practice |
| Profile seed | 20260919 | Fine. Record it in every fingerprint, which is already done. |
| Instrument spreads (`core/instruments.py:78-117`) | Hard-coded "typical" | Replace with E-4 measurements and stamp the registry version |

### 3.4 Operations and agents

| Constant | Where | Current | Recommendation |
|---|---|---|---|
| Lease TTL | `workers/base.py` `WorkerConfig`, `cli.py:860`; live `workers/live_worker.py:306` | 60 s research, 90 s live | Keep, but pulse all long work (P1-10). Live TTL should be at least 3 × max cycle time. |
| Heartbeat stale and down | four places (P2-20) | 120 and 300 s | One value. For a live H1 worker, keep 120 and 300. Research can use 300 and 900. |
| `pulse_seconds` | `research_worker.py:650` | 15 | Keep |
| `reconcile_interval_seconds` | `live_worker.py:290` | 900 | 300 in DEMO. Reconcile immediately after any `UNKNOWN`. |
| `cycle_budget_fraction` | `live_worker.py:293` | 0.25 | Keep |
| `reject_alert_threshold` | `live_worker.py:297` | 3 | Keep |
| FX session close and open | `sim/fills.py:204` | 22 and 22 UTC | 17:00 New York (P2-6) |
| Financing rollover hour | `backtest/engine.py` `BacktestConfig` | 21 UTC | 17:00 New York |
| Agent session budget | `agents/session.py:71-73` | 60 tool calls, 30 model calls, USD 1.0 | Add `max_wall_seconds` (for example 900), `max_tokens` (for example 200k), and a daily GPU-seconds cap in the scheduler |
| Context | `agents/providers.py:421` | 8,192 | Per role, from measured prompt sizes (P2-23) |
| Output tokens | `agents/providers.py:422`, `:123` | 2,048 and 1,024 | Per role |
| Provider timeout | `agents/providers.py:423` | 300 s | Keep for 20B to 30B local. Log tokens per second. |
| Temperature and seed | `agents/providers.py:124`, `:425` | 0.0 and 0 | Keep. Also record llama.cpp `--seed` and the sampler settings in the manifest. |
| Holdout fraction | `validation/holdout.py:87` | 0.20 | Keep; calendar-span definition (P1-7) |
| Evaluator initial balance and currency | `validation/engine_evaluator.py:119-121` | 10,000 USD | GBP, with the real FX source (P1-5) |
| `warmup_margin_bars` and `min_window_bars` | `engine_evaluator.py:127-131` | 8 and 30 | 30 post-warmup bars cannot hold 30 trades. Tie it to the per-fold OOS trade floor (§3.1). |

---

## 4. Performance and cost

### 4.1 Measured

**Unit suite with coverage.** 4 sequential chunks on 2 shared CPUs, other agents running:

| Chunk | Passed | Failed | Skipped | Time |
|---|---|---|---|---|
| 1 | 764 | 4 | 0 | 58 s |
| 2 | 417 | 0 | 0 | 223 s |
| 3 | 1,317 | 6 | 21 | 61 s |
| 4 | 725 | 0 | 0 | 62 s |

That totals 3,223 passed, 10 failed and 21 skipped. Collection counts: 3,946 at about 00:20Z (`pytest -q --co`) and 4,180 at about 01:05Z. The golden marker collects 86 tests across 10 files.

**Slowest unit tests** (`--durations=40`):

| Test | Time |
|---|---|
| `test_discovery_mutation.py::TestOperatorSurface::test_proposals_are_deterministic` | 23.5 s |
| `...test_every_operator_yields_valid_documents_or_a_recorded_refusal[rsi_band_mean_reversion]` | 17.3 s |
| same, `[macd_ema_trend_hybrid]` | 11.4 s |
| `test_discovery_campaign.py` (3 tests) | about 10 s each |
| `test_compiler_causality.py::test_signals_ignore_every_future_bar[fib_golden_pocket_pullback]` | 7.5 s |
| `test_agents_audit_lock.py::test_concurrent_processes_produce_one_valid_chain` | 6.8 s |
| `test_engine_determinism.py` cross-process tests | about 6 s each |
| `test_no_lookahead.py` (7 tests) | about 2.2 s each |
| `test_layering.py` and `test_no_gateway_bypass.py` | about 2 s per test, re-parsing the tree each time |

**Signal generation microbenchmark** (`scratchpad/F_audit/bench2.py`). Setup: `donchian_breakout_atr` bound to its defaults, 20,000 synthetic H4 bars.

| Stage | Time |
|---|---|
| `generate_signal` per bar | **5.48 s, 274 µs per bar** |
| Engine replay of the resulting 2,356 signals | 1.76 s, 88 µs per bar |

Under cProfile, 8.0 s of 12.2 s is inside `EvalContext.read` (`strategy/primitives.py:351-364`), and most of that is `DataFrame.__getitem__` followed by `.iat` per operand per bar. Signal evaluation, not the engine, dominates. A ladder performs hundreds of evaluations per candidate (5 folds × sweep, CPCV paths, stress), so this is the hot path for every campaign and every agent-proposed candidate.

### 4.2 Hot loops and pandas anti-patterns

1. `EvalContext.read` does a `column not in self.frame.columns` check and a `self.frame[column].iat[i]` per operand per bar. `generate_signal` builds `df.iloc[: idx + 1]` per bar (`strategy/compiler.py:125`).
2. `BacktestEngine.run` builds a `BarContext` with `{sym: int(self._positions[sym][i]) ...}` per bar, and `_slice` builds 6 dicts per bar (`backtest/engine.py:880-922`). That is acceptable at 88 µs per bar, but it is the next bottleneck.
3. `agents/jobs.py` `_StrategyAdapter.on_bar` looks up `self._index[symbol].get(ctx.timestamp)` per bar (a dict keyed by `Timestamp`, fine) and then calls the slow `generate_signal`.
4. Datastore reads materialise string identity columns per row (P2-24).

### 4.3 Vectorisation plan without changing results

Compile each rule to a pure function of *column arrays* and produce a boolean mask over all bars once. Every rule is element-wise over causal columns with non-negative offsets (`strategy/primitives.py:286`, `318`: `offset ge=0`), so `mask[i]` depends only on rows `≤ i`. `generate_signal(df, idx)` becomes a lookup plus stop and target arithmetic at `idx`.

**Regression pin** (the pattern already exists in `tests/integration/test_locks_regression_pin.py`):

- (a) Record `ledger_sha256()` and `leg_ledger_sha256()` for the 5 seed documents × 3 instruments × 2 timeframes with the current scalar path, and commit them.
- (b) Add a hypothesis property: on random causal frames and random `idx`, the vectorised mask equals the scalar `rule.evaluate(ctx)`.
- (c) Keep the scalar path as the reference, and have CI run both on a sample.
- (d) The corrupt-the-future harness (`test_compiler_causality.py`) must pass unchanged.

Expected gain: 20x to 50x on signal generation (from about 274 µs to under 10 µs per bar). numba is **not needed** for this step. Avoid numba on the engine until the regression pin exists, because numba's `fastmath` and LLVM version would put another numerical dependency under the pin regime.

Other opportunities:

- Cache prepared indicator frames by `(dataset_version_id, indicator spec)` across sweep points. Many grid points share indicators, for example a sweep over the stop multiple.
- Run ladder folds in a process pool. Cores are plentiful on a Mac Studio; the counter-based RNG keeps results order-independent.
- Parse the source tree once per session in the AST tests.

### 4.4 Cost of running agents locally

GPU time and power, not dollars, are the budget (P2-22). The scheduler already reads CPU, load, memory and battery (`workers/scheduler.py`). Add GPU utilisation from `powermetrics` (sudo) or llama.cpp `/metrics` tokens per second. Cap agent work to off-market hours for H1 and faster strategies, so the paper trader's latency is not affected.

---

## 5. Test quality

### 5.1 Coverage (unit tests only; integration and api tests cover more)

Total: **75%** of 35,747 statements (`coverage report`, combined over the 4 chunks). Lowest:

| Module | Coverage | Note |
|---|---|---|
| `data/positioning/*`, `api/routers/{command,stream,incidents}.py`, `agents/evals/*`, `data/sources/*` | 0% | New and concurrent. Some are covered by `tests/api` or integration tests not in this run. |
| `api/routers/markets.py` | 8% | |
| `agents/jobs.py` | 17% | Where P1-2 lives. Covered further by `tests/integration/test_agents_jobs.py`. |
| `marketstate/state_engine.py` | 24% | |
| `agents/workflows.py` | 29% | |
| `marketstate/events.py` | 35% | |
| `cli.py` | 36% | |
| `workers/runtime.py` | 38% | RiskContextBuilder. Integration tests add more. |
| `data/store.py` | 39% | Integration covered |
| `workers/research_runtime.py` | 43% | |
| `broker/paper.py` | 48% | |
| `core/money.py` | 48% | `SeriesFxSource` is essentially untested because it is unused (P1-5) |
| `broker/execution_service.py` | 64% | `JsonlIntentStore._replay` torn-line path untested (P1-6) |

The core arithmetic is well covered: `risk/gateway.py` 91%, `backtest/engine.py` 85%, `sim/fills.py` 84%, `validation/ladder.py` 87%, `stats/sharpe.py` 94%, `portfolio/sizing.py` 91%.

### 5.2 Flaky-risk tests

- `time.sleep` in 4 files (`integration/test_worker_processes.py`, `integration/test_research_runtime.py`, `unit/test_worker_base.py`, `unit/test_obs_alerts.py`).
- Wall-clock calls in 6 files.
- `subprocess` in 6 files, all deliberately cross-process for the determinism, manifest, pins and sandbox tests.
- Threads in 4 fixture or test files.
- `test_agents_audit_lock.py::test_concurrent_processes_produce_one_valid_chain` (6.8 s, multi-process flock) is the most timing-sensitive.
- On macOS, `flock` semantics on network or iCloud-synced folders differ. Doctor should refuse a state directory under `~/Library/Mobile Documents` or `~/Documents` when iCloud "Desktop and Documents" sync is on. The desktop launcher's fallback root is `~/Documents/Claude/Projects/Fiboki` (`Start Fiboki.command:15`).

### 5.3 Missing property tests

Only `tests/property/test_dsl_properties.py` and `test_stats_properties.py` exist, plus scattered `hypothesis` use. Add:

1. **Sizing:** for any signal and account, `size × stop_distance × contract × fx ≤ equity × risk_fraction`, leverage is never exceeded, and rounding is down.
2. **Fills:** a gap never improves a stop, the target never exceeds its limit, costs are ≥ 0 on both legs, and the P&L identity holds.
3. **Gateway:** for any context with any `None` input, the decision is `allowed=False`. This is monotone in limits: tighter limits never allow more.
4. **Kill switch:** from any sequence of journal events, `allows()` agrees with the replayed state across two processes.
5. **Money:** `to_account_ccy(x, A→B) × rate(B→A) ≈ x`, and staleness raises.
6. **Vectorised rule masks** equal scalar evaluation (§4.3).
7. **Construction:** the final weight is ≤ 1 and never increases on a veto or conviction input.
8. **Holdout:** no sequence of `define`, `claim` and refresh grants two looks at overlapping spans to one structural family beyond its budget.

### 5.4 Missing golden tests

Golden tests exist for fills, P&L, indicator values, exits, CV, multiple testing and Sharpe. Add, with the arithmetic written in each docstring:

1. XAUUSD P&L in a GBP account with a time-varying GBPUSD.
2. Financing over a Friday-to-Monday hold and a Wednesday hold.
3. The DSR worked example from Bailey and López de Prado (2014), with N = 100 and 1,000 as quoted in `stats/sharpe.py:163-164`.
4. The leverage table for all 41 instruments (P1-1).
5. Risk-per-unit including costs (P1-16).
6. The blackout window arithmetic for an H1 entry 20 minutes before NFP (P2-1).

---

## 6. Developer experience and operations for the desktop

### 6.1 Install and setup friction (observed)

1. `python3 -m venv .venv` uses whatever `python3` is. Homebrew's current default is 3.13 or later, but `requires-python` is `>=3.11,<3.13`, so the install fails. **Use `uv` with a pinned 3.12** (`uv python install 3.12`, `uv venv -p 3.12`, `uv pip install -c deploy/constraints.txt -e ".[dev]"`).
2. The launcher installs without constraints (P2-19).
3. `deploy/requirements.lock` was recorded on Linux x86_64. On a Mac, `lock-check` will report platform-specific drift. Record one lock per platform (`requirements.darwin-arm64.lock`) and compare numerical pins across both.
4. Two supervision layouts coexist: `com.fiboki.*` and `uk.fiboki.*` (P2-17).
5. There are three state roots: `~/.fiboki` (state.db, the CLI kill switch, env), `<repo>/var` (API, audit, sessions, paper, datastore by default in dev-up) and `FIBOKI_DATA_ROOT`. Collapse them to **one `FIBOKI_HOME`**, with every path derived from it (B-30).

### 6.2 `fiboki doctor` specification

The existing `system doctor` checks (`cli.py:1067-1260`) are: python, pinned dependencies, state DB, data root, execution mode, alert channels, expected workers, heartbeats and lockfile. A concurrent agent is extending it (`tests/unit/test_cli_doctor.py`, mtime about 01:04Z). The target spec follows.

Each check returns PASS, WARN or FAIL, a measured value, and a one-line fix. `--json` prints machine output. The exit code is non-zero on any FAIL.

1. **Interpreter and architecture.** Python 3.11 or 3.12. `platform.machine()` is arm64 on Apple silicon, or x86_64 under Rosetta, which is a WARN because it changes numerics.
2. **Venv integrity.** `sys.prefix` is under the repo, and `.venv/bin/python` resolves (catches a moved repo, since venvs hold absolute paths).
3. **Pins and lock.** The numerical pins match, and `lockfile verify` passes for this platform.
4. **pip-audit summary.** Offline-tolerant, and a WARN only.
5. **Paths.** Every resolved path (home, state, data, experiments, holdout, killswitch, audit, intents, alerts, logs):
   - is absolute, exists and is writable;
   - is on the same volume;
   - is not inside iCloud or a network mount;
   - reports its free space.
6. **One kill switch.** The CLI, the API and the worker config resolve the same journal. Show its state and age.
7. **SQLite health.** `journal_mode=wal`, `busy_timeout` of at least 30 s, and `PRAGMA quick_check` on every database.
8. **Ledger integrity.** Audit `verify_chain`, intent-store replay with no torn tail, kill-switch replay, experiment triggers present, holdout triggers present.
9. **Durability.** On Darwin, whether `F_FULLFSYNC` is in use.
10. **Calendar.** Coverage start and end, days until `declared_end` (WARN under 30, FAIL at 0), and currencies missing for configured instruments.
11. **Data.** Marked root; latest bar age per configured instrument and timeframe; dirty datasets.
12. **FX.** A GBP conversion is available for every configured instrument (P1-5).
13. **Local model.** Backend reachable (`/v1/models`, `/props` or `/api/tags`), the loaded model equals the pinned id and digest, context ≥ the configured `num_ctx`, and a one-token smoke completes with its latency.
14. **Alerts.** Each configured channel has a transport. `--send-test` delivers a test alert (P1-9).
15. **Supervision.** Loaded launchd services; no duplicate labels for one role; `ProcessType`; last exit codes (`launchctl print`).
16. **Power.** `pmset -g` for sleep, disksleep and autorestart; whether a `caffeinate` assertion is held while services run.
17. **Clock.** NTP offset (`sntp -t 1 time.apple.com`) under 1 s, because the gateway's staleness checks depend on it.
18. **Secrets.** `~/.fiboki/env` is mode 600. No secret appears in `ps` output.
19. **Execution mode.** paper. The live controls are all off, and the reasons are listed.
20. **Backups.** Age of the last backup archive and whether its SHA256SUMS verify.

### 6.3 Log hygiene

- JSON logs exist (`obs/logging.py:189`).
- Add redaction of known secret names and `Bearer` tokens in the formatter.
- Rotation: launchd does not rotate, so use `newsyslog.d` entries or a size-rotating handler.
- Keep a correlation id on every agent call and order.
- Never log `OandaConfig` (P2-16).

### 6.4 Process supervision on macOS (launchd)

Proposed layout:

- One LaunchAgent per role: `uk.fiboki.{api,worker,paper,feed,news,llama,watchdog}`.
- `KeepAlive={SuccessfulExit=false}` and `ThrottleInterval=60`.
- `ExitTimeOut` greater than the shutdown grace.
- `ProcessType=Standard` for paper, feed and watchdog. `Background` is acceptable for research and news.
- `EnvironmentVariables` only for `FIBOKI_HOME`. Everything else comes from `~/.fiboki/env` via `fiboki-service.sh`.
- Exit 75 (lease held) should not respawn a duplicate. Drop the legacy plist. For a genuine lease conflict, alert rather than loop.
- A LaunchAgent stops at logout. For always-on operation, document auto-login or a LaunchDaemon with a dedicated user, and `pmset autorestart 1`.

### 6.5 Backup and restore of `var/`

`scripts/backup.sh` (new, concurrent) uses the SQLite online backup API plus SHA256SUMS, which is good. What remains:

- a `restore.sh` that verifies checksums, audit chains, intent replay and the catalogue checksums (`DataStore.verify_immutable`);
- a scheduled daily backup via launchd;
- a rehearsed restore recorded in `OPERATIONS.md` §9, which says none has been performed;
- exclusion of `~/.fiboki/env`, which is already done.

### 6.6 Upgrade path

- Tag releases.
- `fiboki upgrade` runs these steps, refusing on any failure:
  1. stop services;
  2. back up;
  3. `git pull --ff-only` onto a tag;
  4. `uv pip sync` from the platform lock;
  5. Alembic migrations, which do not exist yet (`ARCHITECTURE.md` §12);
  6. doctor;
  7. golden tests;
  8. start services.
- Any `ENGINE_VERSION`, `PROFILE_FINGERPRINT_VERSION` or gate-set bump prints the "stored results invalidated" list.

### 6.7 What breaks when the repository moves to another machine

| Item | Why it breaks | Fix |
|---|---|---|
| `.venv` | Absolute interpreter symlinks (here `/usr/bin/python3`) and console-script shebangs | Always recreate it; doctor check 2 |
| `apps/web/node_modules` | Platform binaries (SWC, esbuild) and Playwright browsers | `npm ci` on the target |
| Dataset catalogue | Absolute `storage_path` (P2-15) | Store paths relative to the root |
| launchd plists | `/Users/CHANGEME/...` placeholders | Generate them with `launchd-install.sh` from the resolved paths |
| Relative `FIBOKI_STATE_DIR=var` | State depends on the current directory | Derive it from `FIBOKI_HOME` |
| Kill-switch split (P0-1) | Worse after a move, because the defaults differ | One resolver |
| Lock recorded on Linux | Mac drift | Per-platform lock |
| `code_version` | Resolved from `git` by subprocess (`validation/report.py:100-111`); a copied tree without `.git` stores an unknown version | Require `FIBOKI_CODE_VERSION` or `.git` |
| Numerical reproducibility | x86_64 against arm64 BLAS and SIMD can differ in the last bits (not measured here) | Cross-platform CI job comparing ledger hashes |
| iCloud-synced `~/Documents` | Launcher fallback root; flock and SQLite on synced folders are unsafe | Refuse in doctor |

---

## 7. Proposed rule changes to AGENTS.md and the project instructions

The project instructions (claude.ai "Fiboki" project) describe V1. AGENTS.md is the V2 charter and should win. Each change below names the test that enforces it.

| # | Replace or add | With | Reason | Enforcing test |
|---|---|---|---|---|
| R-1 | "12 strategy bots under a common framework" | "Strategies are DSL documents under `research/strategies/` (5 seeds today). Adding one requires the `is_reparameterisation` check and a stated reason why it is a different bet." | V1 roster. AGENTS.md §2 forbids growing the roster for its own sake. | Existing `research/structure` checks; add a test that every committed document has `hypothesis.evidence_against` non-empty |
| R-2 | "Minimum 80 trades for primary ranking" | "Promotion requires the versioned gate set (`validation/gates.py`); ranking is on DSR, PBO, SPA/StepM, WFE, OOS hit and plateau, never on headline profit." | 80 is the V1 number that AGENTS.md cites as a defect | `test_validation_gates.py`; add a test that no API route sorts candidates by net profit |
| R-3 | "KLineChart for financial charts, Plotly for analytics" | "TradingView Lightweight Charts 5.x for price; uPlot plus owned SVG or canvas for analytics; no Plotly, no KLineChart." | Decision taken (COMMON.md, round 3) | A frontend dependency test failing if `plotly` or `klinecharts` is in `apps/web/package.json` |
| R-4 | "SQLite in dev / PostgreSQL in prod"; "Vercel / Railway / Render" | "Local-first on Joe's Mac: SQLite (WAL) plus parquet under one `FIBOKI_HOME`; services under launchd; no cloud dependency in research or execution." | ARCHITECTURE.md §1 and §11 | Doctor path checks; a test that `deploy/` has no Vercel or Railway config |
| R-5 | "fibokei_token cookie"; "SWR"; "60 canonical / 67 registered instruments" | "`fiboki_session` cookie (`api/settings.py:126`); 41 registered instruments (`core/instruments.py`)" | Factual drift. `FIBOKEI_*` names are refused in strict modes. | `test_api_settings_hygiene.py` |
| R-6 | "Phases 1-18 complete" | "Read `docs/v2/ROADMAP.md`; nothing is verified unless verified in this session." | AGENTS.md §0 | none (a process rule) |
| R-7 | (add) "One path resolver" | "Every persisted path (kill switch, intents, audit, holdout, experiments, state DB, data root) is resolved by one function from `FIBOKI_HOME`. No module, CLI option or script may default a path of its own." | P0-1, P2-15, §6.7 | AST test: no `Path("~/.fiboki/...")` or `Path("var")` literal outside the resolver |
| R-8 | (add) "A safety input may not default to a benign value" | "Every gateway input is `Optional`; `None` blocks in DEMO and LIVE." | P1-4 | AST test over `RiskContext` construction, plus a property test (§5.3.3) |
| R-9 | (add) "Durable means F_FULLFSYNC on Darwin, and torn tails are handled" | as stated | P1-6 | A unit test simulating a partial last line for each journal |
| R-10 | (add) "Trial counts come from the ledger" | "`n_trials` and `external_trial_count` are derived from the experiment ledger; any payload value is ignored or must be greater." | P1-2, invariant 24 | Test: a validation job with `n_trials_in_search=1` on a family of 50 uses N ≥ 50 |
| R-11 | (add) "Holdout looks are budgeted per structural family and defined on calendar span" | as stated | P1-7 | Property test (§5.3.8); holdout triggers present |
| R-12 | (add) "Engine inputs are UTC mid bars" | "The engine refuses non-UTC indices and non-mid `price_basis`." | P1-3, P3-4 | Unit test on `_validate_frame` |
| R-13 | (add) "Regulatory constants carry citations and golden tests" | Leverage and minimum-stop tables | P1-1 | Golden leverage table |
| R-14 | (add) "Every composition root is a reviewed file" | "Paper and live workers start only from a committed wiring file; the file is hashed into every attempt row." | P1-15; keeps "no live worker from CLI flags" | Test: the paper entrypoint refuses without the wiring file; its hash is on every attempt |
| R-15 | (add) "Local models only; every call pinned" | "The agent backend is llama.cpp or Ollama on loopback; model id plus GGUF or manifest digest plus sampler settings in every audit record; remote providers off unless named in the wiring file." | COMMON.md round 3 | Test: `_build_provider` refuses a non-loopback URL unless allow-listed; audit records carry a digest |
| R-16 | (add) "No scraped aggregator data enters a decision path" | Explicitly decide the status of `forexfactory_feed.py` against D-A4 | P3-10 | Test: `calendar_feed` output never merges into the official fixture |
| R-17 | Update the gateway count | "20 checks" (or say "the checks in `RiskGateway.CHECKS`" rather than a number) | Counts rot | none; avoid numbers in prose |

---

## 8. Prioritised improvement backlog

Waves: **W0** is hygiene (this week, under 1 day each). **W1** is safety and continuous-operation blockers. **W2** is research integrity. **W3** is realism and parity. **W4** is performance, operations and DX. Effort: S is under 1 day, M is 1 to 3 days, L is over 3 days. Risk means the risk of the change itself.

| # | Item | Value | Effort | Risk | Prerequisite | Wave |
|---|---|---|---|---|---|---|
| B-01 | Fix the 5 ruff findings in `scripts/` | CI lint green | S | Low | none | W0 |
| B-02 | Fix the 4 CI-mode mypy errors (`api/platform.py:553`, `workers/runtime.py:221`, `cli.py:1873`); update the pyproject note | CI typecheck green | S | Low | none | W0 |
| B-03 | Declare `sse-starlette` in `pyproject.toml`; re-record the lock | Clean install works | S | Low | none | W0 |
| B-04 | Dependency upgrade plan: starlette and fastapi (7 advisories), click (pinned for typer; upgrade typer too), orjson; separately pyarrow 18.1 to at least 23.0.1 under the pin regime with golden re-runs | CI audit green; security | M | Med (numerical pin: pyarrow) | golden suite | W0 and W3 |
| B-05 | Land the 10 concurrently broken unit tests (capability pin, roles, tool write domains, GDELT env, news sources) | Green suite | S | Low | concurrent owners | W0 |
| B-06 | Single path resolver from `FIBOKI_HOME`; CLI defaults removed | Kills the split-brain class | M | Med | none | W1 |
| B-07 | `KillSwitch.refresh()` on `allows()`; the gateway refuses an in-memory switch outside BACKTEST; cross-process test | P0-1 closed | M | Low | B-06 | W1 |
| B-08 | `durable_append` with `F_FULLFSYNC`, CRC framing and torn-tail quarantine for intents, kill switch, audit and alerts | Crash safety on Mac | M | Med | none | W1 |
| B-09 | `RiskContext` inputs `Optional`; `None` blocks in DEMO and LIVE; builder `market_open` required | P1-4 closed | M | Med | none | W1 |
| B-10 | Correct the FCA leverage table plus a golden test; bump `ENGINE_VERSION` | Regulatory correctness | S | Low | none | W1 |
| B-11 | Engine refuses non-mid and non-UTC frames; research bar source applies `bid_to_mid` with lineage | Removes directional bias | M | Med (invalidates results) | none | W1 |
| B-12 | Fix the `validation_handler` DSR variance and N source | Stops a flattering number | S | Low | B-14 for N | W1 |
| B-13 | SQLite job ledger for the orchestrator (WAL, idempotency key, fenced claims) | Continuous agents survive restarts | L | Med | none | W1 |
| B-14 | Derive `n_trials` and `external_trial_count` from the experiment ledger per family and campaign | Honest deflation, mechanically | M | Low | none | W2 |
| B-15 | Alert transports (httpx) for webhook and Telegram; outbox for CRITICAL; doctor `--send-test` | Alerts reach Joe | M | Low | none | W1 |
| B-16 | `fiboki watchdog run` plus a launchd plist; expected-workers list | Dead-worker detection | S | Low | B-15 | W1 |
| B-17 | Pulse around deterministic jobs; renew between jobs | No lease loss mid-job | S | Low | none | W1 |
| B-18 | Forward paper entrypoint (`OandaPollingBarFeed` → `LiveWorker` in PAPER) with a committed wiring file and plist | "The deterministic core trades" | L | Med | B-06, B-07, B-09, B-15 | W1 |
| B-19 | `_build_provider` backend switch (llama.cpp or Ollama); launcher writes declared env names; doctor model probe | Local LLM actually used | M | Low | concurrent `LlamaCppProvider` | W1 |
| B-20 | Holdout: calendar-span segments, structural-family budget, append-only triggers, single durable path | Holdout cannot be re-spent | L | Med | B-06 | W2 |
| B-21 | Pipeline power and size study E-1; publish `GATE_SET v2.1.0-calibrated` | Gates with a known FDR and power | L | Med | B-11, B-12, B-14 | W2 |
| B-22 | Replace the plateau ratio with scale-free conditions | Fewer spurious fails and passes | S | Low | B-21 | W2 |
| B-23 | WFE on log-growth or Sharpe; per-fold OOS trade floor; 8 folds | Unbiased transfer measure | M | Low | B-21 | W2 |
| B-24 | Wire portfolio construction into paper and the backtest (same code); cap vol scale at 1.0; property test | Regime, drawdown and correlation controls live, with parity | L | Med | B-09 | W3 |
| B-25 | `SeriesFxSource` from the stored FX store; research in GBP | Research and paper comparable | M | Med (restates results) | data for GBP crosses | W3 |
| B-26 | Sizing policy v2: risk-per-unit includes spread and expected slippage | True 1% risk | S | Med (sizes change) | none | W3 |
| B-27 | Event blackout pre 30 / post 15 plus the next bar; replay `now` = bar close; new LimitSet version | No entries into releases | S | Low | E-3 | W3 |
| B-28 | Calendar refresh job; doctor expiry warning; 2010-2023 backfill; AUD, CAD, CHF and NZD plus more US data | Parity and no coverage cliff | L | Low | none | W3 |
| B-29 | DSL `session_window` with tz and decision-time semantics (new schema version); FX session calendar anchored to 17:00 New York | DST-correct sessions | M | Med (hash move) | holdout key-version handling | W3 |
| B-30 | Measured `HourOfWeekSpread` per instrument from OANDA practice quotes (E-4) | Realistic costs | M | Low | quote recorder running 4 weeks | W3 |
| B-31 | Financing from rate history per instrument; business-day rollover with triple day; remove or relabel API `annual_financing_bps` | Realistic carry; honest display | M | Med | macro rate data | W3 |
| B-32 | Per-asset-class minimum stop; validation against the target venue's profile (OANDA) | Venue parity | S | Low | B-30 | W3 |
| B-33 | Sharpe on daily-resampled equity plus Lo adjustment; report both | Comparable Sharpe across timeframes | S | Med (metric definition change) | none | W2 |
| B-34 | `margin_utilisation` includes the plan's margin; regime per plan instrument; `abnormal_spread` from the live spread in DEMO | Gateway correctness | S | Low | none | W1 |
| B-35 | SQLite `connect` listener (WAL, `busy_timeout`, `synchronous`) for every store | No lock errors under agents | S | Low | none | W1 |
| B-36 | Catalogue paths relative to the data root, with migration | Portable data | M | Med | backup | W4 |
| B-37 | Secrets: `repr=False` and SecretStr for tokens; scrypt password hashes; no default password; log redaction | Security hygiene | S | Low | none | W1 |
| B-38 | AST tests: no `compiled_in=` in src; no worker start in `api/`; `LEDGER_COLUMNS` has no UUID; no `rng=None` calls; no path literals | Closes invariant gaps 12, 20, 27 and P3-2 | S | Low | none | W0 |
| B-39 | Vectorised rule masks with regression pin and property test (§4.3) | 20x to 50x faster campaigns | M | Med | pin recorded first | W4 |
| B-40 | Indicator-frame cache across sweep points; process-pool folds | Faster ladder | M | Low | B-39 | W4 |
| B-41 | Session-cached AST parse in the structural tests; mark the slow mutation tests `slow` | Faster suite | S | Low | none | W4 |
| B-42 | Datastore categorical identity columns (checksum pinned) | Lower memory | S | Low | checksum test | W4 |
| B-43 | Full doctor spec (§6.2) | Operable desktop | M | Low | B-06 | W4 |
| B-44 | launchd consolidation: remove the legacy plist, `ProcessType` per role, caffeinate or power assertion, newsyslog rotation | Continuous running | S | Low | B-18 | W4 |
| B-45 | `uv` plus Python 3.12 bootstrap; per-platform lock; constraints in the launcher | Reproducible install | S | Low | none | W4 |
| B-46 | `restore.sh` with verification; a daily backup plist; a rehearsed restore logged | Recoverability | M | Low | backup.sh | W4 |
| B-47 | Cross-platform determinism CI job (macOS arm64 against Linux) comparing ledger hashes | Mac results comparable | M | Low | CI runner | W4 |
| B-48 | Agent budgets in tokens and seconds; daily GPU-seconds cap in the scheduler; per-role context sizes from measured prompts | Bounded local compute | M | Low | B-19 | W4 |
| B-49 | Split `cli.py` and `agents/tools.py` by command group and tool; reduce the complexity hotspots (`_research_cycle` CC 48) | Maintainability | L | Med | tests stable | W4 |
| B-50 | Property tests (§5.3, 8 families) and golden tests (§5.4, 6 cases) | Catch regressions in money code | M | Low | none | W2 |
| B-51 | New gates: bootstrap MaxDD, correlation with the book, regime coverage | Tail and diversification discipline | M | Low | B-21 | W2 |
| B-52 | Update the project instructions and AGENTS.md per §7 (R-1 to R-17) | Stops V1-era rules steering agents | S | Low | none | W0 |

A realistic first tranche is W0 plus the W1 items B-06 to B-19 and B-34, B-35 and B-37: about 12 to 15 engineer-days. That closes P0-1 and every P1 except the research-integrity items (P1-7, P1-11 and the gate study), which are W2 and W3 by nature.

---

## 9. Facts and assumptions

### Facts: run or read in this session

- **ruff 0.8.4:** `ruff check src/ tests/` reported "All checks passed!". `ruff check src/ tests/ scripts/` reported "Found 5 errors", with locations as above.
- **mypy 1.14.0** (installed in the venv):
  - `--ignore-missing-imports` reported "Found 19 errors in 8 files (checked 168 source files)".
  - `--ignore-missing-imports --no-strict-optional` (the CI invocation) reported "Found 4 errors in 3 files".
- **Test collection:** `pytest -q --co` reported "3946 tests collected in 15.62s" at about 00:20Z. `pytest -m golden --co` reported "86/4180 tests collected" at about 01:05Z.
- **Unit tests with coverage** (4 chunks via `scratchpad/F_audit/runcov.sh`): 3,223 passed, 10 failed, 21 skipped. Combined coverage is 75% of 35,747 statements. The failures are named in P1-13.
- **radon 6.0.1:** installed into the scratchpad with `pip install --target` and run from there. I did not install it into the shared venv, deliberately, so that concurrent agents' lockfile checks would not see a foreign package. Nothing needs removing from the venv.
  - `radon cc -a` gave "4165 blocks, average complexity A (3.25)", with 26 blocks at grade D and 4 at E or F.
  - `radon mi` gave `agents/tools.py` and `cli.py` grade C (0.00).
- **pip list --outdated:** 23 packages outdated, including numpy 2.2.6 (latest 2.4.6), pandas 2.2.3 (3.0.6), pyarrow 18.1.0 (25.0.1), scipy 1.14.1 (1.17.1), fastapi 0.115.6 (0.141.1) and starlette 0.41.3 (1.7.0).
- **pip-audit 2.7.3:** run from the scratchpad against `pip freeze` with `--no-deps`. It reported "Found 11 known vulnerabilities in 5 packages".
- **Lockfile:** `scripts/lockfile.py verify` exited 1, reporting "3 version change(s); 1 unexpected".
- **Microbenchmark** (`scratchpad/F_audit/bench2.py`): signal generation took 5.48 s (274 µs per bar); engine replay took 1.76 s (88 µs per bar); 20,000 synthetic H4 bars. Profile as quoted in §4.1.
- **Calendar fixture** (read with Python): 339 events; currencies GBP 165, USD 102, EUR 40, JPY 32; `declared_end` 2026-12-04T13:30:00Z.
- **Instruments:** 41 registered (`all_symbols()`). There are 5 strategy JSON documents in `research/strategies`.
- **Code reads:** every file:line cited above was read in this session. Some files changed during the session. The gateway check count went from 19 to 20, and the provider module gained `LlamaCppProvider`. Citations reflect the version read last.

### Assumptions and judgements: not verified here

- **Regulatory leverage classes** (ESMA 2018/796 and FCA PS19/18 major pairs and major indices) are stated from knowledge of the published texts, not fetched in this session. Verify against the FCA COBS 22.5 text before merging B-10.
- **The macOS `fsync` versus `F_FULLFSYNC` behaviour** is from Apple's `fsync(2)` man page, from knowledge. It was not demonstrated with a power-cut test.
- **The DSR inflation magnitude** in P1-2 is an order-of-magnitude calculation (per-trade return sd of about 0.01 at 1% risk, T = 400, N = 1,000). The handler was not run on a real record.
- **P1-3 bias direction** is argued from the fill-model conventions. The size of the effect on stored results was not measured. Measure it with a before and after on one HistData instrument.
- **MinTRL figures** use the Bailey and López de Prado (2012) formula with γ3 = 0 and γ4 = 3. Real skew and kurtosis will change them.
- **Spread, financing and minimum-stop venue values** (IG and OANDA) are not verified against current venue schedules. That is why the recommendations are measurement experiments (E-3, E-4) rather than new constants.
- **Cross-platform last-bit numeric differences** between x86_64 and arm64 are a known class of risk, not observed here.
- **Concurrent-work status.** Where a gap is being addressed by another agent (llama.cpp provider, doctor, backup, launchd, event veto), I read the state at the times given. I did not verify the final form.
- **Literature cited:**
  - Bailey and López de Prado (2012) "The Sharpe Ratio Efficient Frontier", *J. Risk*.
  - Bailey and López de Prado (2014) "The Deflated Sharpe Ratio", *J. Portfolio Management*.
  - Bailey, Borwein, López de Prado and Zhu (2017) "The Probability of Backtest Overfitting", *J. Computational Finance*.
  - Hansen (2005) "A Test for Superior Predictive Ability", *JBES*.
  - Romano and Wolf (2005) "Stepwise Multiple Testing as Formalized Data Snooping", *Econometrica*.
  - White (2000) "A Reality Check for Data Snooping", *Econometrica*.
  - Lo (2002) "The Statistics of Sharpe Ratios", *FAJ*.
  - Pardo (2008) *The Evaluation and Optimization of Trading Strategies*.
  - López de Prado (2018) *Advances in Financial Machine Learning* (CPCV, embargo).
  - Harvey and Liu (2015) "Backtesting", *JPM*.
  - Neely, Weller and Ulrich (2009) "The Adaptive Markets Hypothesis: Evidence from the Foreign Exchange Market", *JFQA*.
  - Hsu, Taylor and Wang (2016) "Technical trading: Is it still beating the foreign exchange market?", *JIE*.

  All are cited from knowledge; none were retrieved in this session.
