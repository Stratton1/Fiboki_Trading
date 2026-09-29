#!/usr/bin/env bash
# Fiboki V2: run ONE desktop service in the foreground. launchd calls this
# (deploy/launchd/uk.fiboki.<service>.plist); you can too, to debug one.
#
#   scripts/fiboki-service.sh api|worker|web|news|llama|paper
#
# Environment, in order of precedence:
#   1. ~/.fiboki/env (operator-owned, chmod 600, never in the repository):
#      secrets such as FIBOKI_SESSION_SECRET and FIBOKI_OPERATORS, and the
#      agent settings (FIBOKI_AGENT_*). scripts/desktop-install.sh creates it.
#   2. The desktop defaults below, the same paths scripts/dev-up.sh uses, so
#      `fiboki doctor` resolves them identically.
#
# `paper` runs `fiboki paper forward` against the COMMITTED wiring file below
# (not an environment variable: which strategy trades, where, under which
# limits, is a reviewed file, and its sha256 is stamped on every decision). It
# needs FIBOKI_OANDA_PRACTICE_TOKEN and FIBOKI_OANDA_PRACTICE_ACCOUNT_ID in ~/.fiboki/env.
#
# Paper only, enforced here: FIBOKI_EXECUTION_MODE is forced to paper and the
# live controls are unset AFTER the env file is read, so a line in that file
# cannot arm anything. Nothing in this script can set a live control.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
# Node installed through nvm is not on launchd's PATH: add the newest nvm node.
if [ -d "$HOME/.nvm/versions/node" ]; then
  NVM_NODE="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)"
  [ -n "$NVM_NODE" ] && export PATH="$NVM_NODE:$PATH"
fi

SERVICE="${1:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${FIBOKI_HOME:-$HOME/.fiboki}/env"
if [ -f "$ENV_FILE" ]; then
  # Read KEY=VALUE lines WITHOUT shell expansion: operator password hashes are
  # scrypt strings full of `$`, and sourcing the file would expand them into
  # nothing (or abort under `set -u`). Values may be wrapped in single or
  # double quotes; blank lines and # comments are ignored.
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"; value="${line#*=}"
    case "$key" in *[!A-Z0-9_]*|'') echo "fiboki-service: ignoring malformed line in $ENV_FILE: ${line%%=*}=..." >&2; continue ;; esac
    case "$value" in
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
    esac
    export "$key=$value"
  done < "$ENV_FILE"
fi

# ---- paper only (after the env file, deliberately) ------------------------
export FIBOKI_EXECUTION_MODE=paper
unset FIBOKI_LIVE_EXECUTION_ENABLED FIBOKI_LIVE_RUNTIME_ARMED FIBOKI_OANDA_LIVE_RUNTIME || true

# ---- desktop defaults (dev-up.sh uses the same) ---------------------------
export FIBOKI_HOME="${FIBOKI_HOME:-$HOME/.fiboki}"
export FIBOKI_STATE_DIR="${FIBOKI_STATE_DIR:-$ROOT/var}"
export FIBOKI_DATA_ROOT="${FIBOKI_DATA_ROOT:-$FIBOKI_STATE_DIR/datastore}"
export FIBOKI_EXPERIMENT_DB="${FIBOKI_EXPERIMENT_DB:-$FIBOKI_STATE_DIR/experiments.sqlite}"
export FIBOKI_PAPER_ROOT="${FIBOKI_PAPER_ROOT:-$FIBOKI_STATE_DIR/paper}"
export FIBOKI_ALERT_LOG="${FIBOKI_ALERT_LOG:-$FIBOKI_STATE_DIR/alerts.jsonl}"
export FIBOKI_STATE_DB="${FIBOKI_STATE_DB:-$FIBOKI_HOME/state.db}"
export FIBOKI_WORKER_HEARTBEAT="${FIBOKI_WORKER_HEARTBEAT:-$FIBOKI_STATE_DB}"
export FIBOKI_LOG_FORMAT="${FIBOKI_LOG_FORMAT:-json}"
export FIBOKI_COOKIE_SECURE="${FIBOKI_COOKIE_SECURE:-false}"
export FIBOKI_COOKIE_SAMESITE="${FIBOKI_COOKIE_SAMESITE:-lax}"
export FIBOKI_ALLOWED_ORIGINS="${FIBOKI_ALLOWED_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}"
export FIBOKI_BUILD_SHA="${FIBOKI_BUILD_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo local)}"
mkdir -p "$FIBOKI_STATE_DIR/logs" "$FIBOKI_HOME"

case "$SERVICE" in
  api)
    exec "$ROOT/.venv/bin/python" -m uvicorn "fiboki.api.app:asgi_factory" --factory \
      --host 127.0.0.1 --port 8000
    ;;
  worker)
    exec "$ROOT/.venv/bin/fiboki" worker run research --nice 10
    ;;
  web)
    cd "$ROOT/apps/web"
    if [ ! -f .next/BUILD_ID ]; then
      echo "apps/web has no production build; run scripts/desktop-install.sh (npm run build)." >&2
      exit 78   # EX_CONFIG: launchd's ThrottleInterval keeps this from spinning
    fi
    export NEXT_PUBLIC_FIBOKI_API=""
    export FIBOKI_API_PROXY_TARGET="http://127.0.0.1:8000"
    exec npm run start
    ;;
  news)
    exec "$ROOT/.venv/bin/fiboki" news record --loop --interval 300
    ;;
  llama)
    # LLAMA_SERVER_ARGS in ~/.fiboki/env, e.g. "--tier 64" or "--model /Users/you/Models/x.gguf"
    # shellcheck disable=SC2086
    exec "$ROOT/scripts/llama-server.sh" ${LLAMA_SERVER_ARGS:-}
    ;;
  paper)
    # Hold a sleep assertion for as long as THIS pid lives (exec keeps the pid):
    # -i idle sleep, -s system sleep on AC power. It does not stop a closed
    # laptop lid from sleeping the machine; docs/v2/OPERATIONS.md says what does.
    if command -v caffeinate >/dev/null 2>&1; then
      caffeinate -is -w $$ &
    fi
    exec "$ROOT/.venv/bin/fiboki" paper forward \
      --wiring "$ROOT/src/fiboki/entrypoints/wiring/paper_forward_v1.json"
    ;;
  *)
    echo "usage: $0 api|worker|web|news|llama|paper" >&2
    exit 2
    ;;
esac
