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
