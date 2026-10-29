# Phase audit — 2026-10-19 (laptop loop)

Written at 2026-10-19T23:54:23Z. This session is the laptop. Paper was not restarted. `LIVE_EXECUTION_COMPILED_IN` and `OANDA_LIVE_HOST_COMPILED_IN` were read as `False`. Stored E-4 results are unchanged: no engine, cost-model, metric, or gate edit, and no new campaign.

| Item | Time (UTC) | Result | What was found | Critique |
|---|---|---|---|---|
| abandon-figures | 2026-10-19T23:54Z | done | `AbandonedPositionView.size` and `entry_price` are `Figure`. Missing journal numbers stay `None`. Money strip shows "no data" and "P&L not carried". | A missing size is not zero, and no net P&L is attached to the abandoned rows. |
| restore-qwen | 2026-10-19T23:54Z | done, protocol open | `var/agents/smoke.json`: `ok` true, digest prefix `sha256:359d7dd4bcdab`. `var/agents/eval_report.json`: verdict `NOT_EVALUABLE`, `n_records` 1624, `chain_ok` true. Doctor row: OK, local model, ollama at `http://127.0.0.1:11434`, `qwen3:4b`. | Smoke and eval were not inserted as agent-ledger workflow rows. Twenty intact runs per workflow are not present, so the acceptance protocol stays open. The 27B pin was not pulled. |
| research-memory-http | 2026-10-19T23:54Z | done | Ledger counts are served. Recall of "donchian breakout" on the live desk returned Not novel, with 588 incomparable experiments named as not novelty. Similarity is a Figure; the desk now labels estimated overlap. | Keyword overlap is not a rediscovery rate. Incomparable rows are visible and force the item off "novel". |
| corpus-verify | 2026-10-19T23:54Z | done | `fiboki corpus verify` refuses an empty manifest, an unknown licence, and a bad hash. No download. No experiment-ledger write. | A missing corpus does not look like an empty licensed set. |
| frontend-next-items | 2026-10-19T23:54Z | done | At 390px the Sections drawer opened, Escape closed it, and focus returned to Sections. Sign-in shows "Signing in…" while the button is busy. Chart run picker is disabled with the option text "no stored runs for EURUSD H1". | A disabled control that only says disabled, with no reason, would have failed this row. The run picker carries its reason in the option label. |
| visual-tokens | 2026-10-19T23:54Z | done | `node scripts/contrast.mjs` in `~/fiboki/apps/web`: `350/350 checked pairs pass; 4 exempt` and `8/8 distinct pairs pass`. | Disabled ink stays exempt. Amber remains on strips, warnings, and mode. |
| attention-groups | 2026-10-19T23:54Z | done | Command shows a `warning 3` control and the abandon row "2 forward position(s) abandoned, unrealised P&L not booked" still listed. The warning was not acknowledged. | Grouping did not drop the abandon warning or turn it into a number. |
| rebuild-browser | 2026-10-19T23:54Z | done | `next build` in `~/fiboki/apps/web` with `NEXT_PUBLIC_FIBOKI_API=""` exited 0 (45 pages), twice: once before the browser pass and once after the estimated-overlap label. API `/api/health` 200. Web `/login` 200. Signed in as joe. Command: GBP book £10,000.00, USD book $99,844.89, 2 abandoned, equity "no data". EURUSD chart: 500 bars, dataset `ds_ffff9d78f9224ed696bd72dc`. Agent Desk: last run Idle `wf_event_scan_20261019T2300Z`, protocol sentence present, candidates "cannot execute", every role "Can execute: NO". | The second build was kickstarted onto web only. Paper pids were left running. |
| phase-c-recorder-spreads | 2026-10-19T23:52Z | blocked | Quote files `seg-000000` through `seg-000004` under `~/fiboki/var/quotes`. First timestamp `2026-10-08T23:18:27.067289+00:00`, last `2026-10-19T23:52:51.111365+00:00`. Span is about eleven days. | A spread table from eleven days would be a cost-model version the engine has not earned. None was built. E-4 costs are unchanged. |
| phase-c-calendar | 2026-10-19T23:54Z | blocked | `PINNED_RESEARCH_CALENDAR_VERSION` is `1.0.0`. Latest official manifest is `1.1.0`, retrieved `2026-10-18`, declared start `2024-01-01`, declared end `2027-12-03T13:30:00+00:00`, currencies EUR, GBP, JPY, USD. The manifest's own `not_covered` names anything before 2024-01-01 and AUD, CAD, CHF, NZD. No publisher URL was cited in this session. | Research stays on 1.0.0. No new immutable version was written. |
| phase-c-research-yes | 2026-10-19T23:54Z | blocked | Fibonacci, a new bet, K6, and `crypto_discovery_c1` were not named as filings in this conversation. | Filing any of them would spend trials or lower a stated count. They stay unfiled. |
| phase-c-agent-pipeline | 2026-10-19T23:54Z | done for storage; S4/S6 blocked | Candidate pipeline stores a review with `can_execute` false. Empty evidence-against and a rediscovery refuse. `refuse_trial_stage("S4"|"S6")` raises before bars are evaluated. Desk list is empty and says a candidate cannot execute. | S4 and S6 stay blocked: they would evaluate bars and spend trials. The 20-run protocol stays open. `agent_tier.json` was not written. |
| phase-d-cutover | 2026-10-19T23:54Z | blocked | This session is the laptop. Paper pid 53099 (FX, about 1h 38m at the check) and pid 96795 (crypto, about 22h 43m) were still alive after the API and web restarts. | Unloading paper, pulling the 27B model, or copying `~/.fiboki/env` into git would break the entry gate. None of those happened. |
| phase-d-operator | 2026-10-19T23:54Z | blocked | Finnhub, FRED, export-then-delete of the V1 Railway Postgres database, the OANDA practice question on spread-bet orders and candle divergence, alert credentials, and a timed kill-switch drill. | Each waits on the operator. A Cloudflare tunnel was not started. |
| phase-e-survivor | 2026-10-19T23:54Z | done for the shut door | Imports: `LIVE_EXECUTION_COMPILED_IN` False (`fiboki.broker.mode_guard`), `OANDA_LIVE_HOST_COMPILED_IN` False (`fiboki.broker.oanda`). `tests/unit/test_cli.py::test_worker_run_refuses_to_assemble_a_live_worker_from_flags` — 1 passed in 4.20s. E-4 did not clear rungs 0–5 (`min_track_record`; holdout not read). Tax deferral remains the prior decision: advice before spread-bet versus CFD, threshold £50,000 of profit, account type matters from the first pound, crypto disposals separate, no crypto order path. Calendar reminder: 1 June 2027, refresh before 3 December 2027; research stays on the version it was filed on. Above T1 needs a signed record after the pre-registered shadow comparison. NautilusTrader is a September 2027 review. Pepperstone, Interactive Brokers, and an off-Mac host are unstarted. | Either flag true would have been a failed critique. Both are false. No promotion and no tier file. |

## Tests quoted this session

From `/Users/joe/fiboki`, `.venv/bin/python -m pytest` of `tests/api/test_provenance_contract.py`, `tests/api/test_operator_core.py`, `tests/unit/test_api_paper_journal.py`, `tests/api/test_research_memory_http.py`, `tests/unit/test_research_corpus.py`, `tests/unit/test_candidate_pipeline.py`:

`60 passed, 1 warning in 8.31s`

Live-worker refusal: `1 passed in 4.20s`.

## Roll-up

Done: abandon-figures, restore-qwen (protocol still open), research-memory-http, corpus-verify, frontend-next-items, visual-tokens, attention-groups, rebuild-browser, phase-c-agent-pipeline storage and refusal, phase-e shut door.

Blocked: quote-recorder spread table (eleven-day span), calendar backfill (no citation, research pin stays 1.0.0), Fibonacci / new bets / K6 / crypto discovery filing, S4 and S6 trial stages, desktop cutover, operator rows (Finnhub, FRED, Railway Postgres, OANDA question, alert credentials, kill-switch drill).

Paper pids at the check: 53099 and 96795. API pid 80018 answered 200. Web `/login` answered 200 after the second kickstart.

Doctor model line: OK, ollama at `http://127.0.0.1:11434`, `qwen3:4b`.

Provenance: included in the 60 passed. Contrast: 350/350 pass, 4 exempt; 8/8 distinct pairs pass.

Stored E-4 results are unchanged. No cost-model version was added and no new campaign was filed.
