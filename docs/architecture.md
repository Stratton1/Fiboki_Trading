# Fiboki System Architecture

## 1. System Overview

Fiboki is a multi-strategy automated trading platform built on Ichimoku Cloud and Fibonacci systems. The platform runs 12 strategy bots across backtesting, research ranking, and paper trading modes.

| Layer | Technology | Deployment |
|-------|-----------|------------|
| Frontend | Next.js + TypeScript | Vercel |
| Backend | Python 3.11+ / FastAPI | Railway or Render |
| Database | SQLite (dev) / PostgreSQL (prod) | Co-located with backend |
| Alerts | Telegram Bot API | Backend process |

## 2. Component Boundaries

### Backend (`backend/src/fibokei/`)

All trading logic lives in the backend. The frontend never computes indicators, evaluates signals, or manages positions.

| Module | Responsibility |
|--------|---------------|
| `core/` | Data models, enums, instruments, signals, trades |
| `indicators/` | Ichimoku, ATR, Fibonacci, swing, regime, volatility, candles |
| `strategies/` | 12 bot implementations + registry |
| `backtester/` | Engine, config, position tracking, metrics, result display |
| `research/` | Research matrix, composite scoring, filtering, ranking |
| `paper/` | Paper trading account, bot lifecycle, orchestrator |
| `risk/` | Risk engine (portfolio-aware controls) |
| `execution/` | Adapter ABC, PaperExecutionAdapter, IGExecutionAdapter, IGClient, reconciliation |
| `api/` | FastAPI app, routes, schemas, auth, seed, dependencies |
| `data/` | CSV loader, data validation |
| `alerts/` | Telegram event notifications |
| `db/` | SQLAlchemy models, database session, repository |
| `cli.py` | CLI entry point (`python -m fibokei`) |

### Frontend (`frontend/src/`)

The frontend is display-only. It fetches data from the backend API, renders charts and analytics, and provides controls for backtesting, research, and paper bots. See [docs/frontend_architecture.md](frontend_architecture.md) for full details.

## 3. Deployment Topology

```
                    Internet
                       |
           +-----------+-----------+
           |                       |
     +-----v------+        +------v-------+
     |   Vercel    |        |  Railway /   |
     |  (Next.js)  |------->|   Render     |
     |  Frontend   |  API   | (FastAPI)    |
     +-----------+-+  calls +------+-------+
                                   |
                            +------v-------+
                            |  SQLite /    |
                            |  PostgreSQL  |
                            +--------------+
```

- **Frontend** deploys to Vercel via `frontend/` directory.
- **Backend** deploys to Railway or Render via `backend/` directory.
- CORS configured in `backend/src/fibokei/api/app.py` -- allows `http://localhost:3000` plus any origins in `FIBOKEI_CORS_ORIGINS`.

## 4. Data Flow

### Authentication Flow

```
Browser -> POST /api/v1/auth/login (form-encoded)
        <- Set-Cookie: fibokei_token (HttpOnly, SameSite=Lax, max_age=24h)
        <- JSON: { access_token, token_type }

Browser -> GET /api/v1/auth/me (cookie sent automatically)
        <- JSON: { user_id, username, role }
```

All subsequent API calls use `credentials: "include"`. The frontend never reads or stores the JWT. See [docs/auth_spec.md](auth_spec.md) for details.

### Request Flow

```
Next.js Page
  -> SWR hook (use-backtests, use-trades, etc.)
    -> lib/api.ts (apiFetch with credentials: "include")
      -> FastAPI route handler
        -> get_current_user dependency (cookie -> JWT decode)
        -> Business logic (backtester, research, paper engine)
        -> SQLAlchemy repository
        -> JSON response
      <- Response
    <- SWR cache + revalidation
  <- React render
```

### Chart Data Flow

```
Frontend requests:
  GET /api/v1/market-data/{instrument}/{timeframe}
    <- { candles: CandleBar[], ichimoku: IchimokuSeries[] }

  GET /api/v1/charts/annotations/{backtest_id}
    <- { trade_markers: TradeMarker[], strategy_annotations: StrategyAnnotation[] }

Frontend renders:
  candle-mapper.ts -> KLineChart data format
  IchimokuOverlay.ts -> registerIndicator() rendering
  TradeMarkers.ts -> createOverlay() rendering
```

Indicators are always precomputed server-side. The frontend maps and renders; it never calculates.

## 5. Architectural Rules

These rules are enforced project-wide (from `CLAUDE.md`):

1. **Strategy logic must NOT depend on broker APIs** -- strategies receive candle data, emit signals.
2. **Broker logic must be adapter-based** -- `ExecutionAdapter` ABC in `execution/adapter.py`.
3. **Indicator calculations must be centralized in `indicators/`** -- no inline computation.
4. **Analytics must NOT be embedded inside strategies** -- metrics computed separately in `backtester/metrics.py`.
5. **Risk logic must NOT be duplicated in strategies** -- centralized in `risk/engine.py`.
6. **Frontend must NOT contain trading logic** -- display and controls only.
7. **Backtest and paper mode share execution logic** -- via shared strategy framework.

## 6. Non-Negotiables

- Paper first -- no live execution in V1.
- No plaintext credentials -- env vars only (`FIBOKEI_JWT_SECRET`, `FIBOKEI_USER_*_PASSWORD`).
- All signals evaluated on closed candles only.
- Timestamps stored in UTC.
- Deterministic backtest results.
- Minimum 80 trades for primary ranking.
- Portfolio-aware risk controls mandatory.

## 7. Environment Variables

| Variable | Purpose |
|----------|---------|
| `FIBOKEI_JWT_SECRET` | JWT signing secret (required) |
| `FIBOKEI_USER_JOE_PASSWORD` | Seeded user password |
| `FIBOKEI_USER_TOM_PASSWORD` | Seeded user password |
| `FIBOKEI_DATABASE_URL` | Database connection string (default: `sqlite:///fibokei.db`) |
| `FIBOKEI_CORS_ORIGINS` | Comma-separated allowed origins |
| `FIBOKEI_LIVE_EXECUTION_ENABLED` | Enable IG demo execution (default: `false`) |
| `FIBOKEI_IG_PAPER_MODE` | IG adapter uses demo account (default: `true`) |
| `FIBOKEI_IG_API_KEY` | IG demo API key (required if IG enabled) |
| `FIBOKEI_IG_USERNAME` | IG demo username (required if IG enabled) |
| `FIBOKEI_IG_PASSWORD` | IG demo password (required if IG enabled) |
| `FIBOKEI_IG_ACCOUNT_ID` | IG sub-account ID (optional) |
| `NEXT_PUBLIC_API_URL` | Frontend -> backend URL (default: `http://localhost:8000`) |

## 8. Related Documents

- [Frontend Architecture](frontend_architecture.md)
- [Charting Specification](charting_spec.md)
- [API Contracts](api_contracts.md)
- [Authentication Specification](auth_spec.md)
- [Phase 5 Design Doc](plans/2026-03-07-phase5-web-platform-design.md)
