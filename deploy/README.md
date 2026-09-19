# Deploying Fiboki V2

Fiboki V2 is **local-first**. The normal deployment is: SQLite on the
operator's disk, the research worker under `launchd` on the Mac, and the API in
a terminal when it is wanted. Containers and Postgres are options, not the
default.

## The one rule

**A worker is a process.** It is never a thread inside the API.

V1 ran its worker as a daemon thread in the FastAPI process. When the thread
died the API kept serving and kept returning `{"status": "ok"}`, so every
signal anybody had said the system was healthy while no work was being done.
Everything in this directory follows from that: separate units, separate exit
codes, separate restart policies, and a supervisor that can tell you the
process is gone.

## Mac (primary)

```bash
make -f deploy/Makefile setup          # venv, deps, lockfile, doctor
fiboki system doctor                   # fix whatever it prints, in order
cp deploy/launchd/com.fiboki.research-worker.plist ~/Library/LaunchAgents/
$EDITOR ~/Library/LaunchAgents/com.fiboki.research-worker.plist   # replace CHANGEME
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fiboki.research-worker.plist
fiboki worker status                   # heartbeat age and lease holder
```

The worker runs at `nice 10` with `ProcessType Background` and `LowPriorityIO`
so a 10,000-cell sweep does not make the machine unusable. The in-process
scheduler (`fiboki.workers.scheduler`) additionally backs off on memory
pressure and caps parallelism to one job when the laptop is on battery.

## Linux server

```bash
sudo cp deploy/systemd/fiboki-research-worker.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fiboki-research-worker
journalctl -u fiboki-research-worker -f | jq .
```

## Containers (optional)

```bash
export POSTGRES_PASSWORD=...           # never committed
cd deploy && docker compose --profile local up -d
```

There is **no live-worker service** in the compose file. A market-facing
process started by a compose file somebody copy-pasted is the accident this
project exists to prevent.

## Exit codes

| Code | Meaning | What a supervisor should do |
|-----:|---------|-----------------------------|
| 0 | clean stop | nothing |
| 1 | fatal (resume failed, too many consecutive failures) | restart, with backoff |
| 75 | another holder has the lease | **do not restart-loop**; run `fiboki worker status` |

Both unit files encode this: launchd throttles to 60s, systemd treats 75 as a
successful exit status so it stops rather than spinning.

## Shutdown timing

`WorkerConfig.shutdown_grace_seconds` defaults to 30s. Both unit files allow
45s (`ExitTimeOut`, `TimeoutStopSec`). **Keep the supervisor's timeout above
the worker's grace**, or the supervisor SIGKILLs a worker that is shutting down
correctly — which is the ungraceful shutdown the grace period was added to
avoid, now with a half-written job record behind it.

## Environment

| Variable | Purpose |
|---|---|
| `FIBOKI_HOME` | Base directory (default `~/.fiboki`) |
| `FIBOKI_STATE_DB` | Lease + heartbeat database |
| `FIBOKI_DATA_ROOT` | Bar data store |
| `FIBOKI_ALERT_LOG` | JSONL alert sink — set this, or alerts only reach a terminal nobody is watching |
| `FIBOKI_EXPECTED_WORKERS` | Comma-separated worker ids that *ought* to exist. Without it the watchdog cannot alert on a worker that never started |
| `FIBOKI_ALERT_WEBHOOK_URL` | Optional webhook channel |
| `FIBOKI_TELEGRAM_BOT_TOKEN`, `FIBOKI_TELEGRAM_CHAT_ID` | Optional Telegram channel |
| `FIBOKI_EXECUTION_MODE` | `paper` by default |
| `FIBOKI_LIVE_EXECUTION_ENABLED` | **Never set in committed config.** CI fails the build on a truthy literal |

That last row is not stylistic. V1 shipped
`FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` inside a committed `render.yaml` and
it stayed there for months. `scripts/check_live_flags.py` runs in CI as a
required job and scans every committed config file for it — including the
misspelling, because the misspelling is what was actually in the repository.

## Digest pinning

`deploy/Dockerfile` pins its base image by digest. Refresh deliberately:

```bash
docker buildx imagetools inspect python:3.11-slim-bookworm | head
```

and re-run `make -f deploy/Makefile test-golden` afterwards. A base-image bump
can move a transitive numerical library.

## Dependency lockfile

`deploy/requirements.lock` records every installed distribution. `make
lock-check` (and CI, and `fiboki system doctor`) fails on drift. Drift in
numpy / pandas / scipy / pyarrow is always an error, never a warning: a result
computed against a different numpy is not comparable with one computed against
the locked version.
