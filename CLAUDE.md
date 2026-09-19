# Fiboki

Multi-strategy trading research platform. Python 3.11 / FastAPI backend, Next.js + TypeScript frontend.
Production: https://fiboki.uk · API: https://api.fiboki.uk

**The agent charter for this repo is [`AGENTS.md`](AGENTS.md). Read it before your first change.**
**The only authoritative status document is [`docs/FIBOKI_V2_AUDIT_AND_OVERHAUL.md`](docs/FIBOKI_V2_AUDIT_AND_OVERHAUL.md).** Everything else in `docs/` is historical and much of it is contradictory.

## Commands

```bash
# Setup
cd backend && pip install -e ".[dev]"

# Tests — the env var is required, without it the suite hangs on live-network daemon threads
cd backend && FIBOKEI_WORKER_EXTERNAL=true pytest -m "not network" --timeout=120

# Lint
cd backend && ruff check src/

# Frontend
cd frontend && npm run build && npx tsc --noEmit && npm run lint
```

## The five rules that cannot be broken

Repeated here deliberately, so an agent that reads only this file still gets them. Full context in `AGENTS.md`.

1. **No live-money execution.** `IGClient._base_url` is hardcoded to the IG demo host. Do not make it configurable, and do not refactor it away as duplication — it is the last line of defence.
2. **No broker base URL overridable by a single environment variable.** Live gates assert on parsed hostname, never on string equality.
3. **No deploy config sets a live-execution flag to a literal truthy value.** Use `sync: false`.
4. **No order dispatch** without a pre-dispatch intent record, a persisted broker deal ID, and a client idempotency reference.
5. **No pre-order path skips the risk engine.**

## The prime directive

Never state something as verified unless you verified it in this session. Write "not verified" rather than inferring. Where a document and the code disagree, the code wins — and say so.
