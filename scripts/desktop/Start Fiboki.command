#!/usr/bin/env bash
# Fiboki V2 desktop launcher (macOS). Double-click from the Desktop.
#
# Starts the API, the research worker and the web workstation via
# scripts/dev-up.sh, then opens the browser. Paper mode only: none of the
# five live controls is set here and this launcher cannot set them.
#
# Location of the repository: resolved from FIBOKI_ROOT, then the path this
# launcher was installed with, then the default under ~/Documents.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="${FIBOKI_ROOT:-__FIBOKI_ROOT__}"
if [ ! -d "$ROOT" ] || [ ! -f "$ROOT/scripts/dev-up.sh" ]; then
  ROOT="$HOME/Documents/Claude/Projects/Fiboki"
fi
if [ ! -f "$ROOT/scripts/dev-up.sh" ]; then
  echo "Fiboki repository not found. Set FIBOKI_ROOT or reinstall the launcher:"
  echo "  scripts/desktop/install-launcher.sh"
  read -r -p "Press return to close." _
  exit 1
fi
cd "$ROOT"

echo "== Fiboki V2 =="
echo "repo   : $ROOT"
echo "branch : $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown) @ $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo

# First-run provisioning that dev-up.sh assumes is already done.
if [ ! -x .venv/bin/python ]; then
  echo "Creating Python environment (.venv) ..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e ".[dev]"
fi
if [ ! -d apps/web/node_modules ]; then
  echo "Installing web dependencies (npm ci) ..."
  (cd apps/web && npm ci --silent --no-audit --no-fund)
fi

# Optional local model. If a llama.cpp or Ollama server is listening, the agent
# research cycles are enabled against it; otherwise agents stay on the offline
# EchoProvider and the platform runs exactly as before.
LLM_URL="${FIBOKI_LLM_URL:-http://127.0.0.1:8080}"
if curl -fsS --max-time 2 "$LLM_URL/v1/models" >/dev/null 2>&1; then
  export FIBOKI_AGENT_PROVIDER="${FIBOKI_AGENT_PROVIDER:-local}"
  export FIBOKI_AGENT_CYCLES="${FIBOKI_AGENT_CYCLES:-true}"
  export FIBOKI_LLM_URL="$LLM_URL"
  echo "local model : $LLM_URL (agent cycles ON)"
elif curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  export FIBOKI_AGENT_PROVIDER="${FIBOKI_AGENT_PROVIDER:-local}"
  export FIBOKI_AGENT_CYCLES="${FIBOKI_AGENT_CYCLES:-true}"
  export FIBOKI_LLM_URL="http://127.0.0.1:11434"
  echo "local model : Ollama at 127.0.0.1:11434 (agent cycles ON)"
else
  echo "local model : none detected (agents run offline; start llama.cpp or Ollama first to enable)"
fi
echo

scripts/dev-up.sh

# dev-up.sh prints the URL and returns once the processes are up; keep the
# window open so the operator can see the log tail and stop cleanly.
sleep 3
open "http://localhost:3000" 2>/dev/null || true
echo
echo "Platform is running. Logs: $ROOT/var/{api,worker,web}.log"
echo "Stop everything: scripts/dev-down.sh (or close this window and run it later)."
echo
read -r -p "Press return to stop Fiboki and close this window." _
if [ -x scripts/dev-down.sh ]; then scripts/dev-down.sh; fi
