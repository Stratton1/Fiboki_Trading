# Execution Architecture

**Snapshot:** 2026-09-19T05:05Z (`pytest tests/ -q` → 2683 passed, 2 skipped). Packages: `src/fiboki/sim/`, `src/fiboki/broker/`.
Tests: `tests/golden/test_golden_fills.py`, `test_golden_pnl.py`,
`tests/integration/test_execution_lifecycle.py`, `test_paper_backtest_parity.py`,
`test_both_leg_cost_impact.py`, `tests/unit/test_profiles.py`, `test_mode_guard.py`,
`test_ig_adapter.py`, `test_oanda_adapter.py`, `test_no_gateway_bypass.py`,
`test_sizing_authority.py`, `test_engine_determinism.py`, `test_limits_and_venue.py`.

---

## 1. The simulator's fill model

`sim/fills.py` turns an intention plus a bar into a price and a cost breakdown. Three
conventions govern it, and each one is a V1 defect inverted.

**Bars are MID prices.** Stop and take-profit levels are compared against mid high and low,
and the half-spread is then applied to the resolved level to produce a dealt price. This is
the only defensible treatment of mid-quoted history; assuming bid/ask bars would require data
we do not have. Where the underlying data is *not* mid — HistData is bid — the conversion is
explicit and the result is stamped `SYNTHETIC_MID`, never `MID`.

**Costs are charged on every leg.** `simulate_entry` and `resolve_exit`/`market_exit` both
return a populated `LegCosts`. There is no code path that opens or closes a position without
one. V1 called `_apply_costs` from the entry path only, so exactly half the true round-trip
spread was missing; on a measured EURUSD H4 run that single omission accounted for roughly 46%
of reported net profit. `tests/integration/test_both_leg_cost_impact.py` re-measures the
impact rather than asserting it from memory.

**Slippage is adverse-only.** `SlippageModel.slippage_price` returns a non-negative price
amount and the fill model applies the sign, so a long pays more and a short receives less. A
model that could return a negative number would be modelling price improvement, which no
honest research profile should assume; `FixedPointsSlippage` raises on a negative parameter.
Slippage applies to market and stop orders and **not** to take-profit limits, because a limit
either fills at its price or does not fill. The compensating pessimism is that a limit is never
granted price improvement on a gap.

### Gaps

One helper produces every gap behaviour the engine needs:

```python
def _worse_exit(direction, a, b):
    return min(a, b) if direction is Direction.LONG else max(a, b)
```

- long stop, bar opens below it → `min(stop, open) = open` — the gap loss is taken;
- long target, bar opens above it → `min(target, open) = target` — no windfall;
- short stop, bar opens above it → `max(stop, open) = open` — the gap loss is taken;
- short target, bar opens below it → `max(target, open) = target` — no windfall.

V1 returned `position.stop_loss` unconditionally, so a long stopped at 1.0950 on a bar opening
at 1.0800 was recorded as exiting at 1.0950 — a free 150-pip gift on every gapped stop, across
1,125 weekly-open bars in the EURUSD H1 sample. That is the single largest reason V1's tail
risk looked survivable.

### Resolution order

`resolve_exit` is chronological, which matters:

1. **The open is the first observable price.** If it is already through a level, that level
   fills at the open (for a stop) or at the level (for a target). No ambiguity policy applies,
   because the open genuinely came first.
2. Otherwise both levels are checked against the bar's range, and if both were touched,
   `IntrabarPolicy` decides.

### The intrabar ambiguity, named rather than hidden

Without tick data the true resolution of a bar that touches both the stop and the target is
**unknowable**. `IntrabarPolicy` makes the assumption a named, measurable choice:

| Policy | Behaviour | Use |
|---|---|---|
| `STOP_FIRST` | assumes the worst ordering. **The default.** | publishing a result |
| `TARGET_FIRST` | assumes the best ordering | provided only so the gap between the two can be quantified; never publish from it |
| `PROPORTIONAL` | draws with `p(stop first) = d_tp / (d_stop + d_tp)` using the profile's seeded RNG | unbiased in expectation under a random-walk assumption, at the cost of Monte-Carlo noise |

V1 took whichever branch its `if` tested first — the target — which is the most optimistic
assumption available, and it was never written down.

### Other fill-model behaviour

`Bar.__post_init__` refuses an incoherent bar outright: if the low/high bracket does not
contain the open and close, it raises and tells the caller to repair or reject upstream. The
fill model will not reason about impossible bars.

Staleness is detected, not ignored. `is_stale` flags a gap since the previous bar exceeding
`profile.stale_price_max_gap_multiple`. A stale price is not necessarily bad — every Monday FX
open is a 49-hour gap — but it *is* a price you could not have dealt on continuously, so the
half-spread is multiplied by `stale_spread_multiple` (default 2.0) on both entry **and** exit.
Widening only entries would make the exit leg systematically cheaper, which is a subtler
version of the bug this module exists to kill. `reject_on_stale` is available for research that
wants to refuse those bars entirely.

Minimum stop distance is a broker fact with three possible responses (`MinStopPolicy`):
`REJECT` (broker-accurate — IG refuses the ticket), `WIDEN` (trader-accurate — push the stop
out, which honestly increases the risk taken relative to what the sizer assumed, and the engine
records the widened stop on the position), and `ALLOW` (research-only).

Guaranteed stops, when a profile enables them, fill **at** the level even through a gap and
never slip, and the premium is charged only when the stop is triggered — which is when IG
charges it.

`FxSessionCalendar` closes FX from Friday 22:00 UTC to Sunday 22:00 UTC. Holidays are **not**
modelled; that is a documented gap, mitigated in practice because holiday bars are usually
absent from the data anyway, in which case the engine has nothing to fill against.

### Documented approximations of the fill model

- Intrabar path is unknowable; `STOP_FIRST` is an assumption, not a measurement.
- Spread is a static per-instrument figure scaled by a profile; the real distribution is being
  recorded (`DATA_ARCHITECTURE.md` §9) and is not yet used.
- Financing is a static annual rate over the whole sample. Real 2000–2025 financing swung from
  roughly 0% to 5.5%; that matters for multi-week holds. `FinancingModel`'s docstring says so.
- **Weekend triple-swap is not modelled.** `backtest/engine._nights_between` counts calendar
  rollover crossings, understating financing on Wednesday-held positions by up to two nights a
  week.
- Latency is expressed in whole bars (`latency_bars`), not milliseconds.
- Partial fills are drawn from a probability, not from a depth model.
- Market orders only. There is no limit-order queue model, and none is claimed.

## 2. Broker execution profiles

`sim/profiles.py` expresses cost and friction as **data**. V1 hardcoded
`spread = instrument.typical_spread_pips` inside the backtest loop and `commission = 0.0`
inside the paper bot, so changing an assumption meant editing engine code and nobody ever
measured how sensitive a result was to its own cost assumptions. Every "edge" V1 reported was
an edge at one un-named, un-versioned set of frictions.

A profile is a frozen dataclass tree of small strategy objects with a name, a `fingerprint()`
that is stamped onto every result, and a `seed`. Spread and slippage models return **price
units**; commission and financing return an amount in an explicitly named currency (`"quote"`
means the instrument's quote currency), and the engine converts through `core/money.py`. There
is no implicit 1.0 anywhere.

| Profile | Spread | Slippage | Commission | Financing (long/short bps) | Min stop | Other |
|---|---|---|---|---|---|---|
| `IDEALISED_RESEARCH` | 0 | none | none | none | none, `ALLOW` | Frictionless. **Its only legitimate use is as the numerator of a cost-impact ratio.** A result quoted from it alone is not a result. |
| `IG_REALISTIC` | typical ×1.0, floor 0.6 pips; ×3.0 at 21:00–23:00 UTC rollover; ×1.8 at 23:00–06:00 | 30% chance of 1–3 × 0.4-pip adverse ticks | none (FX/metal CFDs are spread-only at IG) | 290 / 110, 365-day basis | 4.0 pips, `REJECT` | 0.5% rejection; GSLO premium 3 pips, off by default |
| `OANDA_REALISTIC` | typical ×0.85, floor 0.4; ×2.5 rollover; ×1.5 Asia | 25% chance of 1–3 × 0.3-pip ticks | none | 250 / 90, 365 | none, `ALLOW` | 2% partial fills ≥50%; 0.3% rejection |
| `IBKR_REALISTIC` | typical ×0.45, floor 0.2; ×2.0 rollover; ×1.3 Asia | 20% chance of 1–2 × 0.2-pip ticks | 0.20 bps of notional, **USD 2.00 minimum, denominated in USD** | 150 / 40, 360 | none, `ALLOW` | 5% partial fills ≥25% |
| `SEVERE_STRESS` | typical ×3.0, floor 2.0; ×5.0 rollover; ×3.0 Asia | 70% chance of 1–5 × 1.0-pip ticks | 0.50 bps, USD 3.00 min | 500 / 350, 360 | 10.0 pips, `REJECT` | 15% partial fills ≥30%; 3% rejection; **1 bar of latency**. A survivability test, not a forecast. |

The IBKR commission being denominated in USD rather than the account currency is deliberate and
is the reason `LegCosts` carries `commission_ccy` separately: broker schedules are denominated
independently of the instrument, and V1's habit of quietly renaming a USD figure to the account
currency is exactly what `core/money.py` exists to stop.

### Determinism of stochastic models

Every stochastic model takes an explicit `numpy.random.Generator`; models never create their
own, so reproducibility is the caller's contract and cannot be broken by import order or dict
iteration. `rng_for(seed, bar_index, sequence)` derives an independent counter-based generator,
so the draw for bar 900 order 2 is identical whether or not bar 400 drew anything. That is
stronger than a single shared stream: **adding a second instrument to a portfolio does not
silently change the fills of the first.**

`ProbabilisticAdverseTickSlippage` draws exactly two uniforms in a fixed order on every call,
whether or not it slips, so consumed entropy is constant and the generator's state is
predictable and auditable from a test.

## 3. The adapter interface

`broker/base.py`. Two prohibitions define it, both V1 post-mortem findings.

**An adapter must not size.** V1's IG adapter recomputed position size from the live broker
balance, so the internal ledger and the venue disagreed by construction — no bug was needed.
Here an adapter receives an `Order` whose `size` was decided once by
`portfolio.sizing.size_trade`, and its only freedom is to express that number in the venue's
units.

**An adapter must not construct a risk decision.** Permission is granted upstream by
`risk.gateway.RiskGateway` and nowhere else. An adapter may *report* that the venue refused an
order; it may not decide that one is acceptable.

Both are enforced over the AST by
`tests/unit/test_no_gateway_bypass.py::test_adapters_do_not_size`, which parses
`broker/paper.py`, `broker/oanda.py`, `broker/ig.py` and `broker/simulated_venue.py` and fails
on any call to `size_trade`, `size_for` or `PortfolioSizer`. (Note: `broker/base.py`'s docstring
cites `tests/unit/test_adapter_prohibitions.py`, which does not exist; the enforcement is real,
the citation is stale.)

**Idempotency is part of the contract.** Every adapter must transmit `Order.client_ref` to the
venue in whatever field it provides for client-supplied references, and must return the venue's
own reference in `OrderAck.broker_ref`. Reconciliation is keyed on the broker's reference.

The error taxonomy is the part that matters most:

| Exception | Meaning | Consequence |
|---|---|---|
| `BrokerRejected` | the venue positively refused | **terminal**; the order provably never became a position |
| `BrokerUnavailable` | transport failed | the order's fate is **UNKNOWN**, not rejected |
| `DuplicateClientRef` | the venue has seen this reference | not an error to retry through — the idempotency key doing its job |

V1 treated a timeout as a rejection and moved on, which is how a real position ended up with no
local record.

### The adapters

**`PaperBroker`** (`broker/paper.py`) does not reimplement filling. It imports `FillSimulator`
and drives it with the same profile, intrabar policy, calendar, bar-index/sequence RNG
derivation and per-bar ordering as `BacktestEngine`:

1. financing for nights crossed since the previous bar;
2. fill pending orders at this bar's **open**;
3. resolve exits, including positions opened on this very bar — gap risk is real and a position
   is exposed the instant it exists;
4. mark to market on this bar's **close**;
5. bankruptcy guard.

Step 6 of the engine's loop — the strategy seeing the closed bar — is deliberately absent.
Signals are generated upstream, sized once, and arrive as orders after `on_bar` returns, which
is exactly where the engine assigns its sequence numbers, so the RNG streams line up.
`tests/integration/test_paper_backtest_parity.py` runs identical bars through both and compares
the canonical ledger **text** byte-for-byte, across `IDEALISED_RESEARCH`, `IG_REALISTIC`,
`OANDA_REALISTIC` and `SEVERE_STRESS`. The structural reason parity holds rather than being
asserted: the paper adapter is handed one bar and never sees another, so it is in the live
position by construction.

**`SimulatedVenue`** (`broker/simulated_venue.py`) models *venue* friction dishonestly on
purpose, so the execution service can be tested against the things that only break in
production: configurable latency, positive rejections, partial fills where the remainder is
*rejected* rather than silently forgotten, transport disconnection, duplicate-reference refusal
— and `ack_then_fail`, the important one, where the venue accepts the order, assigns a
reference and opens a real position, and then the response never arrives. That is the
crash-mid-order scenario that left V1 with real positions and no local record, forever.

**`OandaAdapter`** (`broker/oanda.py`) is the forward path. Every request goes through an
injected `Transport`; `RecordedTransport` replays fixtures captured from the documented
response shapes, so auth headers, instrument mapping, order placement, client-extension
idempotency, stop/TP attachment, position query, rate-limit pacing and every host-safety
control are tested without a byte leaving the process. `RateLimiter` is a correctness control
rather than a politeness one: a 429 on an order submission is indistinguishable from a timeout,
so the order's fate becomes UNKNOWN. Two documented behaviours: the venue's `marginRate` is read
but **not** used for sizing (surfaced for reconciliation only), and unit conversion to the
venue's precision **fails loudly** if it would change the number, because silently rounding is
how an adapter starts re-deciding size.

**`IgAdapter`** (`broker/ig.py`) is **dormant/legacy**, retained for one reason: V1's single
strongest control was that the IG base URL was a module constant naming the demo host with no
code path substituting a live one. That control is preserved and strengthened.
`IG_DEMO_BASE_URL = "https://demo-api.ig.com/gateway/deal"` is a constant, the constructor takes
**no** base-url argument, and `_assert_demo_only` re-parses the constant at construction and at
every request, comparing the parsed hostname against `IG_DEMO_HOST`. If somebody edits the
constant to a live host, the adapter refuses to run at all. Enabling live IG trading is
therefore not a configuration change: it is a source change that has to delete a check that
exists to stop exactly that, in a reviewed commit. `live_execution_available()` always returns
False. Scope is deliberately thin — session auth, account, positions, market spec, market order
with a `dealReference` idempotency key, close — and everything else raises `NotImplementedError`
rather than returning a plausible empty result.

## 4. The order lifecycle

`broker/execution_service.py`. The protocol:

```
gateway decision  ->  write PENDING intent (fsynced)  ->  dispatch
                                                             |
                      ack: persist broker_ref immediately  <--+
                      rejection: terminal REJECTED
                      anything else: UNKNOWN, reconcile
```

**The PENDING record is written and flushed to durable storage before the dispatch call is
made.** If the process dies at the worst possible instant, the record exists, the client
reference in it is the one the venue was given, and `reconcile` can find the position again.

`client_ref_for(plan)` is **deterministic in the plan**. A random key per attempt would make
every retry a new order, which is the bug rather than the fix; deriving it from `plan_id` means
submitting the same plan twice is detectable both locally and at the venue.

### States

| State | Terminal | Blocks resubmission | Needs reconciliation | Meaning |
|---|---|---|---|---|
| `PENDING` | no | yes | yes | written before dispatch; after a restart, an order may or may not have reached the venue |
| `UNKNOWN` | no | yes | yes | dispatched, outcome genuinely unconfirmed. **Not a rejection.** |
| `ACKED` | no | yes | yes | venue accepted; `broker_ref` persisted |
| `FILLED` | yes | **yes** | no | already reached the book — resubmitting would double a real position |
| `CLOSED` | yes | **yes** | no | same reason |
| `REJECTED` | yes | no | no | the venue refused; it provably never became a position |
| `CANCELLED` | yes | no | no | same |
| `BLOCKED` | yes | no | no | stopped by the gateway or mode guard; never reached a venue |

The `FILLED`/`CLOSED` rows are the subtle ones: terminal but still blocking, because "this plan
already reached the book" is a reason not to retry.

`InMemoryIntentStore` is legitimate for tests and backtests only. A paper or broker-touching
deployment must use `JsonlIntentStore`, which appends and fsyncs on every write — append-only
because the audit question is "what did we believe, when?", not "what do we believe now"; fsync
because the crash being defended against is precisely the one that eats the page cache.

`ExecutionService` is the **only** thing in the codebase that constructs an `Order`. That is
the design, not a coincidence: making the gateway mandatory means making the `Order`
constructor unreachable from anywhere that has not already obtained a `RiskDecision`, and
`tests/unit/test_no_gateway_bypass.py` walks the whole source tree and fails if any other module
constructs one.

## 5. Reconciliation

**Keyed on the broker reference.** V1 built Fiboki positions keyed on an internal `uuid4` and
broker positions keyed on IG's `dealId` — two key spaces that never intersect — so it reported
every position missing on both sides and could not produce a clean result even on a perfectly
healthy system. Its output was therefore ignored, so it may as well not have existed.

The one exception is an intent that never learned its broker reference because the response was
lost. For those, and only those, the venue is searched by the `client_ref` we sent, in order to
*learn* the broker reference, which is then persisted and used for everything thereafter. That
is what the client reference is for.

`RecoveredPosition` and `RecoveryReport` cover the restart case: a position the venue holds is
re-associated with the intent that created it, so it is no longer orphaned. V1's `recover()`
restored bot state but not the position object, so a bot persisted as `position_open` came back
unable to re-enter and unable to exit, and the broker position was orphaned permanently.

`ReconciliationReport` is the artefact. `obs/alerts.AlertEvent.RECONCILIATION_DIVERGENCE` exists
for the case where it is non-empty; the alert taxonomy is in `OBSERVABILITY_STANDARD.md`.

**Not yet wired at this snapshot:** nothing calls `reconcile` on startup or on a timer. The
method exists and is tested (`tests/integration/test_execution_lifecycle.py`); the scheduler
that would call it does not exist.

## 6. Execution-mode isolation

`core/enums.ExecutionMode` is ordered by increasing danger: `BACKTEST`, `PAPER`, `SHADOW`,
`DEMO`, `LIVE`, with `touches_real_money` true only for `LIVE` and `touches_broker` true for
`SHADOW`, `DEMO` and `LIVE`.

V1's arrangement was: one committed string, compared wrongly. `FIBOKEI_TRADOVATE_BASE_URL`
could override the base URL, and the live gate was an exact string comparison that a trailing
slash defeated; `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` was committed as a literal in
`render.yaml`.

### Every live control, enumerated

`broker/mode_guard.py` requires **all five** of these to hold independently before `ModeGuard`
will authorise `ExecutionMode.LIVE`:

1. **Build-time constant.** `LIVE_EXECUTION_COMPILED_IN: bool = False` is a module constant in
   source. No environment variable, config file, database row or API call changes it. Enabling
   live trading requires a code change, a review and a deploy.
2. **Runtime environment flag.** `FIBOKI_LIVE_RUNTIME_ARMED` must equal `LIVE_ARM_TOKEN`
   exactly — `"ARMED-LIVE-EXECUTION-I-ACCEPT-REAL-MONEY-RISK"`. Deliberately not `"true"`: a
   value that cannot be guessed cannot be set by accident, and this is not a string anybody
   types into a deploy file to see what happens.
3. **Persisted operator authorisation.** A stored record naming a human, with a granted
   timestamp and an expiry, checked against the clock — so an authorisation left behind after an
   incident goes stale by itself. A missing or unreadable file means **no** authorisation;
   `LiveAuthorisationStore` never falls back to a permissive default, and the file being absent
   is the normal, safe state.
4. **Per-strategy allow-list.** The specific `strategy_id` must be named in that authorisation.
   Approving the platform for live trading does not approve every strategy on it.
5. **Parsed-hostname assertion.** The venue URL is parsed with `urllib.parse.urlsplit` and its
   `hostname` compared against an allow-list. This is the control V1's trailing slash defeated:
   `https://api.example.com` and `https://api.example.com/` have the same parsed hostname and
   different strings, and `https://api.example.com.evil` has a different hostname and a matching
   prefix.

The guard also runs in the safe direction: a `DEMO` or `PAPER` run whose venue URL resolves to a
known live host is refused, because pointing demo credentials at a live host is how "we were
only testing" becomes an incident.

`tests/unit/test_mode_guard.py` sets each control on its own and asserts live is **still**
blocked, and asserts that a config file alone cannot enable it.

### Adapter-level controls, independent of the mode guard

The OANDA adapter has **three more**, deliberately named differently so that flipping one does
not flip another:

- `OANDA_LIVE_HOST_COMPILED_IN: bool = False` — a build-time constant in `broker/oanda.py`,
  distinct from `LIVE_EXECUTION_COMPILED_IN`. Two separate source edits, two reviews.
- `FIBOKI_OANDA_LIVE_RUNTIME` must equal `OANDA_LIVE_RUNTIME_TOKEN` =
  `"OANDA-LIVE-HOST-ARMED-I-ACCEPT-REAL-MONEY-RISK"` exactly.
- A parsed-hostname assertion against `OANDA_PRACTICE_HOST = "api-fxpractice.oanda.com"` and
  `OANDA_LIVE_HOST = "api-fxtrade.oanda.com"`. Anything that is neither is refused outright:
  **there is no "unknown host, probably fine" branch.** The default `base_url` is the practice
  host.

The IG adapter has a fourth kind: a live host is not expressible at all. `IG_DEMO_BASE_URL` is
the only URL it will build a request from, the constructor takes no base-url argument, and the
hostname is re-asserted at construction and at every request.

### The complete list of what must be true for a live order

| # | Control | Where | Default |
|---|---|---|---|
| 1 | `LIVE_EXECUTION_COMPILED_IN` | `broker/mode_guard.py` source constant | `False` |
| 2 | `FIBOKI_LIVE_RUNTIME_ARMED` == `LIVE_ARM_TOKEN` | environment | unset |
| 3 | persisted `LiveAuthorisation`, unexpired, named human | `<state_dir>/live_authorisation.json` | absent |
| 4 | `strategy_id` in that authorisation's allow-list | same file | n/a |
| 5 | parsed venue hostname in the allow-list | `ModeGuard` | n/a |
| 6 | `OANDA_LIVE_HOST_COMPILED_IN` | `broker/oanda.py` source constant | `False` |
| 7 | `FIBOKI_OANDA_LIVE_RUNTIME` == `OANDA_LIVE_RUNTIME_TOKEN` | environment | unset |
| 8 | adapter base URL parses to `api-fxtrade.oanda.com` | `OandaAdapter` | practice host |
| 9 | `FIBOKI_EXECUTION_MODE=live` | environment, read once at start | `paper` |
| 10 | strategy lifecycle is `LIVE` | `RiskGateway._check_strategy_lifecycle` | blocked otherwise |
| 11 | kill switch inactive | `RiskGateway._check_kill_switch` | n/a |
| 12 | the other 16 gateway checks pass | `RiskGateway.CHECKS` | fail-closed |

Two of those are source constants requiring separate reviewed commits; two are unguessable
environment tokens; one is a persisted human authorisation with an expiry; one is per-strategy;
three are hostname parses; and the remainder are runtime risk checks that fail closed. There is
**no API endpoint, for any role, that can move the platform to LIVE** — `api/__init__.py` states
that asking for live over HTTP returns 403 with the list of controls.

An unrecognised `FIBOKI_EXECUTION_MODE` is a startup error, not a silent fallback to paper,
because a typo must not decide where orders go.

## 7. Known gaps at this snapshot

- **Nothing drives any of this in anger.** `workers/live_worker.py` exists and is tested, and
  `fiboki worker run live` **deliberately refuses** to start it: the live worker needs a wired
  execution service, feed, evaluator and risk-context builder, and *"a live worker assembled from
  command line flags is a live worker whose risk configuration nobody reviewed."* The application
  entrypoint that owns that wiring is not written.
- **Reconciliation is not scheduled.** `reconcile` and `fiboki broker reconcile` exist and are
  tested; nothing calls either on startup or on a timer.
- **No OANDA credentials** in this environment, so the adapter is proved against recorded
  fixtures and has never spoken to the venue. The two OANDA unknowns — whether v20 can place
  orders on a spread-betting sub-account, and how the candle endpoint compares to the account's
  own executable stream — remain open and should be resolved on a free demo **before** more
  adapter code is written.
- **`broker/base.py` cites a test file that does not exist** (`test_adapter_prohibitions.py`);
  the enforcement lives in `test_no_gateway_bypass.py`.
