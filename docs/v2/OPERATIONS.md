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
| `doctor` | (none) | Desktop readiness: toolchain, pins, `.venv`, `node_modules` platform, git, env vs `ENV_REGISTRY`, data root, ledgers, heartbeat, news, calendar, local model and digest, disk, ports, launchd. OK/WARN/FAIL with the fix; `--json`; exit 1 on any FAIL (§13) |
| | `model` | The local model server, the model it serves and its weights digest |
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

**Paper trading forward has that entrypoint.** `fiboki paper forward --wiring <file>` assembles
a PAPER-only market-facing worker from a committed, versioned wiring file
(`src/fiboki/entrypoints/wiring/paper_forward_v1.json`) and runs it in the foreground. The
runbook is §13.6. `fiboki worker run live` still refuses.

`fiboki worker run research` invoked standalone warns that the orchestrator has no handlers
registered and will idle. That path exists so the process, the lease and the heartbeat can be
exercised on their own; handlers are registered by the application wiring.

**Agent research cycles (off by default).** Set `FIBOKI_AGENT_CYCLES=on` and the research worker
composes the research runtime at start (`workers/research_runtime.py`): it registers every
deterministic job handler (the CLI's "no handlers" warning is then stale), runs one
`run_research_cycle` per night and runs a failure investigation for each queued incident.

| Variable | Default | Meaning |
|---|---|---|
| `FIBOKI_AGENT_CYCLES` | `false` | Compose the runtime. Strict boolean. |
| `FIBOKI_AGENT_CYCLE_TARGET` | (none) | `strategy_id:INSTRUMENT:TIMEFRAME`; required when on. |
| `FIBOKI_AGENT_CYCLE_UTC` | `02:15` | UTC time of the nightly cycle. |
| `FIBOKI_AGENT_PROVIDER` | `echo` | `echo` (offline double: every model step fails and is recorded, a wiring check only) or `local`. |
| `FIBOKI_AGENT_LOCAL_MODEL` | (none) | Exact Ollama model name, or the llama-server `--alias`; required for `local`. |
| `FIBOKI_AGENT_LOCAL_URL` | `http://127.0.0.1:11434` | Local model server base URL (Ollama; llama-server is `http://127.0.0.1:8080`). llama.cpp needs the one-line runtime change in §13.4. |
| `FIBOKI_STRATEGIES_DIR` | `research/strategies` | Documents registered at start. |
| `FIBOKI_DATA_ROOT` | (none) | Required when on: the bars the queued backtests read. |

The worker refuses to start if the flag is on and the target, the seed or the data root is
missing. The audit chain is `<FIBOKI_STATE_DIR>/agents/audit.jsonl`; the last claimed nightly
slot is in `<FIBOKI_STATE_DIR>/agents/schedule.json`. A slot is claimed **before** it runs, so a
cycle that crashes the worker is not retried that night; the first start after enabling the flag
records the current slot and waits for the next one. While a cycle runs the worker renews its
lease and writes a `working` heartbeat every `pulse_seconds` (15 s), so a long local-model cycle
does not read as a dead worker. Incidents: `ResearchRuntime.raise_incident(backtest_id)` queues
one investigation per backtest per UTC day; strategy-degraded/halted/quarantined alerts raised
in the research worker's own process queue one automatically when they carry a `backtest_id`.
Alerts from other processes do not reach it.

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

### Where the API reads the worker heartbeat and the paper journal

**Heartbeat.** `FIBOKI_WORKER_HEARTBEAT` names the worker's SQLite store (`scripts/dev-up.sh`
sets `~/.fiboki/state.db`, the same file `fiboki worker run` writes). The API opens it
read-only (`mode=ro`) and ages the newest `beat_at` in the `worker_heartbeat` table against its
own clock; `/api/system/workers` lists every worker row with its own age. The file's mtime is
**not** the heartbeat: the store runs in WAL mode, so a beat lands in `state.db-wal` and the main
file can look hours old while the worker is alive. That was the false-DOWN. States: `absent`
(no path, no file, no table, no rows, or unreadable; age `null`, and the detail names which),
`stale` (age at or above `FIBOKI_WORKER_STALE_SECONDS`, default 120) and `ok`. Only a path that
is not a SQLite file at all falls back to its mtime, and that row is labelled `mtime_fallback`.

**Paper journal.** `FIBOKI_PAPER_ROOT` (default `<FIBOKI_STATE_DIR>/paper`; `dev-up.sh` sets
`var/paper`) holds one directory per session in the format `scripts/run_paper_session.py`
writes: `summary.json`, `trades.csv`, `positions.csv`. To publish a session to the
workstation, run it with `--out var/paper/<name>` (or copy those three files there). The API
reads them read-only, re-reads when a file changes, and serves trades, open positions, the
account, exposure and drawdown from them with provenance `paper`, the session's own currency,
and an `as_of` equal to the last replayed bar. Several sessions are separate accounts; the
portfolio sums them and says so in a caveat. A session whose summary does not state a
provenance, or whose rows disagree with it, is refused and listed, never assumed PAPER.

**No journal.** Only when the paper root holds no session does the API serve the
`api/seed.py` fixture. Then every trading envelope carries `source.kind = "seed"` and a
`seed_fixture` caveat, no seed row carries an executed provenance (paper, shadow, broker), the
account, drawdown and daily-loss figures are `null` rather than a made-up balance, and health
is **DEGRADED rather than OK**. A journal that exists but none of whose sessions parse is
`absent`, not seed: nothing is served.

### Live stream, incidents and the attention queue

- `GET /api/stream` is the workstation's live feed (Server-Sent Events, session cookie
  required). It only reads; it never starts a worker. Behind a proxy, keep response buffering off
  (the API sends `X-Accel-Buffering: no`). To check it by hand while signed in:
  `curl -N -b cookies.txt https://<api>/api/stream?topics=mode,health`.
- Set `FIBOKI_ALERT_LOG` (for example `~/.fiboki/alerts.jsonl`) for both the worker and the API.
  Without it, alerts are not persisted and `/api/system/incidents` lists only kill-switch
  incidents, with a caveat saying so. Acknowledgements and notes (admin only for now) go to
  `<FIBOKI_STATE_DIR>/incident_annotations.jsonl` and the operator audit trail; back that file up
  with the rest of the state directory.
- `GET /api/command/attention` is the Command screen's ranked queue. Its order is the ranking.
- `scripts/gen-openapi.sh` writes `apps/web/openapi.json` from the code (no server needed) for
  frontend type generation; the running API serves the same schema at `/api/openapi.json`.

## 5. The kill switch

```bash
fiboki killswitch status
fiboki killswitch pause   --reason "spreads blowing out on the ECB release" --operator joe
fiboki killswitch flatten --reason "unexplained divergence in reconciliation" --operator joe
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
not a kill switch.*

**Which journal.** There is one: `<FIBOKI_STATE_DIR>/killswitch.jsonl`, resolved by
`fiboki.core.paths.resolve_paths` in the CLI, the API settings and any gateway built with
`KillSwitch.from_paths(...)`. The CLI prints the absolute path it wrote and warns when
`FIBOKI_STATE_DIR` is unset (the default `var` is relative to the current directory, so a
terminal elsewhere resolves a different file) or when `--journal` names a file nothing trades
from. Every gateway re-reads the journal (a `stat`, then a replay only if it changed) before each
decision, so an activation binds a running process's very next order without a restart. A
`RiskGateway` declared for any mode but BACKTEST refuses an in-memory journal at construction.
In the current working tree the paper replay runtime (`workers/runtime.build_replay_session`)
builds its gateway with `KillSwitch.from_paths(...)` and `mode=PAPER`, and the forward paper
entrypoint opens the same resolved journal; every attempt row stamps `kill_switch_journal`, so a
composition that slips back to memory is visible.

The journal is appended with `F_FULLFSYNC` on macOS (plain `fsync` elsewhere), CRC-framed, and a
line torn by a crash is moved to `killswitch.jsonl.torn-<timestamp>` with a CRITICAL
`ledger_torn_tail` alert rather than making the switch unreadable. The intent ledger, the agent
audit ledger (flush only; its hash chain is its integrity check) and the alert log use the same
helper (`fiboki.core.durable`). Unframed lines written before this change are still read.

**Alerts and the watchdog.** `fiboki alerts test [--critical]` sends a test alert through every
configured channel and exits 1 if any failed or if no remote channel (Telegram, webhook) is
configured. CRITICAL alerts to a remote channel go through `<FIBOKI_STATE_DIR>/alerts_outbox.sqlite`
and are retried until delivered. `fiboki watchdog run` is the heartbeat watchdog as a supervised
process (lease `watchdog`, its own heartbeat, exit 75 if another copy holds the lease): run it
under launchd beside the workers, with `FIBOKI_EXPECTED_WORKERS` set, so a worker that never
started is also reported. Staleness thresholds come from one value, `Settings.health`
(`FIBOKI_WORKER_STALE_SECONDS`, `FIBOKI_WORKER_DOWN_SECONDS`, `FIBOKI_DATA_STALE_SECONDS`).

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

**Economic calendar.** Campaign cells and paper replays run against the committed official
calendar by default, and a cell or replay it does not cover (before 2024-01-01, after its
declared end, or a currency other than USD, EUR, GBP, JPY) is **refused**. The opt-outs are
explicit and recorded: `CampaignSpec.allow_empty_calendar=True` or
`run_paper_session.py --allow-empty-calendar` lifts the refusal and still applies the calendar
where it has events; `run_paper_session.py --no-calendar` (or an explicitly empty calendar with
the flag) runs with no event source. `summary.json`'s `economic_calendar.session` says what the
gateway was actually given.

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

**`scripts/backup.sh` and `scripts/restore.sh` exist as of 2026-09-29** (runbook: §13.2). They
have been exercised against temporary directories in `tests/unit/test_desktop_scripts.py`
(backup, checksum verification, refusal over a newer `var/`, forced restore, restore onto an
empty machine, a damaged archive), not yet against a real deployment. The procedure below is
what they automate, and still applies when the scripts cannot be used.

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

- **No live worker can be started.** The CLI refuses it by design. The one market-facing worker
  that can run is PAPER forward (§13.6), which reads OANDA practice prices and writes to no
  broker.
- **Reconciliation is scheduled only inside a running worker.** Paper forward reconciles at
  startup (fail closed) and every 15 minutes; `fiboki broker reconcile` is otherwise manual.
- **Backup and restore are scripted but not rehearsed on a real deployment** (§13.2). Do the
  rehearsal on the desktop before relying on it.
- **No market data in this repository.** `data/` is empty and the migration has never been run
  against the real 7.2 GB V1 store here.
- **No broker credentials.** The OANDA adapter is proved against recorded fixtures and has never
  spoken to a venue.
- **No strategy has been validated end to end** against real data, so no candidate exists and no
  holdout has been consumed.
- **No lifecycle transitions and no demotion monitors.** See `STRATEGY_STANDARD.md` §9.

## 13. Desktop runbooks

Added 2026-09-29 for the Mac desktop deployment (`DEPLOYMENT.md` §2). What was run to back each
statement is in `BUILD_LOG.md` under the same date; anything marked *not exercised* was not.

### 13.1 Install (a fresh desktop)

1. Homebrew, then `brew install python@3.11 node git sqlite llama.cpp`.
2. `git clone` the repository (do not copy a tree without `.git/`), check out the branch.
3. `scripts/desktop-install.sh --check`, read it, then `scripts/desktop-install.sh`. It is
   idempotent; re-run it until it prints no MISSING line.
4. Edit `~/.fiboki/env` (created mode 600 with a fresh `FIBOKI_SESSION_SECRET`): add
   `FIBOKI_OPERATORS`, computing each hash without leaving the password in shell history:
   `read -rs PW && printf '%s' "$PW" | shasum -a 256 && unset PW`.
5. Copy the market-data store (`DEPLOYMENT.md` §2.1) and restore the state archive (§13.2).
6. `.venv/bin/fiboki doctor`. Fix every FAIL in the order printed.
7. `scripts/launchd-install.sh` (writes, does not load), review the five files in
   `~/Library/LaunchAgents/uk.fiboki.*.plist`, then `scripts/launchd-install.sh --load`.
8. `.venv/bin/fiboki doctor` again: `launchd` should read `running` for all five, the ports
   should be "owned by Fiboki", the worker heartbeat should be seconds old.

*Not exercised:* steps 1, 7 and 8 need macOS; the scripts' macOS-only branches (launchctl,
plutil, Homebrew paths) were not run.

### 13.2 Backup and restore

```bash
scripts/launchd-install.sh --unload                 # quiesce writers (recommended)
scripts/backup.sh [--dest /Volumes/Backup] [--include-datastore]
scripts/launchd-install.sh --load
```

The archive `fiboki-backup-<UTC>.tar.gz` holds `var/` (without `var/datastore` and `var/logs`
unless `--include-datastore`), any ledger or paper root that lives outside `var/`, and
`~/.fiboki` **without** `env`. SQLite databases are copied with SQLite's online backup API;
`SHA256SUMS` lists every file; `MANIFEST.json` records time, host, commit and what was
excluded; `<archive>.sha256` sits beside it. Keep `~/.fiboki/env` in a password manager.

```bash
scripts/launchd-install.sh --unload
scripts/restore.sh ARCHIVE --dry-run                # verify only
scripts/restore.sh ARCHIVE                          # refuses over a newer var/ ...
scripts/restore.sh ARCHIVE --force                  # ... unless you mean it
.venv/bin/fiboki doctor && scripts/launchd-install.sh --load
```

Restore refuses a damaged archive, an unsafe member, a file that fails its checksum, a `var/`
holding anything modified after the backup (without `--force`), and a running API or worker.
It deletes nothing: the old `var/` becomes `var.pre-restore-<UTC>`, replaced `~/.fiboki` files
move to `~/.fiboki/pre-restore-<UTC>/`, and a datastore the archive does not carry is moved
across from the old `var/`. Then run the three verifications in §9 (both hash chains and a
dataset checksum); a restore that passes them restored the record, not just the files.

**Rehearse it** on the desktop once: back up, restore into a scratch clone, run `fiboki doctor`
and the §9 checks. Until then the restore is tested only against temporary directories.

### 13.3 Upgrade

The services run from the runtime checkout (`~/fiboki`; `DEPLOYMENT.md` §2.6 says why it is not
under `~/Documents`). Changes are pushed from the development checkout and pulled here:

```bash
cd ~/fiboki
scripts/backup.sh                                   # always, before an upgrade
git status                                          # nothing uncommitted (this checkout is never edited)
git pull --ff-only origin v2/integration
scripts/desktop-install.sh                          # if pins, node_modules or the web build changed
scripts/launchd-install.sh --services api,worker,web,news --load   # boots out, waits, bootstraps
.venv/bin/fiboki doctor
```

`launchctl kickstart -k` restarts a service in place without rewriting its plist; use the
install script when the templates or the repository path changed.

If the upgrade changed `pyproject.toml` pins, run the golden tests
(`.venv/bin/python -m pytest -m golden -q`) before trusting any new number, and read the
BUILD_LOG entry for whether stored results were invalidated. `uk.fiboki.llama` does not need a
restart for a Fiboki upgrade; restart it only to change the model or llama.cpp itself.

### 13.4 llama.cpp

**Install and start.**

```bash
brew install llama.cpp                   # build >= b6325; scripts/llama-server.sh checks
scripts/llama-server.sh --print          # which tier, model file and command; starts nothing
```

The script never downloads. If the model file is missing it prints the `hf download` and `curl`
commands for the exact file, and where to read its published SHA-256. Download, then compare
`shasum -a 256 <file>` with the SHA-256 on the file's Hugging Face page: it is the same digest
Fiboki records for every model call, so this check ties the audit trail to the published weights.
Then `launchctl kickstart gui/$(id -u)/uk.fiboki.llama` (or run the script in a terminal). To
pin a different tier or file, put `LLAMA_SERVER_ARGS=--tier 64` or
`LLAMA_SERVER_ARGS=--model /Users/you/Models/x.gguf` in `~/.fiboki/env`.

**Which server features Fiboki relies on, and since which build.** Read from the llama.cpp git
history (first `b` tag containing each change), not recalled:

| Feature | Build | Source |
|---|---|---|
| `response_format: {"type": "json_object", "schema": ...}` on `/v1/chat/completions` (server compiles the schema to GBNF) | b2487 | PR #5978 |
| `response_format: {"type": "json_schema", "json_schema": {"schema": ...}}` accepted | b3782 | PR #9527 |
| ...and actually honoured: builds in between could accept it and ignore it (issues #10732, #11988) | b4820 | PR #12168 |
| `--reasoning-budget 0` disables thinking | b5488 | PR #13771 |
| `-fa on\|off\|auto` | b6325 | PR #15434 |

The provider reads `build_info` from `/props` and sends the `json_schema` form only from b4820;
below that, or when the server does not report its build, it sends the `json_object` + `schema`
form, which every build since b2487 honours and current master still accepts. If a server rejects
the `json_schema` form outright (an HTTP error naming `response_format`, so nothing was
generated), the provider falls back to the `json_object` form once and stays there. There is no
client-side JSON-Schema-to-GBNF converter: the fallback uses the server's own converter.

**Check it.**

```bash
.venv/bin/fiboki doctor model            # server, model id, n_ctx, GGUF sha256 (cached after the first run)
.venv/bin/python -c "
import json
from fiboki.agents.providers import LocalHTTPProvider, ollama_http_client, smoke_test_provider
p = LocalHTTPProvider.for_llama_cpp('http://127.0.0.1:8080', client=ollama_http_client())
print(json.dumps(smoke_test_provider(p).as_dict(), indent=2))
"
```

Expect `"ok": true` and a `model_digest` equal to `shasum -a 256` of the file. The first
fingerprint hashes the whole GGUF file; `fiboki doctor` caches the digest in
`~/.fiboki/gguf-digests.json`, keyed by path, size and modification time.

**Point the research worker at it.** In `~/.fiboki/env`:

```
FIBOKI_AGENT_PROVIDER=local
FIBOKI_AGENT_LOCAL_URL=http://127.0.0.1:8080
FIBOKI_AGENT_LOCAL_MODEL=<the --alias scripts/llama-server.sh prints>
FIBOKI_AGENT_CYCLES=true
FIBOKI_AGENT_CYCLE_TARGET=<strategy_id:INSTRUMENT:TIMEFRAME>
```

**Required code change, not yet made.** `fiboki.workers.research_runtime._build_provider` still
builds `LocalHTTPProvider.for_ollama(...)`, which speaks Ollama's `/api/chat` and `/api/show`;
llama-server serves neither, so every model step would fail (recorded as failed, not silently).
The change is one line:

```python
    return LocalHTTPProvider.for_local_server(
        settings.local_model, client=ollama_http_client(), base_url=settings.local_url
    )
```

`for_local_server` asks the server (`GET /props`) whether it is llama.cpp and otherwise builds
exactly the Ollama provider as before. Two consequences to accept with it: the provider now
contacts the server when the worker starts, so a model server that is down at start stops the
worker (launchd retries it) instead of failing each step later; and without a
`digest_cache_path` argument the worker re-hashes the GGUF file once per process start.
`fiboki doctor` reports FAIL on the local-model row while `FIBOKI_AGENT_PROVIDER=local` points at
llama.cpp and this line is unchanged.

**Change the model.** Stop `uk.fiboki.llama`, change `LLAMA_SERVER_ARGS` and
`FIBOKI_AGENT_LOCAL_MODEL`, start it, run `fiboki doctor model`, then restart
`uk.fiboki.worker`. A running provider refuses to generate if `/props` reports a different
weights path or context from the one it pinned, so a model swapped underneath a running worker
fails loudly rather than being recorded under the old digest. Every audit record carries the
digest, so runs before and after the change stay distinguishable.

**Known approximations.** The prompt-size refusal uses the provider's 4-characters-per-token
estimate, not the model's tokenizer. Fiboki decodes at temperature 0 with a fixed seed and one
server slot for reproducibility; the Qwen3 model card recommends against greedy decoding in
thinking mode and suggests sampling settings for non-thinking mode, so output quality at
temperature 0 is a property to measure with the eval harness, not assume.

### 13.5 Known issues found during the desktop work

- `apps/web/next.config.ts` builds its Content-Security-Policy from
  `new URL(process.env.NEXT_PUBLIC_FIBOKI_API ?? "http://127.0.0.1:8000")`. `scripts/dev-up.sh`
  (and `scripts/fiboki-service.sh web`, which mirrors it) set `NEXT_PUBLIC_FIBOKI_API=""` for the
  same-origin proxy; `"" ?? x` is `""`, and `new URL("")` throws. Checked with Node; `next build`
  itself was not run here. Expect the web build or first request to fail until the config treats
  an empty value as same-origin. `scripts/desktop-install.sh` reports a failed build as MISSING
  rather than hiding it.
- `scripts/dev-up.sh` exports `FIBOKI_INCIDENT_LOG` and `FIBOKI_API_PROXY_TARGET`, which are not
  in `ENV_REGISTRY`; `fiboki doctor` lists them as unknown (WARN in paper, a startup error in
  demo/live).

### 13.6 Paper forward (OANDA practice prices, paper fills)

Added 2026-09-29 (audit F, P1-15, P2-4, P2-17, P2-18). What this runs, in one line: the Donchian
seed as written, on EURUSD, GBPUSD and XAUUSD H4, priced from OANDA **practice** candles and
quotes, filled by a **paper** venue, sized once, gated by the risk gateway, with every attempt
stamped with the wiring file's sha256. Nothing is sent to any broker; OANDA is only read.

**Once.** Create an OANDA practice (fxTrade Practice) account and a personal access token for
it. Then, in `~/.fiboki/env` (chmod 600; never in the repository):

```bash
FIBOKI_OANDA_PRACTICE_TOKEN=<the practice token>
FIBOKI_OANDA_PRACTICE_ACCOUNT_ID=<101-004-XXXXXXX-001>
```

These two names are read once at start and never logged. A live (fxTrade) token cannot be
used by mistake: the market-data host is asserted, by parsed hostname, to be
`api-fxpractice.oanda.com`, and the HTTP transport is built with that host as its only allowed
host.

**Check, then run once in the foreground:**

```bash
.venv/bin/fiboki paper forward --wiring src/fiboki/entrypoints/wiring/paper_forward_v1.json --check
scripts/fiboki-service.sh paper          # foreground; Ctrl-C stops it cleanly
```

`--check` validates the wiring and prints its sha256 without touching the network. The first
start refuses (exit 1, reason printed) if a token is missing, the strategy document on disk no
longer hashes to the wired `content_hash`, or the official economic calendar does not cover the
next 14 days (it is declared to 2026-12-04; refresh it before then).

**Supervised:**

```bash
scripts/launchd-install.sh --services paper --load
launchctl print gui/$(id -u)/uk.fiboki.paper        # state
tail -f var/logs/paper.log                          # structured log
.venv/bin/fiboki worker status                      # heartbeat + lease "paper-forward"
```

The service is not in the installer's default list on purpose: it starts only when asked.
`scripts/launchd-install.sh --services paper --unload` stops it.

**Where things land.** `<FIBOKI_STATE_DIR>/paper_forward/paper_forward_v1/`: `journal.jsonl`
(every bar, pricing sample, venue fill, book entry, venue instruction, closed trade, restart),
`trades.jsonl` (the P&L ledger the daily and weekly loss limits read, across restarts),
`attempts.jsonl` (every gateway attempt, allowed or blocked, with `wiring_sha256`) and
`intents.jsonl` (the fsynced order intents). `<FIBOKI_PAPER_ROOT>/forward-paper_forward_v1/`
holds `summary.json`, `trades.csv` and `positions.csv`, rewritten after every bar, which the
trading pages read as one continuous PAPER account.

**Kill switch.** Use the journal the process watches, `<FIBOKI_STATE_DIR>/killswitch.jsonl`
(the API's). `fiboki killswitch pause` blocks new entries from the next decision; existing
positions keep being managed. `fiboki killswitch flatten` closes every paper position within
about 30 seconds (it does not wait for the next bar), through the execution service, at the
last closed bar's price. The CLI writes the resolved journal, `<FIBOKI_STATE_DIR>/killswitch.jsonl`,
so run it with the same `FIBOKI_STATE_DIR` the service uses (`scripts/fiboki-service.sh`
defaults it to `<repo>/var`); `fiboki killswitch status` shows which file it read.

**Sleep (P2-18).** The plist is `ProcessType Standard`, and the service wrapper holds
`caffeinate -is -w <pid>` for the life of the process: `-i` stops idle sleep, `-s` stops system
sleep **on AC power only**. Neither stops a closed MacBook lid from sleeping the machine (unless
it is in clamshell mode with power and an external display), and a sleeping Mac polls nothing:
the feed wakes late, the bar is delivered late, and the gateway's freshness check refuses stale
bars rather than trading them. Check with `pmset -g assertions | grep caffeinate` and
`pmset -g` (look at `sleep` and `displaysleep`). On a desktop Mac set "Prevent automatic
sleeping when the display is off" in System Settings, Energy.

**Restarts (read before trusting a number).** The book and the paper venue live in memory. A
restart carries forward the realised balance, the peak equity (so the drawdown limit does not
reset) and every closed trade (so the loss windows do not reset). Positions open at the last
bar are **abandoned**: journalled as `positions_abandoned_at_restart`, alerted at WARNING, and
their unrealised P&L is not carried. Keep restarts rare; each one is visible in `summary.json`
(`restarts`, `abandoned_at_restart`).

**What the numbers are.** The ledger of record is the position book, which fills entries at the
next bar's open through the fill simulator exactly as a backtest does, and charges the
`OANDA_REALISTIC` profile's spreads. The paper venue's instant fill at the signal bar's close is
recorded beside it (`venue_fill` rows) so the gap is measured. The live OANDA quoted spread is
used by the gateway's `abnormal_spread` check (and journalled per bar), not charged. FX is not
converted (all three instruments are USD-quoted, the account is USD). The regime is `unknown`
(no market-state engine is wired), which the sizing policy treats as a reason to size down.

