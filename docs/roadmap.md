# Fiboki — Build Roadmap

Version: 2.2
Status: **Phase 15.4 COMPLETE** — Results bookmarking & research templates. Phases 15.1–15.4 all complete, Phases 16–18 planned.
Last Updated: 2026-03-11
Reference: [blueprint.md](blueprint.md)

---

## Current State & Known Limitations

**What Fiboki is today:** A fully deployed multi-strategy trading research platform with 12 Ichimoku/Fibonacci strategies, 60-instrument canonical dataset (360 parquet files), production-grade backend on Railway, and Next.js frontend on Vercel. The backend is architecturally strong — research matrix, walk-forward/OOS/Monte Carlo validation, paper trading worker, IG demo integration, promotion gates, risk engine, and kill switch are all implemented and tested.

**What works well:**
- Backend architecture: strategy framework, backtester, research engine, paper trading, IG demo integration
- Data infrastructure: multi-tier resolution (canonical → starter → fixtures), manifest system, LRU cache
- Charting: KLineChart with Ichimoku overlays, interactive drawing tools, live IG demo mode (branch)
- Safety: feature flags, hard-blocked production URLs, kill switch, reconciliation, execution audit

**Known limitations — what the next phases address:**
- **Workflow connectivity (Phase 15.2 DONE)**: ~~Research → paper → demo promotion exists in backend but has no UI flow~~ Promotion UI deployed — "Promote to Paper" on research rankings, "Create Paper Bot" on backtest detail, source provenance tracking
- **Async jobs (Phase 15.1 DONE)**: ~~Research and backtests run synchronously~~ Async job engine deployed — research always async, backtests support `?async=true`
- **No operational visibility**: No bot fleet dashboard, no execution audit viewer, no alert centre, no exposure dashboard
- **Missing chart context on detail pages**: Trade detail and backtest detail pages lack KLineChart with trade markers (per charting spec requirements)
- **No portfolio-level analysis**: No scenario sandbox, no fleet-aware risk view, no correlation analysis
- **Observability gaps**: No error tracking (Sentry), no automated DB backups, no slippage analytics

**Pending merges:** None.

**Remaining legacy tasks:**
- T-12.02: ~~Trade detail replay/inspection~~ — absorbed into Phase 15.3 (DONE)
- T-13.02: Railway auto-deploy from GitHub
- T-13.05: Database backup strategy
- ~~T-13.07: Error tracking (Sentry)~~ — DONE

---

## Next Priorities

The recommended implementation order after completing Phase 14:

1. **Phase 15: Workflow Completion & Async Infrastructure** — async job engine, research→paper promotion UI, KLineChart on detail pages, results bookmarking. This is the highest-leverage work: it connects the backend's strong research/validation pipeline to the frontend so operators can actually use the full workflow.

2. **Phase 16: Operator Console & Fleet Operations** — bot fleet dashboard, alert centre, exposure dashboard, slippage analytics. Required before running multiple bots in production.

3. **Phase 17: Chart Workstation & Advanced Analysis** — drawing library, multi-chart layout, trade replay, market session context, scenario sandbox. Quality-of-life improvements for the analysis workflow.

4. **Phase 18: Strategy Families & Fleet Scaling** — parameter variation engine, fleet-aware risk, watchlists, trade journal. The most ambitious phase, requiring everything before it to be solid.

---

## Progress Tracker

| Phase | Status | Tests | Notes |
|-------|--------|-------|-------|
| Phase 1: Scaffold + First Heartbeat | COMPLETE | All pass | Indicators, data loader, CLI |
| Phase 2: One Complete Strategy Pipeline | COMPLETE | All pass | BOT-01, backtester, metrics |
| Phase 3: Scale the Engine | COMPLETE | All pass | All 12 strategies, DB, research matrix |
| Phase 4.1: FastAPI Foundation + Auth | COMPLETE | All pass | JWT, CORS, instruments, strategies routes |
| Phase 4.2: Backtest API Endpoints | COMPLETE | All pass | Run, list, detail, trades, equity-curve |
| Phase 4.3: Research API Endpoints | COMPLETE | All pass | Rankings, compare |
| Phase 4.4: Paper Trading Engine | COMPLETE | All pass | Account, bot, risk engine, orchestrator |
| Phase 4.5: Paper Trading API + Alerts | COMPLETE | All pass | API routes, Telegram notifier |
| Phase 5: Web Platform | COMPLETE | All pass | Next.js dashboard, KLineChart, Plotly analytics, execution adapters |
| Phase 6.1: Critical Fixes | COMPLETE | All pass | Symbol normalization, dropdown forms |
| Phase 6.2: Production Deployment | COMPLETE | All pass | Railway backend (api.fiboki.uk), Vercel frontend (fiboki.uk), PostgreSQL, cross-origin cookie auth verified |
| Phase 6.3: UX Improvements | COMPLETE | All pass | Dashboard polish, SVG logo, trade filters, visual hierarchy |
| Phase 6.4: Real Market Data | COMPLETE | All pass | yfinance ingestion, CLI refresh-data, API refresh endpoint |
| Phase 6.5: Canonical Data Expansion | COMPLETE | All pass | 60 instruments × 6 timeframes = 360 HistData datasets |
| Phase 7: Data Universe Consolidation | COMPLETE | All pass | 67-instrument registry, API asset_class filtering, grouped frontend selectors, list-data CLI |
| Phase 8: Research Engine V2 | COMPLETE | All pass | Walk-forward, OOS, Monte Carlo, sensitivity, validation rerun; batch UI with scoring controls |
| Phase 9: Always-On Paper Trading | COMPLETE | All pass | Worker service, DB-backed bot state, restart recovery, stale-data detection, health endpoint, daily Telegram alerts, promotion gate |
| Phase 10: IG Demo Integration | COMPLETE | All pass | IG REST client (demo only), epic mapping (65 instruments), order lifecycle, position sync, reconciliation, kill switch, execution audit, feature flags, frontend controls |
| Phase 10.5: Production Data Access | COMPLETE | All pass | Centralized path resolver, starter dataset (2.3MB, 7 majors H1), unified load_canonical(), .dockerignore |
| Phase 11: Live Readiness | COMPLETE | All pass | Risk limits config (env-var driven), promotion gates (Paper→Demo, Demo→Live), pre-live checklist, 18 gate tests |
| Phase 12: Frontend V2 | COMPLETE | Build clean | ExecutionModeBanner, backtest comparison, enhanced settings, searchable instrument select. T-12.02 absorbed into Phase 15.3 |
| Phase 13: CI/CD & Operations | PARTIAL | All pass | GitHub Actions CI, env var validation, structured logging, request IDs, Sentry error tracking. **Remaining: T-13.02 auto-deploy, T-13.05 DB backups** |
| Phase 14.1: Online Historical Data | COMPLETE | 467 pass | LRU cache, manifest generator/API, paginated market data, vectorized serialization, dynamic has_canonical_data, data_source observability |
| Phase 14.2: Drawing Tools | COMPLETE | All pass | DrawingToolbar (6 tools), klinecharts overlays, chart_drawings DB, CRUD API, auto-load/persist |
| Phase 14.3: Live Chart Mode | COMPLETE | 507 pass | IGDataProvider, TTL cache, ?mode=live, frontend toggle, SWR 5s auto-refresh. Merged to main |
| Phase 14.4: Full Production UX | COMPLETE | Build clean | Manifest-aware data availability, research preset builder (save/load configs), bulk data sync CLI (fibokei data-sync) |
| Phase 15.1: Async Job Engine | COMPLETE | 522 pass | Thread pool job engine, jobs API (list/detail/cancel), async backtests (?async=true), async research (always), Jobs page with progress bars + sidebar badge |
| Phase 15.2: Promotion Flow | COMPLETE | 526 pass | "Promote to Paper" on research (score >= 0.55), confirmation dialog, score coloring, "Create Paper Bot" on backtest detail, source_type/source_id provenance |
| Phase 15.3: Trade & Backtest Chart Context | COMPLETE | 526 pass | KLineChart with trade markers on backtest/trade detail pages, jump-to-trade, paginated trade list table |
| Phase 15.4: Results Bookmarking | COMPLETE | 526 pass | BookmarkModel + CRUD API, BookmarkButton component, bookmark toggles on backtests/trades/research pages, research preset save/load UI, bulk data sync CLI |
| Phase 16: Operator Console | PLANNED | — | Bot fleet dashboard, alert centre, exposure dashboard, slippage analytics |
| Phase 17: Chart Workstation | PLANNED | — | Drawing library, multi-chart layout, trade replay, market session context, scenario sandbox |
| Phase 18: Strategy Families & Fleet | PLANNED | — | Parameter variations, fleet-aware risk, watchlists, trade journal |

### Audit Fixes Applied (Post Phase 4.2)
- **C1**: Data loader now drops NaN rows after `to_numeric(coerce)` with warning
- **C2**: Engine always calls `position.update(bar)` first; mechanical stops take priority
- **H1**: Sharpe/Sortino use timeframe-aware `_BARS_PER_YEAR` mapping
- **H2**: Volatility indicator guards against zero/negative prices with `clip(lower=1e-10)`
- **M4**: `compute_metrics()` now includes `initial_capital` in output dict
- **M-misc**: BOT-03 bounds check, BOT-05 deprecated reindex, error info sanitization

---

## How to Read This Roadmap

This roadmap converts the FIBOKEI blueprint into executable build phases. It is optimized for **feedback-loop-first vertical slices** — each phase delivers something observable and testable rather than completing horizontal layers in isolation.

**Structure:**
- **18 Phases** (13 complete, 1 near-complete, 2 in-progress, 3 planned) with **50+ Subphases**
- Each subphase contains **Claude-executable tasks** — specific enough for one Claude Code session
- Each subphase ends with a **Verification Gate** — concrete tests that must pass before proceeding
- **Dependencies** are listed where ordering matters

**Task format:** Each task is written as an instruction Claude Code can execute directly. Tasks are numbered for reference (e.g., `T-1.1.03` = Phase 1, Subphase 1, Task 3).

**Phase grouping (Phases 15–18):**
- **Phase 15 (Workflow Completion)**: Connects the strong backend pipeline to usable frontend workflows — async jobs, promotion UI, chart context on detail pages
- **Phase 16 (Operator Console)**: Fleet operations, alerting, exposure — required before scaling to many bots
- **Phase 17 (Chart Workstation)**: Advanced chart features, replay, scenario sandbox — analysis depth
- **Phase 18 (Fleet Scaling)**: Strategy families, parameter variations, fleet-aware risk — the most ambitious phase

---

# PHASE 1: SCAFFOLD + FIRST HEARTBEAT

**Objective:** Go from empty repo to a working Ichimoku indicator computed on real price data and printed to terminal.

**Blueprint sections covered:** S9 (Architecture), S10 (Tech Stack), S26 (Repo Foundations), S8 (Data Strategy), S14 (Indicators)

**Milestone:** `python -c "from fibokei.indicators.ichimoku import IchimokuCloud"` succeeds and indicator values print on sample data.

---

## Subphase 1.1 — Repository Skeleton and Governance [COMPLETE]

**Goal:** Monorepo structure, Python project, governance files, git init.

**Dependencies:** None (first subphase)

### Tasks

- [x] **T-1.1.01** — Initialize git repository in `/Users/joseph/Projects/Fiboki_Trading`. Create `.gitignore` covering Python (`__pycache__/`, `*.pyc`, `.venv/`, `dist/`, `*.egg-info/`), Node (`node_modules/`, `.next/`, `.vercel/`), IDE (`.vscode/`, `.idea/`), secrets (`.env`, `.env.local`, `*.key`), data (`data/raw/`, `*.csv` in data dirs but not test fixtures), and OS files (`.DS_Store`).

- [x] **T-1.1.02** — Create the monorepo directory structure:
```
backend/
  src/
    fibokei/
      __init__.py
      core/
        __init__.py
      indicators/
        __init__.py
      strategies/
        __init__.py
      backtester/
        __init__.py
      research/
        __init__.py
      paper/
        __init__.py
      risk/
        __init__.py
      api/
        __init__.py
      data/
        __init__.py
      alerts/
        __init__.py
      execution/
        __init__.py
  tests/
    __init__.py
    conftest.py
    fixtures/
frontend/
  (empty — scaffolded in Phase 5)
docs/
  (blueprint.md and roadmap.md already exist)
scripts/
data/
  raw/
  cleaned/
  fixtures/
```

- [x] **T-1.1.03** — Create `backend/pyproject.toml` with:
- Project name: `fibokei`
- Python requirement: `>=3.11`
- Dependencies: `pandas>=2.0`, `numpy>=1.24`, `pydantic>=2.0`, `python-dateutil`
- Dev dependencies: `pytest>=7.0`, `pytest-cov`, `ruff`, `mypy`
- Package source: `src`
- Entry point: `fibokei = fibokei.cli:main`
- Pytest config: `testpaths = ["tests"]`
- Ruff config: line-length 100, target Python 3.11

- [x] **T-1.1.04** — Create `CLAUDE.md` at project root with:
- Project overview (FIBOKEI automated trading platform)
- Tech stack (Python 3.11+, FastAPI, Next.js + TypeScript)
- Key commands: `cd backend && pip install -e ".[dev]"`, `pytest`, `ruff check`, `python -m fibokei`
- Architecture rules from blueprint S9.3 (strategy ≠ broker, indicators centralized, risk not in strategies, frontend ≠ trading logic)
- Non-negotiables from blueprint S32
- File structure guide

- [x] **T-1.1.05** — Create `README.md` at project root with: project name, one-paragraph description, tech stack list, setup instructions placeholder, link to `docs/blueprint.md` and `docs/roadmap.md`.

- [x] **T-1.1.06** — Create `RULES.md` at project root summarizing the coding standards: closed-candle-only signals, UTC timestamps, no plaintext secrets, all strategies use common framework, deterministic backtest results, risk-controlled defaults.

- [x] **T-1.1.07** — Create initial commit with all scaffold files.

### Verification Gate

```bash
cd backend && pip install -e ".[dev]"
python -c "import fibokei; print('OK')"
pytest --co  # collection succeeds, 0 tests is fine
ruff check src/
```
All must succeed without errors.

---

## Subphase 1.2 — Core Data Models and OHLCV Ingestion [COMPLETE]

**Goal:** Define all core data types and build a CSV data loader with validation.

**Dependencies:** Subphase 1.1

**Blueprint sections covered:** S8.4 (OHLCV schema), S8.5 (Quality checks), S7.4 (Instrument grouping)

### Tasks

- [x] **T-1.2.01** — Create `backend/src/fibokei/core/models.py` with Pydantic v2 models:
- `Timeframe` enum: `M1, M2, M5, M15, M30, H1, H4`
- `AssetClass` enum: `FOREX_MAJOR, FOREX_CROSS, COMMODITY_METAL, COMMODITY_ENERGY, INDEX, CRYPTO`
- `Direction` enum: `LONG, SHORT`
- `Instrument` model: `symbol: str`, `name: str`, `asset_class: AssetClass`, `pip_value: float | None`, `ig_epic: str | None`
- `OHLCVBar` model: `timestamp: datetime`, `open: float`, `high: float`, `low: float`, `close: float`, `volume: float`
- `DatasetMeta` model: `instrument: str`, `timeframe: Timeframe`, `source_id: str`, `timezone: str = "UTC"`, `ingest_version: str`, `bar_count: int`, `start: datetime`, `end: datetime`, `status: str`

- [x] **T-1.2.02** — Create `backend/src/fibokei/core/instruments.py` with the instrument universe from blueprint §7.2 (67 instruments: 60 HistData canonical + 7 alternate-provider). Define each as an `Instrument` instance with symbol, name, and asset_class. Provide `get_instrument(symbol: str) -> Instrument` and `get_instruments_by_class(asset_class: AssetClass) -> list[Instrument]` functions.

- [x] **T-1.2.03** — Create `backend/src/fibokei/data/loader.py` with:
- `load_ohlcv_csv(path: str, instrument: str, timeframe: Timeframe) -> pd.DataFrame` — reads CSV, standardizes column names to `timestamp, open, high, low, close, volume`, parses timestamps to UTC datetime, sorts by timestamp ascending.
- Support for common CSV formats: with/without header, various date formats, comma/semicolon delimiters.
- Returns DataFrame with DatetimeIndex.

- [x] **T-1.2.04** — Create `backend/src/fibokei/data/validator.py` with `validate_ohlcv(df: pd.DataFrame) -> list[str]` that checks for:
- Missing/null values in OHLC columns
- `high < low` violations
- `open` or `close` outside `[low, high]` range
- Duplicate timestamps
- Out-of-order timestamps
- Negative prices
- Suspicious gaps (>3x median gap between consecutive bars)
- Returns list of warning strings (empty = valid)

- [x] **T-1.2.05** — Create a sample test fixture at `data/fixtures/sample_eurusd_h1.csv` with at least 500 bars of realistic EURUSD H1 OHLCV data. This can be synthetic but must have realistic price ranges (~1.05–1.15), realistic candle sizes (~10-80 pip range), and monotonically increasing hourly timestamps. Include a Python script at `scripts/generate_sample_data.py` that generates this fixture deterministically using a seeded random walk.

- [x] **T-1.2.06** — Create `backend/tests/test_data_models.py` with tests for:
- All enum values accessible
- Instrument creation and lookup
- OHLCVBar validation (valid and invalid cases)
- DatasetMeta creation

- [x] **T-1.2.07** — Create `backend/tests/test_data_loader.py` with tests for:
- Loading the sample fixture CSV
- Correct column names after normalization
- Correct dtype for timestamp index
- Row count matches expected
- Values are in plausible ranges

- [x] **T-1.2.08** — Create `backend/tests/test_data_validator.py` with tests for:
- Valid data passes with no warnings
- Detects high < low
- Detects duplicate timestamps
- Detects out-of-order timestamps
- Detects negative prices
- Detects suspicious gaps

### Verification Gate

```bash
cd backend
pytest tests/test_data_models.py tests/test_data_loader.py tests/test_data_validator.py -v
python -c "
from fibokei.data.loader import load_ohlcv_csv
from fibokei.core.models import Timeframe
df = load_ohlcv_csv('../data/fixtures/sample_eurusd_h1.csv', 'EURUSD', Timeframe.H1)
print(f'Loaded {len(df)} bars, columns: {list(df.columns)}')
print(df.head())
"
```
All tests pass. CSV loads and prints 500+ bars with correct columns.

---

## Subphase 1.3 — Ichimoku Cloud and ATR Indicators [COMPLETE]

**Goal:** First working indicators computed on real data.

**Dependencies:** Subphase 1.2

**Blueprint sections covered:** S14.1 (Core indicators), S14.2 (Ichimoku defaults)

### Tasks

- [x] **T-1.3.01** — Create `backend/src/fibokei/indicators/base.py` with:
- `Indicator` abstract base class:
  - `name: str` property
  - `compute(df: pd.DataFrame) -> pd.DataFrame` abstract method — takes OHLCV DataFrame, returns same DataFrame with new indicator columns added
  - `required_columns: list[str]` property — columns needed in input (default: `["open", "high", "low", "close"]`)
  - `warmup_period: int` property — minimum bars needed before indicator values are valid

- [x] **T-1.3.02** — Create `backend/src/fibokei/indicators/ichimoku.py` implementing `IchimokuCloud(Indicator)` with:
- Parameters: `tenkan_period: int = 9`, `kijun_period: int = 26`, `senkou_b_period: int = 52`, `chikou_shift: int = 26`
- `compute()` adds columns:
  - `tenkan_sen`: (highest high + lowest low) / 2 over tenkan_period
  - `kijun_sen`: (highest high + lowest low) / 2 over kijun_period
  - `senkou_span_a`: (tenkan_sen + kijun_sen) / 2, shifted forward 26 periods
  - `senkou_span_b`: (highest high + lowest low) / 2 over senkou_b_period, shifted forward 26 periods
  - `chikou_span`: close price shifted backward 26 periods
- `warmup_period`: `senkou_b_period + chikou_shift` (78)
- All calculations must use pandas rolling operations for efficiency
- Handle NaN values at boundaries correctly

- [x] **T-1.3.03** — Create `backend/src/fibokei/indicators/atr.py` implementing `ATR(Indicator)` with:
- Parameters: `period: int = 14`
- `compute()` adds column `atr`:
  - True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
  - ATR = exponential moving average of True Range over period
- `warmup_period`: `period`

- [x] **T-1.3.04** — Create `backend/src/fibokei/indicators/registry.py` with:
- `IndicatorRegistry` class with `register(indicator_class)` and `get(name: str) -> Indicator` methods
- Pre-register `IchimokuCloud` and `ATR`
- `list_available() -> list[str]` method

- [x] **T-1.3.05** — Create `backend/tests/test_ichimoku.py` with:
- Test with known 100-bar price series where Tenkan/Kijun values can be manually verified for at least 3 specific bars
- Test that warmup period produces NaN for early bars and valid values after
- Test with custom parameters (e.g., tenkan=7, kijun=22)
- Test column names are correct
- Test output DataFrame has same length as input

- [x] **T-1.3.06** — Create `backend/tests/test_atr.py` with:
- Test with known price series where ATR can be manually computed
- Test warmup period NaN handling
- Test with custom period

- [x] **T-1.3.07** — Create a CLI demo script at `backend/src/fibokei/cli.py` with a `main()` function that:
- Loads the sample EURUSD fixture
- Computes Ichimoku Cloud
- Computes ATR
- Prints last 10 bars with: timestamp, close, tenkan, kijun, senkou_a, senkou_b, chikou, atr
- Formatted as an aligned table using `tabulate` or manual string formatting
- Add `tabulate` to pyproject.toml dependencies

### Verification Gate

```bash
cd backend
pytest tests/test_ichimoku.py tests/test_atr.py -v
python -m fibokei
```
All tests pass. CLI prints a readable table with 10 rows of EURUSD H1 data + Ichimoku + ATR values. No NaN in the displayed rows.

---

# PHASE 2: ONE COMPLETE STRATEGY PIPELINE

**Objective:** BOT-01 (Pure Sanyaku Confluence) runs through the backtester and produces a full metrics report via CLI.

**Blueprint sections covered:** S12 (Strategy Framework), S13.1 (BOT-01), S15 (Market Regime), S16 (Backtester), S18 (Metrics)

**Milestone:** `python -m fibokei backtest --strategy sanyaku --instrument EURUSD --timeframe H1` prints metrics table with 25+ statistics.

---

## Subphase 2.1 — Strategy Framework and Base Classes [COMPLETE]

**Goal:** Define the abstract strategy contract that all 12 bots will implement.

**Dependencies:** Subphase 1.3

### Tasks

- [x] **T-2.1.01** — Create `backend/src/fibokei/core/signals.py` with Pydantic models:
- `Signal` model with all fields from blueprint S12.5:
  - `timestamp: datetime`
  - `instrument: str`
  - `timeframe: Timeframe`
  - `strategy_id: str`
  - `direction: Direction`
  - `setup_type: str`
  - `entry_type: str` (market, limit, stop)
  - `proposed_entry: float`
  - `stop_loss: float`
  - `take_profit_primary: float`
  - `take_profit_secondary: float | None`
  - `confidence_score: float` (0.0 to 1.0)
  - `regime_label: str`
  - `signal_valid: bool`
  - `invalidation_reason: str | None`
  - `rationale_summary: str`
  - `supporting_factors: list[str]`
  - `annotation_payload: dict | None`

- [x] **T-2.1.02** — Create `backend/src/fibokei/core/trades.py` with Pydantic models:
- `TradePlan` model with all fields from blueprint S12.6
- `ExitReason` enum from blueprint S12.7
- `TradeResult` model

- [x] **T-2.1.03** — Create `backend/src/fibokei/strategies/base.py` with `Strategy` abstract base class containing:
- Identity fields from blueprint S12.2
- Configuration: `config: dict` for strategy-specific parameters with defaults
- Abstract methods from blueprint S12.4
- Concrete helper methods

- [x] **T-2.1.04** — Create `backend/src/fibokei/strategies/registry.py` with:
- `StrategyRegistry` class: `register(strategy_class)`, `get(strategy_id: str) -> Strategy`, `list_available() -> list[dict]`

- [x] **T-2.1.05** — Create `backend/tests/test_strategy_base.py` testing:
- Cannot instantiate `Strategy` directly (abstract)
- A minimal concrete subclass can be created and its identity fields accessed
- `get_required_indicators()` returns expected list
- Registry stores and retrieves strategies

### Verification Gate

```bash
cd backend && pytest tests/test_strategy_base.py -v
```
All tests pass. Strategy ABC enforces required methods. Registry works.

---

## Subphase 2.2 — Supporting Indicators: Swing Detection, Candlestick Patterns, Regime [COMPLETE]

**Goal:** Build the indicator modules that BOT-01 and other strategies need beyond Ichimoku.

**Dependencies:** Subphase 1.3

### Tasks

- [x] **T-2.2.01** — Create `backend/src/fibokei/indicators/swing.py` implementing `SwingDetector(Indicator)` with fractal logic for swing high/low detection.

- [x] **T-2.2.02** — Create `backend/src/fibokei/indicators/candles.py` implementing `CandlestickPatterns(Indicator)` with: bullish/bearish engulfing, bullish/bearish pin bar, strong bullish/bearish close.

- [x] **T-2.2.03** — Create `backend/src/fibokei/indicators/regime.py` implementing `MarketRegime(Indicator)` with regime classification: trending_bullish, trending_bearish, pullback_bullish, pullback_bearish, consolidation, breakout_candidate, volatility_expansion, reversal_candidate, no_trade.

- [x] **T-2.2.04** — Create `backend/tests/test_swing.py` with known price series tests.

- [x] **T-2.2.05** — Create `backend/tests/test_candles.py` with pattern detection tests.

- [x] **T-2.2.06** — Create `backend/tests/test_regime.py` with regime classification tests.

- [x] **T-2.2.07** — Register all new indicators in the indicator registry.

### Verification Gate

```bash
cd backend && pytest tests/test_swing.py tests/test_candles.py tests/test_regime.py -v
```
All tests pass. All indicators registered and accessible via registry.

---

## Subphase 2.3 — BOT-01: Pure Sanyaku Confluence [COMPLETE]

**Goal:** First complete strategy implementation.

**Dependencies:** Subphases 2.1, 2.2

**Blueprint section:** S13.1

### Tasks

- [x] **T-2.3.01** — Create `backend/src/fibokei/strategies/bot01_sanyaku.py` implementing `PureSanyakuConfluence(Strategy)` with Sanyaku three-line confirmation logic.

- [x] **T-2.3.02** — Register BOT-01 in strategy registry.

- [x] **T-2.3.03** — Create `backend/tests/test_bot01_sanyaku.py` with unit tests for signal generation and validation.

- [x] **T-2.3.04** — Create `backend/tests/test_bot01_integration.py` with integration test on sample EURUSD H1 data.

### Verification Gate

```bash
cd backend && pytest tests/test_bot01_sanyaku.py tests/test_bot01_integration.py -v
```
All tests pass. BOT-01 generates valid signals on sample data.

---

## Subphase 2.4 — Backtesting Engine Core [COMPLETE]

**Goal:** Simulate strategy execution on historical data with realistic trade mechanics.

**Dependencies:** Subphase 2.3

**Blueprint section:** S16

### Tasks

- [x] **T-2.4.01** — Create `backend/src/fibokei/backtester/config.py` with `BacktestConfig` model.

- [x] **T-2.4.02** — Create `backend/src/fibokei/backtester/position.py` with `Position` class tracking trade state, MFE/MAE, and position sizing.

- [x] **T-2.4.03** — Create `backend/src/fibokei/backtester/engine.py` with `Backtester` class implementing bar-by-bar simulation with spread/slippage.

- [x] **T-2.4.04** — Create `backend/src/fibokei/backtester/result.py` with `BacktestResult` model.

- [x] **T-2.4.05** — Create `backend/tests/test_backtester.py` with position sizing, spread/slippage, time stop, and BOT-01 integration tests.

- [x] **T-2.4.06** — Create `backend/tests/test_backtester_determinism.py` verifying identical results on repeated runs.

### Verification Gate

```bash
cd backend && pytest tests/test_backtester.py tests/test_backtester_determinism.py -v
```
All tests pass. Backtester produces deterministic results. BOT-01 integration run generates trades.

---

## Subphase 2.5 — Performance Metrics Engine and CLI [COMPLETE]

**Goal:** Compute all required statistics and display results via command line.

**Dependencies:** Subphase 2.4

**Blueprint section:** S18

### Tasks

- [x] **T-2.5.01** — Create `backend/src/fibokei/backtester/metrics.py` with `compute_metrics()` calculating all 25+ metrics from blueprint S18.1.

- [x] **T-2.5.02** — Add monthly and yearly returns calculation to `compute_metrics()`.

- [x] **T-2.5.03** — Create `backend/src/fibokei/backtester/display.py` with formatted table output using tabulate.

- [x] **T-2.5.04** — Update `backend/src/fibokei/cli.py` to add backtest command with argparse subcommands.

- [x] **T-2.5.05** — Create `backend/tests/test_metrics.py` with known-value tests for all metric calculations.

- [x] **T-2.5.06** — Create `backend/tests/test_cli.py` with CLI command tests.

### Verification Gate

```bash
cd backend
pytest tests/test_metrics.py tests/test_cli.py -v
python -m fibokei backtest --strategy bot01_sanyaku --instrument EURUSD --timeframe H1
```
All tests pass. CLI command prints a full metrics table with 25+ statistics and a trade list. This is the **critical vertical-slice milestone** — a complete pipeline from data to results.

---

# PHASE 3: SCALE THE ENGINE

**Objective:** All 12 strategies implemented, research matrix ranks combinations, results persist to database.

**Blueprint sections covered:** S13.2–S13.12 (Strategies 2-12), S14 (Indicators), S17 (Research Matrix)

**Milestone:** `python -m fibokei research --strategies all --instruments EURUSD,GBPUSD --timeframes H1,H4` prints a ranked leaderboard.

---

## Subphase 3.1 — Fibonacci Indicators [COMPLETE]

**Goal:** Complete the indicator library needed for Fibonacci-based strategies.

**Dependencies:** Subphase 1.3

### Tasks

- [x] **T-3.1.01** — Create `backend/src/fibokei/indicators/fibonacci.py` implementing `FibonacciRetracement(Indicator)` with standard retracement levels (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0).

- [x] **T-3.1.02** — Add `FibonacciExtension` with extension levels (1.0, 1.272, 1.618, 2.0, 2.618).

- [x] **T-3.1.03** — Add `FibonacciTimeZones` with time zone detection at Fibonacci intervals.

- [x] **T-3.1.04** — Create `backend/src/fibokei/indicators/volatility.py` with `RollingVolatility(Indicator)`.

- [x] **T-3.1.05** — Register all new indicators. Create `backend/tests/test_fibonacci.py` and `backend/tests/test_volatility.py` with known-value tests.

### Verification Gate

```bash
cd backend && pytest tests/test_fibonacci.py tests/test_volatility.py -v
```
All tests pass. Fibonacci levels compute correctly.

---

## Subphase 3.2 — BOT-02 through BOT-06 (Ichimoku Family) [COMPLETE]

**Goal:** Implement the 5 Ichimoku-focused strategies.

**Dependencies:** Subphases 2.1, 2.2, 3.1

**Blueprint sections:** S13.2–S13.6

### Tasks

- [x] **T-3.2.01** — Create `backend/src/fibokei/strategies/bot02_kijun_pullback.py` implementing BOT-02 (Kijun-sen Pullback) per blueprint S13.2.

- [x] **T-3.2.02** — Create `backend/src/fibokei/strategies/bot03_flat_senkou_b.py` implementing BOT-03 (Flat Senkou Span B Bounce) per blueprint S13.3.

- [x] **T-3.2.03** — Create `backend/src/fibokei/strategies/bot04_chikou_momentum.py` implementing BOT-04 (Chikou Open Space Momentum) per blueprint S13.4.

- [x] **T-3.2.04** — Create `backend/src/fibokei/strategies/bot05_mtfa_sanyaku.py` implementing BOT-05 (MTFA Sanyaku) per blueprint S13.5.

- [x] **T-3.2.05** — Create `backend/src/fibokei/strategies/bot06_nwave.py` implementing BOT-06 (N-Wave Structural Targeting) per blueprint S13.6.

- [x] **T-3.2.06** — Register all 5 strategies. Create test files `backend/tests/test_bot02.py` through `backend/tests/test_bot06.py`.

- [x] **T-3.2.07** — Run all 6 strategies through the backtester on sample EURUSD H1 data and print comparison table.

### Verification Gate

```bash
cd backend
pytest tests/test_bot02.py tests/test_bot03.py tests/test_bot04.py tests/test_bot05.py tests/test_bot06.py -v
python -m fibokei backtest --strategy all --instrument EURUSD --timeframe H1
```
All tests pass. All 6 strategies produce backtest results.

---

## Subphase 3.3 — BOT-07 through BOT-12 (Hybrid Family) [COMPLETE]

**Goal:** Complete the strategy library with 6 Fibonacci/hybrid strategies.

**Dependencies:** Subphases 3.1, 3.2

**Blueprint sections:** S13.7–S13.12

### Tasks

- [x] **T-3.3.01** — Create `backend/src/fibokei/strategies/bot07_kumo_twist.py` implementing BOT-07 (Kumo Twist Anticipator) per blueprint S13.7.

- [x] **T-3.3.02** — Create `backend/src/fibokei/strategies/bot08_kihon_suchi.py` implementing BOT-08 (Kihon Suchi Time Cycle Confluence) per blueprint S13.8.

- [x] **T-3.3.03** — Create `backend/src/fibokei/strategies/bot09_golden_cloud.py` implementing BOT-09 (Golden Cloud Confluence) per blueprint S13.9.

- [x] **T-3.3.04** — Create `backend/src/fibokei/strategies/bot10_kijun_382.py` implementing BOT-10 (Kijun + 38.2% Shallow Continuation) per blueprint S13.10.

- [x] **T-3.3.05** — Create `backend/src/fibokei/strategies/bot11_sanyaku_fib_ext.py` implementing BOT-11 (Sanyaku + Fib Extension Targets) per blueprint S13.11.

- [x] **T-3.3.06** — Update `backend/src/fibokei/backtester/position.py` to support partial closes.

- [x] **T-3.3.07** — Create `backend/src/fibokei/strategies/bot12_twist_fib_time.py` implementing BOT-12 (Kumo Twist + Fibonacci Time Zone Anticipator) per blueprint S13.12.

- [x] **T-3.3.08** — Register all 6 strategies. Create test files `backend/tests/test_bot07.py` through `backend/tests/test_bot12.py`.

### Verification Gate

```bash
cd backend
pytest tests/test_bot07.py tests/test_bot08.py tests/test_bot09.py tests/test_bot10.py tests/test_bot11.py tests/test_bot12.py -v
python -m fibokei list-strategies  # Should list all 12
python -m fibokei backtest --strategy all --instrument EURUSD --timeframe H1
```
All tests pass. All 12 strategies registered and produce backtest results.

---

## Subphase 3.4 — Database Setup and Persistence [COMPLETE]

**Goal:** Persist backtest results, datasets, and strategy configs.

**Dependencies:** Subphase 2.5

### Tasks

- [x] **T-3.4.01** — Add dependencies to `pyproject.toml`: `sqlalchemy>=2.0`, `alembic`, `aiosqlite`.

- [x] **T-3.4.02** — Create `backend/src/fibokei/db/models.py` with SQLAlchemy 2.0 models: UserModel, DatasetModel, BacktestRunModel, TradeModel, ResearchResultModel, StrategyConfigModel.

- [x] **T-3.4.03** — Create `backend/src/fibokei/db/database.py` with engine, session factory, and init_db.

- [x] **T-3.4.04** — Create `backend/src/fibokei/db/repository.py` with data access functions.

- [x] **T-3.4.05** — Set up Alembic with initial migration.

- [x] **T-3.4.06** — Update `Backtester.run()` to optionally save results to database.

- [x] **T-3.4.07** — Create `backend/tests/test_db.py` with database round-trip tests.

### Verification Gate

```bash
cd backend
alembic upgrade head
pytest tests/test_db.py -v
```
All tests pass. Database creates, migrations run, data round-trips correctly.

---

## Subphase 3.5 — Research Matrix Engine [COMPLETE]

**Goal:** Batch-run strategies across instruments/timeframes, score, and rank.

**Dependencies:** Subphases 3.3, 3.4

**Blueprint section:** S17

### Tasks

- [x] **T-3.5.01** — Create `backend/src/fibokei/research/scorer.py` with `compute_composite_score()` using blueprint S17.6 weights.

- [x] **T-3.5.02** — Create `backend/src/fibokei/research/matrix.py` with `ResearchMatrix` class for batch backtesting and ranking.

- [x] **T-3.5.03** — Create `backend/src/fibokei/research/filter.py` with minimum trade count filters.

- [x] **T-3.5.04** — Create `backend/src/fibokei/research/display.py` with leaderboard and best-by views.

- [x] **T-3.5.05** — Update CLI to add research command.

- [x] **T-3.5.06** — Create `backend/tests/test_scorer.py` with known-value scoring tests.

- [x] **T-3.5.07** — Create `backend/tests/test_matrix.py` with multi-strategy matrix tests.

### Verification Gate

```bash
cd backend
pytest tests/test_scorer.py tests/test_matrix.py -v
python -m fibokei research --strategies bot01_sanyaku,bot02_kijun_pullback --instruments EURUSD --timeframes H1 --data-dir ../data/fixtures/
```
All tests pass. Research matrix produces ranked results. Leaderboard prints with composite scores.

---

# PHASE 4: API LAYER + PAPER TRADING + ALERTS

**Objective:** FastAPI serves all engine data, paper trading runs, Telegram alerts fire.

**Blueprint sections covered:** S10.2 (API), S6 (Users), S21 (Paper Trading), S20 (Risk), S23 (Alerts)

**Milestone:** Paper bot runs via API, Telegram sends test alert.

---

## Subphase 4.1 — FastAPI Foundation and Auth [COMPLETE]

**Goal:** API skeleton with JWT auth for Joe and Tom.

**Dependencies:** Subphase 3.4

### Tasks

- [x] **T-4.1.01** — Add dependencies to `pyproject.toml`: `fastapi>=0.100`, `uvicorn[standard]`, `python-jose[cryptography]`, `passlib[bcrypt]`, `python-multipart`.

- [x] **T-4.1.02** — Create `backend/src/fibokei/api/app.py` with FastAPI application, CORS middleware, API versioning, lifespan handler, and health check.

- [x] **T-4.1.03** — Create `backend/src/fibokei/api/auth.py` with JWT login, `get_current_user()` dependency, bcrypt password hashing.

- [x] **T-4.1.04** — Create `backend/src/fibokei/api/seed.py` with user seeding from environment variables.

- [x] **T-4.1.05** — Create `backend/src/fibokei/api/routes/instruments.py` with instrument list/detail endpoints.

- [x] **T-4.1.06** — Create `backend/src/fibokei/api/routes/strategies.py` with strategy list/detail endpoints.

- [x] **T-4.1.07** — Create `backend/tests/test_api_auth.py` with auth flow tests.

- [x] **T-4.1.08** — Create `backend/tests/test_api_instruments.py` and `backend/tests/test_api_strategies.py` with endpoint tests.

### Verification Gate

```bash
cd backend
pytest tests/test_api_auth.py tests/test_api_instruments.py tests/test_api_strategies.py -v
FIBOKEI_JWT_SECRET=test-secret uvicorn fibokei.api.app:app --host 0.0.0.0 --port 8000
```
All tests pass. API starts and health check responds.

---

## Subphase 4.2 — Backtest API Endpoints [COMPLETE]

**Goal:** Trigger and retrieve backtests via API.

**Dependencies:** Subphase 4.1

### Tasks

- [x] **T-4.2.01** — Create `backend/src/fibokei/api/routes/backtests.py` with: POST run, GET list, GET detail, GET trades, GET equity-curve endpoints.

- [x] **T-4.2.02** — Create request/response Pydantic models in `backend/src/fibokei/api/schemas/backtests.py`.

- [x] **T-4.2.03** — Create `backend/tests/test_api_backtests.py` with endpoint tests.

### Verification Gate

```bash
cd backend && pytest tests/test_api_backtests.py -v
```
All tests pass. Can trigger backtest via API and retrieve results.

---

## Subphase 4.3 — Research API Endpoints [COMPLETE]

**Goal:** Trigger research runs and retrieve rankings via API.

**Dependencies:** Subphases 4.1, 3.5

### Tasks

- [x] **T-4.3.01** — Create `backend/src/fibokei/api/routes/research.py` with: POST run, GET runs, GET run detail, GET rankings, GET compare endpoints.

- [x] **T-4.3.02** — Create `backend/tests/test_api_research.py` with endpoint tests.

### Verification Gate

```bash
cd backend && pytest tests/test_api_research.py -v
```
All tests pass. Research endpoints serve ranked data.

---

## Subphase 4.4 — Paper Trading Engine [COMPLETE]

**Goal:** Forward-test strategies with virtual capital.

**Dependencies:** Subphases 3.3, 3.4

**Blueprint sections:** S21, S20

### Tasks

- [x] **T-4.4.01** — Create `backend/src/fibokei/paper/account.py` with `PaperAccount` class.

- [x] **T-4.4.02** — Create `backend/src/fibokei/paper/bot.py` with `PaperBot` class and state machine (IDLE → MONITORING → POSITION_OPEN → IDLE).

- [x] **T-4.4.03** — Create `backend/src/fibokei/risk/engine.py` with `RiskEngine` class implementing per-trade, portfolio, and drawdown risk checks.

- [x] **T-4.4.04** — Create `backend/src/fibokei/paper/orchestrator.py` with `BotOrchestrator` managing multiple paper bots.

- [x] **T-4.4.05** — Create `backend/tests/test_paper_account.py`, `backend/tests/test_paper_bot.py`, `backend/tests/test_risk_engine.py`.

### Verification Gate

```bash
cd backend
pytest tests/test_paper_account.py tests/test_paper_bot.py tests/test_risk_engine.py -v
```
All tests pass. Paper bot can run through a simulated data replay and produce trades.

---

## Subphase 4.5 — Paper Trading API + Telegram Alerts [COMPLETE]

**Goal:** Control paper bots via API and receive Telegram notifications.

**Dependencies:** Subphases 4.1, 4.4

**Blueprint section:** S23

### Tasks

- [x] **T-4.5.01** — Create `backend/src/fibokei/api/routes/paper.py` with: POST create bot, GET list bots, GET bot detail, POST stop, POST pause, GET bot trades, GET account endpoints.

- [x] **T-4.5.02** — Create `backend/src/fibokei/alerts/telegram.py` with `TelegramNotifier` class for signal, trade, risk, and daily summary alerts.

- [x] **T-4.5.03** — Create `backend/src/fibokei/alerts/events.py` with `AlertEvent` enum and `AlertDispatcher` class.

- [x] **T-4.5.04** — Create `backend/tests/test_telegram.py` with message formatting tests (mocked HTTP).

- [x] **T-4.5.05** — Create `backend/tests/test_api_paper.py` with paper bot API endpoint tests.

### Verification Gate

```bash
cd backend
pytest tests/test_telegram.py tests/test_api_paper.py -v
```
All tests pass. Paper bot API works. Telegram test message sends successfully.

---

# PHASE 5: WEB PLATFORM + LIVE-READY ARCHITECTURE

**Objective:** Next.js dashboard on Vercel, full system control via browser, live-ready adapter layer.

**Blueprint sections covered:** S10.3 (Frontend), S24 (Web Platform), S22 (Live Execution), S5 (Brand)

**Milestone:** Dashboard functional on Vercel, paper bot controllable from browser.

---

## Subphase 5.1 — Next.js Project Setup [COMPLETE]

**Goal:** Frontend skeleton with auth and API integration.

**Dependencies:** Subphase 4.1

### Tasks

- [x] **T-5.1.01** — Initialize Next.js project in `frontend/` with App Router, TypeScript, Tailwind CSS.

- [x] **T-5.1.02** — Configure project: `.env.local.example`, Tailwind theme with brand colors, Inter font.

- [x] **T-5.1.03** — Create `frontend/src/lib/api.ts` — typed API client with `credentials: "include"` for cookie-based auth.

- [x] **T-5.1.04** — Create `frontend/src/lib/auth.tsx` — AuthProvider context with HTTP-only cookie auth flow.

- [x] **T-5.1.05** — Create `frontend/src/app/login/page.tsx` — login form with error handling and loading state.

- [x] **T-5.1.06** — Create `frontend/src/app/(dashboard)/layout.tsx` — sidebar navigation with 8 nav items and logout.

- [x] **T-5.1.07** — Configure `frontend/vercel.json` and `frontend/.gitignore`.

- [x] **T-5.1.08** — Create `frontend/src/app/(dashboard)/page.tsx` — dashboard placeholder.

### Verification Gate

```bash
cd frontend
npm run build  # Must succeed
npm run dev    # Start dev server
# Visit http://localhost:3000 → see login page
# Login with credentials → see dashboard layout with sidebar
```
Build succeeds. Login works. Dashboard layout renders with navigation.

---

## Subphase 5.2 — Dashboard and Charts [COMPLETE]

**Goal:** Main dashboard with KPIs, candlestick charts with Ichimoku overlay.

**Dependencies:** Subphase 5.1

### Tasks

- [x] **T-5.2.01** — Install charting library: `klinecharts` v10 (KLineChart) for candlestick charts.

- [x] **T-5.2.02** — Create `frontend/src/components/charts/core/TradingChart.tsx` with KLineChart using DataLoader pattern, Ichimoku overlay via `registerIndicator()`, and FIBOKEI brand colours.

- [x] **T-5.2.03** — Create full KPI dashboard at `frontend/src/app/(dashboard)/page.tsx` with StatCards for balance, equity, daily/weekly PnL, active bots, total trades.

- [x] **T-5.2.04** — Create `frontend/src/app/(dashboard)/charts/page.tsx` with instrument/timeframe selectors and TradingChart component.

- [x] **T-5.2.05** — Create shared analytics components: `EquityCurve.tsx`, `MiniSummary.tsx` using Plotly.js with dynamic imports (SSR disabled).

### Verification Gate

```bash
cd frontend && npm run build
# Visit dashboard → summary cards show data from API
# Visit charts → candlestick chart renders with Ichimoku cloud overlay
```
Dashboard displays real data. Charts render candlesticks with overlays.

---

## Subphase 5.3 — Backtest and Research UI [COMPLETE]

**Goal:** Run backtests and view research rankings from the browser.

**Dependencies:** Subphases 5.2, 4.2, 4.3

### Tasks

- [x] **T-5.3.01** — Create `frontend/src/app/(dashboard)/backtests/page.tsx` with run form and results table.

- [x] **T-5.3.02** — Create `frontend/src/app/(dashboard)/backtests/[id]/page.tsx` with metrics grid, EquityCurve, and DrawdownChart.

- [x] **T-5.3.03** — Create `frontend/src/app/(dashboard)/research/page.tsx` with heatmap and rankings table.

- [x] **T-5.3.04** — Install Plotly.js for equity curves, drawdown charts, heatmap, and distribution charts. Create `DrawdownChart.tsx`, `Heatmap.tsx`, `Distribution.tsx`.

### Verification Gate

```bash
cd frontend && npm run build
# Run a backtest from UI → results display with charts and metrics
# Run research → leaderboard and heatmap render with real data
```
Backtest form works end-to-end. Research heatmap visualizes strategy-instrument performance.

---

## Subphase 5.4 — Paper Trading and Trade History UI [COMPLETE]

**Goal:** Control paper bots and inspect trade history from browser.

**Dependencies:** Subphases 5.2, 4.5

### Tasks

- [x] **T-5.4.01** — Create `frontend/src/app/(dashboard)/bots/page.tsx` with account summary, "Add Bot" form, bot list with state badges and pause/stop controls.

- [x] **T-5.4.02** — Create `frontend/src/app/(dashboard)/trades/page.tsx` with trade history table, filters, and sorting.

- [x] **T-5.4.03** — Create `frontend/src/app/(dashboard)/trades/[id]/page.tsx` with individual trade detail view.

### Verification Gate

```bash
cd frontend && npm run build
# Start a paper bot from UI → appears in active bots list
# View trade history → filters and sorting work
```
Bot controls work. Trade history displays correctly.

---

## Subphase 5.5 — Settings, System Pages, and Deployment [COMPLETE]

**Goal:** Settings pages, system diagnostics, production deployment.

**Dependencies:** Subphases 5.1–5.4

### Tasks

- [x] **T-5.5.01** — Create `frontend/src/app/(dashboard)/settings/page.tsx` with user info, risk defaults, and feature flags display.

- [x] **T-5.5.02** — Create `frontend/src/app/(dashboard)/system/page.tsx` with health status indicator and engine status grid.

- [x] **T-5.5.03** — Deploy frontend to Vercel: connect GitHub repo, set root directory to `frontend/`, configure environment variables.

- [x] **T-5.5.04** — Create loading/splash screen at `frontend/src/app/loading.tsx`.

### Verification Gate

```bash
cd frontend && npm run build
# Deploy to Vercel → site accessible at production URL
# Settings page → displays current config
# System page → shows engine status
```
Frontend deployed to Vercel and fully functional.

---

## Subphase 5.6 — Live-Ready Execution Architecture [COMPLETE]

**Goal:** Abstract execution layer so paper and future live trading use the same interface.

**Dependencies:** Subphase 4.4

**Blueprint sections:** S22, S29.2

### Tasks

- [x] **T-5.6.01** — Create `backend/src/fibokei/execution/adapter.py` with `ExecutionAdapter` abstract base class.

- [x] **T-5.6.02** — Create `backend/src/fibokei/execution/paper_adapter.py` with `PaperExecutionAdapter(ExecutionAdapter)`.

- [x] **T-5.6.03** — Create `backend/src/fibokei/execution/ig_adapter.py` with `IGExecutionAdapter(ExecutionAdapter)` stub (all methods raise `NotImplementedError`).

- [x] **T-5.6.04** — Create `backend/src/fibokei/core/feature_flags.py` with `FeatureFlags` and `get_execution_adapter()`.

- [x] **T-5.6.05** — Create `backend/tests/test_execution_adapter.py` with adapter and feature flag tests.

### Verification Gate

```bash
cd backend
pytest tests/test_execution_adapter.py -v
python -c "
from fibokei.core.feature_flags import FeatureFlags, get_execution_adapter
flags = FeatureFlags()
print(f'Live enabled: {flags.FIBOKEI_LIVE_EXECUTION_ENABLED}')
adapter = get_execution_adapter()
print(f'Adapter: {type(adapter).__name__}')
# Should print: Live enabled: False, Adapter: PaperExecutionAdapter
"
```
All tests pass. Feature flags enforce paper mode. Execution adapter abstraction works.

---

# CROSS-CUTTING CONCERNS

These tasks should be addressed throughout the build, not as a separate phase.

---

## Testing Standards

**Apply throughout all phases:**
- [x] Every module has unit tests
- [x] Every strategy has integration tests on sample data
- [x] Backtester determinism test runs on every strategy addition
- [x] API endpoints have integration tests
- [x] Test fixtures use deterministic seed-based sample data

## Documentation Standards

**Apply throughout all phases:**
- [x] Every new module has a docstring explaining its purpose
- [x] Strategy files include the blueprint section reference (e.g., "Implements BOT-01 per blueprint S13.1")
- [x] Architecture docs created: `architecture.md`, `frontend_architecture.md`, `charting_spec.md`, `api_contracts.md`, `auth_spec.md`

---

## V1 Summary

**Total tasks: 108** | **Completed: 108** | **Remaining: 0**

All V1 tasks complete. Platform deployed: frontend on Vercel (`fiboki.uk`), backend on Railway (`api.fiboki.uk`). 310 tests passing.

---

# PHASE 6: POLISH & PRODUCTION READINESS (POST-V1)

**Objective:** Fix broken functionality, add market data pipeline, deploy backend, improve UX.

**Status:** COMPLETE (24/24)

---

## Subphase 6.1 — Critical Fixes ✅

**Goal:** Fix broken pages and symbol mismatches.

### Tasks

- [x] **T-6.1.01** — Fixed Charts symbol mismatch: added `instrument.replace("_", "").replace("/", "").upper()` in market-data API route.

- [x] **T-6.1.02** — Market-data endpoint already existed from Phase 5. Verified working after symbol fix.

- [x] **T-6.1.03** — Ichimoku data already included in `MarketDataResponse` from Phase 5. No separate endpoint needed.

- [x] **T-6.1.04** — Converted Backtests form from free text to `<select>` dropdowns populated from `api.strategies()` and `api.instruments()`. Button properly enables when selections are made.

- [x] **T-6.1.05** — Converted Paper Bots form to same dropdown pattern. Button properly enables when selections are made.

### Verification Gate

Charts page loads candlestick data. Backtest and bot creation forms are functional with dropdown selectors.

---

## Subphase 6.2 — Backend Deployment ✅

**Goal:** Deploy FastAPI backend to a cloud platform.

### Tasks

- [x] **T-6.2.01** — Created `Dockerfile`, `render.yaml` (Render Blueprint), and `Procfile` for deployment. Added `psycopg2-binary` to dependencies. Fixed PostgreSQL compatibility in `app.py` (conditional `check_same_thread` and `StaticPool` for SQLite only).

- [x] **T-6.2.02** — Deployed FastAPI backend to Railway. Set environment variables: `FIBOKEI_JWT_SECRET`, `FIBOKEI_USER_JOE_PASSWORD`, `FIBOKEI_USER_TOM_PASSWORD`, `FIBOKEI_CORS_ORIGINS`. Database URL auto-injected by Railway PostgreSQL addon (app accepts both `FIBOKEI_DATABASE_URL` and `DATABASE_URL`). Health check passes, PostgreSQL schema created on startup. Resolved Dockerfile PORT expansion, Railpack/Nixpacks builder detection, and Docker layer caching issues.

- [x] **T-6.2.03** — Configured custom domain `api.fiboki.uk` on Railway (CNAME + TXT verification). Updated Vercel frontend env var `NEXT_PUBLIC_API_URL` to `https://api.fiboki.uk`. Frontend redeployed.

- [x] **T-6.2.04** — End-to-end production verification: login succeeds from `fiboki.uk` with cross-origin cookies, CORS configured correctly, health check returns `{"status":"ok","version":"1.0.0"}`. Market data endpoints return 404 as expected (canonical data not on server yet — future phase). Remaining smoke tests (backtests, research, bots, trades) depend on server-side data availability.

### Verification Gate

Full stack deployed on Railway + Vercel. Login from `fiboki.uk` hits `api.fiboki.uk` backend. Cross-origin cookie auth works. No CORS errors. Health check passes. Data-dependent smoke tests (charts, backtests, research) deferred until server-side data is available.

---

## Subphase 6.3 — UX Improvements ✅

**Goal:** Improve visual quality and interactivity across all pages.

### Tasks

- [x] **T-6.3.01** — Dashboard: added Quick Actions section (Run Backtest, Add Paper Bot, Research Matrix, View Charts) and System Overview panel (Engine status, Strategies loaded, Mode). Added 4th stat card (Weekly PnL). Added Active Bots and Total Trades cards.

- [x] **T-6.3.02** — Login: added SVG logo icon (green rounded rect with bar chart) above the title. Added "Trading Research Platform" subtitle.

- [x] **T-6.3.03** — Trade History: added strategy and direction filter dropdowns. Strategies populated from `api.strategies()`. Shows filtered count.

- [x] **T-6.3.04** — Settings: kept read-only for V1 (editable settings deferred to V2 — requires new API endpoint).

- [x] **T-6.3.05** — All pages: upgraded border colors from `border-gray-200` to `border-gray-300` for better contrast and visual hierarchy.

- [x] **T-6.3.06** — Backtests detail page: Plotly charts already functional from Phase 5.

### Verification Gate

All pages feel complete and interactive. No dead/disabled buttons without explanation.

---

## Subphase 6.4 — Real Market Data Pipeline ✅

**Goal:** Ingest real OHLCV data instead of relying on fixture CSVs.

### Tasks

- [x] **T-6.4.01** — Created `backend/src/fibokei/data/ingestion.py` with yfinance adapter. Maps all registered instruments to Yahoo Finance tickers (full instrument universe). Supports M1–H4 timeframes.

- [x] **T-6.4.02** — Added CLI command `fibokei refresh-data` with `--symbols`, `--timeframe`, and `--data-dir` options. Added API endpoint `POST /api/v1/market-data/refresh` for triggered refresh.

- [x] **T-6.4.03** — Data saved as CSV files in `data/fixtures/` following existing naming convention (`sample_{symbol}_{timeframe}.csv`). Database storage deferred to V2.

- [x] **T-6.4.04** — Charts page already loads from CSV fixtures via the market-data endpoint. Running `fibokei refresh-data` replaces fixture data with real market data.

### Verification Gate

Charts display real market data after running `fibokei refresh-data`. API refresh endpoint works.

---

## Subphase 6.5 — Canonical Data Expansion ✅

**Goal:** Expand HistData coverage from 16 to 60 instruments with proper symbol mapping audit.

### Tasks

- [x] **T-6.5.01** — Audited HistData symbol mapping. Previous map had incorrect tickers for indices (e.g. `SPX500USD` should be `SPXUSD`, `DE30EUR` should be `GRXEUR`, `JP225USD` should be `JPXJPY`). Corrected all mappings.

- [x] **T-6.5.02** — Discovered previously-unknown HistData coverage: US100 (`NSXUSD`), UK100 (`UKXGBP`), CAC40 (`FRXEUR`), AU200 (`AUXAUD`), HK50 (`HKXHKD`), DXY (`UDXUSD`), plus 17 G10 FX crosses, 4 Scandinavian pairs, 14 EM pairs, and ZARJPY.

- [x] **T-6.5.03** — Downloaded and ingested all 44 new instruments from HistData (2023–2024 M1 data), deriving M1/M5/M15/M30/H1/H4 canonical timeframes for each. Zero failures.

- [x] **T-6.5.04** — Updated centralised symbol map (`symbol_map.py`) with all 60 HistData instruments. Updated Yahoo map with CAC40 and DXY entries. Removed unmapped symbols (US30, US100, UK100 under old incorrect tickers) from HistData map.

- [x] **T-6.5.05** — Verified all new data loads correctly through the data registry (`load_canonical()`). All instruments return valid DataFrames with correct date ranges (2023–2024).

### Coverage Summary

| Category | Count | Instruments |
|----------|-------|-------------|
| Forex Majors | 7 | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, NZDUSD |
| Forex G10 Crosses | 22 | EURJPY, GBPJPY, EURGBP, AUDJPY, EURAUD, AUDCAD, AUDCHF, AUDNZD, CADCHF, CADJPY, CHFJPY, EURCAD, EURCHF, EURNZD, GBPAUD, GBPCAD, GBPCHF, GBPNZD, NZDCAD, NZDCHF, NZDJPY, SGDJPY |
| Forex Scandinavian | 4 | USDNOK, USDSEK, EURNOK, EURSEK |
| Forex EM | 14 | USDSGD, USDHKD, USDTRY, USDMXN, USDZAR, USDPLN, USDCZK, USDHUF, ZARJPY, EURTRY, EURPLN, EURCZK, EURHUF, EURDKK |
| Metals | 2 | XAUUSD, XAGUSD |
| Energy | 2 | BCOUSD, WTIUSD |
| Indices | 9 | US500, US100, UK100, DE40, JP225, CAC40, AU200, HK50, DXY |
| **Total** | **60** | **× 6 timeframes = 360 canonical datasets** |

### Not Available on HistData (Alternate Provider Required)

| Symbol | Recommended Provider |
|--------|---------------------|
| US30 (DJIA) | Dukascopy (`USA30IDXUSD`) or Yahoo (`^DJI`) |
| BTCUSD | Dukascopy or Yahoo |
| ETHUSD | Dukascopy or Yahoo |

### Verification Gate

`ls data/canonical/histdata/ | wc -l` returns 60. All instruments load via `load_canonical()` with correct 2023–2024 date ranges.

---

## Phase 6 Summary

**Total tasks: 24** | **Completed: 24** | **Remaining: 0**

---

# FUTURE PHASES (POST PHASE 7)

The following phases extend Fiboki from a deployed research platform into a broker-connected trading system. Each phase builds on the previous one. Phases are sequenced so that research improvements come first, then operational trading, then broker integration, then live readiness.

---

# PHASE 7: DATA UNIVERSE CONSOLIDATION

**Objective:** Make the 60-instrument HistData universe the default research universe across the entire platform — instrument registry, API, frontend, and docs.

**Dependencies:** Phase 6.5 (Canonical Data Expansion — complete)

**Current state:** COMPLETE. 67-instrument registry with 9 asset classes, API supports `has_canonical_data` field and `?asset_class=` filtering, frontend uses grouped `<optgroup>` selectors on backtests/bots/charts pages, `list-data` CLI reports 360 canonical datasets, docs updated.

---

## Subphase 7.1 — Asset Class Taxonomy and Instrument Registry

**Goal:** Expand the instrument registry and asset class taxonomy to reflect the confirmed 60-instrument HistData universe.

### Tasks

- [x] **T-7.1.01** — Add new `AssetClass` values to `backend/src/fibokei/core/models.py`:
  - `FOREX_G10_CROSS` — for the 17 new G10 cross pairs (AUDCAD, AUDCHF, AUDNZD, CADCHF, CADJPY, CHFJPY, EURCAD, EURCHF, EURNZD, GBPAUD, GBPCAD, GBPCHF, GBPNZD, NZDCAD, NZDCHF, NZDJPY, SGDJPY)
  - `FOREX_SCANDINAVIAN` — USDNOK, USDSEK, EURNOK, EURSEK
  - `FOREX_EM` — USDSGD, USDHKD, USDTRY, USDMXN, USDZAR, USDPLN, USDCZK, USDHUF, ZARJPY, EURTRY, EURPLN, EURCZK, EURHUF, EURDKK
  This is a deliberate taxonomy decision to preserve useful analytical grouping.

- [x] **T-7.1.02** — Expand `backend/src/fibokei/core/instruments.py` from 30 to 60+ instruments. Assign each new instrument to the correct `AssetClass`. Keep instruments without HistData backing (NATGAS, SOLUSD, LTCUSD, XRPUSD, US30, BTCUSD, ETHUSD) in the registry for future alternate-provider support, but mark them clearly as not part of the default HistData research universe.

- [x] **T-7.1.03** — Add a `has_canonical_data: bool` field or equivalent to distinguish instruments with confirmed HistData datasets from those requiring alternate providers.

### Verification Gate

```bash
cd backend
python -c "from fibokei.core.instruments import INSTRUMENTS; print(f'{len(INSTRUMENTS)} instruments')"
# Should print 60+ instruments
python -c "from fibokei.core.models import AssetClass; print([e.value for e in AssetClass])"
# Should include forex_g10_cross, forex_scandinavian, forex_em
```

---

## Subphase 7.2 — API, Frontend, and Documentation Updates

**Goal:** Ensure the expanded universe is visible and usable across the entire platform.

**Dependencies:** Subphase 7.1

### Tasks

- [x] **T-7.2.01** — Verify `/api/v1/instruments` returns all 60+ instruments with correct asset class labels. Update the instruments API route if needed to support filtering by asset class.

- [x] **T-7.2.02** — Verify frontend instrument dropdowns and filters show the expanded universe with correct category grouping. Update frontend components if needed.

- [x] **T-7.2.03** — Add canonical data verification/reporting CLI command: `fibokei list-data` showing available datasets per instrument, timeframes available, row counts, and date ranges.

- [x] **T-7.2.04** — Update docs that still reference the old 30-instrument launch universe: `docs/blueprint.md`, `docs/architecture.md`, `README.md`.

### Verification Gate

```bash
cd backend
python -m fibokei list-data  # Shows 360 canonical datasets
curl http://localhost:8000/api/v1/instruments/ | python -m json.tool | grep -c '"symbol"'
# Should return 60+
```
Frontend dropdowns show all instrument categories.

---

# PHASE 8: RESEARCH ENGINE V2

**Objective:** Production-grade research with statistical rigour, frontend batch controls, and configurable quality filters.

**Dependencies:** Phase 7 (Data Universe Consolidation)

---

## Subphase 8.1 — Advanced Research Methods

**Goal:** Add walk-forward analysis, out-of-sample testing, and robustness checks to the research engine.

### Tasks

- [x] **T-8.1.01** — Implement walk-forward analysis engine with configurable rolling train/test window sizes. Add to `backend/src/fibokei/research/`.

- [x] **T-8.1.02** — Add out-of-sample testing with configurable hold-out period support (e.g. train on 2023, test on 2024).

- [x] **T-8.1.03** — Add Monte Carlo robustness checks: shuffled returns, randomised entry timing, bootstrap confidence intervals.

- [x] **T-8.1.04** — Add parameter sensitivity analysis: vary strategy parameters ±N% and measure stability of results.

- [x] **T-8.1.05** — Add validation rerun on shortlisted combinations: re-test top-N results on a fresh data window or alternate time period.

### Verification Gate

```bash
cd backend
pytest tests/test_walk_forward.py tests/test_oos.py tests/test_monte_carlo.py -v
```
Walk-forward produces windowed results. OOS correctly splits train/test. Monte Carlo generates confidence intervals.

---

## Subphase 8.2 — Research UI Improvements

**Goal:** Frontend batch controls and configurable quality filters.

**Dependencies:** Subphase 8.1

### Tasks

- [x] **T-8.2.01** — Multi-combo batch selection from frontend: strategy × instrument × timeframe matrix picker.

- [x] **T-8.2.02** — Minimum trade-count filters configurable from UI or API config (currently hardcoded at 80).

- [x] **T-8.2.03** — Improved composite scoring with configurable weights exposed in the research UI.

- [x] **T-8.2.04** — Provider-aware validation hooks: flag results where Dukascopy cross-validation would add confidence.

- [x] **T-8.2.05** — Display walk-forward and OOS results in frontend alongside standard backtest metrics.

### Verification Gate

```bash
cd frontend && npm run build
```
Batch selection works across all 60 instruments. Minimum trade-count filter adjustable in UI. Walk-forward results display correctly.

---

# PHASE 9: ALWAYS-ON PAPER TRADING

**Objective:** Paper bots run continuously on Railway as a separate worker service, with state persistence and operational monitoring.

**Dependencies:** Phase 6.2 (Production Deployment — complete), Phase 7 (Data Universe Consolidation)

---

## Subphase 9.1 — Worker Service Architecture

**Goal:** Separate long-running bot orchestration from the API service.

### Tasks

- [x] **T-9.1.01** — Worker vs API separation: `backend/src/fibokei/worker.py` runs as separate process, reads bot configs from DB, evaluates closed candles via `fetch_ohlcv`, writes trade records. Supports `--dry-run`, `--once`, `--poll-interval`.

- [x] **T-9.1.02** — Bot state persistence: `PaperBotModel`, `PaperTradeModel`, `PaperAccountModel` in DB. All state (strategy, instrument, timeframe, bars_seen, last_evaluated_bar, position, errors) survives restart.

- [x] **T-9.1.03** — Restart recovery: `PaperWorker.recover()` reconstructs active bots from DB. `last_evaluated_bar` prevents duplicate candle processing. Failed recovery marks bot as stopped with error.

- [x] **T-9.1.04** — CLI `paper-worker` and `paper-status` commands. Worker supports `--dry-run` for Railway readiness verification.

### Verification Gate

```bash
cd backend
python -m fibokei.worker --dry-run  # Worker starts, loads bots from DB, exits cleanly
```
Worker service runs independently of API. Bot survives worker restart with state intact.

---

## Subphase 9.2 — Operational Monitoring

**Goal:** Health monitoring, stale-data detection, and daily summaries.

**Dependencies:** Subphase 9.1

### Tasks

- [x] **T-9.2.01** — Stale-data detection: per-bot/instrument/timeframe freshness check via `STALE_THRESHOLDS` dict. Health endpoint flags stale bots.

- [x] **T-9.2.02** — `GET /api/v1/paper/health` returns total/active/stale bot counts with per-bot `seconds_since_eval` and `is_stale` flags.

- [x] **T-9.2.03** — Worker sends daily summary via `TelegramNotifier.send_daily_summary()` at 21:00 UTC. Trade events trigger real-time `send_trade_closed()` alerts.

- [x] **T-9.2.04** — Promotion gate: `POST /paper/bots` checks `get_best_research_score()` ≥ `FIBOKEI_PROMOTION_THRESHOLD` (default 0.55). Returns 422 with explanation if below.

### Verification Gate

```bash
cd backend && pytest tests/test_paper_persistence.py tests/test_api_paper.py -v
```
33 persistence tests + 7 API tests all pass. Stale-data detection, health endpoint, promotion gate, and worker recovery all verified.

---

# PHASE 10: IG DEMO INTEGRATION

**Objective:** Real broker execution on IG demo account with full lifecycle management, reconciliation, and safety controls.

**Dependencies:** Phase 9 (Always-On Paper Trading)

**Note:** The exact IG auth flow (API key + session token vs OAuth) must be confirmed against IG API documentation before implementation begins. Do not commit to a specific auth pattern until verified.

---

## Subphase 10.1 — IG Execution Adapter

**Goal:** Implement the real `IGExecutionAdapter` replacing the current stub.

### Tasks

- [x] **T-10.1.01** — IG REST API auth flow confirmed: API key + session token (CST + X-SECURITY-TOKEN). Demo API base: `demo-api.ig.com`. Production URL hard-blocked. Session TTL 5h with auto-refresh. Implemented in `ig_client.py`.

- [x] **T-10.1.02** — IG demo auth/session handling in `execution/ig_client.py`. `IGClient` class with `authenticate()`, `ensure_session()`, auto-refresh. `IGSession` dataclass tracks CST/security tokens with TTL validation. Credentials from env vars (FIBOKEI_IG_API_KEY, FIBOKEI_IG_USERNAME, FIBOKEI_IG_PASSWORD).

- [x] **T-10.1.03** — IG epic mapping centralised in `core/instruments.py`. 65 of 67 instruments have `ig_epic` populated (DXY and SOLUSD/XRPUSD excluded — no IG epics). Helper functions: `get_ig_epic()`, `get_symbol_by_epic()`, `get_ig_supported_instruments()`.

- [x] **T-10.1.04** — Order lifecycle in `execution/ig_adapter.py`: `place_order()` (market orders with stop/limit), `cancel_order()` (working orders), `modify_order()`. All translate Fiboki symbols to IG epics, handle deal confirmations, catch and log errors gracefully.

- [x] **T-10.1.05** — Position sync in `execution/ig_adapter.py`: `get_positions()` maps IG positions to Fiboki symbols, `get_account_info()` returns balance/equity/pnl, `close_position()` and `partial_close()` handle full/partial exits.

- [x] **T-10.1.06** — Reconciliation in `execution/reconciliation.py`: `reconcile_positions()` compares Fiboki-tracked positions vs broker state. Detects: missing_at_broker, missing_in_fiboki, direction_mismatch, size_mismatch. Returns `ReconciliationResult` with `is_clean` property.

### Verification Gate

```bash
cd backend && pytest tests/test_ig_adapter.py tests/test_ig_reconciliation.py -v
```
30 tests pass: epic mapping (8), session lifecycle (4), client safety (3), order placement (4), positions (3), account (2), close/partial (2), orders (3), reconciliation (7). All use mocked IG responses — no real API calls.

---

## Subphase 10.2 — Safety Controls and Frontend

**Goal:** Kill switch, audit logs, demo-only flags, and frontend controls.

**Dependencies:** Subphase 10.1

### Tasks

- [x] **T-10.2.01** — Kill switch implemented: `KillSwitchModel` (single-row DB table), `activate_kill_switch()` / `deactivate_kill_switch()` repository functions, API endpoints POST `/execution/kill-switch/activate` and `/deactivate`. Frontend kill switch toggle on System page with red/green visual states.

- [x] **T-10.2.02** — Execution audit logs: `ExecutionAuditModel` with timestamp, execution_mode, action, instrument, direction, size, deal_id, status, error_message, bot_id. `save_execution_audit()` and `get_execution_audit()` with mode/bot_id filtering. API endpoint GET `/execution/audit`.

- [x] **T-10.2.03** — Feature flags updated: `FeatureFlags.execution_mode` property returns "paper"/"ig_demo"/"ig_live". `get_execution_adapter()` routes to Paper or IG adapter based on `FIBOKEI_LIVE_EXECUTION_ENABLED`. IG adapter hard-blocks production URL. API endpoint GET `/execution/mode` exposes current mode + kill switch state.

- [x] **T-10.2.04** — Frontend controls: System page shows dynamic execution mode badge (Paper Trading / IG Demo), kill switch status with activate/deactivate buttons, execution mode in Engine Status panel. API client extended with `executionMode()`, `killSwitchStatus()`, `activateKillSwitch()`, `deactivateKillSwitch()`, `executionAudit()`.

- [x] **T-10.2.05** — Data boundary documented: HistData canonical datasets (60 instruments × 6 timeframes) serve backtesting and research. IG price feed serves live/demo execution pricing. `has_canonical_data` flag distinguishes the two. IG epic mapping covers 65 of 67 instruments; 2 without IG epics (DXY, SOLUSD) are research-only.

### Verification Gate

```bash
cd backend && pytest tests/test_ig_safety.py -v
```
16 tests pass: kill switch (5), audit logs (5), feature flags (3), API endpoints (4 — execution mode, kill switch activate/deactivate, audit log, system status with execution_mode).

---

# PHASE 11: LIVE READINESS

**Objective:** Define and meet measurable criteria for transitioning from paper to demo to live trading. This phase produces documentation, hardened risk controls, and enforceable promotion gates — not the live trading itself.

**Dependencies:** Phase 10 (IG Demo Integration)

---

## Subphase 11.1 — Risk Hardening and Operational Procedures [COMPLETE]

**Goal:** Strengthen risk controls and document operational procedures.

### Tasks

- [x] **T-11.1.01** — Create pre-live checklist document: all items that must be verified and signed off before any live trading begins.

- [x] **T-11.1.02** — Risk hardening: enforce max position size, daily loss limit, correlation limits, max concurrent positions. These must be configurable and monitored.

- [x] **T-11.1.03** — Monitoring and alerting requirements: error rates, latency, reconciliation failures, unexpected fills. Define thresholds and alert channels.

- [x] **T-11.1.04** — Operational recovery procedures: what to do if worker crashes, if IG session expires, if database is unavailable, if network partitions occur.

- [x] **T-11.1.05** — Environment separation: dev/staging/prod configs with clear boundaries. No accidental cross-environment execution.

### Verification Gate

Pre-live checklist exists and all items can be evaluated. Risk limits are enforced in code. Recovery procedures are documented and have been dry-run tested.

---

## Subphase 11.2 — Promotion Gates [COMPLETE]

**Goal:** Define measurable, enforceable criteria for promotion between trading modes.

**Dependencies:** Subphase 11.1

### Tasks

- [x] **T-11.2.01** — Define and implement **Paper → Demo** promotion gate:
  - Minimum 30-day paper runtime
  - Minimum 80 trades completed
  - No unresolved critical errors
  - Composite score above configurable threshold

- [x] **T-11.2.02** — Define and implement **Demo → Live** promotion gate:
  - Minimum 14-day demo runtime
  - Reconciliation accuracy >99.5%
  - Max tolerated slippage drift within defined tolerance
  - No unresolved critical alerts
  - Manual sign-off required (cannot be automated away)

### Verification Gate

```bash
cd backend && pytest tests/test_promotion_gates.py -v
```
Promotion gates reject bots that do not meet criteria. Manual sign-off step is enforced.

---

# PHASE 12: FRONTEND IMPROVEMENTS V2

**Objective:** Richer analytical and operational controls for the web platform.

**Dependencies:** Phase 10 (IG Demo Integration) for demo-related features. Phase 8 (Research V2) for research-related features.

---

### Tasks

- [x] **T-12.01** — Multi-run backtest comparison views: side-by-side metrics for 3+ backtests.

- [ ] **T-12.02** — Trade detail replay / inspection: step through trade lifecycle with chart context. **Deferred to Phase 15.3** — will be implemented as part of the KLineChart-on-detail-pages work.

- [x] **T-12.03** — Demo/live mode visibility indicators: clear visual state (paper / demo / live) across all operational pages.

- [x] **T-12.04** — Settings page for IG credentials and risk parameters: securely store and manage IG API keys and risk config.

- [x] **T-12.05** — Expanded instrument search/filter UX for 60+ instruments with category grouping.

### Verification Gate

```bash
cd frontend && npm run build
```
Comparison view works for 3+ backtests. Mode indicator visible on all operational pages. Instrument filter groups by asset class.

---

# PHASE 13: CI/CD AND OPERATIONS

**Objective:** Automated quality gates, deployment pipeline, and operational maturity.

**Dependencies:** Phase 6.2 (Production Deployment — complete)

---

### Tasks

- [x] **T-13.01** — GitHub Actions workflow: lint (`ruff`), test (`pytest`), build (`npm run build`) on every PR.

- [ ] **T-13.02** — Automated deployment on merge to main: Railway auto-deploy from GitHub.

- [x] **T-13.03** — Deployment smoke test job: automated health check + auth verification post-deploy. Runs after each deployment and alerts on failure.

- [x] **T-13.04** — Environment variable / config validation: fail-fast on startup if required env vars are missing. Clear error messages.

- [ ] **T-13.05** — Database backup strategy: scheduled PostgreSQL backups, documented restoration procedure.

- [x] **T-13.06** — Structured logging: JSON log output for production, human-readable for dev. Include request IDs, timing, and error context.

- [x] **T-13.07** — Error tracking: Sentry SDK integration with env-based DSN (FIBOKEI_SENTRY_DSN), configurable traces rate and environment.

### Verification Gate

PR triggers lint+test automatically. Merge triggers deploy. Smoke test runs post-deploy. Missing env var causes clear startup failure with actionable error message.

---

## Phase 14: Online Data Layer, Dual-Mode Charting & Drawing Tools

**Goal:** Make the full canonical dataset accessible in production, add dual-mode charting (historical + live IG), and interactive drawing tools with persistence.

**Design doc:** [docs/plans/2026-03-10-online-data-and-charting-design.md](plans/2026-03-10-online-data-and-charting-design.md)

**Dependencies:** Phase 10.5 (Production Data Access), Phase 10 (IG Demo Integration)

---

### Phase 14.1: Online Historical Data Foundation — COMPLETE

**Branch:** `slice1-online-historical-data` (9 commits, 467 tests passing)

**What was built:**

| Component | File(s) | Purpose |
|-----------|---------|---------|
| DataFrame LRU Cache | `data/cache.py` | Process-local OrderedDict cache, TTL 5min, max 50 entries. Avoids re-reading parquet on every request. |
| Data Manifest Generator | `data/manifest.py` | Scans canonical dir, produces `manifest.json` with bar counts, date ranges, checksums, file sizes. |
| CLI Manifest Command | `cli.py` | `python -m fibokei manifest` — generates manifest, prints provider summary. |
| Manifest API | `api/routes/data.py` | `GET /data/manifest` (lazy-cached), `POST /data/manifest/refresh` (regenerate). |
| Market Data Pagination | `api/routes/market_data.py` | `limit` (max 10k), `from_dt`, `to_dt` params. Vectorized `to_dict('records')` replaces `iterrows()`. |
| Response Metadata | `api/schemas/charts.py` | `total_bars`, `from_date`, `to_date`, `source` in MarketDataResponse. |
| Dynamic has_canonical_data | `api/routes/instruments.py` | Derived from manifest instead of static flag. |
| Data Source Observability | `api/routes/system.py` | `data_source` in system status: `"volume"`, `"starter"`, or `"fixtures"`. |
| Remove dead data_path | `api/routes/backtests.py` | Always uses `load_canonical()` search order. |
| Fallback Logging | `data/providers/registry.py` | Logs warnings when falling back from canonical → starter → fixtures. |

**What this enables:** Once a Railway volume is mounted at `/data` and populated with the 961MB canonical dataset (360 parquet files), production gets full historical charting, backtests, and research for all 60 instruments across 6 timeframes.

**Operator next step:** Mount Railway volume, upload `data/canonical/`, run `fibokei manifest`, verify via `GET /api/v1/data/manifest`.

---

### Phase 14.2: Drawing Tools — COMPLETE

**Commits:** `1a8e116`, `813fcae`, `411bc1d`, `12d3542` (merged to main)

**What was built:**

| Component | File(s) | Purpose |
|-----------|---------|---------|
| DrawingToolbar | `frontend/src/components/charts/panels/DrawingToolbar.tsx` | 6 tools: Pointer, Trendline, Horizontal Line, Ray, Fibonacci Retracement, Channel |
| Drawing CRUD API | `backend/src/fibokei/api/routes/drawings.py` | GET/POST/PUT/DELETE with per-user isolation |
| ChartDrawingModel | `backend/src/fibokei/db/models.py` | Persists drawings with points, styles, lock status, visibility |
| TradingChart integration | `frontend/src/components/charts/core/TradingChart.tsx` | Auto-load saved drawings on chart mount, persist on change (debounced) |
| API client | `frontend/src/lib/api.ts` | `listDrawings()`, `saveDrawing()`, `updateDrawing()`, `deleteDrawing()` |

---

### Phase 14.3: Live Chart Mode — COMPLETE

**Merged to main** (commit `bf0ab08`, merge `4660100`, 507 tests passing)

**What was built:**

| Component | File(s) | Purpose |
|-----------|---------|---------|
| Live Data Provider | `backend/src/fibokei/data/live_provider.py` | Fetches recent OHLCV via IG REST API with per-timeframe TTL cache |
| Market Data Mode Routing | `backend/src/fibokei/api/routes/market_data.py` | `?mode=historical|live` query param, shared `_df_to_response()` helper |
| Live Status Endpoint | `GET /market-data/live/status` | Reports whether IG live data is available |
| IG Price History | `backend/src/fibokei/execution/ig_client.py` | `get_prices()` method for IG REST price endpoint |
| Frontend Mode Toggle | `frontend/src/components/charts/panels/ChartToolbar.tsx` | Historical/Live toggle with disabled state and tooltip |
| Live Polling | `frontend/src/lib/hooks/use-market-data.ts` | SWR `refreshInterval: 5000` for live mode; `useLiveStatus()` hook |
| Response Contract | `frontend/src/types/contracts/chart.ts` | `mode: "historical" | "live"` field, `LiveStatusResponse` interface |
| Backend Tests | `backend/tests/test_live_chart.py` | 18 tests covering live provider, mode routing, live status endpoint |

**Operator next step:** Redeploy backend, set IG credentials to enable live mode.

---

### Phase 14.4: Full Production UX — PARTIAL

**What is done:**
- [x] Manifest-aware data availability UI integrated on backtests page (disables timeframes without data)
- [x] Manifest-aware data availability UI integrated on research page (filters instruments/timeframes by manifest)
- [x] System page shows canonical data count and data source status
- [x] `useManifest()` hook with `hasData()`, `availableTimeframes()`, `datasetInfo()` helpers

- [x] Research preset builder — CRUD API for saving/loading research configurations (strategy × instrument × timeframe selections)
- [x] Bulk data sync tooling — `fibokei data-sync` CLI command with parquet validation, optional target sync, manifest regeneration

---

# PHASE 15: WORKFLOW COMPLETION & ASYNC INFRASTRUCTURE

**Objective:** Connect the strong backend research/validation pipeline to usable frontend workflows. Add async job infrastructure so large operations don't block the API. Add KLineChart context to detail pages per charting spec.

**Dependencies:** Phase 14 (near-complete), Phase 8 (Research V2), Phase 9 (Paper Trading)

**Why this phase exists:** Fiboki's backend can research, validate, and paper-trade strategies — but the operator has no UI workflow to move combos from research → paper → demo. Backtests and research run synchronously and time out on large matrices. Trade and backtest detail pages lack the KLineChart context the charting spec requires.

**Current gap:** The research page shows ranked results but has no "Promote to Paper" action. The backtest detail page shows Plotly equity/drawdown charts but no candlestick chart with trade markers. Research runs block the API for minutes on large matrices.

---

## Subphase 15.1 — Async Job Engine & Job Status Centre [COMPLETE]

**Goal:** Background execution for backtests, research, and data operations. Frontend job status tracking.

**Dependencies:** None (foundational infrastructure)

### Tasks

- [x] **T-15.1.01** — Created `backend/src/fibokei/jobs/engine.py` — thread pool job engine (4 workers) with UUID tracking, progress callbacks, cancellation support, `JobState` enum, `JobInfo` dataclass, module-level singleton.

- [x] **T-15.1.02** — Jobs are tracked in-memory (not DB-persisted) via the `JobEngine` singleton. Created `backend/src/fibokei/api/schemas/jobs.py` with `JobResponse`, `JobSubmittedResponse`, `JobListResponse` Pydantic schemas.

- [x] **T-15.1.03** — Created `backend/src/fibokei/api/routes/jobs.py` with endpoints:
  - `GET /api/v1/jobs` — list jobs with optional state/type filtering + active_count
  - `GET /api/v1/jobs/{job_id}` — get job detail with progress and result
  - `POST /api/v1/jobs/{job_id}/cancel` — cancel a running/pending job

- [x] **T-15.1.04** — Wrapped `POST /backtests/run` with `?async=true` query param. Sync path preserved as default. Async path submits to job engine and returns `JobSubmittedResponse`.

- [x] **T-15.1.05** — Wrapped `POST /research/run` to always run as async job. Returns `JobSubmittedResponse` with `job_id` for polling. Progress callbacks at 5%, 70%, 90%.

- [x] **T-15.1.06** — Created `frontend/src/app/(dashboard)/jobs/page.tsx` — Job Status Centre with table, progress bars, cancel buttons, state badges, duration display. Auto-refreshes via SWR polling (3s).

- [x] **T-15.1.07** — Added Jobs to sidebar navigation (`ListTodo` icon) between Research and Paper Bots. Active job count badge polls via SWR (5s).

- [x] **T-15.1.08** — Research page updated to poll job completion and display results when done. (Toast notifications deferred — polling with inline status sufficient for V1.)

### Verification Gate

```bash
cd backend && pytest tests/test_jobs.py -v
# Start a research job via API → returns 202 with job_id
# Poll job status → shows progress 0–100%
# Job completes → GET /jobs/{id} shows result link
cd frontend && npx next build
# Jobs page renders with progress bars and status badges
```

---

## Subphase 15.2 — Research-to-Paper Promotion Flow [COMPLETE]

**Goal:** UI workflow to promote validated research results to paper bots. Includes promotion gate integration.

**Dependencies:** Subphase 15.1 (async jobs)

### Tasks

- [x] **T-15.2.01** — Added "Promote to Paper" button on research rankings table. Appears for combos with composite score ≥ 0.55 (PROMOTION_THRESHOLD). Scores above threshold colored green.

- [x] **T-15.2.02** — Promotion confirmation dialog: modal shows strategy/instrument/timeframe, composite score, threshold. "Create Paper Bot" calls `POST /paper/bots` with `source_type="research"` and `source_id=run_id`.

- [x] **T-15.2.03** — Score validation indicators: composite scores ≥ 0.55 rendered in green, below threshold in default color. (Full validation_status field deferred — existing Validate Top 10 button provides detailed pass/fail.)

- [x] **T-15.2.04** — Added `source_type` and `source_id` columns to `PaperBotModel`. Updated `CreateBotRequest`, `CreateBotResponse`, `BotStatusResponse` schemas. Default source_type is "manual".

- [x] **T-15.2.05** — Added "Create Paper Bot" button on backtest detail page header. Calls `POST /paper/bots` with `source_type="backtest"` and `source_id=backtest_id`. Backend promotion gate enforces research score threshold.

### Verification Gate

```bash
cd backend && pytest tests/test_api_paper.py -v  # 11 tests pass (4 new)
cd frontend && npx next build                     # Clean build
# Research page → "Promote" button on combos with score ≥ 0.55
# Click Promote → confirmation dialog → bot created with source provenance
# Backtest detail → "Create Paper Bot" button in header
```

---

## Subphase 15.3 — Trade & Backtest Chart Context

**Goal:** Add KLineChart with trade markers to trade detail and backtest detail pages per charting spec requirements.

**Dependencies:** Phase 14.1 (market data API), Phase 14.2 (drawing tools TradingChart)

**Note:** This absorbs deferred task T-12.02 (trade detail replay/inspection).

### Tasks

- [x] **T-15.3.01** — KLineChart on backtest detail page with trade markers (entry/exit arrows, dashed PnL lines). TradeMarkerChart component using klinecharts overlays.

- [x] **T-15.3.02** — KLineChart on trade detail page, centered on trade entry. Shows single-trade context with entry/exit markers via backtest timeframe lookup.

- [x] **T-15.3.03** — "Jump to Trade" functionality: clicking Jump button in trade list or clicking chart marker scrolls KLineChart to that trade's entry.

- [x] **T-15.3.04** — Paginated, sortable trade list table on backtest detail. Sort by entry_time/PnL/direction/exit_reason. Each row links to trade detail page.

### Verification Gate

```bash
cd frontend && npx next build
# Backtest detail → KLineChart shows candlesticks with trade markers overlaid
# Trade detail → KLineChart centered on trade with entry/exit arrows and SL/TP lines
# Click trade in backtest list → chart jumps to that trade
```

---

## Subphase 15.4 — Results Bookmarking & Research Templates

**Goal:** Save and recall research configurations and bookmark interesting results for later reference.

**Dependencies:** Subphase 15.1 (async jobs)

### Tasks

- [x] **T-15.4.01** — Create `ResearchPresetModel` in DB: name, strategy_ids (JSON array), instrument_ids (JSON array), timeframes (JSON array), scoring weights, created_at. CRUD API: `GET/POST/PUT/DELETE /api/v1/research/presets`.

- [x] **T-15.4.02** — Add preset save/load UI to the research page. "Save as Preset" button saves current selection. Preset dropdown loads saved configurations.

- [x] **T-15.4.03** — Create `BookmarkModel` in DB: user_id, entity_type (`research_result` | `backtest` | `trade`), entity_id, note, created_at. API: `POST/DELETE /api/v1/bookmarks`, `GET /api/v1/bookmarks`.

- [x] **T-15.4.04** — Add bookmark/star toggle to research results rows, backtest list rows, and trade list rows. Bookmarked items filter available on each page.

- [x] **T-15.4.05** — Remaining Phase 14.4 item: bulk data sync tooling — document Railway volume update workflow with CLI helper command `fibokei sync-data`.

### Verification Gate

```bash
cd backend && pytest tests/test_presets.py tests/test_bookmarks.py -v
cd frontend && npx next build
# Save research preset → appears in dropdown → load restores selections
# Bookmark a research result → appears in filtered view
```

---

# PHASE 16: OPERATOR CONSOLE & FLEET OPERATIONS

**Objective:** Give the operator visibility into what the bot fleet is doing, centralize alerts and notifications, and add portfolio-level risk/exposure monitoring.

**Dependencies:** Phase 15 (Workflow Completion), Phase 9 (Paper Trading), Phase 10 (IG Demo)

**Why this phase exists:** Before scaling to multiple bots, the operator needs a fleet-level view, not just individual bot pages. Alert fatigue is real — centralized notification management prevents missed signals. Exposure monitoring prevents accidentally over-concentrating in one instrument or direction.

**Current gap:** The Bots page shows individual bots but has no fleet-level metrics. Telegram alerts fire but are not visible in the UI. There is no exposure or portfolio risk view. Execution audit log exists in the API but has no frontend viewer.

---

## Subphase 16.1 — Bot Fleet Dashboard & Event Timeline

**Goal:** Fleet-level dashboard showing aggregate bot performance, and a per-bot event timeline for debugging.

**Dependencies:** Phase 9 (Paper Trading)

### Tasks

- [ ] **T-16.1.01** — Create fleet dashboard section on the Bots page (or a new `/fleet` sub-route). Fleet-level metrics: total bots (running/paused/stopped), aggregate PnL (daily/weekly/total), total open positions, total trades today, fleet health summary.

- [ ] **T-16.1.02** — Add per-bot expandable event timeline: show recent events (trade opened, trade closed, signal evaluated, error occurred, bot state change) with timestamps. Backend: `BotEventModel` or structured log query. API: `GET /api/v1/paper/bots/{bot_id}/events`.

- [ ] **T-16.1.03** — Add bot performance sparkline: inline mini equity curve per bot using Plotly `MiniSummary` component. Shows PnL trajectory at a glance.

- [ ] **T-16.1.04** — Add fleet PnL chart: aggregate equity curve across all running bots. Plotly line chart showing combined daily PnL.

- [ ] **T-16.1.05** — Add bot grouping by strategy family: group bots by strategy_id in the fleet view. Show per-strategy aggregate metrics.

### Verification Gate

```bash
cd backend && pytest tests/test_fleet.py -v
cd frontend && npx next build
# Fleet dashboard → shows aggregate metrics across all bots
# Per-bot timeline → shows recent events with timestamps
# Bot sparklines → mini equity curves render correctly
```

---

## Subphase 16.2 — Alert Centre & Notification Inbox

**Goal:** Centralized notification management in the frontend, integrating Telegram alerts with an in-app notification inbox.

**Dependencies:** Phase 4.5 (Telegram notifier), Phase 9 (Paper Trading)

### Tasks

- [ ] **T-16.2.01** — Create `AlertModel` in DB: type (trade_closed, risk_breach, bot_error, daily_summary, system_event), severity (info, warning, critical), title, message, metadata JSON, read status, created_at. Repository: `save_alert()`, `list_alerts()`, `mark_read()`, `mark_all_read()`.

- [ ] **T-16.2.02** — Hook alert creation into existing Telegram notifier: every `send_trade_closed()`, `send_risk_alert()`, `send_daily_summary()` call also creates a DB alert. Telegram remains the push channel; DB alerts are the pull channel.

- [ ] **T-16.2.03** — API endpoints: `GET /api/v1/alerts` (paginated, filterable by type/severity/read), `POST /api/v1/alerts/{id}/read`, `POST /api/v1/alerts/read-all`.

- [ ] **T-16.2.04** — Create `frontend/src/app/(dashboard)/alerts/page.tsx` — Alert Centre page. Shows alerts in reverse chronological order with severity badges, type icons, and read/unread styling. Filter by type and severity.

- [ ] **T-16.2.05** — Add unread alert count badge to sidebar navigation. Poll for new alerts via SWR (30s interval).

- [ ] **T-16.2.06** — Add alert preferences to Settings page: configure which alert types trigger Telegram vs in-app-only. Per-type toggle.

### Verification Gate

```bash
cd backend && pytest tests/test_alerts.py -v
cd frontend && npx next build
# Trade closes → alert appears in Alert Centre + Telegram
# Alert Centre → filters by type/severity, mark as read
# Sidebar → shows unread count badge
```

---

## Subphase 16.3 — Exposure Dashboard & Portfolio Risk View

**Goal:** Portfolio-level risk monitoring showing aggregate exposure by instrument, direction, and asset class.

**Dependencies:** Phase 9 (Paper Trading), Phase 11 (Risk Hardening)

### Tasks

- [ ] **T-16.3.01** — Create `GET /api/v1/paper/exposure` API endpoint returning:
  - Per-instrument exposure (total long lots, total short lots, net exposure)
  - Per-asset-class aggregate exposure
  - Per-direction aggregate (total long, total short)
  - Portfolio risk utilization (current risk % vs max portfolio risk 5%)

- [ ] **T-16.3.02** — Create exposure dashboard page (sub-route of `/bots` or standalone `/exposure`). Show:
  - Instrument exposure heatmap (Plotly) — colour-coded by net position size
  - Direction balance bar chart (long vs short)
  - Risk utilization gauge (current % of max portfolio risk)
  - Correlation warning: flag when >3 bots trade the same instrument

- [ ] **T-16.3.03** — Add real-time risk limit indicators: show when approaching daily loss limit (-4%), weekly loss limit (-8%), max simultaneous trades (8). Colour-coded (green/amber/red).

- [ ] **T-16.3.04** — Add execution audit log viewer to System page: table showing recent execution audit entries (from `ExecutionAuditModel`). Filterable by execution mode, bot, status.

### Verification Gate

```bash
cd backend && pytest tests/test_exposure.py -v
cd frontend && npx next build
# Exposure dashboard → shows instrument/direction/risk breakdown
# Risk gauges → update as positions change
# Execution audit viewer → shows audit entries with filters
```

---

## Subphase 16.4 — Slippage & Execution Quality Analytics

**Goal:** Measure and display execution quality metrics. Only meaningful once IG demo mode is active.

**Dependencies:** Phase 10 (IG Demo Integration), Subphase 16.1 (Fleet Dashboard)

**When this becomes relevant:** After IG demo mode is enabled and the first ~40 demo trades have been executed. Paper mode has zero slippage by design, so this subphase only adds value once real broker fills are happening.

### Tasks

- [ ] **T-16.4.01** — Extend `ExecutionAuditModel` with execution quality fields: `requested_price`, `filled_price`, `slippage_pips`, `fill_latency_ms`. Update `IGExecutionAdapter` to capture these on every order fill.

- [ ] **T-16.4.02** — Create `GET /api/v1/execution/quality` API endpoint returning:
  - Average slippage per instrument (pips)
  - Slippage distribution (histogram data)
  - Fill rate (filled / total orders)
  - Average fill latency (ms)
  - Cost-adjusted PnL (paper PnL minus actual slippage)
  - Slippage trend over time (rolling 7-day average)

- [ ] **T-16.4.03** — Add slippage analytics section to System page or a new `/execution` sub-route:
  - Per-instrument slippage bar chart
  - Slippage distribution histogram (Plotly)
  - Fill latency percentiles (p50, p95, p99)
  - Cost-adjusted vs raw PnL comparison
  - Time-series of rolling average slippage

- [ ] **T-16.4.04** — Add per-trade slippage display to trade detail page: show requested vs filled price and slippage pips alongside existing trade metrics.

- [ ] **T-16.4.05** — Add slippage summary to the Demo→Live promotion gate display: show average slippage relative to the 2.0 pip threshold. Clearly indicate pass/fail.

### Verification Gate

```bash
cd backend && pytest tests/test_execution_quality.py -v
cd frontend && npx next build
# Slippage analytics → shows per-instrument slippage, distribution, latency
# Trade detail → shows slippage for demo-executed trades
# Promotion gate → slippage metric visible in demo→live assessment
```

---

# PHASE 17: CHART WORKSTATION & ADVANCED ANALYSIS

**Objective:** Deepen the charting experience with reusable drawing templates, multi-chart layouts, trade replay, market session context, and portfolio-level scenario analysis.

**Dependencies:** Phase 15 (Workflow Completion), Phase 14 (Drawing Tools, Live Mode)

**Why this phase exists:** Fiboki's charting is functional (candlesticks, Ichimoku, drawings, live mode) but lacks the depth that makes a chart workstation productive for daily analysis. Trade replay, session context, and scenario sandbox are the difference between "I can see a chart" and "I can efficiently analyse and decide."

**Current gap:** Drawings are per-chart but not reusable. Only one chart at a time. No market session awareness (London/NY/Tokyo). No ability to replay a trade step-by-step. No portfolio-level "what-if" simulation.

---

## Subphase 17.1 — Drawing Library & Template System

**Goal:** Save, name, and re-apply drawing sets across charts. Share templates between instruments.

**Dependencies:** Phase 14.2 (Drawing Tools)

### Tasks

- [ ] **T-17.1.01** — Create `DrawingTemplateModel` in DB: name, description, drawings (JSON array of drawing definitions without instrument/timestamp binding), created_at. API: `GET/POST/PUT/DELETE /api/v1/charts/drawing-templates`.

- [ ] **T-17.1.02** — Add "Save as Template" action to drawing toolbar: saves current drawing set as a named template. "Load Template" dropdown applies a template's drawings to the current chart, rebinding to the current instrument's price range.

- [ ] **T-17.1.03** — Template preview: show a thumbnail preview of each template in the dropdown. Use a mini-canvas rendering of the drawing shapes.

### Verification Gate

```bash
cd backend && pytest tests/test_drawing_templates.py -v
cd frontend && npx next build
# Save drawings as template → appears in template list
# Load template on different instrument → drawings re-bound to current price range
```

---

## Subphase 17.2 — Multi-Chart Layout

**Goal:** Display 2–4 charts simultaneously for cross-instrument or cross-timeframe analysis.

**Dependencies:** Phase 14.2 (Drawing Tools), Subphase 15.3 (TradingChart reusability)

### Tasks

- [ ] **T-17.2.01** — Create `MultiChartLayout` component supporting 1x1, 1x2, 2x2 grid layouts. Each cell is an independent `TradingChart` instance with its own instrument/timeframe selectors.

- [ ] **T-17.2.02** — Add layout selector to the Charts page toolbar. Persist selected layout in localStorage.

- [ ] **T-17.2.03** — Add cross-chart synchronization option: when enabled, all charts share the same time axis (panning one pans all). Toggle in toolbar.

- [ ] **T-17.2.04** — Add saved chart layout persistence: save the current multi-chart configuration (which instrument/timeframe in each cell + layout type) to the DB. API: `GET/POST/DELETE /api/v1/charts/layouts`.

### Verification Gate

```bash
cd frontend && npx next build
# Charts page → 2x2 layout shows 4 independent charts
# Cross-chart sync → panning one chart pans all
# Save layout → reload page → same layout restored
```

---

## Subphase 17.3 — Advanced Chart Overlays & Market Session Context

**Goal:** Add market session awareness (London/NY/Tokyo) and advanced indicator overlays computed server-side.

**Dependencies:** Phase 14.1 (Market Data API)

**Market session context tags Fiboki should show:**

| Session | Hours (UTC) | Why It Matters |
|---------|-------------|----------------|
| Tokyo/Asian | 00:00–09:00 | Low volatility for majors, JPY pairs active. Range-bound setups |
| London/European | 07:00–16:00 | Highest FX volume. Breakout setups. EUR/GBP pairs peak |
| New York/American | 12:00–21:00 | USD pairs peak. Commodity correlation active |
| London-NY Overlap | 12:00–16:00 | Highest volatility window. Best for momentum strategies |
| Weekend Gap | Fri 21:00–Sun 22:00 | No trading. Gap risk on Sunday open |

Additional context tags:
- **High-impact news** (future: integrate economic calendar)
- **Session open/close markers** (vertical lines on chart)
- **Volume profile by session** (which session produced the most volume)

### Tasks

- [ ] **T-17.3.01** — Create `backend/src/fibokei/data/sessions.py` with market session definitions and `get_session_for_timestamp(ts: datetime) -> str` utility. Sessions: Asian, London, New York, London-NY Overlap, Off-Hours.

- [ ] **T-17.3.02** — Add `session` field to `MarketDataResponse` candles (optional): each candle tagged with its session. Frontend renders session background shading on the chart (light colour bands).

- [ ] **T-17.3.03** — Add session filter to chart toolbar: toggle session highlighting on/off. Show session legend.

- [ ] **T-17.3.04** — Add volume profile overlay: volume bars coloured by session. Shows which session contributed the most volume per bar.

### Verification Gate

```bash
cd backend && pytest tests/test_sessions.py -v
cd frontend && npx next build
# Chart → session shading visible (London blue, NY green, Asian grey)
# Toggle sessions → shading appears/disappears
```

---

## Subphase 17.4 — Trade Replay Mode

**Goal:** Step through a completed trade's lifecycle bar-by-bar on a KLineChart, watching the strategy's decision points unfold.

**Dependencies:** Subphase 15.3 (KLineChart on detail pages)

### Tasks

- [ ] **T-17.4.01** — Create `TradeReplay` component: wraps TradingChart with a playback controller (play/pause/step-forward/step-back/speed). Loads bars around the trade and animates candle-by-candle from entry to exit.

- [ ] **T-17.4.02** — Add strategy decision annotations: at each bar during replay, show the strategy's signal evaluation result (e.g., "Ichimoku: price above cloud, tenkan > kijun = bullish confirmation"). Requires backend endpoint: `GET /api/v1/backtests/{run_id}/trades/{trade_id}/signals` returning per-bar signal evaluations.

- [ ] **T-17.4.03** — Add replay entry point on trade detail page: "Replay Trade" button launches the replay view.

### Verification Gate

```bash
cd frontend && npx next build
# Trade detail → "Replay Trade" → candles animate from entry to exit
# Strategy annotations visible at each bar during replay
```

---

## Subphase 17.5 — Scenario / Paper Portfolio Sandbox

**Goal:** Simulate a portfolio of N bots on historical data to test combined performance before committing to live paper trading.

**Dependencies:** Phase 8 (Research V2), Subphase 16.3 (Exposure Dashboard)

### Tasks

- [ ] **T-17.5.01** — Create `backend/src/fibokei/research/scenario.py` with `ScenarioSimulator` class. Takes a list of (strategy, instrument, timeframe, risk_pct) tuples and runs them all on the same historical period with shared capital and portfolio-level risk controls (max portfolio risk, max per-instrument exposure, correlation limits).

- [ ] **T-17.5.02** — API endpoint: `POST /api/v1/research/scenario` (async job). Request: list of combos + date range + capital. Response: aggregate equity curve, per-bot PnL, portfolio metrics (combined Sharpe, max portfolio drawdown, trade overlap percentage).

- [ ] **T-17.5.03** — Create scenario builder UI: drag-and-drop or checkbox selection of combos from research results. Configure capital allocation. "Run Scenario" triggers async job.

- [ ] **T-17.5.04** — Scenario results page: combined equity curve, per-bot contribution breakdown, correlation matrix heatmap (Plotly), exposure timeline, trade overlap analysis.

### Verification Gate

```bash
cd backend && pytest tests/test_scenario.py -v
cd frontend && npx next build
# Select 5 combos → run scenario → combined equity curve shows portfolio performance
# Correlation matrix → highlights highly-correlated bot pairs
# Trade overlap → shows percentage of simultaneous positions
```

---

# PHASE 18: STRATEGY FAMILIES & FLEET SCALING

**Objective:** Support parameter variations for strategy families, fleet-aware risk controls, watchlists, and trade journaling. This phase enables scaling from ~5 bots to 30–40+ bots safely.

**Dependencies:** Phase 16 (Operator Console), Phase 17 (Scenario Sandbox)

**Why this phase exists:** Individual strategies have fixed parameters today. To scale the fleet, operators need to run multiple parameter variants of the same strategy (ensemble trading). This requires fleet-aware risk controls to prevent hidden correlation risk and over-concentration.

**Current gap:** Strategies have hardcoded parameters. Risk controls are per-bot, not fleet-aware. No watchlist or saved layout functionality for daily workflow. No trade journal for operator reflection.

**Important safety considerations:** Running 30+ bots with parameter variations creates hidden correlation risk — bots sharing the same strategy/instrument will have highly correlated positions. Fleet-level risk controls (max aggregate exposure per instrument, correlation rejection thresholds, position caps) are mandatory before enabling this capability. See question D in the design rationale for detailed analysis.

---

## Subphase 18.1 — Parameter Variation Engine

**Goal:** Generate and manage strategy parameter variants for ensemble trading.

**Dependencies:** Phase 8 (Sensitivity Analysis)

### Tasks

- [ ] **T-18.1.01** — Add `config_overrides: dict | None` support to the `Strategy` base class. Each strategy defines its tunable parameters with default values and valid ranges. `evaluate()` and `compute_exit()` read from config overrides when present.

- [ ] **T-18.1.02** — Create `backend/src/fibokei/strategies/variation.py` with `generate_variants(strategy_id, param_ranges, num_variants) -> list[StrategyVariant]`. Uses sensitivity analysis results to identify stable parameter regions.

- [ ] **T-18.1.03** — Add variant management API: `POST /api/v1/strategies/{id}/variants` (generate N variants), `GET /api/v1/strategies/{id}/variants` (list variants). Variants are virtual strategies that appear in research/backtest/paper workflows.

- [ ] **T-18.1.04** — Add variant deduplication: reject variants whose historical trade overlap exceeds 80% with an existing variant (prevents clone-bot sprawl).

### Verification Gate

```bash
cd backend && pytest tests/test_variations.py -v
# Generate 10 variants of bot01_sanyaku → each has different parameters
# Overlap check → rejects variants with >80% trade overlap
```

---

## Subphase 18.2 — Fleet-Aware Risk Controls

**Goal:** Portfolio-level risk controls that account for correlation between bots, aggregate exposure, and fleet-level limits.

**Dependencies:** Subphase 18.1 (Parameter Variations), Subphase 16.3 (Exposure Dashboard)

### Tasks

- [ ] **T-18.2.01** — Extend `RiskEngine` with fleet-level checks: max aggregate exposure per instrument (configurable, default 3x single-bot limit), max bots per instrument (default 5), max total open positions across fleet (default 20).

- [ ] **T-18.2.02** — Add correlation monitoring: compute pairwise trade correlation between all running bots. Alert when two bots have >85% trade overlap. Dashboard widget showing correlation matrix.

- [ ] **T-18.2.03** — Add automatic fleet culling: if a bot underperforms the fleet median by >2 standard deviations over its last 50 trades, auto-pause and alert the operator.

- [ ] **T-18.2.04** — Add fleet risk limits to Settings page: configurable max bots per instrument, max aggregate exposure, correlation threshold.

### Verification Gate

```bash
cd backend && pytest tests/test_fleet_risk.py -v
# 6th bot on same instrument → rejected (max 5 per instrument)
# Two bots with 90% overlap → alert generated
# Bot underperforming by >2σ → auto-paused
```

---

## Subphase 18.3 — Watchlists & Saved Layouts

**Goal:** Operator can create instrument watchlists and save/restore their workspace layout.

**Dependencies:** Subphase 17.2 (Multi-Chart Layout)

### Tasks

- [ ] **T-18.3.01** — Create `WatchlistModel` in DB: name, instrument_ids (JSON array), created_at. API: `GET/POST/PUT/DELETE /api/v1/watchlists`. Default watchlist: "Forex Majors" (7 instruments).

- [ ] **T-18.3.02** — Add watchlist selector to Charts page: dropdown to filter instrument selectors by watchlist. Add/remove instruments from watchlist inline.

- [ ] **T-18.3.03** — Add workspace save/restore: save entire workspace state (which pages are open, chart layouts, selected instruments/timeframes) to DB. "Save Workspace" / "Load Workspace" in Settings or top-bar.

### Verification Gate

```bash
cd backend && pytest tests/test_watchlists.py -v
cd frontend && npx next build
# Create watchlist → instruments filtered in chart selectors
# Save workspace → reload → same state restored
```

---

## Subphase 18.4 — Trade Journal & Annotations

**Goal:** Operator can annotate trades with notes, tags, and screenshots for reflection and learning.

**Dependencies:** Subphase 15.3 (Trade Chart Context)

### Tasks

- [ ] **T-18.4.01** — Create `TradeJournalModel` in DB: trade_id (FK), note (text), tags (JSON array), screenshot_url (optional), created_at. API: `GET/POST/PUT/DELETE /api/v1/trades/{trade_id}/journal`.

- [ ] **T-18.4.02** — Add journal panel to trade detail page: text area for notes, tag chips (e.g., "good entry", "held too long", "news event", "trend reversal"), and a chart screenshot capture button.

- [ ] **T-18.4.03** — Add journal summary view: new tab or section on the Trades page showing recent journal entries with trade context. Filter by tag.

- [ ] **T-18.4.04** — Add trade tagging to the trade list: inline tag display and filter-by-tag dropdown.

### Verification Gate

```bash
cd backend && pytest tests/test_journal.py -v
cd frontend && npx next build
# Trade detail → add journal note with tags → persists and displays
# Trades page → filter by journal tag → shows matching trades
```

---

## Information Architecture Reference

The following navigation structure is recommended as Phases 15–18 are implemented. This is a guide for frontend reorganization, not an immediate requirement.

**Current sidebar (8 items):** Dashboard, Charts, Backtests, Research, Paper Bots, Trade History, Settings, System

**Proposed sidebar (reorganized, ~10 items with sub-routes):**

| Top-Level | Sub-Routes | Phase |
|-----------|-----------|-------|
| Dashboard | — | Existing |
| Charts | Multi-chart, Replay | 14 (existing), 17.2, 17.4 |
| Research & Testing | Research, Backtests, Jobs, Scenarios | Existing + 15.1, 17.5 |
| Operations | Fleet, Bots, Alerts, Exposure | 16.1, 16.2, 16.3 |
| Trade History | Trades, Journal | Existing + 18.4 |
| Settings | General, Risk, Alerts, Workspaces | Existing + 16.2.06, 18.3 |
| System | Health, Execution, Audit | Existing + 16.3.04, 16.4 |

Sub-routes can be implemented as tabs within the parent page. The sidebar itself should not grow beyond ~10 items.

---

## Operator Workflow: Research → Paper → Demo → Live

This section documents the intended operator workflow that Phases 15–18 complete:

```
1. DISCOVER
   Research Matrix → batch run strategies × instruments × timeframes (async job)
   ↓ results ranked by composite score

2. VALIDATE
   Walk-Forward + OOS + Monte Carlo on top combos (async job)
   ↓ validated combos marked with pass/fail badges

3. PROMOTE TO PAPER
   "Promote to Paper" button on validated combos
   ↓ paper bot created with provenance tracking

4. OBSERVE (30+ days)
   Fleet Dashboard → aggregate PnL, exposure, alerts
   ↓ bot accumulates forward performance

5. SCENARIO TEST (optional)
   Scenario Sandbox → test portfolio of planned bots on historical data
   ↓ combined equity curve, correlation analysis

6. PROMOTE TO DEMO
   Paper→Demo promotion gate check (30 days, 80 trades, score ≥ 0.55)
   ↓ bot switches to IG demo execution

7. MEASURE EXECUTION QUALITY (14+ days)
   Slippage Analytics → per-instrument fill quality
   ↓ execution metrics accumulate

8. PROMOTE TO LIVE (future)
   Demo→Live gate (14 days, reconciliation >99.5%, slippage ≤2 pips, manual sign-off)
   ↓ requires manual operator approval — cannot be automated away
```

---

## Strategy Reference

Fiboki includes 12 pre-built strategies, all inheriting from the `Strategy` base class in `backend/src/fibokei/strategies/`:

| ID | Name | Family | Complexity | Key Indicators |
|----|------|--------|-----------|----------------|
| bot01 | PureSanyakuConfluence | Ichimoku | Basic | Full Ichimoku confluence (price, tenkan, kijun, cloud, chikou) |
| bot02 | KijunPullback | Ichimoku | Basic | Kijun-sen as dynamic S/R, pullback entries |
| bot03 | FlatSenkouBBounce | Ichimoku | Basic | Flat Senkou B as horizontal S/R level |
| bot04 | ChikouMomentum | Ichimoku | Basic | Chikou span momentum confirmation |
| bot05 | MTFASanyaku | Ichimoku | Advanced | Multi-timeframe analysis — higher TF trend, lower TF entry |
| bot06 | NWaveStructural | Fibonacci | Intermediate | N-wave Fibonacci structural patterns |
| bot07 | KumoTwistAnticipator | Ichimoku | Intermediate | Cloud twist anticipation for trend reversals |
| bot08 | KihonSuchiCycle | Ichimoku | Advanced | Ichimoku time cycle theory (basic numbers: 9, 17, 26, 33, 42) |
| bot09 | GoldenCloudConfluence | Hybrid | Advanced | Golden ratio confluence with Ichimoku cloud |
| bot10 | KijunFibContinuation | Hybrid | Intermediate | Kijun + Fibonacci retracement continuation |
| bot11 | SanyakuFibExtension | Hybrid | Advanced | Sanyaku confluence + Fibonacci extension targets |
| bot12 | KumoFibTimeZone | Hybrid | Advanced | Cloud boundaries + Fibonacci time zones |

**Adding a new strategy:**
1. Create `backend/src/fibokei/strategies/bot13_whatever.py` implementing `Strategy`
2. Register in `registry.py` — it immediately appears in the API and all frontend dropdowns
3. No frontend changes needed — dropdowns populate from `GET /strategies`

**Parameter variations (Phase 18):** Strategy parameters (Ichimoku periods, ATR multipliers, Fibonacci levels) can be varied to create ensemble families. The sensitivity analysis engine (Phase 8) identifies which parameters are stable and what ranges produce consistent results.
