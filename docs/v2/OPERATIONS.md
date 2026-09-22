# Operations

**Snapshot:** 2026-09-19T05:05Z. Commit `46e815f` plus substantial uncommitted work; see
`BUILD_LOG.md` for the moving-target caveat. Verified in this session:
`pytest tests/ -q` → **2683 passed, 2 skipped** in 287 s.

This document is for the person running Fiboki on their own machine. `DEPLOYMENT.md` covers
getting it installed and supervised; this one covers using it and recovering it.

---

## 1. Operating posture

Fiboki V2 is local-first: SQLite and parquet on the operator's disk, a research worker under
`launchd`, and the API in a terminal when it is wanted. Two operators share one deployment.

Three standing rules.

**A worker is a process, never a thread inside the API.** V1 ran its worker as a daemon thread
in the FastAPI process; when the thread died the API kept serving and kept returning
`{"status": "ok"}`, so every signal anybody looked at said the system was healthy while no work
was being done. Everything in `deploy/` follows from that: separate units, separate exit codes,
separate restart policies, and a supervisor that can tell you the process is gone.

**The default execution mode is `paper`.** An unrecognised `FIBOKI_EXECUTION_MODE` is a startup
error rather than a silent fallback, because a typo must not decide where orders go.

**The API stays on `127.0.0.1`.** Its security controls are implemented and tested
(`tests/api/test_security.py`), but nothing here has served a request outside a test client and
no deployment exists. Move it off loopback only after it has been deployed, reviewed in place
and exercised against a browser. `SECURITY_MODEL.md` §11 carries the control-by-control table.

## 2. The CLI

`fiboki`, a Typer application at `src/fiboki/cli.py`. Every command takes `--json` where a
machine might want the output.

| Group | Command | What it does |
|---|---|---|
| `data` | `ingest` | Import provider bytes into RAW, with declared adjustments |
| | `validate` | Run the integrity report over a dataset. Detects; never repairs |
| | `version` | Show or list dataset versions and their lineage |
| | `migrate-v1` | The recorded import of the V1 HistData store (§6) |
| `research` | `sweep` | Run a parameter sweep from a checkpointed cell list |
| | `validate` | Run the validation ladder against a candidate |
| | `campaign` | Plan a research campaign from a JSON description |
| | `memory` | "Have we tried this already?" — exact, structural and related |
| `strategy` | `list` / `show` | The registry |
| | `bind` | Bind parameter values into a document |
| | `compile` | Compile and report warmup, indicators and rule count |
| `worker` | `run <kind>` | Run a worker **process** in the foreground |
| | `status` | Heartbeat age, lease holder, and `--release` to force-expire a lease |
| `system` | `health` | The real health checks (§4) |
| | `doctor` | Diagnose a broken local setup. **Run this first** |
| | `metrics` | Render the Prometheus exposition text |
| `broker` | `status` | Venue reachability and health |
| | `reconcile` | Reconcile an intent store against the venue, keyed on broker reference |
| `killswitch` | `pause` / `flatten` / `status` | §5 |

### Exit codes

| Code | Meaning | What a supervisor should do |
|---:|---|---|
| 0 | clean stop | nothing |
| 1 | fatal — resume failed, or too many consecutive failures | restart with backoff |
| 2 | misuse — a missing path, an unknown argument | do not retry; fix the command |
| 75 | another holder has the lease | **do not restart-loop.** Run `fiboki worker status` |

Both supervisor unit files encode this: `launchd` throttles to 60 s, and `systemd` treats 75 as
a successful exit so it stops rather than spinning.

## 3. Running the workers

```bash
fiboki worker run research --nice 10
```

Foreground is the default **on purpose**: a worker that daemonises itself is a worker whose
death is invisible. Run it under `launchd` or `systemd` and let the supervisor own the restart
policy.

The worker takes a **single-writer lease** before doing anything. Two workers cannot both run;
the second exits 75. This is the direct fix for V1, where a web service and a worker service
could both trade, sharing a default `worker_id` of the literal `"railway-worker"` so that the
duplication overwrote its own heartbeat row and was invisible on the System page.

`--once` and `--max-cycles` exist for exercising the process without committing to a long run.

**The live worker cannot be started from a bare CLI invocation.** `fiboki worker run live`
refuses, with the reason stated: the live worker needs a wired execution service, feed,
evaluator and risk-context builder, and *"a live worker assembled from command line flags is a
live worker whose risk configuration nobody reviewed."* It is started from the application
entrypoint that owns that wiring.

`fiboki worker run research` invoked standalone warns that the orchestrator has no handlers
registered and will idle. That path exists so the process, the lease and the heartbeat can be
exercised on their own; handlers are registered by the application wiring.

### Resource behaviour on a laptop

`workers/scheduler.py` admits work as a function of *observed* resource state, not a fixed
worker count, because the machine running a 10,000-cell sweep is the same machine the operator
is reading charts on. It leaves a CPU reserve free, refuses admission when the one-minute load
exceeds a multiple of the core count, refuses admission below a memory floor (on a Mac, swapping
is what makes the machine feel broken, and a pandas sweep will happily consume everything), and
reports GPU availability without pretending to schedule GPU memory. On battery it caps
parallelism to one job.

The stated reason: *throughput you cannot leave running overnight is not throughput.*

`apply_nice` and the `launchd` plist's `ProcessType Background` / `LowPriorityIO` do the rest.

## 4. Health, and what "healthy" means

```bash
fiboki system doctor     # run this first when something is wrong
fiboki system health
fiboki system metrics
```

`doctor` never guesses: each line is a named check with a verdict, and the instruction is to fix
what it prints, in order.

`health` runs the real checks — a real database connection and `SELECT 1`, the Alembic revision,
the build SHA, and the worker heartbeat age. **The overall status is the worst component status,
never an average and never an assertion.** V1's endpoint returned a hardcoded
`{"status": "ok", "version": "1.0.0"}` and stayed green with the database stopped.

Two states that are deliberately distinguished:

- `worker_heartbeat_age_seconds` of **`null`** means no worker has *ever* beaten — *"Nothing is
  evaluating signals in this deployment"* — which is a different fact from an age of `0`.
- `migration_revision` of `null` means the schema version is unknown, reported as a
  **degradation** rather than as fine.

If `FIBOKI_SESSION_SECRET` is unset, sessions are signed with a per-process key and restarts
invalidate them. That is correct for a development box and is reported as a health warning so
nobody ships it.

If the platform is running against `api/seed.py` rather than a provisioned data store,
`data_source` reports `"seed"`, the services endpoint reports the datasets as **absent**, and
health is **DEGRADED rather than OK**. Nothing in the seed pretends to be a measurement.

## 5. The kill switch

```bash
fiboki killswitch status
fiboki killswitch pause   --reason "spreads blowing out on the ECB release" --actor joe
fiboki killswitch flatten --reason "unexplained divergence in reconciliation" --actor joe
```

`--reason` is **required**; it goes in the journal.

| | `pause` | `flatten` |
|---|---|---|
| New positions / adds | blocked | blocked |
| Reduce, close | **permitted** | **permitted** |
| Risk-reducing amendments | permitted | blocked |
| Existing positions | left open, **keep their stops** | closed now at market |

`pause` prints a reminder that opens are blocked while closes and reductions still run, and
tells you to confirm with `status`.

**There is no deactivate command and no timeout.** Disarming requires an explicit operator
action through the API, and the switch never clears itself: *a switch that turns itself off is
not a kill switch.* The journal is fsynced on every append and state is recovered from it on
startup, because the switch's state must survive the crash that made you hit it.

`FLATTEN` closing orders are dispatched through the **normal** ordering path, so they are
recorded, idempotent and recoverable like any other order — not a side channel.

## 6. Data operations

```bash
fiboki data ingest    --source <provider> ...
fiboki data validate  <path>
fiboki data version   --catalogue <path>
fiboki data migrate-v1 --source <v1 store>
```

`FIBOKI_DATA_ROOT` must be set and must point at a directory containing a `.fiboki-data-root`
marker written by `DataStore.initialise`. There is no search and no fallback. This is the direct
fix for V1's root resolution, which walked *up* the filesystem, found a staging directory, and
recorded 99% of a research batch as complete having tested nothing.

`validate` **detects and never repairs.** A repair is a separate, explicit operation requiring a
named actor and a written reason, and it produces a **new dataset version** whose lineage points
at the unrepaired one. The original bytes stay resolvable forever. See `DATA_ARCHITECTURE.md`
§5.

`migrate-v1` performs the recorded import of the V1 HistData store: EST-no-DST corrected to true
UTC, `price_basis = BID` declared, both recorded as adjustments, integrity validated, **nothing
repaired**. The V1 store contains genuinely broken bars and the point is that they arrive
labelled as broken.

## 7. Research operations

```bash
fiboki research memory   --ledger <path> ...       # FIRST. Has this been tried?
fiboki research campaign research/campaigns/x.json
fiboki research sweep    --checkpoints <db> ...
fiboki research validate --ledger <path> ...
```

`memory` before anything else. It answers on three keys — exact content hash, **structural**
hash (the same rules over the same indicators, differing only in numbers), and weighted
similarity plus free-text recall — and returns the prior experiments *with the rungs they died
at*, because "failed at deflation with a DSR of 0.41" and "failed at rung 0 because the data was
wrong" call for opposite decisions.

`sweep` checkpoints **honestly**. V1 marked a cell DONE unconditionally, including cells that
returned no data, so a restart skipped them permanently and the sweep wrote
`phase1_complete.json` having never computed roughly 8% of its cells. The research worker
separates `DONE` from `NO_DATA`, retries `NO_DATA` on the next run, keeps the record so the
reason is visible, and treats too much missing data as a **failure rather than a footnote**
(`AlertEvent.SWEEP_NO_DATA_EXCEEDED`).

`validate` runs the ladder and writes a `ValidationReport` **whether the candidate passes or
fails**. Read the binding constraint, not the verdict.

Set `LadderConfig.external_trial_count` to the honest size of the campaign. Nothing can enforce
this for you; the report records the value you used.

## 8. Broker operations

```bash
fiboki broker status
fiboki broker reconcile --intents <path/to/intents.jsonl>
```

Reconciliation is keyed on the **broker** reference. V1 compared an internal `uuid4` against
IG's `dealId` — key spaces that never intersect — so it reported every position missing on both
sides and could not produce a clean result even on a healthy system.

The one exception: an intent that never learned its broker reference because the response was
lost is searched at the venue by the `client_ref` we sent, in order to *learn* the broker
reference, which is then persisted.

**A divergence count of anything but zero is an incident**, not a report to read later.
`AlertEvent.RECONCILIATION_DIVERGENCE` is CRITICAL.

## 9. Backup and restore

**There is no backup command and no restore command.** This is a gap, stated rather than papered
over, and it is on `ROADMAP.md`.

What exists is a set of files that are individually append-only or content-addressed, which
makes a file-level copy a valid backup provided nothing is mid-write. Everything mutable lives
under `FIBOKI_HOME` (default `~/.fiboki`) or `FIBOKI_STATE_DIR` (default `var/`), and the market
data lives under `FIBOKI_DATA_ROOT`.

### What to back up, and why each matters

| Path | Contains | Recoverable without it? |
|---|---|---|
| `$FIBOKI_DATA_ROOT/catalogue.db` | dataset versions and lineage | **No.** Without it, stored results cannot re-resolve their bytes |
| `$FIBOKI_DATA_ROOT/raw/` | immutable source bytes | Only by re-downloading, and the provider may have revised history |
| `$FIBOKI_DATA_ROOT/canonical/` | derived bars | Yes — rebuildable from RAW *if* the catalogue survived |
| experiment ledger (SQLite) | every experiment ever run | **No.** This is the research record |
| holdout registry (SQLite) | which strategy has spent its one look | **No.** Losing it silently grants second looks |
| `killswitch.jsonl` | switch state and history | **No.** State is recovered from this file |
| intent store (`*.jsonl`) | orders, including PENDING and UNKNOWN | **No.** This is how a crashed order is found again |
| agent audit ledger | hash-chained agent actions | **No** |
| `api_audit.jsonl` | hash-chained operator actions | **No** |
| quote recorder segments | executable prices | **No.** The quotes are gone otherwise; value grows with elapsed time |
| execution telemetry segments | realised slippage and fills | **No** |
| `var/sessions.json` | active sessions | Yes — losing it logs everyone out |
| `research/strategies/*.json` | strategy documents | Yes, via git |

### Procedure

**Backup.** Stop the worker (`launchctl bootout` / `systemctl stop`) so nothing is mid-write,
then copy the whole of `$FIBOKI_HOME`, `$FIBOKI_STATE_DIR` and `$FIBOKI_DATA_ROOT`. The SQLite
files should be copied with `sqlite3 <db> ".backup <dest>"` rather than `cp` if the worker
cannot be stopped. Restart the worker.

**Restore.** Restore the three trees, then run `fiboki system doctor` and fix what it prints, in
order. Then `fiboki worker status` to confirm no stale lease is held — if one is, `--release` it
deliberately, which is an operator action and is logged.

**Verification, which is the part that makes a backup worth having.** After a restore, verify
the two hash chains (`AuditLedger.verify_chain` and the API audit trail's equivalent) and
re-check a dataset's `content_checksum` against its registered value; `ChecksumMismatch` is
raised on read if stored bytes no longer hash to what the catalogue says. A restore that passes
those three checks has restored the record, not merely the files.

**Untested.** No restore has been performed in this repository. The procedure above is derived
from the storage design, not from an exercise, and it should be rehearsed before it is relied
on.

## 10. Routine

**Daily, while any worker is running.** `fiboki worker status` — heartbeat age and lease holder.
`fiboki system health` — the four checks. If reconciliation runs, its divergence count should be
exactly zero.

**Per research campaign.** Record the campaign size and set `external_trial_count` from it. Read
`PBO` and `DSR` on every run: if PBO drifts above 0.2 the selection process is broken again.

**Weekly.** `make -f deploy/Makefile lock-check` for dependency drift, and `make audit` for the
vulnerability audit when the machine has network. Review the realised-versus-modelled spread
divergence per instrument; a persistent gap means every stored expectancy for that instrument is
overstated by that much per trade, and should be said out loud rather than absorbed.

**Before quoting any number to anyone.** Check its `Provenance`. Backtest, walk-forward,
out-of-sample, holdout, paper, shadow, broker-demo and broker-live are eight different claims,
and the API will not emit an unlabelled float.

## 11. Incident playbook

**The worker is dead.** `WORKER_DOWN` fires at CRITICAL after 300 s of heartbeat silence,
whether or not anybody is looking. Check the supervisor first (`launchctl print` /
`systemctl status`), then the exit code: 75 means another holder has the lease, and the
correct response is `fiboki worker status`, **not** a restart loop. On restart the worker
resumes and reconciles; `PENDING` and `UNKNOWN` intents are looked up at the venue.

**Reconciliation diverges.** Do not trade. Arm `killswitch pause`, establish which side is
wrong from the intent store (which was written **before** dispatch, so a record exists even for
an order whose response was lost), and resolve each `UNKNOWN` deliberately. `UNKNOWN` is
non-terminal and keeps appearing in reconciliation reports until a human or the venue resolves
it — that is correct behaviour, not a stuck state.

**A strategy is behaving unlike its backtest.** Compare realised against modelled spread for its
instruments before concluding anything about alpha. The backtest uses a **static** spread
assumption and zero-to-modest slippage; `divergence_report` answers whether the cost model is
still telling the truth. Then check the regime: the V1 audit's sharpest finding was that six of
fourteen surviving results were USDJPY during one exceptional trend, and
`marketstate/regime.regime_dependence` makes that question mechanical.

**A number looks too good.** The metrics layer raises `DegenerateMetricError` rather than
flattering, so a suspiciously round profit factor is more likely a real edge than an artefact —
but check `n_trades` first, then the `Provenance`, then whether the holdout was consumed. A
result quoted from `IDEALISED_RESEARCH` alone is not a result; it is the numerator of a
cost-impact ratio.

**You need to stop everything.** `fiboki killswitch flatten --reason "..." --actor <you>`.
Closes go through the normal ordering path so they are recorded and recoverable. Then confirm
with `killswitch status` and with a reconcile.

## 12. What is not yet operable

Stated so this document is not read as describing a running system.

- **No live worker can be started.** The CLI refuses it by design until the wiring exists.
- **No scheduled reconciliation.** `fiboki broker reconcile` is manual; nothing runs it on
  startup or on a timer.
- **No backup or restore command**, and no rehearsed restore.
- **No market data in this repository.** `data/` is empty and the migration has never been run
  against the real 7.2 GB V1 store here.
- **No broker credentials.** The OANDA adapter is proved against recorded fixtures and has never
  spoken to a venue.
- **No strategy has been validated end to end** against real data, so no candidate exists and no
  holdout has been consumed.
- **No lifecycle transitions and no demotion monitors.** See `STRATEGY_STANDARD.md` §9.
