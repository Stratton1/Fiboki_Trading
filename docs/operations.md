# Fiboki — Operations Runbook

Version: 1.0
Last Updated: 2026-03-09

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

Railway PostgreSQL supports automated backups. To configure:

1. Open Railway dashboard → PostgreSQL plugin → Settings
2. Enable automated backups (daily recommended)
3. Set retention period (7 days minimum)

### Manual Backup

```bash
pg_dump $DATABASE_URL > fibokei_backup_$(date +%Y%m%d).sql
```

### Restore

```bash
psql $DATABASE_URL < fibokei_backup_YYYYMMDD.sql
```

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

### Common Issues

| Symptom | Cause | Resolution |
|---------|-------|------------|
| 500 on all endpoints | Database connection failed | Check `DATABASE_URL` in Railway |
| 401 on login | JWT secret changed | Ensure `FIBOKEI_JWT_SECRET` matches |
| Charts page empty | No data files | Verify `data/starter/` exists in Docker image |
| CORS errors | Origin not whitelisted | Add origin to `FIBOKEI_CORS_ORIGINS` |
| Startup crash | Missing required env var | Check error message for which var |
