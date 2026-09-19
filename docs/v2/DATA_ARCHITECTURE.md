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
