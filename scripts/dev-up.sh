#!/usr/bin/env bash
# Fiboki V2 — start the whole platform locally. Paper mode only; live is impossible here.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
export PATH="/opt/homebrew/bin:$PATH"

DEV_PASSWORD="${FIBOKI_DEV_PASSWORD:-fiboki-dev}"
HASH="$(printf '%s' "$DEV_PASSWORD" | shasum -a 256 | cut -d' ' -f1)"

# ---- Safety: this is a local paper deployment. None of the five live controls is set.
export FIBOKI_EXECUTION_MODE=paper
unset FIBOKI_LIVE_RUNTIME_ARMED || true

# ---- Session / auth (local http, so Secure off and SameSite lax deliberately)
export FIBOKI_SESSION_SECRET="${FIBOKI_SESSION_SECRET:-$(openssl rand -hex 32)}"
export FIBOKI_COOKIE_SECURE=false
export FIBOKI_COOKIE_SAMESITE=lax
export FIBOKI_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
export FIBOKI_OPERATORS="joe:admin:${HASH},tom:operator:${HASH}"

# ---- State and data
export FIBOKI_STATE_DIR="$ROOT/var"
export FIBOKI_DATA_ROOT="$ROOT/data/canonical/histdata"
export FIBOKI_EXPERIMENT_DB="$ROOT/var/experiments.sqlite"
# The worker owns this db; the API reads it so the heartbeat check is real.
export FIBOKI_WORKER_HEARTBEAT="${FIBOKI_WORKER_HEARTBEAT:-$HOME/.fiboki/state.db}"
export FIBOKI_ALERT_LOG="$ROOT/var/alerts.jsonl"
export FIBOKI_INCIDENT_LOG="$ROOT/var/incidents.jsonl"
export FIBOKI_BUILD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
export FIBOKI_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$ROOT/var"

echo "== Fiboki V2 local =="
echo "mode        : paper (live requires 5 controls, none set)"
echo "build       : $FIBOKI_BUILD_SHA"
echo "data root   : $FIBOKI_DATA_ROOT"
echo "login       : joe / $DEV_PASSWORD   (admin)"
echo

# ---- API
pkill -f "uvicorn.*fiboki" 2>/dev/null || true
nohup "$ROOT/.venv/bin/python" -m uvicorn "fiboki.api.app:asgi_factory" --factory \
  --host 127.0.0.1 --port 8000 > "$ROOT/var/api.log" 2>&1 &
echo "api  -> http://127.0.0.1:8000  (log: var/api.log)"

# ---- Research worker (separate PROCESS, not a thread inside the API)
pkill -f "fiboki worker run" 2>/dev/null || true
nohup "$ROOT/.venv/bin/fiboki" worker run research --nice 10 > "$ROOT/var/worker.log" 2>&1 &
echo "wkr  -> research worker            (log: var/worker.log)"

# ---- Web
cd apps/web
[ -d node_modules ] || npm install --silent
# Same-origin: Next proxies /api/* to the backend (see next.config.ts rewrites).
# An empty base makes the client use relative URLs, so no CORS and no
# private-network request from the page context.
export NEXT_PUBLIC_FIBOKI_API=""
export FIBOKI_API_PROXY_TARGET="http://127.0.0.1:8000"
pkill -f "next dev" 2>/dev/null || true
nohup npm run dev > "$ROOT/var/web.log" 2>&1 &
echo "web  -> http://localhost:3000    (log: var/web.log)"
echo
echo "Open http://localhost:3000 and sign in as joe."
