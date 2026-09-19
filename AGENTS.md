# Fiboki — Agent Charter (v2)

**Effective 19 September 2026.** This file replaces `CLAUDE.md` (old), `GEMINI.md`, `RULES.md` and `.claude/skills/*`, all of which were deleted. Those files stated aspirations as facts — they asserted deterministic backtests, known-value indicator tests, regression tests on fixed datasets, a 5% portfolio risk cap, a maximum of 8 simultaneous trades and daily/weekly drawdown hard stops. A full audit on this date established that **none of those were true in the running system.** That gap between documented rule and enforced behaviour is the single most expensive defect in this project's history, and it is what this charter exists to prevent.

Read `docs/FIBOKI_V2_AUDIT_AND_OVERHAUL.md` before your first change. It is the only status document with authority. Everything else in `docs/` is historical.

---

## 1. The prime directive

**Never state something as verified unless you verified it in this session.**

Write "not verified" rather than inferring. Do not carry a claim forward because a previous document, a previous agent, or a code comment asserted it. When you find a contradiction between a document and the code, the code wins and you say so explicitly.

This applies to your own output. If you report a measured number, show the command that produced it.

---

## 2. Hard safety rules — no exceptions, no exceptions requested

These are enforced by code and test, not by your good intentions.

1. **No live-money execution.** `IGClient._base_url` is hardcoded to `IG_DEMO_BASE` (`execution/ig_client.py:72`). Do not make it configurable. Do not "clean up" this apparent duplication with the environment flags. It is the last line of defence and it stays until Gate C in the overhaul plan is formally met.
2. **No broker base URL may be overridable by a single environment variable.** Any live gate asserts on a parsed hostname, never on string equality against a constant. (This was a real bypass in `tradovate_client.py`; a trailing slash defeated it.)
3. **No deploy config may set a live-execution flag to a literal truthy value.** Use `sync: false` and set it out of band. There is a test for this; do not weaken it.
4. **No order dispatch without a pre-dispatch intent record**, a persisted broker deal ID, and a client-generated idempotency reference.
5. **No pre-order path may skip the risk engine.** If you add an execution route, it calls `RiskEngine` before dispatch or it does not ship.

If a task appears to require breaking one of these, stop and raise it. Do not work around it.

---

## 3. Numbers, and what makes one trustworthy

Fiboki exists to answer one question: does a strategy make money. Every defect that flatters a number is therefore a correctness bug of the highest severity, not a rounding issue.

- Costs are charged on **both** legs. Spread, commission, slippage and overnight financing all apply. 2× modelled spread is the base case for promotion decisions, not the stress case.
- Sharpe is annualised by actual trade frequency, never by a constant.
- Drawdown marks open positions to market every bar.
- Stops and take-profits fill at the worse of the level and the bar open. Price gaps through levels.
- A strategy's score is gated on profitability before any other term is considered.
- Selection and validation never touch the same data. The final 20% of history is a holdout that nothing reads until a survivor is evaluated on it exactly once.
- Every research run records its trial count, the cross-trial Sharpe variance, the deflated Sharpe ratio, and the probability of backtest overfitting. A run without these numbers is not a result.

**Pin dependencies exactly and record the resolved versions in every result artifact.** Determinism across environments is not optional, and this repo did not have it: unpinned floors resolved to pandas 3.0.6 and numpy 2.4.6.

---

## 4. Architecture rules that are actually load-bearing

- One execution engine. The backtester, paper and live paths call the same code. Parity is structural, not aspirational. Do not reimplement fills, sizing or cost handling in a second place.
- One sizing authority. Size is computed once and carried on the plan. Adapters convert units; they never re-decide size.
- Broker logic is adapter-based behind `ExecutionAdapter`. Strategy logic never imports a broker.
- Indicators live in `indicators/` and are causal. An indicator that reads bar `i+k` for `k > 0` is a bug, including when it is "only for display" — if it is in the DataFrame handed to a strategy, it is a loaded gun.
- Signals evaluate on closed candles only. Timestamps are UTC.
- The frontend renders and controls; it contains no trading logic and no indicator maths.
- Ledgers are append-only.

---

## 5. Working method

Orient before changing. Read the actual code path; do not trust a summary, including one you wrote earlier in the session.

Make the smallest coherent change that achieves the objective. Preserve working behaviour. Prefer focused commits.

Test what you changed, and report the command and its output. A compile or a green exit code is not evidence that behaviour is correct.

When you finish, report: what was wrong, what changed, files touched, tests run with results, what remains unverified, and whether previously stored results are invalidated by the change. That last item matters — several fixes in the overhaul plan invalidate every stored backtest, and silently leaving stale numbers in place would recreate the problem this charter exists to solve.

Run the test suite with `FIBOKEI_WORKER_EXTERNAL=true`, otherwise every API test spawns a live-network daemon thread and the suite hangs.

---

## 6. Provenance, in code and in the interface

Every number an operator sees carries its origin: backtest, paper, broker demo, or live. Every screen on which an operator can act states unambiguously which execution mode is active. No copy asserts "paper only" unless it has read the execution mode and confirmed it.

An API failure must never render as a zero. A dead backend and a flat, idle fleet must not look the same.

---

## 7. What not to do

Do not add strategies. The roster is being cut from 64 toward 12–20; every additional strategy raises the statistical bar for all the others.

Do not run the full research grid before the cost model and the validation protocol are corrected.

Do not add documents to `docs/`. There were 52 and they contradicted each other. Update the audit document or the build log.

Do not mark anything complete without evidence, and do not describe a phase of the overhaul plan as done while any of its gates are unmet.
