# FIBOKEI

Multi-strategy automated trading platform using Ichimoku Cloud and Fibonacci systems.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI
- **Frontend:** Next.js + TypeScript (Vercel)
- **Database:** SQLite (dev), PostgreSQL (prod)

## Commands

```bash
# Setup
cd backend && pip install -e ".[dev]"

# Run tests
cd backend && pytest -v

# Lint
cd backend && ruff check src/

# CLI
cd backend && python -m fibokei
```

## Architecture Rules

- Strategy logic must NOT depend on broker APIs
- Broker logic must be adapter-based
- Indicator calculations must be centralized in `indicators/`
- Analytics must NOT be embedded inside strategies
- Risk logic must NOT be duplicated in strategies
- Frontend must NOT contain trading logic
- Backtest and paper mode share execution logic

## Structure

```
backend/src/fibokei/
  core/         — Data models, enums, instruments
  indicators/   — Technical indicator implementations
  strategies/   — Strategy implementations (12 bots)
  backtester/   — Backtesting engine
  research/     — Research matrix, ranking
  paper/        — Paper trading engine
  risk/         — Risk management
  api/          — FastAPI routes
  data/         — Data loading and validation
  alerts/       — Telegram notifications
  execution/    — Execution adapters (paper, IG)
  cli.py        — CLI entry point
```

## Non-Negotiables

- Paper first — no live execution in V1
- No plaintext credentials — env vars only
- All strategies use the common framework
- All signals evaluated on closed candles only
- Timestamps stored in UTC
- Deterministic backtest results
- Minimum 80 trades for primary ranking
- Portfolio-aware risk controls mandatory
