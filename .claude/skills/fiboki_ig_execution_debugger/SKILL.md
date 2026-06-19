---
name: fiboki_ig_execution_debugger
description: Diagnose and fix IG demo order rejections in Fiboki. Use when bots place orders but IG rejects them (e.g. "REJECTED: UNKNOWN"), or fills are inconsistent across instruments. Diagnoses the true cause before patching.
---

# fiboki_ig_execution_debugger

## Purpose
Pin down WHY IG demo orders reject (per instrument) and apply the smallest correct fix — never a blind patch.

## When to use
- Execution audit shows `rejected` rows while some instruments fill.
- Operator reports "bots fire but nothing reaches IG" or unexplained rejections.

## Files to read first
`backend/src/fibokei/worker.py`, `execution/router.py`, `execution/router_factory.py`, `execution/ig_adapter.py`, `execution/ig_client.py`, `paper/bot.py`, `core/feature_flags.py`, `api/routes/execution.py`, `core/instruments.py` (epics).

## Data to inspect (production — read-only)
- API: `GET https://api.fiboki.uk/api/v1/execution/audit?execution_mode=ig_demo` (auth cookie via logged-in Chrome). Read newest `place_order` rows: `error_message`, `error_code`, `detail_json` (now carries the full IG confirmation incl. `affectedDeals`).
- Railway `fibokei-worker` logs: grep `IG deal NOT accepted`, `MARKET_DETAILS_UNAVAILABLE`, `STOP_TOO_TIGHT`, `MISSING_STOP`, `exceeded allowance`, `403`.

## Known causes to classify between
`MARKET_DETAILS_UNAVAILABLE` (IG quota/exchange access) · `STOP_TOO_TIGHT` · `MISSING_STOP` · market closed · size below IG min · epic/account mismatch (REJECTED/UNKNOWN that the 403-only retry never catches) · kill switch active · worker/API env mismatch.

## Allowed actions
- Read audit/logs, form a ranked diagnosis (one primary cause + evidence).
- Apply the smallest fix matching the CONFIRMED cause; add a regression test.
- Improve observability so the real reason is never swallowed.

## Forbidden actions
- Blind patches without a confirmed cause.
- Enabling live money; touching the IG production endpoint; widening stops silently; weakening risk; disabling the require-stop gate.

## Required verification commands
```bash
cd backend && python -m ruff check src/
python -m pytest -q tests/test_ig_adapter.py tests/test_ig_safety.py tests/test_execution_router.py
```

## Output format
`Root cause` · `Evidence (rows/logs)` · `Fix applied (files)` · `Tests run + results` · `Remaining risk` · `Exact next operator action`.

## Stop conditions
- If the production audit/logs are not reachable, STOP before patching: state exactly what you inspected and the precise command/API call needed to get the real `error_code`.
