#!/usr/bin/env bash
# Fiboki V2: move Fiboki from this Mac to the server (scripts/server-install.sh has already run there).
# Run it ON THE MAC, from the runtime checkout. Needs ssh, rsync and, on the server, an account with
# passwordless sudo (or root).
#
#   scripts/server-migrate.sh --host you@server preflight   # checks only; changes nothing anywhere
#   scripts/server-migrate.sh --host you@server prepare     # copy a consistent backup, restore it there, prove the server healthy
#   scripts/server-migrate.sh --host you@server cutover     # stop the Mac services, final sync, start them on the server
#   scripts/server-migrate.sh --host you@server rollback    # stop the server's, restart the Mac's
#   scripts/server-migrate.sh --host you@server all         # prepare, then (after you confirm) cutover
#
# The order is the safety. The Mac keeps running everything until the server has been proven healthy:
#   preflight  local checks (below), ssh and sudo reachable, server installed and idle, disk space, and the
#              same paper wiring on both machines (a different wiring sha makes the server REFUSE the snapshot)
#   prepare    scripts/backup.sh --include-datastore here (SQLite online backups, SHA-256 manifest, no secrets),
#              rsync to the server, scripts/restore.sh there, the Mac's lease/heartbeat db set aside, then start only
#              the API and the web app on the server and run `fiboki doctor` there. Nothing on the Mac is stopped.
#   cutover    re-check the gates, only if the server is healthy: stop the Mac services being moved (launchd bootout,
#              wait for each to exit cleanly, plists moved aside so a reboot cannot resurrect them), final rsync of
#              the whole state directory (everything is quiescent now), start the services on the server, verify,
#              and ROLL BACK AUTOMATICALLY if a moved service is not running or a paper journal says the snapshot
#              was refused or positions abandoned.
#   rollback   stop the moved services on the server, restart the Mac's (scripts/launchd-install.sh --load).
#              --pull-back first rsyncs the server's state home (keeping the Mac's copies in a backup dir): use it
#              if the server has been trading paper for a while.
#
# THE FX PAPER GATE (pre-flight, and again after the Mac process stops): if a RUNNING Mac paper process holds an open
# position and there is no restorable state snapshot (scripts/migrate_preflight.py explains), the move REFUSES. Without
# the snapshot the server's first start would journal positions_abandoned_at_restart and lose their unrealised P&L.
# Wait for flat, or --accept-abandoned-positions if you choose to lose them.
#
# Options:
#   --host USER@HOST (required)   --identity FILE   --ssh-port N
#   --services LIST       moved services, default: whichever of api,worker,web,news,quotes,paper,paper-crypto run on this Mac
#   --remote-prefix DIR   default /opt/fiboki      --remote-lib DIR   default /var/lib/fiboki
#   --local-root DIR      default: this checkout   --stage DIR        default ~/FibokiMigrationStage
#   --no-datastore        leave the market-data store (re-download or copy it later)
#   --force-restore       let restore.sh overwrite a server state directory newer than the backup (fresh installs that ran)
#   --accept-abandoned-positions   see above       --pull-back   (rollback)    --no-auto-rollback
#   --dry-run             print what would run; remote and mutating steps are not executed
#   --yes                 do not ask before cutover
# Never prints a secret. ~/.fiboki/env is NOT copied (it is not in a backup): put its contents in /etc/fiboki/env
# on the server yourself (docs/v2/SERVER.md).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT_DEFAULT="$(pwd)"

HOST=""; IDENTITY=""; SSH_PORT=""; PHASE=""; SERVICES_GIVEN=""
SERVICES=""
RPREFIX="/opt/fiboki"; RLIB="/var/lib/fiboki"
LROOT="$ROOT_DEFAULT"
STAGE="$HOME/FibokiMigrationStage"
DATASTORE=1; FORCE_RESTORE=0; ACCEPT=0; PULL_BACK=0; AUTO_ROLLBACK=1; DRY=0; YES=0
ALL_MOVABLE="worker api web news quotes paper-crypto paper"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?}"; shift 2 ;;
    --identity) IDENTITY="${2:?}"; shift 2 ;;
    --ssh-port) SSH_PORT="${2:?}"; shift 2 ;;
    --services) SERVICES="${2:?}"; SERVICES_GIVEN=1; shift 2 ;;
    --remote-prefix) RPREFIX="${2:?}"; shift 2 ;;
    --remote-lib) RLIB="${2:?}"; shift 2 ;;
    --local-root) LROOT="${2:?}"; shift 2 ;;
    --stage) STAGE="${2:?}"; shift 2 ;;
    --no-datastore) DATASTORE=0; shift ;;
    --force-restore) FORCE_RESTORE=1; shift ;;
    --accept-abandoned-positions) ACCEPT=1; shift ;;
    --pull-back) PULL_BACK=1; shift ;;
    --no-auto-rollback) AUTO_ROLLBACK=0; shift ;;
    --dry-run) DRY=1; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    preflight|prepare|cutover|rollback|all) PHASE="$1"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$PHASE" ] || { sed -n '2,12p' "$0" >&2; exit 2; }
[ -n "$HOST" ] || { echo "--host USER@HOST is required" >&2; exit 2; }
case "$HOST$RPREFIX$RLIB" in *' '*|*"'"*|*'"'*|*';'*|*'$'*|*'`'*|*'&'*|*'|'*) echo "--host/--remote-* contain a character that is not allowed" >&2; exit 2 ;; esac

FHOME="${FIBOKI_HOME:-$HOME/.fiboki}"
LAST="$FHOME/server-migrate.last"
PY="$LROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
SSH_ARGS=(-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ControlMaster=auto
          -o ControlPath="/tmp/fiboki-migrate-%C" -o ControlPersist=120)
[ -n "$IDENTITY" ] && SSH_ARGS+=(-i "$IDENTITY" -o IdentitiesOnly=yes)
[ -n "$SSH_PORT" ] && SSH_ARGS+=(-p "$SSH_PORT")
RSYNC_SSH="ssh ${SSH_ARGS[*]}"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf 'REFUSED: %s\n' "$*" >&2; exit 1; }
dry()  { printf 'DRY  %s\n' "$*"; }
remote() {   # remote 'COMMAND STRING' : read-only or mutating, runs on the server
  if [ "$DRY" -eq 1 ]; then dry "ssh $HOST: $1"; return 0; fi
  ssh "${SSH_ARGS[@]}" "$HOST" "$1"
}
remote_read() {   # always executes (read-only), even in --dry-run; returns non-zero if unreachable
  ssh "${SSH_ARGS[@]}" "$HOST" "$1"
}
rfib() {   # run a command as the service user with the server environment
  remote "sudo -n -u fiboki -H env HOME=$RLIB FIBOKI_HOME=$RLIB/.fiboki FIBOKI_STATE_DIR=$RLIB/var FIBOKI_ENV_FILE=/etc/fiboki/env /bin/bash $RPREFIX/scripts/fiboki-service.sh run $1"
}
rfib_read() {
  remote_read "sudo -n -u fiboki -H env HOME=$RLIB FIBOKI_HOME=$RLIB/.fiboki FIBOKI_STATE_DIR=$RLIB/var FIBOKI_ENV_FILE=/etc/fiboki/env /bin/bash $RPREFIX/scripts/fiboki-service.sh run $1"
}
do_rsync() {   # do_rsync SRC DEST [extra args...]
  local src="$1" dest="$2"; shift 2
  if [ "$DRY" -eq 1 ]; then dry "rsync -az --partial $* $src $dest"; return 0; fi
  rsync -az --partial "$@" -e "$RSYNC_SSH" --rsync-path="sudo -n -u fiboki rsync" "$src" "$dest"
}
sha_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# ---- what runs on this Mac, and where its state is -------------------------------------
mac_running() {   # services (from the movable list) that launchd reports running here
  command -v launchctl >/dev/null 2>&1 || return 0
  local s
  for s in $ALL_MOVABLE; do
    if launchctl print "gui/$(id -u)/uk.fiboki.$s" 2>/dev/null | grep -q "state = running"; then printf '%s\n' "$s"; fi
  done
}
if [ -z "$SERVICES" ]; then
  SERVICES="$(mac_running | paste -sd, -)"
  [ -n "$SERVICES" ] || SERVICES="api,worker,web,news"
fi
IFS=',' read -r -a MOVE <<< "$SERVICES"
for s in "${MOVE[@]}"; do
  case " $ALL_MOVABLE " in *" $s "*) ;; *) echo "unknown service '$s'" >&2; exit 2 ;; esac
done
has() { printf '%s\n' "${MOVE[@]}" | grep -qx "$1"; }

# Paths exactly as backup.sh and fiboki-service.sh resolve them (the env file may set the FIBOKI_*_DIR names).
envval() { [ -f "$FHOME/env" ] && grep -E "^(export )?$1=" "$FHOME/env" | tail -1 | sed "s/^[^=]*=//; s/^['\"]//; s/['\"]$//" || true; }
STATE_DIR="${FIBOKI_STATE_DIR:-$(envval FIBOKI_STATE_DIR)}"; STATE_DIR="${STATE_DIR:-$LROOT/var}"
PAPER_ROOT="${FIBOKI_PAPER_ROOT:-$(envval FIBOKI_PAPER_ROOT)}"; PAPER_ROOT="${PAPER_ROOT:-$STATE_DIR/paper}"
DATA_ROOT="${FIBOKI_DATA_ROOT:-$(envval FIBOKI_DATA_ROOT)}"; DATA_ROOT="${DATA_ROOT:-$STATE_DIR/datastore}"

running_paper() { mac_running | grep -E '^(paper|paper-crypto)$' | paste -sd, - || true; }

gate() {   # gate [--require-fresh]: the FX/crypto paper rule; exits via die on refusal
  local extra=()
  [ "$ACCEPT" -eq 1 ] && extra+=(--accept-abandoned-positions)
  local running; running="$(running_paper)"
  [ -n "$running" ] || running=""
  if has paper || has paper-crypto; then
    if ! PYTHONPATH="$LROOT/src" "$PY" "$LROOT/scripts/migrate_preflight.py" --state-dir "$STATE_DIR" \
         --paper-root "$PAPER_ROOT" --running "$running" "$@" ${extra[@]+"${extra[@]}"}; then
      die "a paper session cannot be moved safely (above). Nothing was changed."
    fi
  else
    say "paper sessions are not being moved (--services); gate not needed"
  fi
}

# ---- preflight --------------------------------------------------------------------------
preflight() {
  step "Preflight (changes nothing)"
  say "moving    : ${MOVE[*]}"
  say "server    : $HOST  ($RPREFIX, state $RLIB/var)"
  say "mac state : $STATE_DIR   paper: $PAPER_ROOT   datastore: $DATA_ROOT"
  [ -x "$LROOT/scripts/backup.sh" ] || die "$LROOT/scripts/backup.sh not found"
  for tool in rsync ssh; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      if [ "$DRY" -eq 1 ]; then say "WARNING: $tool is not installed on this machine (a real run needs it)"; else die "$tool is not installed on this machine"; fi
    fi
  done
  gate
  if [ "$DRY" -eq 1 ]; then dry "ssh $HOST: reachability, sudo -n, installed, idle, free disk, wiring hashes"; return 0; fi
  remote_read "true" || die "cannot ssh to $HOST (BatchMode: add your key with ssh-copy-id, or pass --identity)"
  remote_read "sudo -n true" || die "$HOST has no passwordless sudo for this account (use root, or add a sudoers rule)"
  remote_read "test -x $RPREFIX/.venv/bin/fiboki && test -x /usr/local/bin/fiboki-ctl" \
    || die "Fiboki is not installed on the server at $RPREFIX: run sudo scripts/server-install.sh --no-start there first"
  local active
  active="$(remote_read "systemctl is-active $(for s in "${MOVE[@]}"; do printf 'fiboki-%s ' "$s"; done) 2>/dev/null | grep -c '^active' || true")"
  [ "${active:-0}" -eq 0 ] || die "$active of the moved services are already active on the server; two copies would fight over leases. Stop them: sudo systemctl stop 'fiboki-*'"
  # Disk: the backup's raw size (state + datastore) needs room on the server, twice over while restoring.
  local need_kb free_kb
  need_kb="$(du -sk "$STATE_DIR" 2>/dev/null | awk '{print $1}')"; need_kb="${need_kb:-0}"
  if [ "$DATASTORE" -eq 1 ] && [ -d "$DATA_ROOT" ]; then
    case "$DATA_ROOT" in "$STATE_DIR"/*) ;; *) need_kb=$(( need_kb + $(du -sk "$DATA_ROOT" | awk '{print $1}') )) ;; esac
  fi
  free_kb="$(remote_read "df -Pk $RLIB | awk 'NR==2 {print \$4}'")"
  say "disk      : need about $(( need_kb * 2 / 1048576 )) GiB (twice, while restoring); server has $(( ${free_kb:-0} / 1048576 )) GiB free"
  [ "${free_kb:-0}" -gt $(( need_kb * 2 )) ] || die "not enough free disk on the server"
  # Same paper wiring on both machines, or the server refuses the snapshot ('mismatch').
  local w mine theirs bad=0
  for w in "$LROOT"/src/fiboki/entrypoints/wiring/*.json; do
    mine="$(sha_of "$w" | awk '{print $1}')"
    theirs="$(remote_read "sha256sum $RPREFIX/src/fiboki/entrypoints/wiring/$(basename "$w") 2>/dev/null | awk '{print \$1}'" || true)"
    if [ "$mine" != "$theirs" ]; then say "WIRING DIFFERS: $(basename "$w") (mac ${mine:0:12}, server ${theirs:0:12})"; bad=1; fi
  done
  if [ "$bad" -eq 1 ] && { has paper || has paper-crypto; }; then
    die "the paper wiring files differ between this Mac and the server, so the server would refuse the state snapshot. Put both on the same commit (git pull on the server, or push the Mac's), then re-run."
  fi
  local lh rh
  lh="$(git -C "$LROOT" rev-parse --short HEAD 2>/dev/null || echo ?)"; rh="$(rfib_read "git -C $RPREFIX rev-parse --short HEAD" 2>/dev/null || echo ?)"
  say "code      : mac $lh, server $rh$( [ "$lh" = "$rh" ] || echo '   (differ: the wiring files match, so this is allowed)')"
  say "preflight OK"
}

# ---- prepare ----------------------------------------------------------------------------------
doctor_healthy() {   # the server is healthy when doctor has no FAIL except the ones expected before cutover
  local out="$1" allow="${2:-worker heartbeat}"
  printf '%s' "$out" | python3 -c '
import json, sys
allow = {a.strip() for a in sys.argv[1].split(",") if a.strip()}
try:
    data = json.load(sys.stdin)
except Exception:
    print("doctor output is not JSON"); sys.exit(2)
bad = [c for c in data.get("checks", []) if c.get("status") == "FAIL" and c.get("name") not in allow]
for c in bad:
    print("FAIL  %s: %s" % (c.get("name"), str(c.get("detail"))[:140]))
sys.exit(1 if bad else 0)
' "$allow"
}

prepare() {
  preflight
  step "Prepare: consistent backup here -> server (the Mac keeps running)"
  mkdir -p "$STAGE"
  local flags=(--dest "$STAGE")
  [ "$DATASTORE" -eq 1 ] && flags+=(--include-datastore)
  if [ "$DRY" -eq 1 ]; then dry "$LROOT/scripts/backup.sh ${flags[*]}"; else "$LROOT/scripts/backup.sh" "${flags[@]}"; fi
  local archive
  archive="$(ls -1t "$STAGE"/fiboki-backup-*.tar.gz 2>/dev/null | head -1 || true)"
  [ "$DRY" -eq 1 ] && archive="${archive:-$STAGE/fiboki-backup-DRYRUN.tar.gz}"
  [ -n "$archive" ] || die "backup.sh produced no archive in $STAGE"
  local base; base="$(basename "$archive")"
  remote "sudo -n install -d -m 0750 -o fiboki -g fiboki /var/backups/fiboki/incoming"
  do_rsync "$archive" "$HOST:/var/backups/fiboki/incoming/" --progress
  do_rsync "$archive.sha256" "$HOST:/var/backups/fiboki/incoming/"
  step "Restore on the server (scripts/restore.sh verifies the SHA-256 manifest first)"
  local rflags=""; [ "$FORCE_RESTORE" -eq 1 ] && rflags="--force"
  rfib "$RPREFIX/scripts/restore.sh /var/backups/fiboki/incoming/$base $rflags" \
    || die "restore.sh refused on the server (see above). If it says the server state is newer than the backup and this is a fresh install, re-run with --force-restore."
  # The Mac's leases and heartbeats mean nothing here (worker ids carry the Mac's hostname).
  remote "sudo -n -u fiboki sh -c 'cd $RLIB/.fiboki 2>/dev/null && for f in state.db state.db-wal state.db-shm; do [ -e \$f ] && mv \$f \$f.from-mac; done; true'"
  step "Start the API and the web app only, and check the server"
  remote "sudo -n systemctl start fiboki-api.service fiboki-web.service"
  if [ "$DRY" -eq 0 ]; then
    local i=0
    until remote_read "curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:8000/api/health"; do
      i=$((i + 1)); [ "$i" -lt 30 ] || die "the server's API did not answer on 127.0.0.1:8000 within a minute (journalctl -u fiboki-api)"
      sleep 2
    done
    say "API answers on the server"
    local out; out="$(rfib_read "fiboki doctor --json --no-hash" || true)"
    if ! doctor_healthy "$out" "worker heartbeat,news store,paper journal"; then
      die "the server's doctor reports FAIL rows (above). Fix them, then re-run prepare. The Mac is untouched."
    fi
    say "server doctor: no FAIL rows (worker heartbeat, news store, paper journal are expected before cutover)"
  fi
  say ""
  say "PREPARED. The Mac is still running everything. Next:  scripts/server-migrate.sh --host $HOST cutover"
}

# ---- cutover ----------------------------------------------------------------------------------
mac_stop() {   # mac_stop SERVICE...
  command -v launchctl >/dev/null 2>&1 || { say "(no launchctl here: nothing to stop on this machine)"; return 0; }
  local s waited
  for s in "$@"; do
    if [ "$DRY" -eq 1 ]; then dry "scripts/launchd-install.sh --unload --services $s; wait; move plist aside"; continue; fi
    "$LROOT/scripts/launchd-install.sh" --unload --services "$s" >/dev/null
    waited=0
    while launchctl print "gui/$(id -u)/uk.fiboki.$s" >/dev/null 2>&1; do
      [ "$waited" -lt 120 ] || die "uk.fiboki.$s is still stopping after 120 s; not continuing. Check it, then re-run cutover (or rollback)."
      sleep 1; waited=$((waited + 1))
    done
    if [ -f "$HOME/Library/LaunchAgents/uk.fiboki.$s.plist" ]; then
      mkdir -p "$HOME/Library/LaunchAgents/disabled-by-server-migrate"
      mv "$HOME/Library/LaunchAgents/uk.fiboki.$s.plist" "$HOME/Library/LaunchAgents/disabled-by-server-migrate/"
    fi
    say "stopped  uk.fiboki.$s (plist moved to LaunchAgents/disabled-by-server-migrate/, so a reboot cannot restart it)"
  done
}

cutover() {
  preflight
  step "Gate: the server must be healthy BEFORE anything on the Mac stops"
  if [ "$DRY" -eq 0 ]; then
    remote_read "curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:8000/api/health" \
      || die "the server's API is not answering. Run 'prepare' first (it starts the API and proves the server healthy)."
    local out; out="$(rfib_read "fiboki doctor --json --no-hash" || true)"
    doctor_healthy "$out" "worker heartbeat,news store,paper journal" || die "the server's doctor has FAIL rows (above). Nothing on the Mac was stopped."
    say "server healthy"
  fi
  if [ "$YES" -eq 0 ] && [ "$DRY" -eq 0 ]; then
    say ""
    say "About to STOP on this Mac: ${MOVE[*]}"
    say "and start them on $HOST after a final sync. Paper positions are restored from state_snapshot.json."
    read -r -p "Type 'cutover' to continue: " answer
    [ "$answer" = "cutover" ] || die "not confirmed; nothing was changed."
  fi
  # Record what we are about to stop, so `rollback` can undo exactly it.
  if [ "$DRY" -eq 0 ]; then
    mkdir -p "$FHOME"
    printf 'HOST=%s\nRPREFIX=%s\nRLIB=%s\nSERVICES=%s\nAT=%s\n' "$HOST" "$RPREFIX" "$RLIB" "$SERVICES" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LAST"
  fi
  step "Stop on the Mac (clean shutdown: the paper process writes its final state snapshot)"
  # Order: the paper sessions first (they hold the positions), then the workers, then the front ends.
  local order=() s
  for s in paper paper-crypto quotes worker news web api; do has "$s" && order+=("$s"); done
  mac_stop "${order[@]}"
  step "Gate again, now that the Mac processes have stopped (the snapshot must match the ledgers)"
  # --running names the moved paper services even though they have just stopped: "running" here means "a refusal
  # applies", and --require-fresh makes a ledger that grew past the snapshot's watermark count as unrestorable.
  if has paper || has paper-crypto; then
    local ex=(); [ "$ACCEPT" -eq 1 ] && ex+=(--accept-abandoned-positions)
    if ! PYTHONPATH="$LROOT/src" "$PY" "$LROOT/scripts/migrate_preflight.py" --state-dir "$STATE_DIR" --paper-root "$PAPER_ROOT" \
         --running "$(printf '%s\n' "${MOVE[@]}" | grep -E '^(paper|paper-crypto)$' | paste -sd, -)" --require-fresh ${ex[@]+"${ex[@]}"}; then
      say "The final snapshot cannot be restored. ROLLING BACK the Mac stop."
      do_rollback_mac; exit 1
    fi
  fi
  step "Final sync of the state directory (everything is quiescent)"
  remote "sudo -n systemctl stop fiboki-api.service fiboki-web.service"
  local ex=(--exclude '/logs/' --exclude '/datastore/')
  do_rsync "$STATE_DIR/" "$HOST:$RLIB/var/" "${ex[@]}"
  if [ "$PAPER_ROOT" != "$STATE_DIR/paper" ] && [ -d "$PAPER_ROOT" ]; then
    do_rsync "$PAPER_ROOT/" "$HOST:$RLIB/var/paper/"
  fi
  step "Start on the server"
  local units=() order2=(api worker web news quotes paper-crypto paper)
  for s in "${order2[@]}"; do if has "$s" || [ "$s" = api ] || [ "$s" = web ]; then units+=("fiboki-$s.service"); fi; done
  remote "sudo -n systemctl start ${units[*]}"
  if [ "$DRY" -eq 1 ]; then say ""; say "DRY RUN complete."; return 0; fi
  sleep 45
  local ok=1 st
  for u in "${units[@]}"; do
    st="$(remote_read "systemctl is-active $u" 2>/dev/null || true)"
    if [ "$st" != "active" ]; then say "NOT ACTIVE: $u ($st)"; ok=0; fi
  done
  local j bad
  for j in paper_forward_v1 paper_forward_crypto_v1; do
    bad="$(remote_read "sudo -n tail -n 300 $RLIB/var/paper_forward/$j/journal.jsonl 2>/dev/null | grep -E 'state_snapshot_refused|positions_abandoned_at_restart' | tail -2" || true)"
    if [ -n "$bad" ]; then say "JOURNAL $j: the snapshot was refused or positions abandoned:"; printf '%s\n' "$bad" | cut -c1-300; ok=0; fi
    if remote_read "sudo -n grep -q positions_restored_at_restart $RLIB/var/paper_forward/$j/journal.jsonl 2>/dev/null"; then say "journal $j: positions_restored_at_restart seen"; fi
  done
  local out; out="$(rfib_read "fiboki doctor --json --no-hash" || true)"
  doctor_healthy "$out" "news store,paper journal,calendar coverage" || ok=0
  if [ "$ok" -eq 0 ]; then
    if [ "$AUTO_ROLLBACK" -eq 1 ]; then say "The server is NOT healthy. ROLLING BACK."; do_rollback; exit 1; fi
    die "the server is not healthy (above) and --no-auto-rollback was given. Decide: investigate, or '$0 --host $HOST rollback'."
  fi
  say ""
  say "CUTOVER DONE. The server runs: ${MOVE[*]}"
  say "Watch it:       ssh $HOST fiboki-ctl fiboki doctor     |    journalctl -u fiboki-paper -f   (on the server)"
  say "To undo:        scripts/server-migrate.sh --host $HOST rollback --pull-back    (keep the Mac's plists until you are sure)"
  say "Mac plists moved to ~/Library/LaunchAgents/disabled-by-server-migrate/ ; delete them only when you are sure."
}

# ---- rollback ---------------------------------------------------------------------------------
do_rollback_mac() {
  command -v launchctl >/dev/null 2>&1 || { say "(no launchctl here)"; return 0; }
  local list; list="$(printf '%s' "$SERVICES")"
  if [ "$DRY" -eq 1 ]; then dry "scripts/launchd-install.sh --services $list --load"; return 0; fi
  "$LROOT/scripts/launchd-install.sh" --services "$list" --load
}
do_rollback() {
  step "Rollback: stop the moved services on the server, restart the Mac's"
  local units=() s
  for s in paper paper-crypto quotes worker news web api; do has "$s" && units+=("fiboki-$s.service"); done
  remote "sudo -n systemctl stop ${units[*]} fiboki-api.service fiboki-web.service"
  if [ "$PULL_BACK" -eq 1 ]; then
    step "Pull the server's state home (the Mac's copies are kept in .pre-pullback-*)"
    local bk; bk="$STATE_DIR.pre-pullback-$(date -u +%Y%m%dT%H%M%SZ)"
    if [ "$DRY" -eq 1 ]; then dry "rsync -az --backup --backup-dir=$bk $HOST:$RLIB/var/ $STATE_DIR/"
    else rsync -az --backup --backup-dir="$bk" --exclude '/logs/' --exclude '/datastore/' -e "$RSYNC_SSH" --rsync-path="sudo -n rsync" "$HOST:$RLIB/var/" "$STATE_DIR/"; fi
  else
    say "NOTE: the Mac restarts from ITS OWN state at cutover. If the server has traded paper since, re-run with --pull-back"
    say "      to bring the server's ledgers home first (the Mac's copies are kept)."
  fi
  do_rollback_mac
  say "Rolled back. Check the Mac:  .venv/bin/fiboki doctor"
}
rollback() {
  if [ -f "$LAST" ] && [ -z "${SERVICES_GIVEN:-}" ]; then
    # shellcheck disable=SC1090
    while IFS='=' read -r k v; do case "$k" in SERVICES) SERVICES="$v"; IFS=',' read -r -a MOVE <<< "$SERVICES" ;; esac; done < "$LAST"
    say "using the services recorded at cutover: $SERVICES"
  fi
  do_rollback
}

case "$PHASE" in
  preflight) preflight ;;
  prepare) prepare ;;
  cutover) cutover ;;
  rollback) rollback ;;
  all)
    prepare
    if [ "$YES" -eq 0 ] && [ "$DRY" -eq 0 ]; then
      read -r -p "Prepared. Continue to cutover now? [y/N] " a
      [ "${a:-n}" = "y" ] || { say "Stopped after prepare. Run 'cutover' when ready."; exit 0; }
    fi
    cutover ;;
esac
