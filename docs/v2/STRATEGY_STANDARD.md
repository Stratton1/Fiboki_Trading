# Strategy Standard

**Snapshot:** 2026-09-19T05:05Z (`pytest tests/ -q` → 2683 passed, 2 skipped). Modules: `src/fiboki/strategy/{dsl,primitives,
compiler,registry}.py`. Tests: `tests/unit/test_dsl_roundtrip.py`, `test_compiler_causality.py`,
`test_strategy_registry.py`, `test_indicator_causality.py`, `test_indicator_semantics.py`,
`tests/property/test_dsl_properties.py`, `tests/golden/test_indicator_values.py`.
Seed documents: `research/strategies/*.json` (5).

---

## 1. A strategy is data

In V2 a strategy is a JSON document validated against a pydantic schema, not a Python class.
That buys three things V1's hand-coded bots could not have: documents can be generated and
mutated by a search process; duplicates can be detected by content hash; and the whole
population can be audited by reading one directory.

`SCHEMA_VERSION` is `"2.0.0"` and is checked on load — a document declaring a different version
raises, with the instruction to migrate it explicitly rather than load it under the wrong
schema. `strategy_id` must match `^[a-z][a-z0-9_]{2,63}$`. Every model in the tree is declared
`frozen=True, extra="forbid"`, so a document carrying an unknown field cannot be constructed at
all — which is also what makes the agent sandbox boundary meaningful.

## 2. What a strategy document must contain

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | must equal `2.0.0` |
| `strategy_id` | yes | lowercase slug |
| `name` | yes | 3–120 characters |
| `hypothesis` | **yes** | minimum 120 characters; see §3 |
| `family` | yes | one of `ichimoku`, `trend_following`, `breakout`, `mean_reversion`, `momentum`, `fibonacci`, `volatility`, `hybrid` |
| `universe` | yes | at least one instrument, **all registered** — an unknown symbol raises with "register them in `core/instruments.py` rather than guessing contract specs" |
| `timeframes` | yes | at least one |
| `direction` | yes | `long`, `short` or `both` |
| `entry` | yes | a `RuleSet`, direction-tagged |
| `stop` | **yes** | see §4 |
| `regime`, `setup`, `confirmation`, `filters`, `invalidation` | no | additional rule stages |
| `take_profits` | no | a **list** of legs with allocation fractions |
| `trailing`, `position_management`, `sessions`, `events` | no | |
| `locks` | no | entry locks: `cooldown_bars_after_close`, `stop_streak`; see §4a. **Absent** from the serialised form when undeclared |
| `parameters` | no | name → `ParameterSpec` with an explicit sweep **domain** |
| `parent_strategy_ids`, `mutation` | no | lineage |
| `notes`, `author` | no | |

`complexity_score` is **derived, never supplied**. It is emitted on dump so a directory of
documents is greppable, and refused as an input so a hand-edited file cannot claim a complexity
it does not have. `StrategyRegistry.load_directory` compares the raw value to catch exactly such
an edit. The formula: 1.0 per rule (nested rules counted), 0.5 per tunable parameter, 0.5 per
take-profit leg beyond the first, 1.0 for a trailing model, 1.0 for pyramiding, and 1.0 for each
declared entry lock (a non-zero cooldown, a stop streak). A document without locks scores exactly
what it scored before locks existed. **Higher means
more ways to overfit**, and that is the whole reason it is measured.

## 3. The mandatory hypothesis, and the evidence against

`hypothesis` is required and has a schema-level minimum length. The rule behind the field:
**a rule set with no economic story is a curve fit waiting to be discovered.**

The requirement is not merely to state a story. A strategy document must state **what the
published evidence says against it**, and the five seed documents do. The Ichimoku seed is the
clearest example and is worth quoting, because it is the standard:

> **ECONOMIC STORY.** The Ichimoku lines are, stripped of the folklore, a set of Donchian
> midpoints over 9/26/52 bars plus a 26-bar displacement. A close above both spans with tenkan
> above kijun is therefore a compact statement that price is above its 9-, 26- and 52-bar
> equilibrium and that the short equilibrium is rising. If time-series momentum exists in FX and
> index CFDs at the daily-to-4-hour horizon — and Moskowitz, Ooi and Pedersen (2012) and Hurst,
> Ooi and Pedersen (2017) document it across 50+ futures over a century — then this is one cheap,
> low-parameter way to express it.
>
> **EVIDENCE AGAINST, STATED PLAINLY.** The specific claim that Ichimoku adds value in FX has an
> explicitly NEGATIVE published result: Deng, Sakurai and Ueda (2021) test Ichimoku rules on
> major currency pairs and find no significant profitability once data snooping is accounted
> for. Ichimoku is also drawn from the same moving-average family as the rules in Coakley,
> Marzano and Nankervis (2016), which did NOT survive Step-SPA multiple-testing correction on
> FX. The honest prior on this strategy is therefore "expected edge approximately zero before
> costs, negative after spread". **It is included as a baseline: if the research pipeline cannot
> show this underperforming, the pipeline is broken.** Any positive result here must clear a
> deliberately high bar and survive the holdout untouched.

That is the correct use of a negative prior, and it is the correct posture for the platform's
own namesake. The other four seeds — `donchian_breakout_atr`, `macd_ema_trend_hybrid`,
`rsi_band_mean_reversion`, `fib_golden_pocket_pullback` — each carry a hypothesis of comparable
length with the same two sections.

The Fibonacci one carries a specific outstanding obligation from the V1 audit: no peer-reviewed
study establishes that the specific ratios outperform arbitrary retracement levels, so the burden
of proof sits on the claim, and the cheap in-house test — 0.618 against randomly drawn levels
between 0.5 and 0.7 on the same swings — should be run before the strategy is taken seriously.
**That test has not been run.**

A hypothesis is also required to be *falsifiable* when it reaches the research fleet:
`agents/research_store.Hypothesis.falsifier` is mandatory and non-trivial, because *"a hypothesis
with no statement of what would disprove it is not a hypothesis, it is a hope."*

## 4. Exits are first-class

**`stop` is mandatory at the schema level.** There is no way to express a stopless strategy, so
there is no code path that can produce one. Five kinds: `atr_multiple`, `fixed_pips`, `percent`,
`swing_structure`, `indicator_level`. The model validates that each kind has the operands it
needs — an `atr_multiple` stop needs an ATR operand, a `swing_structure` stop needs a level
operand pointing at a `SwingDetector` output — and it validates a parameter *reference* as
possibly-positive rather than deferring, so a document cannot validate today and fail mid-sweep
tomorrow. `min_distance_atr` defaults to 0.1 so a structure stop cannot collapse to zero
distance.

`take_profits` is a **list** of legs with allocation fractions. V1's engine could only ever use
`targets[0]`, which made every "scale out at 1R then let the rest run" idea unrepresentable and
therefore quietly untested. The Ichimoku seed uses two legs at 1.5R and 3.0R, 50% each.

**A stop is sized with its costs, and must clear the venue's minimum.** Since
`engine_v3_realism` a position is sized so that being stopped out at the level loses
`risk_fraction` of equity INCLUDING the spread and two fills of expected slippage
(`fixed_fractional_v2`, `backtest.engine.stop_out_cost_per_unit`). A tight stop therefore buys a
smaller position than it used to: on a 5-pip EURUSD stop under IG_REALISTIC the cost is 1.38 pips,
28% of the stop. A stop inside the profile's minimum distance is rejected (`MinStopPolicy.REJECT`),
and that minimum is per asset class: 4 pips on FX, three times the instrument's typical spread on
metals, indices and energy under IG_REALISTIC (XAUUSD $0.90, US500 1.2 points, JP225 21 points).
A document whose stop is routinely within a few spreads of entry is a bet on the cost model.

**Research sizes a signal as paper will.** Validation runs size through portfolio construction
(`docs/v2/PORTFOLIO_RISK_STANDARD.md` §2): the strategy's evidence tier sets the base risk
(PROBATIONARY 0.25%, with the run's `risk_fraction` a ceiling), then health, confidence,
correlation, concentration, vol targeting, margin, the drawdown throttle, the regime scalar
(`unknown` 0.6 in a backtest) and the open-risk budgets scale it down. A document's `confidence`
therefore moves its size, and every trade row carries the allocation that sized it
(`BacktestResult.trade_allocations()`). The flat `risk_fraction` path is `construction=None`.

A `Signal` constructed from a document is validated again at the contract boundary
(`core/contracts.Signal.__post_init__`): positive prices, stop on the correct side of entry,
take-profits on the correct side. V1 allowed a wrong-sided stop and relied on a downstream
sanitiser — which then had to be removed, leaving a contradictory test committed in the tree that
nobody saw go red because the suite would not run.

## 4a. Entry locks (`locks`)

An optional block that refuses NEW entries for a while after something has happened. Pattern
after freqtrade `plugins/protections` (GPL-3.0, not copied), corrected in the two places where
freqtrade's design lets a number be believed that should not be:

```json
"locks": {
  "cooldown_bars_after_close": 3,
  "stop_streak": {"n_stops": 2, "lookback_bars": 30, "lock_bars": 12, "scope": "instrument"}
}
```

| Field | Meaning |
|---|---|
| `cooldown_bars_after_close` (int ≥ 0, default 0) | after a position of this strategy on an instrument closes on session bar `c`, signals on bars `c .. c+N-1` are refused; the first permitted signal is on `c+N` and fills at the next open. Every close arms it, whatever the exit reason |
| `stop_streak.n_stops` (int ≥ 2) | stop-outs needed to arm the lock |
| `stop_streak.lookback_bars` (int ≥ 1) | counted over the session bars `(now - lookback, now]` |
| `stop_streak.lock_bars` (int ≥ 1) | signals on the arming bar and the next `lock_bars - 1` bars are refused |
| `stop_streak.scope` | `instrument` (this strategy's stops on one instrument, locks it there), `strategy` (this strategy's stops anywhere, locks it everywhere), `global` (every strategy's stops in the book, locks every strategy) |

Every field accepts a `{"$param": ...}` reference and is bound like any other.

**Counted in session bars, not minutes.** freqtrade counts a lock in wall-clock minutes, so a
four-candle H1 lock set on Friday at 21:00 expires on Saturday at 01:00, across a weekend with no
candles. Here a lock is counted on `backtest/locks.SessionBarClock`, a pure function of the
timestamp that removes the interbank weekend (Friday 22:00 to Sunday 22:00 UTC): the same lock
expires on Monday at 01:00, four real bars later. Because the clock depends only on the timestamp,
a restarted process holding nothing but a ledger of timestamps computes the same indices.

**Always on, everywhere.** freqtrade leaves protections off in backtests unless asked. Here
there is no switch: `exit_policy_from_document` carries the locks on `ExitPolicy.locks`, so the
engine, the validation evaluator and the agents' backtest job all enforce them without being told
to; the risk gateway's `instrument_lock` check enforces the same rule for paper, demo and live
(`PORTFOLIO_RISK_STANDARD.md` §3). Both ask about the signal's **decision bar**.

**Only a stop-out is a stop-out.** `stop_streak` counts exits at the unmoved protective stop
(`stop_loss`). A take-profit, a time stop, a reversal, a trailing stop and a breakeven stop are
not counted. A close whose reason the ledger does not record (the order-intent ledger) counts as a
stop-out, so recovery from that ledger can only be more restrictive than the truth. A streak is
**consumed** by the lock it arms: the stops that armed one lock do not count toward the next.

**A lock refuses new risk and nothing else.** It never blocks an exit, a scale-out, a protective
amendment or a reversal's closing leg; the engine asks it after scheduling a reversal's close.

**It changes the content hash, on purpose.** A document with locks is a different strategy --
it holds a different set of trades -- so it has a different content hash, a separate holdout
look and its own research-memory entry. Three things keep that from becoming a loophole:

- a document **without** locks dumps, hashes, fingerprints and backtests byte-for-byte as it did
  before the field existed (`locks` is absent from the dump, not `null`), pinned for all five seed
  documents by `tests/integration/test_locks_regression_pin.py`;
- an **inert** block (`{}`, a zero cooldown and no streak) is dropped on load, so it cannot mint a
  new hash -- and a new holdout look -- for an unchanged strategy;
- `SCHEMA_VERSION` is **not** bumped. Bumping it would move every content hash in existence;
  adding an optional field whose absence leaves every dump unchanged moves none, and no stored key
  can have been computed from a document with locks because the previous schema refused the field
  (`extra="forbid"`). `strategy_key_version()` therefore still describes every stored key.

**The compiler refuses a streak that can never arm.** With one position per instrument, stop-outs
are at least `cooldown + 1` bars apart, so an instrument-scoped streak of `n` stops needs a lookback
of at least `(n - 1) * (cooldown + 1) + 1` bars.

**Approximations, stated.** The calendar models the weekend only: exchange holidays and the daily
maintenance breaks of index and energy CFDs count as session bars. A slot counts as in session
when at least half of it is open (exact on H1 and below; on H4 the Friday 20:00 and Sunday 20:00
slots count; on D1 Sunday does not). A bar stamped inside a closed slot shares the previous open
slot's index, which holds a lock one bar longer, never shorter. A `global` streak depends on every
strategy sharing the book, so a single-strategy backtest reproduces it exactly only for a book
running one strategy.

**How it relates to `position_management.cooldown_bars_after_exit`.** That older field is
enforced by the position book at FILL time, counted on the engine's timeline index, and a reversal
does not arm it. It is unchanged. `locks.cooldown_bars_after_close` is decided on the signal bar,
counted in session bars, enforced by the gateway as well as the engine, and survives a restart. By
construction, on a single-instrument run with no latency and no weekend in the window, both refuse
an entry that would fill on the same bars; they are still not ledger-identical, because the older
field refuses an order that was already queued (consuming a fill-model sequence number) and the
new one refuses before an order exists. This equivalence is argued, not tested.

## 5. Parameters carry their own domains

Every tunable carries a `ParameterSpec` with a kind, a default, a description and an explicit
domain — `min_value`, `max_value`, `step` for numerics, or `choices`.

**A sweep is therefore defined by the strategy, not by whatever the sweeping script guessed.**
That is what makes rung 2's walk-forward honest: each fold sweeps *the domains the document
declares* on its train window and transfers the winner to test. It is also what makes rung 4's
plateau analysis meaningful, because the neighbourhood is defined in the document's own grid
coordinates.

The Ichimoku seed declares six: `tenkan_period` (9, 5–15), `kijun_period` (26, 17–40),
`senkou_b_period` (52, 34–78, step 2), `senkou_shift` (26, 13–39), `adx_floor` (20.0, 10–35,
step 2.5) and `stop_buffer_atr` (0.5, 0.0–1.5, step 0.25).

`senkou_shift` being **independent of chikou** is a direct fix for a V1 defect: V1 displaced
Senkou A and B using `chikou_shift`, numerically identical at defaults, so a sensitivity sweep
over `chikou_shift` silently moved the cloud too.

`StrategyDocument.bind(values)` substitutes a chosen value into every rule literal that
references it, and `bind_defaults()` binds the document's own declared defaults — which is what
the registry health check compiles against. `tests/unit/test_strategy_binding.py` and
`tests/integration/test_seed_binding_parity.py` cover it, and
`tests/integration/test_validation_ladder_real_engine.py` runs the full ladder against the real
backtest engine through a binding evaluator. The ladder remains defined against the `Evaluator`
protocol rather than calling `run_backtest` directly, which keeps its own logic testable against
evaluators whose truth is known exactly.

## 6. The rule vocabulary

`strategy/primitives.py`. Every primitive does two things: evaluates itself against a single bar,
reading only that bar and bars before it; and **declares which indicators it needs**, so the
compiler derives the strategy's indicator set and therefore its warmup automatically.

| Rule | Meaning |
|---|---|
| `ThresholdRule` | an operand against a fixed number (`rsi < 30`) |
| `IndicatorVsIndicatorRule` | one indicator against another (`tenkan > kijun`) |
| `IndicatorVsPriceRule` | an indicator against an OHLC field (`close > cloud_top`) |
| `CrossoverRule` | `fast` crosses `slow`; needs bar *i* and *i−1*, never *i+1* |
| `RegimeGateRule` | an inclusive band on a regime metric (ADX, realised vol, ATR) |
| `SessionWindowRule` | UTC hour-of-day / weekday window; bar times are UTC by contract |
| `AllOfRule` / `AnyOfRule` / `NotRule` | composition |

`RegimeGateRule` is semantically distinct from a threshold on purpose: it says *"this strategy is
only meaningful in this market state"*, and the compiler keeps regime rules separate from entry
rules so research can measure their contribution independently.

Operands are `IndicatorOperand`, `PriceOperand` or `ConstantOperand`, each with an `offset`.

### Look-ahead is prevented structurally, three times over

1. **Operand offsets are `ge=0` at the schema level.** A negative offset — bar *i+1* — cannot be
   expressed in a valid document at all.
2. **`EvalContext` is constructed over a frame already truncated at the evaluation bar**, and has
   no API reaching past its last row. Even a buggy primitive has no future rows to read.
3. **`CompiledStrategy.generate_signal` slices to `df.iloc[: idx + 1]`** and hands only that slice
   to the rule evaluator; there is no reference to the full frame anywhere inside evaluation.

`tests/unit/test_compiler_causality.py` corrupts every bar after `idx` and demands an identical
signal. `tests/unit/test_no_lookahead.py` does the same at the engine level.

### Warmup is derived, not declared

`warmup_period = max(indicator warmups) + max(rule lookback) + 1`.

V1 hardcoded 98 bars for **every** bot, because `getattr(self.strategy, "warmup_period", 78)`
read an attribute the `Strategy` base class never defined. Here the number is a consequence of
the document and cannot drift away from it.

## 7. Indicators

Twenty registered: `adx`, `atr`, `bollinger`, `cci`, `donchian`, `ema`, `fibonacci`, `ichimoku`,
`keltner`, `macd`, `obv`, `psar`, `realised_volatility`, `roc`, `rsi`, `sma`, `stochastic`,
`swing`, `vwap`, `wma`.

**Every indicator is strictly causal**: the value on row *i* is a function of rows 0…*i* only.
This is not a style preference — it is the property that makes backtest, research and paper
trading agree, and V1 broke it in three separate places (a centred swing window, a
`close.shift(-26)` chikou column sitting in every strategy's DataFrame, and a cloud displaced
with the wrong parameter).

Two rules are enforced rather than trusted. `compute` never mutates the caller's frame — V1's
indicators wrote columns into the passed DataFrame, so the order in which indicators ran changed
what later indicators could see. And `assert_causal` mechanically proves the property by
corrupting the future and demanding a bit-identical prefix, NaN positions included;
`tests/unit/test_indicator_causality.py` parametrises over `registry.causality_suite()`, so
**adding an indicator without a causality proof is impossible — the test collects it
automatically.**

`VolumeUnavailableError` closes the other V1 indicator defect. V1 computed OBV on FX volume that
is identically zero, producing a flat line and a strategy that could never trade, and let VWAP
fall back silently to a rolling mean of typical price — an undisclosed moving-average strategy
wearing a VWAP label. V2 refuses both: either the caller opts in to an explicit `unavailable`
marker, or the indicator raises.

`tests/golden/test_indicator_values.py` pins hand-calculated constants on a 20-bar series chosen
so most intermediate quantities are exact binary fractions. V1 shipped its flagship indicator
with **zero** pinned constants; any refactor could have moved the cloud and no test would have
noticed.

## 8. The registry and its health check

`StrategyRegistry` deduplicates by **content hash**. A search process generates thousands of
documents, most of them small variations; registering by content hash means an identical strategy
proposed twice is recognised as the same strategy once, whatever it was named.
`DuplicateStrategyError` is raised when a different `strategy_id` already holds the same content
hash.

`content_hash` covers **semantic** content only — fields that name or attribute a strategy but do
not change its behaviour are excluded, so renaming does not change the hash, because it is the
same strategy. The complementary key is `research/structure.structure_hash`, which elides every
number, so a rediscovered idea with a period of 21 instead of 14 is recognised as the idea it is.

`health_check()` surfaces the classes of mistake V1 only discovered months later in production:

| Code | Severity | Condition |
|---|---|---|
| `duplicate_content` | error | identical content to another registered id |
| `does_not_compile` | error | fails `compile_strategy(doc.bind_defaults())` |
| `warmup_too_long` | error | derived warmup above `MAX_SANE_WARMUP` |
| `direction_mismatch` | error | declared `both` but only one side has entry rules |
| `volume_on_fx` | warning | **the V1 OBV bug**: a volume indicator on FX instruments, where volume is a tick count or zero |
| `stop_only_exit` | warning | no take-profit, trailing or time stop — the only exit is the hard stop or an opposite signal |
| `unmanaged_runner` | warning | take-profit legs close less than 100% and there is no trailing model for the remainder |
| `high_complexity` | warning | above `COMPLEXITY_WARN_AT`; "expect a large multiple-testing penalty" |
| `missing_parent` | warning | a `parent_strategy_id` is not registered; lineage is broken |

A template is checked at its own declared defaults, which is the one binding the document can be
held to without a caller supplying anything.

## 9. Lifecycle states

`core/enums.StrategyLifecycle` has thirteen members:

```
DISCOVERY → RESEARCH → VALIDATING → CANDIDATE → PAPER → SHADOW → DEMO → APPROVED → LIVE
                                                    ↘  WATCH  ↘  DEGRADED  ↘  QUARANTINED  ↘  RETIRED
```

The intended meanings:

| State | Meaning |
|---|---|
| `DISCOVERY` | an idea, possibly agent-generated, not yet a validated document |
| `RESEARCH` | a registered document being explored |
| `VALIDATING` | currently on the ladder |
| `CANDIDATE` | cleared every gate; awaiting an allocation |
| `PAPER` | allocated in paper mode against the shared fill model |
| `SHADOW` | signals sent to a venue's practice endpoint but not acted on |
| `DEMO` | trading a broker demo account |
| `APPROVED` | cleared for live, not yet allocated |
| `LIVE` | trading real capital |
| `WATCH` | trading, but under review; allocated at reduced size |
| `DEGRADED` | live behaviour has decayed; blocked from new risk |
| `QUARANTINED` | suspended pending investigation; blocked |
| `RETIRED` | permanently withdrawn |

### What the states actually do today

Two components consume them, and both are tested.

`risk/gateway.py::_check_strategy_lifecycle` blocks a risk-adding order when the lifecycle is
`DISCOVERY`, `RESEARCH`, `VALIDATING`, `CANDIDATE`, `DEGRADED`, `QUARANTINED` or `RETIRED`;
blocks when `StrategyView.degraded` is set; blocks `LIVE` mode for anything whose lifecycle is
not `LIVE`; and blocks `DEMO` mode for a `PAPER` strategy. **An unknown lifecycle blocks** —
`strategy_lifecycle_unknown`.

`portfolio/construction.py` allocates risk budget only to `PAPER`, `SHADOW`, `DEMO`, `APPROVED`,
`LIVE` and `WATCH`, as the *first* step of the adjustment pipeline so a quarantined strategy never
consumes budget; and scales `WATCH` to 0.5 and `DEMO` to 0.75 of the entitlement.

### What does not exist

Stated plainly, because this is the section most likely to be read as a description of a working
mechanism.

- **`StrategyDocument` carries no lifecycle field.** The state lives in a `StrategyView` supplied
  by a caller.
- **No module owns transitions.** There is no state machine, no transition table, no validation
  that `RESEARCH → LIVE` is illegal. Any caller can assert any state.
- **Nothing computes a transition.** A passing `ValidationReport` does not promote anything; the
  `StrategyLifecycle.CANDIDATE → PAPER` step is a decision no code makes.
- **Nothing computes degradation.** `StrategyView.degraded` is an input, not a measurement. The
  audit's three pre-registered stopping rules — halt when the probabilistic Sharpe against half
  the backtested Sharpe falls below 0.50; halt at the 95th percentile of the bootstrap
  max-drawdown distribution; and a CUSUM on excess return calibrated to a roughly two-year
  in-control run length — are **not implemented**. The statistical primitives they need exist
  (`probabilistic_sharpe_ratio`, `bootstrap_confidence_interval`); the monitors do not.
- **No lifecycle transition is audited**, because no transition is made.

The design intent is that a transition is an event with an actor, a reason and a
`ValidationReport` or a degradation measurement attached, written to an append-only ledger. That
is not built. See `ROADMAP.md`.

## 10. The roster rule

The V1 audit's most counter-intuitive and most mathematically direct finding: **having 64
strategies made the platform worse off.** Every additional strategy inflates the trial count and
raises the statistical bar every survivor must clear. Culling to 12–20 strategies with an
articulable economic rationale would *improve* expected live performance even holding research
quality constant.

V2 starts at **five** seed documents, deliberately. The standing rule is therefore: **do not add
a strategy to make the roster bigger.** A new document must earn its place against the bar it
raises for everything else, and its hypothesis must say why it is a different bet rather than a
reparameterisation — which `research/structure.is_reparameterisation` can answer mechanically.

The corollary is that `LadderConfig.external_trial_count` must reflect the *whole* campaign, not
one strategy's sweep. A twelve-strategy roster across sixty instruments and two timeframes is
1,440 cells before any parameter sweep, and deflating against a single sweep would understate
the correction by three orders of magnitude.

## 11. Checklist for a new strategy document

1. A hypothesis of at least 120 characters containing an **economic story** and the **evidence
   against**, with citations where published evidence exists.
2. A falsifier: what result would make you abandon this.
3. Every instrument in `universe` registered in `core/instruments.py`, and, for research in the GBP
   account, its quote currency convertible from the store's D1 GBP crosses
   (`validation.run.research_fx_pairs`; HK50 is not, until USDHKD is registered).
4. A stop — the schema will not let you omit one — with the operands its kind requires.
5. An exit that is not only the stop: a take-profit, a trailing model or a time stop. Otherwise
   the health check warns `stop_only_exit`.
6. If take-profit legs close less than 100%, a trailing model for the remainder.
7. Explicit domains on every parameter you intend to sweep.
8. No volume indicator on an FX universe.
9. A complexity score you can justify. Every point is another way to overfit.
10. `research/memory.py` consulted first: structurally, not just by exact hash.
11. `StrategyRegistry.health_check()` clean of errors, and every warning either fixed or
    explained in `notes`.
