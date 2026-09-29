# Observability Standard

**Snapshot:** 2026-09-19, commit `46e815f`. Modules: `src/fiboki/obs/{logging,metrics,alerts}.py`,
`src/fiboki/api/{logging,health,audit_trail}.py`, `src/fiboki/data/telemetry.py`.

**Read this first.** `obs/`, `api/` and `workers/` are implemented and tested
(`tests/unit/test_obs_{logging,metrics,alerts,health}.py`, `tests/api/`,
`tests/unit/test_worker_{base,scheduler}.py`, `tests/integration/test_worker_processes.py`) and
are **uncommitted** at this snapshot. Nothing in this document should be read as a claim that a
running deployment behaves this way, because **no deployment exists**: nothing starts a
watchdog, nothing writes a heartbeat outside a test, and nothing schedules a reconcile. The
design, the test coverage and the wiring gap are stated separately throughout.

---

## 1. The question this standard has to answer

**"What happens when the worker dies at 3am?"**

In V1 the answer was: nothing tells you. There was no Sentry in `worker.py`. There was no
`worker_down` and no `heartbeat_stale` event in `alerts/events.py` — the seven defined events
were all trade or bot lifecycle, so the Telegram channel worked perfectly and had nothing to
send. Heartbeat freshness was computed **only when a human loaded the System page**, which makes
it a dashboard widget rather than monitoring. The health endpoint returned a hardcoded
`{"status": "ok"}` and could not report the failure. And because the worker was a daemon thread
inside the API, the API stayed green. You would find out when you next happened to look.

Worse, the failure was *silent in the right direction*: a dead worker and a strategy with no
signals look identical. V1 also had a path where a restarted worker ran blind for roughly 100
candles — on H4, about two weeks — with the exception swallowed by a bare
`except Exception: return None` and the heartbeat still reporting the bot as active.

§6 answers the 3am question against V2's design, and states which parts of that answer are
implemented.

## 2. Logging

Two modules exist, `obs/logging.py` and `api/logging.py`, with overlapping purposes; that
duplication is noted in §8.

**The V1 defect.** V1 built its "JSON" log lines with an f-string:

```python
f'{{"level":"{level}","msg":"{message}"}}'
```

which stopped being JSON the moment a message contained a quote, a backslash or a newline — that
is, every traceback and every broker error body. The shipper dropped those lines silently, so
the only records that survived were the boring ones. The interesting ones, with the quoted
broker payload in them, were exactly the ones that vanished.

**V2's rule:** the record is built as a `dict` and handed to `json.dumps`. There is **no string
concatenation anywhere in the formatting path**, so escaping is the standard library's problem
and is correct by construction. `tests/unit/test_obs_logging.py` asserts a round-trip through
`json.loads` for a message containing quotes, backslashes, newlines, control characters and
non-ASCII.

**Correlation ids.** A `contextvars` variable carries the correlation id, so a line emitted deep
inside a strategy evaluation is attributable to the job that caused it without threading an id
through every signature. The context is inherited by threads started through a `bind`-wrapped
callable and by asyncio tasks, and is restored on exit even when the block raises.

On the HTTP side, one id is minted per request, returned in `X-Correlation-Id`, attached to every
log line the request produces, and quoted in any error body. That id is the whole point: when an
operator says *"the exposure page showed nothing at 14:07"*, there is one token that joins their
screenshot to the server's record. `api/logging.py` also carries an `_ACTOR` context var, so a
line can say who caused it.

Error bodies carry the correlation id and nothing internal; the traceback, file path, SQL
fragment and settings value go to the log under the same id. See `SECURITY_MODEL.md` §5.

## 3. Metrics

`obs/metrics.py` is an in-process registry with a Prometheus text-exposition renderer, and it is
dependency-free on purpose.

Fiboki runs local-first: on a Mac, in the foreground, often with no Prometheus anywhere near it.
A metrics layer that only works when a scrape target exists is a metrics layer nobody enables
during development, which is precisely when the numbers matter most. So the registry is a plain
object readable from a test —
`registry.value("fiboki_jobs_total", queue="research")` — and rendered to text when something
asks. The exposition format is implemented here rather than pulled from `prometheus_client`
because this project pins every dependency that can change a number, and an observability
library is not worth a pin; the format is small and stable and the renderer is about sixty lines,
tested against the spec's escaping rules.

Three instrument types: `Counter`, `Gauge`, `Histogram`. Default histogram buckets straddle what
actually happens here — a fast sweep cell at 10 ms, a normal backtest at 1–30 s, and the long
tail.

### The named helpers, and the question each answers

| Helper | Operational question |
|---|---|
| `record_job_started` / `record_job_finished` | are jobs actually **completing**, or just starting? |
| `record_queue_depth` | is work piling up behind a stuck worker? |
| `record_worker_liveness` | is the worker alive, and how stale? |
| `observe_backtest_wall_time` | has a sweep cell got 40× slower since the change? |
| `record_data_freshness` | how old is the newest bar, per instrument? |
| `record_broker_health` | is the venue reachable, and how slow? |
| `record_reconciliation_divergence` | how many intents disagree with the venue **right now**? |
| `record_spread_divergence` | is the backtest's cost model still telling the truth? |

The last one deserves its place. Realised-versus-modelled spread is the number that decides
whether a stored backtest may still be believed, and it belongs in the metrics layer rather than
a research notebook, because it degrades continuously and nobody re-opens a notebook. It pairs
with `data/telemetry.divergence_report`, which computes the same comparison per instrument from
recorded execution telemetry: a positive `excess_cost_pips` means live trading is more expensive
than the backtest assumed and **every stored expectancy for that instrument is overstated by
that much per trade.**

`timer` is a context manager for wall-time observation; `snapshot` and `iter_metric_names` exist
for tests and for the health surface.

## 4. The alert taxonomy

`obs/alerts.py`. Twenty-nine events in two groups. The first seventeen are the **failure** events
V1 did not have in any form.

### Failure events

| Event | Default severity | Fires when |
|---|---|---|
| `WORKER_DOWN` | **CRITICAL** | heartbeat older than `down_after_seconds` (300 s), or an expected worker has never beaten |
| `HEARTBEAT_STALE` | ERROR | heartbeat older than `stale_after_seconds` (120 s) |
| `WORKER_LEASE_CONTENDED` | **CRITICAL** | two workers claim one lease — V1's duplicate-order condition |
| `RECONCILIATION_DIVERGENCE` | **CRITICAL** | the venue and our intents disagree |
| `DATA_STALE` | WARNING | the newest bar is older than expected |
| `DATA_QUALITY_DEFECT` | WARNING | an integrity scan found a blocking defect |
| `BROKER_UNHEALTHY` | ERROR | health score below the limit, or unreachable |
| `RISK_LIMIT_BREACH` | **CRITICAL** | a gateway check blocked on a limit |
| `KILL_SWITCH_ACTIVATED` | **CRITICAL** | operator armed PAUSE or FLATTEN |
| `KILL_SWITCH_DEACTIVATED` | WARNING | operator disarmed it |
| `ORDER_REJECTED_REPEATEDLY` | ERROR | a venue keeps refusing |
| `STRATEGY_DEGRADED` | WARNING | a strategy's live behaviour has decayed |
| `QUEUE_BACKED_UP` | WARNING | queue depth above threshold |
| `JOB_DEAD_LETTERED` | ERROR | retries exhausted |
| `SWEEP_NO_DATA_EXCEEDED` | ERROR | **the V1 silent-data-root failure**, made loud: too many cells returning no data |
| `MIGRATION_DRIFT` | ERROR | the running schema revision is not the expected one |
| `SPREAD_MODEL_DIVERGENCE` | WARNING | realised cost has drifted from the modelled assumption |

`SWEEP_NO_DATA_EXCEEDED` is worth pausing on. V1 recorded `no_data` as a *completed* outcome, so
99% of a research batch was written into the checkpoint as DONE having tested nothing, and the
batch looked finished. The data store now raises rather than returning an empty frame, and this
alert is the second line of defence at the campaign level.

### Lifecycle events

`SIGNAL_GENERATED`, `ORDER_SUBMITTED`, `ORDER_ACCEPTED`, `ORDER_REJECTED`, `ORDER_UNKNOWN`,
`POSITION_OPENED`, `POSITION_CLOSED`, `STOP_LOSS_HIT`, `TAKE_PROFIT_HIT`, `POSITION_FLATTENED`.

These are the ten V1 had (approximately — V1 had seven). Note `ORDER_UNKNOWN` at ERROR: an
unconfirmed order is a distinct, non-terminal, alertable state, not a rejection.

### Channels and delivery

`AlertChannel` is a protocol with four implementations: `MemoryChannel` (tests),
`ConsoleChannel`, `FileChannel`, `WebhookChannel` and `TelegramChannel`.

**Each channel is isolated.** V1 let one channel exception kill the dispatch, so a single
misconfigured webhook suppressed every other channel for the same alert. Here a channel that
raises is counted, logged and stepped over.

**Delivery guarantees are stated honestly** in the module's own docstring: the dispatcher is
best-effort and in-process, it does **not** persist an outbox, and an alert raised in the instant
before a hard kill can be lost. The mitigation is that the conditions that matter — a stale
heartbeat, a divergent reconciliation — are **level**, not edge: the watchdog re-evaluates them
on its next tick and fires again. Anything that must not be lost belongs in a ledger, not in an
alert. That is why the kill-switch journal, the intent store, the experiment ledger and both
audit ledgers are durable files and the alert channel is not.

`dedupe_key` prevents a repeating condition from producing a message every tick.

## 5. The heartbeat watchdog

`HeartbeatWatchdog` is the piece V1 did not have in any form.

`evaluate()` is **pure** — it returns the list of alerts it actually dispatched — so the logic is
testable without a thread. `start()` runs it on an interval (30 s by default) in a daemon thread
for a real deployment. **Nothing about the evaluation depends on a page being loaded.**

Thresholds: `stale_after_seconds = 120` → `HEARTBEAT_STALE` at ERROR; `down_after_seconds = 300`
→ `WORKER_DOWN` at CRITICAL. The two are distinct because they mean different things: stale is
"something is wrong", down is "treat it as dead". The `WORKER_DOWN` message says so explicitly,
including the sentence *"the API being healthy says nothing about it"* — which is the exact V1
inference failure written into the alert text.

`expected_workers` means a worker that has **never** beaten is also detected, rather than being
invisible because it produced no row to go stale.

`HeartbeatView` is supplied by a callable, so the watchdog does not import the worker package and
a test can hand it a list.

Every pass also writes `record_worker_liveness`, so the metric and the alert cannot disagree
about freshness.

## 6. What happens when the worker dies at 3am

**As designed, and this is what the code would do if the processes existed:**

1. The worker stops writing `<state_dir>/worker.heartbeat`.
2. Within one watchdog tick — at most 30 seconds — `evaluate()` observes an age above 120 s and
   fires `HEARTBEAT_STALE` at ERROR to every configured channel, isolated from each other.
   `record_worker_liveness` records `fresh=False`.
3. At 300 s the same pass fires `WORKER_DOWN` at CRITICAL, deduplicated on
   `worker_down:<worker_id>`, carrying the worker kind, the age, the last status and the last
   error.
4. `GET /api/health` reports `worker_heartbeat` as **`down`** with the detail *"Worker heartbeat
   is Ns old (stale after 120s)"*, and — because the overall status is the **worst** component
   status, never an average and never an assertion — the endpoint as a whole is not `ok`.
5. The condition is **level-triggered**, so a lost alert is re-fired on the next tick. The
   operator does not have to have been watching at the moment it happened.
6. On restart, the execution service reconciles: `PENDING` and `UNKNOWN` intents are looked up at
   the venue (by broker reference, or by client reference for those that never learned one), and
   any divergence fires `RECONCILIATION_DIVERGENCE` at CRITICAL.

**What is actually true at this snapshot.** Every component in that sequence exists and is
tested: the worker process writes its heartbeat on every cycle including a failed one
(`workers/base.py`), `HeartbeatWatchdog.evaluate()` fires both alerts at the right thresholds,
`build_health` reports the worst component, and `ExecutionService.reconcile` works. What does
**not** exist is the wiring that runs them together: **nothing starts a watchdog outside a
test, and nothing schedules a reconcile.** So the honest answer today is that the machinery to
answer the 3am question is built and nothing has been left running long enough to ask it.
Closing that is near the top of `ROADMAP.md` §6.

## 7. Health checks

`api/health.py`. V1's returned a hardcoded `{"status": "ok", "version": "1.0.0"}`, was green with
the database stopped, and disagreed with `pyproject.toml` about its own version.

Every field is **measured at request time**:

| Check | Critical | What it does |
|---|---|---|
| `database` | yes | a real connection and a real `SELECT 1`, with latency |
| `migration_revision` | no | reads `alembic_version`. `null` is reported as a **degradation**, not as fine |
| `build_identity` | no | `FIBOKI_BUILD_SHA`; empty means "this process cannot say which commit it is running" |
| `worker_heartbeat` | yes | age in seconds. **`null` (never beaten) is a different state from `0`** and is reported as `down` with *"Nothing is evaluating signals in this deployment"*. Staleness is the platform's rule, **age >= threshold**, so a beat exactly at the threshold is stale here, in `/api/system/workers` and in the stream heartbeat alike. An **unreadable** store says *unreadable, liveness UNKNOWN*, never *never written* |
| `paper_journal` | no | present only when a paper journal exists. Any session that does not parse makes it `degraded` and names the session; a journal with no readable session says the record is *unavailable, not empty* |

The report also carries the execution mode, uptime, Python version, and an `advisory` field set
when the report itself should not be treated as a green light — for example when the session
secret is ephemeral (§`SECURITY_MODEL.md` §2) or when the data source is the deterministic seed
generator rather than a provisioned store.

**The overall status is the worst component status, never an average and never an assertion.**
`ok` requires every critical check to pass.

`api/seed.py` deserves a mention here because it is an honesty control rather than a
convenience. The repository ships no market data, no paper journal and no broker session, and the
operator workstation still has to be built and tested against something. The seed generator
produces that something from a fixed seed, and it is **labelled everywhere it surfaces**:
`Platform.data_source` reports `"seed"`, the services endpoint reports the datasets as absent,
and health is **DEGRADED rather than OK**. Nothing in it pretends to be a measurement, and each
generated trade still carries a real `Provenance` drawn from a realistic mix — because the whole
point of the UI work is that a mixed-provenance table renders honestly.

### 7.1 The live stream, incidents and the attention queue

`GET /api/stream` (`api/routers/stream.py`, sse-starlette 3.0.3) is the workstation's one live
feed. Contract, for the frontend and for anyone debugging it:

- Envelope: `id: <topic>:<seq>`, `event: <topic>.<kind>`, `data: {topic, seq, kind, as_of,
  source, data}`; kinds `snapshot | delta | tombstone | heartbeat`. Entities are the REST shapes
  (a position is a `PositionRowView`, health is the `HealthReport`), so every number is the same
  `Figure` REST returns, with the same named exceptions.
- Snapshot per topic on subscribe; then deltas keyed by entity id (full upserts, idempotent) and
  tombstones. A snapshot carries the topic's current seq, so **dedupe on `(event, id)`** and ignore
  any event whose seq is not above the applied one.
- Per-topic seq starting at a per-process base (epoch ms), so a restart forces a visible gap.
  Ring buffer of 500 events per topic; `Last-Event-ID` is a cut across all topics (one hub, one
  publication order) and the gap is replayed; a topic whose needed events aged out gets a fresh
  snapshot.
- Heartbeat every 5 s: `server_time`, `worker_heartbeat_age_s` (a Figure from the
  `worker_heartbeat` table reader, `null` when nothing ever beat), `worker_state`, mode, kill
  switch and every topic's `seq`/`as_of`/`source_kind`. **A connected stream over a dead worker
  therefore shows stale** (§10 rule 1). A revoked session ends the stream at the next heartbeat.
- Health is re-evaluated on the stream's 15 s timer, never per subscriber. Mode and kill switch
  come from a fresh replay of the kill-switch journal each second, so an arm by another process
  is seen within about a second. Positions, fleet and risk use the REST readers and are `absent`
  or `seed` exactly as REST says. `marks` is `absent` until a price feed is composed into the API
  (ARCHITECTURE.md §12); `publish_mark` coalesces to 4 Hz per instrument, latest wins.
- **The stream never starts a worker.** It is an asyncio task inside the API process that reads
  the stores workers write, and it runs only while someone is subscribed.
  `tests/api/test_stream.py::test_the_stream_never_starts_a_worker` enforces it.

Incidents (`GET /api/system/incidents`) are derived, read-only, from the alert log
(`FIBOKI_ALERT_LOG`, the `FileChannel` JSONL) and the kill-switch journal: repeated alerts with
one dedupe key fold into one incident (a 6 h silence starts a new one), a disarm resolves an arm,
and each carries its timeline. Without `FIBOKI_ALERT_LOG` the list says so in a caveat, because
an empty incident list is not a quiet system. Acknowledge and note are audited appends to
`<state>/incident_annotations.jsonl`; an acknowledgement is not a resolution, and a recurrence
re-opens the incident.

`GET /api/command/attention` ranks kill switch, limit breaches, open incidents, stale workers,
unhealthy checks, strategies awaiting review and warning-level record caveats, server-side:
`score = SEVERITY_WEIGHT + CATEGORY_WEIGHT`, severity always dominating, then newest, then id.
The weights are pinned by a test. A source that cannot be read becomes an item of its own.
Breaches computed on the seed fixture are not raised.

## 8. Audit trails as observability

Two hash-chained, append-only ledgers, kept separate because they answer different questions with
different retention needs:

- `agents/audit.py` — what an **agent** did. Every tool call, model call and refusal, with full
  inputs and outputs, the prompt, the parent action, the model and version, tokens, cost, wall
  time and outcome. `verify_chain` detects an edited or removed record.
- `api/audit_trail.py` — what a **human operator** did through HTTP. Sealed with
  `sha256(previous_hash + canonical_payload)`. **Refusals are recorded as loudly as successes**:
  an attempted kill-switch disarm by a non-admin is exactly the row an incident review needs, and
  V1 wrote neither.

Alongside them, the durable operational records: the kill-switch journal (fsynced per append,
recovered on startup), the order intent store (fsynced, written **before** dispatch), the
experiment ledger (SQLite triggers on UPDATE and DELETE), the quote recorder and the execution
telemetry store (CRC-framed segments where a torn tail is skipped and reported while mid-segment
corruption raises).

The division of responsibility is deliberate: **alerts are best-effort notification, ledgers are
the record.** Anything you would be unwilling to lose is in a ledger.

## 9. What V1 had, what V2 has, and what is still missing

| Capability | V1 | V2 design | V2 today |
|---|---|---|---|
| Structured logs that survive a quoted payload | no (f-string JSON) | yes (`json.dumps`) | **tested** |
| Correlation id across a request and its jobs | no | yes (`contextvars`) | **tested** |
| `worker_down` / `heartbeat_stale` events | **absent from the taxonomy** | present, CRITICAL / ERROR | **tested** |
| Heartbeat evaluated on a timer | no — only on page load | `HeartbeatWatchdog` | tested; **nothing starts it** |
| Heartbeat written on every cycle, failures included | no | `workers/base.py` | **tested** |
| Health endpoint that can fail | no — hardcoded `ok` | four measured checks, worst-wins | **tested**; never served outside a test client |
| Channel isolation | no — one exception killed the dispatch | yes | **tested** |
| Reconciliation divergence alert | reconciliation could not produce a clean result at all | CRITICAL event | tested; **not scheduled** |
| Cost-model drift metric | no | `record_spread_divergence` + `divergence_report` | tested; **no live data** |
| Operator action audit | no | hash-chained ledger | **tested** |
| Agent action audit | n/a | hash-chained ledger | **committed and tested** |
| Error-reporting integration (Sentry or equivalent) | absent from the worker | an injected error reporter on `Worker`; exceptions are recorded on the heartbeat, counted, alerted and reported rather than swallowed | interface **tested**; no concrete reporter configured |
| Alert outbox / guaranteed delivery | no | deliberately not attempted; level-triggered instead | n/a |
| Log retention / rotation | no | **not designed** | ledgers grow without bound |
| Login-failure alert event | no | **not in the taxonomy** | gap |
| `obs/logging.py` vs `api/logging.py` duplication | n/a | — | **two implementations of the same concern**; one should absorb the other |

## 10. Standing rules

1. **A number nobody evaluates is not monitoring.** If a condition matters, something must
   evaluate it on a timer and fire; rendering it on a page is not enough. This is the single
   lesson of V1's heartbeat.
2. **Level, not edge.** Prefer conditions that can be re-evaluated and re-fired over events that
   must not be missed. The alert channel is best-effort by design, and building an outbox is a
   worse trade than making the conditions re-checkable.
3. **Absence is a state.** `null` heartbeat age, `null` migration revision and a missing build
   SHA are all reported as degradations, never as zero and never as fine. This is the same rule
   the data platform applies to a missing dataset.
4. **The worst component wins.** Health is never averaged.
5. **Refusals are records.** A blocked order, a denied tool call and a rejected login are all
   things that must appear in a ledger, because "why didn't it trade?" must be answerable after
   the fact.
6. **Never build a log line by concatenation.** Construct a dict and serialise it.
