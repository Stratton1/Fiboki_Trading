# Risk Register

| ID | Risk | Likelihood | Impact | Mitigation | Status |
|----|------|-----------|--------|------------|--------|
| R-01 | Worker thread inside API process dies silently → no evaluation, API still "healthy" | Med | High | Phase C extraction + heartbeat table + alert on missing heartbeat | OPEN |
| R-02 | Deployed env-var/router-mode mismatch silently routes all signals to paper (H1) | High | High | Startup config echo endpoint (auth-gated), router summary surfaced on System page, doc repair | OPEN |
| R-03 | IG quota exhaustion (30 non-trading req/min, 10k hist points/week) once universe expands | High | Med | Phase E quota governor, local candle cache, budget dashboards | OPEN |
| R-04 | Duplicate order submission on retry/restart | Med | High | Idempotency keys + durable trade_intents before submission (T-B2) | OPEN |
| R-05 | Backtest results predating realism audits still in DB mislead ranking | Med | Med | Tag results with code_hash/dataset_version; invalidate pre-audit runs | OPEN |
| R-06 | Correlated bot fleet concentrates exposure | Med | High | Existing fleet-risk engine (correlation 0.85 threshold); extend with currency/class budgets (Phase K) | PARTIAL |
| R-07 | £180 funded account damaged by premature activation | Low (locked) | High | Production hard block + ProductionExecutionGuard + single-approval protocol; account untouched until release report approved | CONTROLLED |
| R-08 | Secrets leakage via logs/docs | Low | High | Redaction in ig_client logging; never print values; report presence only | CONTROLLED |
| R-09 | SQLite/PG behavioural drift between dev and prod | Med | Med | Run test suite against PG in CI (Phase O) | OPEN |
| R-10 | Sandbox network restrictions prevent IG demo integration tests from this session | High | Med | Integration verification executes on Railway (where creds live); local tests use mocks | ACCEPTED |
