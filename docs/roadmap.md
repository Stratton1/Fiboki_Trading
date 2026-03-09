§ it i# Fiboki — Build Roadmap

Version: 1.4
Status: **V1 COMPLETE** — Phase 6 (Polish) in progress, Phases 7–13 planned
Last Updated: 2026-03-09
Reference: [blueprint.md](blueprint.md)

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
- **5 Phases** with **23 Subphases**
- Each subphase contains **Claude-executable tasks** — specific enough for one Claude Code session
- Each subphase ends with a **Verification Gate** — concrete tests that must pass before proceeding
- **Dependencies** are listed where ordering matters

**Task format:** Each task is written as an instruction Claude Code can execute directly. Tasks are numbered for reference (e.g., `T-1.1.03` = Phase 1, Subphase 1, Task 3).

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

- [x] **T-1.2.02** — Create `backend/src/fibokei/core/instruments.py` with the 30-instrument launch universe from blueprint S7.2. Define each as an `Instrument` instance with symbol, name, and asset_class. Provide `get_instrument(symbol: str) -> Instrument` and `get_instruments_by_class(asset_class: AssetClass) -> list[Instrument]` functions.

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

All V1 tasks complete. Platform deployed to Vercel. Backend runs locally. 304 tests passing.

---

# PHASE 6: POLISH & PRODUCTION READINESS (POST-V1)

**Objective:** Fix broken functionality, add market data pipeline, deploy backend, improve UX.

**Status:** IN PROGRESS (18/21 complete)

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

## Subphase 6.2 — Backend Deployment (Partially Complete)

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

- [x] **T-6.4.01** — Created `backend/src/fibokei/data/ingestion.py` with yfinance adapter. Maps all 30 FIBOKEI instruments to Yahoo Finance tickers (forex, commodities, indices, crypto). Supports M1–H4 timeframes.

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

**Total tasks: 21** | **Completed: 18** | **Remaining: 3** (all in Subphase 6.2 — require Railway account setup)

---

# FUTURE PHASES (POST PHASE 6)

The following phases extend Fiboki from a locally-functional research platform into a production-grade, broker-connected trading system. Each phase builds on the previous one. Phases are sequenced so that production stability comes first, then data/research improvements, then operational trading, then broker integration, then live readiness.

---

# PHASE 7: DATA UNIVERSE CONSOLIDATION

**Objective:** Make the 60-instrument HistData universe the default research universe across the entire platform — instrument registry, API, frontend, and docs.

**Dependencies:** Phase 6.5 (Canonical Data Expansion — complete)

**Current state:** HistData canonical datasets exist for 60 instruments × 6 timeframes = 360 datasets. However, the instrument registry (`instruments.py`) still defines only the original 30-instrument launch universe. The `AssetClass` enum lacks categories for the new Scandinavian, EM, and expanded G10 cross pairs.

---

## Subphase 7.1 — Asset Class Taxonomy and Instrument Registry

**Goal:** Expand the instrument registry and asset class taxonomy to reflect the confirmed 60-instrument HistData universe.

### Tasks

- [ ] **T-7.1.01** — Add new `AssetClass` values to `backend/src/fibokei/core/models.py`:
  - `FOREX_G10_CROSS` — for the 17 new G10 cross pairs (AUDCAD, AUDCHF, AUDNZD, CADCHF, CADJPY, CHFJPY, EURCAD, EURCHF, EURNZD, GBPAUD, GBPCAD, GBPCHF, GBPNZD, NZDCAD, NZDCHF, NZDJPY, SGDJPY)
  - `FOREX_SCANDINAVIAN` — USDNOK, USDSEK, EURNOK, EURSEK
  - `FOREX_EM` — USDSGD, USDHKD, USDTRY, USDMXN, USDZAR, USDPLN, USDCZK, USDHUF, ZARJPY, EURTRY, EURPLN, EURCZK, EURHUF, EURDKK
  This is a deliberate taxonomy decision to preserve useful analytical grouping.

- [ ] **T-7.1.02** — Expand `backend/src/fibokei/core/instruments.py` from 30 to 60+ instruments. Assign each new instrument to the correct `AssetClass`. Keep instruments without HistData backing (NATGAS, SOLUSD, LTCUSD, XRPUSD, US30, BTCUSD, ETHUSD) in the registry for future alternate-provider support, but mark them clearly as not part of the default HistData research universe.

- [ ] **T-7.1.03** — Add a `has_canonical_data: bool` field or equivalent to distinguish instruments with confirmed HistData datasets from those requiring alternate providers.

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

- [ ] **T-7.2.01** — Verify `/api/v1/instruments` returns all 60+ instruments with correct asset class labels. Update the instruments API route if needed to support filtering by asset class.

- [ ] **T-7.2.02** — Verify frontend instrument dropdowns and filters show the expanded universe with correct category grouping. Update frontend components if needed.

- [ ] **T-7.2.03** — Add canonical data verification/reporting CLI command: `fibokei list-data` showing available datasets per instrument, timeframes available, row counts, and date ranges.

- [ ] **T-7.2.04** — Update docs that still reference the old 30-instrument launch universe: `docs/blueprint.md`, `docs/architecture.md`, `README.md`.

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

- [ ] **T-8.1.01** — Implement walk-forward analysis engine with configurable rolling train/test window sizes. Add to `backend/src/fibokei/research/`.

- [ ] **T-8.1.02** — Add out-of-sample testing with configurable hold-out period support (e.g. train on 2023, test on 2024).

- [ ] **T-8.1.03** — Add Monte Carlo robustness checks: shuffled returns, randomised entry timing, bootstrap confidence intervals.

- [ ] **T-8.1.04** — Add parameter sensitivity analysis: vary strategy parameters ±N% and measure stability of results.

- [ ] **T-8.1.05** — Add validation rerun on shortlisted combinations: re-test top-N results on a fresh data window or alternate time period.

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

- [ ] **T-8.2.01** — Multi-combo batch selection from frontend: strategy × instrument × timeframe matrix picker.

- [ ] **T-8.2.02** — Minimum trade-count filters configurable from UI or API config (currently hardcoded at 80).

- [ ] **T-8.2.03** — Improved composite scoring with configurable weights exposed in the research UI.

- [ ] **T-8.2.04** — Provider-aware validation hooks: flag results where Dukascopy cross-validation would add confidence.

- [ ] **T-8.2.05** — Display walk-forward and OOS results in frontend alongside standard backtest metrics.

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

- [ ] **T-9.1.01** — Design and implement worker vs API separation. Create `backend/src/fibokei/worker.py` entry point for the Railway worker service. Worker reads bot configs from database, runs signal evaluation loops on closed candles, writes trade records back.

- [ ] **T-9.1.02** — Implement bot state persistence across worker restart: all bot state stored in database, not in-memory. Worker reconstructs running bots from database on startup.

- [ ] **T-9.1.03** — Implement bot restart/recovery behaviour: on worker crash or restart, bots resume from last known state. No duplicate signals or missed candles.

- [ ] **T-9.1.04** — Add Railway worker service configuration alongside the API service.

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

- [ ] **T-9.2.01** — Stale-data detection: alert if data feed gaps exceed configurable threshold.

- [ ] **T-9.2.02** — Bot health monitoring endpoint: `GET /api/v1/paper/health` returns status of all running bots, last signal time, and data freshness.

- [ ] **T-9.2.03** — Daily summary Telegram alerts: aggregate paper trading performance, active bots, notable signals.

- [ ] **T-9.2.04** — Promotion gate: require minimum backtest composite score before allowing paper bot creation for a strategy/instrument combination.

### Verification Gate

```bash
cd backend && pytest tests/test_worker.py tests/test_stale_data.py -v
```
Stale-data alert fires on simulated gap. Daily summary sends via Telegram. Promotion gate rejects low-scoring combinations.

---

# PHASE 10: IG DEMO INTEGRATION

**Objective:** Real broker execution on IG demo account with full lifecycle management, reconciliation, and safety controls.

**Dependencies:** Phase 9 (Always-On Paper Trading)

**Note:** The exact IG auth flow (API key + session token vs OAuth) must be confirmed against IG API documentation before implementation begins. Do not commit to a specific auth pattern until verified.

---

## Subphase 10.1 — IG Execution Adapter

**Goal:** Implement the real `IGExecutionAdapter` replacing the current stub.

### Tasks

- [ ] **T-10.1.01** — Research and document the IG REST API auth flow, endpoints, and data contracts. Confirm API key + session token approach.

- [ ] **T-10.1.02** — Implement IG demo auth/session handling in `backend/src/fibokei/execution/ig_adapter.py`. Handle session refresh and expiry.

- [ ] **T-10.1.03** — Implement instrument-to-epic mapping: Fiboki symbol → IG epic code. Store mappings in a centralised config.

- [ ] **T-10.1.04** — Implement order placement, modification, and cancellation via IG REST API.

- [ ] **T-10.1.05** — Implement position sync and fill/order-status handling. Keep Fiboki state in sync with IG account state.

- [ ] **T-10.1.06** — Implement reconciliation: compare Fiboki internal state vs IG account state, flag and log discrepancies.

### Verification Gate

```bash
cd backend && pytest tests/test_ig_adapter.py -v
```
Adapter authenticates with IG demo. Can place and close a test trade. Reconciliation detects a simulated discrepancy.

---

## Subphase 10.2 — Safety Controls and Frontend

**Goal:** Kill switch, audit logs, demo-only flags, and frontend controls.

**Dependencies:** Subphase 10.1

### Tasks

- [ ] **T-10.2.01** — Implement kill switch / safe mode: emergency stop all IG activity within 5 seconds. Accessible via API endpoint and frontend button.

- [ ] **T-10.2.02** — Execution audit logs: every order action logged with timestamps, order details, and IG response.

- [ ] **T-10.2.03** — Demo-only feature flags: prevent accidental live execution. IG adapter checks feature flag before any order action.

- [ ] **T-10.2.04** — Frontend controls for demo bot operation: start/stop/pause demo bots, view execution log, trigger kill switch.

- [ ] **T-10.2.05** — Chart/feed strategy for demo mode: document the boundary between HistData (backtesting/research) and IG feed (live/demo execution pricing). Historical research data remains from HistData. Operational demo charting aligns with IG price feed where appropriate.

### Verification Gate

```bash
cd backend && pytest tests/test_ig_safety.py -v
```
Kill switch stops all activity within 5 seconds. Demo flag prevents execution when disabled. Audit log captures all order actions.

---

# PHASE 11: LIVE READINESS

**Objective:** Define and meet measurable criteria for transitioning from paper to demo to live trading. This phase produces documentation, hardened risk controls, and enforceable promotion gates — not the live trading itself.

**Dependencies:** Phase 10 (IG Demo Integration)

---

## Subphase 11.1 — Risk Hardening and Operational Procedures

**Goal:** Strengthen risk controls and document operational procedures.

### Tasks

- [ ] **T-11.1.01** — Create pre-live checklist document: all items that must be verified and signed off before any live trading begins.

- [ ] **T-11.1.02** — Risk hardening: enforce max position size, daily loss limit, correlation limits, max concurrent positions. These must be configurable and monitored.

- [ ] **T-11.1.03** — Monitoring and alerting requirements: error rates, latency, reconciliation failures, unexpected fills. Define thresholds and alert channels.

- [ ] **T-11.1.04** — Operational recovery procedures: what to do if worker crashes, if IG session expires, if database is unavailable, if network partitions occur.

- [ ] **T-11.1.05** — Environment separation: dev/staging/prod configs with clear boundaries. No accidental cross-environment execution.

### Verification Gate

Pre-live checklist exists and all items can be evaluated. Risk limits are enforced in code. Recovery procedures are documented and have been dry-run tested.

---

## Subphase 11.2 — Promotion Gates

**Goal:** Define measurable, enforceable criteria for promotion between trading modes.

**Dependencies:** Subphase 11.1

### Tasks

- [ ] **T-11.2.01** — Define and implement **Paper → Demo** promotion gate:
  - Minimum 30-day paper runtime
  - Minimum 80 trades completed
  - No unresolved critical errors
  - Composite score above configurable threshold

- [ ] **T-11.2.02** — Define and implement **Demo → Live** promotion gate:
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

- [ ] **T-12.01** — Multi-run backtest comparison views: side-by-side metrics for 3+ backtests.

- [ ] **T-12.02** — Trade detail replay / inspection: step through trade lifecycle with chart context.

- [ ] **T-12.03** — Demo/live mode visibility indicators: clear visual state (paper / demo / live) across all operational pages.

- [ ] **T-12.04** — Settings page for IG credentials and risk parameters: securely store and manage IG API keys and risk config.

- [ ] **T-12.05** — Expanded instrument search/filter UX for 60+ instruments with category grouping.

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

- [ ] **T-13.01** — GitHub Actions workflow: lint (`ruff`), test (`pytest`), build (`npm run build`) on every PR.

- [ ] **T-13.02** — Automated deployment on merge to main: Railway auto-deploy from GitHub.

- [ ] **T-13.03** — Deployment smoke test job: automated health check + auth verification post-deploy. Runs after each deployment and alerts on failure.

- [ ] **T-13.04** — Environment variable / config validation: fail-fast on startup if required env vars are missing. Clear error messages.

- [ ] **T-13.05** — Database backup strategy: scheduled PostgreSQL backups, documented restoration procedure.

- [ ] **T-13.06** — Structured logging: JSON log output for production, human-readable for dev. Include request IDs, timing, and error context.

- [ ] **T-13.07** — Error tracking: integrate Sentry or similar for production error monitoring and alerting.

### Verification Gate

PR triggers lint+test automatically. Merge triggers deploy. Smoke test runs post-deploy. Missing env var causes clear startup failure with actionable error message.
