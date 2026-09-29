# Build Log

A dated, factual record of the Fiboki V2 rebuild. Numbers here were measured at the stated time
and are not updated retrospectively; where a later measurement supersedes an earlier one, both
are shown.

---

## 2026-09-19 — the V1 audit

`FIBOKI_V2_AUDIT_AND_OVERHAUL.md`, against V1 commit `ccc3af2` plus six uncommitted working-tree
changes. Five auditors working in parallel on the quant core, the execution/risk/security layer,
the frontend, engineering discipline, and external broker and statistical research. Every
material number either measured in that session or carrying a file:line or URL citation.

Its verdict, in one sentence: Fiboki V1 is substantial engineering that **cannot tell you whether
any of its strategies make money**, and must not be pointed at real capital.

Three problems compounding in the same direction — towards flattering results. The measurement
layer overstated performance (entry-only spread, no commission, no financing, exact-level fills
through gaps, `sqrt(252)` annualisation, realised-only drawdown). The selection layer had no
defence against noise (23,040 trials, zero multiple-testing correction, an "out-of-sample"
window that had already driven selection, a walk-forward that fitted nothing). And the safety
layer was not what the documentation believed (a risk engine with zero call sites, a
single-environment-variable live bypass defeated by a trailing slash, and
`FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` committed in `render.yaml`).

Its recommendation was a disciplined refactor rather than a rebuild. The decision taken
afterwards was a rebuild, on the grounds that the defects the audit found were structural —
one-position-at-a-time in the engine, a separately-written paper path, unversioned data — and
that each of them is cheaper to design out than to patch around. The audit's findings, thresholds
and statistical protocol were carried across intact.

Full extraction: `V1_FORENSIC_BASELINE.md`.

## 2026-09-19 — V2, commit `28d9ae1` — foundation

`v2/a-foundation: scaffold, exact pins, core enums/instruments/money/contracts`.

Established before any logic was written, because each one closes an audit finding that could not
be closed later:

- **Exact dependency pins.** numpy 2.2.6, pandas 2.2.3, scipy 1.14.1, pyarrow 18.1.0 and the rest,
  including the build backend, with the reason written above the block. V1's `>=` ranges resolved
  to a major version beyond the author's intent on every machine tested, which meant
  "deterministic backtests" was not a property the repository had.
- **Persisted enums**, with the rule that a member's *value* is never renamed.
  `ExecutionMode` ordered by increasing danger; `Provenance` with eight members ordered by
  trustworthiness.
- **Instrument specs as market facts**, with FCA/ESMA retail leverage caps, and a registry that
  **refuses to guess** — an unknown symbol raises, because guessing is how V1 produced wrong
  position sizes.
- **Mandatory, explicit FX conversion.** `IdentityFxSource` raises on a currency mismatch unless
  the approximation was deliberately recorded. V1 returned 1.0 for everything except JPY and
  mis-stated every USD-quoted result on a GBP account by 1.20–1.43.
- **The layering contract.** A `Signal` carries no size; a `TradePlan` carries exactly one,
  decided once; an `Order` cannot exist without an idempotency key.

## 2026-09-19 — V2, commit `e3dd234` — data, simulator, strategy, statistics

`v2/c-data + v2/d-simulator + v2/e-strategy + v2/f-validation-stats`.

**Data.** The canonical schema with `price_basis` as a required column; content-addressed
versioning; an explicit marked root; validate/repair split; resampling with a declared origin;
session calendars; the CRC-framed recorder and telemetry; three providers.

**During this work the V1 data defects were discovered.** They are the finding this rebuild adds
to the audit, because they are independent of every engine bug: HistData timestamps are EST with
no daylight saving, stamped as UTC and therefore **five hours out year-round**; the prices are
**bid**, treated as mid and then additionally charged a modelled half-spread; volume is
**identically zero**; and EURUSD H1 contains a **negative-price sentinel bar** (OHLC all
`-0.0001`, at 2001-09-11 20:00 EST) that poisons every log return and every expanding percentile
computed across it. `detect_timestamp_convention` was written to make the first of those
empirically checkable — a genuinely-UTC FX series shows its weekly open drifting between 21:00
and 22:00 UTC with US DST, while these files show a rock-solid 17:00 boundary all year.

**Conclusion recorded then and unchanged: every V1 research result is stale for data reasons
alone. V1 rankings are not trustworthy and must be recomputed.**

**Simulator.** Costs on both legs; the single `_worse_exit` rule producing every gap behaviour;
`IntrabarPolicy` as a named, measurable assumption rather than an accident of `if` ordering; five
fingerprinted profiles; counter-based RNG derivation so a portfolio's fills do not depend on how
many instruments are in it.

**Strategy.** The DSL, with a mandatory hypothesis, a mandatory stop, a *list* of take-profit
legs, explicit parameter domains, derived warmup and derived complexity. Twenty causal
indicators with a registry-generated causality suite, so an indicator without a proof cannot be
added. Five seed documents, each stating the published evidence **against** itself — the Ichimoku
one carrying Deng, Sakurai and Ueda's negative FX result and describing itself as a baseline: *if
the research pipeline cannot show this underperforming, the pipeline is broken.*

**Statistics.** DSR/PSR/MinTRL, PBO via CSCV, SPA/RC/StepM, purged and combinatorial purged CV,
block bootstrap with automatic block length, effective trial count by clustering, seven stress
curves, parameter plateau analysis. Each carries its source citation and, where a folk version
of the idea is wrong, a paragraph saying why — most sharply in `effective_trials_by_clustering`,
which rejects the naive equicorrelation adjustment because at 23,040 trials it returns
`N_eff = 3` and would halve the false-discovery threshold.

## 2026-09-19 — V2, commit `46e815f` — validation, agents, market state, portfolio and risk

`v2/f-validation + v2/g-agents + v2/h-market-intelligence + v2/i-portfolio-risk`.

**Validation.** The seven-rung ladder; the nine-gate versioned set with the audit's thresholds
(400 trades, WFE ≥ 50%, OOS hit rate ≥ 0.60, DSR > 0.95, PBO < 0.20, SPA p < 0.05, StepM
membership, profit at 2× spread, plateau ratio ≤ 1.25); the holdout registry that claims before
evaluating and refuses a second look keyed on the content hash; a `ValidationReport` produced for
rejected candidates that names the binding constraint and round-trips byte-identically.

**Agents.** Twelve roles, twenty-five tools, twenty capabilities containing none that can reach
execution, an import-time guard, an AST test, an enumerated write surface, a job queue that
cannot express an execution job, a single enforcement point, and a hash-chained ledger.

**Market state.** Causal features with expanding-only percentiles; a five-axis regime vector with
dwell confirmation; a cross-asset panel that inner-joins and reports coverage rather than
forward-filling; `regime_dependence`, which makes the audit's sharpest finding mechanical — six
of fourteen V1 survivors were USDJPY during one exceptional trend, and that is a finding about a
regime, not a strategy.

**Portfolio and risk.** One sizing authority; a thirteen-step construction pipeline recording
every scaling factor; an eighteen-check mandatory gateway that fails closed and verifies its own
coverage; a kill switch whose semantics are chosen at activation; versioned fingerprinted limit
sets; an execution service writing a fsynced intent before dispatch with UNKNOWN as a first-class
state; a paper broker driving the backtester's own fill model with byte-identical parity.

Measured at 04:38Z on 2026-09-19: **2290 passed, 2 skipped** in 123.5 s; 90 source files /
39,786 lines; 55 test files / 23,238 lines.

## 2026-09-19, 04:38Z–05:05Z — documentation, written against a moving tree

This documentation pass ran concurrently with the final build phase. That is recorded because it
affects how the numbers in these documents should be read.

At 04:38Z the tree contained 90 source files and `src/fiboki/{api,obs,workers}` were empty or
absent; `pyproject.toml` declared a `fiboki = "fiboki.cli:app"` entry point for a `cli.py` that
did not exist. Over the following half hour the API package, the observability package, the
worker processes, the CLI, `deploy/`, `scripts/` and `.github/workflows/ci.yml` all landed. One
intermediate test run at 04:52Z showed **6 failures** in
`tests/integration/test_validation_ladder_real_engine.py`; re-running that file alone immediately
afterwards gave 11 passed, so the failures were an artefact of collecting a file mid-write, not a
defect.

Three claims made earlier in the pass were invalidated by work that landed during it, and have
been corrected: there **is** now a dependency lockfile (`deploy/requirements.lock`) and a
constraints file; there **is** a parameter binder (`StrategyDocument.bind`); and the `api/`,
`obs/` and `workers/` packages **are** now covered by tests.

### Final verification, 2026-09-19T05:05Z

```
/home/claude/v2/.venv/bin/python -m pytest tests/ -q
2683 passed, 2 skipped, 1 warning in 287.26s (0:04:47)
```

| Measure | Value |
|---|---|
| Source files / lines | 125 / 51,937 |
| Test files / lines | 102 / 28,716 |
| Tests passed / skipped | **2683 / 2** |
| `ruff check src tests scripts` | **101 findings** |
| Instruments registered | 41 |
| Indicators registered | 20, all causality-proved |
| Agent roles / tools / capabilities | 12 / 25 / 20 |
| Promotion gates | 9, version `v2.0.0-audit` |
| Seed strategy documents | 5 |
| Uncommitted or untracked paths | 157 |
| Commits | 3 |

The two skips are legitimate and named: `spread_rel` and `spread_pct` are unavailable on a
dataset with no bid/ask, which is the market-state package publishing absence as absence rather
than as a plausible zero.

**The tree was still moving when this was written.** A second full run thirteen minutes later,
at 05:18Z, gave **2691 passed, 2 skipped** and ruff had fallen from 101 findings to 57. Both
measurements are real and neither is wrong; they are recorded as a pair because a single number
would imply a stability the repository did not have at the time. Every figure quoted across
`docs/v2/` is the 05:05Z one unless it says otherwise, and any reader should re-run the suite
rather than trust either.

### Numbers computed in this session, not quoted

Each is reproducible from the repository:

- expected spurious survivors at N = 23,040, α = 0.05: **1,152**
- Bonferroni one-sided critical t: **4.5944**; required per-trade Sharpe at 80 / 400 / 1,000
  trades: **0.514 / 0.230 / 0.145**
- `expected_max_sharpe` at sd(SR) = 0.5: N = 100 → **1.265**, N = 1,000 → **1.628**,
  N = 23,040 → **2.030** (the first two reproduce the published worked values)
- `C(16,8)` = **12,870** CSCV combinations; `C(6,2)` = 15 splits → **5** backtest paths
- `minimum_track_record_length` at Gaussian moments, 95% confidence: **5.1 years** to separate an
  observed Sharpe of 1.0 from zero, **17.2 years** to separate it from 0.5

That last pair is a **discrepancy with the V1 audit**, which quoted 2.8 and 11.2 years. The
implemented values do not reproduce the audit's under any moment set tested; they are more
demanding, which is the safe direction, and the discrepancy is recorded in `ROADMAP.md` §5 rather
than reconciled by preferring whichever number reads better.

## What this rebuild changed, in one table

| V1 defect | V2 mechanism | Proved by |
|---|---|---|
| Spread charged on entry only | `LegCosts` on every leg; no path opens or closes without one | `test_both_leg_cost_impact` |
| Exact-level fills through gaps | one `_worse_exit` rule | `test_golden_fills` |
| Ambiguous bars resolved optimistically and silently | `IntrabarPolicy`, `STOP_FIRST` default | `test_golden_fills` |
| `sqrt(252)` regardless of frequency | annualisation from the measured elapsed span | `test_metrics_honesty` |
| NaN mapped to the metric cap | `DegenerateMetricError`, `strict=True` default | `test_metrics_honesty` |
| Realised-only drawdown | equity marked to market every bar | `test_engine_behaviour` |
| One position at a time | multi-position engine with concurrency caps | `test_engine_behaviour` |
| Cross-currency conversion a no-op | mandatory `FxRateSource`; engine refuses to construct without one | `test_golden_pnl` |
| Centred swing window, `shift(-26)` chikou | every indicator causality-proved by a registry-generated suite | `test_indicator_causality` |
| Selection and validation shared a window | holdout registry, claim-before-evaluate, `assert_untouched` | `test_holdout_registry` |
| Walk-forward that fitted nothing | parameters selected on train are the ones run on test | `test_walk_forward_transfers` |
| No multiple-testing correction | DSR, PBO, SPA, StepM as hard gates; clustered effective N | `test_stats_promotion_gate` |
| Frozen-size Monte Carlo | `resample_with_compounding`, with the bias measured both ways | `test_bootstrap` |
| Risk engine with zero call sites | `Order` constructed in exactly one function, downstream of the gateway | `test_no_gateway_bypass` |
| Three sizing authorities | `size_trade` only; adapters may not size | `test_sizing_authority` |
| Live gate defeated by a trailing slash | parsed-hostname assertion, twice | `test_mode_guard`, `test_oanda_adapter` |
| Live enabled by one committed string | five independent controls, two of them source constants | `test_mode_guard` |
| No order idempotency; orphaned positions | fsynced PENDING intent before dispatch; deterministic `client_ref` | `test_execution_lifecycle` |
| Unconfirmed order treated as rejected | `UNKNOWN`, non-terminal, blocks resubmission | `test_execution_lifecycle` |
| Reconciliation on mismatched key spaces | keyed on the broker reference | `test_execution_lifecycle` |
| Paper reimplemented the backtester | paper drives `FillSimulator` directly | `test_paper_backtest_parity` |
| Kill switch abandoned open positions | `PAUSE` vs `FLATTEN` chosen at activation | `test_killswitch` |
| Worker as a daemon thread in the API | worker processes with a single-writer lease and real exit codes | `test_worker_processes` |
| No `worker_down` event; heartbeat checked on page load | 17 failure events; `HeartbeatWatchdog` on a timer | `test_obs_alerts` |
| Hardcoded `{"status":"ok"}` health | four measured checks, worst component wins | `test_obs_health`, `tests/api/test_health` |
| f-string "JSON" logging | `json.dumps` over a dict; no concatenation in the path | `test_obs_logging` |
| No RBAC, no CSRF, no rate limiting | ranked roles, exact-Origin + double-submit CSRF, login limiter | `tests/api/test_security` |
| Unpinned dependencies | exact pins + constraints + verified lockfile | CI `lockfile` job |
| Nothing gated a deploy | `deploy` needs `gate`; tag or dispatch only; never a push | `ci.yml` |
| A committed live flag nobody re-read | `check_live_flags.py`, self-tested against a planted copy of the exact line | CI `live-flags` job |
| `no_data` recorded as DONE | `CellStatus.NO_DATA` retried; excess missing data is a failure | `test_research_worker` |
| Data root found by walking up the tree | explicit, marked root; a miss raises | `test_data_store` |
| Silent repair | `validate` is pure; `repair` needs an actor and a reason and mints a version | `test_no_silent_repair` |
| No dataset versioning | content-addressed ids with lineage | `test_data_versioning` |
| Bid treated as mid, EST stamped as UTC | `price_basis` a required column; `+5h` a declared adjustment | `test_data_providers`, `test_migrate_v1` |
| No golden indicator values | hand-calculated constants, separately required in CI | `tests/golden/` |
| Editable research history | SQLite triggers on UPDATE and DELETE | `test_experiment_ledger` |

## Open at the close of this session

`ROADMAP.md` is authoritative. In one line each: **commit the 157 untracked paths**; fix the
**101 ruff findings** so the gate is green; open an OANDA practice account and **start the quote
recorder the same day**; run the V1 migration against the real store and take one seed strategy
through the ladder end to end, **expecting it to fail**; write the application entrypoint that
turns the unwired components into running processes; and write the three one-file gaps — the
import-direction layering test, a backtest regression pin, and an Alembic baseline.

Nothing in Fiboki V2 has placed an order, and no strategy has been shown to make money. That is
the correct state for a platform at this stage, and it is the state the documentation says it is
in.

## 2026-09-28: agent-layer and settings hardening (uncommitted working tree)

Four bounded items, measured in the working tree on `v2/integration` before commit.

- **Audit ledger inter-process safety.** `JsonlAuditLedger.append` extended an in-memory chain
  with no lock and no check that the file still ended at its cached tail, so the API and the
  research worker writing one file could fork the hash chain. It now holds an exclusive `flock`
  on `<path>.lock` across read-tail, append and fsync, adopts (after verifying) records other
  writers appended, refuses a stale or forked view with `AuditChainForkError`, fsyncs the
  directory on first create, and refuses to extend a chain that failed verification on
  `reload()`. Measured: four spawned writers × 25 records gave 100 records in one valid chain;
  the same run with the lock patched out gave 45 records and a broken chain.
- **Pinned agent clock.** `ToolContext.as_of` (UTC-aware; naive refused). When set,
  `query_market_data`, `query_regime` and `query_data_quality` see only candles closed by it,
  later model-supplied dates are clamped with `as_of_clamped: true` and `effective_as_of` in the
  output, absent dates default to it, and `query_execution_telemetry` hides later records.
  `None` leaves behaviour unchanged. Job submissions are not clamped (recorded as a gap in
  `AI_AGENT_ARCHITECTURE.md` §10).
- **Settings hygiene.** Booleans parse strictly (`FIBOKI_COOKIE_SECURE=ture` is now a startup
  error, not a silent `False`); malformed numbers name the variable; `ENV_REGISTRY` declares
  every `FIBOKI_*` variable the platform reads, including the ones other modules read directly;
  `warn_unknown_env` lists unknown `FIBOKI_*`/`FIBOKEI_*` names, a startup error in DEMO and LIVE
  and a once-per-process warning otherwise. No default changed. An AST test fails if a module
  starts reading an undeclared `FIBOKI_*` name; on its first run it found `FIBOKI_PAPER_ROOT`
  (`api/platform.py`), which is now declared.
- **Documentation truth.** `AI_AGENT_ARCHITECTURE.md` said the `Capability` enum had 20 members;
  the code has 19. Module count (11, not 12) and the research-store location corrected; tool,
  role, write-domain and job-type counts re-checked and unchanged.

No stored backtest, validation or research result is affected: nothing here touches the engine,
cost model, metrics or gates.

## 2026-09-28: operator API truth for worker health and paper trading (uncommitted working tree)

- **False-DOWN heartbeat.** `Platform.worker_heartbeat_age_seconds` returned the age of the
  worker store's file mtime. The store is SQLite in WAL mode, so beats land in `state.db-wal`
  and the main file's mtime stays old. Reproduced in a test: a `Heartbeat.write` one hour after
  the main file's mtime was set back left the mtime 3,600 s old with `beat_at` fresh. The API
  now opens the store read-only, reads every `worker_heartbeat` row, ages the newest `beat_at`
  on its own clock, and reports `absent` / `stale` (age at or above threshold) / `ok` with a
  reason (`file_missing`, `no_heartbeat_table`, `no_rows`, `unreadable`, `sqlite`, or a labelled
  `mtime_fallback` for a non-SQLite path). `/api/system/workers` lists one row per worker.
- **Paper journal instead of the seed.** New `api/paper_journal.py` reads persisted sessions
  under `FIBOKI_PAPER_ROOT` (default `<state_dir>/paper`). With a journal, trades, positions,
  portfolio, exposure and risk are served from it as PAPER, in the session currency, with the
  replay's last bar as `as_of`. Against the two committed XAUUSD H4 replays
  (`research/reports/paper_sessions`, copied to `tests/fixtures/paper_journal`) the API serves
  283 closed trades (5 + 278), 1 open position, balance 182,618.54 USD on 200,000 USD across two
  independent accounts. `var/paper` does not exist in this mirror, so a default local start
  here still serves the seed.
- **Seed never PAPER.** Seed rows that were PAPER or SHADOW are BACKTEST now (same buckets, so
  research rows are unchanged); seed positions are BACKTEST. No `SEED` member was added to
  `Provenance`: the frontend enumerates eight values and `api/provenance.py` keys trust on
  them. The fixture label is the envelope's `SourceNote` (`kind: "seed"`) plus a `seed_fixture`
  caveat. With no journal the account, drawdown and daily-loss figures are `null`, not a 25,000
  balance.

No stored backtest, validation or research result is affected. Any screenshot or note taken
from the workstation's Portfolio, Risk or Exposure pages before this change showed fixture
numbers under a PAPER label and should be discarded.

## 2026-09-28: dated official economic calendar and the fail-open guard (uncommitted working tree)

- **What was wrong.** `marketstate/calendar.py` shipped no dated events, so every blackout query
  returned False and every declared event restriction in the five seed documents was inert.
- **Fixture.** `marketstate/fixtures/scheduled_events_official.json`: 339 scheduled events from
  official publishers only (Fed 32, ECB 40, BoE 32, BoJ 32, BLS NFP 35, BLS CPI 35, ONS CPI 37,
  ONS monthly GDP 48, ONS labour market 48), 2024-01-01 onwards, declared complete to
  2026-12-04 13:30Z. No aggregator was used. The header lists every URL, the time convention per
  source and what was skipped.
- **Calendar.** `EconomicEvent` gained optional `source_url`, `retrieved_at`, `time_known`,
  `window_end` and `tags` (old JSON/CSV loads unchanged; `event_type` is read as an alias of
  `recurring_key`). Blackout queries honour `window_end` (BoJ). Coverage can carry a declared
  span; `assert_populated` takes `currencies`. New `load_official_calendar`,
  `official_calendar_manifest`, `events_near_currencies`. `USER_ACTION_NOTE` rewritten to the
  remaining gap.
- **CLI.** `fiboki calendar status [--start --end] [--json]`, `fiboki calendar check <ISO-UTC> <CCY>`.
- **Guards.** `run_validation(calendar=..., allow_empty_calendar=False)` refuses a supplied
  calendar that is empty, does not span the bars or lacks the instrument's currencies, passes it
  to the engine as the blackout source, and segregates the evaluation cache by calendar digest
  (the cache key does not include the blackout source). `calendar=None` with a declared blackout
  logs a WARNING only. `scripts/run_paper_session.py` refuses an uncovered replay unless
  `--allow-empty-calendar`, and records the guard in `summary.json`.
- **Gaps left open.** The paper runtime does not feed the calendar to the risk gateway's
  `event_blackout` check; `discovery.campaign.run_cell` passes no calendar; the calendar covers
  only 2024 onwards and four currencies.

Stored results: no stored backtest or validation result changes, because no existing caller
supplies a calendar. Any future run that does supply one will differ from its no-calendar
predecessor for the seed documents (all five declare 15 to 45 minute blackouts); compare them as
different experiments, not as a regression.

## 2026-09-28: live-feed plumbing, startup reconcile, read retries (uncommitted working tree)

- **What was wrong.** Nothing implemented `BarFeed` for a venue. `LiveWorker.resume` reconciled
  but only logged a CRITICAL line on a divergent report and then traded; periodic reconciliation
  was cycle-counted only (twelve H4 cycles is two days) and treated a report carrying only
  `errors` (`venue_unreachable`) as clean. `OandaAdapter` raised `BrokerUnavailable` on the first
  429/5xx/transport failure for reads as well as writes.
- **Feed.** New `workers/feeds.py`: `OandaPollingBarFeed` (boundary + 5 s offset, bounded
  30 s waits, venue `complete` flag authoritative, 3 re-polls 5 s apart, `DATA_QUALITY_DEFECT`
  after a 20 s grace, never gap-fills, session calendar decides closed-vs-stale),
  `TransportHttpClient` (lets the candle provider speak through a broker `Transport`, so
  `RecordedTransport` serves both), metric `fiboki_feed_late_candles_total`.
  `RiskContextBuilder` gained `observe_closes`, `market_open_source` and a close-price mid
  fallback; `BarBatch` gained `market_closed`.
- **Provider.** `OandaCandlesProvider.fetch_bars(count=)`; `request_params` now sends
  `dailyAlignment=0&alignmentTimezone=UTC` by default (`align_utc=False` restores v20's New York
  alignment).
- **Reconcile.** Startup fails closed: divergence exits 75 (reusing the do-not-restart code),
  inability to reconcile exits 1. Wall-time interval (900 s) added alongside the cycle count;
  `errors` now alert `BROKER_UNHEALTHY`; optional `position_reconciler` called with
  `repair=False`.
- **Retry.** New `broker/retry.py` (`retry_idempotent_read`, `ReadRetry`). Applied to
  `OandaAdapter._get` only, and to the provider's `fetch_bars` by the feed.
  `tests/unit/test_retry_scope.py` asserts over the AST that no write-shaped function is ever
  wrapped.
- **Worker.** `log_once(key, ttl)` in `workers/base.py` (obs/ had none). Cycle budget: a live
  evaluation cycle over 25% of the timeframe (excluding the wait for the bar) sets
  `fiboki_live_cycle_seconds`, increments `fiboki_live_cycle_budget_exceeded_total` and fires
  `STRATEGY_DEGRADED` at WARNING (the taxonomy has no slow-worker event).
- **Tests changed.** Two tests in `tests/unit/test_live_worker.py` now also assert that
  `resume()` raises; their original alert assertions are unchanged.
- **Still unwired.** No entrypoint composes a live worker: no real HTTP `Transport` exists in
  `src/`, no live `spread_source`, and nothing drives `VenuePositionManager.on_bar`.
  `deploy/README.md` documents exit 75 as "lease held" only.

Stored results: none affected. No engine, cost model, metric or gate changed. No OANDA candle
data is stored (no credentials have ever existed), so the alignment default invalidates nothing;
any future OANDA H4/D1 import made with `align_utc=False` would not be comparable with the
UTC-anchored research frames.

## 2026-09-28: pre-registered forecast record and deterministic scorer (uncommitted working tree)

- **What was missing.** Agents could write prose but nothing an agent said about the market was
  ever scored, so there was no way to tell whether any role or model is calibrated before its
  output is trusted. Plan Wave 2 item "forecast record".
- **Capabilities.** `WRITE_FORECAST` and `READ_FORECAST_SCORES` added (19 to 21); both pass
  `assert_no_execution_capability` and the registry guard. New `WriteDomain.RESEARCH_FORECAST`.
- **Tools.** `record_forecast` (quant_researcher, market_regime_analyst) and
  `query_forecast_scores` (research_director, statistical_auditor). New public
  `tools.order_vocabulary_hits`: the brief asked to reuse an existing cardinal-rule vocabulary
  check in `tools.py`; none existed (the only vocabulary lists were the capability-name parser
  and the critic's vague-phrase list), so this is new. `ToolContext.model_id` added, default
  empty.
- **Store.** `research/artefacts.py`: `Forecast` and `ForecastScore` artefacts, collections
  `forecasts` and `forecast_scores` in the existing append-only table (no schema migration: the
  payload is JSON).
- **Scorer.** New `research/forecasts.py`: `ForecastPolicy`, `evaluate_forecast`,
  `score_due_forecasts`, `aggregate_scores`, `scoreboard`, `n_forecasts_by_actor`. See
  `docs/v2/AI_AGENT_ARCHITECTURE.md` §11 for the definitions.
- **Tests changed.** `test_agents_roles.py`: `market_regime_analyst` removed from the read-only
  set because it now writes forecasts; replaced by a test pinning its only write to
  `WRITE_FORECAST`, plus a test that no forecasting role can read forecast scores.
  `test_agents_tool_registry.py`: `RESEARCH_FORECAST` added to the allowed write domains and the
  two tools to the required list. No capability-count test existed; one now exists in
  `tests/unit/test_agents_forecasts.py` pinning the 21 names with the reason for the change.
- **Still unwired.** No scheduled scoring run; `AgentSession` does not set `model_id`; scores do
  not feed `ModelRouter`; role prompts are unchanged.

Stored results: none affected. No engine, cost model, metric or gate changed; the two new
collections start empty.

## 2026-09-28: agents on a real local model, run manifest, offline evals (uncommitted working tree)

- **What was missing.** `LocalHTTPProvider` had the Ollama wire format but no client, no
  weights pinning, no structured output beyond `format: "json"`, and no guard against Ollama
  silently truncating an over-long prompt. Audit records named a model but not its weights, and
  nothing recorded which prompts and tool schemas a run was made under. Workflows had no start or
  terminal record, and nothing evaluated a recorded run. Plan Wave 2 items "wire
  `LocalHTTPProvider`", "run manifest hash" and "offline eval harness".
- **Provider.** `providers.py`: `ollama_http_client()` (real `httpx.Client`, `retries=0`,
  `trust_env=False`, 5 s connect / 300 s read), `LocalHTTPProvider.for_ollama(model, client=,
  num_ctx=8192, seed=0)`, `model_fingerprint()` on every provider (`ModelFingerprint{model_id,
  digest}`; Ollama: `/api/tags` manifest digest, else `/api/show`, else the modelfile weights
  blob, else refuse; hosted: `digest=None`), `LLMRequest.json_schema` sent as Ollama `format` with
  `$ref`s inlined, `num_ctx` sent and enforced, a 200 response carrying `error` treated as an
  error, `smoke_test_provider() -> SmokeReport`. One POST per generation, no retry. The request
  digest is unchanged when `json_schema` is unset.
- **Audit.** `AuditRecord` gains optional `model_id`, `model_digest`, `manifest_hash`, hashed only
  when not `None`, so every existing record verifies unchanged (tested against the explicit
  legacy key set). `NO_MODEL = "none"` for actions no model took part in. `AuditedAction` accepts
  the three. The locking code is untouched.
- **Session.** Stamps all three on every record; `think` asks the provider for its fingerprint
  inside the audited block (an unpinnable local model fails the step on the record, before
  generating); `json_schema` pass-through; `default_budget(role_spec)` shared with the workflow
  start record. **Behaviour fix:** the model call's tokens and cost are now written to the record
  before the budget is charged. Previously a call refused by `BudgetExceeded` after generating was
  recorded with cost 0 although the money was spent.
- **Manifest.** New `agents/manifest.py`: `build_run_manifest() -> RunManifest(hash,
  components)` over every role prompt, every tool's input/output schema, the capability enum,
  numpy/pandas/scipy/pydantic versions and the git HEAD (read from `.git`, no subprocess).
- **Workflows.** Both workflows are bracketed by `workflow:start` (manifest hash and components,
  per-role session budgets) and `workflow:end` (written in a `finally`; outcome `error` if any
  step failed or the run raised). The manifest is filed as a `run_manifest`-tagged research note
  once per distinct hash. Tool-feeding steps pass their tool's input schema
  (`WorkflowDeps.schema_constrained_output`, default on). `WorkflowResult.manifest_hash` added.
- **Evals.** New package `agents/evals/`: `run_evals(ledger_path, store) -> EvalReport`,
  `run_evals_on_records`, `write_eval_report`; six cases (critic cites evidence, no trade
  instruction, provenance stamped, terminal record, budget respected, weights pinned); verdicts
  PASS / FAIL / NOT_EVALUABLE / INVALID_ARTIFACT, worst wins, missing instrumentation never a pass.
  `no_trade_instruction` imports `tools.order_vocabulary_hits` read-only for context but decides on
  phrase-level patterns; see `AI_AGENT_ARCHITECTURE.md` §12 for why.
- **Tests added.** `tests/unit/test_agents_local_provider.py` (21), `tests/unit/test_agents_manifest.py`
  (13), `tests/integration/test_agents_run_manifest.py` (7), `tests/integration/test_agents_evals.py`
  (31). No existing test changed.
- **Still unwired.** Not run against a live Ollama server. `workers/runtime.py` does not yet build
  the provider (another agent's file this wave). No scheduled eval run. `ToolContext.model_id`
  still not set by the session.

Smoke test on the Mac (commands only; not run here):

```bash
ollama pull qwen2.5:7b-instruct          # any model; the name below must match
ollama list                               # the ID column is the first 12 hex of the digest
cd ~/Documents/Claude/Projects/Fiboki
.venv/bin/python -c "
import json
from fiboki.agents.providers import LocalHTTPProvider, ollama_http_client, smoke_test_provider
provider = LocalHTTPProvider.for_ollama('qwen2.5:7b-instruct', client=ollama_http_client())
print(json.dumps(smoke_test_provider(provider).as_dict(), indent=2))
"
```

Expect `"ok": true` and a `model_digest` of `sha256:` followed by the `ollama list` ID.

Stored results: none invalidated. No engine, cost model, metric or gate changed. Existing audit
ledgers verify unchanged; their records read back with the three new fields as `None`, which the
evals report as NOT_EVALUABLE. Each new manifest hash adds one research note.

## 2026-09-29: calendar into the gateway and the campaign; research composition root (uncommitted working tree)

- **What was wrong.** (1) `build_replay_session` built its `RiskContextBuilder` with no
  `event_source`, so the gateway's `event_blackout` check ran on every paper order against an
  empty tuple and could never fire (`summary.json`: `wired_into_gateway: false`).
  (2) `discovery.campaign.run_cell` never passed `calendar=` to `run_validation`, so no campaign
  cell ever applied a document's declared event blackout. (3) `EngineEvaluator`'s cache key
  omitted the blackout source; `validation/run.py` worked round it with a per-calendar cache
  subdirectory. (4) No process registered the research job handlers or ran the agent research
  cycle (ARCHITECTURE §12).
- **Calendar, paper.** `build_replay_session(calendar=None, allow_empty_calendar=False)` loads
  `load_official_calendar()` by default, refuses (`CalendarError`) a replay whose span or
  currencies it does not cover unless `allow_empty_calendar=True` (which still applies the
  calendar where it has events), and wires it into `RiskContextBuilder.event_source`
  (`calendar_event_source`; an unfixed-time event reports the point of its span nearest `now`)
  and into the `PaperBroker` blackout source, the one the engine gets. An explicitly empty
  calendar is never wired. `PaperSession.summary()["economic_calendar"]` reports what the gateway
  was given; `wired_into_gateway` is read from the builder. `run_paper_session.py` passes its
  calendar through, gains `--no-calendar`, and reads `wired_into_gateway` back from the session.
- **Calendar, research.** `run_cell(calendar=None)` loads the official calendar (the runner loads
  it once); `CampaignSpec.allow_empty_calendar` (default False, serialised) lifts the coverage
  refusal. An explicitly empty calendar is forwarded as `None`.
- **Cache key.** `EngineEvaluator.engine_fingerprint()` carries
  `blackout = {kind, n_events, events_sha256}` when a source is set (absent otherwise, so
  no-calendar hashes and cache entries are unchanged); a source that cannot be fingerprinted is
  refused behind a cache (`UnfingerprintableBlackout`). The `run.py` subdirectory workaround and
  `_calendar_digest` are removed; its tests stay green.
- **Research runtime.** New `workers/research_runtime.py`: `ResearchRuntimeSettings.from_env`,
  `compose_research_runtime(settings) -> ResearchRuntime` (research store, strategy registry,
  `DataStoreBarSource`, every `jobs.HANDLERS` entry on the worker's orchestrator and on a private
  cycle orchestrator, `EchoProvider` or `LocalHTTPProvider.for_ollama`, `JsonlAuditLedger` at
  `<state_dir>/agents/audit.jsonl`, a claimed-before-run nightly `run_research_cycle`,
  `run_failure_investigation` via `IncidentChannel` or `raise_incident`). Each run pins
  `ToolContext.as_of` to its own start. `ResearchWorker.setup` composes it when
  `FIBOKI_AGENT_CYCLES` is on; agent work runs inside `HeartbeatPulse`, which renews the lease and
  writes a heartbeat every `pulse_seconds`. Seven `FIBOKI_*` variables declared in
  `ENV_REGISTRY`.
- **Tests.** New: `tests/integration/test_replay_calendar_blackout.py` (6: an entry on the
  2024-03-08 13:30Z NFP bar is blocked with `event_blackout:2024-03-08T13:30:00+00:00`, the same
  bars with no calendar are accepted, a quiet-day breakout is accepted, an uncovered replay is
  refused), `tests/unit/test_engine_evaluator_blackout_key.py` (4),
  `tests/unit/test_discovery_campaign_calendar.py` (5), `tests/integration/test_research_runtime.py`
  (14). Changed: `test_calendar_guards.py` (the opt-out now records `wired_into_gateway: true`;
  new `--no-calendar` test), `test_live_worker_runtime.py` (the 2009 XAUUSD slices pass
  `allow_empty_calendar=True`).
- **Still open.** Nothing starts the heartbeat watchdog or schedules reconciliation. Incident
  alerts reach the investigator only in-process. The gateway's 15-minute window is measured from
  a replay bar's OPEN stamp, so on H4 it sees only releases near a bar boundary; the seeds'
  15 to 45 minute document blackouts have the same property on H4 (evaluated on the entry bar's
  stamp). `run_paper_session.py` still builds `PaperConfig` with the default exit policy, not the
  document's, so a document's own blackout does not apply in that script's paper replay.
  `cli.py` still prints "no handlers registered" even when the flag composes them.

Stored results: **superseded for every strategy that declares an event blackout, which is all
five seeds.** Every earlier campaign or ladder result for them ran with no blackout (none was ever
enforced through `run_cell`), so from now on a validation over bars that overlap the calendar
(2024-01-01 to 2026-12-04) differs and the earlier ones are superseded, not regressions. A cell
over bars entirely outside that span is now refused by default rather than silently run; with
`allow_empty_calendar=True` its numbers are unchanged, because the calendar has no events there.
Earlier paper replays likewise traded through every release. Evaluation-cache entries written
under a calendar are recomputed once (new key); no-calendar entries remain valid.

## 2026-09-28: point-in-time headline recorder and macro providers (uncommitted working tree)

- **What was missing.** D-A5 (record news first, classify second) had nothing behind it, and
  no macro source existed that could say when a value became knowable. A backtest reading a
  macro number at its reference date, or a headline at its vendor timestamp, is look-ahead that
  no bar-level test catches.
- **Headlines.** New `data/news/` (`store`, `sources`, `recorder`). Append-only SQLite in
  `<state_dir>/news/headlines.sqlite` (WAL; triggers refuse UPDATE, DELETE and a colliding
  INSERT, which closes `INSERT OR REPLACE`). `observed_at` is our clock and the only
  availability instant; `HeadlineStore.query(as_of, since, sources, currencies_hint)`.
  Twelve official feeds from the six banks, every URL taken from the bank's own RSS index page
  on 2026-09-28 (DATA_ARCHITECTURE.md §14.1). Finnhub and Marketaux clients, off without
  `FIBOKI_FINNHUB_API_KEY` / `FIBOKI_MARKETAUX_API_KEY`. CLI `fiboki news record --once|--loop`,
  `fiboki news status`.
- **Macro.** New `providers/macro_base.py` (long-format frame, `AvailabilityBasis`, `as_of`,
  superset holiday calendars, content-addressed `MacroDatasetStore`) and six first-party clients:
  `alfred` (vintages; `FIBOKI_FRED_API_KEY`), `cftc_cot` (Friday 15:30 ET rule, CFTC 2025
  backlog overrides, lapse windows unresolved), `ecb_sdmx`, `boe_iadb`, `ons` (archived versions
  as vintages; release calendar recorded), `nyfed` (reference rates, repo). CLI
  `fiboki macro describe|fetch`. Three variables declared in `ENV_REGISTRY`.
- **Measured, not assumed.** A live `fiboki news record --once` at 22:55Z fetched all twelve
  feeds (263 items, 256 unique); a second poll inserted 0. Live `macro fetch` succeeded for ECB,
  CFTC, BoE, ONS (with archived versions) and NY Fed; ALFRED refused for want of a key. The ONS
  archive stamps were found to precede some publications (05:07Z on a 07:00 BST release day), so
  only on-the-hour or half-hour stamps are trusted.
- **Not done.** No worker supervision of the recorder (belongs to `workers/`); no
  `query_news` tool or `READ_NEWS_SNAPSHOT` capability; ALFRED, Finnhub and Marketaux never
  exercised live. Seven days of continuous capture (the Wave 3 acceptance) has not started.
- **Tests.** `tests/unit/test_news_recorder.py` (37), `tests/unit/test_macro_providers.py` (45),
  recorded fixtures under `tests/fixtures/news` and `tests/fixtures/macro`.

Stored results: none affected. Nothing reads these datasets yet; no engine, cost model, metric
or gate changed.

## 2026-09-29: bar-indexed entry locks in the DSL, engine and gateway (uncommitted working tree)

- **What was missing.** AGENTIC_INTEGRATION_PLAN §5 Wave 3: "bar-indexed cooldown and stop-streak
  locks declared in the DSL, enforced identically in engine, paper and gateway". The only cooldown
  was `position_management.cooldown_bars_after_exit`, enforced by the position book at fill time on
  the engine's loop counter, invisible to the gateway and forgotten by a restart.
- **DSL.** Optional `locks` block on `StrategyDocument` (`cooldown_bars_after_close`,
  `stop_streak{n_stops, lookback_bars, lock_bars, scope}`), bindable. Absent from the dump when
  undeclared; an inert block is dropped on load. `SCHEMA_VERSION` NOT bumped (reasoning in
  STRATEGY_STANDARD.md §4a). Complexity +1.0 per declared lock rule. The compiler refuses an
  instrument-scoped streak that can never arm, and stamps `locks_declared` on signals of a
  lock-declaring document.
- **One implementation.** New `backtest/locks.py`: `SessionBarClock` (session-bar ordinals from
  the timestamp, interbank weekend removed), `LockPolicy`, `LockBook.on_close / is_locked /
  rebuild`, `LedgerLockView` (rebuilds from a ledger on every question), `closes_from_intents`.
  Pattern after freqtrade `plugins/protections` (GPL-3.0, not copied), corrected to session bars
  and always-on.
- **Enforcement.** `ExitPolicy.locks` carries the policy (fingerprint key present only when
  declared). `BacktestEngine` asks the lock book on the decision bar after scheduling a reversal
  and before sizing; refusals are `rejections["instrument_lock"]` and `BacktestResult.lock_blocks`.
  `RiskGateway` gains a nineteenth check, `instrument_lock`, reading `RiskGateway(locks=...)`,
  fail-closed as described in PORTFOLIO_RISK_STANDARD.md §3. The position book, the paper adapter
  and the venue manager are unchanged.
- **Parity.** `tests/integration/test_lock_parity.py`: one lock-declaring document through the
  engine, `PaperBroker` behind the gateway (also rebuilt from a ledger file at every bar), and
  `VenuePositionManager.submit` over `SimulatedVenue`: identical trade and leg ledgers, and the
  gateway refused exactly the bars the engine refused, including locks armed on a Friday that were
  still in force after the weekend.
- **Not done.** No worker wires a `LedgerLockView` into its gateway (`workers/` was out of scope),
  so a lock-declaring document is refused on every paper/demo entry until one does; documents
  without locks are unaffected. The intent ledger does not record exit reasons, so a stop streak
  recovered from it alone is conservative. `risk/accounting.py` and
  `tests/integration/test_risk_context_inputs.py` still say "eighteen" in prose.
- **Tests.** `tests/unit/test_locks.py`, `tests/unit/test_dsl_locks.py`,
  `tests/unit/test_gateway_instrument_lock.py`, `tests/integration/test_lock_parity.py`,
  `tests/integration/test_locks_regression_pin.py`; `tests/unit/test_risk_gateway.py` check count
  18 -> 19.

Stored results: none invalidated. No document in `research/strategies/` declares locks (grepped);
for all five seeds the content hash, structure hash, serialised JSON, complexity score, exit-policy
fingerprint and engine ledger hashes are pinned to their pre-change values and unchanged.
`ENGINE_VERSION` unchanged, because no run without locks changes by a byte.

## 2026-09-29: wider free data surface with recorded terms (uncommitted working tree)

- **Source registry.** New `data/sources/registry.py`: 31 entries (25 implemented, 6 forbidden or
  licence-only), each with `terms_url`, a quoted `terms_summary`, `terms_status`
  (permitted / personal_only / opt_in_unclear / forbidden), key env, cadence, history depth,
  point-in-time semantics and module. `describe_sources()` and `registry_markdown()`; the table in
  DATA_ARCHITECTURE.md §14.4 is pinned to it by a test.
- **Headlines.** BIS central bankers' speeches added to `OFFICIAL_FEEDS` (as `NewsSource.OTHER`;
  the enum is a CHECK constraint in existing stores). GDELT DOC 2.0 client with six curated
  queries, opt-in (`FIBOKI_GDELT_ENABLED`), one request per 5 s shared limiter, truncation at 250
  logged in the poll log (`NewsRecorder` now merges a reader's `last_report`). Finnhub news clients
  share a 60/min client-side limiter (`data/providers/ratelimit.py`).
- **Calendars.** `data/providers/calendar_feed.py` (dated-event files in the `load_events_json`
  shape, write-once, scheduled times only; `calendar_diff`), `finnhub.py` (premium-only
  `/calendar/economic`; time treated as UTC on the schema sample's evidence), and
  `forexfactory_feed.py` (opt-in, `opt_in_unclear`, JSON only). Official calendar untouched.
- **Positioning.** New `data/positioning/`: append-only snapshot store with `as_of` on
  `observed_at`, OANDA position/order books (GET-only, parsed-host check; endpoints possibly
  withdrawn by OANDA in 2024), Myfxbook Community Outlook via the official API (session reuse,
  100/day budget, credentials scrubbed), and a recorder.
- **Macro.** `fred_pack.py`: `fred_cross_asset_daily` (DGS2, DGS10, DTWEXBGS, VIXCLS, DCOILWTICO)
  through ALFRED, registered in `MACRO_DATASET_PACKS`.
- **Settings.** ENV_REGISTRY gains `FIBOKI_GDELT_ENABLED`, `FIBOKI_FF_CALENDAR_OPT_IN`,
  `FIBOKI_OANDA_BOOKS_TOKEN`, `FIBOKI_OANDA_BOOKS_ENVIRONMENT`, `FIBOKI_MYFXBOOK_EMAIL`,
  `FIBOKI_MYFXBOOK_PASSWORD`.
- **Tests.** `tests/unit/test_data_source_expansion.py` (26), `tests/unit/test_source_registry.py`
  (8); `tests/unit/test_news_recorder.py` expectations updated for the thirteenth feed (rows 20 ->
  22) and the `gdelt` off-reason. Fixtures: `news/bis_cbspeeches.rss` is a trimmed live copy;
  every other new fixture is constructed.
- **Not done.** No CLI commands for calendars, positioning or the pack (`cli.py` was another
  item's), no API route for `describe_sources()`, no worker supervision of the positioning
  recorder, no store migration to give GDELT and BIS their own `NewsSource` values. Nothing new
  was exercised against a live service.

Stored results: none invalidated. Existing headline stores gain BIS rows from their next poll
onward; nothing already recorded changes. No engine, cost model or metric was touched.

## 2026-09-29: desktop migration, llama.cpp provider, `fiboki doctor`, backup/restore (uncommitted working tree)

- **What was wrong.** The desktop target (Mac, continuous, local models on llama.cpp) had no
  llama.cpp provider: `LocalHTTPProvider` speaks Ollama's `/api/chat` and `/api/show`, which
  llama-server does not serve. `scripts/desktop/Start Fiboki.command` exported `FIBOKI_LLM_URL`
  (read by nothing, not in `ENV_REGISTRY`) and switched `FIBOKI_AGENT_CYCLES` on without
  `FIBOKI_AGENT_LOCAL_MODEL` or `FIBOKI_AGENT_CYCLE_TARGET`, both of which make
  `compose_research_runtime` raise, so detecting a model would have stopped the research worker.
  There was no backup or restore command, no launchd unit for the API, web, news or model server,
  and no check that a `.venv` or `node_modules` had been copied from another machine.
- **llama.cpp provider** (`agents/providers.py`). `LlamaCppProvider(LocalHTTPProvider)`, built
  by `LocalHTTPProvider.for_llama_cpp(base_url, model=None)`: model id from `/v1/models` (alias or
  path, `aliases` honoured; a name the server does not serve is refused), per-slot
  `default_generation_settings.n_ctx`, `model_path` and `build_info` from `/props`. Pinned by the
  SHA-256 of the GGUF bytes (all shards of a split model), cached by path+size+mtime in memory
  and optionally in a JSON file; a relative, missing or unreadable path is refused. Every
  `generate` re-reads `/props` and refuses if the weights path or context changed. Schema output
  as `response_format` `json_schema` from b4820, else `json_object`+`schema` (b2487+); a server
  rejection of the `json_schema` form (HTTP error naming `response_format`) falls back once,
  stickily. No client-side GBNF converter (reasoning in the class docstring). `name` stays
  `"local"` so the offline eval still FAILs an unpinned local record. `for_local_server` picks
  llama.cpp or Ollama by probing `/props`. Build numbers from the llama.cpp git history (PRs
  #5978, #9527, #12168, #13771, #15434); endpoint shapes from `tools/server/README.md` and
  `server-context.cpp`.
- **Not changed, proposed.** `workers/research_runtime._build_provider` still calls
  `for_ollama`; the one-line change to `for_local_server` is in `OPERATIONS.md` §13.4. The
  brief's `FIBOKI_LLM_URL` was NOT introduced: `FIBOKI_AGENT_LOCAL_URL` is the declared variable
  for the same thing, and a new name would need an `ENV_REGISTRY` entry (outside scope) to pass
  `test_every_env_name_used_as_a_literal_in_src_is_declared`.
- **`fiboki doctor`** (`cli.py`, delimited block). A group: `fiboki doctor [--json] [--no-hash]
  [--only NAME] [--repo DIR]` and `fiboki doctor model`. Sixteen checks behind a `DoctorHost`
  seam, OK/WARN/FAIL plus a fix, exit 1 on any FAIL, a crashing check reported as a FAIL row.
  `fiboki system doctor` is unchanged.
- **Scripts.** `desktop-install.sh` (idempotent, `--check`), `backup.sh`, `restore.sh`,
  `llama-server.sh`, `launchd-install.sh`, `fiboki-service.sh` (per-service entrypoint: reads
  `~/.fiboki/env`, then forces paper and unsets the live controls), and
  `deploy/launchd/uk.fiboki.{api,worker,web,news,llama}.plist` templates. Launcher model detection
  rewritten to the declared variables; cycles only with a target.
- **Found, not fixed (outside scope).** `apps/web/next.config.ts` calls `new URL("")` when
  `NEXT_PUBLIC_FIBOKI_API=""` (as `dev-up.sh` sets it); checked with Node, `next build` not run.
  `dev-up.sh` exports `FIBOKI_INCIDENT_LOG` and `FIBOKI_API_PROXY_TARGET`, undeclared.
- **Tests.** `tests/unit/test_agents_llama_cpp_provider.py` (29 + 1 skipped as root: recorded
  `httpx.MockTransport`, the README `/props` and `/v1/models` shapes), `tests/unit/test_cli_doctor.py`
  (24, fake host), `tests/unit/test_desktop_scripts.py` (23: real bash runs against temp dirs,
  a stub `llama-server`, a local fake HTTP server for the launcher block). At 01:15Z:
  `pytest tests/unit/test_layering.py tests/unit/test_cli.py tests/unit/test_agents_local_provider.py
  tests/*/test_agents_*.py tests/unit/test_cli_doctor.py tests/unit/test_desktop_scripts.py
  tests/unit/test_api_settings_hygiene.py tests/unit/test_deploy_guards.py
  tests/integration/test_research_runtime.py` printed **666 passed, 1 skipped**. `ruff` clean on
  every file touched; `scripts/check_live_flags.py` clean (334 files).
- **Not exercised.** Anything needing macOS (launchctl, plutil, Homebrew, `sysctl hw.memsize`),
  a real llama-server, a real model, `npm run build`, and a restore of a real deployment.

Stored results: none invalidated. No engine, cost model, metric, gate or strategy was touched;
the Ollama provider path is byte-for-byte unchanged.

## 2026-09-29: backend asks for the frontend overhaul (uncommitted working tree)

FRONTEND_OVERHAUL_PLAN.md §5, §6, §8; report E §6, §7.2.

- **`GET /api/stream`** (`api/routers/stream.py`). One multiplexed SSE endpoint over
  `sse-starlette==3.0.3`, the last release that co-installs with `fastapi==0.115.6` (3.0.4+
  require `starlette>=0.49.1`; FastAPI pins `<0.42`). Envelope as the plan; snapshot per topic,
  then deltas keyed by entity id and tombstones; per-topic seq from a per-process epoch-ms base;
  500-event ring per topic with `Last-Event-ID` replay as a cut across topics, or a fresh snapshot
  when aged out; 5 s heartbeat with `worker_heartbeat_age_s` from the table reader, mode, kill
  switch and per-topic seq/as_of; `Cache-Control: no-cache`, `X-Accel-Buffering: no`, 15 s comment
  keep-alive. Session cookie plus an Origin/Referer allow-list check; a revoked session ends the
  stream at the next heartbeat. Health on a 15 s timer; kill switch replayed from the journal each
  second; positions, fleet and risk through the REST readers; marks `absent` until a feed calls
  `publish_mark` (coalesced to 4 Hz). The hub never starts a worker (tested).
- **`GET /api/command/attention`** (`api/routers/command.py`). Server-ranked; weights pinned by a
  test; unreadable sources become items; seed-fixture breaches are not raised.
- **Incidents** (`api/routers/incidents.py`). Read model over `FIBOKI_ALERT_LOG` and the
  kill-switch journal, deduplicated, stable ids, timeline. `POST .../{id}/ack` (reason >= 8) and
  `.../note`, audited, admin-only because `tests/api/test_security.py` requires every mutating
  route to be admin; operator access needs that policy changed deliberately.
- **`GET /api/markets/overlays/{symbol}`**. Signals (paper telemetry), fills, levels, regime
  segments (marketstate), calendar events, headlines (news store, read-only) and indicator series
  computed only by `fiboki.indicators` (baseline Ichimoku, Fibonacci, ATR plus the seed
  strategies' compiled indicators) with pane hints and dataset version ids. Backtest fills and a
  stop-move history are reported unavailable: neither is persisted. `/api/markets/bars` carries
  `v` and `volume_kind` when the dataset has non-zero volume.
- **Disarm preflight** also served at `GET /api/system/kill-switch/disarm/preflight`, delegating
  to the trading route, which stays as the alias for one release.
- **Health.** The heartbeat check now uses the platform's reading (age >= threshold is stale), says
  "unreadable" for an unreadable store, and a `paper_journal` check degrades on unreadable
  sessions.
- **Caveats.** `realism_caveats`/`figure` take `charged`; `Platform.session_charged_costs` reads
  `summary.json`'s `cost_breakdown`. Used by the stream's fleet figures and the overlay fills.
  `/api/trading/trades` rows still get the settings-only caveats until `trading._trade_view` passes
  `charged` (one line; not in this change's file set).
- **OpenAPI.** `/api/openapi.json` now carries `securitySchemes` and a per-operation
  `x-fiboki-auth` derived from route dependencies; `scripts/gen-openapi.sh` writes
  `apps/web/openapi.json` without a server.
- **Tests.** `tests/api/test_stream.py`, `test_incidents.py`, `test_command_attention.py`,
  `test_overlays.py`, `test_health_edges.py`, `test_openapi_and_preflight.py`,
  `tests/unit/test_api_charged_caveats.py`.

Stored results: none invalidated. No engine, indicator, strategy or risk code changed.
`deploy/requirements.lock` does not yet list `sse-starlette`; regenerate it.

## 2026-09-29: the agent event channel, shadow only (uncommitted working tree)

- **What was missing.** AGENTIC_INTEGRATION_PLAN §5 Wave 3 row 3 (`query_news`) and Wave 4 rows 1
  and 2 (tool-less `event_classifier` into a quarantined store; deterministic `EventVetoPolicy`
  with a shadow evaluator). Headlines were recorded but nothing read them.
- **Agents.** Capabilities `READ_NEWS_SNAPSHOT` and `WRITE_EVENT_ANNOTATION` (21 -> 23, both pass
  the execution guard); write domain `research:event_annotation`; tools `query_news`
  (point-in-time, refuses without `as_of`, <= 200 rows, <= 7 days, quoted data objects) and
  `record_event_annotations` (closed schema, batch-bound citations, stamps observed_at /
  available_at / model / digest / manifest / `event_annotation_v1`). `query_news` granted to
  `market_regime_analyst` and `research_director`. New role `event_classifier`: one capability,
  one tool, no read. `ToolContext` gains `news`, `events`, `clock`, `model_digest`,
  `manifest_hash`. Workflow `run_event_scan` (fetch without a model, batches of <= 40, one
  classifier call per batch, cost cap, fully audited).
- **Contracts.** `core/contracts.py`: `EventAnnotation`, `VetoReason`, `VetoAssessment` (no free
  text, prices, stops or sizes), `EventType`/`EventBucket` vocabularies, `SHADOW_REASON_PREFIXES`
  and `RiskDecision.blocking_reasons` / `shadow_reasons`.
- **marketstate/events.py (new).** Append-only `AnnotationStore` (`<state_dir>/events/
  annotations.sqlite`, WAL, triggers), `instrument_buckets`, `EventVetoPolicy` (`event_veto_v1`,
  disabled), `EventVetoSource` (point-in-time, fail-open with one alert per transition,
  freshness from the `scan_log` heartbeat), `shadow_report` with the vol-spike and
  calendar-only baselines.
- **Gateway.** Twentieth check `event_veto` after `event_blackout`: blocks `RequestKind.OPEN`
  only and only when enabled; otherwise records `event_veto_shadow:<...>`, which does not count
  towards `allowed`. `ExecutionAttempt.reason` and telemetry `venue_error` carry blocking reasons
  only; attempt rows stamp `event_veto_policy` (`not_wired` without a source).
  `tests/unit/test_risk_gateway.py` count 19 -> 20.
- **Runtime.** `FIBOKI_EVENT_SCAN_MINUTES` (declared; default 15, 0 = off) schedules the scan in
  the research runtime when `FIBOKI_AGENT_CYCLES` is on; one `scan_log` row per scan.
  `tests/integration/test_research_runtime.py` sets it to 0 so its ticks run only the nightly
  cycle.
- **CLI.** `fiboki events shadow-report --trades FILE` (read-only). `cli.py` had no other edits
  when this block was added; a `doctor` block from another agent has since landed beside it.
- **Pre-registration.** `research/preregistration/event_veto_v1.json` (draft; model pin and
  decision date to fill at filing), pinned to the code by a test.
- **Tests.** `tests/unit/test_event_veto_policy.py`, `tests/unit/test_event_veto_gateway.py`,
  `tests/unit/test_agents_event_channel.py` (includes the injection test),
  `tests/unit/test_events_cli.py`, `tests/integration/test_agents_event_scan.py`,
  `tests/integration/test_event_scan_runtime.py`; count updates in the capability, role, tool
  registry and gateway tests.
- **Not done.** No worker passes an `EventVetoSource` into its `RiskContext` (`workers/runtime.py`
  was out of scope), so the gateway shadow log is empty until one does. No real model has
  classified a real headline.

Stored results: none invalidated. No engine, cost model, metric, gate threshold or strategy
changed; the new gateway check passes whenever no source is wired, which is every existing path.

## 2026-09-29: safety findings from the backend audit (P0-1, P1-4, P1-6, P1-9, P2-14, P2-16, P2-20, P2-21) (uncommitted working tree)

- **One path resolver (P0-1, B-06).** New `core/paths.py`: pure `resolve_paths(env, *, cwd=None)
  -> FibokiPaths` (state dir, kill-switch journal, intent/audit/API-audit ledgers, holdout and
  experiment dbs, news/events stores, heartbeat db, paper root, alert log, alert outbox).
  `api/settings.load_settings` builds `Settings.paths` from it; `killswitch_path` and
  `audit_path` read it. The CLI killswitch commands lost their `~/.fiboki/killswitch.jsonl`
  default, print the absolute journal, and warn when `FIBOKI_STATE_DIR` is unset or `--journal`
  is not the journal gateways read. The flatten message no longer claims a worker will act.
- **Kill switch refresh (P0-1, B-07).** `KillSwitch.refresh()` re-reads the journal when its
  (inode, size, mtime) changed; called from `allows()`, `state`/`active`/`mode`, `activate`,
  `deactivate` and `flatten_orders`. `KillSwitch.at_path` / `from_paths`. `RiskGateway(mode=...)`
  refuses an in-memory journal for any mode but BACKTEST; `mode=None` (legacy, what
  `workers/runtime.py:1025` still does) is backstopped: a risk-adding order in SHADOW/DEMO/LIVE is
  blocked `kill_switch_journal_not_durable:<mode>`, and every attempt row stamps
  `kill_switch_journal: durable|in_memory`. Cross-process test: a subprocess CLI pause blocks an
  already-built gateway's next evaluate.
- **Explicit risk inputs (P1-4, B-09).** `RiskContext.open_risk_amount`, `correlated_exposure`,
  `daily_pnl`, `weekly_pnl`, `fx_quote_to_account` are `float | None = None`. A missing one blocks
  `<check>_input_missing:<field>`; PAPER may pass only with `RiskGateway(paper_allows_missing_inputs=True)`,
  and attempt rows record `missing_inputs` and the permission. AST test: every `RiskContext(` in
  src passes all five. Test fixtures now state their zeros.
- **Durable ledgers (P1-6, B-08).** New `core/durable.py`: `durable_append` (exclusive flock,
  `F_FULLFSYNC` on Darwin, directory fsync on create), CRC32 spliced into each JSON object as its
  first key (still valid JSON for existing readers), torn-tail quarantine to `<file>.torn-<ts>`
  with named hooks, `DurableLogCorrupt` for mid-file damage, legacy lines accepted. Used by
  `JsonlIntentStore`, `FileKillSwitchJournal`, `FileChannel`, and the agent audit ledger's append
  (unframed there: its readers were out of scope and its hash chain already verifies records).
- **Alerts leave the machine (P1-9, B-15/B-16).** `build_default_dispatcher` injects an httpx
  transport for Telegram/webhook when configured, wraps them in `OutboxChannel` over
  `<state_dir>/alerts_outbox.sqlite` (CRITICAL at-least-once, retried by `retry_pending`), scrubs
  URL/token from delivery errors, filters `/bot<token>/` out of httpx/httpcore logs, and installs
  a `LEDGER_TORN_TAIL` hook. New events `LEDGER_TORN_TAIL` (critical) and `ALERT_TEST`. New CLI
  `fiboki alerts test [--critical]` and `fiboki watchdog run` (Worker subclass: lease `watchdog`,
  heartbeat, evaluates heartbeats and retries the outbox every cycle).
- **SQLite (P2-14, B-35).** `install_sqlite_pragmas` (WAL, busy_timeout 30000, synchronous FULL,
  every connection) on the holdout, experiment and dataset-catalogue engines. Holdout: UPDATE and
  DELETE triggers on `holdout_consumption` and a new append-only `holdout_outcome` table
  (`record_outcome` no longer UPDATEs the claim; legacy inline outcomes are still read and still
  final).
- **Secrets and thresholds (P2-16, P2-20, P2-21).** `OandaConfig.api_token` is `repr=False`.
  Operator passwords: `scrypt$n$r$p$salt$key` via `hash_password`; bare-hex and `sha256$` legacy
  entries still verify and log a rotation warning. `obs.health.HealthThresholds` is the single
  value, resolved into `Settings.health` (`FIBOKI_WORKER_DOWN_SECONDS`, `FIBOKI_DATA_STALE_SECONDS`
  declared); `WorkerHeartbeatCheck`, `WatchdogThresholds`, `fiboki worker status` and `system
  health` derive from it. AST test: no `compiled_in=` / `live_host_compiled_in=` in src.
- **Not done (outside this brief's files).** `workers/runtime.py` (another workstream) now
  passes `KillSwitch.from_paths(...)` and `mode=PAPER` in `build_replay_session`, but its
  `RiskContextBuilder` still substitutes 0.0 for a missing P&L ledger or correlation matrix and
  defaults `market_open=True`; `entrypoints/paper_forward.py` builds its gateway with the
  resolved file journal but without `mode=`. `workers/live_worker.py` `data_stale_after_seconds` and the
  doctor heartbeat check still carry their own numbers. `workers/base.py` applies pragmas to one
  pooled connection. `core.contracts.SHADOW_REASON_PREFIXES` has no prefix for an excused
  missing input, so it is recorded in the attempt row, not in `decision.reasons`. `fiboki doctor`
  does not yet flag legacy password hashes.

Stored results: none invalidated. No engine, cost model, metric or gate changed. Existing
ledgers stay readable. Behaviour change for callers: a `RiskContext` built without the five
inputs now blocks, and a DEMO/LIVE/SHADOW open through a gateway without a durable kill switch
now blocks.

## 2026-09-29: paper trading forward on OANDA practice prices; durable job ledger (P1-8, P1-10, P1-15, P2-4, P2-17, P2-18) (uncommitted working tree)

- **Paper forward (P1-15).** New `src/fiboki/entrypoints/` (rank 115 in `test_layering.py`,
  imported only by `cli.py`). `paper_forward.compose(settings, wiring) -> LiveWorker` reads the
  committed `entrypoints/wiring/paper_forward_v1.json` (schema `fiboki.paper_forward.wiring/1`:
  Donchian seed by bound content hash `66d6a844...`, EURUSD/GBPUSD/XAUUSD H4, `limits_v1_paper`,
  `OANDA_REALISTIC`, official calendar, feed-history correlation, ledger dir, `allowed_modes`
  exactly `["paper"]`) and composes: `OandaPollingBarFeed` over the new real transport, the new
  pricing spread source, `VenuePositionManager` over the shared `PositionBook`, the new instant-fill
  `ForwardPaperVenue` (`entrypoints/paper_venue.py`: fills at the feed's last close +/- the
  profile half spread; the book's next-open fill stays the ledger of record), `ExecutionService`
  (PAPER, `JsonlIntentStore`), `RiskGateway` with the durable kill-switch journal at
  `settings.killswitch_path`, and `LiveWorker` (startup and 900 s reconciliation, heartbeat,
  lease `paper-forward`). The wiring's sha256 is stamped on every attempt row
  (`attempts.jsonl`) and journal line. Journals under `<state_dir>/paper_forward/<version>/`;
  one continuous paper session under `<paper_root>/forward-<version>/` in the format
  `api/paper_journal.py` reads. Restarts carry balance, peak equity and closed trades; open
  positions are journalled and alerted as abandoned. `fiboki paper forward --wiring <path>
  [--check|--once|--max-cycles]`; `fiboki worker run live` still refuses.
- **Real transport.** `broker/http_transport.HttpxTransport`: one `httpx.Client` with
  `HTTPTransport(retries=0)`, no redirects, per-request timeout, `https` plus a PARSED-hostname
  allow-list, no stored headers, token-free `repr`. `bearer_token_from_env` names the variable,
  never the value. AST test: constructed only under `entrypoints/` and in `cli.py`.
  `RecordedTransport` stays the test double.
- **Live spread (P2-4).** `broker/oanda_pricing.OandaPricingSpreadSource`: `GET
  /v3/accounts/{id}/pricing` once per bar (the one `@retry_idempotent_read` method, `_get_pricing`),
  `spread_source(instrument, now) = ask - bid`, `nan` when unsampled, stale (> 120 s), not
  tradeable or crossed. Practice host only.
- **Durable orchestrator (P1-8).** `agents/orchestrator.Orchestrator(path=...)` /
  `Orchestrator.durable(state_dir)` keeps the job ledger in SQLite (WAL, busy_timeout 30 s,
  synchronous FULL): specs, status, attempts, a JSON result record, `idempotency_key UNIQUE`.
  `Orchestrator()` keeps the same tables in memory. `run_next` claims in one `BEGIN IMMEDIATE`;
  `bind_lease(holder, fence)` fences claims and completions with the worker lease token
  (`FencedOut` for a zombie); `recover_abandoned()` returns RUNNING jobs of a dead worker to their
  retry policy. Payloads must be JSON (refused otherwise, so a handler sees the same recorded
  input before and after a restart). Same public API; every existing orchestrator test passes.
- **Pulse (P1-10).** `ResearchWorker._run_one` runs under `HeartbeatPulse` at
  `min(pulse_seconds, lease_ttl/3)`; `Worker.pulse()` renews and beats between jobs in a cycle;
  `ResearchWorker.resume` binds the ledger to the lease and recovers abandoned jobs.
- **launchd (P2-17, P2-18).** `deploy/launchd/uk.fiboki.paper.plist` (`ProcessType Standard`,
  `ExitTimeOut 45`), not in the installer's default list. `scripts/fiboki-service.sh paper` runs
  the committed wiring under `caffeinate -is -w $$`. Legacy `com.fiboki.research-worker.plist`
  deleted. `scripts/dev-up.sh` refuses to start when `launchctl list | grep uk.fiboki` finds a
  loaded service (skipped when `launchctl` is absent).

Tests, run 2026-09-29 ~03:43Z: `pytest tests/unit/test_orchestrator_durable.py
test_research_worker_pulse.py test_http_transport.py test_oanda_pricing_spread.py
test_paper_forward_compose.py test_paper_forward_deploy.py tests/integration/test_paper_forward_e2e.py
tests/unit/test_layering.py test_no_gateway_bypass.py test_retry_scope.py test_cli.py
test_deploy_guards.py test_api_settings_hygiene.py` -> 209 passed. Every test file importing
`fiboki.workers`, `fiboki.broker` or the orchestrator (57 files) -> 1132 passed, 1 skipped,
2 failed (`test_agents_jobs.py`, caveat text and DSR; files another agent is changing under
P1-2). `ruff check` clean on every file touched here.

Not done (outside this brief's files): `cli.py` `worker run research` still builds
`Orchestrator()` (in memory); it should pass `path=<state_dir>/jobs.sqlite`. `deploy/README.md`
still documents the deleted legacy plist. The OANDA credentials are read as
`OANDA_PRACTICE_TOKEN` / `OANDA_PRACTICE_ACCOUNT_ID` because `api/settings.ENV_REGISTRY` (not
owned here) declares every `FIBOKI_*` name. `fiboki doctor`'s launchd check does not list
`uk.fiboki.paper`. Not exercised: a real OANDA practice request (no credentials here), launchd
and caffeinate (Linux).

Stored results: none invalidated. No engine, cost model, metric or gate changed.

## 2026-09-29: sizing through portfolio construction, the conviction channel, agent influence tiers

Joe's request: "the agent should know/decide size based on confidence, leverage, open positions,
open trades, strategy". Delivered as: the deterministic system decides size from all of those;
the agent contributes a schema-bound, expiring verdict that a versioned, default-off, down-only
policy may use to make a size smaller, and only at a signed tier that the reviewed ceiling does
not yet allow.

- **P1-11 closed: construction is wired.** `workers/runtime.SignalEvaluator` now allocates every
  bar's signals together with `PortfolioConstructor` against the venue's own book
  (`RiskContextBuilder.snapshot`, which now measures open risk per instrument), then calls
  `size_trade` once per accepted candidate with `risk_fraction = tier base risk` and
  `portfolio_weight = weight`. `SizingPolicy.risk_fraction` became a ceiling on the tier base.
- **construction_v2** (`portfolio/construction.py`): `StrategyTier` base risk 0.25 / 0.75 / 1.5 /
  2.0% and concurrency caps 2 / 4 / 6 / 8 (RISK_GOVERNANCE Governor 1); drawdown throttle as a
  step function x0.6 / x0.3 (Probationary suspended) / PAUSE 15% / FLATTEN 20% (Governor 3);
  correlation-aware open-risk budget 3% (unmeasured pairs 0.30) and total risk cap 6% (Governor
  2); `vol_target_max_scale` 1.5 -> 1.0 and refused above 1.0; a final `tier_cap` clamps every
  weight to <= 1.0 so final risk <= tier base (property test); `coarse_regime` maps the
  five-axis marketstate key onto the regime scalars (under v1 every real regime fell to
  "unknown" 0.6); each candidate uses its own instrument's regime; `Allocation.trace` records
  every step including those at 1.0; every result and every gateway attempt row is stamped
  (`StampingRecorder`).
- **Conviction channel:** `core.contracts.ConvictionReading`; `ConvictionPolicy` v1 (disabled;
  disagreement strength 2 -> 0.75, strength 1 -> 0.9, everything else 1.0; floor >= 0.5 and
  `max_factor <= 1.0` asserted); `_step_conviction` appended to the pipeline; `ShadowFactor` rows
  with `Provenance.SHADOW` on every allocation; `ConvictionAdapter` in the runtime is the only
  construction site (AST-tested along with single consumption and no path into sizing, the
  gateway or execution).
- **Agent influence tiers:** `core/tier.py` (T0..T4, HMAC-signed `<state_dir>/agent_tier.json`,
  absent = T1, tampered = T1 plus alert, reviewed ceiling `MAX_AUTHORISED_TIER` = T1, no tier
  permits upsizing); `fiboki agents tier status|set` (set: operator, reason, real secret, refuses
  above the ceiling, audited to `agent_tier_audit.jsonl`); conviction applies only at T3+, the
  event veto blocks only at T2+ (`TierGatedVetoSource`), otherwise shadow plus one alert.
- **Thesis debate:** `build_market_brief` (deterministic, content-hashed, stable evidence ids),
  `ThesisStore` (append-only SQLite), `record_debate_turn`, `get_debate`, `record_conviction`;
  capabilities `WRITE_DEBATE_TURN`, `WRITE_CONVICTION` (25 total), write domains
  `research:debate`, `research:conviction` (11), roles `thesis_advocate`, `thesis_arbiter` (15),
  33 tools; `run_thesis_debate` with `offline_thesis_script`; `thesis_debate_due` predicate.
- **Budgets:** per-role context budgets (G §3.4.3) enforced before the request and rendered into
  the system prompt (so versioned in the run manifest); sessions bound prompt tokens,
  completion tokens and model seconds as well as USD (P2-22); `ToolContext.model_id` / digest /
  manifest hash set from the router's choice after every thought.
- **Kill switch:** `build_replay_session` builds its gateway with `mode=PAPER` and
  `KillSwitch.from_paths(resolve_paths(os.environ))` (the operator's durable journal) unless a
  gateway or kill switch is passed.

Tests, run 2026-09-29 (commands and counts in the agent report): new
`tests/unit/test_agent_tier.py`, `test_construction_policy.py`, `test_conviction_channel.py`,
`tests/golden/test_golden_construction.py`, `tests/integration/test_runtime_sizing_path.py`,
`test_agents_thesis_debate.py`. Updated with reasons: `test_portfolio_construction.py` (two tests
whose premise was the 1.5x vol scale-up), pinned counts in `test_agents_forecasts.py`,
`test_agents_roles.py`, `test_agents_tool_registry.py`.

Not done: the backtest engine does not mirror construction (B-24), so **paper and backtest size
differently** for the same signal; nothing schedules `run_thesis_debate`
(`workers/research_runtime.py`); `entrypoints/paper_forward.py` (another agent's file) builds
`SignalEvaluator` without `snapshot=builder.snapshot`, `strategy=builder.strategy_view` or a
regime source, so it allocates against an unmeasured book; the tier does not gate agent writes;
no drawdown hysteresis; RISK_GOVERNANCE's upward multipliers are deliberately not implemented.

Stored results: **paper sessions sized before this change are not comparable** with sessions
after it (sizes now 0.25% x weight for every strategy instead of the flat `risk_fraction`).
Backtest and research results are unaffected (the engine did not change). The run manifest hash
moves for every role (the context-budget line is in every system prompt).

## 2026-09-29: realism corrections from the backend audit; `engine_v3_realism` (P1-1, P1-2, P1-3, P1-5, P1-16, P2-1, P2-2, P2-6, P2-7, P2-9, P2-11, P2-12, P2-13, P3-1..4, P3-9, E-1) (uncommitted working tree)

**Stored results superseded.** `ENGINE_VERSION` moves from `engine_v2_exit_vocabulary` to
`engine_v3_realism`; `backtest/version.ENGINE_V3_REASONS` lists every reason. Every change below
can move a stored number; none is a refactor.

- **P1-1 leverage** (`core/instruments.py`): FCA PS19/18 / ESMA 2018/796 caps by the currency set
  {USD, EUR, JPY, GBP, CAD, CHF}. AUDUSD, NZDUSD 30 -> 20; EURGBP, EURJPY, GBPJPY, EURCHF, CADJPY,
  CHFJPY, GBPCAD, GBPCHF, EURCAD, CADCHF 20 -> 30 (and `FX_MAJOR` now means the regulatory
  major); XAGUSD 20 -> 10; HK50 20 -> 10. Golden over all 41 instruments.
- **P1-3 price basis** (`backtest/engine.py`, `validation/run.py`, `validation/engine_evaluator.py`):
  the engine refuses `bid`/`ask`/`last` frames and records `price_basis` per instrument in the data
  fingerprint (`assumed_mid` for an unlabelled frame; `BacktestConfig.assume_mid=False` refuses
  one). `run_validation` converts BID bars with `bid_to_mid` at the registered typical spread and
  the lineage enters the engine fingerprint and cache keys; ASK is refused.
- **P1-16 sizing** (`backtest/engine.py`, `portfolio/sizing.py`): `fixed_fractional_v2` is the
  default in both sizers: risk per unit = stop + spread + 2 x E[slippage] (`stop_out_cost_per_unit`,
  `sim/profiles.expected_slippage_price`), cost profile named or IG_REALISTIC by default in both,
  recorded in `TradePlan.sizing_basis` and in every result's `config_fingerprint["sizing"]`. The
  gateway's `max_per_trade_risk` reads `plan.risk_amount`, which now includes the costs (verified
  by `tests/golden/test_golden_sizing_v2.py`). `FixedFractionalSizer` clips a requested
  `max_leverage` to the instrument's cap, as `SizingPolicy.leverage_for` always did.
- **P2-7 financing** (`backtest/position.py`): `financing_nights` weights business-day rollovers,
  triple on Wednesday for FX and metals, Friday for indices, energy and equities, nothing on
  Saturday or Sunday, every night for crypto; the `nights_between` docstring corrected. Shared by
  the paper broker through `PositionBook`, so paper financing changes identically.
- **P2-9 min stop** (`sim/profiles.py`): `MinStopRule` per asset class; IG_REALISTIC 4 pips on FX
  (unchanged) and 3 x typical spread elsewhere, SEVERE_STRESS 10 pips / 6x; profile fingerprint
  `profile_v2`.
- **P2-13 Sharpe** (`backtest/metrics.py`): `sharpe` on 17:00-New-York daily equity;
  `sharpe_lo_adjusted` (Lo 2002, Newey-West lag cap), `sharpe_bar_based` (the old figure).
- **P3-4**: the engine requires a UTC index.
- **P1-5 GBP research** (`validation/engine_evaluator.py`, `validation/run.py`,
  `discovery/campaign.py`, `core/money.py`): `EvaluatorConfig`, `run_validation` and
  `CampaignSpec` default to GBP. `build_research_fx_source` builds a `SeriesFxSource` from the
  store's validated D1 GBP crosses (closes indexed at bar close; one USD triangulation leg; refusal
  lists every instrument to ingest); `run_validation(fx_store=...)` and a `CampaignRunner` over
  `store_bar_source` build it automatically. `SeriesFxSource.max_staleness` 7 -> 4 days (P3-3).
- **P1-2 agent DSR** (`agents/jobs.py`, `research/experiment.py`): `N` from
  `ExperimentLedger.count_trials` (family and campaign, payload may only raise it, unknown is
  `NOT_EVALUATED`); null variance from Lo's (2002) `(1 - g3 SR + (g4-1)/4 SR^2)/(T-1)`.
  `register_research_handlers(experiments=...)`.
- **P2-11 WFE** (`validation/ladder.py`): log growth per day where the evaluation records
  `opening_equity` (the engine evaluator now does); per-fold basis recorded.
- **P2-12 plateau** (`stats/stability.py`): `(s + |s|) / (m_excl + |s|)`; the old figure kept as
  `point_plateau_ratio_inclusive`. Threshold unchanged (1.25 == neighbours keep 60%).
- **P2-1, P2-2** (`risk/limits.py`, `risk/gateway.py` two functions): new sets
  `limits_v2_default`, `limits_v2_paper`, `limits_v2_conservative` with the blackout
  `[decision - 15m, decision + max(30m, one bar)]` measured from the signal bar's close, and
  margin utilisation after the trade. v1 sets and their fingerprints are byte-identical.
- **P2-6** (`sim/fills.py`): `FxSessionCalendar` anchored to 17:00 America/New_York.
- **P3-1** (`marketstate/features.py`): demeaned, blockwise rolling OLS (einsum, so a row does not
  depend on how many rows follow it).
- **P3-2** (`stats/bootstrap.py`, `spa.py`, `stress.py`): every `rng` required; `None` refused.
- **P3-9** (`data/store.py`): `end_inclusive`; `end` kept as an alias.
- **E-1**: `research/preregistration/gate_calibration_e1.json` (draft, unfiled) and
  `scripts/gate_power_study.py` (synthetic process only; the two evidence processes raise
  `NotImplementedError`). No gate threshold changed.

Stored results superseded:

1. Every backtest record stamped `engine_v2_exit_vocabulary` or unstamped (sizing, financing,
   leverage caps, minimum stops); `ResearchStore.sweep_superseded_backtests` marks them.
2. Every research result computed on HistData (BID) frames: about half a spread per round trip,
   directionally flattering to longs.
3. Every stored `Metrics.sharpe` (definition changed to daily equity).
4. Every stored `ValidationReport`: walk-forward efficiency (basis), plateau ratio (definition),
   and every result computed in USD (not comparable with GBP paper figures).
5. Every DSR stored by the agent `validation_handler` (invalid, audit P1-2).
6. Every discovery campaign report and ledger row produced under a USD account.
7. Every `EngineEvaluator` cache entry (keys move automatically: engine fingerprint, profile
   fingerprint v2, `sizing_policy`, `price_basis`).
8. Paper sessions sized or financed before this change are not comparable with sessions after it
   (`SizingPolicy()` is now v2; paper financing uses the same book).
9. Stored market-state trend features (`trend_slope/r2/tstat`) on long high-priced series where
   the cancellation was material.
   Not superseded: risk decisions under v1 limit sets; any statistic from a seeded caller (P3-2);
   anything using `FxSessionCalendar` (no production caller passes it).

Regression pins updated deliberately, with the reason in the diff:
`tests/golden/test_golden_pnl.py` (two financing goldens: Wednesday triple, 2.64 -> 4.40 GBP and
5.28 -> 8.80 GBP), `tests/integration/test_locks_regression_pin.py` (five ledger/leg hashes and the
five exit-policy fingerprints, which embed `ENGINE_VERSION`; trade counts, rejections and signals
unchanged), and v1-arithmetic sizing tests now select `fixed_fractional_v1` explicitly.

Tests, run 2026-09-29: `ruff check src/ tests/ scripts/gate_power_study.py` clean; 156 test modules
importing the touched packages plus `tests/golden` and `tests/unit/test_layering.py`: 3697 passed,
23 skipped, 0 failed.

Not done (files outside this change): `workers/runtime.py` and the API still select the v1 limit
sets; `workers/research_runtime.py` does not pass `experiments=` so the agent DSR is
`NOT_EVALUATED` in production; `agents/tools.StoreBarSource.load` does not convert BID bars, so
agent backtests on HistData frames are now refused by the engine; agent backtest/validation
payloads still default to USD; `backtest/locks.py` counts sessions on a fixed 22:00 UTC weekend;
the `parameter_plateau` rationale in `validation/gates.py` still describes the old ratio;
P2-3 (regime per plan instrument) and P2-10 (IG vs OANDA profile) untouched.

## 2026-09-29: round 4 integration, cross-agent leftovers closed (uncommitted working tree)

Closes the "Not done" list above and the api agent's `charged=` note.

- **Durable research jobs** (`cli.py`): `fiboki worker run research` opens
  `Orchestrator(path=<FIBOKI_STATE_DIR>/jobs.sqlite)` (`research_jobs_ledger_path`), not an
  in-memory ledger, so queued jobs, attempts and idempotency keys survive a launchd restart.
- **Doctor** (`cli.py`): the launchd check lists `uk.fiboki.paper` (optional: "not loaded" is not
  a warning, loaded-and-not-running is); a new `operator hashes` check flags legacy unsalted
  SHA-256 entries in `FIBOKI_OPERATORS` via `auth.is_legacy_hash` (names users, never hashes);
  the heartbeat check reads `Settings.health` through the new
  `api.settings.health_thresholds_from_env` (also used by `load_settings`: one parser).
- **One limit-set source** (`risk/limits.default_limit_set(mode)`): BACKTEST/PAPER
  `limits_v2_paper`, SHADOW/DEMO `limits_v2_default`, LIVE `limits_v2_conservative`.
  `workers/runtime.py` (builder and `build_replay_session` defaults) and the API
  (`api/platform.py`, one line: the hard-coded `limits_v1_paper` was there, not in the routers,
  and every router reads `platform.limits`) select through it. The paper-forward wiring still
  names `limits_v1_paper` explicitly: that file is reviewed and sha-stamped, so it was left.
- **No benign defaults** (`workers/runtime.RiskContextBuilder`): no P&L ledger -> `daily_pnl` /
  `weekly_pnl` `None`; no correlation matrix -> `correlated_exposure` `None`; no market-open
  source -> `market_open` `None` (blocks). The replay sources market-open from its own data
  (`replay_market_open_source`: a bar at `now`), so fills still follow the engine's calendar.
  PAPER compositions (replay and paper forward) build the gateway with
  `paper_allows_missing_inputs=True`; the replay summary records it and `limits_version`.
  `open_risk` is priced with the evaluator's sizing rule (`fixed_fractional_v2`: stop distance +
  spread + 2 E[slippage]), the same definition as `TradePlan.risk_amount`.
  `event_source_horizon_minutes` widens the calendar source to the v2 window
  (`bar + max(pre, bar)` ahead), so a v2 blackout is not hidden by a 15-minute source.
- **Paper forward** (`entrypoints/paper_forward.py`): `SignalEvaluator` gets
  `snapshot=builder.snapshot`, `strategy=builder.strategy_view`, `regime=builder.regime_source`
  (none wired: "unknown"); the gateway is `mode=PAPER`; the builder carries the evaluator's
  sizing policy; `data_stale_after_seconds` comes from `Settings.health`. Credentials are
  `FIBOKI_OANDA_PRACTICE_TOKEN` / `FIBOKI_OANDA_PRACTICE_ACCOUNT_ID` (declared in
  `ENV_REGISTRY`); the old `OANDA_PRACTICE_*` names are read for one release when the new ones
  are unset, with a warning naming the variable (never the value).
- **Research runtime** (`workers/research_runtime.py`): the research store's
  `ExperimentLedger` is passed as `experiments=` to every handler, so the agent DSR is
  evaluable. Thesis debates are scheduled per instrument per `FIBOKI_THESIS_DEBATE_TIMEFRAME`
  (`H4` default, or `D1`) close via `thesis_debate_due`, when agent cycles are on and
  `FIBOKI_THESIS_DEBATE_INSTRUMENTS` is set (empty by default: opt-in, a new model workload per
  bar is not switched on by an upgrade). Claimed before running, not fired on first start, a
  missed run of many closes runs once; store `<FIBOKI_STATE_DIR>/agents/thesis.sqlite`.
- **Agent bars and currency** (`agents/tools.py`, `agents/jobs.py`): `DataStoreBarSource` and
  `InMemoryBarSource` convert BID frames with `validation.run.research_mid_frame` (lineage on
  `frame.attrs["price_lineage"]`); payloads default to `GBP` (`jobs.DEFAULT_ACCOUNT_CCY`). A GBP
  default without FX would have dead-lettered every USD-quoted default backtest, so both bar
  sources expose `fx_source(account_ccy, quote_ccy)` (THE research
  `build_research_fx_source` over their D1 crosses) and `jobs._fx_source` tries it first; the
  1.0 approximation still needs `fx_approximation_acknowledged`, and a refusal names the pairs
  to ingest. This goes beyond "currency default" in `jobs.py` by one parameter; it is what makes
  the default coherent. Test fixtures gained a flat GBPUSD D1 series (`gbp_fx_frames`).
- **Live worker** (`workers/live_worker.py`): `data_stale_after_seconds` defaults to
  `DEFAULT_HEALTH_THRESHOLDS.data_stale_after_seconds`; no second 900.
- **Locks** (`backtest/locks.py`): the session clock is anchored to Friday 17:00 to Sunday
  17:00 America/New_York from `sim.fills.FxSessionCalendar` (per-week closures, so DST and the
  two changeover weekends are exact); lock fingerprint calendar id
  `session_bars:interbank_weekend:fri17-sun17_america_new_york`. Winter results are unchanged.
- **Gates** (`validation/gates.py`): the `parameter_plateau` rationale states
  `(s + |s|) / (m + |s|)`. The rationale is part of `GATE_SET_V2.fingerprint()`, so the gate-set
  fingerprint moved; reports computed under the old plateau definition were already superseded.
- **API** (`api/routers/trading.py`): `/api/trading/trades` rows pass `charged` from the row's
  paper session (`Platform.session_charged_costs`), so a caveat the record contradicts is dropped.
- **Tests** (`tests/conftest.py`): an autouse fixture points `FIBOKI_STATE_DIR` at a temporary
  directory for every test; no test reads `./var/killswitch.jsonl`.
- **Docs**: `deploy/README.md` (installer-based launchd, `uk.fiboki.paper`, legacy plist removed,
  exit 75's two meanings); `AGENTIC_INTEGRATION_PLAN.md` §2 D-A4 addendum (ForexFactory opt-in,
  comparison only, terms flagged).

Stored results superseded: paper replays (limit set v2, cost-inclusive open risk feeding the
construction budgets, market-open now sourced); any lock-bearing backtest or paper decision
whose lock spanned a daylight-saving week (exit-policy fingerprints of lock-declaring documents
move with the calendar id); agent backtests run under the USD default (not comparable with GBP
research); every agent validation whose DSR was NOT_EVALUATED for want of the ledger.
Not superseded: v1-limit decisions, documents without locks (pins unchanged), winter lock timing.

## 2026-09-29: research sizes as paper sizes; the engine allocates through portfolio construction (audit F B-24) (uncommitted working tree)

Paper sized every bar's signals through portfolio construction; the backtest engine still used a
flat `risk_fraction`. A research figure and a paper figure for the same signal on the same book
therefore disagreed on size, P&L and drawdown by construction.

- **The seam.** `backtest` (rank 50) may not import `portfolio` (rank 60), so the engine declares
  a `ConstructionPolicy` Protocol (`ConstructionRequest` in, `AllocationDecision` out) and
  `portfolio/engine_policy.BacktestConstructionPolicy` satisfies it. `BacktestConfig.construction`
  defaults to `None` at the ENGINE (the flat path, byte-identical to every existing pin, and
  fingerprinted `construction=none`), because an engine-level default would need the upward
  import. Every validation entry point defaults to the paper runtime's policy instead
  (`validation.engine_evaluator.research_construction_policy`; `EngineEvaluator.construction`,
  `run_validation(construction=...)`). No `ENGINE_VERSION` bump: the policy's fingerprint
  (construction version, a digest of every `ConstructionConfig` number, allocator, tier, sources)
  is in `BacktestConfig.fingerprint()` and so in every result and every evaluation cache key.
- **The engine** offers each decision bar's signals that survive the reversal and lock filters to
  the policy together, against its own book (margin marked as `PaperBroker` marks it; open risk
  cost-inclusive under the sizer's profile, as the runtime now measures it; its own equity curve
  for realised vol), then sizes each accepted one ONCE with the existing sizer at
  `risk_fraction = base_risk_pct / 100`, `portfolio_weight = weight`
  (`FixedFractionalSizer.allocated`; `portfolio_weight` evaluated in `size_trade`'s order, and
  exactly 1.0 by default). Every decision is a row of `BacktestResult.allocation_ledger` (compact
  JSON: tier, base risk, weight, every step's factor and detail, size, outcome) and each trade row
  links to its decision (`trade_allocations()`, an `allocation` column on `ledger_frame()`).
  Refusals are counted as `allocation_dropped`.
- **No conviction in a backtest**, by construction (plan D-A3): no engine, config, request or
  policy field can carry one, `CandidateSignal(conviction=None)` is a literal (AST-tested), the
  step reads `missing` at factor 1.0 even with the conviction policy enabled.
- **Stated in the fingerprint:** regime `unknown` (x0.6) unless a regime source is supplied (the
  engine has none), lifecycle PAPER, health 1.0, tier PROBATIONARY, instrument correlation
  unmeasured (0.30) unless a matrix is supplied, instrument vol not computed under equal risk (only
  volatility parity reads it).
- **Parity** (`tests/integration/test_construction_parity.py`): the engine, the runtime's own
  `SignalEvaluator` + `RiskContextBuilder.snapshot` over a `PaperBroker`, and `build_replay_session`
  end to end (worker, gateway with widened loss limits and every attempt asserted allowed,
  execution service) produce byte-identical size-and-reason, trade and leg ledgers on two
  instruments over 1,500 bars; de-risk, severe, PAUSE and FLATTEN-required each fire, in order, on
  the same bar on all three paths.
- **Pins:** `test_locks_regression_pin.py` unchanged (the `None` path). New
  `tests/golden/test_golden_construction_engine.py` pins the five seed documents under the
  default policy (trade counts and signals seen equal to the flat pins; every size moves; the flat
  run's `max_concurrent` refusals become `allocation_dropped`) with one trade worked by hand
  (0.25% x 0.6 of 100,000 = 150.00 over 9.10437 per unit = 16 units, flat path 109).

Stored results superseded (sizing-dependent metrics only: P&L scale, drawdown, ruin, gates on
them; not hit rate or trade counts except where construction refused an entry): every stored
`ValidationReport`, discovery campaign report and ladder evaluation produced before this change,
and every `EngineEvaluator` cache entry (its key moves automatically). See VALIDATION_STANDARD §10.

Caveats: a replay session measures correlation over the whole replayed window (look-ahead) while
the engine uses the unmeasured default unless handed a matrix; a locked signal is refused before
allocation in the engine and after it by paper's gateway; the replay runtime schedules no
reversals; `agents/jobs.py` builds a `BacktestConfig` without construction (flat path) and is
outside this change; the runtime still calls its own copies of the snapshot arithmetic
(`workers/` outside this change), held together by the parity test. Allocation costs about 5 ms
per signal on a 3,200-bar run, mostly the realised-vol estimate the runtime also makes.

Tests, run 2026-09-29: `ruff check src/ tests/` clean; the 69 test modules importing
`fiboki.backtest`, `fiboki.validation` or `fiboki.portfolio` (three of them new) plus
`tests/unit/test_layering.py`: 1370 passed, 0 failed; the parity suites
(`test_construction_parity`, `test_paper_backtest_parity`, `test_venue_position_manager`,
`test_lock_parity`, `test_seed_binding_parity`, `test_runtime_sizing_path`,
`test_locks_regression_pin`): all passed.

Tests, run 2026-09-29: `ruff check src/ tests/ scripts/` clean; full suite
`FIBOKEI_WORKER_EXTERNAL=true .venv/bin/python -m pytest -q -p no:cacheprovider -m "not network"
--timeout=300`: 4802 passed, 24 skipped, 0 failed (17m25s).

Not done (outside this change's files): `docs/v2/OPERATIONS.md` §13.6, `scripts/fiboki-service.sh`
and the `uk.fiboki.paper.plist` comment still name `OANDA_PRACTICE_*` (the fallback keeps them
working for one release); the paper-forward wiring still selects `limits_v1_paper`.

## 2026-09-29: the discovery campaign script under `engine_v3_realism`; K3 pre-registered (uncommitted working tree)

What was wrong: `research/run_discovery_campaign.py` still ran K1/K2's configuration. It
hard-coded a USD account with its own H4 bid-close FX table while `run_validation` and
`CampaignSpec` default to GBP; it had no calendar option, so a campaign over K2's universe (from
2000) against the official calendar (from 2024-01-01) would have refused every cell; it did not
record the engine version, construction policy or calendar coverage anywhere; and nothing stopped
a stale checkout from producing a report under a new campaign id.

What changed (script only; no change to `validation/run.py`, the calendar module or
`discovery/campaign.py`):

- `--account-ccy GBP|USD` (GBP default): GBP builds FX with `build_research_fx_source` over the
  store's D1 GBP crosses and refuses (exit 3) listing the pairs to ingest; USD keeps the old
  `FX_SERIES_FOR` table for reproducing K1/K2. `fx_coverage.json` records per pair the first and
  last known rate and gaps over `max_staleness`, and per instrument any bars before the first or
  after the last usable rate.
- `--calendar official|none` (official default). Official is "enforce where covered":
  the official calendar stays the blackout source, `allow_empty_calendar` is set only when a series
  is partly uncovered (it lifts `run_validation`'s coverage refusal and nothing else), the share of
  bars inside the declared span and the currencies not carried are written per series to
  `calendar_coverage.json`, run.log and the campaign notes, and a series with bars after the
  declared end or with no carried currency is refused (exit 4). None passes an empty calendar and
  says in the notes that no blackout was enforced.
- `--engine-version-check [VERSION]` refuses (exit 2, before creating `--out`) unless
  `ENGINE_VERSION` is `engine_v3_realism` or the version given.
- The script writes `<out>/run.log` itself (appending); each invocation opens with the effective
  configuration: engine version, account currency, FX label, calendar and coverage range,
  construction policy (`construction_v2`, `run_validation`'s default), gate set, universe, budget.
  The notes carry the engine version, construction policy and calendar statement too.
- Seed and hypothesis directories resolve from the script's location, not the working directory.
  `CalendarPlan` is a NamedTuple because `scripts/build_research_ledger.py` loads the script by
  path without registering it in `sys.modules`, where a dataclass cannot be created.

K3 is pre-registered in `research/reports/RESEARCH_LEDGER.md` (campaign
`k3_multi_instrument_h4_engine_v3`, external prior trials 4,026, the exact Mac command).

Two FX blockers for K3 closed in the same change (`core/money.py`, `validation/run.py`
`build_research_fx_source` only):

- **No D1 in the store** (the migration stored H4 and H1). When a pair has no validated D1,
  `build_research_fx_source` reduces its validated H4 (else H1) bars to one rate per UTC day with
  `money.daily_rates_from_intraday_closes`: the last bar to close that day, stamped at that bar's
  CLOSE (a bar closing at midnight belongs to the day it ends). D1 is still preferred. The label
  tags the pair `[derived:H4 last close per UTC day]` and `source.lineage[pair]` records the
  dataset version, source timeframe and derivation; the script copies it to `fx_coverage.json`.
- **GBP crosses start after the instruments** (GBPJPY 2002, GBPCAD 2007 against USDJPY and USDCAD
  from 2000). `SeriesFxSource(fallback_via_pivot=True)`: a LOADED direct pair that has no
  observation yet or none within `max_staleness` falls back to quote->USD x USD->GBP, each leg
  staleness-checked; the direct pair wins whenever it is fresh; if both fail the direct error is
  raised with the pivot's appended. `rate_with_route()` returns the route per conversion
  (`identity`, `direct`, `inverse`, `via_usd`) and `route_counts` tallies them (a diagnostic: a
  cached evaluation makes no lookups). Off by default, so paper and every other caller are
  unchanged; `build_research_fx_source` turns it on and loads the USD legs (USDxxx, GBPUSD) when
  the store has them, recording in `lineage['_fallback']` and the label which fallbacks exist. The
  script's `fx_coverage.json` now counts, per series and at bar open, bars converted directly, via
  USD, or not at all, with the same as-of rule. The label text changed (`daily closes`, the
  fallback clause), so every research evaluation cache key built on a GBP FX source moves.

New tests: `tests/golden/test_golden_fx_via_usd.py` (5: JPY->GBP via USD before the cross
starts, 30,000 JPY = 160.00 GBP by hand; the direct cross wins when fresh; a gap over the
staleness guard falls back and returns; each leg's staleness is checked; the flag off keeps the
old refusal), `tests/unit/test_research_fx_intraday.py` (8: the per-day reduction and midnight
rule; look-ahead, no derived rate readable one second before its producing bar closes; D1
preferred over H4, H1 used last; refusal still names the pair; the USD legs load and answer
before a late cross; a missing leg leaves the direct route only), and a script test that
`--plan-only` exits 0 on a store holding only H4 for the GBP crosses with a late GBPJPY.

Tests, run 2026-09-29: new `tests/integration/test_run_discovery_campaign_script.py` (8: the
run.log header, GBP account, FX label and report through `main()` with `--max-evaluations 2`; the
per-series covered fraction recomputed from the stored bars; stale engine refused with nothing
written; missing GBP cross refused naming GBPUSD; `--calendar none` notes; bars after the
declared end refused; legacy USD account; one real-engine cell straddling 2024-01-01 reaching rung
0 instead of a CalendarError; `--plan-only` exit 0 on H4-only late GBP crosses). `ruff check
src/ tests/ research/run_discovery_campaign.py` clean. Every test module importing
`fiboki.core.money`, `fiboki.validation.run` or `fiboki.discovery` (38 modules), the three new
modules, `tests/unit/test_layering.py` and `tests/api/test_experiments_view_reads_the_ledger.py`
(loads the script by path): 562 passed, 2 failed, both the same new test collected twice, on an
assertion about label punctuation in the test itself; corrected, then
`test_research_fx_intraday.py`, `test_golden_fx_via_usd.py` and `test_research_fx.py`: 30 passed.

Stored results: none invalidated by this change (it is a runner); K1 and K2 were already
superseded by `engine_v3_realism`.

## 2026-09-29: the MacBook runs the four services under launchd; doctor reads `~/.fiboki/env`

Commits `8df7694`, `cf70c41`, `076fffe`, `2d4b968`, `b2d8fcc` and this one. Every line below
was run on the MacBook (M1, 8 GB) or in the build container as stated.

**What was wrong, in the order it was found.** (1) `launchctl bootstrap` of `uk.fiboki.*`
against the checkout under `~/Documents/Claude/Projects/Fiboki` failed with `Operation not
permitted` before `fiboki-service.sh` ran a line: macOS TCC protects `~/Documents` from
LaunchAgents. Decision: a second, runtime-only checkout at `~/fiboki` (`DEPLOYMENT.md` §2.6).
(2) The service script sourced `~/.fiboki/env`; the new scrypt operator hashes contain `$` and
`set -u` aborted on `$8: unbound variable`. Fixed in `076fffe`: values are quoted in the file and
the script reads it line by line with no expansion. (3) `npm: not found`: node is installed through
nvm, which launchd's minimal `PATH` does not see. Fixed in `2d4b968`: the script adds the newest
`~/.nvm/versions/node/*/bin`. (4) `launchd-install.sh --load` bootstrapped a label while its
previous instance was still finishing its cycle after SIGTERM (`Bootstrap failed: 5`), and
`set -e` then skipped the remaining services. Fixed in `b2d8fcc`: it polls `launchctl print`
for up to 90 s before bootstrapping. (5) Two wrapper tests still asserted the old `. "$ENV_FILE"`
line; they now assert the reader and that the file is never sourced, with a behavioural test of
the reader block against quoted, `$`-laden and malformed lines. (6) Old `dev-up` processes from
two days earlier (uvicorn on 8000, a research worker holding the lease, a Next 14 dev server on
3000) were stopped with SIGTERM; the K3 campaign process was not touched. (7) `fiboki doctor` from
a bare shell reported `FIBOKI_OPERATORS is not set` and an unnamed local model on a machine
whose API process had both: doctor did not read `~/.fiboki/env`. New `fiboki.core.env_file`
(pure parser with the shell reader's rules; `read_env_file`; `env_file_path`) and `DoctorHost`
fills unset names from the file, process environment winning; the `environment` row now says
how many values came from the file and names malformed lines (WARN).

**Verified on the MacBook after the reload.** `launchctl list`: api pid 94375, worker 98307,
news 98314, web 94617 (its `127` is the last exit before the PATH fix; it is running).
`curl` 200 on `/api/health` and on `/`; `lsof` shows Fiboki's own pids on 8000 and 3000; the
worker resumed and holds the lease (heartbeat 1 s old); the news loop fetched 312 headlines,
0 errors; `next start` ready in 173 ms. `fiboki doctor` in `~/fiboki`: 16 OK, 5 WARN, 0 FAIL.
The WARNs: operator hashes and local model (both false, closed by this commit's doctor change),
paper journal empty in the runtime `var/` (correct: nothing has traded forward), disk 14 GiB
free, `llama`/`paper` services not loaded (no llama.cpp model on 8 GB; no OANDA token).
`ps eww` on the API pid lists `FIBOKI_OPERATORS`, `FIBOKI_SESSION_SECRET` and the agent
variables (names only were printed).

Tests (container): `tests/unit/test_core_env_file.py` (4: no expansion and malformed keys; a
missing file is empty, not an error; the path follows `FIBOKI_HOME`/`HOME`; the Python and bash
readers agree on one sample), `tests/unit/test_cli_doctor.py` (+2: the file fills unset names and
the process wins, `$` intact, malformed line WARNs, operator row no longer says "not set"; no file
says so and `env_file=False` skips it; `FakeHost` now pins `HOME` under the temp repo so the
developer's own `~/.fiboki/env` can never leak into a test), `tests/unit/test_desktop_scripts.py`
(+1 reader behaviour). `mypy` clean on the new module; `ruff check` clean. Full suite: see the
figure in the commit message.

Stored results: none affected. Docs: `DEPLOYMENT.md` §2.5, §2.6 (new), `OPERATIONS.md` §13.3,
`USER_ACTIONS.md` (C1, C4, M2, P7 marked done with evidence; status line).

**First real-model workflow, and the 8 GB constraint (same evening).** The runtime worker's
audit ledger (`~/fiboki/var/agents/audit.jsonl`) holds the first agent workflow ever run against
a real model: `wf_event_scan_20260929T1230Z`, role `event_classifier`, provider `local`,
`model_digest sha256:359d7dd4...` (qwen3:4b), `query_news` then `record_event_annotations`
with `quarantined: true`. Of five model calls, one completed (4,024 prompt tokens, 133
completion, 39.6 s) and four timed out at the 300 s read timeout. Cause, from `vm_stat` and
`sysctl vm.swapusage` on the MacBook: 8 GB unified memory, ~5 GB wired while the model is
loaded (weights are wired for Metal), swap 14.8 of 15.4 GB in use, 13% free; K3, Chrome, the
four services and the Claude app share the remainder, so the runner is evicted between calls.
The 5 GB drop in free disk that `fiboki doctor` reported was the swap file growing, not Fiboki.
Operator change on this machine: `FIBOKI_EVENT_SCAN_MINUTES=60` (was 15) in `~/.fiboki/env`,
worker kickstarted and verified reading it; the nightly cycle at 02:15 UTC is unchanged. This
is a hardware limit, not a defect: the platform's working set with a campaign running is above
8 GB, which is why the plan puts it on the desktop. Agent quality remains unmeasured (one
completed call is not a sample).
