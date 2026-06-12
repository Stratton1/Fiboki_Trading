# IG Demo Execution Evidence

## 2026-06-12 — Repair deployment (commit 11477f5, Railway deployment cce97640)

### Pre-fix deployed behaviour (deployment ad79c302, evidence from Railway Deploy Logs)

- Every `GET /prices/{epic}/{resolution}/200` → **404 Not Found**, all instruments, weeks of log history. Worker logged "IG live feed failed … falling back to yfinance" on every cycle.
- Gold/silver orders: `POST /positions/otc → 403`, persisted rejection_reason `'unauthorised access, apiUser has no access to the relevant exchange. Epic=CS.D.USCGC.TODAY.IP exchangeId=FX_BET_ALL'` (examples: Jun 5 17:11, Jun 7 22:10, Jun 8 06:10, Jun 9 08:10/18:10/20:10, Jun 10 18:10/19:10, Jun 11 13:10/19:11/22:10, Jun 12 00:10/11:10/13:10 UTC).
- Stopless orders reaching IG: e.g. `IG place_order: epic=CS.D.USDCHF.CFD.IP dir=BUY size=20.00 stop=0.0 limit=0.0` (bot e01595a5), same pattern bots d62888c1, 49f9b893.
- FX/index orders with stops were submitted and confirmed (no confirmation-retry failures in logs).

### Post-fix deployed behaviour (deployment cce97640, ACTIVE 2026-06-12 15:15 BST)

- `GET /api/v1/health` → `{"status":"ok"}` after deploy.
- **All observed IG price requests → HTTP 200 OK** (15:16:41–15:16:43 BST):
  - `CS.D.USDCHF.CFD.IP/HOUR/200 → 200`
  - `IX.D.SPTRD.IFD.IP/HOUR/200 → 200`, `HOUR_4 → 200`
  - `IX.D.HANGSENG.IFD.IP/HOUR/200 → 200`, `MINUTE_15 → 200`
  - `CS.D.USCGC.TODAY.IP/HOUR/200 → 200` (gold data readable; dealing access remains the 403 question)
  - `IX.D.ASX.IFD.IP/MINUTE_30 + HOUR → 200`
  - `CS.D.AUDCHF.CFD.IP/MINUTE_30 → 200`
- Worker now consumes IG CFD-accurate candles for live monitoring (the yfinance-fallback warnings stopped).

### Awaiting first triggering signal (deployed, test-verified, not yet log-verified)

- Runtime epic remap + duplicate-safe retry on 403 "no access to the relevant exchange" — fires on next gold/silver signal.
- `MISSING_STOP` pre-submission rejection — fires on next stopless-bot signal (bots e01595a5, d62888c1, 49f9b893 evaluate hourly).

### Outstanding for Gate 2 (full EURUSD H1 lifecycle proof)

open → confirm → reconcile → **amend** → **close** → reconcile-after-restart, with audit-trail screenshots. Submission+confirmation legs are already evidenced above; amendment/closure legs and restart drill still need a deliberate controlled run.
