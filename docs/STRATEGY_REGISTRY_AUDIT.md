# Strategy Registry Audit — 2026-06-19

Resolves the recurring "12 vs 22" strategy-count confusion using the **live
registry** as the source of truth (per RULES: registration in `registry.py` is
what makes a strategy real and API-visible).

## Method

Force-imported every `bot*.py` module, then read
`strategy_registry.list_available()` (the same call the API/frontend dropdowns use)
and compared against files on disk.

## Result

- **Strategy files on disk:** 21
- **Registered (API-visible):** 21
- **Import failures:** 0
- **Broken/unregistered:** 0
- Note: there is **no bot14** (numbering gap, not a missing strategy).

## Classification

| Tier | Count | Strategy IDs |
|------|-------|--------------|
| **Canonical** (blueprint 12) | 12 | bot01_sanyaku, bot02_kijun_pullback, bot03_flat_senkou_b, bot04_chikou_momentum, bot05_mtfa_sanyaku, bot06_nwave, bot07_kumo_twist, bot08_kihon_suchi, bot09_golden_cloud, bot10_kijun_fib, bot11_sanyaku_fib_ext, bot12_kumo_fib_tz |
| **Extended / experimental** | 9 | bot13_chikou_session, bot15_momentum_cont, bot16_golden_momentum, bot17_gartley, bot18_fib_ma_confluence, bot19_fib_bb, bot20_pocket_divergence, bot21_fib_arc, bot22_fib_volume |
| factory_generated | 0 | — |
| disabled | 0 | — |
| broken | 0 | — |

Registry entry shape: `{'id', 'name', 'family', 'complexity'}` (no explicit
tradable/experimental flag yet — see recommendation).

## Why the count "churned"

Docs/CLAUDE.md say "12 bots"; the folder has 21 registered. Both are true: 12
canonical + 9 extended. Earlier commits ("remove stale strategy-count assumptions",
"report canonical loaded strategy count") were patching assertions that hardcoded
12 against a registry that actually returns 21.

## Recommendations (Wave 2)

1. Add an explicit `tier` field to registry metadata: `canonical | experimental |
   factory_generated | disabled`. Don't infer tier from the filename.
2. Add `GET /api/v1/strategies/registry-health` returning `{file_count,
   registered_count, by_tier, unregistered_files, import_errors}`.
3. Add a UI badge (Tradable / Research-only / Disabled) driven by `tier`.
4. Add a regression test asserting `registered_count == 21`,
   `canonical_count == 12`, and `import_errors == 0` so this never drifts silently.
