# V1 Forensic Baseline

**Status:** frozen record. This document describes Fiboki V1 as it was found, and is not
updated as V2 develops.
**Primary source:** `FIBOKI_V2_AUDIT_AND_OVERHAUL.md`, dated 19 September 2026, audited
commit `ccc3af2` plus six uncommitted working-tree changes. Findings attributed to that
audit carry its file:line citations; findings marked **(V2 rebuild)** were discovered
during this rebuild and are cited to V2 source or tests.
**Purpose:** to make the failure modes checkable rather than remembered, so that a V2
component can be read against the specific defect it exists to prevent.

---

## 1. Why this record exists

V1's most expensive defect was not any single bug. It was a documentation culture in which
aspirations were written in the indicative mood. Four separate status documents asserted
that the backend test suite passed; the most recent one said the suite could not complete
offline. `LIVE_READINESS_REPORT.md` issued a "GO" verdict on 615 passing tests that had
been green only because rogue worker threads reached the live internet. The project's own
non-negotiables asserted deterministic backtests (dependencies were unpinned and no
lockfile existed), mandatory portfolio-aware risk controls (the risk engine had zero call
sites in production code), and a minimum of 80 trades for primary ranking (the operator
leaderboard ranked a combination with two trades at position twelve).

The rule this document is written under, and which `AGENTS.md` carries forward, is that
nothing may be stated as verified unless it was verified in the session that wrote it.

## 2. Scale and shape

| Measure | V1 |
|---|---|
| Backend Python | 31,794 lines across 16 packages |
| Frontend TypeScript | 19,953 lines across 80 files |
| Backend tests | 103 files, 976 test functions |
| Playwright specs | 10, never run in CI |
| Markdown documents in `docs/` | 52 |
| Registered strategies | 64 |
| Instruments defined / with canonical data | 67 / 60 |
| HistData parquet | 7.2 GB |
| Research grid | 23,040 combinations, of which 25 were completed |
| Lifecycle ledger events | 83 (60 rejected, 8 validated, 12 backtested, 3 generated, 0 promoted to paper) |

No candidate was ever promoted to a paper bot. `backend/.env` did not exist, so the
survivor-publish path to production had never run and the production Candidates page was
empty by construction. The last production deploy was 25 June 2026; nothing shipped in the
twelve weeks that followed.

## 3. Component classification matrix

KEEP means the idea and the code carry forward. REFACTOR means the idea carries forward and
the code does not. REWRITE means the component was rebuilt from its contract rather than
its implementation. RETIRE means the component does not exist in V2. ARCHIVE means it is
preserved unused, for reference or for optionality.

| V1 component | Verdict | One-line reason |
|---|---|---|
| Declarative strategy factory (43 specs, content-hashed, no duplicate rule signatures) | **KEEP** | The best idea in V1; V2's `strategy/dsl.py` is its successor with a mandatory hypothesis and declared parameter domains. |
| Hardcoded IG demo base URL | **KEEP** | A compile-time impossibility outranks any number of flags; preserved verbatim in `broker/ig.py` and strengthened with a parsed-hostname assertion. |
| Execution adapter abstraction | **KEEP** | The interface was right; V2's `broker/base.py` adds only the two prohibitions (an adapter may not size, may not decide risk). |
| Signal → attempts audit schema | **KEEP** | The shape of the audit trail was sound; V2 extends it with a pre-dispatch intent record. |
| Frontend operator-honesty cues (assumptions panel, zero-trade warning, diagnostics, LEGACY badge, validation funnel) | **KEEP** | More honest than most commercial platforms; the fault was that the data was less honest than the prose. |
| Backtest engine (`backtester/engine.py`) | **REWRITE** | Entry-only spread, exact-level fills through gaps, realised-only equity, one position at a time — every defect was structural, not incidental. |
| Metrics (`metrics.py`) | **REWRITE** | `sqrt(252)` unconditional annualisation and NaN-maps-to-cap are not patchable without changing what every stored number means. |
| Research scorer (`research/scorer.py`) | **REWRITE** | Sign-blind stability scoring rewarded smooth losers; the composite was not repairable in place. |
| Research pipeline (`research/pipeline.py`) | **REWRITE** | Selection and validation ran on the same DataFrame; the fix is a different program, not a different line. |
| Walk-forward (`research/walk_forward.py`) | **REWRITE** | It fitted nothing, so it measured nothing about overfitting. |
| Monte Carlo (`monte_carlo.py`) | **REWRITE** | Frozen position sizes answer the wrong question; V2's `stats/bootstrap.py` re-sizes off running equity. |
| Indicators (`indicators/`) | **REFACTOR** | Mostly correct; three genuine causality breaks (centred swing window, `close.shift(-26)` chikou, cloud displaced by `chikou_shift`) had to be designed out. |
| Position sizing (`sizing.py`) | **REWRITE** | Cross-currency conversion was a no-op outside JPY; three independent sizing authorities existed. |
| Risk engine (`risk/engine.py`) | **REWRITE** | Zero call sites in production code; V2's gateway is mandatory and proven so over the AST. |
| Paper trading engine (`paper/`) | **RETIRE** | A second, separately written execution path is the defect; V2's paper broker drives the backtester's own fill model. |
| Paper worker (`worker.py`) | **REWRITE** | No leasing, no position recovery, no idempotency, 22% test coverage on the process that actually trades. |
| IG adapter (`execution/ig_client.py`) | **ARCHIVE** | Better-engineered than the community alternative, but IG's 10,000-datapoint weekly allowance is two orders of magnitude short; kept dormant behind the V2 ABC. |
| Tradovate client (`execution/tradovate_client.py`) | **RETIRE** | A single environment variable reached the live API through a string-equality gate; the module self-documented its endpoints as `TODO_VERIFY`. |
| Reconciliation (`api/routes/execution.py`) | **REWRITE** | Compared internal `uuid4` against IG `dealId` — key spaces that never intersect. |
| Alerts (`alerts/events.py`) | **REFACTOR** | The Telegram dispatcher worked; the taxonomy contained no failure events, so it had nothing to send. |
| FastAPI surface (`api/`) | **REWRITE** | No RBAC, no Origin validation, no rate limiting, a hardcoded health endpoint, and unlabelled numeric responses. |
| Next.js operator console (`apps/web`) | **REWRITE** | 14 nav items serving 5 jobs, six paths to promote a bot, no responsive layout, and `/trades` rendering backtest rows under a "Paper / Backtest" heading. |
| Deploy configuration (`render.yaml` ×2, Railway) | **RETIRE** | Three definitions, one live, mutually contradictory; V2 is local-first. |
| Alembic migrations | **REFACTOR** | Committed and containerised but never run; schema came from `create_all` plus a shim covering four of eight tables. |
| Canonical HistData store (7.2 GB parquet) | **ARCHIVE** | Retained as immutable RAW input to a recorded migration; unusable as-is for the reasons in §7. |
| Gen-1 research results (490 backtests) and the 25-row Phase-1 ladder | **ARCHIVE** | Historical record only. Not a shortlist. See §8. |
| The 52 status documents | **RETIRE** | Superseded by the audit and then by `docs/v2/`; retained only as evidence of intent. |

## 4. Financial-correctness defects

These all push in the same direction, towards flattering results.

**Spread charged on entry only.** `_apply_costs` was called from the entry path
(`engine.py:115-117`); `_get_exit_price` (`:199-210`) returned the raw stop, the raw
take-profit or the raw close with no spread and no slippage. Since spread is symmetric,
exactly half the true round-trip cost was missing. Measured on EURUSD H4, `bot01_sanyaku`
net profit fell from £1,394.79 to £758.35 once the exit half-spread was charged — a 46%
reduction, turning a profit factor of 1.045 into 1.024.

**Commission declared and never applied.** `commission_per_trade` (`config.py:18`) was read
nowhere in the engine.

**No overnight financing at all**, on a platform whose `max_bars_in_trade=50` on H4 means
positions are held roughly eight days.

**Stops and take-profits filled at their exact level through gaps.** `engine.py:203-207`
returned `position.stop_loss` unconditionally. A long at 1.1000 with a stop at 1.0950, on a
bar opening at 1.0800, was recorded as exiting at 1.0950 — a phantom 150-pip gain. The
EURUSD H1 sample contains 1,125 weekly-open bars; every stop sitting inside a weekend gap
was credited at its level. This is the single largest reason V1's tail risk looked
survivable.

**Ambiguous bars resolved optimistically and silently.** When a bar touched both the stop
and the target, the engine took whichever branch its `if` tested first — the target. The
most optimistic assumption available, never written down.

**`sqrt(252)` annualisation regardless of trade frequency.** `metrics.py:229` and `:256`
applied `math.sqrt(252)` to *per-trade* returns. The correct factor is the square root of
trades per year. On the H4 systems that dominated the leaderboard the overstatement was
about 2.5×; on small-sample rows up to roughly 9×. The inflation also disarmed the
guard-rail: the `_SHARPE_WARN = 4.0` alarm at `metrics.py:350` could never fire, because the
inflation was baked in just below it.

**Realised-only drawdown.** `engine.py:91` updated equity only when a trade closed, so the
curve was a step function and open-position excursion was invisible. Maximum adverse
excursion *was* tracked at `position.py:63` and never used. One measured sample reported a
9.65% maximum drawdown against a worst single-trade open excursion of 2.18% of capital that
never reached the curve.

**Cross-currency conversion a no-op except for JPY.** `sizing.py:84-112` returned `1.0/price`
for JPY pairs and `1.0` for everything else, including an explicit CHF branch commented
"Approximate CHF ≈ USD for simplicity". On a GBP account every USD-quoted instrument,
including all of XAUUSD, reported P&L in the wrong currency by a factor that ranged
1.20–1.43 over the sample — a 20%+ error correlated with the macro regimes being traded.

**Structural blindness to portfolio effects.** The engine held one position at a time, so
`max_open_trades` was unused and correlation, concentration and margin effects were absent
from backtests entirely. Warmup was always 98 bars because `getattr(self.strategy,
"warmup_period", 78)` read an attribute the `Strategy` base class never defined
(`engine.py:50-53`). `metrics.py:313-341` built a synthetic hourly index for monthly and
yearly buckets regardless of the real timeframe, mislabelling every period on H4 data.

## 5. Statistical defects

**No multiple-testing correction anywhere.** A grep across the whole package for
`bonferroni|deflated|white.*reality|fdr|benjamini|data snoop` returned zero hits, against a
research grid of 23,040 strategy × instrument × timeframe combinations. At a naive 5%
threshold you expect roughly 1,152 combinations to look significant with zero real skill.
The leaderboard was a sampling distribution of the maximum wearing the costume of a
discovery.

**Selection and validation shared a window.** In `research/pipeline.py`, the backtest
(`:174`) and score (`:181`) ran on the full DataFrame, the keep/discard decision (`:198`)
used that score, and then walk-forward (`:206`), the "held-out" OOS split (`:215`),
sensitivity (`:233`) and cost stress (`:240`) all re-tested the same full DataFrame. The 30%
"out-of-sample" segment had already driven selection, so it was not evidence about
anything.

**Walk-forward that fitted nothing.** `research/walk_forward.py:88-101` ran the same
fixed-parameter strategy on train and test windows. No parameter was estimated on train and
carried to test. It is a useful regime-stability statistic mislabelled as a defence against
overfitting — and a procedure that never overfits anything cannot detect overfitting.

**Frozen-size Monte Carlo.** `monte_carlo.py:104-110` resampled realised P&L and applied the
original position sizes, so a simulation opening with a run of large losses did not shrink
subsequent stakes the way live compounding would. `ruin_probability <= 0.05` was a hard
ladder gate, computed from the wrong experiment. The moving-block bootstrap itself (block
size 5) was a good choice.

**A sign-blind composite score.** `research/scorer.py:63-81` scored stability as the R² of a
linear fit with no check on the slope's sign, so a straight-line march to ruin scored 1.0.
`_score_drawdown` (`:49-54`) rewarded small drawdowns with no reference to returns, and
`_score_profit_factor` (`:35-36`) mapped NaN to the cap. Measured on EURUSD H4,
`bot02_kijun_pullback` (net −£909, PF 0.774) scored 0.3007 and beat `bot01_sanyaku`
(net +£1,395, PF 1.045) at 0.2586. In the shipped Gen-1 file the best-scoring *losing*
combination reached 0.3487, above the `ladder_min_composite = 0.30` pre-screen — losing
combinations were being admitted to the expensive robustness ladder.

**The 80-trade rule did not gate ranking.** `research/matrix.py:131-134` ranked everything
and `research/filter.py` applied the filter afterwards as a label. A shortfall was nearly
free: 74 trades cost 0.0075 of composite. The promotion gates in `research/promotion.py:52`
and `risk/promotion.py:34` did hard-require 80 trades, so nothing unqualified could reach
paper — but the leaderboard a human reads did not.

**Look-ahead in the indicator layer.** `indicators/swing.py:38-51` computed a swing at bar
*i* using bars *i+1 … i+lookback*; mutation testing changed `last_swing_high` on 3 of 15
prior bars. Fifteen of twenty-one hand-coded strategies consumed these columns across 108
references. The backtest impact appeared small, but the parity impact was serious: the loop
ran only to `n - lookback`, so the paper bot evaluating at `len(prepared) - 1`
(`paper/bot.py:113`) saw a stale forward-filled value where the backtest saw a fresh one —
the two engines saw different indicator values for the same bar. Separately,
`indicators/ichimoku.py:67` set `df["chikou_span"] = close.shift(-26)`, a genuine future
value sitting in every strategy's DataFrame; no strategy read it, but the column was exposed
through the API and the CLI and nothing prevented the next one from reading it. Senkou A and
B were displaced using `chikou_shift` rather than a dedicated `senkou_shift`
(`ichimoku.py:57-63`) — numerically identical at defaults, but a sensitivity sweep over
`chikou_shift` silently moved the cloud too.

**Two strategies that could not mean what they claimed.** `factory_trad_obv_confirm_v1`
produced zero trades on FX because canonical FX parquet has volume identically zero; it
occupied a slot in the 64 and consumed 360 grid cells that could only ever return nothing.
`factory_trad_vwap_bias_v1` traded only because `volume.py:46` silently fell back to a
typical-price rolling mean when volume was absent — an undisclosed moving-average strategy
wearing a VWAP label. And `indicators/fibonacci.py:64` was direction-blind, always measuring
downward from the last swing high, so uptrend and downtrend retracements anchored
identically; `FibonacciExtension.compute` was a no-op stub with extensions computed ad hoc
inside `bot11`.

## 6. Execution and safety defects

**The risk engine was never called.** `RiskEngine.check_trade_allowed`,
`check_drawdown_limits` and `check_fleet_trade_allowed` (`risk/engine.py:54`, `:93`, `:122`)
had zero call sites outside their own definitions. `PaperWorker.__init__` constructed one at
`worker.py:133` and never used it; `PaperBot.on_candle_close` went straight from signal to
sizing to dispatch (`paper/bot.py:186-238`). Daily and weekly stops, max-drawdown halts,
concentration limits and fleet caps were rendered on `/system/limits` and enforced nowhere.
There was no 5% portfolio cap and no drawdown hard stop in force, whatever the documentation
said.

**A single environment variable reached a live broker API.**
`execution/tradovate_client.py:160-169` let `FIBOKEI_TRADOVATE_BASE_URL` override the base
URL; the live gate at `:188-196` was an exact string comparison against `TRADOVATE_LIVE_BASE`.
A trailing slash or a casing variance produced a URL that was not `==` the constant, so the
gate never fired, `_env` stayed `"demo"`, and orders flowed to the live API.

**`FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` was committed** in `render.yaml:35-36` as a
literal value rather than `sync: false`. It was the top of the live ladder for three of the
four broker paths.

**No order idempotency, and orphaned positions by construction.** Broker deal IDs lived only
in memory (`paper/bot.py:69`) and `Position.to_dict()` had no field for them. `recover()`
(`worker.py:181-205`) restored state, bar count and last-evaluated bar but not the position
object, so a bot persisted as `position_open` came back with `self.position = None`: it could
not re-enter (entry requires `MONITORING`) and could not exit (exit requires a position). The
broker position was orphaned permanently, protected only by whatever stop was submitted at
open. Nothing was written before dispatch, so a crash between the IG POST and the audit write
left a real position with zero record; on restart the bot would open a second one. No client
`dealReference` was sent, so IG could not deduplicate either. Compounding it,
`_fetch_confirmation_with_retry` returned `PENDING_CONFIRMATION` on total failure
(`ig_adapter.py:592`) and the router treated anything not ACCEPTED/FILLED as a rejection
(`router.py:401`) — a position IG *did* open, but failed to confirm, was recorded as rejected
and its deal ID discarded.

**Reconciliation compared key spaces that never intersect.**
`api/routes/execution.py:629` built Fiboki positions keyed on `position_json["trade_id"]` — an
internal `uuid4` — while broker positions were keyed on IG's `dealId`. It reported every
position missing on both sides and could not produce a clean result even on a perfectly
healthy system. It was also reachable only through a GET endpoint a human had to click; it
never ran on startup or on a timer.

**Two workers could run concurrently and place duplicate orders.** `render.yaml` defined both
a web service and a worker service; the API started an in-process worker thread unless
`FIBOKEI_WORKER_EXTERNAL=true` (`api/app.py:334-337`), and that variable was absent from the
web service's environment. There was no lease, no advisory lock, no ownership column and no
unique constraint, and both `worker_id`s defaulted to the literal `"railway-worker"`
(`worker.py:928`), so the two would overwrite each other's heartbeat row and the duplication
would be invisible on the System page.

**Any authenticated user could enable live execution over HTTP.** `UserModel.role` existed and
was referenced only in the `/auth/me` response; there was no `require_admin` dependency
anywhere. Every logged-in session could create execution accounts with `environment="live"`
and `live_allowed=True`, PATCH `live_allowed`, deactivate the kill switch, delete all bots and
reset the account. There was no rate limiting anywhere, including login: unlimited password
guesses against two known usernames, no lockout, no failed-attempt alerting, and seeded
passwords defaulting to the literal `changeme` (`api/seed.py:14-15`). The session cookie was
`SameSite=None`, so a plain HTML form POST with no body — a CORS-simple request that skips
preflight — reached kill-switch deactivate, bot stop/pause/resume/restart, restart-all,
reset-all and account reset. There was no CSRF token and no Origin validation.

**The kill switch stopped new orders and abandoned open positions.** `dispatch_open` checked
it; nothing flattened existing positions when it was thrown. Its fail-closed behaviour on
exception (`router.py:147-152`, `worker.py:164-166`) was correct; the semantics were simply
never decided or named.

**Worker restart blinded every bot for roughly 100 candles.** `recover()` restored `bars_seen`
but left the in-memory DataFrame empty, and because `_last_evaluated_bar` was also restored the
warmup branch was skipped. `run_preparation` then threw on a one-row frame and was swallowed by
a bare `except Exception: return None` (`paper/bot.py:108-111`). On H4 that is about two weeks
of silence, with no error surfaced and the heartbeat still reporting the bot as active.

**Paper was not the same engine as the backtest**, and every divergence ran optimistic:

| Dimension | Backtester | Paper bot |
|---|---|---|
| Entry spread | half-spread applied (`engine.py:115`) | none — fills at the proposed price (`bot.py:190`) |
| Minimum stop floor | `atr × 2.0` passed to sizing | not passed; tight stops produced larger positions |
| Currency adjustment | applied to every closed trade | never applied; JPY pairs overstated P&L ~150× |
| Sizing base | per-strategy equity, isolated | shared fleet equity |
| Sizing authority | one | three — bot, router and IG adapter each sized independently |
| Bankruptcy guard | `if equity <= 0: break` | none; balance could go unboundedly negative |
| Unrealised P&L | not modelled | read a key nothing ever wrote; permanently zero |
| Data source | canonical parquet | yfinance, while orders were placed at IG prices |

The triple-sizing row is the most corrosive: `Position.position_size` (equity-based) drove the
recorded P&L while `attempt.requested_size` (allocation-based) drove the actual broker order,
and the IG adapter re-sized a third time from the live IG balance. The ledger and the broker
disagreed about position size for the same signal, by construction.

## 7. Data defects **(V2 rebuild)**

These were discovered while building V2's data platform, and they are the finding this
baseline adds to the audit. They matter because they are independent of every engine bug
above: even a perfectly correct engine fed this data produces stale results.

**Timestamps were EST without daylight saving, stamped as UTC.** HistData publishes every bar
on a fixed UTC−05:00 clock — New York's winter offset applied in July as well — and V1's
ingest attached `tz="UTC"` to those naive timestamps without shifting them. Every bar in the
canonical store is therefore labelled five hours earlier than it happened. Because the error is
constant, nothing ever looked obviously broken; what it broke was every session filter, every
hour-of-day feature and every rollover-window cost assumption, year-round. The evidence is in
the data itself: a genuinely-UTC FX series has its weekly open drift between 21:00 and 22:00
UTC with US daylight saving, while these files show a rock-solid 17:00 boundary all year, which
is only possible on a fixed offset. V2's `data/providers/histdata.py` implements
`detect_timestamp_convention` to check exactly this, and applies `true_utc = naive_est + 5h` as
a declared, recorded adjustment.

**Bid prices were treated as mid.** HistData's free ASCII M1 series is built from bid quotes.
V1 loaded them as `open/high/low/close`, every engine downstream treated them as mid, and the
backtester then *additionally* subtracted a modelled half-spread. A long entry was charged a
spread it had already implicitly paid; a short entry was credited one it never received. The
defect is not correctable by a constant, because the bias is direction-dependent. V2 makes
`price_basis` a required column and stamps this data `BID`, so any consumer wanting mid must
convert explicitly through `bid_to_mid` and the result is labelled `SYNTHETIC_MID`, never
`MID`.

**Volume is identically zero.** FX has no consolidated tape, and the HistData files carry a
zero (stored as `-1` in the V2 canonical schema) in every volume field. V1 computed OBV on it —
producing a flat line, and one strategy that could never trade — and let VWAP fall back
silently to a rolling mean of typical price. V2's `DefectCode.VOLUME_ALWAYS_ZERO` reports it,
and `indicators/base.VolumeUnavailableError` makes a volume indicator on volumeless data raise
rather than return a plausible number.

**A negative-price sentinel bar.** EURUSD H1 contains exactly one bar whose OHLC are all
`-0.0001`, at 2001-09-11 20:00 (the original EST stamp; 2001-09-12 01:00 true UTC). A single
non-positive price poisons every log return, every rolling variance and every expanding
percentile computed across it. V1 never detected it. V2 detects it in three independent places
— `data/integrity.DefectCode.NON_POSITIVE_PRICE`, `marketstate/features.invalid_bar_mask`, and
`sim/fills.Bar.__post_init__`, which refuses to reason about an incoherent bar at all — and
refuses to repair it silently: `marketstate/features.drop_invalid_bars` records the drop on the
resulting `FeatureSet`, and `data/integrity.repair` requires a named actor and a written reason
and produces a new dataset version. The bar is reproduced as a fixture in
`tests/unit/test_data_integrity.py`, `tests/unit/test_marketstate_features.py`,
`tests/integration/test_migrate_v1.py`, `tests/integration/test_no_silent_repair.py` and
`tests/integration/test_marketstate_engine.py`.

**No dataset versioning of any kind.** Results were stored against an instrument and a
timeframe, and the underlying parquet was rewritten in place whenever anything was
re-downloaded. Every stored V1 backtest is attached to whatever the file happens to contain
*now*, not to what it contained when the backtest ran. There is no way to re-resolve the bytes
behind any V1 number.

**A data root resolved by walking up the tree.** V1 started at the module's own location and
walked upwards looking for a directory that looked like a data store. On the research box it
found a nearly-empty staging directory two levels above the real one. Every read succeeded;
every read returned nothing. 99% of a research batch recorded `no_data`, and because `no_data`
was treated as a completed outcome those combinations were written into the checkpoint as DONE
and never retried. The batch looked finished and had tested almost nothing.

**Conclusion, stated plainly.** Every V1 research result is stale for data reasons alone,
independently of the engine bugs. The timestamps are five hours out, the prices are one side of
a book being treated as its middle, the volume channel is empty, and at least one bar is not a
price. **V1 rankings are not trustworthy and must be recomputed from re-migrated, versioned,
integrity-checked data.** Nothing in `backend/results/` is a shortlist.

## 8. What the surviving numbers actually said

Of 490 backtested combinations in the Gen-1 sweep, 371 lost money — 76% — before any of the cost
corrections in §4. Applying a sanity gate of at least 80 trades, profitable, profit factor above
1.2 and maximum drawdown under 15%, fourteen survived. Thirteen of the fourteen were H4. Six
were USDJPY, carrying implausibly low drawdowns (0.9%, 1.0%, 1.1%, 1.2%, 2.0%) during one of the
strongest sustained directional trends in recent FX history — a regime artefact, not a result.
Honest re-annualisation cut the reported Sharpes by roughly 2.5× on typical H4 rows (3.48 → 1.41
on the best), and up to about 9× on the tiny-sample rows.

Every one of those figures is in-sample, on two years of FX-only data, on a single parameter set
per family, with no out-of-sample test, no walk-forward, no multiple-testing correction, roughly
half the transaction costs missing, and — per §7 — on data that is five hours out and bid-quoted.
A gross Sharpe of 1.4 on 83 trades over two years, corrected for costs and deflated for 490
trials, is not a demonstrated edge. **No strategy in V1 was ever shown to make money.**

## 9. Engineering-discipline defects

The offline test suite hung because `tests/conftest.py:53-65` built a `TestClient(app)`, which
ran the FastAPI lifespan, which called `_start_worker_thread()` — and that function only opted
out when `FIBOKEI_WORKER_EXTERNAL` was set, which conftest never did. Every one of ~200 API tests
spawned a real daemon thread polling yfinance over the network; daemon threads are never joined,
so they accumulated until the suite crawled to a stop. Measured on the same tree: a default run
timed out at 240s with 22 tests incomplete; with the variable set, the same 22 passed in 27
seconds and the full suite completed at `1 failed, 1078 passed, 4 skipped in 328.09s`. Three
months of working around this with a "122-test core subset" cost more than the one-line fix. The
single failure was a contradiction committed into the tree: `test_tp_hit_negative_pnl.py:129`
asserted a take-profit could produce negative P&L, which commit `78a29f2` had abolished and
`test_tp_side_guard.py:16` asserted the opposite of.

Dependencies were unpinned (`pandas>=2.0`, `numpy>=1.24`) with no lockfile of any kind; two
independently built environments both resolved to pandas 3.0.6 and numpy 2.4.6, a major version
beyond what the author had in mind, and two of the six uncommitted changes were already hot-fixes
for that drift. **"Deterministic backtests" was therefore not a property the repository had**,
whatever the non-negotiables said: determinism held within one environment and not across
environments.

Nothing gated a deploy. CI ran lint, test and frontend-build, then curled the already-live
production API; Railway and Vercel deployed on git push independently of GitHub Actions, so a red
test job stopped nothing. The health endpoint returned a hardcoded `{"status": "ok", "version":
"1.0.0"}` with no database or worker check, and its version string disagreed with
`pyproject.toml`. The `network` and `slow` markers were declared and used zero times across 105
test files, so the `-m "not network"` command the status doc recommended filtered nothing. `mypy`
was a declared dev dependency and never run. Lint covered `src/` only. Playwright had four
defined scripts and never ran in CI.

Coverage was inverted against risk: 69% total, with risk at 94% and indicators at 93%, but
`worker.py` — the process that actually trades — at 22%, `paper/bot.py` at 33%,
`paper/orchestrator.py` at 0%, `ig_client.py` at 58%, and `cli.py` and `diag.py` at 0%.

Test quality was better than expected in one respect: an AST scan of all 976 test functions found
exactly one with no assertion, and the look-ahead tests used the correct mutate-the-future
technique. It was weak in three specific ways that the documentation denied. There were **no
golden-value indicator tests against an external reference** — `test_ichimoku.py` contained zero
pinned constants for the flagship indicator and recomputed with the same formula on the same
data. There were **no backtest regression pins** — `test_backtester_determinism.py` ran the same
backtest twice in one process, which catches an RNG issue and nothing else, so any change to
trade count or P&L still passed. And there were **no paper-versus-backtest parity tests at all**,
despite parity being an architectural rule.

Observability, concretely: if the worker died at 3am, nothing told you. There was no Sentry in
`worker.py`, no `worker_down` or `heartbeat_stale` event in `alerts/events.py` (the seven defined
events were all trade or bot lifecycle), heartbeat freshness was computed only when a human
loaded the System page, the health endpoint could not report the failure, and because the worker
was a thread inside the API the API stayed green.

Security debt beyond §6: `ecdsa` 0.19.2 carried PYSEC-2026-1325 with no fix version available,
sitting in the JWT signing path via `python-jose`; `npm audit --omit=dev` reported 8
vulnerabilities, 3 critical, including `next` at 16.1.6 inside the vulnerable range, plus
`plotly.js` and `maplibre-gl`; and `klinecharts` was pinned as `^10.0.0-beta1` — a caret range on
a beta for the primary chart engine.

## 10. Documentation drift, catalogued

`forensic-ig-realism-audit.md` and `operator-polish-report.md` both claimed "all 661 backend
tests pass". `LIVE_READINESS_REPORT.md` issued a GO verdict on 615 passing tests.
`CURRENT_STATUS.md`, four months later, said the suite could not complete offline. All three are
reconcilable: the suite *can* pass, and the earlier runs were online, where the rogue worker
threads' network calls returned instead of blocking. The GO rested on a suite whose green
depended on live internet.

`RAILWAY_FORENSIC_REPORT.md`, browser-verified in June, stated there was no separate worker
service — it ran as a daemon thread inside the API — directly contradicting
`GROK_CURSOR_ONBOARDING_AUDIT.md` and `deployment.md`. Three deployment configurations existed
and it was not determinable from the repository which was authoritative; several P0 severities
depended on that answer.

The following were asserted in V1 documentation and were **not** enforced anywhere in V1 code:
deterministic backtests across environments; known-value indicator tests; backtest regression
pins; a 5% portfolio risk cap; drawdown hard stops; portfolio-aware risk controls as mandatory;
an 80-trade minimum for primary ranking; and backtest/paper shared execution logic. Each of those
claims now has a corresponding structural enforcement in V2, and where V2 has not yet enforced
one it is listed as unwired in `ROADMAP.md` rather than asserted here.

## 11. What was explicitly not verified

Production behaviour on Railway, Vercel and Sentry. Which deployment configuration was
authoritative. Whether duplicate orders or orphaned positions actually occurred. Actual production
environment variables. Tradovate API correctness beyond its safety gating. Cross-environment
backtest determinism. The behavioural impact of the `bot07`/`bot12` future-row reads. 58 of the 64
strategies were not individually audited. Alembic migrations were never executed. Colour contrast
was not measured. The local database held zero rows in `execution_audit`, `execution_attempts`,
`broker_trades`, `paper_bots`, `paper_trades` and `trades`, so there is no offline-inspectable
record of what the platform ever did — which is itself a finding.
