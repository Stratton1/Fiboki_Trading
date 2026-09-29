# Fiboki V2 — Actions That Require You

**Generated:** 19 September 2026, at the end of the V2 build programme. **Updated 29 September 2026** after the agentic-integration wave (see `docs/v2/AGENTIC_INTEGRATION_PLAN.md` §3 and `docs/v2/BUILD_LOG.md`).

**Status line (29 September 2026, evening):** GitHub `main` and `v2/integration` are the same commit (V1 is under `legacy/v1/`, tag `v1-final`). The four launchd services (`uk.fiboki.api`, `worker`, `web`, `news`) run from the runtime checkout `~/fiboki` (`docs/v2/DEPLOYMENT.md` §2.6), `fiboki doctor` there reports 0 FAIL, and http://localhost:3000 serves the production web build. Ollama `qwen3:4b` passed the smoke test (P7) and the agent cycles are on. Campaign K3 (`engine_v3_realism`, GBP, official calendar) is running in the development checkout. Backend suite 4,802 passed; Playwright 478 passed. **Still needing you:** P1 (OANDA practice token: nothing trades forward until it is in `~/.fiboki/env`), P6 (Finnhub/FRED keys), L2 (tax/capital advice), and the decisions in `docs/v2/IMPROVEMENT_BACKLOG.md` §3.

Everything in this document is something the engineering programme could not do for you: it needs an account, a credential, a payment, a legal acceptance, a physical action, or a judgement that is yours rather than mine. Everything *else* has been built, and where an external dependency was missing I built the adapter, the interface, the fixtures and the tests so the platform is ready the day the dependency arrives.

Items are ordered by when they block you, not by effort.

---

## CRITICAL — do these before anything else

### C1. ~~Review and merge the V2 tree into your repository~~ — DONE 2026-09-29

You chose "V2 is the repository": `main` was fast-forwarded to `v2/integration`, V1 is preserved under `legacy/v1/` and at the tag `v1-final` (commit ccc3af2). Nothing in V2 imports anything from `legacy/`.

### C2. Decide what happens to the V1 production deployment

`fiboki.uk` and `api.fiboki.uk` are still serving the V1 build from 25 June 2026, including the defects in the audit. V2 does not deploy anywhere yet and has no production environment.

Your options are to leave V1 running as-is, take it down, or put a holding page on it. I would not leave a system running that presents numbers we now know are wrong, but it is your product and your call.

### C3. Remove the committed live-execution flag from V1

`render.yaml` in the V1 repository still contains `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` as a literal value. It is mitigated today only by the accident that Render is not the live host. Change it to `sync: false` or delete the file. V2's CI has a check that fails the build if any committed config sets a live-execution flag to a truthy literal; V1 has no such check.

---

### C4. ~~Rotate both operator passwords~~ — DONE 2026-09-29

Both operators now have fresh passwords stored as `scrypt$...` in `~/.fiboki/env` (values quoted; the service script reads the file without shell expansion). The passwords themselves are in the macOS Keychain, never in chat or a file: `security find-generic-password -a joe -s fiboki-operator -w` (and `-a tom`). Tell Tom his the way you would any credential, then have him change it. To rotate again: `.venv/bin/python -c "from fiboki.api.routers.auth import hash_password as h; import getpass; print(h(getpass.getpass()))"` and replace the entry.

## REQUIRED BEFORE PAPER TRADING

### P1. Open an OANDA practice account and resolve two unknowns

Free, no minimum, takes minutes: <https://www.oanda.com/uk-en/> → practice account → generate a personal access token from the account portal.

Two things must be verified on the demo before the broker decision is final, and neither can be answered from documentation:

**Can the v20 REST API place orders on a spread-betting-enabled sub-account?** This decides whether you get UK spread-bet tax treatment. The logic is strong — spread betting is a flag on a v20 sub-account and the API addresses sub-accounts by `accountID` — but OANDA does not state it anywhere I could find. Create a spread-bet sub-account, then attempt a small order against it through the API.

**How far does the candle endpoint's pricing diverge from your own executable stream?** OANDA's own UK help page says the live pricing feed "could be different from the historical data" because of pricing segments and account types. Run the recorder (P2) alongside candle pulls for a few weeks and diff them.

### P2. Start the executable-price recorder

This is the single highest-value thing you can start today, and its value is proportional to elapsed time. Every backtest Fiboki has ever run used somebody else's prices. `src/fiboki/data/recorder.py` is built, tested and crash-safe; it needs a live quote source.

Once P1 gives you a practice token, point the recorder at OANDA's pricing stream and leave it running. In six months you will have the only dataset that actually predicts your fills.

### P3. Extend the economic calendar beyond 2024 and four currencies, and wire it in

Since 2026-09-28 `src/fiboki/marketstate/fixtures/scheduled_events_official.json` carries 339 dated, scheduled events taken only from the publishers' own pages: FOMC, ECB, Bank of England and Bank of Japan decisions, US NFP and CPI (BLS), and UK CPI, monthly GDP and labour-market releases (ONS). `load_official_calendar()` loads it; `fiboki calendar status` shows what it covers. There is still **no network access** in the module.

What remains yours:

1. **Before 2024 and other currencies.** The fixture is declared complete from 2024-01-01 to 2026-12-04 and for USD, EUR, GBP and JPY only. Outside that, blackout queries still answer "not in blackout", so a backtest over 2005-2023, or on AUD, CAD, CHF or NZD pairs, trades straight through FOMC and NFP. Extend it from official sources (central bank and statistics-office archives) in the same shape; commercial aggregators such as ForexFactory and Investing.com forbid automated extraction.
2. **Refresh before the declared end.** Future dates are schedules and can move (the Fed marks each date tentative until the preceding meeting; the 2025 US appropriations lapse cancelled a month of NFP and CPI). Rebuild the file before 2026-12-04 and whenever a publisher revises its calendar.
3. **Wired in (2026-09-29).** `build_replay_session` and `discovery.campaign.run_cell` now load the official calendar by default and pass it to the risk gateway's `event_blackout` check and to validation; the evaluator cache key includes the calendar hash. A replay test proves an entry on the 2024-03-08 NFP bar is blocked. Consequence: every earlier ladder or campaign result for the five seed strategies ran with blackouts silently unenforced and is superseded by the next run. On H4 bars the 15-minute window (measured from bar open) rarely fires; that is a known limitation to revisit.
4. **Impact choice.** Every fixture event is `high`, including UK labour and monthly GDP, which the recurring taxonomy rates `medium`. Lower them if you disagree.

One trap still worth knowing: feeds return *revised* actuals for historical dates, not the print the market traded. The official fixture stores no figures at all, only scheduled times; any future feed with actuals is optimistic unless it preserves first prints.

### P4. Provide the full historical data store

V2 was built and tested against two instruments (EURUSD and XAUUSD, H1 and H4) because that is what fitted in the container. Your 7.2 GB HistData store is on your Mac.

Before you use it, know what the migration found: **its timestamps are EST-without-DST stamped as UTC, so every bar is five hours out; its prices are bid, not mid; its volume is identically zero; and EURUSD H1 contains a negative-price sentinel bar (OHLC all −0.0001) at 2001-09-11 20:00 that sits in the *canonical* store.** `fiboki data migrate-v1` applies the timestamp correction and declares the price basis honestly, and the integrity layer rejects the EURUSD datasets rather than reading them as clean.

This alone means every V1 research result is stale, independently of the engine bugs.

### P5. Decide the account currency question

The engine refuses to invent an exchange rate. If you run a GBP account against USD-quoted instruments you must supply a real GBP/USD series, or explicitly acknowledge the approximation in writing. The XAUUSD work so far used a USD account precisely to sidestep this. V1 reported USD P&L as GBP, an error that ranged 1.20–1.43 over the sample.

### P6. Choose the licensed news API and start the headline recorder

`fiboki news record --loop` records central-bank headlines from twelve official feeds today with no account at all. Its value, like the price recorder's, is proportional to elapsed time: a headline's first-seen instant cannot be reconstructed later. Start it now on the machine that stays on (`fiboki news status` exits 1 if it has stopped).

The decision that is yours: **which licensed vendor API, if any, sits beside the official feeds.** The plan (D-A5) names two; both clients are built and both are off until a key is set.

- **Finnhub (personal tier)**: `FIBOKI_FINNHUB_API_KEY`. General and forex news categories. Check that the personal tier's terms allow storing headlines for your own research; they are unlikely to allow redistribution.
- **Marketaux**: `FIBOKI_MARKETAUX_API_KEY`. The token has to travel in the URL (their API design). The free plan's daily request cap is why the recorder calls it at most every 15 minutes by default; change `--marketaux-min-interval` to match the plan you buy.

Neither has been tested against the live service, because no key exists in the build environment; the first live poll is the test. Separately, for macro vintages, register a free FRED API key and set `FIBOKI_FRED_API_KEY`; FRED's terms require the notice "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." wherever FRED data is shown.

**Update 2026-09-29: more free sources, each with its terms recorded.** Every source, its cost and what its terms allow is now in one table (`docs/v2/DATA_ARCHITECTURE.md` §14.4, from `fiboki.data.sources.describe_sources()`). What changes for you:

- **Nothing to do for the BIS speeches feed**: `fiboki news record` now records thirteen feeds (the twelve bank feeds plus the BIS central bankers' speeches). BIS terms are non-commercial.
- **GDELT (free, open, any use with a citation)**: set `FIBOKI_GDELT_ENABLED=true` to add six curated FX/gold/index/central-bank headline queries. Each poll takes about 30 s more, because GDELT allows one request every five seconds.
- **Finnhub**: the free tier's 60 calls per minute is now enforced by the recorder itself. **The Finnhub economic calendar is premium-only** ("Premium Access Required" in Finnhub's own API schema). The code is built; it only works if you buy a plan that includes it. It is a comparison calendar either way, never the one used for blackouts.
- **ForexFactory weekly calendar: your decision, and my recommendation is no.** Their notices say "The copying, republication or redistribution of FEED, in part or in whole, is explicitly prohibited", and no licence for the export exists. It is built, off, and only turns on with `FIBOKI_FF_CALENDAR_OPT_IN=true`. If you turn it on: personal comparison only, never share or commit the files. Its one real use is `calendar_diff`, which lists releases the feed has and our official calendar does not, so you can check them at the publisher.
- **Retail positioning**: Myfxbook Community Outlook via its official API (`FIBOKI_MYFXBOOK_EMAIL`, `FIBOKI_MYFXBOOK_PASSWORD`; free, 100 requests a day, personal use; the API puts your password in the URL, so use a Myfxbook account with a password you use nowhere else). OANDA position and order books (`FIBOKI_OANDA_BOOKS_TOKEN`, `FIBOKI_OANDA_BOOKS_ENVIRONMENT=practice|live`, read-only) are built, but OANDA appears to have withdrawn those endpoints in 2024; your first poll will tell us.
- **FRED cross-asset pack** (2y and 10y yields, broad dollar, VIX, WTI) needs the same `FIBOKI_FRED_API_KEY`. VIX is CBOE-copyrighted: personal research only.
- **A licence question you should settle before anything is sold**: HistData states no licence, and Dukascopy's site terms forbid robots and building a database from the site. Both are marked `opt_in_unclear`. The Dukascopy downloader stays unbuilt until Dukascopy answers in writing.
- **Not free, not built**: Reuters (LSEG licence) and AP (customer licence). Not built because the terms forbid it: Investing.com, TradingView's undocumented endpoints, scraping Myfxbook or ForexFactory pages.

None of the new sources has been exercised against the live service from the build environment (no keys; GDELT refused this environment's shared address with 429). The calendars, positioning recorder and FRED pack have no `fiboki` CLI command yet.

---

## REQUIRED BEFORE BROKER DEMO

### D1. ~~Build position managers for the IG and OANDA adapters~~ — BUILT

`src/fiboki/broker/position_manager.py` now holds `VenuePositionManager`, which drives the *same* `PositionBook` the backtester and the paper adapter drive, against IG, OANDA or the simulated venue. It attaches the hard stop and the first target at entry and then issues amendments and partial closes as bars close for everything else — later legs, trail steps, breakeven moves, time stops.

`tests/integration/test_venue_position_manager.py` asserts the trade ledger, the per-fill leg ledger, the rejection counts and the final balance are byte-identical to the backtester's across all three venues.

**Nothing here is a user action any more, but two things below are.**

### D2. Accept, in writing, the managed-exit exposure of every strategy you promote

A venue holds exactly one stop and one limit per position. Every strategy that declares more — a multi-leg scale-out, a trailing stop, a breakeven move, a time stop — is managed by our process. If the worker dies, the hard stop stays attached at the broker, but the trail freezes, later legs are never placed and the time stop never fires. A position meant to bank half at 1.5R runs to a stale stop instead. **That is not fixable in client code and is not fixed.**

What has changed is that it is now *measured* and *gated*:

* **Measured.** The manager publishes `managed_exit_exposure` — the account-currency risk-to-the-attached-stop of the open size whose intended exit is not resting at the venue — on every bar, as a Prometheus gauge (`fiboki_managed_exit_exposure`). A three-leg document has leg one resting at the venue; the size behind legs two and three counts. A trail-only document (`donchian_breakout_atr`) has no target at the venue at all, so its whole size counts, and it remains the highest-exposure shape we run. A plain stop-and-one-target document reads exactly zero, because a dead worker changes nothing about it.
* **Gated.** `require_venue_realisable()` **refuses** to promote a strategy whose exit policy a venue cannot hold, unless the caller passes `accept_managed_exit_exposure=True` and names an operator. There is deliberately no config file and no environment variable that grants this: it is an argument at the call site, because the acceptance belongs to whoever is doing the promoting. That is the documented degraded mode.

**Your action:** for each of the twelve strategies, decide whether the outcome a dead worker produces — the hard stop and, at most, the first take-profit leg — is acceptable, and size accordingly. Record the decision with the operator's name. **Every backtest figure still assumes a worker that never dies.** That assumption is now measured at every bar rather than merely stated, but it is still an assumption and it is still optimistic.

### D2b. Treat a stale heartbeat as a position-management incident

When the worker's heartbeat goes stale, the size of the incident is `managed_exit_exposure` at that moment — not zero, and not the whole book. Wire that gauge into whatever you page on, alongside `HEARTBEAT_STALE` and `WORKER_DOWN`.

After any restart the manager's `resume()` runs before trading: it re-derives each recovered position's intent from the durable order record (the full take-profit ladder is persisted on the intent for exactly this purpose) and re-attaches what the venue can hold. Two things it cannot recover, and reports rather than guesses: how far a trail had already moved while we were dead (it re-anchors to the stop the venue actually holds, which can only be at or better than where we left it), and `bars_held` (which restarts at zero, so **a time stop is longer than the document says across a restart**).

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

## DESKTOP MIGRATION (MacBook to Mac desktop, local models on llama.cpp)

Added 2026-09-29. Full procedure: `docs/v2/DEPLOYMENT.md` §2 and `docs/v2/OPERATIONS.md` §13.
Everything here keeps the deployment in paper mode; none of it touches a live control.

### M1. On the MacBook, before you leave it

- [ ] Commit or push everything you want to keep (`git status` clean). `fiboki doctor` warns on a dirty tree.
- [ ] `scripts/launchd-install.sh --unload` (or `scripts/dev-down.sh`), then `scripts/backup.sh`. Keep the `.tar.gz` **and** its `.sha256`.
- [ ] Copy the migrated market-data store (`var/datastore`, with its `.fiboki-data-root` and `catalogue.db`) to an external disk, or re-run the backup with `--include-datastore`.
- [ ] Put the contents of `~/.fiboki/env` (or whatever secrets you exported by hand) into your password manager. **Do not** copy `~/.zsh_history`, `.envrc` files or notes with keys in them to the new machine. If a secret was ever typed on a command line, rotate it on the desktop rather than carrying it over.

### M2. On the desktop

- [ ] `brew install python@3.11 node git sqlite llama.cpp`.
- [x] `git clone` the repository **outside `~/Documents`** (macOS TCC blocks LaunchAgents there; `DEPLOYMENT.md` §2.6). On the MacBook this is `~/fiboki`; on the desktop use the same path so the runbooks apply unchanged.
- [x] `scripts/desktop-install.sh --check`, then `scripts/desktop-install.sh` until it prints no MISSING line.
- [x] `~/.fiboki/env` (mode 600): `FIBOKI_OPERATORS` for Joe and Tom as `scrypt$` hashes (C4); the file is shared by both checkouts. Still to add: `FIBOKI_OANDA_PRACTICE_TOKEN` / `FIBOKI_OANDA_PRACTICE_ACCOUNT_ID` (P1), `FIBOKI_FINNHUB_KEY`, `FIBOKI_FRED_KEY` (P6).
- [x] Market-data store in `var/datastore` (60 instruments, 16.5M bars, verified by `fiboki doctor`). On the desktop: copy it again, or `scripts/backup.sh --include-datastore` on this machine first.
- [ ] Download one model (M3) into `~/Models`, verify its SHA-256 against the Hugging Face page, `scripts/llama-server.sh --print`. On the MacBook (8 GB) Ollama `qwen3:4b` is used instead; llama.cpp with a larger model is the desktop plan.
- [x] ~~`_build_provider` change~~ — made (`for_local_server`; detects llama.cpp by `/props`, Ollama otherwise).
- [x] Agent variables set in `~/.fiboki/env`; `FIBOKI_AGENT_CYCLES=true` after the smoke test passed (P7).
- [x] `.venv/bin/fiboki doctor`: 0 FAIL in `~/fiboki`; `scripts/launchd-install.sh --services api,worker,web,news --load` done; `uk.fiboki.llama` and `uk.fiboki.paper` are not loaded (no llama.cpp model; no OANDA token).
- [ ] System Settings: log in automatically, never sleep, restart after a power failure. LaunchAgents only run while you are logged in. (Your action; I cannot change system settings.)
- [ ] Rehearse a restore once (`OPERATIONS.md` §13.2) before you rely on the backups.
- [x] ~~Web build with `NEXT_PUBLIC_FIBOKI_API=""`~~ — fixed (`apiOrigin()` treats empty as same-origin).

### M3. Which model for which Mac

`scripts/llama-server.sh` picks the row for the machine's memory. The choice is a starting point
made on three stated grounds only: an Apache-2.0 licence, a file small enough to leave room for
the KV cache at a 16,384-token context plus the rest of Fiboki, and a native context of at least
that length. **No benchmark was run and none is quoted.** Measure the model with
`smoke_test_provider` and the offline eval harness before trusting it.

| Mac memory | File (quantisation) | File size | Source |
|---|---|---|---|
| 32 GB | `Qwen3-14B-Q5_K_M.gguf` | 10.51 GB | [Qwen/Qwen3-14B-GGUF](https://huggingface.co/Qwen/Qwen3-14B-GGUF) |
| 64 GB | `Qwen3-32B-Q5_K_M.gguf` | 23.21 GB | [Qwen/Qwen3-32B-GGUF](https://huggingface.co/Qwen/Qwen3-32B-GGUF) |
| 128 GB | `Qwen3-32B-Q8_0.gguf` | 34.82 GB | [Qwen/Qwen3-32B-GGUF](https://huggingface.co/Qwen/Qwen3-32B-GGUF) |
| 128 GB, alternative (not the script's default) | `gpt-oss-120b-MXFP4.gguf` | 63.39 GB | [ggml-org/gpt-oss-120b-GGUF](https://huggingface.co/ggml-org/gpt-oss-120b-GGUF), model card [openai/gpt-oss-120b](https://huggingface.co/openai/gpt-oss-120b) |

Where each fact comes from:

- File sizes and licences: the Hugging Face model API (`/api/models/<repo>?blobs=true`), read
  2026-09-29. All four are Apache-2.0.
- Qwen3 context: the Qwen3-14B-GGUF model card, "Context Length: 32,768 natively and 131,072
  tokens with YaRN". A 16,384 context therefore needs no RoPE scaling.
- Qwen3 thinking: the same card documents `/think` and `/no_think`; the script disables thinking
  server-side with `--reasoning-budget 0` (llama.cpp PR #13771) so the JSON-schema grammar
  constrains the answer itself. The card also advises against greedy decoding in thinking mode;
  Fiboki decodes at temperature 0 for reproducibility, which is a property to measure, not assume.
- gpt-oss-120b: its model card says it "fit[s] into a single 80GB GPU" with MXFP4 weights. It is
  a reasoning model; whether `--reasoning-budget 0` and grammar-constrained output behave well
  with it was **not** verified here, which is why it is not the default.
- Server flags and endpoint shapes: the llama.cpp server README
  ([tools/server/README.md](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)):
  `/props` (`default_generation_settings.n_ctx`, `model_path`, `build_info`), `/v1/models`
  (`id` is the `--alias` or the `-m` path), `response_format` on `/v1/chat/completions`, and
  `-c`, `-np`, `-fa`, `--alias`, `--reasoning-budget`. Minimum build b6325 (`-fa on`, PR #15434).

macOS caps how much unified memory the GPU may wire, below the physical total; if llama-server
fails to allocate, drop one tier (`LLAMA_SERVER_ARGS=--tier 32` on a 64 GB machine) rather than
raising the limit.

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

### P7. ~~Run the first real-model agent smoke test (Ollama on the Mac)~~ — DONE 2026-09-29

Ollama `qwen3:4b` on the MacBook: `smoke_test_provider` returned `ok: true`, `model_digest` `sha256:359d7dd4...` (matches `ollama list`). Two provider changes came out of it: Ollama requests send `think: false`, and a leading closed `<think>` block is stripped before JSON parsing (Qwen3 otherwise spends its whole output thinking). `FIBOKI_AGENT_CYCLES=true` is set with `FIBOKI_AGENT_CYCLE_TARGET=donchian_breakout_atr:XAUUSD:H4` at 02:15 UTC and headline scans every 15 minutes; the worker's audit ledger is `~/fiboki/var/agents/audit.jsonl`. **What is still unmeasured:** agent quality. Run `fiboki.agents.evals.run_evals` over the ledger after the first few nights, and the 20-run acceptance protocol (`FRONTEND_OVERHAUL_PLAN.md` revision, item 12) before trusting any output beyond research notes. The original procedure, for the desktop or another model:

```bash
ollama pull qwen2.5:7b-instruct      # or any 7B–30B instruct model you prefer
cd ~/Documents/Claude/Projects/Fiboki
.venv/bin/python -c "
import json
from fiboki.agents.providers import LocalHTTPProvider, ollama_http_client, smoke_test_provider
p = LocalHTTPProvider.for_ollama('qwen2.5:7b-instruct', client=ollama_http_client())
print(json.dumps(smoke_test_provider(p).as_dict(), indent=2))
"
```

Expect `"ok": true` and a `model_digest` matching `ollama list`. Then set `FIBOKI_AGENT_CYCLES=true`, `FIBOKI_AGENT_PROVIDER=local` and the model name (table in `docs/v2/OPERATIONS.md`) and restart the research worker; the nightly research cycle and failure investigations will run on the local model with every step in `var/agents/audit.jsonl`. Run `fiboki.agents.evals.run_evals` over that ledger after the first night.
