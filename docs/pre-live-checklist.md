# Fiboki — Pre-Live Checklist

All items must be verified before enabling any form of live or demo trading.

---

## 1. Infrastructure

- [ ] Backend deployed to Railway and healthy (`GET /api/v1/health` → 200)
- [ ] Frontend deployed to Vercel and accessible at fiboki.uk
- [ ] PostgreSQL database connected and migrations applied
- [ ] CI pipeline passing (lint, test, build)
- [ ] SSL certificates valid on both domains

## 2. Authentication & Security

- [ ] `FIBOKEI_JWT_SECRET` set to a strong random value (min 32 chars)
- [ ] User passwords set via env vars (`FIBOKEI_USER_JOE_PASSWORD`, etc.)
- [ ] CORS origins restricted to `fiboki.uk` and `www.fiboki.uk`
- [ ] No plaintext credentials in codebase or Docker image

## 3. Risk Controls

- [ ] Risk engine configured with appropriate limits:
  - Max risk per trade: 1% (default)
  - Max portfolio risk: 5% (default)
  - Max open trades: 8 (default)
  - Daily hard stop: 4% (default)
  - Weekly hard stop: 8% (default)
- [ ] Kill switch functional — test activate/deactivate via API
- [ ] Drawdown limits verified with test trades

## 4. Execution Layer

- [ ] `FIBOKEI_LIVE_EXECUTION_ENABLED` explicitly set (not relying on default)
- [ ] `FIBOKEI_IG_PAPER_MODE=true` for demo trading
- [ ] IG demo credentials set and validated at startup
- [ ] Hard URL block confirmed — `api.ig.com` (production) is rejected
- [ ] Audit logging working — actions appear in `/execution/audit`

## 5. Data

- [ ] Starter dataset present in Docker image (7 forex majors H1)
- [ ] Charts page renders EURUSD H1 data
- [ ] Backtest completes successfully with starter data
- [ ] Research matrix runs against available instruments

## 6. Monitoring

- [ ] Structured logging active in production (JSON format)
- [ ] Request IDs appearing in logs
- [ ] Startup validation logging IG credential status
- [ ] Health check endpoint monitored externally (recommended)

## 7. Promotion Gates

Before promoting a bot from paper to demo:
- [ ] Bot has run for minimum 30 days in paper mode
- [ ] Bot has completed minimum 80 trades
- [ ] Zero critical errors during paper period
- [ ] Composite score >= 0.55

Before promoting from demo to live:
- [ ] Bot has run for minimum 14 days in demo mode
- [ ] Reconciliation rate >= 99.5%
- [ ] Average slippage within 2 pips tolerance
- [ ] Manual sign-off by operator

## 8. Rollback Plan

- [ ] Previous deployment tagged in git
- [ ] Database backup taken before go-live
- [ ] Kill switch tested and ready
- [ ] Rollback to paper mode documented: set `FIBOKEI_LIVE_EXECUTION_ENABLED=false` and redeploy

---

**Sign-off:**

| Name | Date | Signature |
|------|------|-----------|
| | | |
