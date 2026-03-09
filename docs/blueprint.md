# FIBOKEI — MASTER BLUEPRINT
Version: 1.0
Status: Foundational Blueprint
Project Type: Python-first multi-strategy automated trading research and execution platform
Primary Mode at Launch: Research + backtesting + paper trading
Future Mode: Live execution via IG broker adapter
Owner: Joe
Secondary User: Tom

---

# 1. Executive Summary

## 1.1 What FIBOKEI is

FIBOKEI is a full-stack, multi-strategy automated trading platform focused on **Ichimoku Cloud** and **Fibonacci-based** trading systems. It is being built to research, rank, simulate, monitor, and eventually execute algorithmic strategies across multiple liquid instruments and short-to-medium intraday timeframes.

FIBOKEI is not a single bot and not a one-off script. It is a structured platform with:

- a reusable common strategy framework
- a library of 12 prebuilt strategies/bots
- a historical data ingestion and preparation pipeline
- a backtesting engine
- a research matrix engine for strategy/instrument/timeframe discovery
- a paper trading engine for forward-testing
- a risk and portfolio management engine
- a web control platform
- a notification system using Telegram
- a future-ready IG broker integration layer for live execution

## 1.2 Why this platform exists

The purpose of FIBOKEI is to solve a specific trading workflow problem:

1. design and encode strategies consistently
2. test them at scale across many markets and timeframes
3. identify which combinations genuinely perform best
4. reject weak, low-sample, or unstable combinations
5. run approved combinations simultaneously in paper mode
6. observe them under realistic forward conditions
7. later promote the strongest combinations into live execution when desired

This means FIBOKEI is as much a **research platform** as it is an **execution platform**.

## 1.3 Primary objective

The primary objective is to build a professional-grade platform that can:

- host 12 initial Ichimoku/Fibonacci strategies
- test them over downloaded historical datasets
- compare them across the full instrument universe (60+ instruments with canonical data)
- support 1m, 2m, 5m, 15m, 30m, 1h, and 4h timeframes
- show chart markers for entries and exits
- show comprehensive statistics and visual performance outputs
- rank best strategy/instrument/timeframe combinations
- run multiple bots simultaneously in paper mode
- send Telegram alerts and summaries
- provide a clean web platform for control and inspection
- remain structurally ready for future live execution through IG

---

# 2. Product Philosophy

## 2.1 Core philosophy

FIBOKEI must be built around the following principles:

- **docs first**
- **framework first**
- **paper first**
- **live ready but not live dependent**
- **deterministic strategy logic**
- **clear separation of concerns**
- **backtest-to-paper parity**
- **risk-controlled by default**
- **portfolio-aware, not just trade-aware**
- **reusable, inspectable, and auditable outputs**

## 2.2 What this project is not

FIBOKEI is not:

- a hardcoded single-exchange bot
- a one-file trading script
- a black-box "AI trading" toy
- a platform that depends on live market APIs to function at the research stage
- a platform that allows raw user text to go directly into live execution
- a platform that prioritises raw profit over robustness

## 2.3 Success criteria philosophy

The system should favour:

- enough trades to matter
- cleaner execution logic
- robust risk-adjusted outputs
- strategies that survive across related markets/timeframes
- combinations with acceptable drawdown and repeatability
- research workflows that are reproducible

---

# 3. Scope

## 3.1 In-scope for this project

The project includes:

- 12 initial bots based on Ichimoku and Fibonacci principles
- Python-based strategy logic, backtesting, analytics, and paper execution
- chart overlays and markers for signals, entries, exits, stops, and targets
- historical-data-based research using downloaded datasets
- strategy scoring and ranking
- multi-bot simultaneous operation in paper mode
- Telegram notifications
- a light-themed web app
- user login for Joe and Tom
- deployment of frontend to Vercel
- future-ready IG execution adapter design
- GitHub-based version control and structured documentation

## 3.2 Out-of-scope for initial release

These are not required in first release unless later specified:

- live money trading enabled by default
- mobile apps
- copy trading
- social/community features
- third-party subscription management
- broker abstraction for many brokers in V1
- full low-latency tick trading
- intrabar microstructure logic
- options strategies
- machine-learning models as core strategy drivers
- unrestricted natural-language-to-live-bot conversion

---

# 4. Operating Model

## 4.1 Launch mode

FIBOKEI launches in:

- historical backtest mode
- research matrix mode
- paper trading mode

Live execution is deliberately excluded from launch activation.

## 4.2 Paper-first rule

Every strategy/instrument/timeframe combination must pass through:

1. historical backtest screening
2. minimum trade-count filtering
3. score/ranking review
4. paper trading forward test
5. human approval

Only after this process can a combination be considered for live readiness.

## 4.3 Timeframe philosophy

Because the target is intraday and quick trading, the platform must support:

- 1m
- 2m
- 5m
- 15m
- 30m
- 1h
- 4h

The priority execution/research band should initially be:

- 5m
- 15m
- 30m
- 1h

The lower timeframes are valuable but noisier. The 4h timeframe is useful for structure and filters.

## 4.4 Signal evaluation timing

To maintain clean parity between research and forward execution:

- all strategy signals are evaluated on **closed candles only**
- no intrabar signal triggering in V1
- no repainting logic permitted
- no use of future data except in explicitly projected Ichimoku components that are valid by design, such as forward cloud arrays
- paper engine evaluation cycle should poll every few seconds but strategy decisioning still occurs only at candle close

Recommended engine behaviour:

- system heartbeat/polling: every 5 seconds
- strategy decisioning: on candle close event
- execution timestamp: immediate on next bar open or defined rule per strategy

---

# 5. Platform Identity

## 5.1 Name

**FIBOKEI**

The brand reflects:

- Fibonacci logic (`FIBO`)
- Kei-style Ichimoku trading influence (`KEI`)

It clearly signals the platform's strategic identity.

## 5.2 Brand direction

The brand should feel:

- professional
- technical
- clean
- sharp
- modern
- not flashy
- more like a serious quant/research terminal than a retail gimmick

## 5.3 Visual direction

UI palette requirements:

- layered off-white backgrounds
- black text
- multiple greens for positive states, highlights, actions, performance accents
- complementary muted support colours
- subtle depth, clean spacing, crisp charts
- professional light theme only in V1

---

# 6. Users and Access

## 6.1 Initial users

Initial supported users:

- Joe
- Tom

## 6.2 Access model

For V1:

- only 2 users seeded
- both users are admins
- passwords must be hashed
- no plaintext password in code or repo
- auth secrets must be environment-managed
- audit log of user actions should be architecturally supported even if initially lightweight

## 6.3 Future access readiness

Architecture should leave room for:

- role-based access control
- view-only roles
- operator roles
- 2FA
- per-user preference storage
- login session management improvements

---

# 7. Markets, Instruments, and Universe Design

## 7.1 Objective for starting universe

The starting instrument universe must be broad enough to reveal real strategy behaviour, but narrow enough to remain operationally manageable. The supported instrument universe is 67 instruments (60 HistData canonical + 7 alternate-provider); canonical data is 60 × 6 timeframes as of Phase 6.5.

It should prioritise:

- liquidity
- repeatable price action
- relevance to intraday trend/pullback systems
- availability of downloadable historical data
- a mix of market behaviours

## 7.2 Supported instrument universe

The supported instrument universe comprises 67 instruments across 9 asset classes: 60 with HistData canonical data (60 × 6 timeframes = 360 datasets) and 7 flagged for alternate providers (NATGAS, US30, BTCUSD, ETHUSD, SOLUSD, LTCUSD, XRPUSD — `has_canonical_data=False`). The full registry is defined in `backend/src/fibokei/core/instruments.py`. The API supports filtering by `asset_class` query parameter.

### Historical Phase 1 Baseline (30 instruments)

The original 30-instrument selection below was the Phase 1 design baseline. For the current full universe, see `instruments.py`.

### Forex (12)
1. EURUSD
2. GBPUSD
3. USDJPY
4. AUDUSD
5. USDCHF
6. USDCAD
7. NZDUSD
8. EURJPY
9. GBPJPY
10. EURGBP
11. AUDJPY
12. EURAUD

### Commodities (5)
13. XAUUSD
14. XAGUSD
15. Brent Crude
16. WTI Crude
17. Natural Gas

### Indices (8)
18. US500
19. US100
20. US30 / Wall Street
21. Germany 40
22. FTSE 100
23. Japan 225
24. Hong Kong 50
25. Australia 200

### Crypto (5)
26. BTCUSD
27. ETHUSD
28. SOLUSD
29. LTCUSD
30. XRPUSD or a crypto basket proxy if better data quality exists

## 7.3 Why this instrument selection is strong

This basket gives:

- forex for steady intraday trend structures
- metals and oil for strong directional and pullback behaviour
- indices for macro-driven momentum
- crypto for 24/7 volatility and robustness testing

It also exposes the strategy library to different volatility, trend, and session characteristics.

## 7.4 Instrument grouping

The platform should classify each instrument into a bucket:

- forex_major
- forex_cross
- commodity_metal
- commodity_energy
- index
- crypto

This supports risk grouping, correlation caps, and performance analysis by class.

---

# 8. Data Strategy

## 8.1 Core data rule

Historical backtesting and research must use **downloaded datasets**, not paid live API feeds.

## 8.2 Data philosophy

The platform must treat data as a first-class subsystem, not a side detail.

Data handling must support:

- source tracking
- file ingestion
- cleaning
- normalization
- resampling
- metadata storage
- reproducibility
- auditability

## 8.3 Required data layers

The system should maintain the following conceptual layers:

### Layer 1 — Raw
Original downloaded files from GitHub repositories or other approved sources.

### Layer 2 — Cleaned canonical
Validated and normalized OHLCV datasets in a standard schema.

### Layer 3 — Resampled
Derived timeframe datasets, where needed.

### Layer 4 — Enriched
Datasets with indicators, wave tags, swing points, or other derived fields.

### Layer 5 — Result-linked
Backtest and research outputs linked to exact source versions and parameter sets.

## 8.4 Canonical OHLCV schema

Each canonical dataset must be transformed into a standard structure:

- timestamp
- open
- high
- low
- close
- volume
- instrument
- timeframe
- source_id
- timezone
- ingest_version

## 8.5 Data quality checks

The ingestion pipeline must validate:

- missing candles
- duplicate timestamps
- out-of-order timestamps
- impossible OHLC values
- negative or null data where invalid
- suspicious gaps
- timezone inconsistencies
- malformed file formats

## 8.6 Timezone rule

A clear canonical timezone must be selected and applied consistently.
Preferred approach:

- store timestamps in UTC internally
- convert only at presentation layer if required

## 8.7 Data readiness scoring

Each dataset should receive a status label:

- raw_only
- cleaned
- resampled
- validated
- research_ready
- paper_ready

---

# 9. System Architecture Overview

## 9.1 High-level system components

FIBOKEI should be built as a modular platform composed of the following major subsystems:

1. web frontend
2. backend API
3. strategy core
4. strategy library
5. indicator engine
6. backtesting engine
7. research matrix engine
8. analytics engine
9. risk engine
10. paper trading engine
11. alert/notification engine
12. data service
13. future broker execution adapter layer
14. persistence layer

## 9.2 Monorepo recommendation

Use a monorepo so all components evolve in lockstep.

Recommended structure:

```text
/apps
  /web
  /api

/services
  /data-service
  /backtester
  /research-engine
  /paper-engine
  /alert-service
  /execution-engine

/packages
  /strategy-core
  /strategy-library
  /indicator-core
  /analytics-core
  /risk-core
  /shared-types
  /chart-annotations

/docs
/scripts
/infrastructure
/tests
```

## 9.3 Separation rules

The following separations are mandatory:

- strategy logic must not depend directly on broker APIs
- broker logic must be adapter-based
- indicator calculations must be reusable and centralized
- analytics must not be embedded inside strategies
- risk logic must not be duplicated in strategies
- frontend must not contain trading logic
- backtest mode and paper mode should share as much execution logic as possible

---

# 10. Technical Stack

## 10.1 Backend and strategy stack

Use Python for:

- indicators
- strategies
- backtesting
- analytics
- research ranking
- paper execution
- risk management
- alert payload generation
- future live execution logic

## 10.2 API layer

Use FastAPI for:

- frontend-backend communication
- auth endpoints
- strategy management endpoints
- backtest run endpoints
- research result endpoints
- bot state endpoints
- settings/config endpoints

## 10.3 Frontend stack

Use Next.js with TypeScript for the web platform.
The frontend should support:

- login
- dashboard
- charts
- backtests
- running bots
- trade history
- settings/system pages

## 10.4 Persistence

Use a relational database for:

- users
- configs
- dataset registry
- backtest runs
- paper trades
- trade events
- rankings
- alerts
- logs
- strategy states

## 10.5 Caching / queue support

If required, use a lightweight service such as Redis for:

- transient state
- queue handling
- worker coordination
- caching expensive reads
- pub/sub for UI updates

## 10.6 Deployment split

Important non-negotiable:

- frontend can be deployed to Vercel
- long-running engines must be deployed elsewhere
- Vercel must not be the runtime for the paper engine or future live engine

---

# 11. Core Modes of Operation

## 11.1 Historical backtest mode

Purpose:

- test a strategy on a specified instrument/timeframe/date range
- produce detailed metrics and chart outputs
- examine signal quality and trade mechanics

## 11.2 Research matrix mode

Purpose:

- run many combinations at scale
- compare outcomes
- rank best candidates
- identify strategy/instrument/timeframe fit

## 11.3 Paper trading mode

Purpose:

- forward test approved combinations
- observe live-like behaviour without real-money execution
- validate stability after historical testing

## 11.4 Future live mode

Purpose:

- route approved signals to IG
- execute orders
- manage live positions
- reconcile execution state

V1 status: disabled by feature flag

---

# 12. Common Strategy Framework

## 12.1 Why a common framework matters

The 12 initial bots must not be 12 separate, inconsistent, one-off codebases.
They must all inherit or conform to a shared framework.

This ensures:

- consistent inputs and outputs
- easier ranking and comparison
- simpler testing
- simpler chart annotation
- simpler risk management
- easier addition of future strategies
- less duplicate logic

## 12.2 Standard strategy identity fields

Each strategy must define:

- strategy_id
- strategy_name
- strategy_family
- description
- logic_summary
- valid_market_regimes
- supported_timeframes
- supported_instruments
- supports_long
- supports_short
- requires_mtfa
- requires_fibonacci
- requires_pattern_recognition
- requires_wave_detection
- requires_time_cycle_logic
- complexity_level

## 12.3 Standard strategy inputs

Each strategy must expose a configuration object including:

- Ichimoku parameters
  - tenkan_period
  - kijun_period
  - senkou_b_period
  - chikou_shift
- ATR settings if relevant
- Fibonacci settings if relevant
- swing/fractal settings if relevant
- regime filter settings
- entry tolerance settings
- stop and target rules
- trailing rules
- risk band rules
- max bars in trade
- cooldown rules

## 12.4 Standard strategy methods

Every strategy should implement at minimum:

- prepare_data(data)
- compute_indicators(data)
- detect_market_regime(data)
- detect_setup(data, context)
- generate_signal(data, context)
- validate_signal(signal, context)
- build_trade_plan(signal, context)
- manage_position(position, context)
- generate_exit(position, context)
- score_confidence(signal, context)
- annotate_chart(data, trades, context)
- explain_decision(context)

## 12.5 Standard signal object

Every strategy must return a normalized signal object containing:

- timestamp
- instrument
- timeframe
- strategy_id
- direction
- setup_type
- entry_type
- proposed_entry
- stop_loss
- take_profit_primary
- take_profit_secondary
- confidence_score
- regime_label
- signal_valid
- invalidation_reason
- rationale_summary
- supporting_factors
- annotation_payload

## 12.6 Standard trade plan object

Each trade plan must define:

- entry price
- stop loss
- one or more take-profit targets
- trailing stop rule
- break-even logic
- max risk amount
- risk %
- position size
- expiry / stale entry rule
- max bars in trade
- partial close settings
- exit reasons allowed

## 12.7 Standard exit reason taxonomy

All exits should be categorized consistently:

- stop_loss_hit
- take_profit_hit
- partial_take_profit
- trailing_stop_hit
- break_even_exit
- opposite_signal_exit
- indicator_invalidation_exit
- time_stop_exit
- manual_stop
- system_shutdown_exit

## 12.8 Chart annotation standard

Every strategy must support chart annotations for:

- setup marker
- entry marker
- stop line
- target line(s)
- exit marker
- trailing stop movement
- Ichimoku components
- Fib components where relevant

---

# 13. Strategy Library — Initial 12 Bots

These 12 bots form the starting strategy library. They should be implemented within the common framework and share the same execution standards.

---

## 13.1 BOT-01 — Pure Sanyaku Confluence

### Summary

This is the classic full-confirmation Ichimoku trend-following bot.

### Purpose

Capture higher-quality trend entries only when all major bullish or bearish Ichimoku conditions align.

### Signal logic

Bullish version requires:

1. price closes above the Kumo
2. Tenkan-sen crosses above Kijun-sen
3. Chikou Span is above price from 26 periods ago

Bearish version is the inverse.

### Entry

- market order on next candle open after full confirmation candle

### Exit

- Chikou crossing back through price
- or Tenkan crossing back against Kijun
- optional additional fail-safe stop

### Important implementation notes

- Chikou confirmation must correctly reference shifted historical arrays
- strategy should support both long and short
- baseline benchmark strategy for comparison against all others

---

## 13.2 BOT-02 — Kijun-sen Pullback

### Summary

Trend continuation strategy using Kijun as pullback support/resistance.

### Purpose

Improve reward-to-risk versus breakout entry by waiting for retracement inside a valid trend.

### Signal logic

Trend identified by:

- price above/below cloud
- Tenkan above/below Kijun
- regime labelled as trend continuation

Entry requires:

- pullback touches or nearly touches Kijun
- reversal candle confirms rejection from Kijun
- rejection must not decisively invalidate trend structure

### Entry

- enter on close of valid reversal candle or next candle open

### Exit

- stop at 1 ATR beyond Kijun or structure-aware level
- take profit at recent swing high/low or wave target
- optional trailing using Kijun

### Important implementation notes

- requires candlestick pattern recognition
- should support pin bar / engulfing / rejection body logic
- likely strong on trending FX, Gold, indices

---

## 13.3 BOT-03 — Flat Senkou Span B Bounce

### Summary

Cloud equilibrium bounce strategy.

### Purpose

Trade strong support/resistance reactions at flat Senkou Span B levels.

### Signal logic

- Span B flat for configurable lookback window
- macro trend context aligned
- price approaches flat level from correct side
- level has not already been heavily broken/invalidated

### Entry

- limit order at flat Span B level
- optional confirmation mode can require reaction candle

### Exit

- stop when price closes through opposite cloud boundary or invalidation threshold
- target at prior swing or structured target

### Important implementation notes

- requires slope/flatness detection
- flat threshold should be configurable, not strictly mathematical zero only
- useful where equilibrium levels repeatedly attract price

---

## 13.4 BOT-04 — Chikou Open Space Momentum

### Summary

Momentum breakout strategy driven by lagging-line clearance.

### Purpose

Exploit aggressive continuation when Chikou has "open air" and the chart behind price is structurally clear.

### Signal logic

- Chikou breaks above/below historical price structure and cloud
- there is no immediate overhead/underfoot conflict in the mapped historical space for N bars

### Entry

- market entry on valid open-space breakout

### Exit

- Tenkan trailing stop
- immediate exit on close back through Tenkan
- optional profit lock if extension too stretched

### Important implementation notes

- requires backward-aligned spatial validation
- aggressive, likely lower win rate but strong momentum capture potential

---

## 13.5 BOT-05 — MTFA Sanyaku

### Summary

Multi-timeframe version of Sanyaku with higher timeframe filter.

### Purpose

Reduce false lower-timeframe signals by requiring macro trend alignment.

### Timeframe model

Suggested primary pairings:

- 4H filter → 1H execution
- 1H filter → 15m execution
- 30m filter → 5m execution

### Signal logic

Higher timeframe must confirm trend alignment, then lower timeframe must trigger Sanyaku entry.

### Entry

- only enter on lower timeframe if higher timeframe filter is valid

### Exit

- lower timeframe Kijun trailing stop
- lower timeframe invalidation, not higher timeframe reversal, governs exit

### Important implementation notes

- requires reliable multi-timeframe synchronization
- should be a strong candidate for live/paper shortlist later

---

## 13.6 BOT-06 — N-Wave Structural Targeting

### Summary

Wave-structure continuation with Hosoda-style target projection.

### Purpose

Use A-B-C identification to project a measured continuation objective.

### Signal logic

- identify A-B-C structure
- C forms as valid higher low / lower high continuation pivot
- ideally aligns with Kijun or cloud support/resistance

### Entry

- enter as price confirms pivot from point C

### Exit

- hard target at N-wave projection:
  - bullish: C + (B - A)
  - bearish inverse
- stop beyond C pivot

### Important implementation notes

- requires fractal or zigzag swing detection
- must avoid noisy or over-fragmented swing logic
- strong candidate for structural take-profit discipline

---

## 13.7 BOT-07 — Kumo Twist Anticipator

### Summary

Forward-looking reversal strategy using projected cloud twist.

### Purpose

Detect likely trend exhaustion and early reversal opportunities.

### Signal logic

- projected cloud twist appears 26 periods ahead
- current price is extended away from Kijun
- counter-direction TK cross confirms exhaustion starting

### Entry

- counter-trend entry on trigger confirmation

### Exit

- mean reversion target at Kumo
- stop at recent swing extreme

### Important implementation notes

- requires correct use of projected cloud arrays
- higher complexity, lower frequency, potentially sharper R:R
- should be risk-weighted more cautiously in ranking

---

## 13.8 BOT-08 — Kihon Suchi Time Cycle Confluence

### Summary

Time-cycle confluence strategy using Ichimoku number logic.

### Purpose

Weight or amplify entries when price and time align around key Ichimoku counts such as 9, 17, and 26.

### Signal logic

- count candles since last major swing point
- only trigger enhanced setup when bounce or breakout aligns with key count

### Entry

- normal strategy trigger plus valid time-cycle alignment

### Exit

- Kijun trailing stop or strategy-specific exit rules

### Important implementation notes

- bar counter must use a consistent swing definition
- can be used as confidence booster / risk multiplier within safe bounds
- useful as a meta-layer across selected base strategies

---

## 13.9 BOT-09 — Golden Cloud Confluence

### Summary

Fibonacci pullback into Kumo support/resistance.

### Purpose

Capture deeper but still structurally valid pullbacks where Fibonacci and Ichimoku agree.

### Signal logic

- macro trend valid
- draw Fib retracement from confirmed swing low to swing high or inverse
- 50% or 61.8% retracement overlaps with strong cloud structure
- cloud must still support primary trend thesis

### Entry

- limit order at 61.8% or approved overlap zone
- order cancelled if trend structure breaks before touch

### Exit

- stop below 78.6% or lower cloud boundary, whichever is safer and structurally correct
- target at prior swing high / 0% retracement
- optional second extension target

### Important implementation notes

- requires overlap tolerance logic
- likely useful across Gold, indices, FX trends

---

## 13.10 BOT-10 — Kijun + 38.2% Shallow Continuation

### Summary

Momentum continuation via shallow pullback.

### Purpose

Catch strong trends that do not retrace deeply.

### Signal logic

- momentum regime present
- expanding Tenkan-Kijun separation
- Chikou open space supportive
- 38.2% retracement aligns with or nearly aligns with Kijun

### Entry

- enter on candle closing back in direction of trend from aligned area

### Exit

- Kijun trailing stop
- optional scale-out into extension

### Important implementation notes

- must aggressively reject sideways markets
- suitable for faster trend continuation phases

---

## 13.11 BOT-11 — Sanyaku + Fib Extension Targets

### Summary

Classic Sanyaku entry with structured extension-based profit taking.

### Purpose

Improve profit capture discipline rather than relying only on trailing exits.

### Signal logic

- standard Sanyaku confirmation

### Entry

- market entry after confirmation

### Exit

- TP1 at 1.272 extension, close 50%
- TP2 at 1.618 extension, close remainder
- stop initially below/above Tenkan or structure
- move stop to breakeven after TP1

### Important implementation notes

- requires partial position management
- strong candidate for cleaner realised profit profiles

---

## 13.12 BOT-12 — Kumo Twist + Fibonacci Time Zone Anticipator

### Summary

Advanced predictive reversal strategy combining future cloud structure and Fib time cycles.

### Purpose

Predict reversals based on both projected structure and bar-time rhythm.

### Signal logic

- define major swing anchor
- calculate Fibonacci time zones
- projected Kumo twist aligns with major Fib time column
- price crosses Tenkan in expected reversal direction

### Entry

- enter on confirmation of reversal activation

### Exit

- target opposite side of current cloud
- stop beyond reversal extreme

### Important implementation notes

- requires complex X-axis time alignment
- most advanced of initial set
- should be clearly labeled high complexity and higher validation need

---

# 14. Indicator and Derived-Logic Requirements

## 14.1 Core indicators

The system must support at minimum:

- Ichimoku Cloud
- ATR
- Fibonacci retracement
- Fibonacci extension
- Fibonacci time zones
- fractal or zigzag swing logic
- candlestick pattern recognition
- rolling volatility measures
- simple regime filters

## 14.2 Ichimoku defaults

Default inputs:

- Tenkan = 9
- Kijun = 26
- Senkou Span B = 52
- Chikou shift = 26

These should be configurable per strategy but the above values are the standard baseline.

## 14.3 Swing detection

A robust swing detection module is required because multiple strategies depend on it for:

- A-B-C logic
- wave targets
- Fib anchoring
- time counts
- recent swing highs/lows
- reversal structure

## 14.4 Candlestick recognition

At minimum support:

- bullish engulfing
- bearish engulfing
- bullish pin bar / rejection
- bearish pin bar / rejection
- strong close continuation candle

---

# 15. Market Regime Framework

## 15.1 Why regime classification matters

Strategies should not evaluate blindly in all conditions.
Each strategy should operate only in conditions suited to its edge.

## 15.2 Required regime labels

At minimum:

- trending_bullish
- trending_bearish
- pullback_bullish
- pullback_bearish
- consolidation
- breakout_candidate
- volatility_expansion
- reversal_candidate
- no_trade

## 15.3 Regime usage

Examples:

- Kijun pullback only valid in pullback_bullish or pullback_bearish
- Chikou open space best in breakout_candidate or volatility_expansion
- Twist anticipator requires reversal_candidate
- Sanyaku best in clean trend/breakout environments

---

# 16. Backtesting Engine

## 16.1 Purpose

The backtester must provide deterministic, reproducible trade simulation over historical data.

## 16.2 Required capabilities

The backtester must support:

- one strategy on one instrument/timeframe
- one strategy across multiple instruments/timeframes
- multiple strategies across a batch matrix
- configurable date windows
- configurable transaction cost assumptions
- configurable slippage assumptions
- long/short toggles
- position sizing based on risk model
- trade event logging
- metrics output
- equity and drawdown output
- trade list output
- chart annotation output

## 16.3 Execution realism rules

The backtester should model:

- entry on next bar open where specified
- stop and target interaction in realistic order
- partial exit handling where applicable
- time stops
- stale signal invalidation
- one-trade-per-strategy-per-symbol rules where defined

## 16.4 Reproducibility rule

Given the same dataset, parameters, and assumptions, the backtester must produce the same result every time.

---

# 17. Research Matrix Engine

## 17.1 Purpose

The research matrix engine is one of the most important parts of the platform.
Its job is to discover:

- which strategy works best
- on which instrument
- on which timeframe
- under which conditions
- according to which scoring priority

## 17.2 Dimensions

The matrix should support runs across:

- strategy
- instrument
- timeframe
- date range
- direction bias
- parameter variants
- transaction cost profile
- slippage profile

## 17.3 Output philosophy

The engine must not only find raw winners.
It must identify:

- best absolute performers
- best risk-adjusted performers
- most stable performers
- performers with enough trade count
- performers robust across related contexts

## 17.4 Minimum trade count rule

To avoid misleading rankings:

- primary ranking threshold: minimum 80 trades
- exploratory threshold: 40–79 trades
- below 40 trades: insufficient for primary ranking

This threshold may be adjusted by timeframe and test window later, but V1 should start with strict discipline.

## 17.5 Required ranking leaderboards

The platform must support "best by" views for:

- net profit
- Sharpe ratio
- Sortino ratio
- profit factor
- expectancy
- max drawdown control
- recovery factor
- trade consistency
- combined score

## 17.6 Combined score model

A default composite score should be created, for example:

- 25% risk-adjusted quality
- 20% profit factor
- 20% normalized return
- 15% drawdown control
- 10% sample-size sufficiency
- 10% stability / consistency

The weightings must be configurable in the future.

## 17.7 Stability preference

FIBOKEI should prefer:

- higher-quality curves
- smoother equity
- lower drawdown for given return
- enough trades to matter
- repeatability across related market groups

not just the biggest raw profit number.

---

# 18. Performance Metrics

## 18.1 Required statistics

Every backtest and research result should produce at minimum:

- total net profit
- gross profit
- gross loss
- profit factor
- win rate
- loss rate
- expectancy
- average win
- average loss
- average R multiple if supported
- reward-to-risk ratio
- total trade count
- long trade count
- short trade count
- max drawdown
- max drawdown %
- Sharpe ratio
- Sortino ratio
- Calmar ratio
- recovery factor
- best trade
- worst trade
- average trade duration
- exposure %
- consecutive wins
- consecutive losses
- monthly returns
- yearly returns if test span allows

## 18.2 Useful secondary metrics

Also desirable:

- ulcer index
- equity curve slope
- rolling Sharpe
- return-to-drawdown ratio
- regime-specific performance
- session-specific performance
- instrument-class performance

---

# 19. Visual Analytics

## 19.1 Required visual outputs

The platform must produce:

- equity curve
- drawdown curve
- cumulative PnL curve
- monthly return heatmap
- trade PnL histogram
- duration distribution
- strategy comparison bar/line views
- instrument-timeframe heatmaps
- annotated entry/exit price charts

## 19.2 Required chart annotation features

Charts should display:

- entry arrows
- exit markers
- stop lines
- take-profit lines
- partial take-profit markers
- strategy labels
- Ichimoku cloud and lines
- Fibonacci levels
- swing markers where relevant

---

# 20. Risk and Portfolio Framework

## 20.1 Guiding principle

Risk management must be portfolio-aware and conservative by default.

## 20.2 Risk philosophy

The platform should not simply risk "1–2% per trade" without context.
It should intelligently cap aggregate and correlated exposure.

## 20.3 Recommended default risk model

Use:

- base risk per trade: 0.75%
- standard risk per trade: 1.00%
- elevated confidence risk: 1.25%
- absolute max risk: 2.00%, behind configuration and not default

## 20.4 Position sizing inputs

Position sizing must consider:

- account equity
- risk %
- stop distance
- instrument contract value / tick value / point value
- leverage assumptions where relevant
- asset-class-specific sizing nuances

## 20.5 Portfolio limits

Recommended defaults:

- max open portfolio risk: 5%
- max bucket risk by correlated group: 2.5%
- max simultaneous open trades: 8
- max open trades per instrument: configurable, default 2 across all strategies
- max same-direction stacking in highly correlated instruments: capped

## 20.6 Drawdown controls

Recommended defaults:

- daily soft stop: -3%
- daily hard stop: -4%
- weekly soft stop: -6%
- weekly hard stop: -8%

On hard-stop breach:

- no new entries
- send Telegram alert
- switch engine into safe mode
- require manual review/reset

## 20.7 Strategy-health controls

A strategy that materially underperforms its expected baseline in paper mode should be able to:

- receive a warning flag
- have its risk reduced
- be disabled
- be marked for review

## 20.8 Cooling and throttling

Support:

- cooldown after X consecutive losses
- cooldown after high-volatility rejection events
- time-based lockout after daily stop event
- stale signal expiry after N bars

---

# 21. Paper Trading Engine

## 21.1 Purpose

Paper mode is the live rehearsal layer.

## 21.2 Requirements

It must:

- consume candle updates / streaming-like updates
- evaluate strategies on close
- simulate entries/exits
- maintain position state
- maintain portfolio exposure state
- calculate realised and unrealised PnL
- store trade events
- send alerts
- reflect control-panel status in real time or near-real time

## 21.3 Paper-to-live similarity rule

Paper engine architecture should resemble future live engine architecture wherever practical.

That means:

- signal generation path should be the same
- trade plan object should be the same
- position lifecycle handling should be similar
- alert/event model should be the same
- only execution adapter should differ

---

# 22. Future Live Execution via IG

## 22.1 V1 status

Live execution is not enabled in initial release.

## 22.2 Design requirement

The platform must still be built so that later live trading can be added without rewriting strategy logic.

## 22.3 Broker adapter concept

All broker-specific behaviour must sit behind an adapter layer.

The adapter should ultimately support:

- authentication
- account sync
- instrument mapping
- order placement
- order status
- fill handling
- position sync
- partial close operations if supported
- reconciliation
- retry logic
- safe shutdown

## 22.4 Live readiness requirements before activation

No combination should be made live unless it passes:

- historical test quality thresholds
- paper stability thresholds
- operational checks
- slippage and order-behaviour checks
- human approval workflow

---

# 23. Alerts and Notifications

## 23.1 Alert channels

Primary initial notification channel:

- Telegram

## 23.2 Event types

Support alerts for:

- strategy signal detected
- paper order created
- trade opened
- partial take profit
- stop moved
- trade closed
- strategy disabled
- risk limit breached
- daily stop triggered
- weekly stop triggered
- data issue
- engine issue
- system restart
- daily summary
- weekly summary

## 23.3 Alert payload

Every alert should contain enough context to be useful:

- timestamp
- strategy
- instrument
- timeframe
- direction
- entry
- stop
- targets
- position size
- risk %
- current status
- closed PnL if relevant
- reason or rationale summary

---

# 24. Web Platform

## 24.1 Purpose

The web app is the command centre and inspection layer.

It should let a user:

- log in
- view current status
- inspect charts and signals
- run and compare backtests
- see research rankings
- manage running paper bots
- inspect history
- review settings and logs

## 24.2 Required pages

### Login

- secure login for Joe and Tom
- clean light-themed interface

### Loading screen

- animated startup/loading sequence after login

### Dashboard

Must show:

- total paper equity
- day PnL
- week PnL
- current drawdown
- active bots
- open trades
- recent trades
- alert feed
- top-ranked combinations
- system health summary

### Charts

Must support:

- instrument selection
- timeframe selection
- candlesticks
- Ichimoku overlays
- Fibonacci overlays
- entry/exit markers
- stop/target markers
- strategy-specific annotations

### Backtests

Must support:

- choosing strategy
- choosing instrument(s)
- choosing timeframe(s)
- date-range selection
- launching runs
- viewing stats
- viewing equity curve
- viewing drawdown curve
- viewing trade list
- comparing runs

### Running Bots

Must support:

- start/stop/pause bot
- set instrument/timeframe
- set risk profile
- see last signal reason
- see current bot state
- toggle combinations on/off for paper mode

### Trade History

Must support:

- filter by strategy
- filter by instrument
- filter by timeframe
- filter by date
- inspect closed trade details
- export CSV

### System & Settings

Must support:

- user settings
- Telegram settings
- risk defaults
- feature flags
- dataset registry
- engine status
- logging/diagnostics
- auth/admin settings

---

# 25. Text-to-Strategy Future Feature

## 25.1 Vision

Eventually, a user should be able to type a normalized description of a trading idea and have the platform convert it into a structured strategy draft.

## 25.2 Safety model

This feature must not turn raw text into a live bot automatically.

Approved workflow should be:

1. user enters idea in plain English
2. system parses into structured rules
3. user reviews and edits
4. system creates draft config / code scaffold
5. validation checks run
6. backtest runs
7. results displayed
8. user may approve for paper mode
9. live eligibility remains separate and gated

## 25.3 V1 status

This feature is not part of initial implementation, but architecture should leave room for it.

---

# 26. Repository Foundations and Required Files

The codebase should be scaffolded with a strong documentation and governance layer.

Required foundational files:

- README.md
- CLAUDE.md
- CLAUDE_RULES.md
- .cursorrules or Cursor-equivalent rules files
- PROJECT_MEMORY.md
- BUILD_LOG.md
- RULES.md

Required /docs files:

- blueprint.md
- project_summary.md
- roadmap.md
- architecture.md
- strategy_spec.md
- risk_model.md
- data_model.md
- deployment.md
- security.md
- backtesting_spec.md
- paper_trading_spec.md
- alerts_spec.md

---

# 27. Testing Requirements

## 27.1 Test philosophy

This project requires real software discipline.
Trading logic must be tested, not assumed.

## 27.2 Minimum testing layers

Support tests for:

- indicator correctness
- swing/fractal logic
- Fibonacci calculations
- Ichimoku array handling
- strategy signal generation
- strategy invalidation conditions
- backtester reproducibility
- risk engine calculations
- paper engine lifecycle
- API endpoints
- auth
- chart annotation payload validity

## 27.3 Regression testing

Important fixed datasets should be used to verify that:

- strategy outputs have not silently changed
- metrics remain reproducible
- bug fixes do not introduce logic drift

---

# 28. Logging, Auditability, and Observability

## 28.1 Logging requirements

The platform should log:

- strategy decisions
- signal generation
- trade creation
- trade closure
- risk rejections
- system warnings
- data warnings
- user actions
- service-level errors

## 28.2 Auditability rule

Every trade and decision should be traceable back to:

- strategy version
- parameter set
- dataset version
- signal timestamp
- execution reason
- exit reason

---

# 29. Security and Secrets

## 29.1 Non-negotiables

- no plaintext passwords in code
- no committed secrets
- environment-managed sensitive config
- auth must be secure even in V1
- future broker credentials must be isolated and protected

## 29.2 Feature-flag rule

Live execution features must be behind explicit feature flags and disabled by default.

---

# 30. Development Priorities

## 30.1 Priority Order

### Priority 1 — Foundation

- repo setup
- docs
- rules files
- monorepo skeleton
- auth skeleton
- data model skeleton
- strategy framework skeleton

### Priority 2 — Core research engine

- indicator engine
- swing/fractal engine
- backtester
- metrics engine
- chart annotation engine

### Priority 3 — Strategy library

- implement all 12 bots
- add tests
- validate outputs

### Priority 4 — Research matrix

- batch runner
- ranking engine
- composite scoring
- result storage and retrieval

### Priority 5 — Paper engine + alerts

- paper lifecycle
- bot orchestration
- Telegram alerts
- engine state tracking

### Priority 6 — Web platform

- login
- dashboard
- charts
- backtests
- running bots
- trade history
- settings

### Priority 7 — Live-ready architecture

- IG adapter spec
- execution abstraction
- safe feature flags

---

# 31. Definition of Success

Phase 1 of FIBOKEI is successful when all of the following are true:

- the repo and docs foundation is complete
- all 12 strategies exist under one common framework
- historical datasets can be ingested and normalized
- the full instrument universe (60+ instruments) can be tested with canonical data
- all target timeframes are supported
- backtests produce reliable metrics and annotated charts
- the research matrix can rank best combinations
- minimum trade count filters are enforced
- multiple approved combinations can run simultaneously in paper mode
- Telegram alerts work
- the web platform provides control and inspection of the system
- the architecture is ready for future IG live integration without rewriting strategy logic

---

# 32. Non-Negotiables

The following are absolute rules for the project:

- paper first
- no plaintext credentials
- no one-off strategy implementations outside the framework
- no mixing broker logic into strategy logic
- no live execution enabled by default
- all strategies must output normalized signals
- all results must be metrics-rich
- all charts must support entry/exit markers
- all ranking must consider trade count sufficiency
- portfolio-aware risk controls are mandatory
- docs-first workflow is mandatory

---

# 33. Immediate Next Step After This Blueprint

The next file to create is:

/docs/roadmap.md

That roadmap must convert this blueprint into a build plan with:

- phases
- sections
- subsections
- tasks
- dependencies
- milestones
- validation gates
- GitHub setup tasks
- Vercel setup tasks
- backend tasks
- frontend tasks
- data tasks
- strategy tasks
- testing tasks
- deployment tasks
- live-readiness tasks

This blueprint defines what FIBOKEI is.
The roadmap must define how FIBOKEI gets built.
