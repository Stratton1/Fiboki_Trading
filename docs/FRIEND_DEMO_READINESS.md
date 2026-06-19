# Friend-Demo Readiness Checklist

**Date:** 2026-06-19
**Goal:** a clean, credible narrative — not a perfect platform.

## The demo narrative
1. Dashboard → 2. IG demo balance pulled into Fiboki → 3. the real Spot Gold
trade → 4. trade history shows broker execution (ref `SBQLDCAC`) → 5. chart
overlay of entry/exit → 6. the bot/strategy behind it → 7. fleet overview →
8. research/backtest concept → 9. roadmap for autonomous evolution.

## Status
| Item | Status | Note |
|------|--------|------|
| IG demo balance in Fiboki | ✅ | Live, refreshes 60s, sizes off real £20k |
| IG demo actually trades | ✅ | Gold fill confirmed (audit row 359) |
| Real IG reject reason captured | ✅ | Adapter now persists full IG confirmation (was "UNKNOWN") |
| Phase reset zeroes daily/weekly PnL | ✅ | Shipped + worker reload |
| Stale-bot recovery action | 🟡 | `restore-stale` endpoint shipped; UI panel + worker auto-heal pending |
| IG trade in Trade History (ref `SBQLDCAC`) | ⛔ | **Top gap** — reconciliation importer needed (plan written) |
| Recent-execution panel on dashboard | ⛔ | Wave 2 |
| Chart entry/exit overlays | ⛔ | Wave 4 |
| Short/sell PnL display correct | ⛔ | Backend PnL is direction-correct; audit UI/Telegram presentation |
| Clickable dashboard cards/drawers | ⛔ | Wave 2 |

## Minimum to be "friend-ready" (in priority order)
1. IG trade appears in Trade History with broker ref + PnL (Wave 1).
2. Recent-execution panel on dashboard (Wave 2).
3. Chart entry/exit markers for the IG trade (Wave 4 subset).
4. Phase-scoped dashboard stats ✅ (reset fixed) — finish the toggle.
5. Short/sell PnL presentation audit (Wave 6).

## Honest line for the demo
"The broker loop is proven — Fiboki connected to IG, placed a trade, and it
filled. We're now wiring that execution into the product surface (trade history,
charts, analytics) so every trade is visible and explainable end-to-end."
