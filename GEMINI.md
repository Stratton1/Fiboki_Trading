# FIBOKEI

## Project Overview
FIBOKEI is a full-stack, multi-strategy automated trading platform focused on **Ichimoku Cloud** and **Fibonacci-based** trading systems. It is designed to research, rank, simulate, monitor, and eventually execute algorithmic strategies across multiple liquid instruments and short-to-medium intraday timeframes.

The project is structured as a monorepo containing a Python backend for research, backtesting, and paper trading execution, alongside a Next.js frontend for monitoring and control.

### Main Technologies
- **Backend:** Python 3.11+, FastAPI, Pandas, NumPy, Pydantic, SQLAlchemy, Alembic
- **Frontend:** Next.js, TypeScript, Tailwind CSS (deployed on Vercel)
- **Database:** SQLite (development), PostgreSQL (production)

## Building and Running

### Setup
```bash
cd backend
pip install -e ".[dev]"
```

### Running the CLI
```bash
cd backend
python -m fibokei
```

### Testing
```bash
cd backend
pytest -v
```

### Linting
```bash
cd backend
ruff check src/
```

## Development Conventions

- **Strategy Architecture:** All strategies must inherit from the common `Strategy` base class and output normalized `Signal` and `TradePlan` objects. Strategy logic must NOT depend directly on broker APIs; broker logic must be adapter-based.
- **Signal Evaluation:** Signals are evaluated on **closed candles only**. No intrabar triggering and no repainting logic. No use of future data is permitted except for valid projected Ichimoku components.
- **Data Standards:** All timestamps must be stored and processed in UTC. The canonical OHLCV schema is strictly enforced: `timestamp`, `open`, `high`, `low`, `close`, `volume`. Data must be validated before use.
- **Testing:** Backtest results must be 100% deterministic (given the same input data and parameters, the output must be identical). Every indicator must have known-value unit tests, and every strategy must have signal generation integration tests.
- **Risk Management:** Risk controls are mandatory and portfolio-aware (e.g., default risk per trade is 1.0%, max portfolio risk is 5%). Analytics and risk logic must not be embedded directly inside strategies.
- **Security:** No plaintext credentials in the repository; use environment variables exclusively. Live execution is disabled by default and gated behind feature flags.
- **Execution Mode:** A strictly "paper-first" workflow is enforced. Strategies must pass historical backtesting with a minimum trade-count filtering, then forward paper testing before being considered for live execution.
