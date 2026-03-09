# Fiboki — Deployment Plan

Date: 2026-03-07
Status: Ready to execute
Reference: [deployment.md](../deployment.md)
Domain: **fiboki.uk**

---

## Step 1: Deploy Backend to Railway

1. Go to [railway.app](https://railway.app), sign in with GitHub
2. New Project → Deploy from GitHub Repo → select `Stratton1/Fiboki_Trading`
3. Railway will detect `railway.json` and configure the service
4. Add a PostgreSQL plugin to the project (click "New" → "Database" → "PostgreSQL")
5. Set environment variables in the Railway service settings:

| Variable | Value |
|----------|-------|
| `FIBOKEI_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (Railway auto-injects) |
| `FIBOKEI_JWT_SECRET` | Generate a strong random string (32+ chars) |
| `FIBOKEI_CORS_ORIGINS` | `https://fiboki.uk,https://www.fiboki.uk` |
| `FIBOKEI_USER_JOE_PASSWORD` | Choose a strong password |
| `FIBOKEI_USER_TOM_PASSWORD` | Choose a strong password |

6. Set Root Directory to `backend` in Railway service settings
7. Deploy. Wait for health check to pass at `/api/v1/health`
8. Note the public Railway URL (e.g. `fiboki-api-production.up.railway.app`)

### If using Render instead

1. Go to [render.com](https://render.com), sign in with GitHub
2. New → Blueprint → select `Stratton1/Fiboki_Trading`
3. Render reads `render.yaml` and creates the web service + PostgreSQL database
4. Set the `sync: false` env vars when prompted:
   - `FIBOKEI_CORS_ORIGINS` = `https://fiboki.uk,https://www.fiboki.uk`
   - `FIBOKEI_USER_JOE_PASSWORD` = strong password
   - `FIBOKEI_USER_TOM_PASSWORD` = strong password
5. Deploy. Wait for health check.

---

## Step 2: Verify Backend

```bash
# Replace with actual backend URL
BACKEND=https://api.fiboki.uk

# Health check
curl $BACKEND/api/v1/health
# Expected: {"status":"ok","version":"1.0.0"}

# Login
curl -X POST $BACKEND/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=joe&password=YOUR_PASSWORD" \
  -c cookies.txt
# Expected: {"access_token":"...","token_type":"bearer"}

# Authenticated endpoint
curl $BACKEND/api/v1/auth/me -b cookies.txt
# Expected: {"user_id":1,"username":"joe","role":"admin"}

# List instruments
curl $BACKEND/api/v1/instruments/ -b cookies.txt
# Expected: array of instrument objects

# List strategies
curl $BACKEND/api/v1/strategies/ -b cookies.txt
# Expected: array of strategy objects
```

---

## Step 3: Connect fiboki.uk to Vercel

1. Go to [vercel.com](https://vercel.com) → project settings → Domains
2. Add `fiboki.uk` and `www.fiboki.uk`
3. Vercel will provide DNS records to configure:
   - `fiboki.uk` → A record → `76.76.21.21`
   - `www.fiboki.uk` → CNAME → `cname.vercel-dns.com`
4. Set environment variable: `NEXT_PUBLIC_API_URL` = `https://api.fiboki.uk`
5. Redeploy the frontend

---

## Step 4: Connect api.fiboki.uk to Railway

1. Railway → project settings → Domains → Add `api.fiboki.uk`
2. Railway will provide a CNAME target
3. Set DNS: `api.fiboki.uk` → CNAME → `<railway-provided-domain>`
4. Verify: `curl https://api.fiboki.uk/api/v1/health`

---

## Step 5: Verify End-to-End

1. Open `https://fiboki.uk` in a browser
2. You should see the login page with the Fiboki logo
3. Log in with username `joe` and the password you set
4. Verify the dashboard loads with instruments and strategies
5. Open browser DevTools → Network tab:
   - API calls should go to `https://api.fiboki.uk`
   - Cookies should be set with `Secure; SameSite=None; HttpOnly`
   - No CORS errors in the console

---

## Production Verification Checklist

### Backend

- [ ] `GET /api/v1/health` returns `{"status":"ok"}`
- [ ] `GET /api/v1/system/health` returns `{"status":"ok"}`
- [ ] `POST /api/v1/auth/login` returns token and sets cookie
- [ ] Cookie has `Secure=true`, `SameSite=None`, `HttpOnly=true`
- [ ] `GET /api/v1/auth/me` works with cookie auth
- [ ] `GET /api/v1/instruments/` returns all registered instruments (67)
- [ ] `GET /api/v1/strategies/` returns 12 strategies
- [ ] Database connected (check `GET /api/v1/system/status`)
- [ ] No `localhost` in CORS response headers
- [ ] HTTPS enforced

### Frontend

- [ ] `https://fiboki.uk` loads login page
- [ ] Login succeeds and redirects to dashboard
- [ ] Dashboard loads instruments and strategies from API
- [ ] No CORS errors in browser console
- [ ] No mixed content warnings
- [ ] API calls visible in Network tab going to `api.fiboki.uk`
- [ ] Logout works and redirects to login
- [ ] Fiboki logo shows in browser tab (not Vercel icon)

### Security

- [ ] `FIBOKEI_JWT_SECRET` is unique and not committed to git
- [ ] User passwords are strong and not the default `changeme`
- [ ] No secrets in git history
- [ ] Database not publicly accessible (managed by platform)

---

## Environment Variable Summary

### Backend (Railway / Render)

| Variable | Source | Notes |
|----------|--------|-------|
| `PORT` | Platform-injected | Automatic |
| `FIBOKEI_DATABASE_URL` | Platform-injected | From PostgreSQL plugin |
| `FIBOKEI_JWT_SECRET` | Manual / generated | Random 32+ char string |
| `FIBOKEI_CORS_ORIGINS` | Manual | `https://fiboki.uk,https://www.fiboki.uk` |
| `FIBOKEI_USER_JOE_PASSWORD` | Manual | Strong password for Joe |
| `FIBOKEI_USER_TOM_PASSWORD` | Manual | Strong password for Tom |
| `FIBOKEI_TELEGRAM_BOT_TOKEN` | Manual | Optional — for alerts |
| `FIBOKEI_TELEGRAM_CHAT_ID` | Manual | Optional — for alerts |
| `FIBOKEI_LOCAL_DEV` | **Not set** | Absence = production mode |

### Frontend (Vercel)

| Variable | Source | Notes |
|----------|--------|-------|
| `NEXT_PUBLIC_API_URL` | Manual | `https://api.fiboki.uk` |
