# `fiboki.lifecycle` — strategy lifecycle and forward validation

The machinery that compares expectation against reality and demotes a strategy before it costs
money.

## Why this package exists

`StrategyLifecycle` has been in `core/enums.py` since the beginning, and it was **consumed by
everything and owned by nothing**. `risk/gateway.py::_check_strategy_lifecycle` blocks seven of
its states. `portfolio/construction.py` allocates risk to six of them and scales two down. Both
are tested. And yet:

- no module made a transition, or validated one;
- `StrategyDocument` carried no lifecycle field (and still does not — see below);
- nothing computed a degradation signal, so `StrategyView.degraded` was always whatever the
  caller passed and `AlertEvent.STRATEGY_DEGRADED` had no producer;
- none of the audit's three pre-registered stopping rules existed.

`ROADMAP.md` §2 and §3 and `VALIDATION_STANDARD.md` §7 both stated this plainly. This package is
the owner those documents said was missing.

## Modules

| Module | Owns |
|---|---|
| `state.py` | the 13 states, the legal edges as data, the hash-chained append-only transition log, `StrategyRecord` |
| `promotion.py` | objective criteria per edge, versioned, built on `validation/gates.py` |
| `monitor.py` | eleven-dimension forward validation with block-bootstrap confidence |
| `stopping_rules.py` | PSR floor, bootstrap drawdown limit, CUSUM on excess return — pre-registered, latched |
| `degradation.py` | one score, with hysteresis, driving WATCH → DEGRADED → QUARANTINED |
| `service.py` | what the live worker calls and the API reads |

## The state diagram

```
                      ┌──────────── the laboratory ─────────────┐
   register ─────────▶│                                         │
                      │  DISCOVERY ─▶ RESEARCH ─▶ VALIDATING ─▶ │─▶ CANDIDATE
                      └─────────────────────────────────────────┘      │
                                                                       │ (human)
                      ┌──────────────── running ─────────────────┐     ▼
                      │  PAPER ─▶ SHADOW ─▶ DEMO ─▶ APPROVED ─▶ LIVE   │
                      └──┬──────────┬────────┬─────────┬──────────┬────┘
                         │          │        │         │          │
     health escalation   ▼          ▼        ▼         ▼          ▼
     (automatic)              WATCH ─▶ DEGRADED ─▶ QUARANTINED
                                 │        │            │
     recovery (HUMAN only) ◀─────┴────────┴────────────┘
     demotion (automatic) ───────────────▶ back down the ladder, any distance
     retirement (HUMAN only) ────────────▶ RETIRED   (terminal)
```

**105 legal edges**, all of them data in `LEGAL_TRANSITIONS`: 1 registration, 8 promotions,
48 demotions, 18 health escalations, 18 recoveries, 12 retirements. Anything not in that table
raises `IllegalTransition`.

Three structural properties:

- **A promotion moves exactly one stage.** The eight promotion edges are exactly the adjacent
  pairs of the ladder. `CANDIDATE → DEMO` is not a fast path, it is an error.
- **A demotion may drop several stages.** Getting out is always allowed to be faster than getting
  in, and an automated rule may do it.
- **`RETIRED` is terminal.** A revived idea is a new content hash and a new record, so the retired
  one's history stays exactly as it was.

## The transition table

| Kind | Edges | Actor required | Authorisation | Note |
|---|---|---|---|---|
| `REGISTRATION` | `DISCOVERY → DISCOVERY` | any | no | the only self-transition; written by `register()`, never requestable |
| `PROMOTION` | `DISCOVERY → RESEARCH`, `RESEARCH → VALIDATING`, `VALIDATING → CANDIDATE` | any (the research loop may promote inside the lab) | no | nothing has been allocated risk yet |
| `PROMOTION` | `CANDIDATE → PAPER`, `PAPER → SHADOW`, `SHADOW → DEMO`, `DEMO → APPROVED` | **human** | no | each one puts the strategy somewhere that generates orders |
| `PROMOTION` | `APPROVED → LIVE` | **human** | **yes** | see *Reaching LIVE* below |
| `DEMOTION` | any ladder state → any lower one; health states → the lab | any (rules do this) | no | the automatic path |
| `HEALTH_ESCALATION` | any running state → WATCH/DEGRADED/QUARANTINED; WATCH → DEGRADED → QUARANTINED | any (rules do this) | no | driven by `degradation.py` |
| `RECOVERY` | health state → running state; QUARANTINED → DEGRADED → WATCH | **human** | **yes** into LIVE | a halt reverses only by an explicit operator action |
| `RETIREMENT` | every state except RETIRED → RETIRED | **human** | no | the automated path stops at QUARANTINED |

A recovery additionally may not return a strategy **higher than it was when it left the ladder**:
`StrategyRecord.pre_health_state` remembers where it fell from, and the machine enforces the
ceiling.

## Promotion criteria

Each edge's criteria are `Gate` rows from `validation/gates.py` — a name, a metric, a comparison,
a number and the reason the number is what it is — in `PROMOTION_RULES_V1`
(`version="lifecycle_promotion_v1"`, fingerprinted). Reusing `Gate` inherits the property that
matters most: `GateStatus.NOT_EVALUATED` **blocks**, so a criterion whose input nobody computed is
not a criterion that passed. The `rung` field on every gate here is `-1`, because these gates are
not produced by a ladder rung and claiming a rung they do not have would make the record lie.

### `DISCOVERY → RESEARCH` — does this deserve any compute?

| Gate | Requirement |
|---|---|
| `hypothesis_states_evidence_against` | yes. `STRATEGY_STANDARD.md`: a hypothesis must carry the published evidence *against* the idea |
| `structurally_novel` | yes. `research/structure.is_reparameterisation` answers this mechanically |

### `RESEARCH → VALIDATING` — cheap in-sample screens

| Gate | Requirement |
|---|---|
| `screen_trades` | `in_sample_trades ≥ 100` — a screen, not the bar; the bar is 400 at rung 0 |
| `screen_sharpe` | `in_sample_sharpe ≥ 0.30` — in-sample and un-deflated, therefore worthless as evidence |
| `parameter_count` | `≤ 8` — beyond this the honest trial count grows faster than any plausible edge |

Its only job is to avoid spending a full ladder — CPCV, SPA, stress, a holdout look that can never
be taken back — on something that is not even in-sample profitable. **Passing it proves nothing.**

### `VALIDATING → CANDIDATE` — Gate A, the statistics

Structural requirements:

- a `ValidationReport` with a `PROMOTE` verdict,
- produced under the **production** gate set: the report's `gate_set_fingerprint` must equal
  `GATE_SET_V2.fingerprint()`. A report that cleared a softer, overridden set does not count;
- with no `NOT_EVALUATED` gates on it;
- and `holdout_consumption_count == 1` **exactly**. `0` blocks, `2` blocks, and `None` — nobody
  asked the registry — blocks.

| Gate | Requirement |
|---|---|
| `external_trial_count_declared` | `≥ 1`. Leaving the external count at zero deflates against a floor. Requiring it to be non-zero does not make it honest — nothing can — but it refuses the default |

### `CANDIDATE → PAPER` — the config check

| Gate | Requirement |
|---|---|
| `live_execution_disabled` | yes — `DEPLOYMENT.md` Gate A's automated check |
| `risk_limits_reviewed` | yes |
| `stopping_rules_pre_registered` | `≥ 3`. **All three rules must exist before the first forward observation, not before LIVE.** Paper data is forward data |

### `PAPER → SHADOW` — Gate B, first half

| Gate | Requirement |
|---|---|
| `min_days_in_paper` | `days_in_state ≥ 30` (calendar time) |
| `min_paper_trades` | `forward_trades ≥ 40` |
| `max_heartbeat_gap` | `max_unexplained_heartbeat_gap_hours ≤ 1.0` |
| `forward_sharpe_agreement` | `forward_to_backtest_sharpe_ratio ≥ 0.50` |
| `no_divergent_dimensions` | `divergence_dimensions_flagged ≤ 0` |

### `SHADOW → DEMO` — Gate B, second half

| Gate | Requirement |
|---|---|
| `min_days_in_shadow` | `≥ 14` |
| `min_shadow_trades` | `≥ 20` |
| `worker_kill_alert_proven` | yes — the chaos test `DEPLOYMENT.md` Gate B names as a blocker |
| `reconciliation_clean_days` | `≥ 7` |
| `forward_sharpe_agreement`, `no_divergent_dimensions` | as above |

### `DEMO → APPROVED` — Gate C's evidence

| Gate | Requirement |
|---|---|
| `min_days_in_demo` | `≥ 90` |
| `min_demo_trades` | `≥ 100` |
| `sharpe_inside_backtest_ci` | yes — inside the 90% **block**-bootstrap CI of the backtest Sharpe |
| `realised_cost_ratio` | `realised_cost_to_modelled_ratio ≤ 1.5` |
| `unreconciled_fills` | `≤ 0`. Zero, not "few" |
| `forward_sharpe_agreement`, `no_divergent_dimensions` | as above |

APPROVED is deliberately a separate state from LIVE, so that the evidence review and the decision
to risk money are two acts on the record rather than one.

### `APPROVED → LIVE` — the decision

Requires a `HumanAuthorisation`, plus:

| Gate | Requirement |
|---|---|
| `min_days_in_approved` | `≥ 14` — a deliberate cooling-off period |
| `kill_switch_drill_completed` | yes, executed and timed |
| `approximations_accepted_in_writing` | yes — USD→GBP conversion, static spreads, zero default slippage, no overnight financing |
| `stopping_rules_pre_registered` | `≥ 3`, checked again here |
| `stopping_rules_registered_before_forward_data` | yes — catches a rule re-registered with kinder parameters once the returns were visible |
| `forward_sharpe_agreement`, `no_divergent_dimensions` | as above |

### Reaching LIVE

**Two independent controls, and neither can be satisfied by an automated rule.**

1. `PROMOTION_RULES_V1` marks `APPROVED → LIVE` as `requires_human_authorisation`.
2. `LifecycleStateMachine.transition` refuses **any** transition whose target is LIVE unless the
   acting party's kind is `HUMAN` **and** a matching `HumanAuthorisation` is among the evidence.
   This check does not consult the transition table, so an edit that loosened the table would
   still not open a path.

A `HumanAuthorisation` is bound to one strategy content hash and one target state, and carries a
statement of at least 24 characters. It records a decision; it is not a credential and confers no
capability, because the machine checks the actor's kind separately. An authorisation for another
strategy, or for another state, does not transfer.

`tests/unit/test_lifecycle_state.py` and `tests/unit/test_lifecycle_promotion.py` parametrise over
every automated actor and every automated route — the last hop, the hop before it, and recovery
from each of the three health states — and assert the refusal in all of them.

## Forward validation (`monitor.py`)

Eleven dimensions, compared on every tick:

| Dimension | Statistic | Reference distribution |
|---|---|---|
| `RETURN` | mean | block-resampled **backtest** windows of the forward length |
| `SHARPE` | per-observation Sharpe | as above |
| `WIN_RATE` | fraction positive | as above |
| `DRAWDOWN` | compounded max drawdown | as above |
| `RETURN_DISTRIBUTION` | two-sample KS | block-permutation null over the pooled sample |
| `TRADE_FREQUENCY` | trade count | two-sided Poisson tail |
| `SPREAD`, `SLIPPAGE`, `LATENCY` | median | observed block bootstrap against `1.25 ×` modelled |
| `REJECTED_ORDERS` | rejection rate | one-sided binomial tail |
| `REGIME` | per-regime mean, worst regime reported | observed block bootstrap per `RegimeVector` key |

**The choice of reference is the whole argument.** Bootstrapping the *observed* series asks how
variable the statistic is within the forward record — so a forward record containing a disaster
reproduces the disaster in most of its own resamples and finds it unremarkable. Measured on a
planted 64% drawdown against a 4.9% expectation, the observed-series null returns `p = 0.016`;
the backtest-window null returns `p = 0.001`. Resampling the backtest also propagates the
backtest's own sampling error, and gets short-window tolerance for free: a 40-trade forward record
is judged against the spread of 40-trade backtest windows, which is wide, exactly as it should be.

Eleven dimensions each flagged at `α = 0.05` would flag a healthy strategy about half the time, so
the p-values are **Holm-adjusted across the dimensions evaluated in one report** and the status is
decided on the adjusted value. The dimensions are strongly dependent — return, Sharpe and win rate
are three views of one series — so Holm is conservative here, in the direction of demoting less
readily. That is the direction that needs an argument, and the argument is that this is a screen:
the halts are the pre-registered rules, which are not adjusted and not negotiable.

A dimension with too few observations is `NOT_EVALUATED`, **never** `AGREES`.
`DivergenceReport.coverage` states what fraction was actually computed.

### Named approximations

- **Trade frequency uses a Poisson tail.** Trade arrivals are not Poisson — they cluster around
  regime changes and session opens — so the interval is narrower than reality and this is the
  dimension most likely to flag spuriously.
- **Rejected orders use a binomial tail**, for the same reason and with the same consequence.
- **The distribution test's null assumes the two samples share a block structure.**
- **Per-regime comparison needs the labels supplied.** This module parses and validates regime keys
  through `marketstate.regime.RegimeVector`; it does not classify bars.
- **Spread, slippage, latency and regime have no backtest series**, only a modelled number, so they
  resample the observed series and ask whether the model is plausible. That is a weaker question,
  and the result says so.

## The three pre-registered stopping rules

### 1. PSR floor

Continuously compute the probabilistic Sharpe ratio of the forward returns against a benchmark of
**half** the backtested Sharpe. Halt when it falls to `0.50` or below.

The comparison is `≤`, not `<`, and that matters at exactly one point: a forward Sharpe precisely
equal to half the backtested Sharpe produces a PSR of 0.50 and **halts**. A strategy delivering
half of what it promised has not earned the benefit of the doubt, and the boundary case is the one
an operator is most likely to be arguing about.

A near-constant return series drives the moment estimators into catastrophic cancellation — the
Sharpe comes back as `1e15` and the skew and kurtosis as `NaN`, and `nan <= floor` is `False`. The
rule therefore checks finiteness explicitly and reports `NOT_EVALUATED`, because an uncomputable
PSR reading as a passing one is absence wearing the costume of a value.

### 2. Bootstrap drawdown limit

Halt at the **95th percentile of the block-bootstrap max-drawdown distribution derived from the
validation run** — not a round number. `calibrate_drawdown_limit` resamples the validation returns
in circular blocks (preserving the serial dependence that makes drawdowns deeper than an iid
resample suggests), computes each path's compounded max drawdown, and takes the quantile.

A round number is a statement about the operator's nerve; a strategy that breaches 20% may simply
be one whose normal bad quarter is 22%. This threshold is a statement about *this* strategy's own
resampled history.

**Caveat, stated on the function:** the distribution inherits the validation sample's regime mix.
A forward period containing a regime the validation window never saw can breach it without
anything having decayed. That is a reason to investigate, which is what a halt is for.

### 3. CUSUM on excess return

`S_t = max(0, S_{t-1} + (mu_expected - r_t))`, halting when `S_t` exceeds `h`.

The reflection at zero is what makes this a decay detector rather than a performance tally: good
periods bank no credit against future bad ones, so a strategy that delivers and then stops
delivering is caught on the second half rather than averaged out over both. A drawdown limit is
blind to exactly this failure — `tests/unit/test_lifecycle_stopping_rules.py` constructs a strategy
whose per-trade return drifts from +0.4% to −0.1% over 400 trades, asserts the CUSUM fires, **and
asserts the drawdown limit does not.**

#### The ARL calibration, and why it is a design choice

`h` is found by simulation, not by a closed form. `calibrate_cusum_threshold` seeds a search grid
on the driftless-random-walk reference `h ≈ σ·√(ARL)`, simulates the in-control process once for
the whole grid (the path of `S` does not depend on the threshold, only the stopping does),
interpolates `h` at the target ARL, and re-verifies at a **different seed**.

Derived in this session:

| Frequency | σ | Target ARL | **h** | Analytic `σ√ARL` | Simulated ARL | Independent check (2000 fresh paths) | Median run length | P(false halt in year 1) |
|---|---|---|---|---|---|---|---|---|
| daily, 252/yr | 0.010 | 504 obs (2.0 y) | **0.207530** | 0.224499 | 476 (−5.6%) | 477, 0.0% censored | 362 | 0.333 |
| per trade, ~500/yr | 0.010 | 1000 obs (2.0 y) | **0.301628** | 0.316228 | 961 (−3.9%) | 979, 0.0% censored | 731 | 0.311 |
| per trade, ~500/yr | 0.020 | 1000 obs (2.0 y) | **0.603256** | 0.632456 | 961 (−3.9%) | 979, 0.0% censored | 731 | 0.311 |

`h` scales exactly linearly in σ, as the reflected-random-walk argument predicts; only `h/σ` is a
free parameter.

**Three things in this calibration are ours, not the data's, and all three are recorded on
`CusumCalibration` so a later reader can disagree with them specifically rather than with the
number:**

1. **The two-year target** is a judgement about how much operator attention a false alarm costs,
   not a statistical fact. A shorter target halts sooner on real decay and more often on nothing.
2. **The in-control model is Gaussian.** Real excess returns are skewed and fat-tailed, which makes
   the true ARL at a given `h` *shorter* than simulated — so this errs towards halting more often
   than advertised. That is the safe direction, but it is still an error.
3. **The censoring horizon** (20× target) counts uncrossed paths as having crossed there, biasing
   every simulated ARL *downwards* and therefore `h` *upwards* — towards halting *less* often. The
   two biases point in opposite directions and neither is corrected. At these settings the censored
   fraction is 0.0%, so the second bias is inactive here.

**And the number an operator must be shown next to the ARL:** the first-passage distribution is
right-skewed, so the **median** run length (731 observations) is well below the mean (961), and the
probability of a false halt in the *first year* is **0.31**, not one-half of "one every two years".
`CusumCalibration.false_alarm_probability(n)` and `CusumParameters.median_run_length` exist so this
is reported rather than discovered.

### What "pre-registered" means, mechanically

- Parameter objects are frozen dataclasses: mutation raises `FrozenInstanceError`.
- `PreRegistrationStore.register` **never edits**. Registering different parameters for the same
  strategy and rule appends a *new* record whose `supersedes` names the old one. Both remain, in a
  hash chain, for ever — so "we loosened the rule after we saw the data" is a question anyone can
  answer from the artefact.
- An identical re-registration is a no-op, not a new row.
- `is_pre_registered(hash, first_forward_observation_at=…)` checks the **active** registration, not
  the earliest one. A rule registered in good time and then re-registered with kinder parameters
  once the returns were visible is **not** pre-registered, and answering on the first registration
  would let exactly that pass.
- The service refuses a partial set: all three rules or none.

### Halts latch

A firing writes to an append-only journal and **stays set even when a later evaluation comes back
clear** — because the condition clearing for a week is exactly what a decaying strategy does. This
is the opposite choice from `obs/alerts.py`'s level-based repeat suppression, and deliberately: an
alert is a notification, a halt is a decision that a human took the evidence seriously enough to
stop. `HaltRegistry.release` requires a named operator and a written reason, with no timeout and no
auto-reset — the same shape as `risk/killswitch.py`.

Releasing the halt does **not** restore the strategy. Clearing the latch and putting the risk back
on are two decisions, and they leave two records.

## Degradation and hysteresis

`score_degradation` folds the divergence report and the rule evaluations into one number in
`[0, 1]` under `DEGRADATION_CONFIG_V1` (`lifecycle_degradation_v1`):

| Component | Weight | Dimensions |
|---|---|---|
| `return` | 0.32 | return, Sharpe, win rate, return distribution |
| `cost` | 0.26 | spread, slippage, latency, rejected orders |
| `risk` | 0.20 | drawdown, trade frequency |
| `regime` | 0.10 | per-regime behaviour |
| `rules` | 0.12 | fired or latched stopping rules |

Costs carry nearly as much weight as returns on purpose: a return shortfall over a short forward
window is frequently luck, but a cost shortfall is a *measurement*, and it invalidates every stored
expectancy for the instrument rather than merely disappointing.

**Unevaluated components are excluded, not scored zero.** Their weight is removed from the
denominator, so the score is the weighted mean over the evidence that exists, and
`DegradationScore.confidence` reports how much weight that was. A score of 0.1 at 20% confidence is
not a healthy strategy, it is an unobserved one — and below `min_confidence = 0.30` the score is
reported but never acted on.

### Bands

| Band | Enter | Exit | Lifecycle state |
|---|---|---|---|
| WATCH | 0.20 | 0.12 | `WATCH` |
| DEGRADED | 0.40 | 0.28 | `DEGRADED` |
| QUARANTINED | 0.65 | 0.50 | `QUARANTINED` |

The levels are chosen for **reachability** against the weights — the check nobody does and which
quietly disarms most scoring systems. The heaviest single component is `return` at 0.32 and the
next is `cost` at 0.26, so a WATCH entry of 0.35 would be unreachable by any single component
failing completely: spread, slippage *and* latency all three times their model would score 0.22 and
raise nothing at all. A low WATCH entry costs nothing in false alarms, because
`Divergence.severity` is exactly 0.0 for any dimension that is not `DIVERGED`, and `DIVERGED` is
decided on a Holm-adjusted p-value — a clean report scores 0.000.

### Two mechanisms prevent flapping

1. **A gap, not a line.** Entering a band needs a higher score than staying in it.
2. **Dwell time.** Worsening needs 2 consecutive evaluations, recovering needs 3 — asymmetric,
   because the costs of the two errors are not equal.

Worsening may skip a band; recovery moves one band at a time, however good the score looks,
because a single good evaluation after a quarantine is the least trustworthy number in the system.

**A fired pre-registered stopping rule bypasses dwell entirely** and forces QUARANTINED. A fired
rule is not a noisy statistic.

**Recovery is not automatic.** `allow_automatic_recovery` defaults to `False`: the tracker computes
the recovery and reports it as a `recommended_band`, and the service notes it, but applying it
requires a named human at the state machine.

## The service

```python
service = LifecycleService(
    machine=LifecycleStateMachine(FileTransitionLog("var/lifecycle.jsonl")),
    registrations=FilePreRegistrationStore("var/stopping_rules.jsonl"),
    halts=HaltRegistry(FileHaltJournal("var/halts.jsonl")),
    dispatcher=alert_dispatcher,
)
evaluation = service.evaluate(content_hash, expectation=expected, observation=observed)
```

On each tick it compares expectation against observation, evaluates every registered rule, latches
any firing, folds everything into a degradation score, applies the automatic demotion that follows
under a named `AUTOMATED_RULE` actor, raises alerts, and leaves `service.status(hash)` behind.

It **can demote and cannot promote**: `promote()` exists but the state machine refuses any
non-human actor from CANDIDATE upwards, and LIVE regardless of what the service does.

It does **not** start a thread. The worker owns the timer; this owns the evaluation.

### Feeding the risk gateway

`StrategyStatus` carries exactly the three fields `risk.gateway.StrategyView` needs
(`lifecycle`, `health`, `degraded`), and the worker joins them:

```python
status = service.status(content_hash)
view = StrategyView(lifecycle=status.lifecycle, health=status.health, degraded=status.degraded)
```

This package deliberately does **not** import `fiboki.risk`, `fiboki.portfolio`, `fiboki.broker`
or `fiboki.api`; `tests/integration/test_lifecycle_service.py` asserts that structurally.

## Tamper resistance

Both the transition log and the pre-registration store are hash-chained: each record commits to its
predecessor's digest, so editing, removing or reordering any row invalidates every row after it,
and `verify()` names the first break. `LifecycleStateMachine` **refuses to start** against a broken
chain — a machine that silently served state from a tampered log would be worse than one that would
not start, because it would look like it was working.

Rewriting a record's own hash does not save it: the *next* record's `previous_hash` still names the
old digest. `test_a_record_hash_cannot_be_forged_without_the_predecessor` demonstrates exactly that.

## A field this package deliberately does not add

`StrategyDocument` still has no lifecycle field, and **it should not get one**. The document is
content-addressed and frozen, so a lifecycle stored on it would change the content hash every time
the strategy was promoted — and the holdout registry and the experiment ledger both key on that
hash. Lifecycle is a property of the *deployment* of a document, not of the document. It lives on
`StrategyRecord`, bound to the content hash, for the same reason the holdout registry keys on the
content hash: two bindings of one template are two strategies everywhere else in this system, and a
lifecycle keyed on the strategy id would let a reparameterisation inherit a promotion it never
earned.

## Known gaps

- **`AlertEvent` has no halt or quarantine event.** Every alert raised here uses
  `STRATEGY_DEGRADED`, with severity carrying the difference (WARNING for a divergence, ERROR for
  an automatic demotion, CRITICAL for a fired stopping rule). Adding `STRATEGY_HALTED` and
  `STRATEGY_QUARANTINED` to `obs/alerts.py` is the correct fix, made there, by whoever owns it.
- **Nothing calls `evaluate()` on a timer yet.** The live worker's wiring does not exist
  (`ROADMAP.md` §2), so this package is implemented and tested but **unwired** — exactly the state
  the roadmap describes for the live worker itself. Nothing in this package starts a process.
- **No API routes.** `StrategyStatus.to_dict()` and `LifecycleEvaluation.to_dict()` are
  API-shaped, but no route serves them.
- **The forward trade counts are the weakest numbers in `promotion.py`.** 40 paper trades cannot
  separate a Sharpe of 1.0 from one of 0.5 — `VALIDATION_STANDARD.md` §6 puts that at roughly 17
  years — and nothing here pretends otherwise. The forward stages detect *gross* divergence and
  cost surprises. Subtle decay is what the CUSUM is for.
- **The degradation tracker's dwell counter is not persisted.** It is re-served from the lifecycle
  record on restart, deliberately: a counter that survived a restart but whose evaluations did not
  would claim consecutive evidence it never had.
- **`is_pre_registered` needs the caller to supply the first forward observation time.** Nothing
  derives it, because nothing yet records when a strategy produced its first forward trade.
