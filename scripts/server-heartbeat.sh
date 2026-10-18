#!/usr/bin/env bash
# Fiboki V2 server: the external heartbeat. fiboki-heartbeat.timer runs it every five minutes.
#
#   scripts/server-heartbeat.sh            # check, push on a change (what the timer runs)
#   scripts/server-heartbeat.sh --print    # check and print the problems; send and record nothing
#   scripts/server-heartbeat.sh --test     # send one test message through every configured channel
#
# What it checks (each problem is one line):
#   * `fiboki doctor` rows that are FAIL
#   * every worker heartbeat (`fiboki worker status`): stale or down
#   * the quote recorder's freshness (`fiboki quotes status`), when fiboki-quotes is enabled; skipped
#     while the FX market is closed (Friday 22:30 to Sunday 22:30 UTC) because only CHANGED quotes are written
#   * every ENABLED fiboki-*.service: failed or not active
#   * the nightly backup: none recorded in 36 hours (when fiboki-backup.timer is enabled)
#
# Where it pushes (all optional, any combination; configure in the env file, /etc/fiboki/env):
#   FIBOKI_TELEGRAM_BOT_TOKEN + FIBOKI_TELEGRAM_CHAT_ID   the channel fiboki.obs.alerts already uses
#   FIBOKI_NTFY_URL [+ FIBOKI_NTFY_TOKEN]                 ntfy (https://ntfy.sh/<topic> or self-hosted)
#   FIBOKI_ALERT_WEBHOOK_URL                              the alerts webhook; body {"text", "source", "severity"}
#   FIBOKI_HEALTHCHECK_URL                                dead-man switch (healthchecks.io style): GET <url> when
#                                                         all is well, GET <url>/fail when not. The one signal that
#                                                         still arrives when this whole box is down.
# A change in the problem set pushes at once; an unchanged problem repeats every
# FIBOKI_HEARTBEAT_REPEAT_MINUTES (default 60); a recovery pushes once. Tokens and URLs go to curl through
# a config on stdin (never argv, never the journal). FIBOKI_HEARTBEAT_IGNORE=name,name skips doctor check names
# or worker ids that are expected to be down.
# Exit 1 when there is a problem (so `systemctl --failed` shows it too), 0 otherwise.
set -uo pipefail
cd "$(dirname "$0")/.."
MODE=run
case "${1:-}" in
  --print) MODE=print ;;
  --test) MODE=test ;;
  -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
  '') ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

HOST="$(hostname -s 2>/dev/null || echo server)"
IGNORE=",${FIBOKI_HEARTBEAT_IGNORE:-},"
REPEAT_MIN="${FIBOKI_HEARTBEAT_REPEAT_MINUTES:-60}"
case "$REPEAT_MIN" in *[!0-9]*|'') REPEAT_MIN=60 ;; esac
STATE_FILE="${FIBOKI_HOME:-$HOME/.fiboki}/heartbeat.state"
PROBLEMS=()
problem() { PROBLEMS+=("$1"); }
ignored() { case "$IGNORE" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

# ---- 1 + 2: doctor and the worker heartbeats ------------------------------
check_json() {   # check_json KIND  (stdin: JSON) -> problem lines on stdout
  python3 -c '
import json, sys
kind = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception as exc:
    print(f"{kind}: output is not JSON ({type(exc).__name__})")
    raise SystemExit(0)
if kind == "doctor":
    for c in data.get("checks", []):
        if c.get("status") == "FAIL":
            print("doctor " + c.get("name", "?") + "\t" + str(c.get("detail", ""))[:160])
else:
    for w in data.get("workers", []):
        if w.get("verdict") in ("stale", "down"):
            print("worker " + str(w.get("worker_id")) + "\t" + w["verdict"] + ", last beat " + str(w.get("age_seconds")) + "s ago")
' "$1"
}
add_lines() {
  local key rest line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    key="${line%%$'\t'*}"; rest="${line#*$'\t'}"
    ignored "${key#* }" && continue
    problem "$key: $rest"
  done
}
if ! command -v fiboki >/dev/null 2>&1; then
  problem "fiboki is not on PATH (run through scripts/fiboki-service.sh run)"
else
  out="$(fiboki doctor --json --no-hash 2>/dev/null)"
  if [ -z "$out" ]; then problem "doctor: produced no output"; else add_lines < <(check_json doctor <<<"$out"); fi
  out="$(fiboki worker status --json 2>/dev/null)"
  if [ -z "$out" ]; then problem "worker status: produced no output"; else add_lines < <(check_json workers <<<"$out"); fi
fi

# ---- 3: quote recorder freshness ------------------------------------------
fx_closed() {
  local dow hm; dow="$(date -u +%u)"; hm="$(date -u +%H%M)"
  { [ "$dow" = 5 ] && [ "$hm" -ge 2230 ]; } || [ "$dow" = 6 ] || { [ "$dow" = 7 ] && [ "$hm" -lt 2230 ]; }
}
unit_enabled() { [ "$(systemctl is-enabled "$1" 2>/dev/null)" = "enabled" ]; }
if command -v systemctl >/dev/null 2>&1 && unit_enabled fiboki-quotes.service && command -v fiboki >/dev/null 2>&1; then
  if fx_closed; then :; else
    msg="$(fiboki quotes status --max-age "${FIBOKI_HEARTBEAT_QUOTES_MAX_AGE:-900}" 2>&1)" \
      || problem "quotes: $(printf '%s' "$msg" | tr '\n' ' ' | cut -c1-160)"
  fi
fi

# ---- 4: enabled units ------------------------------------------------------
if command -v systemctl >/dev/null 2>&1; then
  units="$(systemctl list-unit-files 'fiboki-*.service' --state=enabled --no-legend 2>/dev/null | awk '{print $1}')"
  for u in $units; do
    st="$(systemctl is-active "$u" 2>/dev/null)"
    [ "$st" = "active" ] && continue
    ignored "${u%.service}" && continue
    problem "unit ${u%.service}: $st"
  done
  # ---- 5: backup recency ---------------------------------------------------
  if unit_enabled fiboki-backup.timer; then
    last="${FIBOKI_HOME:-$HOME/.fiboki}/backup.last"
    if [ ! -f "$last" ]; then
      [ "$(( $(date +%s) - $(stat -c %Y /etc/fiboki 2>/dev/null || echo 0) ))" -gt 172800 ] && problem "backup: none recorded yet"
    elif [ "$(( $(date +%s) - $(stat -c %Y "$last") ))" -gt 129600 ]; then
      problem "backup: last success over 36 hours ago"
    fi
  fi
fi

# ---- push ------------------------------------------------------------------
configured=()
[ -n "${FIBOKI_TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${FIBOKI_TELEGRAM_CHAT_ID:-}" ] && configured+=(telegram)
[ -n "${FIBOKI_NTFY_URL:-}" ] && configured+=(ntfy)
[ -n "${FIBOKI_ALERT_WEBHOOK_URL:-}" ] && configured+=(webhook)
[ -n "${FIBOKI_HEALTHCHECK_URL:-}" ] && configured+=(healthcheck)

http() {   # http NAME CODE: a failed send is logged by channel name only (no URL, no token)
  case "$2" in 2??) ;; *) echo "heartbeat: $1 send failed (HTTP ${2:-none})" >&2; return 1 ;; esac
}
send() {   # send SEVERITY MESSAGE
  local sev="$1" text="$2" code
  text="${text:0:3500}"
  if [ -n "${FIBOKI_TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${FIBOKI_TELEGRAM_CHAT_ID:-}" ]; then
    code="$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$FIBOKI_TELEGRAM_BOT_TOKEN" \
      | curl -sS -m 20 -o /dev/null -w '%{http_code}' -K - --data-urlencode "chat_id=$FIBOKI_TELEGRAM_CHAT_ID" \
        --data-urlencode "text=$text" 2>/dev/null)"; http telegram "$code"
  fi
  if [ -n "${FIBOKI_NTFY_URL:-}" ]; then
    local prio=default tags=white_check_mark
    [ "$sev" = problem ] && { prio=high; tags=warning; }
    code="$({ printf 'url = "%s"\n' "$FIBOKI_NTFY_URL"
              [ -n "${FIBOKI_NTFY_TOKEN:-}" ] && printf 'header = "Authorization: Bearer %s"\n' "$FIBOKI_NTFY_TOKEN"
              printf 'header = "Title: Fiboki %s"\nheader = "Priority: %s"\nheader = "Tags: %s"\n' "$HOST" "$prio" "$tags"; } \
      | curl -sS -m 20 -o /dev/null -w '%{http_code}' -K - --data-binary "$text" 2>/dev/null)"; http ntfy "$code"
  fi
  if [ -n "${FIBOKI_ALERT_WEBHOOK_URL:-}" ]; then
    local body
    body="$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1], "source": "fiboki-heartbeat", "severity": sys.argv[2]}))' "$text" "$sev")"
    code="$(printf 'url = "%s"\n' "$FIBOKI_ALERT_WEBHOOK_URL" \
      | curl -sS -m 20 -o /dev/null -w '%{http_code}' -K - -H 'Content-Type: application/json' --data-binary "$body" 2>/dev/null)"; http webhook "$code"
  fi
}
ping_deadman() {   # ping_deadman ok|fail
  [ -n "${FIBOKI_HEALTHCHECK_URL:-}" ] || return 0
  local url="${FIBOKI_HEALTHCHECK_URL%/}"
  [ "$1" = fail ] && url="$url/fail"
  printf 'url = "%s"\n' "$url" | curl -fsS -m 15 --retry 2 -o /dev/null -K - 2>/dev/null \
    || echo "heartbeat: healthcheck ping failed" >&2
}

if [ "$MODE" = test ]; then
  [ "${#configured[@]}" -gt 0 ] || { echo "no push channel configured (Telegram, ntfy, webhook or healthcheck)" >&2; exit 1; }
  send ok "Fiboki heartbeat test from $HOST. Channels: ${configured[*]}."
  ping_deadman ok
  echo "sent a test through: ${configured[*]}"
  exit 0
fi

n="${#PROBLEMS[@]}"
if [ "$MODE" = print ]; then
  if [ "$n" -eq 0 ]; then echo "healthy"; else printf 'PROBLEM %s\n' "${PROBLEMS[@]}"; fi
  [ "$n" -eq 0 ]; exit $?
fi

now="$(date +%s)"
prev_hash=""; prev_sent=0
if [ -f "$STATE_FILE" ]; then { read -r prev_hash; read -r prev_sent; } < "$STATE_FILE" || true; fi
case "$prev_sent" in *[!0-9]*|'') prev_sent=0 ;; esac
mkdir -p "$(dirname "$STATE_FILE")"

if [ "$n" -eq 0 ]; then
  if [ -n "$prev_hash" ]; then send ok "[$HOST] Fiboki: all clear again."; fi
  : > "$STATE_FILE"
  ping_deadman ok
  exit 0
fi

summary="[$HOST] Fiboki: $n problem(s)"$'\n'"$(printf -- '- %s\n' "${PROBLEMS[@]}")"
hash="$(printf '%s' "${PROBLEMS[*]}" | sha256sum | cut -c1-16)"
if [ "$hash" != "$prev_hash" ] || [ $(( now - prev_sent )) -ge $(( REPEAT_MIN * 60 )) ]; then
  if [ "${#configured[@]}" -eq 0 ]; then
    echo "heartbeat: $n problem(s) and NO push channel configured:" >&2
  else
    send problem "$summary"
  fi
  printf '%s\n%s\n' "$hash" "$now" > "$STATE_FILE"
fi
printf '%s\n' "$summary" >&2
ping_deadman fail
exit 1
