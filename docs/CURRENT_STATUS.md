# Fiboki — Current Status

**Date:** 2026-07-09  
**Author:** Grok Cursor onboarding audit (verified against live repo + local commands)  
**Supersedes:** 2026-06-19 Wave 0 status below (historical facts retained in `build_log.md` / `TEST_HEALTH_2026-06-19.md`)

> This file is the authoritative *current* state. `roadmap.md` remains the historical phase tracker.  
> Full onboarding map: [`GROK_CURSOR_ONBOARDING_AUDIT.md`](GROK_CURSOR_ONBOARDING_AUDIT.md).

---

## 1. Headline

Fiboki is in the **strategy-factory / candidate-review** stage. The V1 app (auth, backtests, research matrix, paper bots, worker, IG demo routing, dashboards) is deployed. IG demo execution is proven (Z5ZAV Spot Gold). Strategy factory Gen-1 is registered (**64** strategies). Candidate review API/UI and approve→paper exist. Broker trade ledger + Trades “IG Demo” tab exist in code.

The open product gap is **operator credibility of broker history** (reliably show `SBQLDCAC` / broker PnL in prod) plus **scaling the robustness ladder** (Phase-1 incomplete locally). Not “missing a platform.”

---

## 2. Verified facts (2026-07-09)

| Area | Finding | Evidence |
|------|---------|----------|
| Python | 3.11.15; `pip install -e ".[dev]"` OK | local shell |
| Lint | `ruff check src/` → **All checks passed** | local |
| Core tests | **122 passed** (indicators, metrics, router/signals, fleet/risk, broker_ledger, promotion) in ~56s | local pytest subset |
| Full suite | Still hangs offline (~6% after 15m) — env/test hygiene, not claimed green | same class as `TEST_HEALTH_2026-06-19.md` |
| Frontend build | `npm run build` **success** (incl. `/research/candidates`) | local |
| Strategies | **64** registered, registry healthy | `strategy_registry.registry_health()` |
| Tiers | 12 canonical + 9 experimental + 25 trad + 10 hybrid + 8 triple | same |
| Instruments | **67** defined; **60** with canonical HistData | `core/instruments.py` + disk |
| Data | `data/canonical/histdata/` ~**7.2 GB**, 60 symbols × 6 TFs | `du` / listing |
| Candidates | API `GET/POST /research/candidates` + UI page + approve→paper | code |
| Local lifecycle | 8 validated, 60 rejected, etc. in `fibokei.db` | sqlite query |
| Broker ledger | Importer + UI shipped; local `broker_trades` = **0** (no sync run here) | sqlite + code |
| Research sweep | H4 `sweep.csv` **3401** rows; Phase-1 checkpoint **25** lines; no `phase1_complete.json` | files |
| IG demo | Gold fill proven historically; FX/index often reject on Z5ZAV | `IG_REJECTION_DIAGNOSIS_2026-06-19.md` |
| Live money | Hard-blocked at IG client | `execution/ig_client.py` |

---

## 3. Known limitations / honest caveats

- **Full pytest is not offline-green.** Use the core subset until network/slow markers are complete.
- **System page can lie about execution targets** — API env ≠ worker env (`fiboki_worker_observer`).
- **Sync from IG** needs IG **read** creds on the API (or a worker-scheduled import). Worker-only creds → UI Sync fails.
- **Paper ≠ IG demo balance** — separate ledgers; do not “fix” by matching £20k paper to demo.
- **Phase-1 ladder** is not a completed universe of candidates; do not treat early survivors as fleet-ready.
- **FX volume synthetic** — VWAP/OBV strategies remain research-limited.

---

## 4. Myths still busted

- “Paper must match £20k to trade IG” — **false.**
- “Nothing places orders” — **false** (worker → IG demo; Gold filled).
- “Only 12 strategies / registry broken” — **false** (64 registered).
- “Candidate review doesn’t exist” — **false** (API + `/research/candidates`).
- “Broker ledger not built” — **false** (code shipped; prod visibility is an ops/UI findability gap).

---

## 5. Immediate priorities (gated, in order)

1. **Broker Trade UI completion** — prove Sync imports `SBQLDCAC`; IG-ref search; bot/strategy columns; update stale reconciliation docs. See onboarding audit §18.
2. **System worker vs API observability** — surface worker heartbeat / router targets separately from API env.
3. **Phase-1 robustness backfill** (scheduled, cost-stress-fixed) → more real candidates in app DB → richer review.
4. **Wave 0 test hygiene** — offline markers so full suite completes locally.
5. **IG Gate 2** — open→sync→close→audit→reconcile during market hours once broker history is visible.

Do **not** expand strategy generation as the next slice while broker history findability is incomplete.

---

## 6. Historical note (2026-06-19 Wave 0)

Prior status correctly established: IG demo fills, paper/IG ledger separation, core 104-test green, full-suite hang triage. It undercounted strategies (21 vs factory 64) and predated candidate UI / broker ledger UI shipping recorded in `build_log.md` the same day. Use this 2026-07-09 file going forward.
