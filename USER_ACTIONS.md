# Fiboki V2 — Actions That Require You

**Generated:** 19 September 2026, at the end of the V2 build programme. **Updated 29 September 2026** after the agentic-integration wave (see `docs/v2/AGENTIC_INTEGRATION_PLAN.md` §3 and `docs/v2/BUILD_LOG.md`).

**Status line (8 October 2026):** The production promotion bar is now `v2.1.0-calibrated`
(R3, switched on your instruction). V1 is fully down: Vercel paused, and on Railway both V1
services are offline with their GitHub source disconnected (the API on 30 September, the V1
worker `humorous-grace`, which had still been running, on 8 October); only the V1 Postgres
database remains (C2). The executable-price recorder is built and running (P2). Superseded files
are gathered in `~/Fiboki_Old` (M1). A one-folder migration kit exists
(`scripts/migration-kit.sh`, M1). Still needing you: the Finnhub and FRED keys (P6, five minutes
each), P1's two venue questions, the pooled-universe pre-registration decision (R3), D2 to D4
before any broker demo, and the desktop itself (M2, M3).

**Status line (3 October 2026):** V1 is down (Vercel paused, Railway deployment removed). E-1 and
E-2 are complete and decided: the audited gate set has no power against edges up to 0.12 per trade
on either data-generating process, every proposed loosening keeps the false-promotion rate at zero,
three are published as `v2.1.0-calibrated` (not yet the production bar, R3), and no gate design can
restore power because the documents produce a median of 330 trades against the 425 a 0.08 edge
needs; the recommended next experiment is pooled-universe candidates (R3). The OANDA practice token
is in place and verified (P1). The runtime checkout `~/fiboki` is at 7e487a7, six commits behind
`main` (f18ec8c); the paper service does not use the changed code, but pull it before the next
campaign so the ladder fix (zero-dispersion trials) is in force there too.

**Status line (29 September 2026, evening):** GitHub `main` and `v2/integration` are the same commit (V1 is under `legacy/v1/`, tag `v1-final`). The four launchd services (`uk.fiboki.api`, `worker`, `web`, `news`) run from the runtime checkout `~/fiboki` (`docs/v2/DEPLOYMENT.md` §2.6), `fiboki doctor` there reports 0 FAIL, and http://localhost:3000 serves the production web build. Ollama `qwen3:4b` passed the smoke test (P7), the agent cycles are on, and the first real-model workflow (an event scan) has run; on this 8 GB MacBook the model calls time out while K3 runs (swap-bound), so event scans are hourly until the desktop. Campaign K3 (`engine_v3_realism`, GBP, official calendar) is running in the development checkout. Backend suite 4,802 passed; Playwright 478 passed. **Still needing you:** P1 (OANDA practice token: nothing trades forward until it is in `~/.fiboki/env`), P6 (Finnhub/FRED keys), L2 (tax/capital advice), and the decisions in `docs/v2/IMPROVEMENT_BACKLOG.md` §3.

Everything in this document is something the engineering programme could not do for you: it needs an account, a credential, a payment, a legal acceptance, a physical action, or a judgement that is yours rather than mine. Everything *else* has been built, and where an external dependency was missing I built the adapter, the interface, the fixtures and the tests so the platform is ready the day the dependency arrives.

Items are ordered by when they block you, not by effort.

---

## CRITICAL — do these before anything else

### C1. ~~Review and merge the V2 tree into your repository~~ — DONE 2026-09-29

You chose "V2 is the repository": `main` was fast-forwarded to `v2/integration`, V1 is preserved under `legacy/v1/` and at the tag `v1-final` (commit ccc3af2). Nothing in V2 imports anything from `legacy/`.

### C2. Decide what happens to the V1 production deployment — DONE 2026-09-30/10-01: V1 is down

You decided on 2026-09-30 to take V1 down and make V2 the live site. Verified 2026-10-01 00:10 UTC:
`https://fiboki.uk/` answers HTTP 503 `x-vercel-error: DEPLOYMENT_PAUSED` (the Vercel project
`fiboki-trading` is paused; Unpause reverses it), and `https://api.fiboki.uk/` answers Railway's
fallback 404 (`x-railway-fallback: true`, "Application not found"): the V1 API deployment was
removed and the `Fiboki_Trading` Railway service's GitHub source was disconnected on 2026-09-30, so
pushes to `main` no longer trigger a (failing) Railway build. The Railway service, its custom
domain and its variables still exist; delete the service when you no longer want the DNS record.

**Found and fixed 2026-10-08:** the Railway project `ravishing-benevolence` also held the V1
*worker*, `humorous-grace`, which had been online for three months (its last good deploy was the
V1 "unrealized fleet P&L" fix) and had been attempting a failed rebuild on every push to `main`.
Its deployment was removed and its GitHub source disconnected on 2026-10-08. It held the IG demo
credentials V1 used; it now runs nothing.

- [ ] **Your decision: the V1 Postgres database** in the same Railway project is still online
  (and billed). V2 does not use it. If you want V1's history, export it first (Railway → Postgres
  → Data, or `pg_dump` with its connection string); then delete the database and its volume. Then
  delete the whole Railway project, and remove the IG demo API key that V1 used from your IG
  account settings, since nothing should hold it any more.

**V2 cannot be "the live site" on Vercel as it stands, and this is a design property, not a bug.**
V2's web tier is thin (display and controls); every number comes from the FastAPI service and the
workers that run on your Mac against the local data store, the ledgers and the OANDA practice
account. A Vercel deployment of `apps/web` would render nothing without an API it can reach. The
honest routes to a public V2, in order of preference:

1. **Private access to the Mac** (recommended for the operator phase): Cloudflare Tunnel from the
   Mac to `fiboki.uk` with Cloudflare Access in front (your Google account as the identity; Tom's
   later). No inbound port, no public exposure of the API, the workstation you already run. Cost:
   free tier. Needs: the fiboki.uk DNS moved to Cloudflare (where it is hosted today is not known to
   me), `cloudflared` as a launchd service (`uk.fiboki.tunnel`), and the web/API CORS origin set to
   `https://fiboki.uk`. Half a day including tests; I can do all of it except the DNS transfer and
   the Access policy sign-off.
2. **Hosted API + web** (Railway/Fly for the API and workers, Vercel for the web, Postgres for the
   ledgers, the data store on a volume or S3): the "real" deployment, but it moves the paper engine,
   the OANDA credentials and 2 GB of bars off the Mac and onto a paid host, and the workers need a
   machine that runs 24/7 anyway. £20–60/month. Only worth it once a strategy has survived and the
   paper forward is worth keeping alive without your laptop. Not now.
3. A public V2 with no API (static marketing page) is possible today but is not "V2 live"; it is a
   brochure.

Until one of these is done, `fiboki.uk` paused is the truthful state: nothing public claims numbers
we cannot stand behind.

### C3. ~~Remove the committed live-execution flag from V1~~ — DONE (verified 2026-10-08)

V1 lives in this repository under `legacy/v1/`, and `legacy/v1/render.yaml` now declares
`FIBOKEI_LIVE_EXECUTION_ENABLED` with `sync: false` (no value committed). Render is not running
V1, and the V1 Railway services are offline (C2).

### C4. ~~Rotate both operator passwords~~ — DONE 2026-09-29

Both operators now have fresh passwords stored as `scrypt$...` in `~/.fiboki/env` (values quoted; the service script reads the file without shell expansion). The passwords themselves are in the macOS Keychain, never in chat or a file: `security find-generic-password -a joe -s fiboki-operator -w` (and `-a tom`). Tell Tom his the way you would any credential, then have him change it. To rotate again: `.venv/bin/python -c "from fiboki.api.routers.auth import hash_password as h; import getpass; print(h(getpass.getpass()))"` and replace the entry.

## REQUIRED BEFORE PAPER TRADING

### P1. OANDA practice account — TOKEN IN PLACE AND VERIFIED 2026-10-03; two unknowns remain yours

`~/.fiboki/env` holds `FIBOKI_OANDA_PRACTICE_TOKEN` and `FIBOKI_OANDA_PRACTICE_ACCOUNT_ID`, and a
read-only call to `api-fxpractice.oanda.com/v3/accounts/<id>/summary` on 2026-10-03 answered 200:
a GBP practice account created 2026-09-22, balance 100,000, no open trades, positions or orders.
The paper-forward service (`uk.fiboki.paper`) is running under launchd. The token is never printed
or logged; nothing here writes to OANDA (the paper composition reads candles and pricing only).

Free, no minimum, takes minutes: <https://www.oanda.com/uk-en/> → practice account → generate a personal access token from the account portal.

Two things must be verified on the demo before the broker decision is final, and neither can be answered from documentation:

**Can the v20 REST API place orders on a spread-betting-enabled sub-account?** This decides whether you get UK spread-bet tax treatment. The logic is strong — spread betting is a flag on a v20 sub-account and the API addresses sub-accounts by `accountID` — but OANDA does not state it anywhere I could find. Create a spread-bet sub-account, then attempt a small order against it through the API.

**How far does the candle endpoint's pricing diverge from your own executable stream?** OANDA's own UK help page says the live pricing feed "could be different from the historical data" because of pricing segments and account types. Run the recorder (P2) alongside candle pulls for a few weeks and diff them.

### P2. The executable-price recorder — BUILT AND STARTED 2026-10-08

**What it is, plainly.** Every backtest assumes a spread from a fixed table. What a live account
would actually have paid depends on the spread at the moment of each trade, which widens at the
open, around news and at rollover. Those prices cannot be downloaded afterwards; they only exist if
something records them as they happen. The recorder does that: every 30 seconds it reads OANDA's
practice prices (best bid and ask) for the 20 research instruments and appends each changed quote
to a crash-safe log in `var/quotes/`. After a few weeks it gives the measured spread by hour and day
of week, which replaces the static spread table; after a few months it says how far OANDA's candle
prices sit from its executable prices (P1's second question).

**How it runs.** `fiboki quotes record --loop` (code: `broker/oanda_quotes.py`), as the launchd
service `uk.fiboki.quotes`. Practice host only, read-only: it cannot place or change anything.
`fiboki quotes status` exits 1 when nothing has been written for ten minutes. Interval:
`FIBOKI_QUOTES_INTERVAL` in `~/.fiboki/env` (default 30, minimum 5). Disk: roughly 15 to 25 MB a
day at 30 seconds for 20 instruments (an estimate; check `fiboki quotes status` after a day).

**Nothing to do** except keep the machine on. On the desktop, install it with
`scripts/launchd-install.sh --services quotes --load`.

### P3. Extend the economic calendar beyond 2024 and four currencies, and wire it in

**What it is, plainly.** Strategies are blocked from opening trades in a window around
high-impact scheduled events (central-bank decisions, US payrolls and CPI, UK CPI and GDP). That
block only works for events the platform knows about. Today it knows the official dates for USD,
EUR, GBP and JPY from January 2024 to 4 December 2026. Outside that (2005 to 2023 in every
backtest, and AUD, CAD, CHF and NZD at any date) a backtest trades straight through events a live
operator would avoid, which flatters some strategies and hurts others. Two jobs remain: extend the
list backwards and to the other four currencies from the publishers' own archives, and refresh it
before 4 December 2026. The source shortlist in `docs/v2/research/DATA_SOURCES_2026-10.md` (BLS,
BEA, ONS, Bank of Canada, RBNZ, RBA and others) names the official calendars to use. This is
collation work I can do; it needs no account.

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

**The two keys, step by step (about five minutes each).** Full detail, limits and terms:
`docs/v2/research/DATA_SOURCES_2026-10.md` §A.

1. **Finnhub (free, personal use).** Sign up at https://finnhub.io/register (name, email,
   password; pressing Sign Up accepts their terms and confirms personal, non-professional use).
   Your key is on https://finnhub.io/dashboard. Free plan: 60 calls a minute; forex and general
   news are free; the economic calendar is premium only. Read their terms of service in a browser
   before relying on any right to store headlines (their robots file blocks automated reading of
   it).
2. **FRED (St. Louis Fed, free).** Create an account at
   https://fredaccount.stlouisfed.org/login/secure/ ("Create New Account"; untick the newsletters),
   then request a key at https://fredaccount.stlouisfed.org/apikeys. The key is 32 lower-case
   letters and digits; 120 requests a minute. Wherever FRED data is shown you must display: "This
   product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of
   St. Louis."
3. Add both to `~/.fiboki/env` (never paste them into chat or a terminal command):
   ```
   FIBOKI_FINNHUB_API_KEY=<your Finnhub key>
   FIBOKI_FRED_API_KEY=<your FRED key>
   ```
   These are the names the code reads. (An earlier line in M2 said `FIBOKI_FINNHUB_KEY` and
   `FIBOKI_FRED_KEY`; those names are read by nothing and have been corrected.) Then restart the
   headline recorder: `launchctl kickstart -k gui/$(id -u)/uk.fiboki.news`, and run
   `.venv/bin/fiboki doctor`.

**More free sources (researched 2026-10-08, 37 beyond the 34 already in the registry).** The
catalogue, with each source's terms on storage and redistribution, is in
`docs/v2/research/DATA_SOURCES_2026-10.md` §B. The ten worth wiring first for FX, indices and gold:
the BLS release schedule and API; the BEA schedule and API; the US Treasury yield curve with the Fed
H.10 (FX rates) and H.15 (interest rates) releases; Eurostat; the ONS release calendar; the Bank
of Canada; the RBNZ and the Riksbank; the FOMC, ECB, BoE and BoJ meeting calendars; the BIS data
portal; and the US EIA (oil, which moves CAD and NOK). Findings that matter: there is no usable
free general-news API beyond Finnhub and GDELT (NewsAPI's free plan forbids production use and
delays 24 hours; Alpha Vantage, Tiingo and FMP news are paid; EODHD allows 20 calls a day); CME
Group's site forbids scripted access; the IMF forbids bulk automated download.

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

**The options, plainly (decide per strategy; recorded with your name when you promote).**

1. **Keep every exit at the broker (no exposure).** Promote only strategies whose exits are one
   hard stop and one take-profit, which the venue holds even if Fiboki dies. Simplest and safest;
   it rules out trailing stops, breakeven moves, time stops and scale-outs, which several seed
   documents use (`donchian_breakout_atr` trails).
2. **Accept the exposure and size for the worst case.** Keep the managed exits, and size so that
   the dead-worker outcome (the hard stop, plus at most the first take-profit leg) is a loss you
   accept. The platform measures this every bar (`managed_exit_exposure`). This is what the gate
   expects you to write down.
3. **Use OANDA's native trailing stop (to build).** OANDA's v20 API supports a trailing stop held
   at the broker (`trailingStopLossOnFill`, a price distance). For trail-only strategies that would
   move the trail from Fiboki to OANDA and remove most of the exposure. Not built today: the OANDA
   adapter attaches a fixed stop and a single take-profit. A bounded piece of work if you want it.
4. **Flatten on silence (to decide).** Have the watchdog arm the kill switch's FLATTEN when the
   worker's heartbeat has been stale for a set time. It closes positions rather than leaving them
   unmanaged, at the cost of closing good trades during a restart. Not built: it changes the
   kill switch's never-automatic design, so it is your call.
5. **Guaranteed stops** (IG only, for a premium) remove gap risk on the hard stop but not the
   managed-exit problem; noted for completeness, as OANDA is the chosen broker.

My recommendation: option 1 for the first demo promotions, and option 3 as the next build if a
trailing strategy is ever the one that passes, because it removes the risk rather than accepting it.

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

## RESEARCH DECISIONS THAT ARE YOURS

### R1. ~~File E-2 (gate calibration, the step after E-1)~~ — FILED and DECIDED 2026-10-03 (see the ledger's E-2 result; summary in R3)

`research/preregistration/gate_calibration_e2.json` is a DRAFT (2026-10-01). It names the candidate
gate sets built from the audit's section 3.1 proposals (`validation/gates.py`,
`GATE_SET_V2_1_CANDIDATES`, none promotable), states before any result what each is expected to do,
and fixes the decision rule: a replacement is admitted only if its size stays at or below 0.05 on
both E-1 processes; the calibrated set is the audited set with every admitted replacement, published
as `v2.1.0-calibrated` only if its own size holds; `c_dsr_family` (deflation against the candidate's
own trials only) is measured and never admitted. Read it, change what you disagree with, then set
`status` to `FILED <date>` with `filed_at`, `filed_commit` and a `decision_date` after both E-1
processes have run. Until it is filed no candidate-set rate is read or quoted; both E-1 result files already hold
them, sealed, so filing costs no compute: say "file E-2" and the reading follows the same day. What you are deciding: that these, and only these, are the loosenings
on the table, and that the deflation's trial count is a separate experiment (E-3), not a knob.

### R3. Confirm the switch to `v2.1.0-calibrated`, and choose the next experiment

E-2's answer (ledger, E-2 result): every section 3.1 loosening keeps size at 0/400 on both
processes, so three are admitted and published as `GATE_SET_V2_1_CALIBRATED`; none of them, nor
all of them, nor even dropping deflation entirely, gives usable power at a 0.08 per-trade edge,
because the documents produce a median of 330 trades and a 95% test of that edge needs 425
(MinTRL). The constraint is trades per candidate, not thresholds.

- [x] **Switched 2026-10-08** on your instruction ("confirm switch"). `PRODUCTION_GATE_SET` in
  `validation/gates.py` names `v2.1.0-calibrated`; `lifecycle/promotion.py`, the ladder's default,
  `validation/run.py`, discovery campaigns and the agents' promotion-gate job all read it. The
  rung-0 trade floor is now 150 (the MinTRL gate then decides). Measured effect on both E-1
  processes: no verdict changes; candidates with 150 to 399 trades are measured through every rung
  instead of ending at rung 0. Every stored validation report was produced under `v2.0.0-audit`
  and is refused by promotion until re-run (none was promotable).
- [ ] **Choose the next experiment.** My recommendation is to pre-register pooled-universe
  candidates (one document across its 16 series as a single candidate, ~5,000 trades), which is the
  only route that makes a 0.08 edge testable at all; the alternatives are to accept that only edges
  of 0.12+ per trade can ever be certified, or lower timeframes where costs dominate. E-3 (effective
  campaign trial count) is demoted: H2 failed.

### R4. Is there a better way to run the platform? (review of 30 projects, 8 October 2026)

Full report: `docs/v2/research/PLATFORM_SURVEY_2026-10.md` (NautilusTrader, QuantConnect Lean,
freqtrade, pysystemtrade, vectorbt, backtrader, zipline-reloaded, qlib, FinRL, Lumibot, OctoBot,
Hummingbot, Jesse, backtesting.py, bt, quantstats, OpenBB, ArcticDB, RQAlpha, barter-rs,
hftbacktest, StockSharp, mlfinlab, ccxt, OANDA's own client libraries and others). One limit: this
session's GitHub access is scoped to Fiboki's own repository, so star counts and licences came from
the deps.dev mirror and release registries, and "last push" from release dates; the report says so.

**Answer: keep the architecture; adopt three patterns; replace nothing.**

- Nothing surveyed is more rigorous on validation and cost realism than Fiboki's ladder, holdout
  registry, versioned gates and execution controls; most stop at "beware of overfitting".
- **Do not move to NautilusTrader or Lean now.** Nautilus is the closest peer (one strategy code
  path for backtest, sandbox and live) but is pre-2.0 with breaking releases, needs Python 3.12+,
  and has no OANDA or IG adapter; its strengths (ticks, latency, order books) do not address
  Fiboki's real constraints (sample size and engine speed). Lean has an OANDA plugin but is a C#
  core whose CLI live trading needs a paid tier. Revisit only if live execution is approved.
- **Adopt, in this order:** (1) engine speed: arrays instead of per-bar DataFrame access,
  incremental portfolio volatility, then a Numba kernel, parallel across backtests, each step
  proven byte-identical by the golden tests (estimate one to three weeks); (2) pooled evaluation
  across the instrument universe, the way pysystemtrade pools forecasts across instruments while
  keeping each instrument's costs, with an effective-sample-size correction for correlated trades
  (this is R3's recommended next experiment); (3) single-operator operations: an external heartbeat
  check with push alerts (D3, D4), a rehearsed restore, and a failover rehearsal between the
  MacBook and the desktop.
- Optional cross-checks: skfolio's purged combinatorial CV and the `arch` package's SPA/StepM as
  independent checks on Fiboki's own implementations.

### R2. ~~Run E-1 process 2 on the Mac~~ — DONE 2026-10-01 (14.7 h, four shards; result in the ledger)

`perturbed_price_paths` costs about 500 s of engine time per replicate on a single core (60-odd
`engine_v3_realism` backtests per replicate; the ladder statistics are negligible), so 400
replicates are about 58 core-hours. The study now shards (`--replicate-range`), checkpoints as it
goes and resumes (`--resume`), and merges (`--merge`); `research/reports/e1/run_paths_mac.sh`
launches the shards. Leave the MacBook on mains and awake; a sleep interrupts nothing that
`--resume` cannot continue.

## REQUIRED BEFORE LIVE MONEY

### L1. Accept that no strategy currently qualifies

This is the most important item in the document. **Fiboki V2 has not identified a single strategy that passes its validation gates.** Campaign K1 ran 326 true trials on XAUUSD H4 and produced zero survivors. The holdout has never been consumed by anything.

That is a successful research programme, not a failed one — but it means there is nothing to promote, and no amount of infrastructure changes that.

### L2. Tax advice — DEFERRED by your decision (8 October 2026) until profits exceed £50,000

Recorded as you asked. Two facts so the deferral is an informed one, then nothing more until the
threshold: (1) the broker account type decides the treatment from the first pound, not from
£50,000: spread betting is currently outside Capital Gains Tax for UK retail traders, while CFD
and spot FX gains are chargeable above the annual CGT allowance (£3,000 at the time of writing),
and spread-bet losses cannot be set against other gains; (2) P1's open question (whether OANDA's
API can trade a spread-bet sub-account) is therefore worth answering before live money, whatever
the profit level. I am not a tax adviser; check the current HMRC position when you reach the
threshold.

### L3. Complete the live-execution authorisation chain (details)

Live trading needs **every one** of these at once; each blocks on its own, and the tests set them
one, two and three at a time and assert live is still refused
(`docs/v2/EXECUTION_ARCHITECTURE.md` §6):

| # | Control | Where | Today |
|---|---|---|---|
| 1 | `LIVE_EXECUTION_COMPILED_IN = True` | a source edit to `broker/mode_guard.py`, reviewed and deployed | `False` |
| 2 | `FIBOKI_LIVE_RUNTIME_ARMED` set to the exact long token in `mode_guard.py` (not "true") | environment | unset; the service wrapper unsets it |
| 3 | a persisted live authorisation naming you, with a grant time and an expiry | `<state>/live_authorisation.json` | absent |
| 4 | the strategy named in that authorisation's allow-list | same file | n/a |
| 5 | the venue URL parses to an allow-listed live host | the mode guard | practice host |
| 6 | `OANDA_LIVE_HOST_COMPILED_IN = True` | a second source edit, in `broker/oanda.py` | `False` |
| 7 | `FIBOKI_OANDA_LIVE_RUNTIME` set to its own exact token | environment | unset |
| 8 | the OANDA adapter's base URL is `api-fxtrade.oanda.com` | adapter | practice |
| 9 | `FIBOKI_EXECUTION_MODE=live` | environment, read once at start | forced to `paper` by the wrapper |
| 10 | the strategy's lifecycle state is LIVE (reached only through the promotion chain, D2) | risk gateway | none qualifies (L1) |
| 11 | kill switch inactive | risk gateway | inactive |
| 12 | the other gateway checks pass (limits, exposure, spread, data freshness, event blackout) | risk gateway | fail-closed |

There is no API endpoint that enables live; the API returns 403 and audits the attempt. Today the
service wrapper also refuses: `fiboki worker run live` refuses, and the compose file has no live
service. **Nothing here is to be done until a strategy passes the calibrated gates and a demo
period (L1), and L4 has been run.** When that day comes it is a deliberate, reviewed change, with
two source edits in separate commits.

### L4. Run the kill-switch drill and time it (details)

The kill switch has two actions, both journalled to `<state>/killswitch.jsonl` and read by every
gateway before its next decision (no restart needed):

- **PAUSE** (`fiboki killswitch pause --reason "..." --operator joe`): no new positions or adds;
  closes and reductions still run; open positions keep their stops.
- **FLATTEN** (`fiboki killswitch flatten --reason "..." --operator joe`): no new positions, and
  every open position closed at market, through the normal order path (so recorded, idempotent and
  recoverable).
- `fiboki killswitch status` shows the state. There is deliberately no timeout and no CLI
  deactivate command: disarming is an explicit operator action through the API, recorded in the
  journal (`docs/v2/OPERATIONS.md` §5).

**The drill, once on demo before any live money:** open one or two minimum-size demo positions;
note the time; run PAUSE and confirm a new signal is refused; run FLATTEN and time how long until
the broker shows no open positions; confirm the journal, the alert and the incident entry; write
down both times and anything that surprised you, here, with the date. Until a broker demo exists
the drill can be rehearsed against the paper venue, which proves the journal and the gateway path
but not the broker's response time.

### L5. Close or explicitly accept the documented approximations

Static spreads, zero default slippage, no weekend triple-swap, a static financing rate over a 25-year sample, fixed-date-only holiday calendars, and bars treated as mid when the source is bid. Each is documented in code. Either close them or accept them in writing before capital is at risk.

---

## DESKTOP MIGRATION (MacBook to Mac desktop, local models on llama.cpp)

Added 2026-09-29. Full procedure: `docs/v2/DEPLOYMENT.md` §2 and `docs/v2/OPERATIONS.md` §13.
Everything here keeps the deployment in paper mode; none of it touches a live control.

### M1. On the MacBook, before you leave it

**Where everything lives today (verified 2026-10-08).** Fiboki is not one folder, by design: macOS
will not let launchd services run from inside `~/Documents`, so the services run from a second
checkout outside it.

| What | Where | Size | Carried by |
|---|---|---|---|
| Code (all of it) | GitHub `Stratton1/Fiboki_Trading`, `main` | | `git clone`, or `repo.bundle` in the kit |
| Development checkout (research campaigns, their working files, eval caches, the V1 HistData canonical copy) | `~/Documents/Claude/Projects/Fiboki` | about 12.6 GB (was 16 GB) | kit: `dev/` |
| Runtime checkout (the launchd services; operator state; the OANDA market-data store) | `~/fiboki` | 3.3 GB | kit: `runtime/` (includes the data store) |
| Secrets (OANDA token, session secret, operator hashes) | `~/.fiboki/env` | | **your password manager only** |
| Leases and heartbeats | `~/.fiboki/state.db` | 4 MB | kit (inside both backups) |
| Service definitions | `~/Library/LaunchAgents/uk.fiboki.*.plist` | | regenerated by `scripts/launchd-install.sh` |
| Desktop launcher | `~/Desktop/Fiboki.app` | | reinstalled by `scripts/desktop/install-launcher.sh` |
| Superseded material (V1 data, logs, old bundles, dead scheduled tasks) | `~/Fiboki_Old` (README inside) | 3.4 GB | not needed; copy only if you want it |

**The sweep (done 2026-10-08).** Every old Fiboki file outside the two checkouts and `~/.fiboki`
was found and moved (not deleted) into `~/Fiboki_Old`: 3.2 GB of V1 raw HistData downloads, the
V1 Dukascopy pull, 102 MB of untracked V1 backend data and logs, old git bundles, the 20 September
delivery files, and three dead V1 Cowork scheduled tasks (one of which would have placed an IG
demo order through the now-offline Railway worker). Left in place on purpose: the canonical V1
HistData copy inside the development checkout (two tests and the store-migration script read it),
and tool histories under `~/.cursor` and `~/.claude`.

**The one-folder transfer kit.** `scripts/migration-kit.sh` writes everything the new machine
needs that GitHub does not hold into a single folder, `~/FibokiMigration-<date>/`: a git bundle of
every branch, a verified backup of the runtime state with the market-data store, a backup of the
development checkout's state, the untracked research working files, copies of the launchd plists,
`SHA256SUMS`, `MANIFEST.json` and `RESTORE.md` (step-by-step restore). It never includes
`~/.fiboki/env`. Tested 2026-10-08 against a simulated layout: every archive verifies, the bundle
clones, no secret is copied.

- [ ] Put the contents of `~/.fiboki/env` into your password manager. **Do not** copy
  `~/.zsh_history`, `.envrc` files or notes with keys in them to the new machine. If a secret was
  ever typed on a command line, rotate it on the desktop rather than carrying it over.
- [ ] Commit or push anything you changed yourself (`git status` clean in both checkouts).
- [ ] `scripts/launchd-install.sh --unload --services api,worker,web,news,quotes,paper`, then
  `scripts/migration-kit.sh --dest /Volumes/<external disk>` (about 4 to 6 GB; the MacBook has
  about 19 GB free, so write it straight to an external disk).
- [ ] Reload the services if the MacBook keeps running until the desktop is ready:
  `scripts/launchd-install.sh --services api,worker,web,news,quotes,paper --load`.

**On the desktop, one folder.** The two-checkout split exists only because the development copy
lives in `~/Documents`. On the desktop, clone once to `~/fiboki` and do everything there: the
services, research campaigns and the market-data store all under that one folder, with
`~/.fiboki/env` beside it. That makes `~/fiboki` plus your password manager the whole platform.

### M2. On the desktop

- [ ] `brew install python@3.11 node git sqlite llama.cpp`.
- [x] `git clone` the repository **outside `~/Documents`** (macOS TCC blocks LaunchAgents there; `DEPLOYMENT.md` §2.6). On the MacBook this is `~/fiboki`; on the desktop use the same path so the runbooks apply unchanged.
- [x] `scripts/desktop-install.sh --check`, then `scripts/desktop-install.sh` until it prints no MISSING line.
- [x] `~/.fiboki/env` (mode 600): `FIBOKI_OPERATORS` for Joe and Tom as `scrypt$` hashes (C4); the file is shared by both checkouts. `FIBOKI_OANDA_PRACTICE_TOKEN` / `FIBOKI_OANDA_PRACTICE_ACCOUNT_ID` are present and verified (P1, 2026-10-03). Still to add: `FIBOKI_FINNHUB_API_KEY`, `FIBOKI_FRED_API_KEY` (P6).
- [x] Market-data store in `var/datastore` (60 instruments, 16.5M bars, verified by `fiboki doctor`). On the desktop: copy it again, or `scripts/backup.sh --include-datastore` on this machine first.
- [ ] Download one model (M3) into `~/Models`, verify its SHA-256 against the Hugging Face page, `scripts/llama-server.sh --print`. On the MacBook (8 GB) Ollama `qwen3:4b` is used instead; llama.cpp with a larger model is the desktop plan.
- [x] ~~`_build_provider` change~~ — made (`for_local_server`; detects llama.cpp by `/props`, Ollama otherwise).
- [x] Agent variables set in `~/.fiboki/env`; `FIBOKI_AGENT_CYCLES=true` after the smoke test passed (P7).
- [x] Desktop launcher reinstalled from `~/fiboki` (`scripts/desktop/install-launcher.sh --root ~/fiboki`): silent start, brand icon.
- [x] `.venv/bin/fiboki doctor`: 0 FAIL in `~/fiboki`; `scripts/launchd-install.sh --services api,worker,web,news --load` done; `uk.fiboki.llama` and `uk.fiboki.paper` are not loaded (no llama.cpp model; no OANDA token).
- [ ] System Settings: log in automatically, never sleep, restart after a power failure. LaunchAgents only run while you are logged in. (Your action; I cannot change system settings.)
- [ ] Rehearse a restore once (`OPERATIONS.md` §13.2) before you rely on the backups.
- [x] ~~Web build with `NEXT_PUBLIC_FIBOKI_API=""`~~ — fixed (`apiOrigin()` treats empty as same-origin).

### M3. Which model for which Mac

**What I need from you:** the desktop's unified memory (Apple menu → About This Mac). The row
below follows from it; a 16 GB machine is not in the table because it cannot hold a model large
enough to be useful beside Fiboki, and should keep Ollama `qwen3:4b` as the MacBook does.

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
