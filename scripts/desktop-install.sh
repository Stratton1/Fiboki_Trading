#!/usr/bin/env bash
# Fiboki V2: provision this Mac to run Fiboki continuously. Idempotent: safe to re-run.
#
#   scripts/desktop-install.sh              # check, build what is missing, report
#   scripts/desktop-install.sh --check      # report only; change nothing
#   scripts/desktop-install.sh --launchd    # also write (not load) the launchd services
#
# Steps, each skipped when already done:
#   1. Homebrew dependencies are CHECKED, never installed for you; the exact
#      `brew install` line is printed for anything missing.
#   2. .venv from Homebrew python@3.11, then `pip install -e '.[dev]'` under
#      deploy/constraints.txt (the exact pins). An existing .venv built by a
#      different interpreter, or copied from another Mac, is REFUSED with the
#      command to rebuild it; it is never deleted by this script.
#   3. apps/web: `npm ci` when node_modules is missing, stale, or was built for
#      another platform (a Linux or Intel @next/swc binary), then `npm run build`.
#   4. var/ layout and ~/.fiboki (with a chmod 600 env file holding a generated
#      session secret). The data root is CHECKED for its marker, never created:
#      marking a directory is what makes it trusted, so it is not done by default.
#   5. The desktop launcher (scripts/desktop/install-launcher.sh).
#   6. `fiboki doctor`, whose table is the summary.
# Nothing here sets FIBOKI_EXECUTION_MODE to anything but paper or touches a
# live control.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
CHECK=0
LAUNCHD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --launchd) LAUNCHD=1; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

PROBLEMS=0
say()  { printf '%-8s %s\n' "$1" "$2"; }
need() { say "MISSING" "$1 -> $2"; PROBLEMS=$((PROBLEMS + 1)); }
act()  { if [ "$CHECK" -eq 1 ]; then say "WOULD" "$*"; return 1; fi; say "DOING" "$*"; return 0; }

echo "== Fiboki desktop install ($ROOT) =="
[ "$(uname -s)" = "Darwin" ] || say "NOTE" "not macOS ($(uname -s)); launchd and Homebrew steps will not apply"

# ---- 1. Homebrew dependencies --------------------------------------------
command -v brew >/dev/null 2>&1 || need "Homebrew" '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
PY311=""
for cand in /opt/homebrew/opt/python@3.11/bin/python3.11 /usr/local/opt/python@3.11/bin/python3.11 "$(command -v python3.11 || true)"; do
  if [ -n "$cand" ] && [ -x "$cand" ]; then PY311="$cand"; break; fi
done
[ -n "$PY311" ] || need "python3.11" "brew install python@3.11"
command -v node >/dev/null 2>&1 || need "node" "brew install node"
command -v git >/dev/null 2>&1 || need "git" "brew install git"
command -v sqlite3 >/dev/null 2>&1 || need "sqlite3" "brew install sqlite"
if command -v llama-server >/dev/null 2>&1; then
  say "OK" "llama-server: $(llama-server --version 2>&1 | head -1)"
else
  say "OPTIONAL" "llama-server not installed (local agents) -> brew install llama.cpp"
fi

# ---- 2. Python environment -------------------------------------------------
if [ -f .venv/pyvenv.cfg ]; then
  base="$(sed -n 's/^home *= *//p' .venv/pyvenv.cfg)"
  ver="$(sed -n 's/^version_info *= *//p; s/^version *= *//p' .venv/pyvenv.cfg | head -1)"
  if [ ! -d "$base" ] || [ "${ver#3.11}" = "$ver" ]; then
    need ".venv usable" "it was built by '$base' (Python $ver). Rebuild: mv .venv .venv.broken && $0"
  else
    say "OK" ".venv (Python $ver)"
  fi
elif [ -n "$PY311" ]; then
  if act "create .venv with $PY311"; then "$PY311" -m venv .venv; fi
fi
if [ -x .venv/bin/python ]; then
  if .venv/bin/python - <<'PY' >/dev/null 2>&1
import importlib.metadata as m, sys, tomllib
deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
pins = dict(d.split("==") for d in deps if "==" in d)
sys.exit(0 if all(m.version(n) == v for n, v in pins.items()) else 1)
PY
  then
    say "OK" "Python dependencies at their exact pins"
  elif act "pip install -e '.[dev]' -c deploy/constraints.txt"; then
    .venv/bin/python -m pip install -q --upgrade pip
    .venv/bin/python -m pip install -q -e ".[dev]" -c deploy/constraints.txt
  fi
fi

# ---- 3. Web workstation ----------------------------------------------------
if command -v npm >/dev/null 2>&1; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) swc="swc-darwin-arm64" ;;
    Darwin-x86_64) swc="swc-darwin-x64" ;;
    Linux-x86_64) swc="swc-linux-x64-gnu" ;;
    Linux-aarch64|Linux-arm64) swc="swc-linux-arm64-gnu" ;;
    *) swc="" ;;
  esac
  nm="apps/web/node_modules"
  reason=""
  if [ ! -d "$nm" ]; then reason="missing"
  elif [ -n "$swc" ] && [ -d "$nm/@next" ] && [ ! -d "$nm/@next/$swc" ]; then
    reason="built for another platform ($(ls "$nm/@next" | grep '^swc-' | tr '\n' ' '))"
  elif [ apps/web/package-lock.json -nt "$nm/.package-lock.json" ]; then reason="older than package-lock.json"
  fi
  if [ -n "$reason" ]; then
    if act "npm ci in apps/web (node_modules $reason)"; then (cd apps/web && npm ci --no-audit --no-fund); fi
  else
    say "OK" "apps/web/node_modules ($swc)"
  fi
  if [ ! -f apps/web/.next/BUILD_ID ] || [ -n "$reason" ]; then
    if act "npm run build in apps/web (the web service runs next start)"; then
      # Same build-time settings as scripts/fiboki-service.sh web.
      if ! (cd apps/web && NEXT_PUBLIC_FIBOKI_API="" FIBOKI_API_PROXY_TARGET="http://127.0.0.1:8000" npm run build); then
        need "web build" "npm run build failed; see the output above (docs/v2/OPERATIONS.md, desktop runbook)"
      fi
    fi
  else
    say "OK" "apps/web production build present"
  fi
fi

# ---- 4. State layout -------------------------------------------------------
FHOME="${FIBOKI_HOME:-$HOME/.fiboki}"
for d in var var/logs var/agents var/research var/news var/paper "$FHOME"; do
  if [ ! -d "$d" ]; then act "mkdir -p $d" && mkdir -p "$d"; fi
done
if [ ! -f "$FHOME/env" ]; then
  if act "create $FHOME/env (chmod 600) with a generated FIBOKI_SESSION_SECRET"; then
    umask 077
    {
      echo "# Fiboki desktop environment. Read by scripts/fiboki-service.sh. NEVER commit this file."
      echo "# It is excluded from scripts/backup.sh archives; keep a copy in your password manager."
      echo "FIBOKI_SESSION_SECRET=$(openssl rand -hex 32)"
      echo "# Operators: user:role:sha256(password). Compute the hash WITHOUT leaving the password"
      echo "# in shell history:  read -rs PW && printf '%s' \"\$PW\" | shasum -a 256 && unset PW"
      echo "# FIBOKI_OPERATORS=joe:admin:<sha256>,tom:operator:<sha256>"
      echo "# Local agents (after scripts/llama-server.sh is running):"
      echo "# FIBOKI_AGENT_PROVIDER=local"
      echo "# FIBOKI_AGENT_LOCAL_URL=http://127.0.0.1:8080"
      echo "# FIBOKI_AGENT_LOCAL_MODEL=qwen3-32b-q5_k_m"
      echo "# FIBOKI_AGENT_CYCLES=true"
      echo "# FIBOKI_AGENT_CYCLE_TARGET=donchian_breakout_atr:XAUUSD:H4"
      echo "# LLAMA_SERVER_ARGS=--tier 64"
    } > "$FHOME/env"
    chmod 600 "$FHOME/env"
  fi
else
  perms="$(stat -c '%a' "$FHOME/env" 2>/dev/null || stat -f '%Lp' "$FHOME/env")"
  if [ "$perms" != "600" ]; then need "$FHOME/env mode 600" "chmod 600 $FHOME/env (it is $perms)"; else say "OK" "$FHOME/env (600)"; fi
fi
DATA_ROOT="${FIBOKI_DATA_ROOT:-$ROOT/var/datastore}"
if [ -f "$DATA_ROOT/.fiboki-data-root" ]; then
  say "OK" "data root $DATA_ROOT is marked"
else
  need "marked data root at $DATA_ROOT" "copy the MIGRATED store from the old Mac (docs/v2/DEPLOYMENT.md §2) or run scripts/migrate-store.sh; never mark a directory by hand"
fi

# ---- 5. Launchers ----------------------------------------------------------
if [ "$(uname -s)" = "Darwin" ]; then
  if [ ! -f "$HOME/Desktop/Start Fiboki.command" ]; then
    act "install the desktop launcher" && scripts/desktop/install-launcher.sh
  else
    say "OK" "desktop launcher installed"
  fi
  if [ "$LAUNCHD" -eq 1 ]; then
    act "write launchd services (not loaded)" && scripts/launchd-install.sh
  fi
fi

# ---- 6. Doctor ---------------------------------------------------------------
echo
if [ -x .venv/bin/fiboki ]; then
  .venv/bin/fiboki doctor --no-hash || true
fi
echo
if [ "$PROBLEMS" -gt 0 ]; then
  echo "$PROBLEMS item(s) need you (MISSING above). Re-run this script after fixing them."
  exit 1
fi
echo "Install steps complete. FAIL rows in the doctor table above are what remains."
