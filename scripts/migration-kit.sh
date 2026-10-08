#!/usr/bin/env bash
# Fiboki V2: everything needed to move Fiboki to another Mac, in ONE folder.
#
#   scripts/migration-kit.sh                          # -> ~/FibokiMigration-<UTC>/
#   scripts/migration-kit.sh --dest /Volumes/External
#   scripts/migration-kit.sh --runtime ~/fiboki --dev ~/Documents/Claude/Projects/Fiboki
#
# Fiboki lives in several places on purpose (docs/v2/DEPLOYMENT.md section 2.6):
#   runtime checkout  ~/fiboki                         launchd services run from here; its
#                                                      var/ holds the operator state and the
#                                                      market-data store
#   dev checkout      ~/Documents/Claude/Projects/Fiboki
#                                                      research campaigns, their working files,
#                                                      evaluation caches
#   ~/.fiboki         env (SECRETS), state.db (leases, heartbeats)
#   ~/Library/LaunchAgents/uk.fiboki.*.plist, ~/Desktop/Fiboki.app
# The code itself is on GitHub; this kit carries what GitHub does not.
#
# The kit folder holds:
#   repo.bundle                    git bundle of every branch and tag of the dev checkout
#                                  (restorable without GitHub: git clone repo.bundle fiboki)
#   runtime/fiboki-backup-*.tar.gz scripts/backup.sh --include-datastore of the runtime checkout
#   dev/fiboki-backup-*.tar.gz     scripts/backup.sh of the dev checkout (its var/, without
#                                  its datastore, which is the same OANDA store)
#   dev/research-working.tar.gz    research/reports/**/{experiments.sqlite,checkpoint.json}
#                                  and research/reports/e1/shards: untracked, not in git
#   launchd/                       copies of the uk.fiboki.*.plist files (for reference: the
#                                  desktop regenerates them with scripts/launchd-install.sh)
#   SHA256SUMS, MANIFEST.json, RESTORE.md
#
# NOT included, deliberately: ~/.fiboki/env. It holds the OANDA token, the session secret and
# the operator hashes. Put its contents in your password manager and re-create the file on the
# new machine (mode 600). Also not included: .venv and node_modules (rebuilt by
# scripts/desktop-install.sh), and ~/Fiboki_Old (superseded V1 material; copy it separately
# only if you want it).
#
# Stop the services first for a clean ledger copy: scripts/launchd-install.sh --unload
# Nothing is deleted or modified at the source.
set -euo pipefail

RUNTIME="${FIBOKI_RUNTIME_ROOT:-$HOME/fiboki}"
DEV="${FIBOKI_DEV_ROOT:-$HOME/Documents/Claude/Projects/Fiboki}"
DEST_PARENT="$HOME"
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST_PARENT="${2:?}"; shift 2 ;;
    --runtime) RUNTIME="${2:?}"; shift 2 ;;
    --dev) DEV="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KIT="$DEST_PARENT/FibokiMigration-$STAMP"
for d in "$RUNTIME" "$DEV"; do
  [ -d "$d/.git" ] || { echo "not a git checkout: $d" >&2; exit 2; }
done
if [ -n "$(git -C "$DEV" status --porcelain --untracked-files=no)" ]; then
  echo "WARNING: $DEV has uncommitted changes to tracked files; they are NOT in repo.bundle." >&2
  echo "         Commit them first, or copy them by hand." >&2
fi
mkdir -p "$KIT/runtime" "$KIT/dev" "$KIT/launchd"

echo "1/5 git bundle (all refs) from $DEV"
git -C "$DEV" bundle create "$KIT/repo.bundle" --all >/dev/null
git -C "$DEV" bundle verify "$KIT/repo.bundle" >/dev/null

echo "2/5 runtime state and market-data store from $RUNTIME (large)"
"$RUNTIME/scripts/backup.sh" --dest "$KIT/runtime" --include-datastore

echo "3/5 dev checkout state from $DEV"
( cd "$DEV" && FIBOKI_STATE_DIR="$DEV/var" FIBOKI_DATA_ROOT="$DEV/var/datastore" \
    "$DEV/scripts/backup.sh" --dest "$KIT/dev" )

echo "4/5 untracked research working files from $DEV"
( cd "$DEV" && {
    find research/reports \( -name experiments.sqlite -o -name checkpoint.json \) -type f
    if [ -d research/reports/e1/shards ]; then echo research/reports/e1/shards; fi
  } | tar -czf "$KIT/dev/research-working.tar.gz" -T - )

echo "5/5 launchd plists"
cp -p "$HOME"/Library/LaunchAgents/uk.fiboki.*.plist "$KIT/launchd/" 2>/dev/null || true

cat > "$KIT/RESTORE.md" <<EOF
# Restoring Fiboki from this kit ($STAMP)

1. Install the toolchain: \`brew install python@3.11 node git sqlite llama.cpp\`.
2. Code: \`git clone $KIT/repo.bundle ~/fiboki\` (or clone from GitHub; the
   bundle is the offline copy), then \`cd ~/fiboki && git remote set-url origin
   https://github.com/Stratton1/Fiboki_Trading.git\`.
3. Secrets: re-create \`~/.fiboki/env\` from your password manager, \`chmod 600 ~/.fiboki/env\`.
4. \`scripts/desktop-install.sh --check\`, then \`scripts/desktop-install.sh\` until no MISSING line.
5. State and market data: \`scripts/restore.sh runtime/fiboki-backup-*.tar.gz\`
   (docs/v2/OPERATIONS.md section 13.2). Check the archive first:
   \`shasum -a 256 -c runtime/*.sha256\`.
6. Research working files (optional): \`tar -xzf dev/research-working.tar.gz -C ~/fiboki\`.
7. \`.venv/bin/fiboki doctor\` until 0 FAIL, then
   \`scripts/launchd-install.sh --services api,worker,web,news,paper,quotes --load\`.
8. System Settings on the desktop: log in automatically, never sleep, restart after a power
   failure (LaunchAgents only run while you are logged in).
EOF

( cd "$KIT" && find . -type f ! -name SHA256SUMS ! -name MANIFEST.json | sort | while read -r f; do
    shasum -a 256 "$f" 2>/dev/null || sha256sum "$f"
  done > SHA256SUMS )
cat > "$KIT/MANIFEST.json" <<EOF
{
  "format": 1,
  "created_at_utc": "$STAMP",
  "host": "$(hostname)",
  "runtime_checkout": "$RUNTIME",
  "runtime_head": "$(git -C "$RUNTIME" rev-parse HEAD)",
  "dev_checkout": "$DEV",
  "dev_head": "$(git -C "$DEV" rev-parse HEAD)",
  "excludes": ["~/.fiboki/env (secrets: password manager)", ".venv", "node_modules", "~/Fiboki_Old"]
}
EOF
du -sh "$KIT"
echo "kit: $KIT  (verify on the new machine: cd <kit> && shasum -a 256 -c SHA256SUMS)"
