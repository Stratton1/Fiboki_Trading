# Data Architecture

**Snapshot:** 2026-09-19T05:05Z (`pytest tests/ -q` → 2683 passed, 2 skipped). Package: `src/fiboki/data/`. Tests:
`tests/unit/test_data_{schema,integrity,versioning,store,resample,providers,recorder,telemetry,calendars}.py`,
`tests/integration/test_data_store.py`, `test_migrate_v1.py`, `test_no_silent_repair.py`.

The governing sentence: **V1 could not answer "which data produced this result", and it fed
bid prices labelled as mid, on a clock five hours out, to every strategy it ran.** This
package exists to make both of those impossible rather than unlikely.

---

## 1. Layers

```
providers/   fetch bytes from a source, DECLARE what they actually are
schema       the one canonical bar shape; price basis is a required COLUMN
integrity    detect defects; never fix them
versioning   content-addressed ids + lineage, persisted in SQLite
store        raw (immutable) / canonical (derived), explicit root
resample     timeframe aggregation with declared session and DST semantics
recorder     executable-price capture, append-only and crash-safe
telemetry    execution telemetry, append-only, backtest-vs-live divergence
calendars    what "the market was open" means, per asset class
migrate_v1   the recorded import of the V1 HistData store
```

Everything downstream — research, backtest, paper — references a
`DatasetVersion.version_id`, so any stored result can re-resolve the exact bytes it was
computed from.

## 2. The canonical bar schema

A stored bar frame has a tz-aware UTC monotonic `DatetimeIndex` and these columns, in this
fixed order (`data/schema.COLUMN_ORDER`), because the content checksum must be a function of
the data and not of dict iteration:

| Group | Columns | Required |
|---|---|---|
| Identity | `instrument`, `timeframe`, `price_basis` | yes |
| OHLC | `open`, `high`, `low`, `close` | yes |
| Volume | `volume`, `tick_volume` | optional |
| Bid | `bid_open`, `bid_high`, `bid_low`, `bid_close` | optional |
| Ask | `ask_open`, `ask_high`, `ask_low`, `ask_close` | optional |

`price_basis` is the field this schema exists for. Its values are `BID`, `ASK`, `MID`, `LAST`
and `SYNTHETIC_MID` — the last meaning `(bid+ask)/2` computed by us rather than quoted by
anyone. `PriceBasis.is_executable_both_sides` is true only for `MID` and `SYNTHETIC_MID`,
which is the property an execution model needs before it can price a buy and a sell off the
same numbers. A provider declares the basis, an importer records it, and a consumer wanting a
different basis must convert explicitly.

`canonical_frame()` is a **shape** operation only: it reorders and types columns, stamps the
identity columns, and enforces the index contract. It does not clean, repair, fill, dedupe or
sort away problems — those are integrity concerns and must be explicit. The one thing it will
do is raise. `source_timezone` in the metadata is documentation; the index must already be
true UTC by the time it arrives, because only the provider knows the source convention.

`BarDatasetMetadata` travels with the parquet as arrow key/value metadata and into the version
catalogue. It records the source timezone verbatim, the ingestion code version, the content
checksum, every `Adjustment` (a declared, deliberate transformation of the source numbers —
timezone correction, price-basis conversion, corporate action), every `RepairRecord`, and every
`GapRecord`. Where a field is unknown, the honest value is recorded as unknown rather than
guessed.

`DatasetKind` places a dataset in the chain: `RAW`, `VALIDATED`, `REPAIRED`, `RESAMPLED`,
`FEATURE`.

## 3. The store: raw immutable, canonical derived

```
<root>/.fiboki-data-root
<root>/catalogue.db
<root>/raw/<INSTRUMENT>/<TF>/<version_id>/year=YYYY/part-0.parquet
<root>/raw/<INSTRUMENT>/<TF>/<version_id>/_dataset.json
<root>/canonical/<INSTRUMENT>/<TF>/<version_id>/...
<root>/features/<INSTRUMENT>/<TF>/<version_id>/...
```

Partitioning by year makes date-range reads cheap: a 2019–2020 request touches two directories
out of twenty-six rather than a whole 25-year file.

Three rules are enforced in `data/store.py` rather than documented, each named after the V1
failure it closes. V1 resolved its data root by starting at the module's own location and
walking *up* the tree until it found something that looked like a data store; on the research
box it found a nearly-empty staging directory two levels above the real one, every read
succeeded, every read returned nothing, and 99% of a research batch recorded `no_data` — which
was treated as a completed outcome and written into the checkpoint as DONE. The batch looked
finished and had tested almost nothing.

1. **The root is explicit.** It comes from an argument or from `FIBOKI_DATA_ROOT`. There is no
   search, no fallback, no walking up. `resolve_root` raises `DataRootNotFound` if neither is
   set.
2. **A root must be marked.** It must contain a `.fiboki-data-root` marker written by
   `DataStore.initialise`. A plausible-looking directory that was never initialised is rejected,
   so pointing at the wrong place fails immediately.
3. **A miss is loud.** Asking for an absent instrument/timeframe raises `DatasetNotFound`. There
   is no empty-DataFrame return path, because "empty" and "absent" are different facts.

`RawImmutabilityError` is raised by any attempt to modify RAW. `ChecksumMismatch` is raised when
stored bytes no longer hash to the registered checksum.

## 4. Versioning and lineage

```
version_id = H( content_checksum || canonical(lineage) )
```

`content_checksum` (`data/schema.content_checksum`) is built from the raw little-endian bytes of
each column in a fixed order with the index as int64 nanoseconds, and deliberately contains no
record of when it was taken. The lineage half is the canonicalised chain of `TransformationStep`
records; each step's `parameters` must be JSON-serialisable and must fully determine the step's
behaviour, because an undeclared parameter is an invisible fork.

Properties that follow: identical content produced by an identical chain gets the same id on any
machine in any process at any time; different content, or the same content arrived at another
way, gets a different id; nothing about creation time enters identity, so the id is reproducible
rather than merely unique.

`DatasetCatalogue` (SQLite) is append-mostly. Registering an id twice with identical facts is a
no-op — idempotent re-ingestion. Registering it with *different* facts raises `VersionConflict`,
because that would mean the content hash lied. `LineageEdgeRow` stores parent→child so a chain
can be walked without parsing JSON, and `diff_versions` compares two versions without the
catalogue.

## 5. The integrity model, and why repair is always explicit

V1 repaired silently. A bar with `high < low` was quietly clipped, a duplicate timestamp quietly
dropped, a hole quietly forward-filled, and the research batch downstream had no idea. The
numbers looked fine.

V2 splits the two operations completely:

```
validate(frame)                  -> IntegrityReport        pure; cannot mutate; returns no frame
repair(frame, report, plan)      -> (new_frame, records)   explicit; named actor; written reason
```

`validate` is a pure function. It does not return a frame, so there is no signature by which it
could quietly hand back a cleaned one.

### The defect taxonomy

`DefectCode` has sixteen members. Severity `ERROR` and above blocks a clean read.

| Code | What it catches |
|---|---|
| `IMPOSSIBLE_BAR` | high/low bracket inconsistent with open/close |
| `NON_POSITIVE_PRICE` | the EURUSD `-0.0001` sentinel and anything like it |
| `NAN_PRICE` | non-finite OHLC |
| `DUPLICATE_TIMESTAMP` | two bars claiming one instant |
| `NON_MONOTONIC_INDEX` | out-of-order rows |
| `NAIVE_TIMESTAMP` | a tz-less index (defensive; `canonical_frame` should have raised first) |
| `UNEXPECTED_GAP` | a hole the session calendar says should not be there |
| `EXPECTED_GAP` | a weekend or a modelled holiday; recorded, not a defect to fix |
| `OFF_SESSION_BAR` | a bar when the venue was closed |
| `STALE_RUN` | `stale_run_length` (default 6) identical consecutive bars |
| `RETURN_OUTLIER` | beyond `outlier_z_threshold` (default 12.0) |
| `VOLUME_ALWAYS_ZERO` | the FX case: the channel carries no information at all |
| `VOLUME_ANOMALY` | beyond `volume_anomaly_z_threshold` (default 15.0) |
| `NEGATIVE_VOLUME` | impossible |
| `BID_ASK_CROSSED` | bid above ask |
| `MISALIGNED_BAR_START` | a bar start off the timeframe grid measured from `alignment_anchor_utc_minutes` |

`IntegrityConfig` holds those thresholds explicitly and is stored with the report, so a verdict is
reproducible rather than dependent on whatever the defaults were that week.

### The repair contract

`RepairAction` enumerates the only six repairs V2 will perform: `DROP_NON_POSITIVE`,
`DROP_IMPOSSIBLE_BARS`, `DROP_NAN_PRICES`, `DROP_DUPLICATE_TIMESTAMPS`, `SORT_INDEX`,
`DROP_OFF_SESSION_BARS`. **There is no interpolation, no forward fill and no clipping.** Every
action removes or reorders; none invents a number. A hole stays a hole.

`RepairPlan` requires both a `reason` and an `actor`. `repair` returns a **new** frame — the input
is never modified — plus a `RepairResult` recording exactly which rows were removed. The caller is
expected to register the result as a new dataset version whose lineage points back at the
unrepaired one, so the original bytes remain resolvable forever and the diff between them is
readable.

`assert_clean` raises `DirtyDataError` when blocking defects are present and no repair was
authorised. `tests/integration/test_no_silent_repair.py` is the end-to-end proof.

Why versioned rather than in-place: a repair is a *judgement*. Dropping the 2001-09-11 sentinel is
almost certainly right; dropping every off-session bar may be wrong for an instrument whose
calendar we modelled badly. Making the repair a new version with a stated reason means a later
reader can disagree with the judgement and re-resolve the original, instead of discovering that a
number changed and being unable to say why.

## 6. Resampling

Two things make resampling wrong in practice and V1 hit both.

**Implicit origin.** Resample without declaring an anchor and pandas anchors to the frame's first
timestamp, so the same instrument resampled from two different date ranges produces two different
bar grids and the "same" backtest on the "same" data disagrees with itself. `data/resample.py`
makes the origin explicit: epoch-anchored UTC by default, so H4 buckets begin at 00:00, 04:00,
08:00, 12:00, 16:00 and 20:00 UTC whatever the frame starts with. That is the grid every Fiboki
research artefact is computed on. The full `ResampleSpec` is hashed into the lineage.

**DST.** A daily bar anchored at 17:00 New York is 23 hours long on one Sunday a year and 25 on
another; resampling in UTC with a fixed 24-hour rule slides the day boundary for half the year.
With `anchor_tz` set, bucketing happens in the venue's local time and the tz database does the
work.

`assert_nested` refuses a resample whose buckets do not nest exactly, which is what makes
M1 → H1 → H4 agree with M1 → H4. `resample_chain` exists so that transitivity can be exercised
directly rather than assumed, and `tests/unit/test_data_resample.py` asserts the two agree
exactly.

Empty buckets are dropped, never forward-filled. A period with no bars is a period with no bars.

## 7. Session calendars

`data/calendars.py` defines sessions in the venue's **local** time and converts through a real tz
database, so DST is handled by construction. FX trades Sunday 17:00 New York to Friday 17:00 New
York, which is 21:00 UTC in summer and 22:00 in winter — exactly the kind of thing V1 got wrong by
hardcoding one of the two. `daily_break` supports a wrapping window, so both the CME metals
maintenance hour `(17, 18)` and a cash session open only 09:00–15:00 local `(15, 9)` are
expressible; without wrapping the latter would be modelled as open all day, which is worse than
being modelled as absent.

`calendar_for` raises `KeyError` for an unregistered symbol. The instrument registry refuses to
guess contract specifications and this refuses to guess trading hours.

**Known approximation, stated:** holiday sets are fixed-date only (1 January, 25 and 26 December).
Moving holidays — Good Friday, Thanksgiving — are not modelled, so a gap on those days is reported
as unexpected. That is the deliberate direction to be wrong in: a false alarm is cheap, a missed
hole is not.

## 8. Providers

`data/providers/base.py` requires four declarations before a provider may hand over a single bar:
its `native_price_basis` (not optional, not inferred), its `native_timezone` verbatim including
the awkward ones, whether bid and ask are separately available, and whether an incomplete
(still-forming) bar can appear in its output and how it is identified — because Fiboki evaluates
signals on closed candles only, and a provider that can leak a forming candle must filter at
source.

`AuthenticationRequired` exists so that a missing credential is never a silent skip.

### HistData — `data/providers/histdata.py`

Two defects, both of which V1 inherited whole.

**Bid, not mid.** The free ASCII M1 series is built from bid quotes. V1 loaded them as OHLC, every
engine treated them as mid, and the backtester then *additionally* subtracted a modelled
half-spread — so a long entry paid a spread it had already implicitly paid and a short was
credited one it never received. The fix is not a constant, because the bias is direction-dependent;
the fix is declaring `price_basis = BID` in the data, so a consumer needing mid must call
`bid_to_mid`, which requires both the assumed spread and the pip size to be passed explicitly and
stamps the result `SYNTHETIC_MID` — never `MID`, because no mid was ever quoted.

**EST with no daylight saving.** HistData stamps every bar in US Eastern *Standard* Time year
round: a fixed UTC−05:00, never UTC−04:00. It is not `America/New_York`, it is New York's winter
offset applied in July. V1 attached `tz="UTC"` to those naive timestamps, so every bar was labelled
five hours early, every session filter fired at the wrong hour year-round, and the error was
constant so nothing ever looked broken. `convert_histdata_index` applies `true_utc = naive_est +
5h` — the `already_mislabelled_utc=True` path handles the V1 store, where the stamps were given a
UTC label without being shifted, and both paths end at the same answer.

`detect_timestamp_convention` is the empirical check that would have caught this on day one: a
genuinely-UTC FX series shows its weekly open drifting between 21:00 and 22:00 UTC with US DST,
while these files show a rock-solid 17:00 boundary all year, which is only possible on a fixed
offset.

`zero_volume_fraction` reports the third defect: FX volume is identically zero, so the channel is
blind. `HistDataParquetProvider` reads the V1 store at
`<root>/<SYMBOL>/<symbol>_<tf>.parquet` and corrects and declares both problems.

### Dukascopy — `data/providers/dukascopy.py`

The upgrade path: free tick data with **both sides of the book**, timestamped in genuine UTC,
fixing both HistData defects at once. Implemented here: URL construction, record decoding and
tick-to-bar aggregation, all pure functions, all tested against fixtures the module can generate.
**Not implemented: the HTTP fetch**, which is a stub that raises rather than a stub that returns
nothing.

The wire format is
`https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5` where `MM0`
is a **zero-based month**. Getting that wrong silently returns the previous month's data, so
`tick_url` is the only sanctioned way to build the path and it is unit-tested. The payload is
LZMA-compressed fixed-width big-endian `>IIff` records; prices are integers in points, divided by
the instrument's point factor (10⁵ for most FX, 10³ for JPY pairs and metals).

`ticks_to_bars` defaults to `SYNTHETIC_MID` because a mid built from bid and ask was computed by
us, and keeps the `bid_*`/`ask_*` columns so an execution model can price a buy and a sell
differently instead of assuming a symmetric spread.

**The defect of this source, stated:** Dukascopy is a different venue. Its quotes are not the
quotes Fiboki would deal on at OANDA or IG, its spread distribution reflects its own liquidity
provision, and its historical record has been revised. It is the right tool for *cross-checking*
another source's bars and for reconstructing realistic spread behaviour, and it is the wrong tool
for claiming that a modelled fill is achievable.

### OANDA v20 — `data/providers/oanda.py`

Implemented against the documented response shape for
`GET /v3/instruments/{instrument}/candles`. No credentials exist in this environment, so the
network path raises `AuthenticationRequired` and the **parsing** path — where all the correctness
lives — is fully implemented and tested against constructed fixtures.

Three things it refuses to be casual about. **Incomplete candles**: `complete: false` marks the
candle currently forming, and Fiboki evaluates signals on closed candles only, so it is dropped by
default and its presence is *reported*, never silently aggregated into a bar that will change next
minute. **Prices are strings**: v20 sends decimal strings to avoid float ambiguity, and they are
parsed once, explicitly. **`volume` is a tick count, not traded size**: OANDA is a broker, not an
exchange, so it is recorded as `tick_volume` and the real `volume` column is left absent — no
strategy can mistake broker tick counts for market volume.

**The defect of this source, stated:** OANDA's own UK documentation notes that the live pricing
feed may differ from historical data because of pricing segments and account tiers, so candles
come from OANDA's engine but not necessarily from the tier the account will deal on. The
mitigation is §9.

## 9. The live recorder

`data/recorder.py` exists *before* a broker does, and that is the point.

A backtest fills at a bar price plus a static spread assumption. A live account fills at whatever
was quotable, after latency, with a spread that widens at exactly the moments a strategy most wants
to trade. The gap between those two numbers is the largest unknown in the platform, and it cannot
be measured retrospectively because the quotes are gone. The recorder's value is proportional to
elapsed time, which makes it the one component worth starting before everything else is ready.

It captures `QuoteRecord`s — bid, ask, derived mid and spread, with `price_basis` flowing into any
bar built from them — from whatever feed is available. Today that is `SimulatedQuoteFeed`, a
deterministic seeded random walk with a spread that widens outside the session and around the top
of the hour, and occasional latency spikes. It is **not** a market model and is not used for
research; its job is to exercise the recorder, the rotation logic and the replay reader on
realistically-shaped input.

### Crash safety

Recording runs unattended for months and the process *will* be killed mid-write. The format makes a
torn write detectable rather than corrupting:

- one record per line, `<crc32 hex> <payload length> <json>`;
- the reader verifies the CRC and the declared length of every line;
- a truncated or corrupt **final** line is reported and skipped, and every record before it is
  still readable;
- segments rotate on a size/record budget, and each closed segment gets a manifest written via
  `tmp` + `os.replace`, which is atomic on POSIX;
- `fsync_every` makes the durability/throughput trade-off explicit rather than leaving it to the
  page cache.

A corrupt line in the *middle* of a segment is a different animal from a torn tail — it means real
corruption, not a crash — so it raises `LogCorruption` unless the caller explicitly opts into lossy
reading.

`spread_profile` turns recorded quotes into observed spread by hour or weekday. **That is the
number a backtest should eventually use instead of a single static
`Instrument.typical_spread_pips`.** Until enough has been recorded, the static assumption stands
and is documented as an approximation — here, and on every result that used it.

`quotes_to_bars` takes an explicit `side`, which flows into `price_basis`: bars built from the bid
are BID bars, and V2 will not let them be mistaken for mid.

## 10. Execution telemetry

`data/telemetry.py` uses the same CRC-framed append-only segment format, for the same reason.

Per execution attempt it records five timestamps — `signal_ts` (when the closed bar produced the
signal), `decision_ts` (when the strategy and risk stack finished deciding), `submit_ts` (when the
order left us), `ack_ts` (broker acknowledgement) and `fill_ts` — together with requested versus
filled price (slippage), requested versus filled size (partial fills), rejected size and the broker
error (capacity and rejection reality), the spread at decision time (whether the cost model was
close), and the market regime (where divergence concentrates).

`slippage_summary` produces the backtest-realism scorecard. `divergence_report` compares assumed
cost against realised cost per instrument: a positive `excess_cost_pips` means live trading is more
expensive than the backtest assumed, and every stored expectancy for that instrument is overstated
by that much per trade. The module's instruction is that this should be said out loud rather than
absorbed.

## 11. The V1 migration

`data/migrate_v1.py` performs a recorded import, per `(instrument, timeframe)`:

1. read the V1 parquet through `HistDataParquetProvider`, which corrects the EST-no-DST stamps to
   true UTC and stamps `price_basis = BID`;
2. write that as an **immutable RAW** dataset, with the timezone correction and the basis
   declaration recorded as declared `Adjustment`s;
3. run integrity validation against a real session calendar — **nothing is repaired**; the report
   is stored next to the data and attached to the version;
4. register a `VALIDATED` canonical dataset derived from RAW by a recorded transformation, carrying
   the integrity verdict as its quality.

What it deliberately does not do is fix anything. The V1 store contains genuinely broken bars, and
the point of the exercise is that they arrive in V2 *labelled as broken* rather than quietly
cleaned up. Repair, if wanted, is a separate explicit step producing yet another version.

At this snapshot, `data/` in the repository is empty and `.gitignore` excludes `data/raw/`,
`data/canonical/`, `data/live/` and `data/features/`. **The migration has not been run against the
real 7.2 GB V1 store in this environment.** `tests/integration/test_migrate_v1.py` exercises it
against a constructed fixture that reproduces the known defects, including the 2001-09-11
`-0.0001` bar.

## 12. Known defects, by source

| Source | Price basis | Clock | Volume | Other |
|---|---|---|---|---|
| **HistData** (V1 canonical store) | **BID**, treated as mid by V1 | **EST with no DST**, labelled UTC; five hours out | identically zero | one negative-price sentinel bar (EURUSD H1, OHLC all `-0.0001`, 2001-09-11 20:00 EST); no bid/ask, so spread features are unavailable |
| **Dukascopy** | bid and ask; mid is synthetic | genuine UTC | tick counts per side | **a different venue's prices**; its spreads are its own liquidity, not the dealing venue's; history has been revised; network fetch not implemented |
| **OANDA v20** | bid, ask and mid all available | genuine UTC | **tick count, not traded size** | live feed may differ from historical candles by pricing segment and account tier; forming candles must be filtered; no credentials in this environment, so only the parser is exercised |
| **Recorder (simulated feed)** | bid and ask | UTC | none | **not a market model.** Deterministic seeded walk; exists to exercise the recorder, never to be researched against |

Two further approximations apply to all sources at this snapshot: research uses a **static** spread
per instrument (`Instrument.typical_spread_pips`), and financing rates in `sim/profiles.py` are
**static over the whole sample** in a period where real rates moved from roughly 0% to roughly
5.5%. Both are stated on the profiles that carry them and both are closed by data the recorder is
accumulating.

## 13. Scheduled economic events

`marketstate/fixtures/scheduled_events_official.json` is the only dated event data in the tree.
It was fetched once, on 2026-09-28, from the publishers' own pages and committed;
`marketstate/calendar.py` still performs no network access, so a backtest re-run later reads the
same bytes. `load_official_calendar()` returns it as an `InMemoryEconomicCalendar`;
`fiboki calendar status` prints its coverage and `fiboki calendar check <ISO-UTC> <CCY>` answers a
single blackout question.

| Source | Events | Local clock | Horizon |
|---|---|---|---|
| Federal Reserve, FOMC decisions | 32 | 14:00 New York | 2027 (Fed: dates tentative until the preceding meeting) |
| ECB Governing Council decisions | 40 | 14:15 Frankfurt | 2028 |
| Bank of England MPC | 32 | 12:00 London | 2027 |
| Bank of Japan MPM | 32 | **no fixed time**: 09:00-14:00 Tokyo window | 2027 |
| BLS Employment Situation (NFP) | 35 | 08:30 New York | Dec 2026 (no 2027 schedule published) |
| BLS CPI | 35 | 08:30 New York | Dec 2026 |
| ONS CPI / monthly GDP / labour market | 37 / 48 / 48 | 07:00 London | confirmed dates only (to Jan 2027 / Dec 2027 / Dec 2027) |

Rules the file keeps: every `event_time` is UTC and re-derives from the stored `local_date`,
`local_time` and `local_tz` (a test checks all of them, which is the DST bug class); scheduled
times only, no actual/forecast/previous; the October 2025 NFP and CPI that were never published
(US appropriations lapse) are absent, not invented; ONS provisional dates are dropped. Events with
no fixed time carry `time_known: false` and a `window_end`, and blackout queries treat the whole
span as the event.

Coverage is **declared**: `2024-01-01` to the earliest last-published event across sources, so a
span only counts as covered where every source is complete. Outside it, and for any currency
other than USD, EUR, GBP and JPY, the calendar still answers "not in blackout".
`EconomicCalendar.assert_populated(start, end, currencies)` is the guard; `run_validation` applies
it to a supplied calendar and `scripts/run_paper_session.py` applies it by default
(`--allow-empty-calendar` opts out and is recorded). Two gaps remain and are stated rather than
hidden: the paper runtime does not yet feed the calendar to the risk gateway's `event_blackout`
check, and no validation caller passes a calendar yet.

## 14. Point-in-time text and macro data

Added 2026-09-28 (Wave 3, D-A5). Packages: `src/fiboki/data/news/` and six macro providers
under `src/fiboki/data/providers/`, sharing `providers/macro_base.py`. Tests:
`tests/unit/test_news_recorder.py`, `tests/unit/test_macro_providers.py`. No LLM anywhere in
either.

The governing sentence: **a number or a headline is usable in a backtest only from the instant
it could have been known, and that instant is stored beside it, never inferred from its
reference date.** Both halves of this section store two times and keep them apart.

### 14.1 The headline recorder

```
<state_dir>/news/headlines.sqlite      WAL, synchronous=FULL, append-only by trigger
  headline            id, source (enum), source_item_id, url, url_hash, title, summary,
                      vendor_published_at (nullable), observed_at (UTC, ours), raw_json,
                      content_hash, feed_key;  UNIQUE (source, url_hash)
  headline_revision   later content seen for an existing (source, url): appended, never merged
  feed_source         every configured feed URL, where it was found, when it was retrieved
  poll_log            one row per poll with per-feed outcomes (the evidence for gaps)
```

- **Availability is `observed_at`, set by our clock at the poll.** `vendor_published_at` is
  informational. `HeadlineStore.query(as_of, since, sources, currencies_hint)` filters on
  `observed_at` alone and has no "latest" mode.
- **Append-only in the database, not in the code.** Triggers refuse `UPDATE` and `DELETE` on
  every table, and a `BEFORE INSERT` trigger refuses a colliding insert, because SQLite's
  `INSERT OR REPLACE` deletes without firing delete triggers.
- **Dedupe** is `(source, url_hash)`; the URL hash lower-cases scheme and host and drops the
  fragment, nothing else (a false merge loses a headline). The same URL from two sources is two
  rows. A retitled item goes to `headline_revision`; `query` serves the first-seen row.
- **Restart safety**: one transaction per poll; a crash loses that poll's inserts, and the next
  poll re-inserts them with a later `observed_at`. Wrong only in the safe direction.
- **Bootstrap burst, stated**: the first poll stamps every item currently in each feed with the
  first poll's instant. Items published before the recorder started carry `observed_at` far
  after `vendor_published_at`; they are genuinely unseen until then and must not be read as news
  at that instant by an event study. The archive is valid from its first poll forward.
- **CLI**: `fiboki news record --once|--loop --interval 300`, `fiboki news status` (exit 1 when
  no poll has run within `--gap-threshold`, default 900 s). Supervision under the worker
  supervisor is not wired (`workers/` belongs to another item).

Official feeds (discovered on each bank's own RSS index page, fetched and parsed
2026-09-28T22:40Z; none guessed):

| Source | Feed key | URL | Format |
|---|---|---|---|
| Fed | `fed_press_monetary` | https://www.federalreserve.gov/feeds/press_monetary.xml | RSS 2.0 |
| Fed | `fed_press_all` | https://www.federalreserve.gov/feeds/press_all.xml | RSS 2.0 |
| Fed | `fed_speeches` | https://www.federalreserve.gov/feeds/speeches_and_testimony.xml | RSS 2.0 |
| ECB | `ecb_press` | https://www.ecb.europa.eu/rss/press.html | RSS 2.0 |
| ECB | `ecb_blog` | https://www.ecb.europa.eu/rss/blog.html | RSS 2.0 |
| BoE | `boe_news` | https://www.bankofengland.co.uk/rss/news | RSS 2.0 |
| BoE | `boe_speeches` | https://www.bankofengland.co.uk/rss/speeches | RSS 2.0 |
| BoJ | `boj_whatsnew` | https://www.boj.or.jp/en/rss/whatsnew.xml | RSS 2.0 |
| SNB | `snb_pressrel` | https://www.snb.ch/public/rss/en/pressrel | RSS 2.0 |
| SNB | `snb_mopo` | https://www.snb.ch/public/rss/en/mopo | RSS 2.0 |
| RBA | `rba_media` | https://www.rba.gov.au/rss/rss-cb-media-releases.xml | RSS 1.0 / RDF (RSS-CB) |
| RBA | `rba_speeches` | https://www.rba.gov.au/rss/rss-cb-speeches.xml | RSS 1.0 / RDF (RSS-CB) |
| BIS (added 2026-09-29, §14.5) | `bis_cbspeeches` | https://www.bis.org/doclist/cbspeeches.rss | RSS 1.0 / RDF (RSS-CB) |

The BoJ's `https://www.boj.or.jp/en/rss/index.htm` returns 404 and the SNB's
`.../digital-services/rss` page moved to `.../rss-calendar-feeds`; the URLs above come from the
pages that do exist. XML with an `<!ENTITY` declaration is refused before parsing.

Vendors are optional and off without a key: Finnhub `GET /api/v1/news?category=forex|general`
(`FIBOKI_FINNHUB_API_KEY`, sent as `X-Finnhub-Token`, never in the URL) and Marketaux
`GET /v1/news/all` (`FIBOKI_MARKETAUX_API_KEY`; the API requires it as a query parameter, so
every error message is scrubbed of it). Marketaux polls at most every 900 s by default to stay
inside a daily request quota. Neither has been exercised against the live API: no key exists
here, and their fixtures are constructed from the documented response shapes.

### 14.2 Point-in-time macro providers

Every macro observation is a row of a long-format frame (`macro_base.MACRO_COLUMNS`):
`series_id, period, period_start, value, available_at, superseded_at, availability_basis,
vintage, attributes`. `macro_base.as_of(frame, D)` returns, per `(series, period)`, the latest
row with `available_at <= D` that was not superseded by `D`. That is the only sanctioned read.

`AvailabilityBasis`: `VINTAGE` (source vintages), `SOURCE_TIMESTAMP` (the source stamps its own
publication instant), `RELEASE_OVERRIDE` (a published exception), `RELEASE_RULE` (a documented
schedule, stamped conservatively), `FIRST_SEEN` (our fetch time; valid forward only) and
`UNRESOLVED` (no `available_at`; never served). **Every approximation stamps later, never
earlier**: holiday sets are supersets of the true closures, because an extra holiday can only
delay a stamp.

| Provider | Endpoint | `available_at` | Basis |
|---|---|---|---|
| `alfred` | `api.stlouisfed.org/fred/series/observations` with `realtime_start=1776-07-04`, `realtime_end=9999-12-31` | 00:00 America/Chicago on `realtime_start + 1`; superseded 00:00 Chicago on `realtime_end + 2` | VINTAGE |
| `cftc_cot` | `publicreporting.cftc.gov/resource/gpe5-46if.json` (TFF; legacy and disaggregated also mapped) | Friday after the as-of date, 15:30 ET; next business day if a federal holiday falls between; CFTC backlog table 2025 and first 2018-19 report as overrides; 2013 and 2018-19 lapse windows UNRESOLVED | RELEASE_RULE / OVERRIDE / UNRESOLVED |
| `ecb_sdmx` | `data-api.ecb.europa.eu/service/data/{flow}/{key}?format=csvdata` | `EXR` daily: 17:00 Europe/Berlin on the date (ECB: "around 16:00 CET"); anything else FIRST_SEEN | RELEASE_RULE / FIRST_SEEN |
| `boe_iadb` | `bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp` (CSV) | `IUDSOIA`: 12:00 London next London business day (09:00 publication, republication by midday); `IUDBEDR`: 12:00 London on the date; others FIRST_SEEN | RELEASE_RULE / FIRST_SEEN |
| `ons` | `www.ons.gov.uk/<topic>/timeseries/<cdid>/<dataset>/data` and `.../previous/vN/data` | observation `updateDate` date resolved to a trustworthy archived-version instant, else 09:30 London; archived versions give vintages; `nextRelease` recorded | SOURCE_TIMESTAMP / RELEASE_RULE |
| `nyfed` | `markets.newyorkfed.org/api/rates/{secured,unsecured}/{type}/search.json`, `/api/rp/results/search.json` | rates: 15:00 ET next SIFMA-superset business day (after the 14:30 revision window); repo: `lastUpdated` | RELEASE_RULE / SOURCE_TIMESTAMP |

Rules verified against the source on 2026-09-28: the CFTC rule reproduces all six 2026
exceptions on the published schedule and keeps Monday-holiday weeks on Friday (a test pins
twelve dates); the ONS versions table was measured and **archive stamps are not always
publication instants** (2018-2021 stamps such as 05:07Z precede a 07:00 BST release), so only
on-the-hour or half-hour stamps are trusted as instants, and irregular ones can only move the
09:30 rule later.

Storage: `MacroDatasetStore(<data root>)` writes `macro/<provider>/<dataset>/<version_id>/`
(`data.parquet`, `_dataset.json` with descriptor, lineage and fetch report). `version_id` is
`versioning.compute_version_id(content_checksum, lineage)`; fetch time is not identity, so two
fetches of rule-stamped data with identical content share an id, while FIRST_SEEN data differs
per fetch because its `available_at` does. Macro datasets are not registered in
`DatasetCatalogue`, whose schema requires a `Timeframe` and a `PriceBasis` that a macro series
does not have; registering one there would mean inventing both. The root must be a marked data
root. CLI: `fiboki macro describe [name]`, `fiboki macro fetch <provider> --series ... [--as-of]`.

Licences and attribution (each in its provider's `describe()`):

| Provider | Licence | Required notice |
|---|---|---|
| ALFRED | FRED API Terms of Use, https://fred.stlouisfed.org/docs/api/terms_of_use.html; third-party "Copyright" series need the owner's permission | "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." |
| CFTC | public domain, acknowledgement requested, https://www.cftc.gov/WebPolicy/index.htm | "Source: U.S. Commodity Futures Trading Commission, Commitments of Traders." |
| ECB | ESCB free reuse with citation, no modification, https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.en.html | "Source: ECB statistics." |
| BoE | OGL v3.0 except third-party exchange-rate series, https://www.bankofengland.co.uk/legal | OGL notice; SONIA copyright wording |
| ONS | OGL v3.0, https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/ | "Source: Office for National Statistics licensed under the Open Government Licence v3.0." |
| NY Fed | Terms of Use, https://www.newyorkfed.org/privacy/termsofuse | the prescribed notice plus the non-affiliation disclaimer |

### 14.3 Known limitations, stated

- **ALFRED is untested against the live API**: no key exists here; its fixture is constructed.
  Real-time periods are dates, so a vintage is up to a day late.
- **COT**: ad-hoc federal closures and any future appropriations lapse are not in the rule and
  would make a stamp early until added as overrides or unresolved windows. Corrections overwrite
  Socrata rows. Override release times are assumed 15:30 ET (the CFTC published dates only).
- **ECB, BoE and COT have no vintages**: a revision overwrites the value at source; only two
  stored versions of our own record it. Non-EXR ECB series and unregistered IADB series are
  FIRST_SEEN and have no usable history before our first fetch.
- **ONS** uses the website's JSON view, which is not a versioned API; the retired
  `api.ons.gov.uk` and the no-longer-updated CMD dataset API are not used. A non-standard ONS
  release later than 09:30 on the day would be stamped early.
- **NY Fed** first prints before the 14:30 ET revision window are not recoverable.
- **Headlines** are recorded, not classified; there is no currency tagging of vendor items
  (`currencies_hint` narrows only the central-bank sources). The `query_news` tool and
  `READ_NEWS_SNAPSHOT` capability (Wave 3, row 3) are not built by this item.

### 14.4 The source registry: every source, its cost and its terms

Added 2026-09-29. `src/fiboki/data/sources/registry.py` lists every external source Fiboki
knows about, implemented or not, with `terms_url`, a `terms_summary` quoted from that page (read
2026-09-29 unless stated), a `terms_status` and its point-in-time semantics.
`describe_sources(kind=, implemented_only=)` is the listing for the API and the docs (no API route
is wired yet: `api/` was outside this item). `tests/unit/test_source_registry.py` fails when a
module under `data/providers/` or `data/positioning/` has no entry, when a feed key, macro
provider, pack or secondary calendar is unregistered, when a forbidden source acquires a module,
and when the table below differs from `registry_markdown()`.

`terms_status`: **permitted** (automated retrieval and storage for research allowed, usually with
attribution); **personal_only** (the operator's own non-commercial research; no redistribution and
no commercial product without a licence); **opt_in_unclear** (no licence found for automated use,
or the terms arguably prohibit it: behind an explicit opt-in, off by default, never in CI);
**forbidden** (prohibited, or a paid licence we do not hold: never implemented).

| Source | Kind | Cost | Terms status | Key env | Cadence | Point in time | Module |
|---|---|---|---|---|---|---|---|
| `fed_rss` | news | free | [permitted](https://www.federalreserve.gov/disclaimer.htm) | none | 300 s poll | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `ecb_rss` | news | free | [permitted](https://www.ecb.europa.eu/services/disclaimer/html/index.en.html) | none | 300 s poll | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `boe_rss` | news | free | [permitted](https://www.bankofengland.co.uk/legal) | none | 300 s poll | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `boj_rss` | news | free | [personal_only](https://www.boj.or.jp/en/about/copyright.htm) | none | 300 s poll | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `snb_rss` | news | free | [personal_only](https://www.snb.ch/en/srv/disclaimer_copyright) | none | 300 s poll | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `rba_rss` | news | free | [permitted](https://www.rba.gov.au/copyright/) | none | 300 s poll | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `bis_cbspeeches` | news | free | [personal_only](https://www.bis.org/terms_conditions.htm) | none | 300 s poll | observed_at (our clock at first poll); vendor pubDate informational only; BIS republishes speeches days after delivery, stamped midnight | `fiboki.data.news.sources` |
| `finnhub_news` | news | free tier (60 calls/min, enforced client-side) | [personal_only](https://finnhub.io/terms-of-service) | `FIBOKI_FINNHUB_API_KEY` | 60 s per category | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `marketaux_news` | news | free tier (100 requests/day, 3 articles/request) | [personal_only](https://www.marketaux.com/tos) | `FIBOKI_MARKETAUX_API_KEY` | 900 s | observed_at (our clock at first poll); vendor pubDate informational only | `fiboki.data.news.sources` |
| `gdelt_doc` | news | free | [permitted](https://www.gdeltproject.org/about.html) | `FIBOKI_GDELT_ENABLED` | 900 s per query, 1 request per 5 s | observed_at (our clock at first poll); vendor pubDate informational only; seendate kept as vendor time | `fiboki.data.news.sources` |
| `official_calendar` | calendar | free | [permitted](https://www.federalreserve.gov/disclaimer.htm) | none | committed fixture, refreshed by hand | scheduled times only; no values | `fiboki.marketstate.calendar` |
| `finnhub_calendar` | calendar | PREMIUM: Finnhub marks /calendar/economic 'Premium Access Required' | [personal_only](https://finnhub.io/terms-of-service) | `FIBOKI_FINNHUB_API_KEY` | on demand (weekly is enough) | dated snapshot files; scheduled times only; 'time' zone inferred UTC from the schema sample | `fiboki.data.providers.finnhub` |
| `forexfactory_feed` | calendar | free | [opt_in_unclear](https://www.forexfactory.com/notices) | `FIBOKI_FF_CALENDAR_OPT_IN` | at most hourly; the feed is the current week | dated snapshot files; scheduled times only | `fiboki.data.providers.forexfactory_feed` |
| `cftc_cot` | positioning | free | [permitted](https://www.cftc.gov/WebPolicy/index.htm) | none | weekly (Friday 15:30 ET release) | available_at by the CFTC release rule, overrides and unresolved windows | `fiboki.data.providers.cftc_cot` |
| `oanda_books` | positioning | free with an OANDA account | [personal_only](https://www.oanda.com/site/terms-of-use) | `FIBOKI_OANDA_BOOKS_TOKEN`, `FIBOKI_OANDA_BOOKS_ENVIRONMENT` | hourly | observed_at; book 'time' kept as vendor time | `fiboki.data.positioning.oanda_books` |
| `myfxbook_outlook` | positioning | free (100 requests/24 h; paid 2,880) | [personal_only](https://www.myfxbook.com/api) | `FIBOKI_MYFXBOOK_EMAIL`, `FIBOKI_MYFXBOOK_PASSWORD` | hourly (24 of 100 daily requests) | observed_at only (no vendor time) | `fiboki.data.positioning.myfxbook` |
| `alfred` | macro | free (API key) | [permitted](https://fred.stlouisfed.org/docs/api/terms_of_use.html) | `FIBOKI_FRED_API_KEY` | on demand | VINTAGE | `fiboki.data.providers.alfred` |
| `fred_cross_asset_daily` | macro | free (API key) | [personal_only](https://fred.stlouisfed.org/docs/api/terms_of_use.html) | `FIBOKI_FRED_API_KEY` | daily | VINTAGE | `fiboki.data.providers.fred_pack` |
| `ecb_sdmx` | macro | free | [permitted](https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.en.html) | none | daily | RELEASE_RULE (EXR) / FIRST_SEEN | `fiboki.data.providers.ecb_sdmx` |
| `boe_iadb` | macro | free | [permitted](https://www.bankofengland.co.uk/legal) | none | daily | RELEASE_RULE / FIRST_SEEN | `fiboki.data.providers.boe_iadb` |
| `ons` | macro | free | [permitted](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | none | per release | SOURCE_TIMESTAMP / RELEASE_RULE | `fiboki.data.providers.ons` |
| `nyfed` | macro | free | [permitted](https://www.newyorkfed.org/privacy/termsofuse) | none | daily | RELEASE_RULE / SOURCE_TIMESTAMP | `fiboki.data.providers.nyfed` |
| `histdata` | prices | free | [opt_in_unclear](https://www.histdata.com/f-a-q/) | none | bulk download | historical bars; no availability semantics needed | `fiboki.data.providers.histdata` |
| `dukascopy` | prices | free | [opt_in_unclear](https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/) | none | not fetched | historical ticks | `fiboki.data.providers.dukascopy` |
| `oanda_candles` | prices | free with an OANDA account | [personal_only](https://www.oanda.com/site/terms-of-use) | none | live recorder / on demand | closed candles only | `fiboki.data.providers.oanda` |
| `investing_com` | calendar | n/a | [forbidden](https://www.investing.com/about-us/terms-and-conditions) | none | n/a | n/a | not implemented |
| `tradingview_undocumented` | prices | n/a | [forbidden](https://www.tradingview.com/policies/) | none | n/a | n/a | not implemented |
| `myfxbook_scraping` | positioning | n/a | [forbidden](https://www.myfxbook.com/terms) | none | n/a | n/a | not implemented |
| `forexfactory_scraping` | calendar | n/a | [forbidden](https://www.forexfactory.com/notices) | none | n/a | n/a | not implemented |
| `reuters` | news | paid licence (LSEG / Reuters Connect) | [forbidden](https://developers.lseg.com/en/product/news/overview) | none | n/a | n/a | not implemented |
| `associated_press` | news | paid licence (AP Media API) | [forbidden](https://developer.ap.org/) | none | n/a | n/a | not implemented |

Not implemented because the terms forbid it, with the page that says so:

- **Investing.com** (https://www.investing.com/about-us/terms-and-conditions): "It is prohibited
  to use, store, reproduce, display, modify, transmit or distribute the data contained in this
  website without the explicit prior written permission of Fusion Media and/or the data provider."
- **TradingView undocumented endpoints** (https://www.tradingview.com/policies/): the terms
  prohibit non-display and automated use and "third-party products, tools, or services designed
  to facilitate or enable such non-display usage", and say "We do not permit commercial usage of
  any of our services or APIs." The Lightweight Charts library is a separate Apache-2.0 product.
- **Myfxbook page scraping** (https://www.myfxbook.com/terms): "Reproduction is prohibited by
  law." The official API is used instead (`myfxbook_outlook`).
- **ForexFactory calendar pages** (https://www.forexfactory.com/notices): copying "calendar
  schedules and specs" is prohibited without written consent.
- **Reuters and AP are not free.** Reuters news is an LSEG-licensed product ("News Feeds are
  licensed primarily for programmatic internal end uses", redistribution needs separate
  licences, https://developers.lseg.com/en/product/news/overview; licensing:
  https://www.reutersagency.com/en/licensing/). AP's Media API serves a customer's "licensed
  content" (https://developer.ap.org/). Neither is implemented without a paid licence.

Two existing sources are now marked **opt_in_unclear** on reading their terms, and that is a
finding, not a formality: **HistData** states no licence at all (its FAQ offers the data "at your
own will and risk"), and **Dukascopy**'s site terms forbid "any 'scraper,' 'robot,' 'bot,'
'spider' ... to access, acquire, copy, or monitor any portion of the WEBSITE" and using it "to
construct a database of any kind". Whether `datafeed.dukascopy.com` is "the WEBSITE" is unclear;
its HTTP fetch is still a stub and should stay one until Dukascopy answers in writing.

### 14.5 Sources added on 2026-09-29

**Headlines** (all through the existing recorder, `observed_at` as availability):

- **BIS central bankers' speeches**, `bis_cbspeeches`, https://www.bis.org/doclist/cbspeeches.rss,
  RSS 1.0 with the RSS-CB speech extension, found on https://www.bis.org/rss/index.htm. It is a
  republication channel: speeches appear days after delivery, stamped midnight, so it is text and
  coverage (it carries banks with no feed of their own), never an event clock. Stored as
  `NewsSource.OTHER`: the source enum is a `CHECK` constraint in every existing
  `headlines.sqlite`, so a new enum value needs a store migration (not in this item's files).
- **Central-bank feeds re-verified** on their index pages today: the Fed's
  `speeches_and_testimony.xml` already covers `speeches.xml` and `testimony.xml`
  (https://www.federalreserve.gov/feeds/feeds.htm); the ECB has no separate speeches feed, its
  `press.html` carries speeches (https://www.ecb.europa.eu/home/html/rss.en.html); BoE speeches
  are `boe_speeches` (https://www.bankofengland.co.uk/rss); the BoJ English site links only
  `whatsnew.xml` (https://www.boj.or.jp/en/index.htm; `/en/rss/index.htm` and
  `/en/about/services/rss.htm` are 404). No bank URL was added except BIS, and none was guessed.
- **GDELT DOC 2.0**, opt-in with `FIBOKI_GDELT_ENABLED=true`: six curated queries
  (`GDELT_QUERIES`: central banks, FX majors, US dollar, gold, equity indices, macro releases),
  `mode=ArtList&format=json&maxrecords=250&timespan=1h&sort=DateDesc`, every 900 s. Limits,
  stated: the API searches a **rolling 3 months**; **250 results** per request at most (a full
  response is logged `truncated: true` in the poll log, because older matches in the hour were not
  returned); **one request every 5 seconds** (GDELT's own 429 text), enforced by one limiter shared
  across queries, so a full poll takes about 30 s. Dedupe is by URL within a response and by
  `(source, url_hash)` in the store, so an article found by two queries is one row. `seendate` is
  kept as the informational vendor time. Terms: free for any use with a citation (the constant
  `GDELT_ATTRIBUTION`). From this build environment GDELT answered 429 to every request (a shared
  egress address), so the fixture is constructed.
- **Finnhub** news: the free tier's **60 calls per minute** is now enforced client-side by one
  `SlidingWindowLimiter` shared by every Finnhub client from the same key. Finnhub's pricing and
  terms pages could not be read by tooling (JavaScript-rendered; robots.txt), so the 60/min figure
  is from the brief and third-party summaries; the API's own swagger states 429 on excess and a
  30 calls/second cap on every plan.

**Calendars** (secondary: written beside the official calendar, diffed, never merged):

- `data/providers/calendar_feed.py`: `dated_event`, `write_events_file` (write-once; refuses the
  official fixture's file name; refuses events carrying actual/forecast/previous),
  `load_events_file`, `snapshot_path` (`<root>/calendar/<source>/<YYYYMMDDTHHMMSSZ>.json`) and
  `calendar_diff(official, feed)`. The file shape is the object form
  `marketstate.calendar.load_events_json` reads; `data` may not import `marketstate`, so a test
  loads every written file through that loader. `calendar_diff` matches by currency and time
  (never by name), an official no-fixed-time event matching anywhere in its window; its scope is
  the feed's span, currencies present on both sides, and `impact >= min_impact` (default high).
  `only_in_feed` is the list worth reading: releases our official fixture does not carry.
- **Finnhub economic calendar** (`data/providers/finnhub.py`): `/api/v1/calendar/economic` is
  marked **"Premium Access Required"** in Finnhub's swagger; a free key gets 401/403, raised as
  `AuthenticationRequired` naming the plan. The `time` field's zone is undocumented; the schema's
  own sample (Australian current account at 01:30 for an 11:30 AEST release) is UTC, so it is
  treated as UTC and every file says so in `time_basis`. Country codes map to currencies (euro
  members to EUR); unmapped countries, unrated impacts and bad times are counted.
- **ForexFactory weekly feed** (`data/providers/forexfactory_feed.py`), `opt_in_unclear`, off
  unless `FIBOKI_FF_CALENDAR_OPT_IN=true`. ForexFactory's notices: "The copying, republication or
  redistribution of FEED, in part or in whole, is explicitly prohibited"; no licence for the
  `nfs.faireconomy.media` export was found. JSON `date` carries an offset and is converted; the
  XML twin's zone-less times are not parsed; Holiday and Non-Economic rows are skipped and
  counted; a local 00:00 time (reportedly how "All Day"/"Tentative" is encoded) becomes a
  whole-day window with `time_known: false`, which can only widen a blackout. At most one fetch an
  hour. Nothing fetched from it is committed; the fixture is constructed.

**Positioning** (`data/positioning/`, append-only `snapshots.sqlite`, `PositioningStore.as_of`
filters on `observed_at` alone; no backfill):

- **OANDA position and order books** (`oanda_books.py`), hourly, `FIBOKI_OANDA_BOOKS_TOKEN` with
  `FIBOKI_OANDA_BOOKS_ENVIRONMENT=practice|live`. GET-only on exactly two path templates, host
  checked by parsed hostname against the two v20 REST hosts. **Availability is not
  established**: the endpoints were added in v20 3.0.25 (2018-09-28, release notes) but are absent
  from today's instrument reference, and a third-party report (Dekalog, 2024-09) says OANDA
  withdrew them. A 4xx per instrument is logged, never stored as a flat book.
- **Myfxbook Community Outlook** (`myfxbook.py`), `personal_only`, via the official API only:
  login once (email and password travel in the query string by API design; both and the session
  are scrubbed from errors), then one request per poll; a 100-per-UTC-day budget is enforced
  before every request. No vendor timestamp exists, so each poll's reading is its own row.
- CFTC COT is unchanged; it stays a macro provider because its availability comes from a release
  rule, not first sight.

**Macro pack**: `fred_cross_asset_daily` (`data/providers/fred_pack.py`, registered in
`MACRO_DATASET_PACKS`): DGS2, DGS10, DTWEXBGS, VIXCLS, DCOILWTICO through the ALFRED provider,
stored as one content-addressed dataset. VIXCLS carries "Copyright, 2016, Chicago Board Options
Exchange, Inc. Reprinted with permission.", so the pack is `personal_only`; the other four are
public domain (Federal Reserve Board, EIA).

**Not wired, stated**: no CLI commands for the calendars, positioning or the pack (`cli.py`
belonged to another item), no API route for `describe_sources()`, no worker supervision of the
positioning recorder. GDELT, Finnhub and BIS reach `fiboki news record` through
`vendor_clients_from_env` and `OFFICIAL_FEEDS` without a CLI change.
