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

## 2026-06-12 evening — Gate 2 attempt chain (deployed worker console, real IG demo)

1. Dedicated worker service live: `ExecutionRouter built: mode=legacy_single targets=[ig:demo(on)]`, 21 bots recovered; API logs `in-process worker thread disabled` → exactly one worker (Gate 1 core).
2. EPIC diag: XAUUSD static epic readable; account switch to Z5ZAV → 401 `error.security.account-token-invalid`, fallback to default Z5ZAW; failed switch poisons session tokens (serial 401s) — explains intermittent 401s in production logs.
3. Lifecycle attempt 1 (poisoned session): aborted safely — MARKET_DETAILS_UNAVAILABLE. No blind order. Gate works on real IG.
4. Config fix: FIBOKEI_IG_ACCOUNT_ID → Z5ZAW. Auth then clean (no 401s).
5. Lifecycle attempt 2: STOP_TOO_TIGHT rejection — exposed IG FX CFD price scale (EURUSD bid 13050.9, onePipMeans=1): classic-scale stops are 1e4 too small. Complete explanation of historic naked orders. Diag fixed to IG-native distances.
6. Lifecycle attempt 3 (clean session, valid order, stop 26.1 pts): IG 403 `unauthorised access, apiUser has no access to the relevant exchange. Epic=CS.D.EURUSD.CFD.IP exchangeId=FX_EURUSD_ST`.

**Conclusion:** default account Z5ZAW has data access but NO dealing rights; dealing account is Z5ZAV but the account-switch endpoint rejects with token-invalid. Gate 2 is blocked on IG account entitlements — an operator action in IG's dashboard (verify Z5ZAV is active and the API key is attached to it), or an account-switch repair (capture-and-replace token semantics) once entitlements are confirmed. All failure modes ended in safe rejection; zero naked or duplicate orders.

## 2026-06-12 late — Gate 2 chain fully unblocked (technical)

7. Account enumeration (read-only): **Z5ZAV = Demo-CFD (ENABLED)**, **Z5ZAW = Demo-Spread bet (preferred)**. The original FIBOKEI_IG_ACCOUNT_ID=Z5ZAV was correct; reverted.
8. Raw console experiment: PUT /session switch to Z5ZAV → **200, dealingEnabled=true** when sent without the shared client's cookie jar; identical request through the client 401s. Root cause: IG login cookies in the shared httpx.Client conflict with header tokens on switch. Fixed (one-shot request + cookie-jar clear, commit 528a224).
9. Post-fix lifecycle: clean session on Z5ZAV, no 401s, no switch failure. New defect caught safely: CFD-account onePipMeans='0.0001 USD/EUR' (unit suffix) broke float() → MARKET_DETAILS_UNAVAILABLE abort. Parser fixed (commit ebf8d9c).
10. Final run: full chain healthy — auth ✓ switch ✓ spec parsed ✓ — **aborted on marketStatus=EDITS_ONLY** (Friday pre-weekend dealing restriction). Market-status gate verified on real IG.

**Status: Gate 2 is technically unblocked. The remaining step is a market-hours window** (FX reopens Sunday ~22:05 BST). One command completes the proof from the worker console:

    python -m fibokei.diag lifecycle --confirm-demo-order
