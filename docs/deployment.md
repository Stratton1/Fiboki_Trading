# Fiboki — Production Deployment Architecture

Version: 1.2
Last Updated: 2026-03-09
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
- **Docker on Railway.** The backend deploys via `Dockerfile` on Railway. Render uses native Python (`render.yaml`).
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
| Runtime | Python 3.11+ (Docker on Railway, native on Render) |
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
| `FIBOKEI_LIVE_EXECUTION_ENABLED` | Enable IG demo execution (`true`/`false`) | No (default: `false`) |
| `FIBOKEI_IG_PAPER_MODE` | IG adapter uses demo account (`true`/`false`) | No (default: `true`) |
| `FIBOKEI_IG_API_KEY` | IG API key (from IG demo account settings) | Only if IG demo enabled |
| `FIBOKEI_IG_USERNAME` | IG demo account username | Only if IG demo enabled |
| `FIBOKEI_IG_PASSWORD` | IG demo account password | Only if IG demo enabled |
| `FIBOKEI_IG_ACCOUNT_ID` | IG sub-account ID (for multi-account users) | Optional |

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

The database URL is injected automatically by the hosting platform. Railway and Render inject `DATABASE_URL` by default. The app checks `FIBOKEI_DATABASE_URL` first, then falls back to `DATABASE_URL`, then defaults to SQLite. This means Railway's auto-injected PostgreSQL URL is picked up automatically without additional configuration. SQLAlchemy handles the `postgres://` → `postgresql://` prefix normalisation that some providers require.

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

## IG Demo Integration

### Safety Architecture

The IG integration is **demo-only** by design. Multiple layers prevent accidental live trading:

1. **Hard URL block** — `IGClient` refuses to connect to `api.ig.com` (production). Only `demo-api.ig.com` is allowed.
2. **Feature flags** — `FIBOKEI_LIVE_EXECUTION_ENABLED` defaults to `false`. When `false`, the system uses the paper adapter exclusively.
3. **Kill switch** — A database-persisted emergency stop. When active, operators can halt all execution via API or frontend.
4. **Startup validation** — The backend logs a warning if IG demo mode is enabled but credentials are missing.

### Execution Modes

| `FIBOKEI_LIVE_EXECUTION_ENABLED` | `FIBOKEI_IG_PAPER_MODE` | Effective Mode | Adapter Used |
|-----------------------------------|--------------------------|----------------|---------------|
| `false` (default) | any | `paper` | PaperExecutionAdapter |
| `true` | `true` (default) | `ig_demo` | IGExecutionAdapter (demo API) |
| `true` | `false` | blocked | IGExecutionAdapter refuses — production URL is hard-blocked |

### IG Demo Go-Live Checklist

Before enabling IG demo execution on Railway:

1. **Create IG demo account** at https://demo-api.ig.com — note your API key, username, password
2. **Set Railway env vars:**
   ```
   FIBOKEI_LIVE_EXECUTION_ENABLED=true
   FIBOKEI_IG_PAPER_MODE=true
   FIBOKEI_IG_API_KEY=<your-ig-demo-api-key>
   FIBOKEI_IG_USERNAME=<your-ig-demo-username>
   FIBOKEI_IG_PASSWORD=<your-ig-demo-password>
   FIBOKEI_IG_ACCOUNT_ID=<optional-sub-account-id>
   ```
3. **Redeploy backend** — check logs for `Execution mode: ig_demo — IG demo credentials configured`
4. **Verify via API:**
   - `GET /api/v1/execution/mode` → `{"mode": "ig_demo", "live_execution_enabled": true, "ig_paper_mode": true, "kill_switch_active": false}`
   - `GET /api/v1/system/status` → should show `execution_mode: "ig_demo"`
5. **Verify kill switch:**
   - `POST /api/v1/execution/kill-switch/activate` with `{"reason": "test"}` → confirm `is_active: true`
   - `POST /api/v1/execution/kill-switch/deactivate` → confirm `is_active: false`
6. **Check audit log:** `GET /api/v1/execution/audit` → should return empty list (no executions yet)

### Recommended First-Launch Subset

Start with a single forex major (e.g. EURUSD) on a single timeframe (H1) using one strategy (bot01_sanyaku). Verify:
- Order placement returns a deal reference and confirmation
- Position appears in `get_positions()` response
- Position can be closed
- All actions appear in the execution audit log

### Reverting to Paper Mode

Set `FIBOKEI_LIVE_EXECUTION_ENABLED=false` in Railway and redeploy. No other changes needed — the system defaults back to the paper adapter.

---

## Starter Dataset

The Docker image bundles a lightweight **starter dataset** (~2.3MB) at `data/starter/histdata/` containing H1 parquet files for 7 forex majors: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, NZDUSD.

This ensures charts, backtests, and research work out of the box in production without requiring the full canonical dataset (961MB, 360 files).

**Data resolution order** (handled by `load_canonical()`):

1. `data/canonical/dukascopy/{symbol}/` — validation-grade data
2. `data/canonical/histdata/{symbol}/` — bulk research data
3. `data/starter/histdata/{symbol}/` — production starter (bundled in Docker)
4. `data/fixtures/sample_{symbol}_{tf}.csv` — legacy demo fallback

**To add more instruments or timeframes:**

1. Copy the parquet file from `data/canonical/histdata/{symbol}/` to `data/starter/histdata/{symbol}/`
2. Commit and redeploy — the Dockerfile copies `data/starter/` into the image

**Override data directory** (optional): Set `FIBOKEI_DATA_DIR` env var to point to a custom data root directory. The system will look for `canonical/`, `starter/`, and `fixtures/` subdirectories within it.

---

## Railway Persistent Volume

For production with the full canonical dataset (961MB, 60 instruments × 6 timeframes), use a Railway persistent volume. See **`docs/operations.md` → "Production Data: Full Canonical Dataset on Railway"** for detailed operator instructions.

### Quick Reference

1. Railway dashboard → Backend Service → Settings → Volumes → New Volume: name `fiboki-data`, mount at `/data`
2. Set env var: `FIBOKEI_DATA_DIR=/data`
3. Upload canonical parquet files to `/data/canonical/histdata/{symbol}/`
4. Generate manifest: `railway run python -m fibokei manifest generate` or `POST /api/v1/data/manifest/refresh`
5. Verify: `GET /api/v1/data/manifest` should return 360 datasets

### Supported Timeframes

M1, M5, M15, M30, H1, H4 — all produced by the HistData canonical pipeline.
**M2 is not supported** — no data source produces it.

### Data Resolution Order

1. `canonical/dukascopy/` — validation-grade
2. `canonical/histdata/` — bulk research
3. `starter/histdata/` — Docker-bundled fallback (H1 only, 7 majors)
4. `fixtures/` — legacy

### Fallback Behavior

If the volume is not mounted or `FIBOKEI_DATA_DIR` is unset, the backend falls back to the starter dataset bundled in the Docker image. Deployments work out of the box — the volume is an enhancement, not a requirement.

---

## Deployment Configs in Repo

| File | Purpose |
|------|---------|
| `Dockerfile` | Docker build for Railway deployment |
| `railway.json` | Railway service configuration (preferred) |
| `render.yaml` | Render Blueprint (fallback, native Python) |
| `frontend/.env.local.example` | Local dev env template |

No Kubernetes. No Terraform. Keep it simple and managed.

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
