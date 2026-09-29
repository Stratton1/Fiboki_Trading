#!/usr/bin/env bash
# Fiboki V2: install the desktop launchd services from deploy/launchd/ templates.
#
#   scripts/launchd-install.sh                       # write all five plists, do not load
#   scripts/launchd-install.sh --load                # write, then (re)load them
#   scripts/launchd-install.sh --services api,worker # a subset
#   scripts/launchd-install.sh --unload              # stop and remove the loaded services
#   scripts/launchd-install.sh --target DIR          # write elsewhere (review, tests)
#
# Services: api worker web news llama (uk.fiboki.<name>). Each runs
# scripts/fiboki-service.sh <name>, which forces paper mode. This script sets
# no environment and no live control; secrets live in ~/.fiboki/env.
# Idempotent: re-running rewrites the plists from the templates and, with
# --load, boots each service out before bootstrapping it again.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
TARGET="$HOME/Library/LaunchAgents"
SERVICES="api,worker,web,news,llama"
LOAD=0
UNLOAD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --load) LOAD=1; shift ;;
    --unload) UNLOAD=1; shift ;;
    --services) SERVICES="${2:?}"; shift 2 ;;
    --target) TARGET="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$ROOT" in
  *'#'*|*'&'*|*'\'*) echo "repository path $ROOT contains a character sed cannot substitute safely" >&2; exit 2 ;;
esac

DOMAIN="gui/$(id -u)"
IFS=',' read -r -a NAMES <<< "$SERVICES"

if [ "$UNLOAD" -eq 1 ]; then
  for name in "${NAMES[@]}"; do
    label="uk.fiboki.$name"
    launchctl bootout "$DOMAIN/$label" 2>/dev/null && echo "stopped  $label" || echo "not loaded  $label"
  done
  exit 0
fi

mkdir -p "$TARGET" "$ROOT/var/logs"
for name in "${NAMES[@]}"; do
  src="deploy/launchd/uk.fiboki.$name.plist"
  if [ ! -f "$src" ]; then echo "no template $src (services: api worker web news llama)" >&2; exit 2; fi
  dest="$TARGET/uk.fiboki.$name.plist"
  sed "s#__FIBOKI_ROOT__#$ROOT#g" "$src" > "$dest.tmp"
  if grep -q '__FIBOKI_ROOT__' "$dest.tmp"; then echo "substitution failed for $dest" >&2; exit 1; fi
  if command -v plutil >/dev/null 2>&1; then plutil -lint -s "$dest.tmp"; fi
  mv "$dest.tmp" "$dest"
  echo "wrote    $dest"
  if [ "$LOAD" -eq 1 ]; then
    label="uk.fiboki.$name"
    if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
      launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
      # bootout returns before the service is gone: the worker finishes its
      # current cycle on SIGTERM, and bootstrapping the same label while it is
      # still being torn down fails with "Bootstrap failed: 5". Wait for it.
      waited=0
      while launchctl print "$DOMAIN/$label" >/dev/null 2>&1; do
        if [ "$waited" -ge 90 ]; then
          echo "$label is still stopping after ${waited}s; not reloaded (retry with --load once it has exited)" >&2
          exit 1
        fi
        sleep 1; waited=$((waited + 1))
      done
      [ "$waited" -gt 0 ] && echo "stopped  $label (after ${waited}s)"
    fi
    launchctl bootstrap "$DOMAIN" "$dest"
    echo "loaded   $label   (launchctl print $DOMAIN/$label)"
  fi
done

if [ "$LOAD" -eq 0 ]; then
  echo
  echo "Not loaded. Review the files, then: scripts/launchd-install.sh --load"
fi
if launchctl print "$DOMAIN/com.fiboki.research-worker" >/dev/null 2>&1; then
  echo "NOTE: the older com.fiboki.research-worker is loaded as well. Only one worker holds the"
  echo "      lease; remove the old one: launchctl bootout $DOMAIN/com.fiboki.research-worker"
fi
echo "Check with: .venv/bin/fiboki doctor"
