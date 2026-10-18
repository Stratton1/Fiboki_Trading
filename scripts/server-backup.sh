#!/usr/bin/env bash
# Fiboki V2 server: nightly backup, retention and the OFF-BOX copy.
#
#   scripts/server-backup.sh              # what fiboki-backup.timer runs (via fiboki-service.sh run)
#   scripts/server-backup.sh --print      # show what it would do, change nothing
#
# Runs scripts/backup.sh (SQLite online backup, SHA-256 manifest, the env file is NEVER included),
# then keeps the newest N archives and copies the new one off the box if you configured a target.
# The services are NOT stopped: every SQLite file is a consistent snapshot, and a JSONL ledger being
# appended to at that instant can end in a torn line, which the framed readers quarantine on read.
#
# Settings (all optional; put them in the env file, /etc/fiboki/env):
#   FIBOKI_BACKUP_DIR                 default /var/backups/fiboki   (daily/ and weekly/ below it)
#   FIBOKI_BACKUP_KEEP                default 14   daily archives kept
#   FIBOKI_BACKUP_KEEP_WEEKLY         default 3    weekly archives kept (they include the datastore)
#   FIBOKI_BACKUP_DATASTORE           weekly (default) | always | never
#   FIBOKI_BACKUP_RCLONE_REMOTE       e.g. b2:fiboki-backups/server1   (needs rclone + its config)
#   FIBOKI_BACKUP_RESTIC_REPOSITORY   e.g. sftp:user@host:/srv/restic   (needs restic + RESTIC_PASSWORD_FILE)
# Off-box is the OPERATOR'S to configure (docs/v2/SERVER.md, "Backups"): this script ships no
# credentials and no default target. With neither set it says so on every run, because a backup that
# shares the disk with the data is not a backup.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PRINT=0
case "${1:-}" in
  --print) PRINT=1 ;;
  -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
  '') ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

DEST="${FIBOKI_BACKUP_DIR:-/var/backups/fiboki}"
KEEP="${FIBOKI_BACKUP_KEEP:-14}"
KEEP_WEEKLY="${FIBOKI_BACKUP_KEEP_WEEKLY:-3}"
DATASTORE="${FIBOKI_BACKUP_DATASTORE:-weekly}"
RCLONE_REMOTE="${FIBOKI_BACKUP_RCLONE_REMOTE:-}"
RESTIC_REPO="${FIBOKI_BACKUP_RESTIC_REPOSITORY:-}"
STAMP_FILE="${FIBOKI_HOME:-$HOME/.fiboki}/backup.last"

case "$KEEP$KEEP_WEEKLY" in *[!0-9]*|'') echo "FIBOKI_BACKUP_KEEP and FIBOKI_BACKUP_KEEP_WEEKLY must be whole numbers" >&2; exit 2 ;; esac
case "$DATASTORE" in weekly|always|never) ;; *) echo "FIBOKI_BACKUP_DATASTORE must be weekly, always or never" >&2; exit 2 ;; esac

run_weekly=0
case "$DATASTORE" in
  always) run_weekly=1 ;;
  weekly) [ "$(date -u +%u)" = "7" ] && run_weekly=1 ;;
esac

if [ "$PRINT" -eq 1 ]; then
  echo "destination : $DEST (daily keep $KEEP, weekly keep $KEEP_WEEKLY)"
  echo "daily       : scripts/backup.sh --dest $DEST/daily"
  if [ "$run_weekly" -eq 1 ]; then echo "weekly      : scripts/backup.sh --dest $DEST/weekly --include-datastore (today)"
  else echo "weekly      : none today (FIBOKI_BACKUP_DATASTORE=$DATASTORE)"; fi
  if [ -n "$RCLONE_REMOTE" ]; then echo "rclone      : configured"; else echo "rclone      : not configured"; fi
  if [ -n "$RESTIC_REPO" ]; then echo "restic      : configured"; else echo "restic      : not configured"; fi
  exit 0
fi

mkdir -p "$DEST/daily" "$DEST/weekly"
newest() { { ls -1t "$1"/fiboki-backup-*.tar.gz 2>/dev/null || true; } | head -1; }
prune() {   # prune DIR KEEP: newest KEEP archives (and their .sha256) stay
  local dir="$1" keep="$2" old
  { ls -1t "$dir"/fiboki-backup-*.tar.gz 2>/dev/null || true; } | tail -n +"$((keep + 1))" | while read -r old; do
    rm -f -- "$old" "$old.sha256"
    echo "pruned    $old"
  done
}

"$ROOT/scripts/backup.sh" --dest "$DEST/daily"
daily="$(newest "$DEST/daily")"
weekly=""
if [ "$run_weekly" -eq 1 ]; then
  "$ROOT/scripts/backup.sh" --dest "$DEST/weekly" --include-datastore
  weekly="$(newest "$DEST/weekly")"
fi
prune "$DEST/daily" "$KEEP"
prune "$DEST/weekly" "$KEEP_WEEKLY"

offbox=""
failed=0
copy_rclone() {
  command -v rclone >/dev/null 2>&1 || { echo "FIBOKI_BACKUP_RCLONE_REMOTE is set but rclone is not installed" >&2; return 1; }
  local f rc=0 sub
  for f in "$@"; do
    [ -n "$f" ] || continue
    sub="$(basename "$(dirname "$f")")"
    rclone copyto "$f" "$RCLONE_REMOTE/$sub/$(basename "$f")" || rc=1
    rclone copyto "$f.sha256" "$RCLONE_REMOTE/$sub/$(basename "$f").sha256" || rc=1
  done
  return "$rc"
}
copy_restic() {
  command -v restic >/dev/null 2>&1 || { echo "FIBOKI_BACKUP_RESTIC_REPOSITORY is set but restic is not installed" >&2; return 1; }
  RESTIC_REPOSITORY="$RESTIC_REPO" restic backup --tag fiboki --host fiboki-server "$@"
}
if [ -n "$RCLONE_REMOTE" ]; then
  if copy_rclone "$daily" "$weekly"; then offbox="rclone"; else failed=1; fi
fi
if [ -n "$RESTIC_REPO" ]; then
  files=("$daily"); [ -n "$weekly" ] && files+=("$weekly")
  if copy_restic "${files[@]}"; then offbox="${offbox:+$offbox,}restic"; else failed=1; fi
fi
if [ -z "$offbox" ] && [ "$failed" -eq 0 ]; then
  echo "WARNING: no off-box target configured (FIBOKI_BACKUP_RCLONE_REMOTE / FIBOKI_BACKUP_RESTIC_REPOSITORY)." >&2
  echo "         These archives share a disk with the data. See docs/v2/SERVER.md, Backups." >&2
fi

mkdir -p "$(dirname "$STAMP_FILE")"
if [ "$failed" -eq 0 ]; then
  printf '%s offbox=%s archive=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${offbox:-none}" "$(basename "$daily")" > "$STAMP_FILE"
  echo "backup ok (off-box: ${offbox:-none})"
else
  echo "backup archives written, but the OFF-BOX copy FAILED" >&2
  exit 1
fi
