# Portfolio and Risk Standard

**Snapshot:** 2026-09-19T05:05Z (`pytest tests/ -q` → 2683 passed, 2 skipped). Packages: `src/fiboki/portfolio/`,
`src/fiboki/risk/`. Tests: `tests/unit/test_portfolio_construction.py`,
`test_sizing_authority.py`, `test_risk_gateway.py`, `test_killswitch.py`,
`test_limits_and_venue.py`, `test_no_gateway_bypass.py`.

The sentence this document exists to make untrue of V2: *"The portfolio risk engine has zero
call sites in production code. Daily and weekly stops, max-drawdown halts, concentration
limits and fleet caps are rendered on `/system/limits` and enforced nowhere."*

---

## 1. Three separations

Alpha, sizing and risk are three different decisions made by three different components, and
each one may only produce its own kind of answer.

| Concern | Owner | Output | May it decide anything else? |
|---|---|---|---|
| **Alpha** | `strategy/` | `Signal` — direction, entry reference, stop, targets | No size. No risk logic. The `Signal` dataclass has no size field. |
| **Sizing** | `portfolio/sizing.size_trade` | `TradePlan` — exactly one size | It cannot grant permission. |
| **Budgeting** | `portfolio/construction` | `Allocation` — a fraction of the per-trade risk budget | It decides *how much of the budget*, never *how many units*. |
| **Permission** | `risk/gateway.RiskGateway` | `RiskDecision` | It cannot resize. `adjusted_size` exists on the contract and the gateway always sets it to `None`. |
| **Execution** | `broker/` | `Order` → `Fill` | An adapter converts units and may refuse. It may not size and may not decide risk. |

Two prohibitions carry the weight and both are enforced over the AST by
`tests/unit/test_no_gateway_bypass.py`: `Order` is constructed in exactly one function
(`ExecutionService.submit`), and no adapter calls `size_trade`, `size_for` or constructs a
`PortfolioSizer`.

### One sizing authority

V1 sized the same signal three times — the paper bot off fleet equity, the router off
allocated capital, the IG adapter off the live broker balance. Three answers, one position,
and an internal ledger that disagreed with the broker *by construction*: no bug was required.
`Position.position_size` drove the recorded P&L while `attempt.requested_size` drove the actual
order.

V2 has exactly one function that turns a `Signal` into a size:

```
risk_account   = equity × risk_fraction × portfolio_weight
risk_per_unit  = stop_distance × contract_size × fx(quote → account)
size           = risk_account / risk_per_unit
size           = min(size, equity × leverage / notional_per_unit)
size           = round_DOWN_to_step(size)
```

Rounding is always **down** (`core/money.round_size`). Rounding up would manufacture risk the
rule did not intend, and one step is a material amount on an instrument whose step is 1.0 units.

`SizingPolicy.leverage_for` will never exceed the instrument's registered retail cap, whatever
is asked: a caller may request *less* leverage than the venue permits, never more, because the
cap is a regulatory fact about the instrument (FCA PS19/18 and the ESMA product intervention,
recorded in `core/instruments._RETAIL_LEVERAGE`) and not a strategy preference. FX majors are
30:1, FX crosses, metals and indices 20:1, energy 10:1, equity 5:1, crypto 2:1.

`fx_quote_to_account` is a **required** argument supplied by the caller from an explicit
`FxRateSource`. `size_trade` will not invent one, because inventing 1.0 is exactly how V1
mis-stated every USD-quoted result on a GBP account by a factor that ranged 1.20 to 1.43 over
the sample.

A signal that cannot be sized produces a `SizingOutcome` carrying a **named** rejection —
`NON_POSITIVE_EQUITY`, `NON_POSITIVE_STOP_DISTANCE`, `NON_POSITIVE_RISK_BUDGET`,
`NON_POSITIVE_RISK_PER_UNIT`, `BELOW_MIN_SIZE`, `ROUNDED_TO_ZERO`, `INVALID_FX_RATE`,
`ZERO_PORTFOLIO_WEIGHT` — never a silent zero. The outcome is either a plan or a reason, never
both and never neither.

`tests/unit/test_sizing_authority.py` asserts the number survives unchanged from `size_trade`
all the way to the `Order` handed to a venue.

**The honest limitation, stated in the code:** `risk_fraction` is the fraction of equity lost
*if the stop fills exactly at its level*. Gaps exceed it. This is a sizing **intent**, not a
loss guarantee, and the realised MAE distribution (recorded on every `Trade`) is where you find
out how often the intent failed.

## 2. Portfolio construction

V1 had no portfolio layer at all. Each bot sized its own signal against fleet equity, so twelve
strategies could each take "1% risk" on six correlated FX majors simultaneously and call the
result 12% risk when the realised one-factor exposure was closer to 50%.

`portfolio/construction.py` decides how much of the per-trade risk budget each concurrent
candidate is entitled to, *before* `size_trade` turns that entitlement into units. Weights are
fractions of the risk budget, not of equity: a weight of 1.0 means "the full `risk_fraction`".

### Allocators — the base weight

| Allocator | Base weight | Note |
|---|---|---|
| `EqualRiskAllocator` | flat per candidate | The honest baseline. |
| `VolatilityParityAllocator` | proportional to 1/σ | **Inverse volatility, not true risk parity** — it ignores covariance terms, so with correlated candidates it understates aggregate risk. Named accordingly on purpose, which is why the correlation penalty still applies on top. |
| `CorrelationPenalisedAllocator` | proportional to `1 − mean(|ρ|)` against the rest of the candidate set | A candidate perfectly correlated with the set scores zero and receives nothing. |

`CorrelationMatrix` returns `default` for unmeasured pairs rather than 0.0. **Assuming zero
correlation for a pair you have not measured is the single most dangerous default in portfolio
construction: it is maximally permissive exactly where you know least.** The default is
therefore a constructor argument, and `ConstructionConfig.unmeasured_correlation` is a
deliberately positive 0.30.

### The adjustment pipeline

A fixed, ordered sequence of thirteen steps runs after the allocator, and **every** scaling
factor is recorded on the `Allocation` with the step that applied it. An allocation you cannot
explain is an allocation you should not have taken, so the reasoning is data, not a log line.

```
lifecycle → health → confidence
  → instrument_correlation → strategy_correlation
  → instrument_concentration → asset_class_concentration → currency_exposure
  → volatility_target → margin → drawdown → regime
  → total_budget cap
```

The order is fixed and **not configurable**, because changing it changes the answer. Eligibility
gates run first, so a quarantined strategy never consumes budget; then per-candidate quality
scaling; then book-level constraints; then the global cap.

**`ConstructionConfig`, version `construction_v1`:**

| Field | Default | Meaning |
|---|---:|---|
| `max_total_weight` | 3.0 | Total budget across all concurrent candidates, as a multiple of the single-trade risk fraction. |
| `max_weight_per_candidate` | 1.0 | No candidate exceeds one full budget. |
| `min_weight_to_trade` | 0.10 | Below this a candidate is dropped rather than traded at a token size. |
| `correlation_soft_cap` / `correlation_hard_cap` | 0.40 / 0.85 | Penalty band; above the hard cap a candidate goes to zero. |
| `unmeasured_correlation` | 0.30 | Conservative assumption for pairs with no measurement. |
| `max_instrument_notional_pct` | 10.0 | Gross notional per instrument, as a **multiple of equity**. |
| `max_asset_class_notional_pct` | 20.0 | Per asset class. |
| `max_currency_notional_pct` | 15.0 | Net per currency leg. |
| `target_portfolio_vol` | 0.12 | Annualised vol target; `None` disables. |
| `vol_target_max_scale` | 1.50 | Vol targeting may never scale *up* by more than this. |
| `max_margin_utilisation` | 0.50 | |
| `drawdown_derisk_start_pct` / `drawdown_zero_pct` | 5.0 / 20.0 | Linear de-risking between the two; at or beyond 20% no new risk is allocated at all. |
| `regime_scalars` | trend 1.0, range 0.7, high_vol 0.5, crisis 0.25, unknown 0.6 | Missing regimes use 1.0. **`unknown` is 0.6, not 1.0** — not knowing the regime reduces size. |
| `allocatable_lifecycles` | PAPER, SHADOW, DEMO, APPROVED, LIVE, WATCH | Everything else receives nothing. |
| `lifecycle_scalars` | WATCH 0.5, DEMO 0.75 | States that may trade but at reduced size. |

Currency exposure is **net**: long EURUSD is +EUR and −USD, so a simultaneous long EURUSD and
short EURGBP partially nets in EUR and does not in USD/GBP. `PortfolioSnapshot` carries signed
notionals in the account currency.

The notional caps read high — 10× equity in one instrument — and that is arithmetic rather than
laxity: risking 1% over a 30-pip EURUSD stop (0.27% of price) requires a notional of 3.7× equity
*for one trade*, and a 15-pip stop requires 7.3×. A cap below about 8× would silently forbid
tight-stop trading while appearing to permit it, which is the worst of both worlds. The controls
that bind day to day are the risk-budget and margin limits.

## 3. The mandatory gateway

`risk/gateway.py`. `RiskGateway` is the only way to obtain permission to place an order,
`ExecutionService` will not construct an `Order` without one, and the AST test proves no other
module constructs an `Order` at all.

Usage is always two lines and the second may not be skipped:

```python
decision = gateway.evaluate(context)
if not decision.allowed:
    return   # the attempt row has already been recorded
```

`evaluate` records the attempt itself, so there is no way to obtain a block and forget to
persist it.

### Three properties that matter more than the individual checks

**Every check is named.** `RiskDecision.checks_run` lists all twenty names on every decision,
allowed or blocked. An audit can *prove* which rules ran rather than trusting that they did.
`_verify_coverage` compares the list that ran against `CHECKS` after the fact, and a check that
somehow did not run appends `check_did_not_run:<name>`, which blocks.

**Fail closed.** An exception inside any check blocks the order and is reported as
`check_error:<name>:<type>:<message>`. So does missing input: `MarketView` fields are `None`
when unknown, and **`None` never means "fine", it means "unknown", and unknown blocks.**

**Nothing is dropped silently.** Every blocked plan produces an `ExecutionAttempt` row carrying
the named reasons, the limits version and the limits fingerprint. V1 dropped rejects on the
floor, so "why didn't it trade?" was unanswerable after the fact and a strategy that stopped
trading looked identical to a strategy with no signals.

**Checks are never short-circuited.** A blocked order still runs the remaining checks, so the
operator sees *all* the reasons. "Blocked for stale price" is a much worse answer than "blocked
for stale price, 6% account risk and a paused kill switch" when you are deciding whether to
intervene.

**The gateway performs no I/O.** It cannot reach a broker, a database or a clock of its own; the
caller assembles `RiskContext`. That is what makes it exhaustively testable and what stops a
check from silently succeeding because a network call timed out.

### The twenty checks, in order

| # | Check | Blocks when |
|---:|---|---|
| 1 | `kill_switch` | the switch forbids this `RequestKind` |
| 2 | `strategy_lifecycle` | lifecycle unknown, or in {DISCOVERY, RESEARCH, VALIDATING, CANDIDATE, DEGRADED, QUARANTINED, RETIRED}; or the strategy is flagged degraded; or the mode exceeds the lifecycle stage (LIVE mode requires lifecycle LIVE; DEMO refuses a PAPER strategy) |
| 3 | `market_state` | the market is halted or closed |
| 4 | `data_freshness` | bar age unknown or above `max_data_age_seconds` |
| 5 | `stale_price` | quote age unknown or above `max_price_age_seconds` |
| 6 | `abnormal_spread` | spread above `max_spread_multiple` × the instrument's typical |
| 7 | `broker_health` | health score below `min_broker_health`, or unknown |
| 8 | `event_blackout` | inside `event_blackout_minutes` either side of a flagged release |
| 9 | `event_veto` | a NEW entry (`RequestKind.OPEN` only) meets an unscheduled, severe, confident event annotation for one of the instrument's buckets AND `EventVetoPolicy.enabled` (off by default). Disabled or unavailable: never blocks, records `event_veto_shadow:<...>` instead (see below) |
| 10 | `instrument_lock` | an entry lock declared by a strategy document is in force on the signal's decision bar; or the signal's document declares locks and the lock state cannot be established (see below) |
| 11 | `max_per_trade_risk` | this trade's risk-to-stop exceeds the per-trade cap |
| 12 | `max_account_risk` | summed open risk-to-stop across the book exceeds the cap |
| 13 | `max_instrument_exposure` | gross notional in one instrument exceeds the cap |
| 14 | `max_strategy_exposure` | gross notional attributable to one strategy exceeds the cap |
| 15 | `max_currency_exposure` | net notional in one currency leg exceeds the cap |
| 16 | `max_correlated_exposure` | gross notional across positions correlated above `correlation_threshold` exceeds the cap |
| 17 | `daily_loss` | realised loss today exceeds the daily cap |
| 18 | `weekly_loss` | realised loss this week exceeds the weekly cap |
| 19 | `total_drawdown` | drawdown from peak exceeds the total cap |
| 20 | `margin_utilisation` | margin used exceeds the cap |

### `event_veto`: the agent event channel (veto-only, shadow first)

Added 2026-09-29 (AGENTIC_INTEGRATION_PLAN §5 Wave 4). The one place LLM output can reach the
risk path, and it can only ever REMOVE a new entry.

- **Input.** `RiskContext.event_veto`, an `EventVetoProvider` (protocol in `risk/gateway.py`,
  implemented by `marketstate.events.EventVetoSource`, which `risk` may not import). The check
  asks `assess(plan.instrument, ctx.now)` and reads nothing else: no size, stop, exit, limit or
  kill-switch state (AST test in `tests/unit/test_event_veto_gateway.py`). Like `instrument_lock`,
  the I/O is in the source the caller supplies, not in the gateway.
- **Policy.** `EventVetoPolicy` (`marketstate/events.py`, version `event_veto_v1`):
  `enabled=False`, `min_severity=2`, `min_confidence=0.6`, window `[observed_at, +2h)`,
  unscheduled only, point-in-time on the annotation's `available_at`, instrument mapped to
  currency/asset buckets deterministically. Its version is stamped on every attempt row as
  `event_veto_policy` (`not_wired` when no source is supplied).
- **Shadow reasons.** Disabled, a matching annotation is recorded as
  `event_veto_shadow:<type>:<buckets>:sev<n>:<annotation_id>` and the order is ALLOWED.
  `RiskDecision.allowed` is decided by `blocking_reasons` only; the prefixes in
  `core.contracts.SHADOW_REASON_PREFIXES` are the only non-blocking ones (a mislabelled note
  blocks). `ExecutionAttempt.reason` and the telemetry `venue_error` carry blocking reasons only;
  shadow notes go to `extra["shadow_reasons"]`.
- **Fail-OPEN, the one exception to "unknown blocks".** A missing, unreadable or stale annotation
  store (no successful scan within `max_annotation_age`, 1 h) means NO veto, a shadow note
  `event_veto_shadow:unavailable:<why>` and one operator alert per transition. Failing closed
  would let a dead classifier or a switched-off model host halt all entries, i.e. hand the LLM
  layer an indirect kill switch. A raise inside the source is still `check_error:event_veto`
  and blocks.
- **Scope.** OPEN only; an INCREASE is left to the other nineteen checks. Not in `EXIT_CHECKS`.
- **Not wired.** No worker passes a source into its `RiskContext` yet (`workers/runtime.py` was
  outside this change), so today every attempt row says `event_veto_policy: not_wired` and the
  gateway's shadow log is empty; `fiboki events shadow-report` computes the same counterfactual
  offline from the annotation store and a trade ledger.

### `instrument_lock`: strategy-declared entry locks

A strategy document may declare `locks` (`STRATEGY_STANDARD.md` §4a): a cooldown after a close
and a stop-streak lock, both counted in **session bars** of the strategy's timeframe, weekends
excluded. The rule is implemented once, in `backtest/locks.py`, and asked the same question in
two places: `BacktestEngine` asks it before queuing an entry, and this check asks it before an
order exists. Both ask about the signal's **decision bar** (`plan.signal.bar_time`), never
`ctx.now`, so the two cannot disagree at a lock's boundary.

The gateway reads lock state from `RiskGateway(locks=...)`, held like the kill switch rather than
passed per call. The intended source is `LedgerLockView`, which **rebuilds the lock book from the
durable ledger on every question** and holds no state of its own, so a restart cannot forget a
lock: the answer is a pure function of the ledger and the bar. Two ledgers can feed it:

- the **trade (position) ledger** -- exact for both rules, because it records the exit reason;
- the **order-intent ledger**, via `closes_from_intents` -- exact for cooldowns; a close whose
  reason the intent does not record counts as a stop-out, so a stop streak recovered from here
  can only be more restrictive than the truth.

Fail-closed, stated precisely. Compiled signals from a lock-declaring document carry the feature
`locks_declared`. Such a signal is refused, as `instrument_lock_state_unavailable:<why>`, when the
gateway has no lock source, when the source cannot read its ledger, when no policy is registered
for the strategy, or when the signal's timeframe is not the one the policy is counted in. A
source that cannot read its ledger also refuses signals that declare nothing, because a
book-wide (`global`) lock may be in force that it cannot see. A gateway with **no** source passes
a signal that declares nothing: no lock-declaring strategy can trade through it, so nothing can
have armed a lock. A lock never blocks an exit; `instrument_lock` is not in `EXIT_CHECKS`.

`tests/integration/test_lock_parity.py` drives one lock-declaring document through the engine,
through `PaperBroker` behind this gateway (also with the gateway rebuilt from a ledger file at
every bar), and through `VenuePositionManager.submit` over `SimulatedVenue`, and asserts
identical ledgers and that the gateway refused exactly the bars the engine refused.

**Not wired in production.** No worker constructs a `LedgerLockView` today (`workers/` is outside
this change). Until one does, a lock-declaring document is refused on every entry in paper and
demo -- the safe failure -- and documents without locks are unaffected. When wiring it, ask the
lock BEFORE `VenuePositionManager.precheck_entry`: the precheck consumes a sequence number when
it refuses, and doing that for a signal the engine never sequenced decorrelates the fill model's
price path from the backtest's.

### Exits run a smaller set, deliberately

`evaluate_exit` runs five checks: `kill_switch`, `market_state`, `data_freshness`,
`stale_price`, `broker_health`.

Running `max_account_risk` on a *closing* order would refuse to let you out of the book
precisely when the book is over its limit — the trap version of a risk control. What an exit
must still satisfy is that the venue is reachable, the market is open, the price is not stale,
and the kill switch permits this kind of request.

`ExitContext` carries the same `market`/`venue`/`now`/`request_kind` surface as `RiskContext`,
so the **very same check methods** run against it. There is no second implementation of "is this
price stale". `evaluate_exit` raises if handed a request kind that adds risk.

The names of the two sets are separate (`CHECKS`, `EXIT_CHECKS`) so a decision record makes
plain which ran: an exit decision listing five checks is not a bug.

This is also why exits go through the gateway at all — V1's kill switch blocked new orders and
abandoned open positions, so exits were never first-class and never audited.

## 4. Kill switch semantics

`risk/killswitch.py`. V1's kill switch blocked new orders and abandoned open positions. Nobody
wrote down which of the two it was supposed to do, so it did the first and the operator believed
it did the second. In a fast market that is the worst possible combination: you stop hedging and
keep the exposure.

V2 forces the choice **at activation time**. There are exactly two modes.

| | `PAUSE` | `FLATTEN` |
|---|---|---|
| New position | blocked | blocked |
| Add to / increase | blocked | blocked |
| Reduce | **permitted** | **permitted** |
| Close | **permitted** | **permitted** |
| Risk-reducing stop/target amendment | permitted | blocked (only closing orders) |
| Existing positions | left open, **keep their stops** | closed **now** at market |

`PAUSE` means *no new risk*. A pause that prevents you from getting flat is a trap, so
reducing, closing and protective amendments stay open.

`FLATTEN` means *close everything now*. `KillSwitch.flatten_orders` enumerates the closing
intents and the execution service dispatches them through the normal ordering path, so they are
recorded, idempotent and recoverable like any other order — not a side channel.

Both modes are activated with a named operator and a written reason, append an immutable record
to a journal, and require an **explicit operator deactivation**. There is no timeout, no
auto-reset and no clearing when the market calms down. *A switch that turns itself off is not a
kill switch.*

`FileKillSwitchJournal` fsyncs on every append, because the switch's state must survive the
crash that made you hit it. *An unflushed kill switch is not a kill switch.* State is recovered
from the journal on startup.

`allows(kind)` returns `(ok, reason)` and the reason string is what lands in the
`RiskDecision.reasons` tuple: `kill_switch_pause_blocks_new_risk`,
`kill_switch_flatten_blocks_non_closing`, `kill_switch_flatten_permits_closing`, and so on. An
unknown mode returns `(False, "kill_switch_unknown_mode")` — fail closed.

## 5. Limits as versioned data

`risk/limits.py`. V1 rendered limits on a dashboard and enforced them nowhere, and when a limit
was edited there was no record of which limit set produced a historical decision — so a report
could never answer "was this trade allowed under the rules in force at the time?".

A `LimitSet` is an immutable, named, fingerprinted value. Every `RiskDecision` is taken against
exactly one of them, and the execution record stores its `version` and `fingerprint`. Editing
limits means publishing a **new** version via `derive(version, **changes)`, which refuses to
reuse the old name; the old set stays registered so old decisions remain explicable. The
fingerprint covers the limit *values* and excludes `version` and `notes`, so two differently
named sets with identical numbers share a fingerprint — correct, because it is the numbers that
decided the trade.

`__post_init__` refuses incoherent sets: no negative limits, `correlation_threshold` and
`min_broker_health` in [0,1], per-trade risk not exceeding account risk (otherwise one trade
could breach the book limit), daily loss not exceeding weekly, weekly not exceeding total
drawdown.

### The three registered sets

| Field | `limits_v1_default` | `limits_v1_conservative` | `limits_v1_paper` |
|---|---:|---:|---:|
| `max_per_trade_risk_pct` | 1.0 | 0.5 | 1.0 |
| `max_account_risk_pct` | 5.0 | 2.5 | 5.0 |
| `max_instrument_exposure_pct` | 1000 | 500 | 1000 |
| `max_strategy_exposure_pct` | 1500 | 750 | 1500 |
| `max_currency_exposure_pct` | 1500 | 750 | 1500 |
| `max_correlated_exposure_pct` | 2000 | 1000 | 2000 |
| `correlation_threshold` | 0.60 | 0.60 | 0.60 |
| `max_daily_loss_pct` | 3.0 | 1.5 | 3.0 |
| `max_weekly_loss_pct` | 6.0 | 3.0 | 6.0 |
| `max_total_drawdown_pct` | 20.0 | 10.0 | 20.0 |
| `max_margin_utilisation_pct` | 50.0 | 25.0 | 50.0 |
| `max_price_age_seconds` | 90 | 90 | **600** |
| `max_data_age_seconds` | 300 | 300 | **1800** |
| `max_spread_multiple` | 3.0 | 2.0 | **5.0** |
| `min_broker_health` | 0.50 | 0.70 | **0.10** |
| `event_blackout_minutes` | 15 | 30 | 15 |

`limits_v1_default` is the baseline for monitored paper and demo. `limits_v1_conservative`
halves the risk for the first live-adjacent period. **`limits_v1_paper` relaxes only the
data-quality tolerances** — a stale paper quote costs nothing real — **and leaves every risk
limit identical to the default**, so paper and live risk behaviour stay comparable. That
asymmetry is deliberate and is stated in the set's own `notes`.

**On the "5% portfolio cap".** V1's documentation asserted one and V1 enforced nothing. In V2
the 5% figure is `max_account_risk_pct` in `limits_v1_default`: summed open risk-to-stop across
the whole book, checked by gateway check 10 on every risk-adding order, with the limit version
and fingerprint recorded on the resulting attempt row. It is enforced at the point of decision,
not rendered on a page.

**On "drawdown hard stops".** Three exist and are distinct. `max_total_drawdown_pct` (20% by
default, 10% conservative) blocks new risk at gateway check 17. `ConstructionConfig` de-risks
linearly from 5% drawdown and allocates nothing at or beyond 20%. And the backtest engine's
`bankruptcy_equity` guard closes everything at `MARGIN_CALL` and stops the run. None of these
*flattens* a live book automatically — that is what `FLATTEN` is for, and it is operator-driven.
An automatic drawdown-triggered flatten does not exist at this snapshot.

## 6. What is enforced, and what is not

| Property | Enforcement | Status |
|---|---|---|
| Exactly one sizing authority | AST test: adapters call no sizer | enforced |
| Size survives to the venue unchanged | `tests/unit/test_sizing_authority.py` | enforced |
| `Order` constructed only downstream of a `RiskDecision` | AST test, single call site | enforced |
| Gateway called before `Order` is built | AST test, source-order assertion | enforced |
| Close path consults the gateway | AST test on `ExecutionService.close` | enforced |
| All 20 checks ran | `_verify_coverage` on every decision | enforced |
| The event veto can only block a new entry, and only when enabled | AST test on `_check_event_veto`; `blocking_reasons`; not in `EXIT_CHECKS` | enforced; **off by default and no worker wires a source yet** |
| Strategy-declared entry locks agree with the backtest | `tests/integration/test_lock_parity.py`; lock state rebuilt from the ledger per question | enforced in the gateway and engine; **no worker wires a lock source yet**, so lock-declaring documents are refused in paper/demo |
| A raising check blocks | `except Exception` → `check_error:` | enforced |
| Unknown input blocks | `None` handling in each check | enforced |
| Every attempt recorded, allowed or blocked | `evaluate` records before returning | enforced |
| Limit version + fingerprint on every decision | `LimitSet.stamp()` | enforced |
| Limits cannot be mutated in place | frozen dataclass; `derive` requires a new version | enforced |
| Kill-switch state survives a crash | fsynced journal, recovered on startup | enforced |
| Kill switch never self-clears | no timeout path exists | enforced |
| Allocation reasoning is data | `AllocationReason` per pipeline step | enforced |
| Unmeasured correlation is conservative | `unmeasured_correlation = 0.30` | enforced |
| **Automatic drawdown-triggered flatten** | — | **not implemented** |
| **Lifecycle transition machine** | — | **not implemented**; the gateway reads a lifecycle nothing owns |
| **Strategy degradation detection** | — | **not implemented**; `StrategyView.degraded` is an input nothing computes |
| **Live fleet state assembly** | `workers/live_worker.py` assembles a `RiskContext` and is tested | the worker exists; **no entrypoint starts it**, and `fiboki worker run live` refuses by design |

The first three are the honest limit of this layer today. Every check is written, tested and
mandatory, and the live worker that would assemble a real `RiskContext` exists and is tested —
but nothing has ever run a real book through it, because no application entrypoint owns the
wiring and no venue credential exists. The gateway is proved correct against constructed
contexts; it has never been proved correct against a real one.
