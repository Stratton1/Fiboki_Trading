# Fiboki — Operations Runbook

Version: 1.1
Last Updated: 2026-03-12

---

## Service Architecture

| Service | Host | URL |
|---------|------|-----|
| Frontend | Vercel | https://fiboki.uk |
| Backend API | Railway | https://api.fiboki.uk |
| Database | Railway PostgreSQL | (internal) |

## Health Checks

### Quick Health

```bash
curl https://api.fiboki.uk/api/v1/health
# Expected: {"status": "ok"}
```

### Full Status (authenticated)

```bash
# Login
TOKEN=$(curl -s -X POST https://api.fiboki.uk/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"joe","password":"..."}' | jq -r '.access_token')

# Status
curl -s https://api.fiboki.uk/api/v1/system/status \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## CI/CD Pipeline

### GitHub Actions (`.github/workflows/ci.yml`)

Triggers on every push/PR to `main`:

1. **Lint** — `ruff check src/`
2. **Test** — `pytest -v --tb=short`
3. **Frontend build** — `npm run build`
4. **Smoke test** — Health check after merge to main

### Railway Auto-Deploy

Railway is configured to auto-deploy from the `main` branch. After merging a PR:

1. Railway detects the push to `main`
2. Builds Docker image from `Dockerfile`
3. Deploys to `api.fiboki.uk`
4. GitHub Actions smoke job verifies health endpoint

### Vercel Auto-Deploy

Vercel auto-deploys the frontend from the `main` branch:

1. Vercel detects push to `main`
2. Builds Next.js from `frontend/`
3. Deploys to `fiboki.uk`

---

## Environment Variables

### Required (production)

| Variable | Validation |
|----------|------------|
| `FIBOKEI_DATABASE_URL` or `DATABASE_URL` | Must be set; PostgreSQL connection string |
| `FIBOKEI_JWT_SECRET` | Must be set; min 32 characters recommended |
| `FIBOKEI_CORS_ORIGINS` | Must be set; comma-separated origins |

Missing any of these causes the backend to fail on startup with a clear error message.

### Optional

| Variable | Default | Notes |
|----------|---------|-------|
| `FIBOKEI_LOCAL_DEV` | unset | Set to any value for local dev mode |
| `FIBOKEI_DATA_DIR` | auto-detected | Override data directory |
| `FIBOKEI_LIVE_EXECUTION_ENABLED` | `false` | Enable IG demo mode |
| `FIBOKEI_IG_PAPER_MODE` | `true` | Use IG demo API |

---

## Database

### Backup Strategy

Railway Hobby plan does **not** include automated PostgreSQL backups. Backups are
operator-initiated using `pg_dump` from a local terminal.

**Important:** Railway injects two database connection strings:

| Variable | Hostname | Reachable from |
|----------|----------|----------------|
| `DATABASE_URL` | `postgres.railway.internal` | Railway services only |
| `DATABASE_PUBLIC_URL` | `*.proxy.rlwy.net:<port>` | Internet (local terminal) |

Local `pg_dump` commands **must** use `DATABASE_PUBLIC_URL`. Using `DATABASE_URL`
will fail with a DNS resolution error (`could not translate host name`).

### Manual Backup (from local terminal)

```bash
# 1. Extract the public connection string
export DB_PUBLIC=$(railway variables --json | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('DATABASE_PUBLIC_URL',''))")

# 2. Create the backup (use version-matched pg_dump — server is Postgres 17)
/opt/homebrew/opt/postgresql@17/bin/pg_dump "$DB_PUBLIC" > fiboki_backup_$(date +%Y%m%d).sql

# 3. Verify
ls -lh fiboki_backup_*.sql
head -20 fiboki_backup_$(date +%Y%m%d).sql
# Should show SQL: SET statements, CREATE TABLE, COPY data
```

**Prerequisites:** Railway CLI installed and linked (`railway link`), `pg_dump`
version-matched to the server (`brew install postgresql@17`). The default
`pg_dump` from `brew install postgresql` may be too old and will abort with a
version mismatch error.

### When to Back Up

- Before every deployment that includes database model changes
- Before running destructive maintenance (schema drops, bulk deletes)
- Weekly as a routine safety measure

### Restore (disaster recovery only)

```bash
# DANGER: This overwrites the production database.
# Only use for disaster recovery after confirming the backup is valid.
#
# /opt/homebrew/opt/postgresql@17/bin/psql "$DB_PUBLIC" < fiboki_backup_YYYYMMDD.sql
```

### Future Improvements

When moving to Railway Pro or a dedicated Postgres provider:

- Enable automated daily backups with 7-day retention
- Add off-site backup (S3/R2) via scheduled cron service
- Add backup verification job that restores to a scratch database

### Schema Migrations

The application uses `create_all()` on startup — tables are created if they don't exist. For schema changes:

1. Modify models in `backend/src/fibokei/db/models.py`
2. Test locally with fresh SQLite database
3. For production: use Alembic migrations if columns need to be altered/dropped

---

## Logging

### Format

- **Production**: JSON structured logging (`{"time":"...","level":"...","logger":"...","message":"..."}`)
- **Local dev**: Human-readable (`2026-03-09 12:00:00 INFO [fibokei.startup] ...`)

### Request Logging

Every HTTP request is logged with:
- Method, path, status code, duration (ms)
- Request ID (`X-Request-ID` header, auto-generated if not provided)

### Key Loggers

| Logger | Purpose |
|--------|---------|
| `fibokei.startup` | Application initialization |
| `fibokei.http` | HTTP request/response logging |
| `fibokei.execution` | Trade execution events |

---

## Incident Response

### Kill Switch

Emergency stop for all trading execution:

```bash
# Activate
curl -X POST https://api.fiboki.uk/api/v1/execution/kill-switch/activate \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Emergency stop"}'

# Deactivate
curl -X POST https://api.fiboki.uk/api/v1/execution/kill-switch/deactivate \
  -H "Authorization: Bearer $TOKEN"
```

Also available from the System page in the frontend.

---

## Production Data: Full Canonical Dataset on Railway

### Overview

The Docker image bundles only H1 data for 7 forex majors (~2.3MB). For full production use — all 60 instruments across 6 timeframes (M1, M5, M15, M30, H1, H4) — the full canonical dataset (~961MB, 360 parquet files) must be deployed to a Railway persistent volume.

**Supported timeframes:** M1, M5, M15, M30, H1, H4
**Not supported:** M2 (no data source produces this timeframe; removed from UI)

### Step 1: Create Railway Volume

In Railway dashboard → your backend service → Settings → Volumes:

1. Click **New Volume**
2. Name: `fiboki-data`
3. Mount Path: `/data`
4. Size: 2 GB (canonical dataset is ~961MB)
5. Click **Create**

This triggers a redeploy with the volume mounted.

### Step 2: Set Environment Variable

In Railway dashboard → your backend service → Variables:

```
FIBOKEI_DATA_DIR=/data
```

This tells the backend to use `/data` as the data root instead of the Docker-bundled `/app/data`.

### Step 3: Upload Canonical Data

Railway does not support SSH or rsync directly. Use `railway shell` to get a shell inside the running container, then transfer data.

**Option A: Upload via railway shell + tar (recommended)**

From your local machine with the canonical dataset at `data/canonical/`:

```bash
# 1. Create a tarball of the canonical data
cd /path/to/Fiboki_Trading
tar czf canonical-data.tar.gz -C data/canonical histdata

# 2. Open a railway shell (requires Railway CLI linked to your project)
railway shell

# 3. Inside the shell, create the directory structure
mkdir -p /data/canonical

# 4. Exit the shell. Use railway run to copy via base64 encoding:
#    (Railway shell doesn't support file upload, so use this approach)
#    Split the tarball into chunks and decode inside the container

# Alternative: use Railway's volume with a one-time data loader service
```

**Option B: One-time data loader service (most reliable)**

1. Create a temporary Railway service in the same project
2. Mount the same volume (`fiboki-data`) at `/data`
3. Use a Dockerfile that copies your local canonical data:

```dockerfile
FROM python:3.11-slim
WORKDIR /upload
COPY data/canonical/ /data/canonical/
CMD ["echo", "Data uploaded successfully"]
```

4. Deploy this service once — it writes the canonical data to the shared volume
5. Delete the temporary service after verifying the data is mounted

**Option C: Bundle canonical in the main Docker image (simplest but largest)**

If volume upload is impractical, bundle the full dataset directly:

```dockerfile
# In backend/Dockerfile, add before the CMD:
COPY ../data/canonical/ data/canonical/
```

This increases the Docker image from ~200MB to ~1.2GB but eliminates volume management.
Do NOT set `FIBOKEI_DATA_DIR` with this approach — the auto-detection finds `/app/data/`.

### Step 4: Generate Manifest

After canonical data is on the volume, generate the manifest:

```bash
railway run python -m fibokei manifest generate
```

Or via the API (authenticated):

```bash
curl -X POST https://api.fiboki.uk/api/v1/data/manifest/refresh \
  -H "Authorization: Bearer $TOKEN"
# Expected: {"status": "ok", "datasets": 360}
```

### Step 5: Verify Production Data

```bash
# 1. Check manifest (should return 360 datasets)
curl -s https://api.fiboki.uk/api/v1/data/manifest \
  -H "Authorization: Bearer $TOKEN" | jq '.datasets | length'

# 2. Check EURUSD across all timeframes
for TF in M1 M5 M15 M30 H1 H4; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://api.fiboki.uk/api/v1/market-data/EURUSD/$TF?limit=10" \
    -H "Authorization: Bearer $TOKEN")
  echo "EURUSD/$TF: $STATUS"
done
# Expected: all 200

# 3. Check data source label (should say canonical/histdata, not starter)
curl -s "https://api.fiboki.uk/api/v1/market-data/EURUSD/H1?limit=1" \
  -H "Authorization: Bearer $TOKEN" | jq '.source'
# Expected: "canonical/histdata"

# 4. Check data availability for a non-starter instrument
curl -s "https://api.fiboki.uk/api/v1/data/check/XAUUSD/H1" \
  -H "Authorization: Bearer $TOKEN" | jq .
# Expected: {"available": true, "rows": ..., "start": "...", "end": "..."}
```

### Expected Directory Structure on Volume

```
/data/                          ← FIBOKEI_DATA_DIR
└── canonical/
    └── histdata/
        ├── eurusd/
        │   ├── eurusd_m1.parquet
        │   ├── eurusd_m5.parquet
        │   ├── eurusd_m15.parquet
        │   ├── eurusd_m30.parquet
        │   ├── eurusd_h1.parquet
        │   └── eurusd_h4.parquet
        ├── gbpusd/
        │   └── (same 6 files)
        ├── ... (60 instruments total)
        └── manifest.json         ← generated by fibokei manifest
```

### Timeframe Coverage

| Timeframe | Canonical Data | Production Status |
|-----------|---------------|-------------------|
| M1 | Yes (60 instruments) | Available once volume is mounted |
| M2 | **No** — no data source produces M2 | **Not supported** — removed from UI |
| M5 | Yes (60 instruments) | Available once volume is mounted |
| M15 | Yes (60 instruments) | Available once volume is mounted |
| M30 | Yes (60 instruments) | Available once volume is mounted |
| H1 | Yes (60 instruments) | Available now via starter (7 majors) |
| H4 | Yes (60 instruments) | Available once volume is mounted |

---

### Common Issues

| Symptom | Cause | Resolution |
|---------|-------|------------|
| 500 on all endpoints | Database connection failed | Check `DATABASE_URL` in Railway |
| 401 on login | JWT secret changed | Ensure `FIBOKEI_JWT_SECRET` matches |
| Charts page empty | No data files | Verify `data/starter/` exists in Docker image |
| CORS errors | Origin not whitelisted | Add origin to `FIBOKEI_CORS_ORIGINS` |
| Startup crash | Missing required env var | Check error message for which var |
