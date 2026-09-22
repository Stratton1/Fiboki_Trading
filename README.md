# Fiboki V2

A quantitative research laboratory, and the execution machinery to run what the laboratory
approves.

**Status: research-only.** Nothing here has ever placed an order, and no strategy has been shown
to make money. That is the correct state for this stage and it is what `docs/v2/ROADMAP.md` says.

**Verified 2026-09-19T05:05Z:** `pytest tests/ -q` → **2683 passed, 2 skipped** in 287 s.
125 source files / 51,937 lines; 102 test files / 28,716 lines.

---

## What this is

Fiboki V2 is one installable Python distribution, `fiboki` 2.0.0, that runs on one machine. The
majority of the code exists to decide whether a result can be believed; the execution layer
exists so that the thing which eventually trades is the same thing that was tested.

It is a rebuild, not a refactor. V1 was ~32,000 lines of backend Python with a working IG demo
integration and a five-rung robustness ladder — and it could not tell you whether any of its
strategies made money. `docs/v2/V1_FORENSIC_BASELINE.md` is the frozen record of why. The short
version: spread charged on entry only, `sqrt(252)` annualisation regardless of trade frequency,
stops filling at their exact level through weekend gaps, 23,040 trials with no multiple-testing
correction, an "out-of-sample" window that had already driven selection, a risk engine with zero
call sites in production code, and a canonical data store whose timestamps were five hours out
and whose prices were bid quotes treated as mid.

Every one of those has a named mechanism in V2 and a test that proves it. The table is at the end
of `docs/v2/BUILD_LOG.md`.

## What it is not

It is not a trading bot you can point at an account. There is no live worker entrypoint, no
broker credential, no market data in this repository, and reaching `ExecutionMode.LIVE` requires
five independent controls, two of which are source constants that would need separate reviewed
commits.

It is not a frontend. `apps/web/` is empty. The API's labelled-`Figure` contract exists for one.

## Running it

Requires Python 3.11 or 3.12.

```bash
make -f deploy/Makefile setup     # venv, deps under constraints, lockfile, doctor
fiboki system doctor              # fix what it prints, in order

make -f deploy/Makefile test      # the full offline suite
make -f deploy/Makefile check     # everything CI gates on
```

Install manually, with the constraints applied — they matter, and each pin in
`deploy/constraints.txt` records the incident that produced it:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -c deploy/constraints.txt -e ".[dev]"
```

### The CLI

```bash
fiboki --help
fiboki system health          # real checks; the worst component wins
fiboki worker run research    # a PROCESS, in the foreground, holding a single-writer lease
fiboki worker status          # heartbeat age and lease holder
fiboki research memory ...    # "have we tried this?" — ask BEFORE doing the work
fiboki killswitch status
```

Seven command groups: `data`, `research`, `strategy`, `worker`, `system`, `broker`,
`killswitch`. Exit codes are meaningful: 0 clean, 1 fatal, 2 misuse, **75 another holder has the
lease — do not restart-loop**.

`fiboki worker run live` deliberately refuses. A live worker assembled from command-line flags is
a live worker whose risk configuration nobody reviewed.

### Serving the API

```bash
make -f deploy/Makefile run-api   # uvicorn on 127.0.0.1:8000
```

Keep it on loopback. `docs/v2/SECURITY_MODEL.md` §11 carries the verification table.

## Where to start reading

**If you have fifteen minutes**, read `docs/v2/V1_FORENSIC_BASELINE.md` §§4–7 and then
`docs/v2/ROADMAP.md`. Between them they tell you what was wrong and what is and is not true
today.

**If you are going to change the code**, read `AGENTS.md` first — it is the working charter and
it applies to humans as much as to agents — then `docs/v2/ARCHITECTURE.md` for the package
boundaries and how the layering is enforced.

**If you are going to interpret a number**, read `docs/v2/QUANT_RESEARCH_STANDARD.md` and
`docs/v2/VALIDATION_STANDARD.md`. The first states what makes a result admissible; the second
gives every threshold with its rationale and the arithmetic behind it.

### The document set

| Document | Answers |
|---|---|
| `docs/v2/V1_FORENSIC_BASELINE.md` | What V1 was, defect by defect, with a KEEP/REFACTOR/REWRITE/RETIRE/ARCHIVE verdict per component |
| `docs/v2/ARCHITECTURE.md` | Package boundaries, the ALPHA→PORTFOLIO→RISK→EXECUTION layering and how it is enforced, process topology, storage, and why one distribution rather than twelve |
| `docs/v2/QUANT_RESEARCH_STANDARD.md` | How research is conducted and what makes a result admissible. Includes: headline profit is never the ranking criterion |
| `docs/v2/DATA_ARCHITECTURE.md` | Raw/canonical layers, the integrity model, versioning and lineage, the recorder, and the known defects of every data source |
| `docs/v2/EXECUTION_ARCHITECTURE.md` | The fill model and its documented approximations, broker profiles, the adapter contract, the order lifecycle, reconciliation, and every live control enumerated |
| `docs/v2/VALIDATION_STANDARD.md` | The seven rungs, the nine gates with their thresholds, the formulas with their sources, and the quantified argument for a 400-trade minimum |
| `docs/v2/PORTFOLIO_RISK_STANDARD.md` | Alpha / sizing / risk separated, the eighteen-check gateway, kill-switch semantics, and the limit sets |
| `docs/v2/AI_AGENT_ARCHITECTURE.md` | The roles and capability model, and precisely how "an LLM is never the final authority on an executable order" is structurally enforced |
| `docs/v2/SECURITY_MODEL.md` | Auth, RBAC, CSRF, rate limiting, secrets, supply chain, and what is explicitly **not** defended against |
| `docs/v2/OBSERVABILITY_STANDARD.md` | Logging, metrics, the alert taxonomy, health checks, and what happens when the worker dies at 3am |
| `docs/v2/STRATEGY_STANDARD.md` | The DSL, the mandatory hypothesis and evidence-against, and the lifecycle states |
| `docs/v2/OPERATIONS.md` | Running it, the CLI, incidents, backup and restore |
| `docs/v2/DEPLOYMENT.md` | launchd, systemd, containers, dependency pinning, deploy gates, and the gates to real money |
| `docs/v2/ROADMAP.md` | What is implemented and verified, implemented but unwired, designed but not built, and blocked externally |
| `docs/v2/BUILD_LOG.md` | A dated, factual record of this rebuild |

## Layout

```
src/fiboki/
  core/         enums, contracts, instruments, money          — imports nothing from Fiboki
  data/         schema, integrity, versioning, store, providers, recorder, telemetry
  indicators/   20 indicators, every one causality-proved
  strategy/     the DSL, primitives, compiler, registry
  sim/          the fill model and the broker execution profiles
  backtest/     the event-driven engine and honest metrics
  stats/        DSR, PBO, SPA/StepM, purged CV, bootstrap, stress, stability
  validation/   the seven-rung ladder, the gate set, the holdout registry, the report
  marketstate/  causal features, regime axes, cross-asset, economic calendar
  portfolio/    the single sizing authority and portfolio construction
  risk/         the mandatory gateway, the kill switch, versioned limits
  broker/       adapters, the crash-survivable execution service, the mode guard
  agents/       the research fleet — no import edge to broker/, risk/ or portfolio/
  obs/          logging, metrics, alerts, health
  api/          the HTTP surface; every number carries a Provenance
  workers/      worker PROCESSES, never threads inside the API
  cli.py

tests/          unit, integration, api, golden (hand-calculated), property
research/       strategy documents, experiments, reports
deploy/         Makefile, Dockerfile, compose, launchd, systemd, constraints, lockfile
docs/v2/        this document set
```

## Four things to know before you touch it

**Nothing is stated as verified unless it was verified in that session.** V1's single most
expensive defect was documentation in the indicative mood: four status documents claimed the test
suite passed while the most recent said it could not complete. `AGENTS.md` carries this as the
prime directive.

**A worker is a process, never a thread inside the API.** V1's worker was a daemon thread; when
it died the API kept serving `{"status": "ok"}`, so every signal said the system was healthy
while no work was being done.

**Absence is a state, not a zero.** A missing dataset raises rather than returning an empty
frame. A metric that cannot be honestly computed raises rather than returning a flattering value.
A gate whose input is missing is `NOT_EVALUATED`, which **blocks** promotion. A `null` heartbeat
age means no worker has ever beaten, which is not the same as `0`.

**The golden tests are not negotiable.** `pytest -m golden` runs the hand-calculated financial
correctness tests. They have their own required CI job, that job fails if zero were collected,
and it fails if one was skipped. The marker's own description says: *never weaken to make them
pass.*
