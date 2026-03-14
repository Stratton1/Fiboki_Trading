# Live Readiness Report — Phase 18

**Date:** 14 March 2026, UK time
**Verdict:** **GO**
**Backend tests:** 615 passed (139s)
**Frontend build:** Clean (compiled in 5.8s, 17 routes)
**Outstanding:** T-18.3.03 (workspace save/restore) — NOT implemented, deferred by design

---

## Evidence Checklist

| # | Check | Result | Evidence |
|---|-------|--------|----------|
| 1 | `pytest -v` — all 615 pass | PASS | 615 passed in 139.02s, zero failures |
| 2 | `npm run build` — clean compile | PASS | Compiled successfully, 17 routes generated |
| 3 | `StrategyVariantModel` exists with correct fields | PASS | `models.py:384` — id, strategy_id, name, params (JSON), is_active, backtest_run_id, trade_overlap, created_at; UniqueConstraint on (strategy_id, name) |
| 4 | Variant CRUD in repository.py | PASS | `repository.py:1069-1140` — create_variant, list_variants, get_variant, update_variant, delete_variant |
| 5 | `variation.py` module exists with all 5 functions | PASS | get_strategy_params, get_param_ranges, generate_variants, run_variant, check_overlap |
| 6 | Variations API routes registered | PASS | `app.py:348-349` — variations_router mounted at `/api/v1` |
| 7 | 7 variation endpoints wired | PASS | GET/POST /variations, GET/PATCH/DELETE /variations/{id}, GET /variations/params/{strategy_id}, POST /variations/generate |
| 8 | Fleet risk methods on RiskEngine | PASS | `engine.py` — check_fleet_trade_allowed (line 118), compute_trade_overlap (146), find_correlated_bots (166), find_underperformers (190) |
| 9 | Fleet risk env vars in limits.py | PASS | 6 FIBOKEI_FLEET_* vars: max_bots_per_instrument (5), max_total_positions (20), max_exposure_per_instrument (6), correlation_threshold (0.85), cull_sigma (2.0), cull_min_trades (50) |
| 10 | GET /paper/fleet/risk endpoint | PASS | `paper.py:577` — returns fleet_limits, fleet_status, instrument_alerts, correlation_alerts, underperformers |
| 11 | Frontend API client — fleet + variant methods | PASS | `api.ts` — fleetRisk (280), listVariants (333), createVariant (347), deleteVariant (351), variantParams (353), generateVariants (359) |
| 12 | Exposure page — Fleet Risk Analysis panel | PASS | `exposure/page.tsx` — SWR query for fleet risk data, fleet capacity stats, instrument alerts, correlation alerts, underperformer alerts, all-clear message |
| 13 | Settings page — Fleet Risk Limits section | PASS | `settings/page.tsx` — FLEET_RISK_PARAMS array (6 items), Fleet Risk Limits card |
| 14 | test_fleet_risk.py — 15 tests | PASS | Fleet position limits, trade overlap (4 cases), correlated bots, underperformers, API integration |
| 15 | test_variations.py — 15 tests | PASS | Strategy params, param ranges, variant generation, overlap checks, API CRUD + generation |
| 16 | Roadmap Phase 18 row marked COMPLETE | PASS | `roadmap.md:96` — "COMPLETE | 615 pass" with full description |
| 17 | Blueprint updated for Phase 18 | PASS | Sections 17.2, 20.5, 20.7, 24.2 annotated with implementation details |
| 18 | T-18.3.03 workspace save/restore | SKIP | Not implemented — checkbox remains unchecked in roadmap. Deferred, not blocking. |

---

## Commands Run

```
cd backend && python3 -m pytest -v
→ 615 passed in 139.02s (0:02:19)
→ Python 3.12.3, pytest 9.0.2

cd frontend && npm run build
→ Compiled successfully in 5.8s
→ 17 routes (15 static, 2 dynamic)
→ Node v20.18.0, npm 10.8.2
```

---

## Issues Found & Fixed

| Issue | Fix |
|-------|-----|
| Roadmap header still said "Phase 17 COMPLETE, Phase 18 planned" | Updated to "Phase 18 COMPLETE — Strategy Families & Fleet Scaling. Phases 1–18 complete." |

No code fixes required. All endpoints, models, and frontend components verified against actual source.

---

## Testing Quickstart

### Prerequisites

- Backend running at `https://api.fiboki.uk` (Railway)
- Frontend running at `https://fiboki.uk` (Vercel)
- Logged in with valid credentials

### Phase 18.1 — Parameter Variation Engine

1. **View param ranges:** Navigate to the browser console or use curl:
   ```
   GET /api/v1/variations/params/bot01_sanyaku
   ```
   Expect: `strategy_id`, `params` (tenkan_period, kijun_period ranges), `constructor_params`

2. **Generate variants (dry run):**
   ```
   POST /api/v1/variations/generate
   Body: {"strategy_id": "bot01_sanyaku", "max_variants": 5}
   ```
   Expect: `count` <= 5, `variants` array of param dicts

3. **Create a variant:**
   ```
   POST /api/v1/variations
   Body: {"strategy_id": "bot01_sanyaku", "name": "fast_tenkan", "params": {"tenkan_period": 7, "kijun_period": 22}}
   ```
   Expect: 201 with id, strategy_id, name, params

4. **List variants:**
   ```
   GET /api/v1/variations
   ```
   Expect: items array containing your created variant

5. **Delete variant:**
   ```
   DELETE /api/v1/variations/{id}
   ```
   Expect: `{"deleted": <id>}`

### Phase 18.2 — Fleet-Aware Risk Controls

1. **View fleet risk dashboard:** Go to `https://fiboki.uk/exposure`
2. **Scroll to "Fleet Risk Analysis" panel** at the bottom
3. **Verify:** Fleet capacity stats (open positions / max, active bots, max bots/instrument, correlation threshold)
4. **Check alerts:** If no bots are running, expect "No fleet risk alerts. All bots operating within limits."
5. **View fleet limits in Settings:** Go to `https://fiboki.uk/settings`, scroll to "Fleet Risk Limits"
6. **Verify 6 parameters:** Max Bots/Instrument (5), Max Total Positions (20), Max Exposure/Instrument (6), Correlation Threshold (0.85), Auto-Cull Sigma (2.0), Min Trades for Cull (50)

### Phase 18.3 — Watchlists

1. **Go to Charts page** (`/charts`)
2. **Look for WatchlistPicker** component — dropdown to select/create watchlists
3. **Create a watchlist:** Click create, enter name and instrument codes
4. **Verify watchlist appears** in Backtests, Bots, and Research pages too

### Phase 18.4 — Trade Journal

1. **Go to Trades page** (`/trades`)
2. **Click a trade row** to open the trade detail page
3. **Look for Journal panel** — ability to add notes, tags, and annotations
4. **Add a journal entry** with a tag (e.g. "setup:clean", "mistake:early-exit")
5. **Return to Trades list** — verify tag column shows your tag

### API Smoke Test (curl)

```bash
# Set your token
TOKEN="your-jwt-token"

# Fleet risk
curl -s -H "Authorization: Bearer $TOKEN" https://api.fiboki.uk/api/v1/paper/fleet/risk | python3 -m json.tool

# Variant param ranges
curl -s -H "Authorization: Bearer $TOKEN" https://api.fiboki.uk/api/v1/variations/params/bot01_sanyaku | python3 -m json.tool

# Generate variants
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"strategy_id":"bot01_sanyaku","max_variants":3}' \
  https://api.fiboki.uk/api/v1/variations/generate | python3 -m json.tool
```

---

## Summary

Phase 18 is **wired end-to-end**. All 4 slices (watchlists, trade journal, fleet-aware risk, parameter variation engine) have:

- Backend models and migrations
- Repository CRUD functions
- API endpoints registered and reachable
- Frontend API client methods
- Frontend UI components rendering data
- Dedicated test coverage (615 total, all passing)
- Documentation updated to reflect shipped reality

The single outstanding item (T-18.3.03 workspace save/restore) is a convenience feature deferred by design — it does not block any other functionality.

**Verdict: GO for testing.**
