# Fiboki V2 — Actions That Require You

**Generated:** 19 September 2026, at the end of the V2 build programme.

Everything in this document is something the engineering programme could not do for you: it needs an account, a credential, a payment, a legal acceptance, a physical action, or a judgement that is yours rather than mine. Everything *else* has been built, and where an external dependency was missing I built the adapter, the interface, the fixtures and the tests so the platform is ready the day the dependency arrives.

Items are ordered by when they block you, not by effort.

---

## CRITICAL — do these before anything else

### C1. Review and merge the V2 tree into your repository

V2 was built in an isolated container and is a self-contained git repository with six checkpoint commits. It has **not** been merged into `Fiboki_Trading` on your Mac, because merging a 65,000-line parallel implementation into your working repo is a decision with your name on it, not mine.

You need to decide the shape: a `v2/` subtree alongside the existing code with `legacy/v1/` preserved, a long-lived branch, or a new repository with V1 archived. The architecture documents assume the first.

Nothing in V2 imports, modifies or deletes anything in V1.

### C2. Decide what happens to the V1 production deployment

`fiboki.uk` and `api.fiboki.uk` are still serving the V1 build from 25 June 2026, including the defects in the audit. V2 does not deploy anywhere yet and has no production environment.

Your options are to leave V1 running as-is, take it down, or put a holding page on it. I would not leave a system running that presents numbers we now know are wrong, but it is your product and your call.

### C3. Remove the committed live-execution flag from V1

`render.yaml` in the V1 repository still contains `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` as a literal value. It is mitigated today only by the accident that Render is not the live host. Change it to `sync: false` or delete the file. V2's CI has a check that fails the build if any committed config sets a live-execution flag to a truthy literal; V1 has no such check.

---

## REQUIRED BEFORE PAPER TRADING

### P1. Open an OANDA practice account and resolve two unknowns

Free, no minimum, takes minutes: <https://www.oanda.com/uk-en/> → practice account → generate a personal access token from the account portal.

Two things must be verified on the demo before the broker decision is final, and neither can be answered from documentation:

**Can the v20 REST API place orders on a spread-betting-enabled sub-account?** This decides whether you get UK spread-bet tax treatment. The logic is strong — spread betting is a flag on a v20 sub-account and the API addresses sub-accounts by `accountID` — but OANDA does not state it anywhere I could find. Create a spread-bet sub-account, then attempt a small order against it through the API.

**How far does the candle endpoint's pricing diverge from your own executable stream?** OANDA's own UK help page says the live pricing feed "could be different from the historical data" because of pricing segments and account types. Run the recorder (P2) alongside candle pulls for a few weeks and diff them.

### P2. Start the executable-price recorder

This is the single highest-value thing you can start today, and its value is proportional to elapsed time. Every backtest Fiboki has ever run used somebody else's prices. `src/fiboki/data/recorder.py` is built, tested and crash-safe; it needs a live quote source.

Once P1 gives you a practice token, point the recorder at OANDA's pricing stream and leave it running. In six months you will have the only dataset that actually predicts your fills.

### P3. Supply an economic-events feed, or accept trading through the news

`src/fiboki/marketstate/calendar.py` ships the taxonomy (21 recurring event types — FOMC, NFP, CPI, ECB, BoE and the rest) and a complete blackout implementation, but **no dated events and no network access**. Until a feed is loaded every blackout query returns "not in blackout", which is the dangerous default: backtests and paper bots trade straight through FOMC and NFP.

Candidate feeds: ForexFactory export, Econoday, Trading Economics, FRED release dates, or your broker's own calendar. Licensing is yours to check — several forbid redistribution.

One trap worth knowing: feeds return *revised* actuals for historical dates, not the print the market traded. Any backtest conditioned on "actual versus forecast" is optimistic unless the feed preserves first prints. `EconomicCalendar.assert_populated()` exists as the guard for any pipeline that must not run blind.

### P4. Provide the full historical data store

V2 was built and tested against two instruments (EURUSD and XAUUSD, H1 and H4) because that is what fitted in the container. Your 7.2 GB HistData store is on your Mac.

Before you use it, know what the migration found: **its timestamps are EST-without-DST stamped as UTC, so every bar is five hours out; its prices are bid, not mid; its volume is identically zero; and EURUSD H1 contains a negative-price sentinel bar (OHLC all −0.0001) at 2001-09-11 20:00 that sits in the *canonical* store.** `fiboki data migrate-v1` applies the timestamp correction and declares the price basis honestly, and the integrity layer rejects the EURUSD datasets rather than reading them as clean.

This alone means every V1 research result is stale, independently of the engine bugs.

### P5. Decide the account currency question

The engine refuses to invent an exchange rate. If you run a GBP account against USD-quoted instruments you must supply a real GBP/USD series, or explicitly acknowledge the approximation in writing. The XAUUSD work so far used a USD account precisely to sidestep this. V1 reported USD P&L as GBP, an error that ranged 1.20–1.43 over the sample.

---

## REQUIRED BEFORE BROKER DEMO

### D1. Build position managers for the IG and OANDA adapters

Only the paper broker currently drives a `PositionBook`. A demo deployment today would attach one stop and one target per position and **nothing would trail** — no scale-out legs, no breakeven move, no time stop.

This is the largest single item of remaining engineering before demo enablement, and it is engineering rather than a user action, but it is listed here because it gates the demo step and you should know it is outstanding.

### D2. Understand that client-side exits depend on the worker being alive

A venue holds exactly one stop and one limit per position. Every strategy that declares more — a multi-leg scale-out, a trailing stop, a breakeven move, a time stop — is managed by our process. If the worker dies, the hard stop stays attached at the broker, but the trail freezes, later legs are never placed and the time stop never fires. A position meant to bank half at 1.5R runs to a stale stop instead.

**Every backtest figure assumes a worker that never dies, and that assumption is not modelled anywhere.** Decide whether you accept it, and size accordingly.

### D3. Prove the alerting actually fires, by killing the worker on purpose

Do not promote anything to demo until a deliberate worker kill produces both an error-reporter event and an alert within one poll interval. V1's worker could die at 3am with the dashboard green and Telegram silent, and nobody would know until they next looked at a page.

V2 has the watchdog, the `WORKER_DOWN` and `HEARTBEAT_STALE` events and the timer. What it has not had is a chaos test against your real alert channel. Until that test passes, an unnoticed dead worker is indistinguishable from a flat strategy.

### D4. Supply alert channel credentials

Telegram bot token and chat id, or a webhook URL. The dispatcher is built and fixture-tested; no credentials exist.

### D5. Replace the Dockerfile base image digest

`deploy/Dockerfile` carries a placeholder digest and will not build until you substitute a real `python:3.11-slim-bookworm` digest. This was deliberate — inventing a digest would be worse than leaving it obviously absent.

---

## REQUIRED BEFORE LIVE MONEY

### L1. Accept that no strategy currently qualifies

This is the most important item in the document. **Fiboki V2 has not identified a single strategy that passes its validation gates.** Campaign K1 ran 326 true trials on XAUUSD H4 and produced zero survivors. The holdout has never been consumed by anything.

That is a successful research programme, not a failed one — but it means there is nothing to promote, and no amount of infrastructure changes that.

### L2. Get professional advice on the tax treatment

Spread betting is CGT-exempt for UK retail under current HMRC treatment; CFD and spot FX gains are chargeable above the £3,000 allowance at 18% or 24%. The mirror image is that spread-bet losses cannot be offset against other capital gains, which matters if you expect early losses.

This materially affects the OANDA-versus-IBKR choice and I am not the right source for it. Speak to an accountant.

### L3. Complete the live-execution authorisation chain

V2 requires five independent controls to reach live, by design: a build-time constant, a runtime environment token (not the string `"true"`), a persisted operator authorisation record with timestamps, a per-strategy allow-list, and a parsed-hostname assertion. Tests assert that every one-, two- and three-way subset still blocks.

None of them are set, and setting them is deliberately a deploy-time act, not an API call. There is no endpoint that can enable live execution; the API returns 403 and audits the attempt.

### L4. Run the kill-switch drill and time it

PAUSE and FLATTEN are distinct and implemented. Execute both against demo, time them, and write down the result.

### L5. Close or explicitly accept the documented approximations

Static spreads, zero default slippage, no weekend triple-swap, a static financing rate over a 25-year sample, fixed-date-only holiday calendars, and bars treated as mid when the source is bid. Each is documented in code. Either close them or accept them in writing before capital is at risk.

---

## OPTIONAL

### O1. Consider Pepperstone's cTrader API as a data source

It offers raw bid and ask tick history direct from the broker's own server — the best backtest-live parity available in the comparison. It was not recommended as primary only because Protobuf-over-TCP is a much heavier integration than JSON REST. If parity matters more than integration cost, revisit it.

### O2. Interactive Brokers, if you scale past about £25k

Better execution, genuine interbank FX data, and the strongest Python ecosystem (`ib_async`). Against it: hostile historical-data pacing, no spread betting, 20,000-unit minimum spot FX orders, a USD 2.00 minimum commission that is roughly 15% of a 1% risk budget on a £2,000 account, and a persistent IB Gateway process to operate.

### O3. Local LLM runtime

The agent layer is provider-agnostic with a local-first router and a deterministic echo provider for tests. Installing Ollama or similar would let the research agents run without sending anything to a remote API. Entirely optional — nothing depends on it.

### O4. Prune the strategy roster deliberately

V1 had 64 strategies; V2 ships five seeds. Counterintuitively this is an improvement: every additional strategy inflates the trial count and raises the deflation bar every survivor must clear. Resist the urge to add strategies to feel productive.

---

## FUTURE

### F1. Re-evaluate NautilusTrader around September 2027

Conditional on v2.0 reaching general availability and an IG or cTrader adapter appearing. Today it ships exactly one non-crypto broker integration, which is why V2 stayed custom.

### F2. Run the Fibonacci falsification experiment

Compare the 0.618 level against randomly drawn levels between 0.5 and 0.7 on the same swings. If it does not beat random, the ratio is decoration and the family should be cut. The hypothesis document already specifies this experiment; nobody has run it.

### F3. Extend the campaign across instruments and timeframes

The 400-trade minimum was written for a 60-instrument campaign. A single instrument on a single timeframe mostly cannot clear it, which is why most K1 cells were rejected for having too little evidence rather than bad evidence. A genuine multi-instrument campaign is the next real research step — and it needs P4 (the full data store) first.
