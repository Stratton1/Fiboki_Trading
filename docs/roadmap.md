# FIBOKEI — BUILD ROADMAP

Version: 1.1
Status: Active
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
| Phase 5: Web Platform | NOT STARTED | — | Next.js dashboard |

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

**T-1.1.01** — Initialize git repository in `/Users/joseph/Projects/Fiboki_Trading`. Create `.gitignore` covering Python (`__pycache__/`, `*.pyc`, `.venv/`, `dist/`, `*.egg-info/`), Node (`node_modules/`, `.next/`, `.vercel/`), IDE (`.vscode/`, `.idea/`), secrets (`.env`, `.env.local`, `*.key`), data (`data/raw/`, `*.csv` in data dirs but not test fixtures), and OS files (`.DS_Store`).

**T-1.1.02** — Create the monorepo directory structure:
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

**T-1.1.03** — Create `backend/pyproject.toml` with:
- Project name: `fibokei`
- Python requirement: `>=3.11`
- Dependencies: `pandas>=2.0`, `numpy>=1.24`, `pydantic>=2.0`, `python-dateutil`
- Dev dependencies: `pytest>=7.0`, `pytest-cov`, `ruff`, `mypy`
- Package source: `src`
- Entry point: `fibokei = fibokei.cli:main`
- Pytest config: `testpaths = ["tests"]`
- Ruff config: line-length 100, target Python 3.11

**T-1.1.04** — Create `CLAUDE.md` at project root with:
- Project overview (FIBOKEI automated trading platform)
- Tech stack (Python 3.11+, FastAPI, Next.js + TypeScript)
- Key commands: `cd backend && pip install -e ".[dev]"`, `pytest`, `ruff check`, `python -m fibokei`
- Architecture rules from blueprint S9.3 (strategy ≠ broker, indicators centralized, risk not in strategies, frontend ≠ trading logic)
- Non-negotiables from blueprint S32
- File structure guide

**T-1.1.05** — Create `README.md` at project root with: project name, one-paragraph description, tech stack list, setup instructions placeholder, link to `docs/blueprint.md` and `docs/roadmap.md`.

**T-1.1.06** — Create `RULES.md` at project root summarizing the coding standards: closed-candle-only signals, UTC timestamps, no plaintext secrets, all strategies use common framework, deterministic backtest results, risk-controlled defaults.

**T-1.1.07** — Create initial commit with all scaffold files.

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

**T-1.2.01** — Create `backend/src/fibokei/core/models.py` with Pydantic v2 models:
- `Timeframe` enum: `M1, M2, M5, M15, M30, H1, H4`
- `AssetClass` enum: `FOREX_MAJOR, FOREX_CROSS, COMMODITY_METAL, COMMODITY_ENERGY, INDEX, CRYPTO`
- `Direction` enum: `LONG, SHORT`
- `Instrument` model: `symbol: str`, `name: str`, `asset_class: AssetClass`, `pip_value: float | None`, `ig_epic: str | None`
- `OHLCVBar` model: `timestamp: datetime`, `open: float`, `high: float`, `low: float`, `close: float`, `volume: float`
- `DatasetMeta` model: `instrument: str`, `timeframe: Timeframe`, `source_id: str`, `timezone: str = "UTC"`, `ingest_version: str`, `bar_count: int`, `start: datetime`, `end: datetime`, `status: str`

**T-1.2.02** — Create `backend/src/fibokei/core/instruments.py` with the 30-instrument launch universe from blueprint S7.2. Define each as an `Instrument` instance with symbol, name, and asset_class. Provide `get_instrument(symbol: str) -> Instrument` and `get_instruments_by_class(asset_class: AssetClass) -> list[Instrument]` functions.

**T-1.2.03** — Create `backend/src/fibokei/data/loader.py` with:
- `load_ohlcv_csv(path: str, instrument: str, timeframe: Timeframe) -> pd.DataFrame` — reads CSV, standardizes column names to `timestamp, open, high, low, close, volume`, parses timestamps to UTC datetime, sorts by timestamp ascending.
- Support for common CSV formats: with/without header, various date formats, comma/semicolon delimiters.
- Returns DataFrame with DatetimeIndex.

**T-1.2.04** — Create `backend/src/fibokei/data/validator.py` with `validate_ohlcv(df: pd.DataFrame) -> list[str]` that checks for:
- Missing/null values in OHLC columns
- `high < low` violations
- `open` or `close` outside `[low, high]` range
- Duplicate timestamps
- Out-of-order timestamps
- Negative prices
- Suspicious gaps (>3x median gap between consecutive bars)
- Returns list of warning strings (empty = valid)

**T-1.2.05** — Create a sample test fixture at `data/fixtures/sample_eurusd_h1.csv` with at least 500 bars of realistic EURUSD H1 OHLCV data. This can be synthetic but must have realistic price ranges (~1.05–1.15), realistic candle sizes (~10-80 pip range), and monotonically increasing hourly timestamps. Include a Python script at `scripts/generate_sample_data.py` that generates this fixture deterministically using a seeded random walk.

**T-1.2.06** — Create `backend/tests/test_data_models.py` with tests for:
- All enum values accessible
- Instrument creation and lookup
- OHLCVBar validation (valid and invalid cases)
- DatasetMeta creation

**T-1.2.07** — Create `backend/tests/test_data_loader.py` with tests for:
- Loading the sample fixture CSV
- Correct column names after normalization
- Correct dtype for timestamp index
- Row count matches expected
- Values are in plausible ranges

**T-1.2.08** — Create `backend/tests/test_data_validator.py` with tests for:
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

**T-1.3.01** — Create `backend/src/fibokei/indicators/base.py` with:
- `Indicator` abstract base class:
  - `name: str` property
  - `compute(df: pd.DataFrame) -> pd.DataFrame` abstract method — takes OHLCV DataFrame, returns same DataFrame with new indicator columns added
  - `required_columns: list[str]` property — columns needed in input (default: `["open", "high", "low", "close"]`)
  - `warmup_period: int` property — minimum bars needed before indicator values are valid

**T-1.3.02** — Create `backend/src/fibokei/indicators/ichimoku.py` implementing `IchimokuCloud(Indicator)` with:
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

**T-1.3.03** — Create `backend/src/fibokei/indicators/atr.py` implementing `ATR(Indicator)` with:
- Parameters: `period: int = 14`
- `compute()` adds column `atr`:
  - True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
  - ATR = exponential moving average of True Range over period
- `warmup_period`: `period`

**T-1.3.04** — Create `backend/src/fibokei/indicators/registry.py` with:
- `IndicatorRegistry` class with `register(indicator_class)` and `get(name: str) -> Indicator` methods
- Pre-register `IchimokuCloud` and `ATR`
- `list_available() -> list[str]` method

**T-1.3.05** — Create `backend/tests/test_ichimoku.py` with:
- Test with known 100-bar price series where Tenkan/Kijun values can be manually verified for at least 3 specific bars
- Test that warmup period produces NaN for early bars and valid values after
- Test with custom parameters (e.g., tenkan=7, kijun=22)
- Test column names are correct
- Test output DataFrame has same length as input

**T-1.3.06** — Create `backend/tests/test_atr.py` with:
- Test with known price series where ATR can be manually computed
- Test warmup period NaN handling
- Test with custom period

**T-1.3.07** — Create a CLI demo script at `backend/src/fibokei/cli.py` with a `main()` function that:
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

**T-2.1.01** — Create `backend/src/fibokei/core/signals.py` with Pydantic models:
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

**T-2.1.02** — Create `backend/src/fibokei/core/trades.py` with Pydantic models:
- `TradePlan` model with all fields from blueprint S12.6:
  - `entry_price: float`
  - `stop_loss: float`
  - `take_profit_targets: list[float]`
  - `trailing_stop_rule: str | None`
  - `break_even_rule: str | None`
  - `max_risk_amount: float`
  - `risk_pct: float`
  - `position_size: float`
  - `stale_after_bars: int | None`
  - `max_bars_in_trade: int | None`
  - `partial_close_pcts: list[float] | None`
  - `allowed_exit_reasons: list[str]`
- `ExitReason` enum from blueprint S12.7: `STOP_LOSS_HIT, TAKE_PROFIT_HIT, PARTIAL_TAKE_PROFIT, TRAILING_STOP_HIT, BREAK_EVEN_EXIT, OPPOSITE_SIGNAL_EXIT, INDICATOR_INVALIDATION_EXIT, TIME_STOP_EXIT, MANUAL_STOP, SYSTEM_SHUTDOWN_EXIT`
- `TradeResult` model:
  - `trade_id: str`
  - `strategy_id: str`
  - `instrument: str`
  - `timeframe: Timeframe`
  - `direction: Direction`
  - `entry_time: datetime`
  - `entry_price: float`
  - `exit_time: datetime`
  - `exit_price: float`
  - `exit_reason: ExitReason`
  - `pnl: float`
  - `pnl_pct: float`
  - `position_size: float`
  - `bars_in_trade: int`
  - `max_favorable_excursion: float`
  - `max_adverse_excursion: float`

**T-2.1.03** — Create `backend/src/fibokei/strategies/base.py` with `Strategy` abstract base class containing:
- Identity fields from blueprint S12.2: `strategy_id`, `strategy_name`, `strategy_family`, `description`, `logic_summary`, `valid_market_regimes: list[str]`, `supported_timeframes: list[Timeframe]`, `supports_long: bool`, `supports_short: bool`, `requires_mtfa: bool`, `requires_fibonacci: bool`, `complexity_level: str`
- Configuration: `config: dict` for strategy-specific parameters with defaults
- Abstract methods from blueprint S12.4:
  - `prepare_data(df: pd.DataFrame) -> pd.DataFrame`
  - `compute_indicators(df: pd.DataFrame) -> pd.DataFrame`
  - `detect_market_regime(df: pd.DataFrame, idx: int) -> str`
  - `detect_setup(df: pd.DataFrame, idx: int, context: dict) -> bool`
  - `generate_signal(df: pd.DataFrame, idx: int, context: dict) -> Signal | None`
  - `validate_signal(signal: Signal, context: dict) -> Signal`
  - `build_trade_plan(signal: Signal, context: dict) -> TradePlan`
  - `manage_position(position: dict, df: pd.DataFrame, idx: int, context: dict) -> dict`
  - `generate_exit(position: dict, df: pd.DataFrame, idx: int, context: dict) -> ExitReason | None`
  - `score_confidence(signal: Signal, context: dict) -> float`
  - `explain_decision(context: dict) -> str`
- Concrete helper methods:
  - `get_required_indicators() -> list[str]` — returns indicator names needed
  - `run_preparation(df: pd.DataFrame) -> pd.DataFrame` — calls prepare_data then compute_indicators

**T-2.1.04** — Create `backend/src/fibokei/strategies/registry.py` with:
- `StrategyRegistry` class: `register(strategy_class)`, `get(strategy_id: str) -> Strategy`, `list_available() -> list[dict]` (returns id, name, family, complexity for each)

**T-2.1.05** — Create `backend/tests/test_strategy_base.py` testing:
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

**T-2.2.01** — Create `backend/src/fibokei/indicators/swing.py` implementing `SwingDetector(Indicator)` with:
- Parameters: `lookback: int = 5` (number of bars on each side to confirm swing)
- `compute()` adds columns:
  - `swing_high: float | NaN` — price at confirmed swing highs, NaN elsewhere
  - `swing_low: float | NaN` — price at confirmed swing lows, NaN elsewhere
  - `last_swing_high: float` — most recent swing high value (forward-filled)
  - `last_swing_low: float` — most recent swing low value (forward-filled)
- Uses fractal logic: a swing high at bar `i` requires `high[i]` > all highs in `[i-lookback, i+lookback]` excluding `i`
- Note: swing detection has a built-in `lookback`-bar lag since it needs future bars to confirm

**T-2.2.02** — Create `backend/src/fibokei/indicators/candles.py` implementing `CandlestickPatterns(Indicator)` with:
- `compute()` adds boolean columns:
  - `bullish_engulfing`: current body fully engulfs prior bearish body
  - `bearish_engulfing`: current body fully engulfs prior bullish body
  - `bullish_pin_bar`: small body in upper third, long lower wick (>2x body)
  - `bearish_pin_bar`: small body in lower third, long upper wick (>2x body)
  - `strong_bullish_close`: close in top 25% of range with body > 60% of range
  - `strong_bearish_close`: close in bottom 25% of range with body > 60% of range
- Helper: `body_size`, `upper_wick`, `lower_wick`, `range_size` computed internally

**T-2.2.03** — Create `backend/src/fibokei/indicators/regime.py` implementing `MarketRegime(Indicator)` with:
- Depends on: Ichimoku values and ATR being already computed
- `compute()` adds column `regime: str` using logic:
  - `trending_bullish`: price above cloud, tenkan > kijun, expanding cloud
  - `trending_bearish`: price below cloud, tenkan < kijun, expanding cloud
  - `pullback_bullish`: overall trend bullish but price retracing toward kijun/cloud
  - `pullback_bearish`: overall trend bearish but price retracing toward kijun/cloud
  - `consolidation`: price inside cloud or tenkan ≈ kijun (within ATR * 0.3)
  - `breakout_candidate`: price near cloud edge, volatility contracting
  - `volatility_expansion`: ATR > 1.5x its 20-period average
  - `reversal_candidate`: extended from kijun, cloud twist projected ahead
  - `no_trade`: conflicting signals or insufficient data
- Implementation note: evaluate conditions in priority order, first match wins

**T-2.2.04** — Create `backend/tests/test_swing.py` with:
- Known price series with obvious peaks/troughs, verify detected swing points
- Test that `last_swing_high` and `last_swing_low` are properly forward-filled
- Test with different lookback values

**T-2.2.05** — Create `backend/tests/test_candles.py` with:
- Manually constructed bars that are definitively engulfing/pin-bar patterns
- Verify correct detection
- Verify no false positives on neutral bars

**T-2.2.06** — Create `backend/tests/test_regime.py` with:
- Test trending scenario (price above cloud, TK cross up) → `trending_bullish`
- Test pullback scenario → `pullback_bullish`
- Test consolidation (price inside cloud) → `consolidation`

**T-2.2.07** — Register all new indicators in the indicator registry.

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

**T-2.3.01** — Create `backend/src/fibokei/strategies/bot01_sanyaku.py` implementing `PureSanyakuConfluence(Strategy)` with:
- Identity:
  - `strategy_id = "bot01_sanyaku"`
  - `strategy_name = "Pure Sanyaku Confluence"`
  - `strategy_family = "ichimoku"`
  - `complexity_level = "standard"`
  - `supports_long = True`, `supports_short = True`
  - `valid_market_regimes = ["trending_bullish", "trending_bearish", "breakout_candidate"]`
- Required indicators: `IchimokuCloud`, `ATR`, `MarketRegime`
- `detect_setup()`: checks all three Sanyaku conditions simultaneously:
  1. Price closed above/below Kumo
  2. Tenkan-sen crossed above/below Kijun-sen (cross within last 3 bars)
  3. Chikou Span is above/below price from 26 periods ago
- `generate_signal()`: creates Signal with `entry_type = "market"`, `proposed_entry = next bar open estimate` (use current close as estimate), stop loss at kijun_sen or 1.5x ATR (whichever is wider), take profit at 2x risk distance
- `validate_signal()`: reject if regime is `no_trade` or `consolidation`, reject if confidence < 0.3
- `manage_position()`: track if Chikou crosses back through price or TK cross reverses
- `generate_exit()`: return `INDICATOR_INVALIDATION_EXIT` if Chikou crosses back, `OPPOSITE_SIGNAL_EXIT` if TK cross reverses, `TIME_STOP_EXIT` if max_bars_in_trade exceeded (default 50)

**T-2.3.02** — Register BOT-01 in strategy registry.

**T-2.3.03** — Create `backend/tests/test_bot01_sanyaku.py` with:
- Test strategy identity fields are correct
- Test with a constructed bullish Sanyaku setup → generates valid LONG signal
- Test with a constructed bearish Sanyaku setup → generates valid SHORT signal
- Test with non-confirming conditions → returns None (no signal)
- Test signal validation rejects in consolidation regime
- Test exit logic: TK cross reversal triggers exit

**T-2.3.04** — Create `backend/tests/test_bot01_integration.py` that:
- Loads sample EURUSD H1 fixture
- Runs `prepare_data()` + `compute_indicators()`
- Iterates through bars calling `generate_signal()` on each
- Collects all signals
- Asserts: at least 5 signals generated, all signals have valid structure, mix of LONG and SHORT present

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

**T-2.4.01** — Create `backend/src/fibokei/backtester/config.py` with:
- `BacktestConfig` model:
  - `initial_capital: float = 10000.0`
  - `risk_per_trade_pct: float = 1.0`
  - `spread_points: float = 0.0` (configurable per instrument)
  - `slippage_points: float = 0.0`
  - `commission_per_trade: float = 0.0`
  - `allow_long: bool = True`
  - `allow_short: bool = True`
  - `max_open_trades: int = 1`
  - `max_bars_in_trade: int = 100`

**T-2.4.02** — Create `backend/src/fibokei/backtester/position.py` with:
- `Position` class tracking:
  - `trade_id: str` (UUID)
  - `strategy_id: str`
  - `instrument: str`
  - `direction: Direction`
  - `entry_time: datetime`
  - `entry_price: float`
  - `stop_loss: float`
  - `take_profit_targets: list[float]`
  - `position_size: float`
  - `bars_in_trade: int`
  - `max_favorable_excursion: float`
  - `max_adverse_excursion: float`
  - `is_open: bool`
- Methods:
  - `update(bar: pd.Series) -> ExitReason | None` — check if stop or target hit within the bar, update MFE/MAE, increment bars_in_trade. Check stop first (conservative), then target.
  - `close(exit_price: float, exit_time: datetime, reason: ExitReason) -> TradeResult`
- Position sizing: `calculate_position_size(capital: float, risk_pct: float, entry: float, stop: float) -> float`

**T-2.4.03** — Create `backend/src/fibokei/backtester/engine.py` with `Backtester` class:
- `__init__(strategy: Strategy, config: BacktestConfig)`
- `run(df: pd.DataFrame, instrument: str, timeframe: Timeframe) -> BacktestResult`:
  1. Call `strategy.run_preparation(df)` to add indicators
  2. Initialize: equity = initial_capital, position = None, trades = [], equity_curve = []
  3. Iterate from `warmup_period` to end of data:
     a. If position open: call `strategy.generate_exit()` and `position.update(bar)`. If exit triggered, close position, record trade, update equity.
     b. If no position: call `strategy.generate_signal()`. If valid signal, call `strategy.build_trade_plan()`, calculate position size, open position with entry on current bar's close (simulating next-bar-open entry).
     c. Record equity snapshot.
  4. Close any remaining open position at last bar close with `SYSTEM_SHUTDOWN_EXIT`.
  5. Return `BacktestResult`.
- Apply spread: adjust entry price by `spread_points / 2` against trade direction
- Apply slippage: adjust entry price by `slippage_points` against trade direction

**T-2.4.04** — Create `backend/src/fibokei/backtester/result.py` with:
- `BacktestResult` model:
  - `strategy_id: str`
  - `instrument: str`
  - `timeframe: Timeframe`
  - `config: BacktestConfig`
  - `trades: list[TradeResult]`
  - `equity_curve: list[float]`
  - `start_date: datetime`
  - `end_date: datetime`
  - `total_bars: int`

**T-2.4.05** — Create `backend/tests/test_backtester.py` with:
- Test with a simple mock strategy that generates a known signal at bar 100 → verify trade opens and closes
- Test position sizing is correct given risk % and stop distance
- Test spread/slippage adjustment
- Test max_bars_in_trade time stop
- Test equity curve length equals data length minus warmup
- Test with BOT-01 on sample data → runs without error, produces trades

**T-2.4.06** — Create `backend/tests/test_backtester_determinism.py`:
- Run the same backtest twice with identical inputs
- Assert all trade entries, exits, and PnL values are identical (reproducibility rule from S16.4)

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

**T-2.5.01** — Create `backend/src/fibokei/backtester/metrics.py` with `compute_metrics(result: BacktestResult) -> dict` that calculates all metrics from blueprint S18.1:
- `total_net_profit`: sum of all trade PnL
- `gross_profit`: sum of winning trade PnL
- `gross_loss`: sum of losing trade PnL (negative)
- `profit_factor`: gross_profit / abs(gross_loss), handle zero division
- `win_rate`: winning trades / total trades
- `loss_rate`: 1 - win_rate
- `expectancy`: average PnL per trade
- `average_win`: mean PnL of winning trades
- `average_loss`: mean PnL of losing trades
- `reward_to_risk_ratio`: abs(average_win / average_loss)
- `total_trades`: len(trades)
- `long_trades`: count of LONG trades
- `short_trades`: count of SHORT trades
- `max_drawdown`: maximum peak-to-trough decline in equity curve
- `max_drawdown_pct`: max_drawdown as % of peak equity
- `sharpe_ratio`: annualized (mean daily return / std daily return) * sqrt(252), using equity curve returns
- `sortino_ratio`: like Sharpe but using downside deviation only
- `calmar_ratio`: annualized return / max_drawdown_pct
- `recovery_factor`: total_net_profit / max_drawdown
- `best_trade`: max single trade PnL
- `worst_trade`: min single trade PnL
- `avg_trade_duration_bars`: mean bars_in_trade across all trades
- `exposure_pct`: bars with open position / total bars
- `consecutive_wins`: max consecutive winning trades
- `consecutive_losses`: max consecutive losing trades

**T-2.5.02** — Add monthly and yearly returns calculation to `compute_metrics()`:
- `monthly_returns: dict[str, float]` — keyed by "YYYY-MM", value is return %
- `yearly_returns: dict[str, float]` — keyed by "YYYY", value is return %
- Calculate from equity curve by grouping snapshots into calendar periods

**T-2.5.03** — Create `backend/src/fibokei/backtester/display.py` with:
- `print_metrics(metrics: dict)` — formatted table output using tabulate:
  - Section: "Performance Summary"
  - Section: "Trade Statistics"
  - Section: "Risk Metrics"
  - Section: "Streaks"
  - Format numbers: 2 decimal places for money, 4 for ratios, 1 for percentages
- `print_trade_list(trades: list[TradeResult], limit: int = 20)` — tabulated last N trades with entry/exit times, prices, PnL, exit reason

**T-2.5.04** — Update `backend/src/fibokei/cli.py` to add backtest command:
- Parse arguments: `--strategy`, `--instrument`, `--timeframe`, `--data` (path to CSV), `--capital`, `--risk-pct`
- Default data path: `../data/fixtures/sample_eurusd_h1.csv`
- Load data, instantiate strategy from registry, configure backtest, run, compute metrics, print results
- Usage: `python -m fibokei backtest --strategy bot01_sanyaku --instrument EURUSD --timeframe H1`
- Also support: `python -m fibokei list-strategies` and `python -m fibokei list-indicators`
- Use `argparse` with subcommands

**T-2.5.05** — Create `backend/tests/test_metrics.py` with:
- Test with known trade list (3 wins of $100, 2 losses of -$50):
  - total_net_profit = $200
  - win_rate = 0.6
  - profit_factor = 3.0
  - expectancy = $40
- Test Sharpe ratio formula with known equity curve
- Test max drawdown calculation with known equity sequence [100, 110, 95, 105, 90] → max_dd = 20 (110→90)
- Test consecutive wins/losses counting
- Test edge cases: zero trades, all wins, all losses

**T-2.5.06** — Create `backend/tests/test_cli.py` with:
- Test `list-strategies` command outputs BOT-01
- Test `list-indicators` command outputs Ichimoku, ATR
- Test `backtest` command with sample data runs without error (subprocess call)

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

**T-3.1.01** — Create `backend/src/fibokei/indicators/fibonacci.py` implementing `FibonacciRetracement(Indicator)` with:
- Method: `compute_levels(swing_high: float, swing_low: float) -> dict[str, float]`
  - Returns levels: `0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0`
  - Values are absolute price levels between swing_low and swing_high
- `compute()` adds columns using the most recent confirmed swing high/low:
  - `fib_0`, `fib_236`, `fib_382`, `fib_500`, `fib_618`, `fib_786`, `fib_100`
  - These update whenever a new swing is confirmed

**T-3.1.02** — Add `FibonacciExtension` to same file with:
- Method: `compute_extensions(swing_a: float, swing_b: float, swing_c: float) -> dict[str, float]`
  - Returns levels: `1.0, 1.272, 1.618, 2.0, 2.618`
  - Extension from point C: `C + ratio * (B - A)` for bullish, inverse for bearish

**T-3.1.03** — Add `FibonacciTimeZones` to same file with:
- Method: `compute_time_zones(anchor_bar_idx: int, total_bars: int) -> list[int]`
  - Returns bar indices at Fibonacci intervals from anchor: anchor + 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144
  - Only return indices within `total_bars` range
- `compute()` adds column `fib_time_zone: bool` — True at bars that align with any active Fibonacci time zone

**T-3.1.04** — Create `backend/src/fibokei/indicators/volatility.py` with:
- `RollingVolatility(Indicator)`: adds `rolling_vol` column — standard deviation of log returns over configurable period (default 20)
- Useful for regime detection and position sizing

**T-3.1.05** — Register all new indicators. Create `backend/tests/test_fibonacci.py` and `backend/tests/test_volatility.py` with known-value tests for each indicator.

### Verification Gate

```bash
cd backend && pytest tests/test_fibonacci.py tests/test_volatility.py -v
python -c "
from fibokei.indicators.fibonacci import FibonacciRetracement
fib = FibonacciRetracement()
levels = fib.compute_levels(1.1500, 1.1000)
print(levels)
# Should show: {0.0: 1.15, 0.236: 1.1382, ..., 1.0: 1.1}
"
```
All tests pass. Fibonacci levels compute correctly.

---

## Subphase 3.2 — BOT-02 through BOT-06 (Ichimoku Family) [COMPLETE]

**Goal:** Implement the 5 Ichimoku-focused strategies.

**Dependencies:** Subphases 2.1, 2.2, 3.1

**Blueprint sections:** S13.2–S13.6

### Tasks

**T-3.2.01** — Create `backend/src/fibokei/strategies/bot02_kijun_pullback.py` implementing BOT-02 (Kijun-sen Pullback) per blueprint S13.2:
- Requires: Ichimoku, ATR, SwingDetector, CandlestickPatterns, MarketRegime
- Setup: price in valid trend, pulls back to touch/near Kijun (within 0.5 ATR)
- Entry: reversal candle (pin bar or engulfing) confirms rejection from Kijun
- Stop: 1 ATR beyond Kijun
- Target: recent swing high/low
- Valid regimes: `pullback_bullish`, `pullback_bearish`

**T-3.2.02** — Create `backend/src/fibokei/strategies/bot03_flat_senkou_b.py` implementing BOT-03 (Flat Senkou Span B Bounce) per blueprint S13.3:
- Requires: Ichimoku, ATR, MarketRegime
- Custom logic: detect flat Senkou Span B (slope < threshold over lookback window, default 10 bars)
- Entry: limit order at flat Span B level (simulate as entry when price touches level)
- Stop: opposite cloud boundary + buffer
- Target: prior swing level

**T-3.2.03** — Create `backend/src/fibokei/strategies/bot04_chikou_momentum.py` implementing BOT-04 (Chikou Open Space Momentum) per blueprint S13.4:
- Requires: Ichimoku, ATR, MarketRegime
- Custom logic: check that Chikou Span has "open space" — no price/cloud obstruction in the 26-bar lookback zone for at least N bars (configurable, default 5)
- Entry: market on valid breakout
- Exit: Tenkan trailing stop (close back through Tenkan)

**T-3.2.04** — Create `backend/src/fibokei/strategies/bot05_mtfa_sanyaku.py` implementing BOT-05 (MTFA Sanyaku) per blueprint S13.5:
- Requires: IchimokuCloud on two timeframes
- Custom logic: higher timeframe Sanyaku confirmation + lower timeframe Sanyaku entry
- Implementation: accept a `higher_tf_data: pd.DataFrame` parameter in config, or compute both timeframes in `prepare_data()` by resampling
- Default pairing: H4 filter → H1 execution (configurable)
- Exit: lower timeframe Kijun trailing stop

**T-3.2.05** — Create `backend/src/fibokei/strategies/bot06_nwave.py` implementing BOT-06 (N-Wave Structural Targeting) per blueprint S13.6:
- Requires: Ichimoku, SwingDetector, ATR
- Custom logic: identify A-B-C wave structure using swing detector, project N-wave target: `C + (B - A)` for bullish
- Entry: confirmation of pivot from point C
- Exit: hard target at N-wave projection, stop beyond C

**T-3.2.06** — Register all 5 strategies. Create test files `backend/tests/test_bot02.py` through `backend/tests/test_bot06.py` with:
- Identity field validation
- Signal generation on constructed scenarios
- Integration test on sample data (generates signals without errors)

**T-3.2.07** — Run all 6 strategies (BOT-01 through BOT-06) through the backtester on sample EURUSD H1 data and print comparison table via CLI enhancement: `python -m fibokei backtest --strategy all --instrument EURUSD --timeframe H1`.

### Verification Gate

```bash
cd backend
pytest tests/test_bot02.py tests/test_bot03.py tests/test_bot04.py tests/test_bot05.py tests/test_bot06.py -v
python -m fibokei backtest --strategy all --instrument EURUSD --timeframe H1
```
All tests pass. All 6 strategies produce backtest results. Comparison table shows metrics for each.

---

## Subphase 3.3 — BOT-07 through BOT-12 (Hybrid Family) [COMPLETE]

**Goal:** Complete the strategy library with 6 Fibonacci/hybrid strategies.

**Dependencies:** Subphases 3.1, 3.2

**Blueprint sections:** S13.7–S13.12

### Tasks

**T-3.3.01** — Create `backend/src/fibokei/strategies/bot07_kumo_twist.py` implementing BOT-07 (Kumo Twist Anticipator) per blueprint S13.7:
- Custom logic: detect projected cloud twist 26 periods ahead, confirm price extension from Kijun, counter-direction TK cross
- Counter-trend entry
- Target: mean reversion to Kumo
- Complexity: high

**T-3.3.02** — Create `backend/src/fibokei/strategies/bot08_kihon_suchi.py` implementing BOT-08 (Kihon Suchi Time Cycle Confluence) per blueprint S13.8:
- Custom logic: count bars since last swing, boost confidence when count aligns with Ichimoku numbers (9, 17, 26, 33, 42, 65, 76)
- Acts as confidence multiplier on base setups (pullback or breakout near key count)
- Kijun trailing stop

**T-3.3.03** — Create `backend/src/fibokei/strategies/bot09_golden_cloud.py` implementing BOT-09 (Golden Cloud Confluence) per blueprint S13.9:
- Custom logic: Fib 50%/61.8% retracement overlaps with Kumo support/resistance (within tolerance, default 0.5 ATR)
- Entry: limit at overlap zone
- Stop: below 78.6% or cloud boundary
- Target: prior swing / 0% retracement

**T-3.3.04** — Create `backend/src/fibokei/strategies/bot10_kijun_382.py` implementing BOT-10 (Kijun + 38.2% Shallow Continuation) per blueprint S13.10:
- Custom logic: momentum regime, expanding TK separation, 38.2% retracement aligns with Kijun (within tolerance)
- Aggressively reject sideways markets
- Kijun trailing stop

**T-3.3.05** — Create `backend/src/fibokei/strategies/bot11_sanyaku_fib_ext.py` implementing BOT-11 (Sanyaku + Fib Extension Targets) per blueprint S13.11:
- Standard Sanyaku entry
- Partial position management: close 50% at 1.272 extension, remainder at 1.618
- Move stop to breakeven after TP1
- Requires partial close support in backtester position management

**T-3.3.06** — Update `backend/src/fibokei/backtester/position.py` to support partial closes:
- `partial_close(pct: float, exit_price: float, exit_time: datetime) -> TradeResult` — closes a percentage of position, creates a TradeResult for the closed portion, reduces remaining position size
- Track remaining position separately

**T-3.3.07** — Create `backend/src/fibokei/strategies/bot12_twist_fib_time.py` implementing BOT-12 (Kumo Twist + Fibonacci Time Zone Anticipator) per blueprint S13.12:
- Custom logic: Kumo twist aligns with Fibonacci time zone column, Tenkan cross confirms reversal
- Most complex strategy — labeled `complexity_level = "advanced"`
- Target: opposite side of cloud
- Stop: beyond reversal extreme

**T-3.3.08** — Register all 6 strategies. Create test files `backend/tests/test_bot07.py` through `backend/tests/test_bot12.py` with unit and integration tests.

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

**T-3.4.01** — Add dependencies to `pyproject.toml`: `sqlalchemy>=2.0`, `alembic`, `aiosqlite` (for async SQLite dev).

**T-3.4.02** — Create `backend/src/fibokei/db/models.py` with SQLAlchemy 2.0 models:
- `UserModel`: id, username, password_hash, role, created_at
- `DatasetModel`: id, instrument, timeframe, source_id, bar_count, start_date, end_date, status, file_path, created_at
- `BacktestRunModel`: id, strategy_id, instrument, timeframe, config_json, start_date, end_date, total_trades, net_profit, sharpe_ratio, max_drawdown_pct, metrics_json, created_at
- `TradeModel`: id, backtest_run_id (FK), strategy_id, instrument, direction, entry_time, entry_price, exit_time, exit_price, exit_reason, pnl, bars_in_trade
- `ResearchResultModel`: id, run_id, strategy_id, instrument, timeframe, composite_score, rank, metrics_json, created_at
- `StrategyConfigModel`: id, strategy_id, config_json, is_active, created_at

**T-3.4.03** — Create `backend/src/fibokei/db/database.py` with:
- `get_engine(url: str)` — create SQLAlchemy engine (default: `sqlite:///fibokei.db`)
- `get_session()` — session factory
- `init_db()` — create all tables

**T-3.4.04** — Create `backend/src/fibokei/db/repository.py` with data access functions:
- `save_backtest_result(session, result: BacktestResult, metrics: dict)`
- `get_backtest_results(session, strategy_id=None, instrument=None) -> list`
- `save_research_results(session, results: list)`
- `get_research_rankings(session, sort_by="composite_score", limit=50) -> list`
- `save_dataset_meta(session, meta: DatasetMeta)`

**T-3.4.05** — Set up Alembic: `alembic init backend/alembic`, configure `alembic.ini` and `env.py` to use the models, create initial migration.

**T-3.4.06** — Update `Backtester.run()` to optionally save results to database when a session is provided.

**T-3.4.07** — Create `backend/tests/test_db.py` with:
- Test creating tables on fresh SQLite
- Test saving and retrieving a backtest result
- Test saving and retrieving research results
- Test filtering by strategy/instrument

### Verification Gate

```bash
cd backend
alembic upgrade head
pytest tests/test_db.py -v
python -c "
from fibokei.db.database import init_db, get_engine
engine = get_engine('sqlite:///test.db')
init_db(engine)
print('Database initialized successfully')
"
rm -f test.db
```
All tests pass. Database creates, migrations run, data round-trips correctly.

---

## Subphase 3.5 — Research Matrix Engine [COMPLETE]

**Goal:** Batch-run strategies across instruments/timeframes, score, and rank.

**Dependencies:** Subphases 3.3, 3.4

**Blueprint section:** S17

### Tasks

**T-3.5.01** — Create `backend/src/fibokei/research/scorer.py` with:
- `compute_composite_score(metrics: dict) -> float` using blueprint S17.6 weights:
  - 25% risk-adjusted quality: normalized Sharpe ratio (capped at 3.0, scaled 0–1)
  - 20% profit factor: min(profit_factor, 5.0) / 5.0
  - 20% normalized return: total_net_profit / initial_capital, capped and scaled
  - 15% drawdown control: 1.0 - min(max_drawdown_pct / 30.0, 1.0)
  - 10% sample sufficiency: min(total_trades / 80, 1.0)
  - 10% stability: based on equity curve smoothness (R² of linear fit)
- `ScoringConfig` model with configurable weights

**T-3.5.02** — Create `backend/src/fibokei/research/matrix.py` with `ResearchMatrix` class:
- `__init__(strategies: list[str], instruments: list[str], timeframes: list[Timeframe], config: BacktestConfig)`
- `run(data_dir: str) -> list[ResearchResult]`:
  1. For each (strategy, instrument, timeframe) combination:
     a. Load data from `data_dir/{instrument}_{timeframe}.csv`
     b. Skip if data file not found (log warning)
     c. Run backtest
     d. Compute metrics
     e. Compute composite score
     f. Create `ResearchResult` with all fields
  2. Sort by composite_score descending
  3. Assign rank
  4. Return results
- Progress reporting: print "Running {strategy} on {instrument} {timeframe}..." for each combination

**T-3.5.03** — Create `backend/src/fibokei/research/filter.py` with:
- `apply_minimum_trade_filter(results: list, min_trades: int = 80) -> tuple[list, list]` — returns (qualified, insufficient) results
- `apply_exploratory_filter(results: list, min_trades: int = 40) -> list` — returns results with 40-79 trades marked as "exploratory"

**T-3.5.04** — Create `backend/src/fibokei/research/display.py` with:
- `print_leaderboard(results: list, sort_by: str = "composite_score", limit: int = 20)` — formatted ranked table with: rank, strategy, instrument, timeframe, net_profit, sharpe, profit_factor, max_dd%, trades, composite_score
- `print_best_by(results: list, metric: str, limit: int = 10)` — top N by specific metric
- Support all "best by" views from blueprint S17.5

**T-3.5.05** — Update CLI to add research command:
- `python -m fibokei research --strategies bot01_sanyaku,bot02_kijun_pullback --instruments EURUSD,GBPUSD --timeframes H1,H4 --data-dir ../data/fixtures/`
- `--strategies all` runs all registered strategies
- `--min-trades 80` configurable threshold
- Print leaderboard and best-by-composite-score on completion

**T-3.5.06** — Create `backend/tests/test_scorer.py` with:
- Test composite score with known metrics → verify expected score
- Test each component (risk-adjusted, profit factor, etc.) independently
- Test weight normalization

**T-3.5.07** — Create `backend/tests/test_matrix.py` with:
- Test with 2 strategies × 1 instrument × 1 timeframe using fixture data
- Verify results are ranked by composite score
- Verify minimum trade filter works
- Verify results contain all expected fields

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

**T-4.1.01** — Add dependencies to `pyproject.toml`: `fastapi>=0.100`, `uvicorn[standard]`, `python-jose[cryptography]`, `passlib[bcrypt]`, `python-multipart`.

**T-4.1.02** — Create `backend/src/fibokei/api/app.py` with:
- FastAPI application with title "FIBOKEI API", version "1.0.0"
- CORS middleware allowing `http://localhost:3000` (Next.js dev) and configurable production origins
- API versioning via router prefix `/api/v1`
- Lifespan handler that initializes database on startup
- Health check: `GET /api/v1/health` → `{"status": "ok", "version": "1.0.0"}`

**T-4.1.03** — Create `backend/src/fibokei/api/auth.py` with:
- `POST /api/v1/auth/login` — accepts `username` and `password`, returns JWT access token
- JWT token with: `sub` (user_id), `username`, `exp` (configurable, default 24h)
- `get_current_user()` dependency that validates JWT from Authorization header
- Password hashing using bcrypt
- JWT secret from environment variable `FIBOKEI_JWT_SECRET` (required, no default)

**T-4.1.04** — Create `backend/src/fibokei/api/seed.py` with:
- `seed_users(session)` — create Joe and Tom users with hashed passwords from environment variables `FIBOKEI_USER_JOE_PASSWORD` and `FIBOKEI_USER_TOM_PASSWORD`
- Only seed if users don't exist
- Called during app startup

**T-4.1.05** — Create `backend/src/fibokei/api/routes/__init__.py` and `backend/src/fibokei/api/routes/instruments.py`:
- `GET /api/v1/instruments` → list of all 30 instruments with symbol, name, asset_class
- `GET /api/v1/instruments/{symbol}` → single instrument detail
- Protected by auth

**T-4.1.06** — Create `backend/src/fibokei/api/routes/strategies.py`:
- `GET /api/v1/strategies` → list of all registered strategies with id, name, family, complexity, description
- `GET /api/v1/strategies/{strategy_id}` → full strategy detail including supported timeframes, regimes, config
- Protected by auth

**T-4.1.07** — Create `backend/tests/test_api_auth.py` with:
- Test login with valid credentials → 200 + JWT token
- Test login with invalid credentials → 401
- Test protected endpoint without token → 401
- Test protected endpoint with valid token → 200

**T-4.1.08** — Create `backend/tests/test_api_instruments.py` and `backend/tests/test_api_strategies.py` with endpoint tests.

### Verification Gate

```bash
cd backend
pytest tests/test_api_auth.py tests/test_api_instruments.py tests/test_api_strategies.py -v
FIBOKEI_JWT_SECRET=test-secret uvicorn fibokei.api.app:app --host 0.0.0.0 --port 8000
# In another terminal: curl http://localhost:8000/api/v1/health
# Should return: {"status": "ok", "version": "1.0.0"}
```
All tests pass. API starts and health check responds.

---

## Subphase 4.2 — Backtest API Endpoints [COMPLETE]

**Goal:** Trigger and retrieve backtests via API.

**Dependencies:** Subphase 4.1

### Tasks

**T-4.2.01** — Create `backend/src/fibokei/api/routes/backtests.py` with:
- `POST /api/v1/backtests/run` — accepts: strategy_id, instrument, timeframe, data_path (optional), config overrides. Runs backtest synchronously (V1), returns backtest_run_id + summary metrics.
- `GET /api/v1/backtests` — list past backtest runs with filters (strategy_id, instrument, timeframe), pagination
- `GET /api/v1/backtests/{run_id}` — full backtest result with all metrics
- `GET /api/v1/backtests/{run_id}/trades` — paginated trade list
- `GET /api/v1/backtests/{run_id}/equity-curve` — equity curve data points for charting

**T-4.2.02** — Create request/response Pydantic models in `backend/src/fibokei/api/schemas/backtests.py`:
- `BacktestRunRequest`, `BacktestRunResponse`, `BacktestDetailResponse`, `TradeListResponse`, `EquityCurveResponse`

**T-4.2.03** — Create `backend/tests/test_api_backtests.py` with:
- Test POST run → returns valid result with metrics
- Test GET list → returns previous runs
- Test GET detail → returns full metrics
- Test GET trades → returns trade list
- Test GET equity-curve → returns numeric array

### Verification Gate

```bash
cd backend && pytest tests/test_api_backtests.py -v
```
All tests pass. Can trigger backtest via API and retrieve results.

---

## Subphase 4.3 — Research API Endpoints

**Goal:** Trigger research runs and retrieve rankings via API.

**Dependencies:** Subphases 4.1, 3.5

### Tasks

**T-4.3.01** — Create `backend/src/fibokei/api/routes/research.py` with:
- `POST /api/v1/research/run` — accepts: strategy_ids, instruments, timeframes, config. Runs research matrix, persists results, returns run_id + summary.
- `GET /api/v1/research/runs` — list past research runs
- `GET /api/v1/research/runs/{run_id}` — detailed results for a run
- `GET /api/v1/research/rankings` — current top-ranked combinations with sort_by parameter (composite_score, sharpe, profit_factor, etc.)
- `GET /api/v1/research/compare` — compare specific strategy-instrument-timeframe combinations side by side (query params: combos as comma-separated)

**T-4.3.02** — Create `backend/tests/test_api_research.py` with endpoint tests.

### Verification Gate

```bash
cd backend && pytest tests/test_api_research.py -v
```
All tests pass. Research endpoints serve ranked data.

---

## Subphase 4.4 — Paper Trading Engine

**Goal:** Forward-test strategies with virtual capital.

**Dependencies:** Subphases 3.3, 3.4

**Blueprint sections:** S21, S20

### Tasks

**T-4.4.01** — Create `backend/src/fibokei/paper/account.py` with:
- `PaperAccount` class:
  - `balance: float`, `equity: float`, `initial_balance: float`
  - `open_positions: list[Position]`
  - `closed_trades: list[TradeResult]`
  - `daily_pnl: float`, `weekly_pnl: float`
  - `update_equity()` — recalculate from balance + unrealised PnL
  - `deposit(amount)`, `reset()`

**T-4.4.02** — Create `backend/src/fibokei/paper/bot.py` with `PaperBot` class:
- `__init__(bot_id: str, strategy: Strategy, instrument: str, timeframe: Timeframe, account: PaperAccount, risk_config: dict)`
- State machine: `IDLE → MONITORING → POSITION_OPEN → IDLE`
- `on_candle_close(bar: pd.Series, context: dict)`:
  1. Update indicators on new bar
  2. If position open: check exit conditions, manage position
  3. If no position: check for new signal, validate against risk limits, open position if valid
- `start()`, `stop()`, `pause()`, `get_status() -> dict`

**T-4.4.03** — Create `backend/src/fibokei/risk/engine.py` with `RiskEngine` class:
- `check_trade_allowed(signal: Signal, account: PaperAccount, portfolio_state: dict) -> tuple[bool, str]`:
  - Check per-trade risk ≤ configured max (default 1.0%)
  - Check total portfolio risk ≤ 5%
  - Check max open trades ≤ 8
  - Check per-instrument trade limit
  - Check correlated-group risk ≤ 2.5%
  - Returns (allowed, rejection_reason)
- `check_drawdown_limits(account: PaperAccount) -> tuple[bool, str]`:
  - Daily soft stop -3%, hard stop -4%
  - Weekly soft stop -6%, hard stop -8%
  - Returns (safe, alert_level)
- Position sizing: `calculate_size(equity: float, risk_pct: float, entry: float, stop: float, instrument: Instrument) -> float`

**T-4.4.04** — Create `backend/src/fibokei/paper/orchestrator.py` with `BotOrchestrator` class:
- Manages multiple `PaperBot` instances
- `add_bot(strategy_id, instrument, timeframe) -> str` (returns bot_id)
- `remove_bot(bot_id)`
- `start_all()`, `stop_all()`
- `get_all_status() -> list[dict]`
- `on_tick(instrument: str, bar: pd.Series)` — routes to relevant bots
- Shared `PaperAccount` and `RiskEngine` across all bots

**T-4.4.05** — Create `backend/tests/test_paper_account.py`, `backend/tests/test_paper_bot.py`, `backend/tests/test_risk_engine.py` with:
- Account balance/equity updates
- Bot state transitions
- Risk engine trade rejection scenarios
- Drawdown limit enforcement

### Verification Gate

```bash
cd backend
pytest tests/test_paper_account.py tests/test_paper_bot.py tests/test_risk_engine.py -v
```
All tests pass. Paper bot can run through a simulated data replay and produce trades.

---

## Subphase 4.5 — Paper Trading API + Telegram Alerts

**Goal:** Control paper bots via API and receive Telegram notifications.

**Dependencies:** Subphases 4.1, 4.4

**Blueprint section:** S23

### Tasks

**T-4.5.01** — Create `backend/src/fibokei/api/routes/paper.py` with:
- `POST /api/v1/paper/bots` — create and start a paper bot (strategy_id, instrument, timeframe)
- `GET /api/v1/paper/bots` — list all bots with status
- `GET /api/v1/paper/bots/{bot_id}` — bot detail (status, PnL, position info)
- `POST /api/v1/paper/bots/{bot_id}/stop` — stop a bot
- `POST /api/v1/paper/bots/{bot_id}/pause` — pause a bot
- `GET /api/v1/paper/bots/{bot_id}/trades` — paper trade history for this bot
- `GET /api/v1/paper/account` — account overview (balance, equity, daily/weekly PnL)

**T-4.5.02** — Create `backend/src/fibokei/alerts/telegram.py` with:
- `TelegramNotifier` class:
  - `__init__(bot_token: str, chat_id: str)` — from env vars `FIBOKEI_TELEGRAM_BOT_TOKEN`, `FIBOKEI_TELEGRAM_CHAT_ID`
  - `send_message(text: str)` — send via Telegram Bot API
  - `send_signal_alert(signal: Signal)` — formatted signal notification
  - `send_trade_opened(trade_plan: TradePlan)` — formatted trade notification
  - `send_trade_closed(trade: TradeResult)` — formatted close notification
  - `send_risk_alert(alert_type: str, details: str)` — risk limit breach notification
  - `send_daily_summary(account: PaperAccount, trades: list[TradeResult])` — daily recap
- All messages include: timestamp, strategy, instrument, timeframe, direction, key prices, PnL where relevant
- Uses `httpx` for async HTTP calls (add to dependencies)

**T-4.5.03** — Create `backend/src/fibokei/alerts/events.py` with:
- `AlertEvent` enum covering all event types from blueprint S23.2
- `AlertDispatcher` class that routes events to configured notifiers
- Hook into paper bot lifecycle: signal detected → alert, trade opened → alert, trade closed → alert, risk breach → alert

**T-4.5.04** — Create `backend/tests/test_telegram.py` with:
- Test message formatting (mock HTTP calls)
- Test signal alert contains required fields
- Test trade closed alert includes PnL

**T-4.5.05** — Create `backend/tests/test_api_paper.py` with:
- Test create bot → returns bot_id
- Test list bots → includes created bot
- Test stop bot → status changes
- Test account endpoint → returns balance info

### Verification Gate

```bash
cd backend
pytest tests/test_telegram.py tests/test_api_paper.py -v
# Manual verification with real Telegram bot (requires env vars):
# FIBOKEI_TELEGRAM_BOT_TOKEN=xxx FIBOKEI_TELEGRAM_CHAT_ID=yyy python -c "
# from fibokei.alerts.telegram import TelegramNotifier
# notifier = TelegramNotifier()
# notifier.send_message('FIBOKEI test alert: system online')
# "
```
All tests pass. Paper bot API works. Telegram test message sends successfully (manual test).

---

# PHASE 5: WEB PLATFORM + LIVE-READY ARCHITECTURE

**Objective:** Next.js dashboard on Vercel, full system control via browser, live-ready adapter layer.

**Blueprint sections covered:** S10.3 (Frontend), S24 (Web Platform), S22 (Live Execution), S5 (Brand)

**Milestone:** Dashboard functional on Vercel, paper bot controllable from browser.

---

## Subphase 5.1 — Next.js Project Setup

**Goal:** Frontend skeleton with auth and API integration.

**Dependencies:** Subphase 4.1

### Tasks

**T-5.1.01** — Initialize Next.js project in `frontend/` with:
- `npx create-next-app@latest . --typescript --tailwind --app --src-dir --no-import-alias`
- App Router, TypeScript, Tailwind CSS
- Remove boilerplate content

**T-5.1.02** — Configure project:
- `frontend/.env.local.example` with `NEXT_PUBLIC_API_URL=http://localhost:8000`
- Tailwind config matching blueprint S5.3 visual direction:
  - Background colors: off-white layers (`#FAFAF9`, `#F5F5F4`, `#FFFFFF`)
  - Text: near-black (`#1C1917`)
  - Primary greens: `#16A34A` (main), `#15803D` (dark), `#22C55E` (light), `#86EFAC` (accent)
  - Muted support: slate grays
  - Font: Inter or system sans-serif

**T-5.1.03** — Create `frontend/src/lib/api.ts` — typed API client:
- `login(username, password) → { token }`
- `fetchWithAuth(url, options)` — adds JWT from stored token
- Type definitions for all API response shapes
- Error handling with typed errors

**T-5.1.04** — Create `frontend/src/lib/auth.tsx` — auth context:
- `AuthProvider` with `login()`, `logout()`, `user`, `isAuthenticated`
- Store JWT in localStorage (V1)
- Redirect to login if unauthenticated

**T-5.1.05** — Create `frontend/src/app/login/page.tsx`:
- Clean login form matching brand direction
- Username + password fields
- Submit calls API, stores token, redirects to dashboard
- Error display for invalid credentials

**T-5.1.06** — Create layout in `frontend/src/app/(dashboard)/layout.tsx`:
- Sidebar navigation: Dashboard, Charts, Backtests, Research, Paper Bots, Trade History, Settings
- Header with username and logout
- Protected by auth context
- Light theme matching blueprint visual direction

**T-5.1.07** — Configure `frontend/vercel.json` for deployment. Create `frontend/.gitignore`.

**T-5.1.08** — Create `frontend/src/app/(dashboard)/page.tsx` — minimal dashboard placeholder showing "Welcome to FIBOKEI" and system health status from API.

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

## Subphase 5.2 — Dashboard and Charts

**Goal:** Main dashboard with KPIs, candlestick charts with Ichimoku overlay.

**Dependencies:** Subphase 5.1

### Tasks

**T-5.2.01** — Install charting library: `npm install lightweight-charts` (TradingView's lightweight charts — excellent for candlesticks + overlays).

**T-5.2.02** — Create `frontend/src/components/charts/CandlestickChart.tsx`:
- Renders OHLCV candlestick chart using lightweight-charts
- Props: `data: OHLCVBar[]`, `overlays?: ChartOverlay[]`, `markers?: ChartMarker[]`
- Support overlays for: Ichimoku cloud (area between senkou_a and senkou_b), tenkan line, kijun line
- Support markers for: entry arrows (up/down), exit markers, stop/target lines
- Responsive sizing

**T-5.2.03** — Create `frontend/src/app/(dashboard)/page.tsx` (full dashboard):
- Summary cards row: Total Equity, Day PnL, Week PnL, Current Drawdown %
- Active Bots count with status indicators (green = running, yellow = paused)
- Open Trades list (mini table)
- Recent Trades list (last 10)
- System Health indicator (API connected / disconnected)
- All data fetched from API endpoints

**T-5.2.04** — Create `frontend/src/app/(dashboard)/charts/page.tsx`:
- Instrument selector dropdown (30 instruments)
- Timeframe selector (M1–H4)
- Strategy overlay selector (choose which strategy's signals to display)
- CandlestickChart with Ichimoku overlay
- Entry/exit markers from backtest or paper trade data
- Data fetched from API

**T-5.2.05** — Create shared UI components in `frontend/src/components/ui/`:
- `StatCard.tsx` — metric card with label, value, trend indicator
- `DataTable.tsx` — sortable, filterable table component
- `Select.tsx` — styled dropdown
- `Badge.tsx` — status badges (running, paused, stopped)
- All using Tailwind, matching brand colors

### Verification Gate

```bash
cd frontend && npm run build
# Visit dashboard → summary cards show data from API
# Visit charts → candlestick chart renders with Ichimoku cloud overlay
# Select different instruments/timeframes → chart updates
```
Dashboard displays real data. Charts render candlesticks with overlays.

---

## Subphase 5.3 — Backtest and Research UI

**Goal:** Run backtests and view research rankings from the browser.

**Dependencies:** Subphases 5.2, 4.2, 4.3

### Tasks

**T-5.3.01** — Create `frontend/src/app/(dashboard)/backtests/page.tsx`:
- Backtest configuration form: strategy dropdown, instrument multi-select, timeframe multi-select, date range picker, capital input, risk % input
- "Run Backtest" button → POST to API
- Results list below: past backtest runs as cards/rows, click to expand

**T-5.3.02** — Create `frontend/src/app/(dashboard)/backtests/[id]/page.tsx`:
- Full backtest result view
- Metrics table (all 25+ metrics, organized in sections)
- Equity curve chart (line chart)
- Drawdown curve chart (area chart, inverted)
- Trade list table with sorting by PnL, date, duration
- Annotated price chart with entry/exit markers

**T-5.3.03** — Create `frontend/src/app/(dashboard)/research/page.tsx`:
- Research matrix trigger: select strategies, instruments, timeframes → run
- Leaderboard table: ranked by composite score, sortable by any metric
- "Best by" dropdown to switch ranking metric
- Heatmap view: strategy × instrument grid, colored by composite score (green = strong, red = weak)
- Minimum trade count filter toggle (80 / 40 / off)

**T-5.3.04** — Install and use `recharts` for equity curves, drawdown charts, and heatmap: `npm install recharts`.

### Verification Gate

```bash
cd frontend && npm run build
# Run a backtest from UI → results display with charts and metrics
# Run research → leaderboard and heatmap render with real data
```
Backtest form works end-to-end. Research heatmap visualizes strategy-instrument performance.

---

## Subphase 5.4 — Paper Trading and Trade History UI

**Goal:** Control paper bots and inspect trade history from browser.

**Dependencies:** Subphases 5.2, 4.5

### Tasks

**T-5.4.01** — Create `frontend/src/app/(dashboard)/bots/page.tsx`:
- "Add Bot" form: strategy, instrument, timeframe, risk profile
- Active bots list: bot_id, strategy, instrument, timeframe, status badge, current PnL, actions (stop/pause)
- Bot detail expandable: last signal, current position, recent trades
- Polling for real-time updates (every 5 seconds via `setInterval` + API fetch)

**T-5.4.02** — Create `frontend/src/app/(dashboard)/trades/page.tsx`:
- Trade history table with columns: date, strategy, instrument, direction, entry, exit, PnL, exit reason, duration
- Filters: strategy dropdown, instrument dropdown, timeframe dropdown, date range, direction, exit reason
- Sort by any column
- CSV export button → generates and downloads CSV file
- Pagination

**T-5.4.03** — Create `frontend/src/app/(dashboard)/trades/[id]/page.tsx`:
- Individual trade detail: full trade info, annotated chart snippet showing entry/exit on price chart, signal rationale

### Verification Gate

```bash
cd frontend && npm run build
# Start a paper bot from UI → appears in active bots list
# Stop bot from UI → status updates
# View trade history → filters and sorting work
# Export CSV → file downloads with correct data
```
Bot controls work. Trade history displays and exports.

---

## Subphase 5.5 — Settings, System Pages, and Vercel Deployment

**Goal:** Settings pages, system diagnostics, production deployment.

**Dependencies:** Subphases 5.1–5.4

### Tasks

**T-5.5.01** — Create `frontend/src/app/(dashboard)/settings/page.tsx`:
- User settings section: change password (calls API)
- Telegram settings: bot token, chat ID (stored in backend config)
- Risk defaults: risk per trade %, max portfolio risk %, max open trades, drawdown limits
- Feature flags display (read-only in V1): live_execution_enabled (always false)

**T-5.5.02** — Create `frontend/src/app/(dashboard)/system/page.tsx`:
- Engine status: API connection, database status, paper engine status
- Dataset registry: list of available datasets with status labels
- Active alerts log: recent alert history
- Logging: last 100 log entries from API

**T-5.5.03** — Deploy frontend to Vercel:
- Connect GitHub repo to Vercel
- Set root directory to `frontend/`
- Configure environment variables: `NEXT_PUBLIC_API_URL` pointing to backend deployment
- Verify deployment builds and serves correctly

**T-5.5.04** — Create loading/splash screen at `frontend/src/app/loading.tsx`:
- FIBOKEI branding
- Animated loading indicator
- Displayed during initial app load after login

### Verification Gate

```bash
cd frontend && npm run build
# Deploy to Vercel → site accessible at production URL
# Login → loading screen → dashboard
# Settings page → displays current config
# System page → shows engine status
```
Frontend deployed to Vercel and fully functional.

---

## Subphase 5.6 — Live-Ready Execution Architecture

**Goal:** Abstract execution layer so paper and future live trading use the same interface.

**Dependencies:** Subphase 4.4

**Blueprint sections:** S22, S29.2

### Tasks

**T-5.6.01** — Create `backend/src/fibokei/execution/adapter.py` with:
- `ExecutionAdapter` abstract base class:
  - `place_order(order: Order) -> OrderResult`
  - `cancel_order(order_id: str) -> bool`
  - `modify_order(order_id: str, changes: dict) -> OrderResult`
  - `get_positions() -> list[Position]`
  - `get_account_info() -> AccountInfo`
  - `close_position(position_id: str) -> OrderResult`
  - `partial_close(position_id: str, pct: float) -> OrderResult`
- `Order`, `OrderResult`, `AccountInfo` models

**T-5.6.02** — Create `backend/src/fibokei/execution/paper_adapter.py` with `PaperExecutionAdapter(ExecutionAdapter)`:
- Refactor `PaperBot` to route all order operations through this adapter
- Implements all methods using the paper account/position system
- Drop-in replacement: if paper bot works through this adapter, live adapter will slot in identically

**T-5.6.03** — Create `backend/src/fibokei/execution/ig_adapter.py` with `IGExecutionAdapter(ExecutionAdapter)`:
- **Stub implementation only** — all methods raise `NotImplementedError("IG live trading not enabled in V1")`
- Includes docstrings specifying which IG REST API endpoints each method will call when implemented
- Authentication placeholder: `ig_api_key`, `ig_username`, `ig_password`, `ig_account_id` from env vars

**T-5.6.04** — Create `backend/src/fibokei/core/feature_flags.py` with:
- `FeatureFlags` class reading from env vars:
  - `FIBOKEI_LIVE_EXECUTION_ENABLED: bool = False`
  - `FIBOKEI_IG_PAPER_MODE: bool = True`
- `get_execution_adapter() -> ExecutionAdapter` — returns `PaperExecutionAdapter` unless live is enabled AND explicitly confirmed

**T-5.6.05** — Create `backend/tests/test_execution_adapter.py` with:
- Test PaperExecutionAdapter implements all abstract methods
- Test IGExecutionAdapter raises NotImplementedError
- Test feature flags default to paper mode
- Test paper bot works through ExecutionAdapter interface

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
- Every module has unit tests
- Every strategy has integration tests on sample data
- Backtester determinism test runs on every strategy addition
- API endpoints have integration tests
- Test fixtures use deterministic seed-based sample data

## Documentation Standards

**Apply throughout all phases:**
- Every new module has a docstring explaining its purpose
- Strategy files include the blueprint section reference (e.g., "Implements BOT-01 per blueprint S13.1")
- Complex algorithms have inline comments explaining the logic
- API endpoints have OpenAPI descriptions

## Security Standards

**Apply throughout all phases:**
- No secrets in code — all from environment variables
- Passwords hashed with bcrypt
- JWT tokens with expiration
- API routes protected by auth
- Feature flags gate dangerous operations
- `.env` files in `.gitignore`

---

# DEPENDENCY GRAPH

```
Phase 1.1 (Repo Skeleton)
  └─→ Phase 1.2 (Data Models)
       └─→ Phase 1.3 (Indicators)
            ├─→ Phase 2.1 (Strategy Framework)
            │    └─→ Phase 2.3 (BOT-01)
            │         └─→ Phase 2.4 (Backtester)
            │              └─→ Phase 2.5 (Metrics + CLI)
            │                   └─→ Phase 3.4 (Database)
            │                        ├─→ Phase 3.5 (Research Matrix)
            │                        └─→ Phase 4.1 (FastAPI)
            │                             ├─→ Phase 4.2 (Backtest API)
            │                             ├─→ Phase 4.3 (Research API)
            │                             ├─→ Phase 4.5 (Paper API + Alerts)
            │                             └─→ Phase 5.1 (Next.js Setup)
            │                                  └─→ Phases 5.2-5.5 (UI Pages)
            └─→ Phase 2.2 (Swing/Candle/Regime)
                 └─→ Phase 3.1 (Fibonacci Indicators)
                      └─→ Phase 3.2 (BOT-02 to BOT-06)
                           └─→ Phase 3.3 (BOT-07 to BOT-12)

Phase 4.4 (Paper Engine) ─→ Phase 4.5 (Paper API)
Phase 4.4 (Paper Engine) ─→ Phase 5.6 (Execution Adapter)
```

---

# MILESTONES SUMMARY

| Milestone | Phase | Verification |
|-----------|-------|-------------|
| First Heartbeat | 1.3 | Ichimoku values print on EURUSD sample data |
| First Backtest | 2.5 | CLI command produces full metrics for BOT-01 |
| Strategy Library Complete | 3.3 | All 12 strategies registered and producing results |
| Research Matrix Live | 3.5 | Ranked leaderboard across strategies/instruments |
| API Online | 4.1 | Health check responds, auth works |
| Paper Engine Running | 4.5 | Paper bot runs via API, Telegram alerts fire |
| Dashboard Live | 5.5 | Frontend on Vercel, full system control |
| Live-Ready | 5.6 | ExecutionAdapter abstraction tested, IG stub in place |

---

# ESTIMATED SESSION COUNT

| Phase | Subphases | Estimated Claude Code Sessions |
|-------|-----------|-------------------------------|
| Phase 1 | 1.1–1.3 | 3–4 sessions |
| Phase 2 | 2.1–2.5 | 5–7 sessions |
| Phase 3 | 3.1–3.5 | 8–12 sessions |
| Phase 4 | 4.1–4.5 | 5–7 sessions |
| Phase 5 | 5.1–5.6 | 7–10 sessions |
| **Total** | **23 subphases** | **28–40 sessions** |

---

This roadmap defines how FIBOKEI gets built. Each task is executable. Each gate is testable. Start at T-1.1.01.
