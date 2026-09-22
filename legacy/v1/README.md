# Fiboki

Multi-strategy automated trading platform focused on Ichimoku Cloud and Fibonacci-based trading systems. Built for research, backtesting, paper trading, and eventual live execution.

**Production:** https://fiboki.uk | **API:** https://api.fiboki.uk

## Tech Stack

- **Backend:** Python 3.11+ / FastAPI
- **Frontend:** Next.js / TypeScript
- **Database:** SQLite (dev) / PostgreSQL (prod)
- **Deployment:** Vercel (frontend), separate host (backend engines)

## Setup

```bash
cd backend
pip install -e ".[dev]"
python -m fibokei
```

## Documentation

Supported instrument universe: 67 instruments (60 with HistData canonical data × 6 timeframes; 7 alternate-provider).

- [Blueprint](docs/blueprint.md) — full project specification
- [Roadmap](docs/roadmap.md) — phased build plan
