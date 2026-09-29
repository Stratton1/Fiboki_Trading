#!/usr/bin/env bash
# Fiboki.app's executable: start the platform silently and open the workstation.
#
# No Terminal window, no log tail. Like any desktop app, the operator sees a
# notification while it starts, the browser when it is ready, and a dialog
# only if something failed (with the log path to look at). The services are
# the launchd LaunchAgents (uk.fiboki.*), so closing the browser stops
# nothing and a second click just re-opens the workstation.
#
# Paper mode only: nothing here can set a live control; the services force
# FIBOKI_EXECUTION_MODE=paper themselves (scripts/fiboki-service.sh).
#
# ROOT is the runtime checkout the launcher was installed from
# (scripts/desktop/install-launcher.sh substitutes it), overridable with
# FIBOKI_ROOT. It must be outside ~/Documents: DEPLOYMENT.md §2.6.
set -uo pipefail
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

ROOT="${FIBOKI_ROOT:-__FIBOKI_ROOT__}"
[ -d "$ROOT/scripts" ] || ROOT="$HOME/fiboki"
URL="http://localhost:3000"
API="http://127.0.0.1:8000/api/health"
DOMAIN="gui/$(id -u)"
SERVICES="api worker web news"
[ -f "$HOME/Library/LaunchAgents/uk.fiboki.paper.plist" ] && SERVICES="$SERVICES paper"
LOG="$ROOT/var/logs/launcher.log"
mkdir -p "$ROOT/var/logs" 2>/dev/null || LOG="/tmp/fiboki-launcher.log"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; }
notify() {  # $1 title, $2 body
  osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
}
fail() {  # $1 message: a dialog, because there is no window to read
  log "FAIL: $1"
  osascript -e "display dialog \"$1\n\nLog: $LOG\" with title \"Fiboki\" buttons {\"Open log folder\", \"OK\"} default button \"OK\" with icon stop" \
    -e 'if button returned of result is "Open log folder" then do shell script "open '"$(dirname "$LOG")"'"' >/dev/null 2>&1 || true
  exit 1
}
healthy() { curl -fsS -m 3 -o /dev/null "$API" 2>/dev/null && curl -fsS -m 5 -o /dev/null "$URL" 2>/dev/null; }

log "launch from $ROOT"
if [ ! -f "$ROOT/scripts/fiboki-service.sh" ]; then
  fail "Fiboki is not installed at $ROOT. Clone it there (outside Documents) and run scripts/desktop-install.sh, then reinstall this launcher."
fi

# Already up: just open it.
if healthy; then
  log "already healthy; opening $URL"
  open "$URL"
  exit 0
fi

notify "Fiboki" "Starting the platform…"

# Load whatever is installed but not loaded; install the four core services if
# nothing is. launchctl print succeeds only for a loaded label.
loaded_any=0
for name in $SERVICES; do
  label="uk.fiboki.$name"
  plist="$HOME/Library/LaunchAgents/$label.plist"
  if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
    loaded_any=1
    # Loaded but not running (a clean exit is not restarted by KeepAlive): kick it.
    if ! launchctl print "$DOMAIN/$label" 2>/dev/null | grep -q 'state = running'; then
      log "kickstart $label"
      launchctl kickstart "$DOMAIN/$label" >/dev/null 2>&1 || true
    fi
  elif [ -f "$plist" ]; then
    log "bootstrap $label"
    launchctl bootstrap "$DOMAIN" "$plist" >>"$LOG" 2>&1 && loaded_any=1
  fi
done
if [ "$loaded_any" -eq 0 ]; then
  log "no services installed; running launchd-install.sh --load"
  if ! (cd "$ROOT" && bash scripts/launchd-install.sh --services api,worker,web,news --load >>"$LOG" 2>&1); then
    fail "Installing the Fiboki services failed."
  fi
fi

# Wait for the API and the web build. next start is ready in seconds; a cold
# API on a busy machine can take longer, so allow 120 s before giving up.
waited=0
until healthy; do
  if [ "$waited" -ge 120 ]; then
    fail "Fiboki did not become healthy within 120 s. Services: $(launchctl list 2>/dev/null | grep 'uk\.fiboki' | awk '{printf "%s(pid %s, exit %s) ", $3, $1, $2}')"
  fi
  sleep 2; waited=$((waited + 2))
done
log "healthy after ${waited}s; opening $URL"
notify "Fiboki" "Ready. Opening the workstation."
open "$URL"
exit 0
