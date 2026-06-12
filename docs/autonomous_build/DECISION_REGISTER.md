# Decision Register

| ID | Date | Decision | Rationale | Alternatives rejected |
|----|------|----------|-----------|----------------------|
| D-001 | 2026-06-12 | Treat existing fan-out router / per-account architecture as the base; do not rebuild | Code audit shows mature, tested implementation (Phases 1–5 of fan-out plan already shipped). Mandate's assumption of a missing adapter was stale | Greenfield rewrite (wasteful, regression-prone) |
| D-002 | 2026-06-12 | Root-cause via deployed evidence before any code change to execution path | Five competing hypotheses (H1–H5) are config/ops, not code; changing code blind risks masking the real fault | "Fix" router defaults speculatively |
| D-003 | 2026-06-12 | Worker extraction (Phase C) keeps in-process thread as fallback behind `FIBOKEI_WORKER_EXTERNAL` flag | Zero-downtime migration; single-service deploys keep working | Hard cutover |
| D-004 | 2026-06-12 | PostgreSQL for queues/leases; no Redis initially | Mandate prefers bounded infra; Railway PG already attached; volumes are modest until research factory scales | Redis/Celery from day one |
| D-005 | 2026-06-12 | Production-live code (ProductionExecutionGuard, env design from mandate §0B.2) is built but ships disabled-by-default with `FIBOKEI_PRODUCTION_TRADING_ENABLED=false`; activation requires operator approval ID matching a signed manifest | Mandate §0B; £180 account must never be a debugging environment | Deferring all live-capable code (slows eventual canary), enabling behind single flag (too weak) |
| D-006 | 2026-06-12 | £180→£1M challenge is tracked as a research benchmark only, with simulated/demo/funded results strictly segregated in reporting | No system can guarantee that outcome; conflating simulated and funded performance is the primary integrity risk | Headline-driven reporting |
