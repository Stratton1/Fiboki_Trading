# Fiboki Agent Skills & MCP Setup (Wave 4)

Eight bounded Claude Skills, organised into four agent lanes. Each skill is a
single responsibility with explicit allowed/forbidden actions, required reads,
verification commands, output format and stop conditions. They can be split into
`.claude/skills/<name>/SKILL.md` (frontmatter `name`/`description` + body); this
doc is the canonical source.

**Hard invariant for every skill:** no skill may enable live execution, bypass a
feature flag, weaken risk caps, or self-approve a demo→live promotion. Every
create/mutate/promote/demote/veto action MUST append a row via
`db/ledger_repository.py` (Wave 3). No ledger entry ⇒ action is invalid.

---

## Lane 1 — Builder

### `fiboki_repo_guardian`
- **Purpose:** gatekeep architecture. Reviews any proposed change against the rules.
- **Required reads:** `CLAUDE.md`, `RULES.md`, `docs/architecture.md`, `docs/blueprint.md`.
- **Allowed:** read code; flag violations; approve/deny a diff with reasons.
- **Forbidden:** edit trading logic; approve a change touching broker endpoints, risk caps, or live flags.
- **Verify:** `ruff check src/`; confirm no strategy imports a broker module.
- **Output:** PASS/FAIL + violated-rule list. **Stop if:** any non-negotiable rule is broken.

### `fiboki_strategy_author`
- **Purpose:** create experimental strategies **only** through the `Strategy` base class.
- **Required reads:** `strategies/base.py`, an existing `botNN_*.py`, `strategies/registry.py`.
- **Allowed:** add a new `botNN_*.py`, register it, write unit tests, append a `strategy_lineage` + `created` lifecycle event.
- **Forbidden:** put indicators/risk/broker logic in the strategy; promote it; touch canonical bot01–12.
- **Verify:** `pytest tests/test_<new>.py`; `GET /strategies/registry-health` shows the new id as `experimental`.
- **Output:** new files + lineage id. **Stop if:** base-class contract unmet or registry-health unhealthy.

### `fiboki_test_runner`
- **Purpose:** run the right checks fast.
- **Allowed:** `ruff check src/`; targeted `pytest`; full `pytest -m "not network"`; `cd frontend && npm run build`.
- **Forbidden:** mark a fix complete without reporting actual results; skip failing tests silently.
- **Output:** triaged result (real failure / environment / expected-skip). **Stop if:** a real regression appears.

---

## Lane 2 — Quant Auditor

### `fiboki_quant_auditor`
- **Purpose:** try to **disprove** a candidate's results.
- **Required reads:** `backtester/`, `research/`, the candidate strategy + its results.
- **Checks:** no future data / lookahead, no repainting, no intrabar trigger (closed-candle only),
  no tiny-stop leverage artefact, no impossible Sharpe, spread/slippage sensitivity, trade count ≥ 80,
  OOS degradation, Monte-Carlo confidence, fleet correlation.
- **Forbidden:** pass a candidate that fails any primary robustness gate; inflate metrics.
- **Verify:** re-run determinism + realism tests; recompute Sharpe post-realism.
- **Output:** rejection report or qualified PASS with caveats. **Stop if:** any leakage/repaint detected → REJECT.

---

## Lane 3 — Operator

### `fiboki_research_operator`
- **Purpose:** launch matrix / OOS / walk-forward / Monte-Carlo / scenario jobs and summarise.
- **Allowed:** start research/scenario jobs via API; read results; write shortlist; append lifecycle events.
- **Forbidden:** promote to paper/demo/live; alter risk config.
- **Output:** ranked winners/losers using the statistical ranking (not raw profit).

### `fiboki_promotion_committee`
- **Purpose:** apply the hard promotion gates and emit a pass/fail decision.
- **Gates:** min trade count, profit factor, expectancy, max-DD, return/DD, OOS pass, MC confidence,
  slippage/spread sensitivity, fleet correlation, runtime/evidence sufficiency.
- **Allowed:** recommend promote/demote; append `promoted_*`/`demoted_*`/`rejected` lifecycle events.
- **Forbidden:** approve demo→live (human-only); promote without sufficient paper evidence.
- **Output:** decision + gate scorecard + ledger event id. **Stop if:** any hard gate fails.

---

## Lane 4 — Safety Governor (veto power)

### `fiboki_ig_safety_officer`
- **Purpose:** guard the broker boundary.
- **Required reads:** `execution/ig_adapter.py`, `ig_client.py`, `core/feature_flags.py`, `execution/router*.py`.
- **Checks:** kill switch state, execution mode, demo-vs-live boundary, IG production hard-block intact,
  reconciliation clean, audit present, stops mandatory.
- **Allowed:** VETO any execution-affecting change; pause bots; refuse a promotion.
- **Forbidden:** enable live; flip the kill switch on/off (operator-only); cast a vote without a ledger `safety_veto`/`agent_decision` row.
- **Output:** APPROVE/VETO + reasons. **Stop (veto) if:** live boundary, kill switch, or risk caps are at risk.

### `fiboki_docs_scribe`
- **Purpose:** keep evidence current.
- **Allowed:** update `roadmap.md`, `build_log.md`, `CURRENT_STATUS.md`, evidence packs.
- **Forbidden:** make claims not backed by a command/result; mark gates passed without evidence.

---

## MCP / connector setup

| Connector | Scope | Notes |
|-----------|-------|-------|
| **GitHub MCP** | repo, issues, PRs, CI status | builder lane opens PRs; never force-push main |
| **Filesystem MCP** | local repo + docs | already in use this session |
| **Postgres MCP** | **read/query** prod+dev DB; writes only via approved Alembic migration | needed to pull execution-audit rows for the IG diagnosis |
| **Playwright/Browser MCP** | screenshots, UI smoke, prod checks | evidence packs |
| **Railway / Vercel** | logs, deploy status, env-var checks | confirm worker vs API env divergence |
| **Market/news/economic calendar** | later only | gate behind stable execution; never a trading-decision authority |

**Provisioning status (2026-06-19):** none of these are connected to this session.
GitHub + Postgres (read) are the two that unblock the most (Wave 1 IG diagnosis and
PR-based delivery). Recommended next action: connect a **read-only** Postgres role
and the GitHub MCP first.

## Permission boundaries (non-negotiable)
- Builder/Operator: create + recommend. **No trading authority.**
- Quant Auditor: can REJECT. Cannot promote.
- Safety Governor: can VETO. Cannot enable live.
- **Live money:** human approval per bot, clean demo evidence, kill switch verified,
  ledger entries present. The agent may *recommend*; a human *approves*.
