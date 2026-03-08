 # Fiboki — Build Roadmap

Version: 1.3
Status: **V1 COMPLETE** — Phase 6 (Polish) in progress
Last Updated: 2026-03-07
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
| Phase 6.2: Production Deployment | IN PROGRESS | All pass | Railway (primary) + Render (fallback), Vercel frontend, cross-origin auth, deployment docs — awaiting first cloud deploy |
| Phase 6.3: UX Improvements | COMPLETE | All pass | Dashboard polish, SVG logo, trade filters, visual hierarchy |
| Phase 6.4: Real Market Data | COMPLETE | All pass | yfinance ingestion, CLI refresh-data, API refresh endpoint |

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

**Status:** IN PROGRESS (13/16 complete)

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

- [ ] **T-6.2.02** — Set environment variables on hosting: `FIBOKEI_JWT_SECRET`, `FIBOKEI_USER_JOE_PASSWORD`, `FIBOKEI_USER_TOM_PASSWORD`, `DATABASE_URL`. *(Requires cloud platform account setup)*

- [ ] **T-6.2.03** — Update Vercel frontend env var `NEXT_PUBLIC_API_URL` to point to deployed backend URL. *(Blocked on T-6.2.02)*

- [ ] **T-6.2.04** — Verify end-to-end: Vercel frontend talks to deployed backend, login works, API calls succeed. *(Blocked on T-6.2.03)*

### Verification Gate

Full stack deployed. Login from Vercel URL hits remote backend. No CORS errors.

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

## Phase 6 Summary

**Total tasks: 16** | **Completed: 13** | **Remaining: 3** (all in Subphase 6.2 — require cloud platform account)
