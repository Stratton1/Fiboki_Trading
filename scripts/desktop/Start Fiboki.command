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

# Optional local model. The research runtime reads FIBOKI_AGENT_PROVIDER,
# FIBOKI_AGENT_LOCAL_URL and FIBOKI_AGENT_LOCAL_MODEL (all declared in
# ENV_REGISTRY). llama.cpp is recognised by GET /props, which Ollama does not
# serve (llama-server also answers /v1/models, so that alone proves nothing).
# The model name is taken from the server only when you have not set one.
# Agent cycles are switched on only when FIBOKI_AGENT_CYCLE_TARGET is set: the
# runtime refuses to compose a nightly cycle without a target, and that refusal
# would stop the research worker. With no model found agents stay on the
# offline EchoProvider and the platform runs exactly as before.
_model_from() {  # $1 = JSON on stdin shape: "llama" (/v1/models) or "ollama" (/api/tags)
  /usr/bin/python3 -c 'import json,sys
d=json.load(sys.stdin); k=sys.argv[1]
rows=d.get("data") if k=="llama" else d.get("models")
names=[r.get("id") if k=="llama" else r.get("name") for r in (rows or [])]
print(names[0] if len(names)==1 and names[0] else "")' "$1" 2>/dev/null || true
}
LLM_URL=""
LLM_KIND=""
for cand in ${FIBOKI_AGENT_LOCAL_URL:-http://127.0.0.1:8080 http://127.0.0.1:11434}; do
  props="$(curl -fsS --max-time 2 "$cand/props" 2>/dev/null || true)"
  if [ "${props#*default_generation_settings}" != "$props" ]; then
    LLM_URL="$cand"; LLM_KIND="llama.cpp"; break
  elif curl -fsS --max-time 2 "$cand/api/tags" >/dev/null 2>&1; then
    LLM_URL="$cand"; LLM_KIND="Ollama"; break
  fi
done
if [ -n "$LLM_URL" ]; then
  if [ -z "${FIBOKI_AGENT_LOCAL_MODEL:-}" ]; then
    if [ "$LLM_KIND" = "llama.cpp" ]; then
      FIBOKI_AGENT_LOCAL_MODEL="$(curl -fsS --max-time 2 "$LLM_URL/v1/models" | _model_from llama)" || true
    else
      FIBOKI_AGENT_LOCAL_MODEL="$(curl -fsS --max-time 2 "$LLM_URL/api/tags" | _model_from ollama)" || true
    fi
  fi
  export FIBOKI_AGENT_PROVIDER="${FIBOKI_AGENT_PROVIDER:-local}"
  export FIBOKI_AGENT_LOCAL_URL="$LLM_URL"
  export FIBOKI_AGENT_LOCAL_MODEL="${FIBOKI_AGENT_LOCAL_MODEL:-}"
  if [ -z "$FIBOKI_AGENT_LOCAL_MODEL" ]; then
    echo "local model : $LLM_KIND at $LLM_URL, but no single model to name; set FIBOKI_AGENT_LOCAL_MODEL"
    echo "              (agent cycles stay OFF)"
    unset FIBOKI_AGENT_PROVIDER FIBOKI_AGENT_LOCAL_MODEL
  elif [ -n "${FIBOKI_AGENT_CYCLE_TARGET:-}" ]; then
    export FIBOKI_AGENT_CYCLES="${FIBOKI_AGENT_CYCLES:-true}"
    echo "local model : $LLM_KIND at $LLM_URL, model $FIBOKI_AGENT_LOCAL_MODEL (agent cycles ON)"
  else
    echo "local model : $LLM_KIND at $LLM_URL, model $FIBOKI_AGENT_LOCAL_MODEL"
    echo "              agent cycles OFF: set FIBOKI_AGENT_CYCLE_TARGET (strategy:INSTRUMENT:TF) to enable"
  fi
  echo "              check it: .venv/bin/fiboki doctor model"
else
  echo "local model : none detected (agents run offline; scripts/llama-server.sh starts one)"
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
