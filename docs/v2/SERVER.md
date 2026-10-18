# Running Fiboki on a Linux server

Written 18 October 2026. This is the runbook for moving Fiboki from the MacBook to a rented Linux
VPS so it runs 24/7 without the laptop. Everything here is **paper only**: the units run
`scripts/fiboki-service.sh`, which forces `FIBOKI_EXECUTION_MODE=paper` and unsets every live control
after reading the env file, exactly as on the Mac. Nothing in this runbook enables live trading, and
nothing opens a public port.

What was exercised when this was written, and what was not, is in section 12. Read that before you
trust a step.

## 1. The shape of it

```
 you (browser, phone, laptop)
   |  SSH tunnel, or Tailscale (https, tailnet only)
   v
 server: ufw denies everything incoming except SSH (and the tailnet interface, if you add Tailscale)
   |
   +-- fiboki-web.service    127.0.0.1:3000   Next.js, proxies /api to the API
   +-- fiboki-api.service    127.0.0.1:8000   FastAPI
   +-- fiboki-worker.service                  research worker (its own process, never a thread)
   +-- fiboki-news.service                    headline recorder
   +-- fiboki-quotes.service        opt-in    OANDA practice top of book (needs the practice token)
   +-- fiboki-paper.service         opt-in    FX paper forward (OANDA practice prices, paper fills)
   +-- fiboki-paper-crypto.service  opt-in    BTC and ETH paper forward (Coinbase public data, no credential)
   +-- fiboki-llama.service         optional  local model for the research agents (CPU)
   +-- fiboki-backup.timer          nightly   scripts/server-backup.sh, retention, off-box copy
   +-- fiboki-heartbeat.timer       5 min     scripts/server-heartbeat.sh, pushes to your phone
```

| Where | What |
|---|---|
| `/opt/fiboki` | the code (a git checkout of GitHub `main`), `.venv`, the web build. Owned by `fiboki`, read-only to the services |
| `/var/lib/fiboki/var` | `FIBOKI_STATE_DIR`: datastore, ledgers, paper journals and snapshots, quotes, news, killswitch, logs |
| `/var/lib/fiboki/.fiboki` | `FIBOKI_HOME`: worker leases and heartbeats (`state.db`), digest cache, heartbeat state |
| `/etc/fiboki/env` | **the secrets file**, mode 600, owned by `fiboki`. Never in git, never in a backup |
| `/var/backups/fiboki` | the nightly archives (`daily/`, `weekly/`) |

Services run as the unprivileged `fiboki` user (no login shell, no sudo). Each unit has
`NoNewPrivileges`, `ProtectSystem=strict` (the only writable path is `/var/lib/fiboki`, plus the web
build's cache), `ProtectHome`, `PrivateTmp`, `PrivateDevices`, and a `MemoryMax`.

`fiboki-ctl` (installed in `/usr/local/bin`) runs a command with the server's environment as the
service user: `fiboki-ctl fiboki doctor`, `fiboki-ctl fiboki worker status`. Use it instead of
activating a venv by hand; a plain `fiboki doctor` in your own shell would look at the wrong paths.

### Memory budget (16 GB)

| Unit | MemoryHigh | MemoryMax | Notes |
|---|---:|---:|---|
| `fiboki-paper`, `fiboki-paper-crypto` | 768 MB | 1 GB | killed last (`OOMScoreAdjust=-500`) |
| `fiboki-quotes`, `fiboki-news` | 320 MB | 384 MB | |
| `fiboki-api` | 1.2 GB | 1.5 GB | |
| `fiboki-web` | 768 MB | 1 GB | |
| `fiboki-worker` | 4 GB | 5 GB | `Nice=10`, idle IO, `CPUWeight=50`; killed early (`+300`) |
| `fiboki-llama` | 7 GB | 8 GB | `CPUQuota=400%`; killed first (`+500`) |
| `fiboki.slice` (all of the above) | 13 GB | **14 GB** | the group can never take more than this |

That leaves at least 2 GB for the kernel, sshd, tailscaled, journald and the page cache. The
installer also adds a 4 GB swap file (`vm.swappiness=10`) when the machine has none. If the model is
loaded and a sweep is running at the same time, the kernel will kill the model first, then the
worker; the paper sessions are the last thing to go. `tests/unit/test_server_scripts.py` asserts
these relationships so a later edit cannot quietly break them.

## 2. Costs (estimates; check the price list when you order)

I could not check netcup's live price list from the build environment, so treat these as planning
numbers, not quotes.

| Item | Cost |
|---|---|
| netcup VPS 2000 G12 (8 shared vCPU x86_64, 16 GB RAM, 512 GB NVMe) | roughly 15 to 20 EUR per month before VAT, billed monthly; confirm on netcup.com |
| Tailscale | free tier (Personal) is enough for two operators and a few devices |
| healthchecks.io | free tier (20 checks) is enough |
| ntfy.sh | free; or a Telegram bot, also free |
| Off-box backup | a few GB of archives: Backblaze B2 or similar, well under 1 EUR per month; a second small VPS or a NAS over SFTP also works |
| Domain / TLS | none needed: Tailscale supplies the `*.ts.net` name and certificate |

A 16 GB machine is what the memory budget above is written for. An 8 GB machine can run everything
except the 9B model; set `--tier 8` for the model and expect to lower the worker's `MemoryMax`.

## 3. Order the server

1. Order a **VPS 2000 G12** at netcup.com. Choose **Debian 12** or **Ubuntu 24.04 LTS** as the
   image, and the region nearest you (Nuremberg or Vienna). The bars are H4 and the practice API is
   polled once a minute at most, so latency is irrelevant.
2. Add your SSH public key in the netcup server control panel (SCP), or after first login with
   `ssh-copy-id`. Do not rely on the emailed root password.
3. Log in as root once and make yourself an administrator account, then stop using root over SSH:

   ```bash
   adduser you && usermod -aG sudo you
   rsync -a ~/.ssh/ /home/you/.ssh/ && chown -R you:you /home/you/.ssh
   # In /etc/ssh/sshd_config.d/10-fiboki.conf:  PermitRootLogin no  /  PasswordAuthentication no
   systemctl reload ssh
   ```

   The installer does not touch `sshd_config`; that decision stays yours. The firewall it enables
   rate-limits SSH (`ufw limit`).
4. Check the machine: `uname -m` (x86_64), `free -h` (about 15 GB shown for "16 GB"),
   `df -h /` (about 500 GB).

## 4. Install

The installer needs the repository on the server. Pick one:

**A. The repository is reachable from the server** (public, or a read-only deploy key):

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/Stratton1/Fiboki_Trading.git /opt/fiboki      # main
# Private repository: add a READ-ONLY deploy key in GitHub, then
#   sudo scripts/server-install.sh --repo-url git@github.com:Stratton1/Fiboki_Trading.git --deploy-key /root/fiboki_deploy
```

**B. Copy the checkout from the Mac** (no credentials on the server at all):

```bash
rsync -a --exclude .venv --exclude node_modules --exclude var --exclude .next ~/fiboki/ you@server:/tmp/fiboki/
ssh you@server 'sudo mv /tmp/fiboki /opt/fiboki'
```

The installer then notes that it cannot fetch and uses the checkout as it is. Make sure the Mac's
runtime checkout is on the commit you want to run (`git -C ~/fiboki rev-parse --short HEAD`).

Then, on the server:

```bash
sudo /opt/fiboki/scripts/server-install.sh --dry-run          # read every line it would run
sudo /opt/fiboki/scripts/server-install.sh --no-start         # install and enable, start nothing yet
```

`--no-start` is right when you are about to migrate from the Mac: the state has to arrive before the
services run (section 6). On a brand-new setup with nothing to migrate, drop it and the default set
(api, worker, web, news) starts. It is idempotent; run it again after every `git pull` to rebuild
and refresh the units.

What it does, in order: apt packages (build tools, `sqlite3`, `rsync`, `ufw`, `unattended-upgrades`);
the `fiboki` user and directories; Python 3.11 (the system one on Debian 12, a pinned `uv`-managed
one on Ubuntu 24.04, which ships 3.12); Node.js 22 LTS from NodeSource's signed apt repository (no
pipe-to-shell); the checkout; `.venv` and `pip install -e '.[dev]' -c deploy/constraints.txt` (the
same pins as the Mac, then `scripts/lockfile.py verify --warn`); `npm ci` and `npm run build` for
the web app; `/etc/fiboki/env` (created once, mode 600, with a generated `FIBOKI_SESSION_SECRET`,
never printed); the units, slice and timers; the firewall, `unattended-upgrades` (security updates,
**no automatic reboot**), a 1 GB journald cap, logrotate for `var/logs`, and swap. Useful flags:
`--services api,worker,web,news,paper-crypto`, `--ssh-port N`, `--python PATH`, `--skip-web-build`,
`--no-firewall`, `--tailscale`, `--build-llama [TAG]`.

### The secrets file

```bash
sudo -e /etc/fiboki/env
```

Put in it (the template lists them, commented out): `FIBOKI_OPERATORS` (the scrypt hashes; copy the
line from your Mac's `~/.fiboki/env`, or generate with the command in the template),
`FIBOKI_OANDA_PRACTICE_TOKEN` and `FIBOKI_OANDA_PRACTICE_ACCOUNT_ID` (copy from the Mac; **practice
only**, the live host is refused by the code), `FIBOKI_TELEGRAM_BOT_TOKEN` and
`FIBOKI_TELEGRAM_CHAT_ID`, `FIBOKI_NTFY_URL`, `FIBOKI_HEALTHCHECK_URL`, the backup target, and any
agent settings. Rules: `KEY=VALUE` per line, no `export`, no shell expansion (a `$` is a literal
`$`, which is why the scrypt hashes work), mode 600. Keep a copy in your password manager: it is
deliberately excluded from backups. Do not paste the contents anywhere, including into a terminal
that is being logged; `fiboki doctor` reports names, never values.

Restart what you changed: `sudo systemctl restart fiboki-api fiboki-web fiboki-worker`.

## 5. Reach it privately

There is **no public port** to reach: the API and web app bind `127.0.0.1`, and `ufw` denies
everything incoming except SSH. Two ways in.

**SSH tunnel** (nothing to install):

```bash
ssh -L 3000:127.0.0.1:3000 you@server        # then open http://localhost:3000
```

**Tailscale, tailnet only** (a stable `https://<name>.<tailnet>.ts.net` for you and Tom, on the
phone too):

1. Make a Tailscale account (tailscale.com) and, in its admin console under DNS, enable **MagicDNS**
   and **HTTPS certificates**. Invite Tom to the tailnet, or share the machine with him.
2. On the server: `sudo /opt/fiboki/scripts/server-install.sh --tailscale --no-start` (idempotent, so
   it re-checks everything else). It installs Tailscale from the signed apt repository, then asks you
   to run `sudo tailscale up` once and open the URL it prints; re-run the installer and it runs

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:3000
   tailscale serve status
   ```

   and allows traffic on the `tailscale0` interface in `ufw`. (If you would rather authenticate with
   a pre-authorised key, `export TS_AUTHKEY=...` before running it; it is never echoed.)
3. Tell the app it is behind https: in `/etc/fiboki/env` set `FIBOKI_COOKIE_SECURE=true` and
   `FIBOKI_ALLOWED_ORIGINS=https://<name>.<tailnet>.ts.net`, then
   `sudo systemctl restart fiboki-api fiboki-web`.

Never use `tailscale funnel`; that publishes to the public internet. After this you can, if you
want, remove the SSH rule and use `tailscale ssh`, but that is your call and not done for you.

## 6. Migrate from the Mac

Prerequisites: the installer has run on the server with `--no-start`; your SSH key works
(`ssh you@server true`) and that account has passwordless `sudo` (or is root); `rsync` is on the
Mac (it ships with macOS); `/etc/fiboki/env` on the server already has the operator hashes and the
OANDA practice token; the Mac and the server are on the same commit
(`scripts/server-migrate.sh preflight` compares the paper wiring files and refuses if they differ,
because the server would then refuse the state snapshot).

Run from the Mac's runtime checkout (`~/fiboki`):

```bash
scripts/server-migrate.sh --host you@server preflight    # checks only
scripts/server-migrate.sh --host you@server prepare      # copy and restore; the Mac keeps running
scripts/server-migrate.sh --host you@server cutover      # the actual move
```

(`all` runs prepare, asks, then cutover; `--dry-run` prints every command without running any.)

**What `prepare` does.** `scripts/backup.sh --include-datastore` on the Mac (every SQLite file by
the online backup API; a SHA-256 manifest; the env file is never included), `rsync` of the archive
to the server, `scripts/restore.sh` there (it verifies the manifest before touching anything and
keeps the previous state directory as `var.pre-restore-<UTC>`), the Mac's `state.db` (leases and
heartbeats carrying the Mac's hostname) set aside, then only the API and the web app are started on
the server and `fiboki doctor` is run there. **Nothing on the Mac is stopped.** What is copied:
`var/` (datastore, experiment ledger, paper journals and their `state_snapshot.json`, quotes, news,
killswitch, agent audit, research store), `FIBOKI_PAPER_ROOT`, and `~/.fiboki` minus the env file.
What is not: the env file, `.venv`, `node_modules`, launchd plists.

**What `cutover` does, in order.** It re-checks everything and requires the server to be healthy
**before** the Mac loses anything. Then it stops the moved Mac services (paper sessions first, so
their shutdown writes the final snapshot; then worker, news, web, API), waits for each to exit,
and moves their plists to `~/Library/LaunchAgents/disabled-by-server-migrate/` so a reboot cannot
bring them back. It checks the paper snapshots again now that they are final, does a last `rsync`
of the whole state directory (everything is quiescent, so this is consistent), starts the services
on the server, waits 45 seconds, and checks that every unit is active, that no paper journal says
`state_snapshot_refused` or `positions_abandoned_at_restart`, and that `fiboki doctor` has no FAIL.
If any of that fails it **rolls back automatically** (unless `--no-auto-rollback`).

**The FX paper gate.** If a *running* Mac paper process holds an open position and there is no
state snapshot it can hand over (missing, failing its CRC, or, after the stop, written before the
ledgers last grew), the script refuses and says why. The reason: a start without a restorable
snapshot journals `positions_abandoned_at_restart` and drops the unrealised P&L
(`src/fiboki/entrypoints/paper_state.py`), and the server's first start would be that start. As of
17 October the running FX paper process predates the snapshot code and holds a GBPUSD long
(`USER_ACTIONS.md`, status line); that is exactly the case that is refused. Your options: wait for
it to close; or restart the Mac FX service once on the current build so it writes
`state_snapshot.json` (which is itself an abandon of that position, so only after it is flat); or
accept the loss with `--accept-abandoned-positions`. Crypto and FX are judged separately; a flat
session or one with a verifying snapshot moves freely. You can move everything else first
(`--services api,worker,web,news,paper-crypto,quotes`) and leave FX on the Mac until it is flat.

**Verify after cutover** (section 8), keep the Mac's plists and its `var/` for a few days, and only
then clean up.

### Rollback

```bash
scripts/server-migrate.sh --host you@server rollback              # stop the server's, restart the Mac's
scripts/server-migrate.sh --host you@server rollback --pull-back  # first bring the server's state home
```

It reads `~/.fiboki/server-migrate.last` (which services were moved) and stops them on the server
**before** the Mac's start, so two copies never hold the same lease; then it runs
`scripts/launchd-install.sh --services ... --load`, which regenerates the plists from the
templates. The Mac restarts from its own state as of the cutover. If the server has traded paper
for any time since, use `--pull-back`: it rsyncs the server's state home to the Mac with
`--backup --backup-dir`, so the Mac's older copies are kept next to it in
`var.pre-pullback-<UTC>`. After a rollback, the server's units stay installed but stopped; you can
run `prepare` again whenever you are ready.

## 7. Backups and the heartbeat

### Nightly backup

`fiboki-backup.timer` runs `scripts/server-backup.sh` at about 03:30 (randomised by up to 20
minutes; `Persistent=true`, so a missed run happens at boot). It runs `scripts/backup.sh`, keeps 14
daily archives in `/var/backups/fiboki/daily`, and on Sundays (UTC) also a 3-deep `weekly/` set
that **includes the datastore**. The services are not stopped: every SQLite database is a
consistent snapshot, and a JSONL ledger caught mid-append can end in a torn line that the framed
readers quarantine on read. Tune with `FIBOKI_BACKUP_KEEP`, `FIBOKI_BACKUP_KEEP_WEEKLY` and
`FIBOKI_BACKUP_DATASTORE=weekly|always|never` in the env file.

**Off-box is yours to configure**, and until you do, every run prints "no off-box target
configured; these archives share a disk with the data" (nothing else will remind you: the heartbeat
only proves that a local backup ran). Two
supported targets, set in `/etc/fiboki/env`:

```bash
FIBOKI_BACKUP_RCLONE_REMOTE=b2:fiboki-backups/server1      # then: sudo -u fiboki -H rclone config
FIBOKI_BACKUP_RESTIC_REPOSITORY=sftp:user@host:/srv/restic  # plus RESTIC_PASSWORD_FILE in the same file
```

rclone's config lives in `/var/lib/fiboki/.config/rclone/` (created by `rclone config` run as the
`fiboki` user). A failed off-box copy makes the unit fail (visible in `systemctl --failed` and in
the heartbeat) and is not recorded as a success. Test it once: `sudo systemctl start fiboki-backup`
then `journalctl -u fiboki-backup -n 30`, and **practise a restore** onto a scratch directory
(`scripts/restore.sh --dry-run ARCHIVE` verifies an archive without changing anything). The env
file is excluded from every archive; its copy is your password manager.

### The external heartbeat

`fiboki-heartbeat.timer` runs `scripts/server-heartbeat.sh` every five minutes, outside the Fiboki
memory slice so it still runs when that group is starved. It checks `fiboki doctor`, every worker
heartbeat (`fiboki worker status`: stale and down), the quote recorder's freshness (skipped while
the FX market is shut, Friday 22:30 to Sunday 22:30 UTC, because only changed quotes are written),
every enabled unit, and that a backup has succeeded in the last 36 hours. Then:

| Channel | Set in `/etc/fiboki/env` |
|---|---|
| Telegram (the channel `fiboki.obs.alerts` already uses) | `FIBOKI_TELEGRAM_BOT_TOKEN`, `FIBOKI_TELEGRAM_CHAT_ID` |
| ntfy | `FIBOKI_NTFY_URL` (e.g. `https://ntfy.sh/<long-random-topic>`), optional `FIBOKI_NTFY_TOKEN` |
| the alerts webhook | `FIBOKI_ALERT_WEBHOOK_URL` (body `{"text", "source", "severity"}`) |
| **dead-man switch** | `FIBOKI_HEALTHCHECK_URL` |

A new problem pushes immediately, an unchanged one repeats every 60 minutes
(`FIBOKI_HEARTBEAT_REPEAT_MINUTES`), and recovery pushes once. Tokens and URLs go to `curl` on
stdin, never argv. `FIBOKI_HEARTBEAT_IGNORE=name,name` silences a doctor check or a worker id you
expect to be down.

**The dead-man switch is the part that matters most.** Everything the server pushes is silent if
the server, its network, or its timer is dead. Create a check at healthchecks.io (period 5
minutes, grace 10), put its ping URL in `FIBOKI_HEALTHCHECK_URL`, and the service pings it on every
healthy run and `/fail` on a problem. When the pings stop, healthchecks.io alerts you from outside.
That is a one-line `curl`, and it is already wired.

Prove it works, the way D3 in `USER_ACTIONS.md` asks for the worker: `fiboki-ctl
/opt/fiboki/scripts/server-heartbeat.sh --test` sends a test through every configured channel, and
stopping the worker (`sudo systemctl stop fiboki-worker`) should produce a push within ten minutes.

## 8. Verify

```bash
fiboki-ctl fiboki doctor --no-hash          # 0 FAIL; the systemd row lists every unit
fiboki-ctl fiboki worker status             # research worker fresh; paper-forward leases held
fiboki-ctl fiboki quotes status             # if quotes are enabled
systemctl status 'fiboki-*' --no-pager
ss -ltn | grep -E ':(3000|8000|8080)\b'     # every line must start 127.0.0.1
sudo ufw status verbose                     # deny (incoming); SSH limited; nothing else
journalctl -u fiboki-paper -f               # live; JSON lines
```

For the paper sessions after a migration, look in `/var/lib/fiboki/var/paper_forward/<wiring>/journal.jsonl`
for `positions_restored_at_restart` (good) and make sure there is no `state_snapshot_refused` or
`positions_abandoned_at_restart`. `summary.json` under the paper root shows `restarts` incremented.

## 9. Operating it

**Update** (after a merge to `main`): `sudo -u fiboki git -C /opt/fiboki pull --ff-only`, then
`sudo /opt/fiboki/scripts/server-install.sh --no-start` (rebuilds the venv pins and the web app,
refreshes the units), then `sudo systemctl restart fiboki-api fiboki-web fiboki-worker`. A paper
service restart restores its positions from the snapshot (docs/v2/OPERATIONS.md §13.6), but restart
it only deliberately and when a new wiring is not involved (a changed wiring sha refuses the
snapshot, by design).

**Reboots.** Security updates install themselves; kernel updates do not reboot the machine. When
`/var/run/reboot-required` exists, pick a moment (not just before an H4 close) and `sudo reboot`.
Units are enabled, so everything returns, and paper positions are restored from the snapshots.

**Exit 75** from a worker or paper unit is a held lease or an operator-only reconciliation
divergence. systemd treats it as a clean stop (`SuccessExitStatus=75`) so it does not spin; the
heartbeat will tell you the unit is not active. Read `fiboki-ctl fiboki worker status`, resolve it,
then `sudo systemctl start`.

**Logs.** Everything goes to the journal (`journalctl -u fiboki-api -f | jq .`), capped at 1 GB.
`var/logs/*.log`, if anything writes there, rotates weekly.

**Disk.** `fiboki doctor` warns under 20 GiB free and fails under 5. The 512 GB disk holds the
datastore, 14 dailies and 3 weeklies comfortably; check `du -sh /var/lib/fiboki /var/backups/fiboki`
monthly.

## 10. The local model on the server (optional)

The agents and the headline classifier need a local model. On a 16 GB machine that already runs the
rest of Fiboki, the 16 GB tier fits (the unit caps the process at 8 GB). The script never
downloads weights; it prints the pinned download and the check.

| Machine RAM | Model (Q4_K_M) | Source (pinned to a commit) | SHA-256 |
|---|---|---|---|
| 8 GB | Qwen3.5-4B, 2,740,937,888 bytes | `unsloth/Qwen3.5-4B-GGUF` @ `e87f176479d0855a907a41277aca2f8ee7a09523` | `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |
| 16 GB | Qwen3.5-9B | `unsloth/Qwen3.5-9B-GGUF` @ `3885219b6810b007914f3a7950a8d1b469d598a5` | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| 32 GB and up | Qwen3.6-27B | `unsloth/Qwen3.6-27B-GGUF` @ `82d411acf4a06cfb8d9b073a5211bf410bfc29bf` | `5ed60d0af4650a854b1755bd392f9aef4872643dc25a254bc68043fa638392a0` |

```bash
sudo /opt/fiboki/scripts/server-install.sh --build-llama --no-start   # CPU build of the latest llama.cpp release tag
sudo -u fiboki -H /opt/fiboki/scripts/llama-server.sh --print         # prints the exact pinned download for this machine
# run ONE of the printed download commands as the fiboki user, then the printed sha256sum check
sudo -u fiboki -H /opt/fiboki/scripts/llama-server.sh --verify --print
```

Then in `/etc/fiboki/env`: `FIBOKI_AGENT_PROVIDER=local`, `FIBOKI_AGENT_LOCAL_URL=http://127.0.0.1:8080`,
`FIBOKI_AGENT_LOCAL_MODEL=qwen3.5-9b-q4_k_m`, `LLAMA_SERVER_ARGS=--tier 16`, and enable the unit:
`sudo systemctl enable --now fiboki-llama`. The script reads the RAM as the kernel reports it, so a
"16 GB" VPS (about 15.6) selects the 16 GB tier. Qwen3.5 and 3.6 are newer architectures than the
`b6325` floor the script enforces; if `llama-server` rejects the file, you need a newer llama.cpp
build (re-run `--build-llama`, which takes the latest release tag). On CPU, the 9B model answers
slowly (a few tokens per second); it is for the research agents, not interactive use.

## 11. Disaster recovery

The server is rebuilt, not repaired: order a new one, run section 4 (`--no-start`), put
`/etc/fiboki/env` back from your password manager, copy the newest archive in from the off-box
target (`rclone copy b2:fiboki-backups/server1/weekly/ /tmp/restore/` or `restic restore`), verify
it (`sha256sum -c` on the `.sha256`, or `restore.sh --dry-run`), then
`sudo -u fiboki -H fiboki-ctl /opt/fiboki/scripts/restore.sh /path/to/archive` and start the units.
The Mac keeps working as a fallback only while its `var/` is kept; after you have a few good
off-box archives you can retire it.

## 12. What was and was not exercised, and known gaps

Exercised while writing this (on a Linux build host, with stubs for everything that would touch the
network): every unit renders and passes `systemd-analyze verify`; the installer's `--dry-run` and
`--render-units`; the memory-budget and hardening properties; the heartbeat (healthy, problem,
dedupe, recovery, weekend suppression, secret handling) against stub `curl`, `systemctl` and
`fiboki`; the backup script with the real `backup.sh`, retention, and a stub `rclone`; the
migration script's refusal logic and the order of its steps in `--dry-run`; `fiboki doctor` on a
Linux host (systemd units, `ss` instead of `lsof`, Linux hints); `scripts/llama-server.sh` tier
selection and pinned download text. The tests are `tests/unit/test_server_scripts.py`,
`test_cli_doctor.py`, `test_desktop_scripts.py`.

**Not exercised, because there was no server:** a real `apt`/`useradd`/`ufw`/`swapon` run, the
NodeSource and Tailscale repository steps, `uv python install`, a real `npm run build` as the
`fiboki` user, a real SSH/rsync migration (the `ssh`, `rsync` and `launchctl` calls were verified as
commands, not run), the web unit under `ProtectSystem=strict` (if Next needs another writable path
the unit logs it; add it to `ReadWritePaths`), a llama.cpp build, and the live reachability of the
OANDA practice and Coinbase endpoints from a German address (expected to work; confirm with
`fiboki-ctl fiboki paper forward --wiring /opt/fiboki/src/fiboki/entrypoints/wiring/paper_forward_v1.json --check`).
Run the installer with `--dry-run` first, and expect to fix a small thing or two on the first real
run; the script is idempotent for exactly that reason.

Known gaps:

- **Time zone and clock.** The server should run on UTC (`timedatectl set-timezone UTC`) and keep NTP
  on (`timedatectl`); the code stores UTC, but the weekly backup and the FX-closed window in the
  heartbeat use the host's UTC clock.
- **One server, one provider.** There is no standby. A dead VPS means paper trading stops until you
  restore (section 11); the dead-man switch is how you find out.
- **Off-box backup is not configured for you** and the heartbeat's backup check only proves a local
  backup ran.
- **`fiboki-research-worker.service`** (the older single unit) is left in `deploy/systemd/` and is
  not installed; the new worker unit `Conflicts=` with it.
- **No automatic reboot** after kernel updates, on purpose.
- **Alert channels in `fiboki.obs.alerts`** (Telegram, webhook) are for in-process alerts; the
  heartbeat is a separate sender that shares the Telegram credentials. ntfy exists only in the
  heartbeat.
