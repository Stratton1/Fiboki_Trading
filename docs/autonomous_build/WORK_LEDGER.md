# Work Ledger

States: TODO | IN_PROGRESS | BLOCKED_EXTERNAL | FAILED_TEST | READY_FOR_DEPLOY | DEPLOYED_UNVERIFIED | VERIFIED | DEFERRED_WITH_REASON

| ID | Phase | Task | Depends | State | Evidence / Next action |
|----|-------|------|---------|-------|------------------------|
| T-A1 | A | Repo + architecture audit | — | VERIFIED | VERIFICATION_REPORT.md A1–A12 |
| T-A2 | A | Deployed-state audit (Railway env, logs, deploy SHA) | — | VERIFIED | Via authenticated browser session — RAILWAY_FORENSIC_REPORT.md. Deployed=origin/main; 15 env vars present (names only); no worker service; worker thread alive in logs |
| T-A3 | A | Local test suite + lint baseline | — | VERIFIED | Execution-layer suites green (80 tests after repair); ruff issues pre-existing style only; full suite belongs in CI (sandbox is py3.10 vs required 3.11+) |
| T-A4 | A | Root-cause report for IG-demo gap | T-A2 | VERIFIED | Three root causes with deployed log evidence: (1) prices VERSION 3 on v2 path → 404 all instruments; (2) spread-bet epics on CFD-only apiUser → 403 FX_BET_ALL on gold/silver orders; (3) stopless size-capped orders reaching IG |
| T-B0 | B | Repair: prices version, runtime epic resolution+retry, mandatory-stop gate | T-A4 | DEPLOYED_UNVERIFIED | Commit 11477f5 pushed; Railway build started 2026-06-12. Verify logs post-deploy |
| T-B1 | B | Fix deployment.md env-var drift | T-A4 | VERIFIED | Commit 6b54ff8 |
| T-B2 | B | IG adapter gap-fill: working orders, idempotency keys/durable intents pre-submission, reconcile_after_restart | T-B0 | TODO | Confirmation-retry partial response also outstanding |
| T-B3 | B | Deployed EURUSD H1 demo lifecycle proof (open→confirm→reconcile→amend→close) | T-B0 | TODO | Gate 2 evidence into IG_DEMO_EXECUTION_EVIDENCE.md. Note: FX/index orders already submit+confirm per logs; full cycle proof pending |
| T-B4 | B | Correct static gold/silver epics in core/instruments.py from observed runtime remaps; fix live_provider epic path | T-B0 | TODO | Evidence-based, not guessed |
| T-B5 | B | Decide handling of stopless strategies (bots e01595a5, d62888c1, 49f9b893) | T-B0 | TODO | Now rejected pre-submission with MISSING_STOP; operator choice: add stops or IG-disable |
| T-C1 | C | Worker as separate Railway service (`python -m fibokei.worker`), API stops starting in-process thread when FIBOKEI_WORKER_EXTERNAL=true | T-A3 | TODO | |
| T-C2 | C | Worker heartbeats + leases + restart drill | T-C1 | TODO | New tables worker_heartbeats, worker_leases |
| T-D..Q | D–Q | Per MASTER_EXECUTION_PLAN | gated | TODO | Sequenced after Gate 2/3 |
