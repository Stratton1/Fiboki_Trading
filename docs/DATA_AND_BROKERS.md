# Instruments, Data Sources & Brokers

**Date:** 2026-06-19. Answers the operator questions about mapping IG epics,
making IG the test universe, getting more `.cfp` instruments, and broker choice.

## 1. Can we map the epics up? — Yes, shipped
`POST /execution/ig-epic-audit` (and `execution/ig_epic_audit.py`) walks every
catalogue instrument and reports, per instrument: `ok` (static epic priceable),
`remapped` (found a tradeable alternative — gives `resolved_epic`), or
`unavailable`. It returns an `epic_overrides` map and a `tradable_symbols` list.

**Workflow (run on the worker — it has IG creds):**
1. Call `POST /execution/ig-epic-audit` (optionally `?symbols=GBPUSD,JP225`).
2. Take `epic_overrides` and update `core/instruments.py` with the verified epics.
3. Re-run to confirm everything is `ok`.

The runtime auto-remap retry (shipped) is the *automatic* mitigation; this audit
is the *permanent* fix — bake verified epics into the catalogue once.

## 2. Make IG the source of the test universe — recommended path
`tradable_symbols` from the audit is exactly "what this IG account can trade."
To make research/backtests run over that set:
- Gate the research/paper instrument universe to `tradable_symbols`.
- **Data caveat (important):** backtesting needs *price history* per instrument.
  Trading-universe ≠ research-universe. An instrument can be tradable on IG but
  have no Fiboki history, so it can't be backtested until data is ingested.
- So the loop is: audit → tradable set → ingest history for that set → research.

## 3. Getting more instruments / `.cfp` products & data sources
**Tradability** comes from IG (the audit finds the epics your account supports —
including `.CFP` £-per-point products like the working Gold epic). If a market
isn't enabled on the demo account, enable it in the IG demo platform, then re-run
the audit.

**Backtest history** (separate from tradability) — open-source / free options:
- **yfinance** — already integrated (`data/ingestion.py`); broad FX/index/commodity coverage, free.
- **HistData.com** — free 1-minute FX history (the canonical 60-instrument set's origin).
- **Dukascopy** — free tick/bar FX + CFD history, high quality.
- **IG `/prices`** — same feed as execution, but quota-limited (~10k points/week) — reserve for charts/spot-checks, not bulk research.
Recommended: yfinance/HistData/Dukascopy for bulk backtest data; IG for execution + reconciliation only.

## 4. Is there a better broker?
For this use case (UK, CFD demo→live, FX/indices/metals, API automation):
- **IG** (current) — solid REST API, broad markets, good demo. The `.cfp`/epic
  quirks are an account-product detail, not a blocker, and the adapter pattern
  isolates them. Reasonable to continue.
- **OANDA** — very clean v20 REST API, excellent for FX, simple instrument names
  (no epic remapping pain), good historical data. Strong alternative for an
  FX-focused fleet.
- **Tradovate** — futures; an adapter is already scaffolded in `execution/`.
- **Interactive Brokers** — widest universe, powerful but heavier API.
The architecture is adapter-based, so adding OANDA later is low-risk and would
sidestep the IG epic-mapping friction for FX. No need to switch now — IG works
once epics are mapped; revisit if FX coverage/epic friction becomes limiting.

## 5. Allow multiple bots of the same combo — already supported
`POST /paper/bots` blocks an accidental duplicate (409 `already_promoted`) by
default, but `allow_duplicate: true` deliberately creates a clone. The UI should
offer "Promote anyway / clone" using the 409's `existing_bot_ids`.
`GET /paper/bots/promotion-status` shows how many copies already exist.

## Next build (planned)
- `fiboki ig-universe` CLI wrapper around the audit for one-command epic refresh.
- Repoint `core/instruments.py` from verified `epic_overrides`.
- Optional OANDA adapter spike if FX coverage needs to grow fast.
