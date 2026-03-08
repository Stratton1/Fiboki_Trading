# Fiboki — Production Deployment Architecture

Version: 1.1
Last Updated: 2026-03-07
Domain: **fiboki.uk**

---

## Approved Architecture

```
                    ┌─────────────────┐
                    │   Custom Domain  │
                    │    fiboki.uk     │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
     ┌────────▼────────┐          ┌─────────▼─────────┐
     │     Vercel       │          │  Railway / Render  │
     │   (Frontend)     │          │    (Backend)       │
     │   Next.js        │  HTTPS   │    FastAPI         │
     │   fiboki.uk      │─────────▶│    api.fiboki.uk   │
     └─────────────────┘          └─────────┬──────────┘
                                            │
                                   ┌────────▼────────┐
                                   │   PostgreSQL     │
                                   │   (Managed)      │
                                   └─────────────────┘
```

### Principles

- **Always-on.** Platform runs on managed cloud services. No dependency on any laptop or local machine.
- **No Docker** unless a future requirement demands it. Railway and Render both support native Python deployments.
- **Frontend and backend are separate services** on separate hosts, communicating via HTTPS API calls.
- **Railway is the preferred backend host**, with Render as a tested fallback.
- **Database is managed PostgreSQL** attached to whichever backend host is active.
- **Long-running work** (paper bots, scheduled jobs) runs on a separate worker service alongside the API, not on the frontend host.

---

## Services

### 1. Frontend — Vercel

| Item | Value |
|------|-------|
| Framework | Next.js 16 + TypeScript |
| Root directory | `frontend/` |
| Build command | `npm run build` |
| Output | `.next/` (automatic) |
| Domain | `fiboki.uk` + `www.fiboki.uk` |

**Environment variables (set in Vercel dashboard):**

| Variable | Value | Where |
|----------|-------|-------|
| `NEXT_PUBLIC_API_URL` | `https://api.fiboki.uk` | All environments |

The frontend is a static/SSR app that calls the backend API. It contains **zero** trading logic, scheduling, or long-running processes.

### 2. Backend API — Railway (preferred) / Render (fallback)

| Item | Value |
|------|-------|
| Runtime | Python 3.11+ (native, no Docker) |
| Framework | FastAPI + Uvicorn |
| Root directory | `backend/` |
| Start command | `uvicorn fibokei.api.app:app --host 0.0.0.0 --port $PORT` |
| Build command | `pip install -e .` |
| Health check | `GET /api/v1/health` |
| Domain | `api.fiboki.uk` |

**Environment variables (set in hosting dashboard):**

| Variable | Purpose | Required |
|----------|---------|----------|
| `FIBOKEI_DATABASE_URL` | PostgreSQL connection string | Yes |
| `FIBOKEI_JWT_SECRET` | JWT signing key (min 32 chars) | Yes |
| `FIBOKEI_CORS_ORIGINS` | `https://fiboki.uk,https://www.fiboki.uk` | Yes |
| `FIBOKEI_USER_JOE_PASSWORD` | Joe's login password | Yes |
| `FIBOKEI_USER_TOM_PASSWORD` | Tom's login password | Yes |
| `FIBOKEI_TELEGRAM_BOT_TOKEN` | Telegram bot token | Optional |
| `FIBOKEI_TELEGRAM_CHAT_ID` | Telegram chat ID | Optional |

**Environment behaviour:**

- When `FIBOKEI_LOCAL_DEV` is set (any truthy value), the backend runs in local dev mode:
  - CORS allows `http://localhost:3000`
  - Cookies use `SameSite=Lax`, `Secure=False`
- When `FIBOKEI_LOCAL_DEV` is **not set** (the default on all cloud hosts), the backend runs in production mode:
  - CORS only allows origins listed in `FIBOKEI_CORS_ORIGINS`
  - Cookies use `SameSite=None`, `Secure=True` (required for cross-origin cookie auth)

### 3. Database — Managed PostgreSQL

| Item | Value |
|------|-------|
| Provider | Railway PostgreSQL plugin / Render PostgreSQL |
| Engine | PostgreSQL 15+ |
| Schema management | SQLAlchemy `create_all` on startup |

The database URL is injected automatically by the hosting platform. SQLAlchemy handles the `postgres://` → `postgresql://` prefix normalisation that some providers require.

### 4. Worker Service — Paper Bot Orchestration (future)

| Item | Value |
|------|-------|
| Runtime | Same Python codebase |
| Start command | `python -m fibokei.worker` (to be built) |
| Purpose | Long-running paper bot loops, signal evaluation |

Currently, paper bots run in-process with the API server. For V1 this is acceptable. When scaling, split into a dedicated worker that:

- Reads bot configs from the database
- Runs signal evaluation loops on closed candles
- Writes trade records back to the database
- Sends Telegram alerts

### 5. Cron / Scheduled Jobs (future)

| Job | Schedule | Purpose |
|-----|----------|---------|
| `refresh-data` | Daily 00:30 UTC | Fetch latest OHLCV from Yahoo Finance |
| `daily-summary` | Daily 18:00 UTC | Telegram summary of paper trading performance |
| `housekeeping` | Weekly Sunday 03:00 | Clean old backtest results, compact logs |

Railway supports cron jobs natively. Render supports cron via "Cron Jobs" service type. Both run a command on schedule — no custom scheduler needed.

---

## Cross-Origin Auth Flow

Frontend (`fiboki.uk`) and backend (`api.fiboki.uk`) are on different origins. Cookie-based auth across origins requires:

1. **Backend CORS** allows the frontend origin with `credentials: true`
2. **Backend cookies** set `SameSite=None; Secure; HttpOnly`
3. **Frontend fetch** uses `credentials: "include"` on every request
4. **HTTPS on both sides** (mandatory for `Secure` cookies)

This is already implemented. The `FIBOKEI_LOCAL_DEV` flag switches to `SameSite=Lax; Secure=false` for local development where both run on localhost.

---

## Deployment Configs in Repo

| File | Purpose |
|------|---------|
| `railway.json` | Railway service configuration (preferred) |
| `render.yaml` | Render Blueprint (fallback) |
| `backend/Procfile` | Heroku-compatible start command (used by some platforms) |
| `frontend/.env.local.example` | Local dev env template |

No Docker. No Kubernetes. No Terraform. Keep it simple and managed.

---

## Domain Routing

| Domain | Service | Provider |
|--------|---------|----------|
| `fiboki.uk` | Frontend (Next.js) | Vercel |
| `www.fiboki.uk` | Frontend (redirect/alias) | Vercel |
| `api.fiboki.uk` | Backend (FastAPI) | Railway / Render |

Both Vercel and Railway support custom domains with automatic SSL. Configure DNS:

```
fiboki.uk        →  A      →  76.76.21.21 (Vercel)
www.fiboki.uk    →  CNAME  →  cname.vercel-dns.com
api.fiboki.uk    →  CNAME  →  <railway-provided-domain>
```
