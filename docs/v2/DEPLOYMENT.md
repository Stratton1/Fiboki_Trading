# Deployment

**Snapshot:** 2026-09-19T05:05Z. Artefacts: `deploy/`, `scripts/`, `.github/workflows/ci.yml`,
`pyproject.toml`. Verified in this session: `pytest tests/ -q` → **2683 passed, 2 skipped** in 287 s.

Fiboki V2 is **local-first**. The normal deployment is SQLite on the operator's disk, the
research worker under `launchd` on the Mac, and the API in a terminal when it is wanted.
Containers and Postgres are options, not the default.

---

## 1. The one rule

**A worker is a process. It is never a thread inside the API.**

V1 ran its worker as a daemon thread in the FastAPI process. When the thread died the API kept
serving and kept returning `{"status": "ok"}`, so every signal anybody looked at said the system
was healthy while no work was being done. Worse, `render.yaml` also defined a *separate* worker
service while the API started its own thread unless `FIBOKEI_WORKER_EXTERNAL=true` — a variable
absent from the web service — so two workers could run concurrently, place duplicate orders, and
overwrite each other's heartbeat row under a shared default `worker_id`.

Everything in `deploy/` follows from that one rule: separate units, separate exit codes,
separate restart policies, a single-writer lease, and a supervisor that can tell you the process
is gone.

## 2. Mac, the primary target

```bash
make -f deploy/Makefile setup            # venv, deps under constraints, lockfile, doctor
fiboki system doctor                     # fix whatever it prints, in order

cp deploy/launchd/com.fiboki.research-worker.plist ~/Library/LaunchAgents/
$EDITOR ~/Library/LaunchAgents/com.fiboki.research-worker.plist   # replace CHANGEME paths
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fiboki.research-worker.plist

fiboki worker status                     # heartbeat age and lease holder
```

Inspect and stop:

```bash
launchctl print  gui/$(id -u)/com.fiboki.research-worker
launchctl bootout gui/$(id -u)/com.fiboki.research-worker
```

Three decisions in that plist are worth knowing:

- It is a **LaunchAgent, not a LaunchDaemon**, so it runs as the operator with the operator's
  data directory and stops at logout. *A research worker that survives logout on a laptop is a
  research worker that drains the battery in a bag.*
- `KeepAlive` uses `SuccessfulExit=false`, so a crash is restarted and a clean exit is not.
  Exit 75 (another holder has the lease) is not a success, so it *would* be restarted —
  `ThrottleInterval` makes that a slow retry rather than a spin, and `fiboki worker status`
  tells you who holds the lease.
- `ExitTimeOut` must exceed `WorkerConfig.shutdown_grace_seconds` (default 30 s; the unit files
  allow 45 s). Otherwise the supervisor `SIGKILL`s a worker that is shutting down correctly —
  which is the ungraceful shutdown the grace period exists to avoid, now with a half-written job
  record behind it.

The worker runs at `nice 10` with `ProcessType Background` and `LowPriorityIO` so a 10,000-cell
sweep does not make the machine unusable, and `workers/scheduler.py` additionally backs off on
memory pressure and caps parallelism to one job on battery.

## 3. Linux server

```bash
sudo cp deploy/systemd/fiboki-research-worker.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fiboki-research-worker
journalctl -u fiboki-research-worker -f | jq .
```

The unit declares exit 75 as a **successful** exit status, so systemd stops rather than
restart-looping against a held lease.

## 4. Containers, optional

```bash
export POSTGRES_PASSWORD=...             # never committed
cd deploy && docker compose --profile local up -d
```

Profiles keep everything opt-in: bare `up` starts nothing, `--profile db` starts Postgres only,
`--profile local` starts Postgres, the API and the research worker.

**There is no live-worker service in the compose file, on purpose.** *A market-facing process
started by a compose file somebody copy-pasted is exactly the accident this project exists to
prevent.* The live worker is started explicitly, by a human, from the application entrypoint
that owns its risk wiring — and `fiboki worker run live` refuses outright, because a live worker
assembled from command-line flags is a live worker whose risk configuration nobody reviewed.

`FIBOKI_LIVE_EXECUTION_ENABLED` is **intentionally absent** from the compose environment, and CI
fails if it appears set to a truthy literal anywhere in committed config.

The `Dockerfile` is multi-stage, non-root, and **digest-pinned**: `python:3.11-slim` is a moving
tag, and the image you built the golden results against is not the image you get next month. The
pin makes the runtime a recorded fact in the same way the lockfile makes the Python environment
one. Refresh it deliberately with
`docker buildx imagetools inspect python:3.11-slim-bookworm | head`, and **re-run the golden
tests afterwards** — a base-image bump can move a transitive numerical library.

## 5. Environment

| Variable | Purpose |
|---|---|
| `FIBOKI_HOME` | Base directory (default `~/.fiboki`) |
| `FIBOKI_STATE_DB` | Lease and heartbeat database |
| `FIBOKI_STATE_DIR` | Mutable operator state (default `var/`). Never inside the source tree |
| `FIBOKI_DATA_ROOT` | Bar data store. Must contain a `.fiboki-data-root` marker |
| `FIBOKI_EXPERIMENT_DB` | Experiment ledger |
| `FIBOKI_EXECUTION_MODE` | `backtest`/`paper`/`shadow`/`demo`/`live`. Default `paper`. An unrecognised value is a **startup error** |
| `FIBOKI_ALLOWED_ORIGINS` | Exact origins allowed to make a mutating request |
| `FIBOKI_SESSION_SECRET` | HMAC key. Unset ⇒ per-process key, sessions die on restart, reported as a health warning |
| `FIBOKI_COOKIE_SECURE` / `_SAMESITE` / `_DOMAIN` / `_NAME` | Cookie hardening; secure defaults to true |
| `FIBOKI_SESSION_TTL` | Seconds, default 43200 |
| `FIBOKI_BUILD_SHA`, `FIBOKI_BUILD_TIME` | Build identity. Empty is reported as unknown, not hidden |
| `FIBOKI_WORKER_HEARTBEAT`, `FIBOKI_WORKER_STALE_SECONDS` | Heartbeat file and staleness threshold (120 s) |
| `FIBOKI_EXPECTED_WORKERS` | Comma-separated worker ids that *ought* to exist. **Without it the watchdog cannot alert on a worker that never started** |
| `FIBOKI_ALERT_LOG` | JSONL alert sink. **Set this, or alerts only reach a terminal nobody is watching** |
| `FIBOKI_ALERT_WEBHOOK_URL` | Optional webhook channel |
| `FIBOKI_TELEGRAM_BOT_TOKEN`, `FIBOKI_TELEGRAM_CHAT_ID` | Optional Telegram channel |
| `FIBOKI_VENUE_URL` | Venue the mode guard's parsed-hostname control checks |
| `FIBOKI_SLIPPAGE_MODEL`, `_SPREAD_MODEL`, `_FINANCING_MODEL`, `_FX_CONVERSION_MODEL` | The realism assumptions in force; feed the **computed** caveats attached to every performance figure |
| `FIBOKI_LIVE_RUNTIME_ARMED` | One of five live controls. Must equal an unguessable token |
| `FIBOKI_OANDA_LIVE_RUNTIME` | One of three OANDA host controls. Same |
| `FIBOKI_LIVE_EXECUTION_ENABLED` | **Never set in committed config.** CI fails on a truthy literal |

That last row is not stylistic. V1 shipped `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` inside a
committed `render.yaml` and it sat there for months; nothing reviewed it, nothing tested it, and
the only reason it did no harm is that the broker credentials happened to be absent.

## 6. Reproducibility of the environment

Three layers, because each catches something the others cannot.

**`pyproject.toml`** pins every **direct** dependency that can change a number to an exact
version — numpy 2.2.6, pandas 2.2.3, scipy 1.14.1, pyarrow 18.1.0, and the rest — plus the build
backend (`hatchling==1.27.0`), with Python constrained to `>=3.11,<3.13`.

**`deploy/constraints.txt`** pins the transitive ones `pyproject.toml` cannot express, each with
the incident that produced it written above the pin. The documented example: `typer 0.15.1`
calls `Parameter.make_metavar()` with the pre-8.2 signature, so with `click >= 8.2` every
`--help` in the CLI raises `TypeError`. That is a broken operator tool rather than a broken
number, but it breaks the moment a fresh `pip install` resolves a newer click — and it was found
exactly that way, on a clean environment.

**`deploy/requirements.lock`** records every installed distribution. `make lock-check`, CI and
`fiboki system doctor` all fail on drift. Drift in numpy, pandas, scipy or pyarrow is **always
an error, never a warning**: a result computed against a different numpy is not comparable with
one computed against the locked version.

Install with the constraints applied:

```bash
pip install -c deploy/constraints.txt -e ".[dev]"
python scripts/lockfile.py record --path deploy/requirements.lock   # or: make lock
```

V1 had none of this. `pandas>=2.0` and `numpy>=1.24` with no lockfile resolved to pandas 3.0.6
and numpy 2.4.6 on every machine tested — a major version beyond what the author had in mind —
and two of the six uncommitted working-tree changes were already hot-fixes for the resulting
breakage. **"Deterministic backtests" was not a property V1 had**, whatever its non-negotiables
said, because determinism held within one environment and not across environments.

## 7. Deploy gates

`.github/workflows/ci.yml`. The design point, stated in the file's own header: V1 deployed on
every push to the default branch, tests ran in a parallel job, and the deploy step did not
depend on them. A red suite and a deployed service were routine and nobody noticed, because the
deploy badge was green either way.

| Job | Gates on | Why it is separate |
|---|---|---|
| `lint` | `ruff check src tests scripts` | formatting drift is a warning, not a gate |
| `typecheck` | `mypy src/fiboki` | V1 declared mypy as a dev dependency and never ran it |
| `test` | the full offline suite, on Python **3.11 and 3.12**, `-m "not external"`, `--timeout=120`, **25-minute job ceiling** | V1's suite hung and was cancelled by humans who assumed flakiness; a suite that hangs must **fail** |
| `golden` | `pytest -m golden`, **separately required** | these are the hand-calculated financial tests, the ones most likely to be "temporarily" skipped to unblock a release, so they get their own red X rather than being one line of a 2,484-test summary nobody reads |
| `live-flags` | `scripts/check_live_flags.py` | scans every committed config for a truthy live-execution flag |
| `lockfile` | `scripts/lockfile.py verify` | fails on dependency drift, and prints the drift |
| `audit` | `pip-audit --strict` | V1 carried a known-vulnerable `ecdsa` in the JWT signing path |
| `gate` | **one** required status aggregating all of the above | branch protection points here, so a new job is automatically gating without anyone ticking a settings box |
| `deploy` | `needs: gate`, tag-or-dispatch only, bound to a protected `production` environment | **push does not deploy. Ever.** |

Four details in that workflow are worth reading as design rather than plumbing.

**The golden job refuses a silently empty run.** `pytest -m golden` exits 0 when the marker
matches nothing, so a suite that collected zero golden tests would show green forever. The job
counts collected tests and fails below one.

**The golden job refuses a skipped test.** A skipped golden test is a failure there, not an
amber.

**The live-flag scanner is self-tested against a planted flag.** The job writes V1's exact
line — *including the `FIBOKEI` misspelling, because the misspelling is what was actually in the
repository* — into a scratch directory and asserts a non-zero exit. A guard that cannot be shown
to catch the thing it guards against is a guard nobody should trust. The scanner matches on the
key fuzzily (tolerating the typo and any prefix) and on a **literal** value only, so
`value: ${LIVE_ENABLED}` is a reference rather than a flag.

**The gate requires `success`, not `!= failure`.** A cancelled or skipped gate is not a passed
one.

The `test` job also asserts afterwards that no test reached the network, by checking that no
provider cache was written.

## 8. Migrations

`alembic` is a pinned dependency and the health endpoint reads `alembic_version`, reporting a
`null` revision as a **degradation** rather than as fine. `AlertEvent.MIGRATION_DRIFT` exists
for a running schema that is not the expected one.

V1 committed Alembic and copied it into the image, and **no start command ever ran
`alembic upgrade head`**; the schema came from `create_all` plus a hand-rolled
`_ensure_new_columns` shim covering four tables and missing four others. V2's intent is that
`alembic upgrade head` is a release step and no shim exists.

**At this snapshot there is no `alembic/` directory and no migration has been written.** The
health check reads a revision that nothing yet sets, which is why it reports unknown.

## 9. Pre-deploy checklist

```bash
make -f deploy/Makefile check      # lint, typecheck, live-flags, lock-check, golden, test
```

Then, before a tag:

1. `fiboki system doctor` clean on the target machine.
2. `FIBOKI_BUILD_SHA` set in the deploy environment — an unknown build is a degradation.
3. `FIBOKI_SESSION_SECRET` set — otherwise sessions die on every restart.
4. `FIBOKI_ALERT_LOG` and `FIBOKI_EXPECTED_WORKERS` set, or the alerting is decorative.
5. `FIBOKI_EXECUTION_MODE` explicitly stated, not inherited.
6. Exactly one worker holds the lease. `fiboki worker status`.
7. Supervisor `ExitTimeOut` / `TimeoutStopSec` above the worker's shutdown grace.
8. A backup taken and its hash chains verified (`OPERATIONS.md` §9) — noting that no restore has
   ever been rehearsed.

## 10. Gates to real money

These are not deploy gates and must not be compressed into them. Each answers a different
question. They are carried forward from the V1 audit and restated here because the deployment
apparatus is what makes them checkable.

**Gate A — research to paper.** Every gate in `GATE_SET_V2` passed: DSR > 0.95 against a
clustered effective N, PBO < 0.20, SPA consistent p < 0.05 with StepM membership, walk-forward
efficiency ≥ 50% and profitable in ≥ 60% of OOS windows, ≥ 400 trades, edge surviving 2× spread,
and a plateau ratio ≤ 1.25. Plus an automated config check asserting live execution is disabled.

**Gate B — paper to broker demo.** At least 30 days of continuous paper running with no
unexplained heartbeat gaps. **A deliberate worker kill in staging must produce both an error
report and an alert within one poll interval — until a chaos test proves the alert fires, demo
promotion is not safe**, because an unnoticed dead worker is indistinguishable from a flat
strategy. Reconciliation clean for a full week on the broker-reference key. The adapter tested
against recorded real payloads, not only fixtures.

**Gate C — demo to small live.** At least 3 months and 100 live-equivalent trades. Paper Sharpe
inside the 90% block-bootstrap confidence interval of the backtest Sharpe. Realised spread and
slippage within 1.5× of modelled. Zero unreconciled fills. Every documented approximation either
closed or explicitly accepted in writing. A kill-switch drill executed and timed. Start at the
minimum size the broker permits.

**Gate D — scale.** Only after the minimum track record length for the *observed live* Sharpe,
benchmarked against half the backtested Sharpe, has elapsed. At Gaussian moments and 95%
confidence that is roughly 17 years to separate a Sharpe of 1.0 from 0.5 (computed in this
session; see `VALIDATION_STANDARD.md` §6). Scale in fractions, never in steps.

And three stopping rules, pre-registered in code before any live capital: halt when the
probabilistic Sharpe against half the backtested Sharpe falls below 0.50; halt at the 95th
percentile of the bootstrap max-drawdown distribution rather than a round number; and run a
CUSUM on excess return calibrated to a roughly two-year in-control run length, to catch slow
decay a drawdown limit would miss. Every halt automatic, logged to an append-only ledger, and
requiring an explicit operator action to reverse. **None of these three is implemented.**

## 11. Gaps at this snapshot

- **No Alembic migrations exist**, so the revision the health check reads is always unknown.
- **The Dockerfile's base digest is a placeholder** and must be replaced with the digest the
  registry actually reports before the image is built.
- **The deploy job's final step is a placeholder** that echoes what it would do. The gating
  around it is real; the deployment itself is not wired.
- **No backup or restore tooling**, and no rehearsed restore.
- **No `alembic upgrade head` release step**, because there is nothing to upgrade to.
- **No chaos test** proving a killed worker produces an alert, which Gate B requires.
- **The compose stack has never been brought up** in this environment.
