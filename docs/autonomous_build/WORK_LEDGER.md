# Work Ledger

States: TODO | IN_PROGRESS | BLOCKED_EXTERNAL | FAILED_TEST | READY_FOR_DEPLOY | DEPLOYED_UNVERIFIED | VERIFIED | DEFERRED_WITH_REASON

| ID | Phase | Task | Depends | State | Evidence / Next action |
|----|-------|------|---------|-------|------------------------|
| T-A1 | A | Repo + architecture audit | — | VERIFIED | VERIFICATION_REPORT.md A1–A12 |
| T-A2 | A | Deployed-state audit (Railway env, logs, deploy SHA) | access | BLOCKED_EXTERNAL | No Railway connector in session. Need: Railway MCP/token, or operator pastes startup log lines ("ExecutionRouter built: mode=…" and "Execution mode: …" from `_validate_ig_config`) |
| T-A3 | A | Local test suite + lint baseline | — | IN_PROGRESS | pip install running in sandbox; then pytest -v, ruff |
| T-A4 | A | Root-cause report for IG-demo gap | T-A2 | IN_PROGRESS | Hypothesis matrix H1–H5 written; discrimination requires T-A2 inputs |
| T-B1 | B | Fix deployment.md env-var drift; single source of truth for adapter selection | T-A4 | TODO | |
| T-B2 | B | IG adapter gap-fill: working orders, stream fallback hardening, idempotency keys, reconcile_after_restart | T-A3 | TODO | Audit found: confirmation retry returns partial response on exhaustion; no working-order support; no idempotency record before submission |
| T-B3 | B | Deployed EURUSD H1 demo lifecycle proof | T-A2,T-B2 | TODO | Gate 2 evidence into IG_DEMO_EXECUTION_EVIDENCE.md |
| T-C1 | C | Worker as separate Railway service (`python -m fibokei.worker`), API stops starting in-process thread when FIBOKEI_WORKER_EXTERNAL=true | T-A3 | TODO | |
| T-C2 | C | Worker heartbeats + leases + restart drill | T-C1 | TODO | New tables worker_heartbeats, worker_leases |
| T-D..Q | D–Q | Per MASTER_EXECUTION_PLAN | gated | TODO | Sequenced after Gate 2/3 |
