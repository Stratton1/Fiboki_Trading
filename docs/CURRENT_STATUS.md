# Fiboki — Current Status

**Date:** 2026-06-19
**Author:** Wave 0 trust reset (verified against live repo, not prior summaries)
**Branch:** main (clean working tree at audit time)

> This file supersedes the stale top-matter in `roadmap.md` (dated 2026-03-14).
> The roadmap progress tracker remains the historical record; this file is the
> authoritative *current* state.

---

## 1. Headline

Fiboki is in **late-stage production hardening**. Phases 1–19 are recorded complete.
Backend and worker run on Railway; frontend on Vercel. Bots are already executing
on the **IG demo** (confirmed: an XAUUSD order filled live on demo on 2026-06-18).
The open items are execution reliability (per-instrument IG rejections), test
hygiene, and operator-facing visibility — not missing platform capability.

## 2. Verified facts (this audit)

| Area | Finding | Evidence |
|------|---------|----------|
| Python env | Requires 3.11; clean editable install (`pip install -e ".[dev]"`) on 3.11.15 | ran 2026-06-19 |
| Lint | `ruff check src/` → **All checks passed** | ran 2026-06-19 |
| Core tests | **104 passed** (indicators, metrics, execution router/signals, fleet/risk) | `tests/test_ichimoku…test_risk_engine` |
| Full suite | Cannot complete locally — network-coupled (yfinance/IG) + heavy-compute tests hang without offline markers/mocks | see `TEST_HEALTH_2026-06-19.md` |
| Strategies | **21 files, all 21 registered & API-visible** (bot01–13, 15–22; no bot14) | registry introspection |
| Canonical vs extended | 12 canonical (bot01–12) + 9 extended/experimental (bot13,15–22); none broken | see `STRATEGY_REGISTRY_AUDIT.md` |
| IG demo execution | Live and sizing off the real demo balance (`_get_account_balance()`), not £1,000 or the paper account | `execution/ig_adapter.py` |
| IG rejections | Real, per-instrument (gold fills, FX/indices reject). Cause not yet retrieved — lives only in Railway DB/logs | see `IG_GATE_STATUS.md` |
| Git cadence | Last commit 2026-06-13; ~34 commits/30d; recent work = per-page operator audits + IG Gate 2 | `git log` |

## 3. Known limitations / honest caveats

- **Test suite is not offline-runnable.** A subset depends on live yfinance/IG and
  has no network mock or `offline`/`slow` markers, and the default config has no
  per-test timeout. This is why a local full run hangs while CI reports green.
- **Operator visibility split-brain.** The System page "Execution Accounts" panel
  reflects the **API** process env (shows `legacy_single` / Paper-only), not the
  **worker** that actually places orders. Misleading; fix planned (Wave 1/Phase 3).
- **Realism approximations remain** (documented elsewhere): USD→GBP conversion,
  static spreads, zero slippage by default, no overnight financing.

## 4. What is NOT a problem (myths busted)

- "Paper must match £20k to trade IG" — **false.** Paper and IG demo are separate
  ledgers; IG already sizes off its own live balance.
- "Nothing is placing orders" — **false.** The worker routes to IG demo; gold filled.
- "12 vs 22 strategies is broken" — **false.** 21 files all register cleanly.

## 5. Immediate priorities (gated, in order)

1. **Wave 1 — IG Gate 2 proof:** retrieve the IG rejection reason from prod, fix the
   true cause, then run one clean demo trade (open→sync→close→audit→reconcile).
2. **Wave 0 finish:** add `offline`/`network` markers + mocks + `--timeout` so the
   full suite runs locally and in CI; then capture a true full green number.
3. **Wave 2:** ship `/strategies/registry-health` + UI tradable/research-only badges.
4. **Wave 3+:** append-only agent/lifecycle ledger before any autonomous generation.

See `NEXT_PHASES.md` for the full 8-wave programme.
