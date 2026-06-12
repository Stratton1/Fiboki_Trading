# Master Execution Plan — FIBOKI Autonomous Build

**Mandate:** Transform FIBOKI into a persistent, observable research + paper + IG-demo execution platform. Production IG remains hard-blocked. Funded trading requires a separate, explicit operator approval after a Live-Canary Release Report — never before.

## Standing constraints

- IG demo only (`demo-api.ig.com`); production block in `ig_client.py` is non-negotiable.
- Closed candles only, UTC, deterministic backtests, centralised risk, adapter-based brokers (RULES.md / CLAUDE.md binding).
- IG quotas: ~30 non-trading req/min/account, 60/app, 100 trading/min, 10k historical points/week, 40 streaming subs. No parallel-connection workarounds.
- £180 funded account: never used for debugging; live canary only after demo evidence + explicit approval.
- No secrets in repo, logs, or docs.

## Phases and gates

| Phase | Deliverable | Gate |
|-------|------------|------|
| A | Forensic baseline + root-cause report | Root cause of IG-demo gap identified with evidence |
| B | IG demo execution repair | One deployed EURUSD H1 open→confirm→reconcile→amend→close cycle on Railway |
| C | Dedicated Railway worker (out of API process) | Heartbeat visible, restart drill passed, no laptop dependency |
| D | Data fabric + IG instrument catalogue | Catalogue persisted, gap/duplicate validation, quota-respecting backfill |
| E | Rate-limited streaming supervisor | Prioritised subscriptions under hard ceiling, degraded mode |
| F | Strategy composition engine | Typed serialisable strategy definitions, no file explosion |
| G | Research factory | Staged rejection pipeline (static→screen→event-driven→OOS→walk-forward→MC→shadow→paper→demo), resumable queues |
| H | Fitness/ranking | Composite robust scoring; explainable rejections |
| I | Evolution engine | Bounded children, lineage, dominated-variant rejection |
| J | Promotion funnel | State machine DRAFT→…→IG_DEMO_SCALED; auto-demotion |
| K | Portfolio governor | Budgets per bot/instrument/currency/class/direction; pauses; close-only; kill switch |
| L | Operator cockpit | Mode-distinct UI, funnel, reconciliation, quotas, audit |
| M | News/event layer | Event-risk pauses only (no event-driven entries initially) |
| N | Observability/ops | Heartbeats, alerts, restart drill, runbooks |
| O | Testing & hardening | Full pyramid incl. research-validity tests |
| P | Open-source review | OPEN_SOURCE_ARCHITECTURE_REVIEW.md |
| Q | Final deployed verification | All acceptance gates, final evidence report |

Production-live work (multi-layer `ProductionExecutionGuard`, £180 feasibility report, live-canary release report) is built **behind locks** during K–Q; activation stops for one explicit operator approval.

## Known deviations from mandate assumptions (evidence-based)

1. The mandate assumed the IG adapter/worker largely missing. Reality: both exist and are mature (fan-out router, per-account risk, reconciliation, audit tables). The defect is operational/configurational, not architectural — see VERIFICATION_REPORT H1–H5.
2. `railway.json` defines only the API service; worker currently runs as a daemon thread inside the API process (single point of failure; restart loses in-flight evaluation). Phase C fixes this.
3. `docs/deployment.md` env-var guidance is stale relative to the router-factory flags. Phase B includes doc repair.

## External access currently missing from this session

- Railway connector (or API token) — required to read env-var presence, logs, deploy SHA, and to add the worker service.
- IG demo credentials are on Railway only (correct) — integration verification must run there, not locally.
- Authenticated API access (operator JWT) would substitute for some Railway reads.
