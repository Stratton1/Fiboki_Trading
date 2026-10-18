#!/usr/bin/env bash
# Fiboki V2: bootstrap a fresh Debian 12 / Ubuntu 24.04 server to run Fiboki 24/7 under systemd.
# Run as root. Idempotent: re-running converges, it never deletes state.
#
#   sudo scripts/server-install.sh                       # install + start api, worker, web, news
#   sudo scripts/server-install.sh --dry-run             # print every action, change nothing
#   sudo scripts/server-install.sh --services api,worker,web,news,paper-crypto
#   sudo scripts/server-install.sh --no-start            # install and enable, start nothing (before a migration)
#   sudo scripts/server-install.sh --tailscale           # also install Tailscale and `tailscale serve` the web app
#   sudo scripts/server-install.sh --build-llama [TAG]   # build llama.cpp (CPU) into /opt/llama.cpp
#   scripts/server-install.sh --render-units DIR         # as anyone: write the filled-in units to DIR, nothing else
#
# Services: api worker web news quotes paper paper-crypto llama (fiboki-<name>.service). Default start set:
# api,worker,web,news. quotes, paper, paper-crypto and llama are opt-in (they need credentials or a model).
# The nightly backup timer and the five-minute heartbeat timer are always installed and enabled.
#
# Other flags:
#   --repo-url URL        default https://github.com/Stratton1/Fiboki_Trading.git
#   --branch NAME         default main
#   --deploy-key FILE     a READ-ONLY deploy key for a private repository (use an ssh:// or git@ URL with it)
#   --prefix DIR          default /opt/fiboki          --user NAME   default fiboki
#   --ssh-port N          default: what sshd reports, else 22
#   --swap-size SIZE      default 4G (created only when the machine has no swap)
#   --python PATH         use this Python 3.11 (default: system python3.11, else uv-managed 3.11)
#   --upgrade-node        re-run the NodeSource setup even if node is installed
#   --skip-web-build      do not run npm ci / npm run build
#   --no-firewall --no-swap --no-unattended-upgrades --no-journald-cap --no-logrotate   skip that step
#
# What it does, in order (each step says what it will do, and skips what is already done):
#   1 apt packages            2 service user + directories     3 Python 3.11 + node LTS
#   4 clone / fast-forward /opt/fiboki   5 .venv + `pip install -e '.[dev]' -c deploy/constraints.txt`
#   6 apps/web: npm ci + build           7 /etc/fiboki/env (created ONCE, mode 600, generated session secret)
#   8 systemd units, slice, timers       9 ufw (deny incoming except SSH), unattended-upgrades,
#   journald size cap, logrotate, swap   10 start, wait for the API, `fiboki doctor`
# Paper only: every unit runs scripts/fiboki-service.sh, which forces FIBOKI_EXECUTION_MODE=paper and
# unsets every live control. The web app and the API bind 127.0.0.1; nothing here opens a port but SSH.
# Never prints a secret: the env file is written without echoing it, and its contents are never shown.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_ROOT="$(cd "$SELF_DIR/.." && pwd)"

DRY=0; NO_START=0; TAILSCALE=0; BUILD_LLAMA=0; LLAMA_REF=""
SERVICES="api,worker,web,news"
REPO_URL="https://github.com/Stratton1/Fiboki_Trading.git"
BRANCH="main"
DEPLOY_KEY=""
PREFIX="/opt/fiboki"
SVC_USER="fiboki"
LIB="/var/lib/fiboki"
ENV_DIR="/etc/fiboki"
ENV_FILE="/etc/fiboki/env"
BACKUP_DIR="/var/backups/fiboki"
SSH_PORT=""
SWAP_SIZE="4G"
PYTHON_BIN=""
NODE_MAJOR="${NODE_MAJOR:-22}"
UPGRADE_NODE=0; SKIP_WEB=0
DO_FIREWALL=1; DO_SWAP=1; DO_UNATTENDED=1; DO_JOURNALD=1; DO_LOGROTATE=1
RENDER_DIR=""
UV_VERSION="0.8.4"

ALL_SERVICES="api worker web news quotes paper paper-crypto llama"

usage() { sed -n '2,36p' "$0"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --services) SERVICES="${2:?}"; shift 2 ;;
    --tailscale) TAILSCALE=1; shift ;;
    --build-llama)
      BUILD_LLAMA=1; shift
      if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then LLAMA_REF="$1"; shift; fi ;;
    --repo-url) REPO_URL="${2:?}"; shift 2 ;;
    --branch) BRANCH="${2:?}"; shift 2 ;;
    --deploy-key) DEPLOY_KEY="${2:?}"; shift 2 ;;
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --user) SVC_USER="${2:?}"; shift 2 ;;
    --ssh-port) SSH_PORT="${2:?}"; shift 2 ;;
    --swap-size) SWAP_SIZE="${2:?}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?}"; shift 2 ;;
    --upgrade-node) UPGRADE_NODE=1; shift ;;
    --skip-web-build) SKIP_WEB=1; shift ;;
    --no-firewall) DO_FIREWALL=0; shift ;;
    --no-swap) DO_SWAP=0; shift ;;
    --no-unattended-upgrades) DO_UNATTENDED=0; shift ;;
    --no-journald-cap) DO_JOURNALD=0; shift ;;
    --no-logrotate) DO_LOGROTATE=0; shift ;;
    --render-units) RENDER_DIR="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for p in "$PREFIX" "$LIB" "$ENV_FILE" "$BACKUP_DIR" "$SVC_USER"; do
  case "$p" in *'#'*|*'&'*|*'\'*|*' '*) echo "'$p' contains a character the unit substitution cannot handle safely" >&2; exit 2 ;; esac
done
IFS=',' read -r -a SVC_LIST <<< "$SERVICES"
for s in "${SVC_LIST[@]}"; do
  case " $ALL_SERVICES " in *" $s "*) ;; *) echo "unknown service '$s' (services: $ALL_SERVICES)" >&2; exit 2 ;; esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
run() {   # run CMD...: print it; execute unless --dry-run
  if [ "$DRY" -eq 1 ]; then printf 'DRY  %s\n' "$*"; return 0; fi
  "$@"
}
as_svc() {   # as_svc CMD...: run as the service user, with ITS home (pip and npm caches live there)
  if [ "$DRY" -eq 1 ]; then printf 'DRY  (as %s) %s\n' "$SVC_USER" "$*"; return 0; fi
  runuser -u "$SVC_USER" -- env HOME="$LIB" "$@"
}
svc_out() {  # svc_out CMD...: as_svc, but prints nothing and succeeds in --dry-run (for values we read)
  [ "$DRY" -eq 1 ] && return 0
  runuser -u "$SVC_USER" -- env HOME="$LIB" "$@"
}

# ---- render units (no root, no side effects) ---------------------------------
render() {   # render SRC DEST
  sed -e "s#__FIBOKI_ROOT__#$PREFIX#g" -e "s#__FIBOKI_USER__#$SVC_USER#g" -e "s#__FIBOKI_GROUP__#$SVC_USER#g" \
      -e "s#__FIBOKI_LIB__#$LIB#g" -e "s#__FIBOKI_ENV_FILE__#$ENV_FILE#g" -e "s#__FIBOKI_BACKUP_DIR__#$BACKUP_DIR#g" \
      "$1" > "$2.tmp"
  if grep -q '__FIBOKI_' "$2.tmp"; then echo "unsubstituted placeholder left in $2" >&2; rm -f "$2.tmp"; exit 1; fi
  mv "$2.tmp" "$2"
}
unit_src() {   # the installed code's templates win over this script's own checkout
  if [ -d "$PREFIX/deploy/systemd" ]; then printf '%s' "$PREFIX/deploy/systemd"; else printf '%s' "$SRC_ROOT/deploy/systemd"; fi
}
render_all() {   # render_all DIR
  local dir="$1" f src; src="$(unit_src)"
  mkdir -p "$dir"
  for f in "$src"/fiboki-*.service "$src"/fiboki-*.timer "$src"/fiboki.slice; do
    [ -f "$f" ] || continue
    case "$(basename "$f")" in fiboki-research-worker.service) continue ;; esac   # the superseded single-unit file
    render "$f" "$dir/$(basename "$f")"
    say "wrote    $dir/$(basename "$f")"
  done
  ctl_wrapper > "$dir/fiboki-ctl"
  chmod 755 "$dir/fiboki-ctl"
  say "wrote    $dir/fiboki-ctl"
}
ctl_wrapper() {
  cat <<EOF
#!/bin/sh
# fiboki-ctl: run a command with the SERVER's Fiboki environment, as the service user.
#   fiboki-ctl fiboki doctor          fiboki-ctl fiboki worker status          fiboki-ctl fiboki quotes status
# Reads $ENV_FILE exactly as the services do (no shell expansion), forces paper mode. Written by scripts/server-install.sh.
if [ "\$(id -un)" = "$SVC_USER" ]; then
  exec env HOME=$LIB FIBOKI_HOME=$LIB/.fiboki FIBOKI_STATE_DIR=$LIB/var FIBOKI_ENV_FILE=$ENV_FILE \\
    /bin/bash $PREFIX/scripts/fiboki-service.sh run "\$@"
fi
exec sudo -u $SVC_USER -H env HOME=$LIB FIBOKI_HOME=$LIB/.fiboki FIBOKI_STATE_DIR=$LIB/var FIBOKI_ENV_FILE=$ENV_FILE \\
  /bin/bash $PREFIX/scripts/fiboki-service.sh run "\$@"
EOF
}
if [ -n "$RENDER_DIR" ]; then
  render_all "$RENDER_DIR"
  exit 0
fi

# ---- preflight --------------------------------------------------------------
step "Preflight"
if [ "$(id -u)" -ne 0 ] && [ "$DRY" -eq 0 ]; then echo "run as root (sudo scripts/server-install.sh), or add --dry-run" >&2; exit 1; fi
[ -r /etc/os-release ] && . /etc/os-release || { ID=unknown; VERSION_ID=unknown; }
case "${ID:-}:${VERSION_ID:-}" in
  debian:12|ubuntu:24.04) note "OS: ${PRETTY_NAME:-$ID $VERSION_ID}" ;;
  debian:*|ubuntu:*) note "OS: ${PRETTY_NAME:-$ID $VERSION_ID} (tested on Debian 12 and Ubuntu 24.04; continuing)" ;;
  *) echo "unsupported OS '${ID:-?} ${VERSION_ID:-?}': this script is for Debian 12 or Ubuntu 24.04" >&2; [ "$DRY" -eq 1 ] || exit 1 ;;
esac
note "arch: $(uname -m), memory: $(awk '/MemTotal/ {printf "%.1f GB", $2/1048576}' /proc/meminfo 2>/dev/null || echo '?')"
if [ ! -d /run/systemd/system ] && [ "$DRY" -eq 0 ]; then echo "this host is not running systemd" >&2; exit 1; fi
[ "$DRY" -eq 1 ] && note "DRY RUN: nothing below is executed"
if [ "$(id -u)" -eq 0 ] && [ "$DRY" -eq 0 ]; then export DEBIAN_FRONTEND=noninteractive; fi

# ---- 1 apt packages -----------------------------------------------------------
step "1  apt packages"
PKGS="ca-certificates curl gnupg git sqlite3 rsync jq build-essential pkg-config python3 python3-venv python3-dev unzip iproute2 logrotate"
[ "$DO_FIREWALL" -eq 1 ] && PKGS="$PKGS ufw"
[ "$DO_UNATTENDED" -eq 1 ] && PKGS="$PKGS unattended-upgrades apt-listchanges"
[ "$BUILD_LLAMA" -eq 1 ] && PKGS="$PKGS cmake"
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  run apt-get update -y
  # shellcheck disable=SC2086
  run apt-get install -y --no-install-recommends $missing
else
  note "all present"
fi

# ---- 2 user and directories ---------------------------------------------------
step "2  service user and directories"
if id "$SVC_USER" >/dev/null 2>&1; then
  note "user $SVC_USER exists"
else
  run useradd --system --home-dir "$LIB" --create-home --shell /usr/sbin/nologin --user-group "$SVC_USER"
fi
for d in "$LIB" "$LIB/.fiboki" "$LIB/var" "$LIB/var/logs" "$BACKUP_DIR"; do
  [ -d "$d" ] || run install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$d"
done
[ -d "$ENV_DIR" ] || run install -d -m 0750 -o root -g "$SVC_USER" "$ENV_DIR"
if [ ! -d "$PREFIX" ]; then
  run install -d -m 0755 -o "$SVC_USER" -g "$SVC_USER" "$PREFIX"
elif [ "$(stat -c %U "$PREFIX")" != "$SVC_USER" ]; then
  # Someone cloned or copied the repository here as root (the bootstrap path in docs/v2/SERVER.md).
  run chown -R "$SVC_USER:$SVC_USER" "$PREFIX"
fi

# ---- 3 Python 3.11 and Node LTS -----------------------------------------------
step "3  Python 3.11 and Node.js $NODE_MAJOR LTS"
PY311=""
if [ -n "$PYTHON_BIN" ]; then
  PY311="$PYTHON_BIN"
elif command -v python3.11 >/dev/null 2>&1; then
  PY311="$(command -v python3.11)"
elif python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)' 2>/dev/null; then
  PY311="$(command -v python3)"
fi
if [ -n "$PY311" ]; then
  note "Python 3.11: $PY311"
else
  note "no system Python 3.11 (this OS ships $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)); installing a uv-managed 3.11 under /opt/python"
  if [ ! -x /opt/uv/bin/uv ]; then
    run python3 -m venv /opt/uv
    run /opt/uv/bin/pip install --quiet "uv==$UV_VERSION"
  fi
  run env UV_PYTHON_INSTALL_DIR=/opt/python /opt/uv/bin/uv python install 3.11
  if [ "$DRY" -eq 1 ]; then PY311="/opt/python/cpython-3.11.x/bin/python3.11"
  else PY311="$(find /opt/python -maxdepth 3 -path '*cpython-3.11*' -name python3.11 2>/dev/null | sort | tail -1)"; fi
  [ -n "$PY311" ] || { echo "uv installed no Python 3.11 under /opt/python" >&2; exit 1; }
  run chmod -R a+rX /opt/python
fi

have_node=0
if command -v node >/dev/null 2>&1 && [ "$UPGRADE_NODE" -eq 0 ]; then
  nv="$(node --version | sed 's/^v//; s/\..*//')"
  [ "$nv" -ge 20 ] && have_node=1
  note "node $(node --version) present"
fi
if [ "$have_node" -eq 0 ]; then
  # NodeSource's documented manual method: a signed apt repository, no pipe-to-shell.
  run install -d -m 0755 /etc/apt/keyrings
  if [ "$DRY" -eq 1 ]; then
    say "DRY  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg"
    say "DRY  write /etc/apt/sources.list.d/nodesource.list (node_${NODE_MAJOR}.x, signed-by /etc/apt/keyrings/nodesource.gpg)"
  else
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list
  fi
  run apt-get update -y
  run apt-get install -y nodejs
fi

# ---- 4 the code -----------------------------------------------------------------
step "4  code: $REPO_URL ($BRANCH) -> $PREFIX"
GIT_ENV=()
if [ -n "$DEPLOY_KEY" ]; then
  [ -r "$DEPLOY_KEY" ] || { echo "deploy key $DEPLOY_KEY is not readable" >&2; exit 1; }
  # The key is read by the service user: keep a copy it owns, mode 600, outside the repository.
  if [ "$DRY" -eq 0 ]; then
    install -m 0600 -o "$SVC_USER" -g "$SVC_USER" "$DEPLOY_KEY" "$ENV_DIR/deploy_key"
    chmod 0750 "$ENV_DIR"
  fi
  GIT_ENV=(env "GIT_SSH_COMMAND=ssh -i $ENV_DIR/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new")
fi
if [ -d "$PREFIX/.git" ]; then
  if [ -n "$(svc_out git -C "$PREFIX" status --porcelain --untracked-files=no 2>/dev/null || true)" ]; then
    note "WARNING: $PREFIX has local changes to tracked files; not touching the checkout"
  else
    if as_svc "${GIT_ENV[@]}" git -C "$PREFIX" fetch --quiet origin "$BRANCH"; then
      as_svc git -C "$PREFIX" checkout --quiet "$BRANCH"
      as_svc git -C "$PREFIX" merge --ff-only --quiet "origin/$BRANCH" || note "WARNING: cannot fast-forward $BRANCH (local commits?); left as it is"
    else
      note "WARNING: cannot fetch origin (a private repository without --deploy-key?). Using the checkout in $PREFIX exactly as it is."
    fi
  fi
else
  if [ -n "$(ls -A "$PREFIX" 2>/dev/null || true)" ]; then
    echo "$PREFIX exists, is not empty and is not a git checkout; refusing to clone over it" >&2; exit 1
  fi
  as_svc "${GIT_ENV[@]}" git clone --branch "$BRANCH" "$REPO_URL" "$PREFIX"
fi
if [ "$DRY" -eq 0 ]; then note "HEAD: $(svc_out git -C "$PREFIX" rev-parse --short HEAD)"; fi

# ---- 5 python environment ---------------------------------------------------------
step "5  .venv and the pinned dependencies"
if [ -f "$PREFIX/.venv/pyvenv.cfg" ] && "$PREFIX/.venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)' 2>/dev/null; then
  note ".venv exists (Python 3.11)"
else
  if [ -e "$PREFIX/.venv" ]; then
    echo "$PREFIX/.venv exists but is not a working Python 3.11 venv. Move it aside (mv .venv .venv.broken) and re-run." >&2; exit 1
  fi
  as_svc "$PY311" -m venv "$PREFIX/.venv"
fi
as_svc "$PREFIX/.venv/bin/python" -m pip install --quiet --upgrade pip
as_svc "$PREFIX/.venv/bin/python" -m pip install --quiet -e "$PREFIX[dev]" -c "$PREFIX/deploy/constraints.txt"
as_svc "$PREFIX/.venv/bin/python" -m compileall -q "$PREFIX/src" || true
if [ "$DRY" -eq 0 ]; then
  (cd "$PREFIX" && as_svc "$PREFIX/.venv/bin/python" scripts/lockfile.py verify --warn) || note "lockfile check could not run (non-fatal)"
fi

# ---- 6 web build ----------------------------------------------------------------------
step "6  operator web app"
if [ "$SKIP_WEB" -eq 1 ]; then
  note "skipped (--skip-web-build)"
else
  if [ ! -d "$PREFIX/apps/web/node_modules" ] || [ "$PREFIX/apps/web/package-lock.json" -nt "$PREFIX/apps/web/node_modules/.package-lock.json" ]; then
    as_svc env -C "$PREFIX/apps/web" npm ci --no-audit --no-fund
  else
    note "node_modules current"
  fi
  head_now="$(svc_out git -C "$PREFIX" rev-parse HEAD 2>/dev/null || echo none)"
  built="$(cat "$PREFIX/apps/web/.next/.fiboki-built-from" 2>/dev/null || echo none)"
  if [ ! -f "$PREFIX/apps/web/.next/BUILD_ID" ] || [ "$head_now" != "$built" ]; then
    # The same build-time settings as scripts/fiboki-service.sh web.
    as_svc env -C "$PREFIX/apps/web" NEXT_PUBLIC_FIBOKI_API="" FIBOKI_API_PROXY_TARGET="http://127.0.0.1:8000" npm run build
    [ "$DRY" -eq 1 ] || printf '%s' "$head_now" | as_svc tee "$PREFIX/apps/web/.next/.fiboki-built-from" >/dev/null
  else
    note "build current ($head_now)"
  fi
fi

# ---- 7 the env file ------------------------------------------------------------------
step "7  $ENV_FILE (secrets live here, never in the repository)"
if [ -f "$ENV_FILE" ]; then
  note "exists; not touched"
  perms="$(stat -c '%a %U' "$ENV_FILE")"
  [ "$perms" = "600 $SVC_USER" ] || { note "fixing mode/owner (was $perms)"; run chown "$SVC_USER:$SVC_USER" "$ENV_FILE"; run chmod 600 "$ENV_FILE"; }
else
  if [ "$DRY" -eq 1 ]; then
    say "DRY  write $ENV_FILE (mode 600, owner $SVC_USER) with a generated FIBOKI_SESSION_SECRET; contents never printed"
  else
    ( umask 077
      {
        echo "# Fiboki server environment. Read by scripts/fiboki-service.sh (no shell expansion: quote nothing"
        echo "# you do not have to, \$ is literal). Mode 600. NEVER commit it; it is excluded from backups, so keep"
        echo "# a copy in your password manager. Edit, then: sudo systemctl restart fiboki-api fiboki-worker ..."
        echo "FIBOKI_SESSION_SECRET=$(openssl rand -hex 32)"
        echo "# Operators (user:role:scrypt\$...); see docs/v2/SECURITY_MODEL.md. Generate on any machine with the repo:"
        echo "#   python -c \"from fiboki.api.routers.auth import hash_password as h; print(h(input()))\""
        echo "# FIBOKI_OPERATORS=joe:admin:<hash>,tom:operator:<hash>"
        echo "#"
        echo "# Behind \`tailscale serve\` (https): set both, then restart api and web."
        echo "# FIBOKI_COOKIE_SECURE=true"
        echo "# FIBOKI_ALLOWED_ORIGINS=https://<this-machine>.<your-tailnet>.ts.net"
        echo "#"
        echo "# OANDA PRACTICE (paper prices and the quote recorder). Practice only; the live host is refused."
        echo "# FIBOKI_OANDA_PRACTICE_TOKEN="
        echo "# FIBOKI_OANDA_PRACTICE_ACCOUNT_ID="
        echo "#"
        echo "# Push alerts: fiboki.obs.alerts AND the external heartbeat use these."
        echo "# FIBOKI_TELEGRAM_BOT_TOKEN="
        echo "# FIBOKI_TELEGRAM_CHAT_ID="
        echo "# FIBOKI_NTFY_URL=https://ntfy.sh/<a-long-random-topic>"
        echo "# FIBOKI_HEALTHCHECK_URL=https://hc-ping.com/<uuid>     # dead-man switch: the one alert that works when this box is down"
        echo "#"
        echo "# Backups: off-box target (yours to choose; see docs/v2/SERVER.md)"
        echo "# FIBOKI_BACKUP_RCLONE_REMOTE=b2:fiboki-backups/server1"
        echo "# FIBOKI_BACKUP_RESTIC_REPOSITORY=sftp:user@host:/srv/restic"
        echo "#"
        echo "# Local agents (after scripts/llama-server.sh has a model; fiboki-llama.service):"
        echo "# FIBOKI_AGENT_PROVIDER=local"
        echo "# FIBOKI_AGENT_LOCAL_URL=http://127.0.0.1:8080"
        echo "# FIBOKI_AGENT_LOCAL_MODEL=qwen3.5-9b-q4_k_m"
        echo "# LLAMA_SERVER_ARGS=--tier 16"
      } > "$ENV_FILE" )
    chown "$SVC_USER:$SVC_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"
    note "created (mode 600). Edit it with: sudo -e $ENV_FILE"
  fi
fi

# ---- 8 systemd -----------------------------------------------------------------------
step "8  systemd units, slice and timers"
UNIT_DIR=/etc/systemd/system
if [ "$DRY" -eq 1 ]; then
  for f in "$(unit_src)"/fiboki-*.service "$(unit_src)"/fiboki-*.timer "$(unit_src)"/fiboki.slice; do
    case "$(basename "$f")" in fiboki-research-worker.service) continue ;; esac
    say "DRY  render $(basename "$f") -> $UNIT_DIR/$(basename "$f")"
  done
  say "DRY  write /usr/local/bin/fiboki-ctl"
else
  render_all "$UNIT_DIR" >/dev/null
  install -m 0755 "$UNIT_DIR/fiboki-ctl" /usr/local/bin/fiboki-ctl && rm -f "$UNIT_DIR/fiboki-ctl"
  note "wrote units to $UNIT_DIR and /usr/local/bin/fiboki-ctl"
fi
if [ -f "$UNIT_DIR/fiboki-research-worker.service" ]; then
  note "NOTE: the older fiboki-research-worker.service is installed; fiboki-worker.service Conflicts with it. Remove it: systemctl disable --now fiboki-research-worker && rm $UNIT_DIR/fiboki-research-worker.service"
fi
run systemctl daemon-reload
for s in "${SVC_LIST[@]}"; do
  case "$s" in
    paper|quotes)
      if [ -f "$ENV_FILE" ] && ! grep -Eq '^FIBOKI_OANDA_PRACTICE_TOKEN=.+' "$ENV_FILE"; then
        note "NOTE: fiboki-$s needs FIBOKI_OANDA_PRACTICE_TOKEN / _ACCOUNT_ID in $ENV_FILE (it will fail until they are set)"
      fi ;;
  esac
done
UNITS=(); for s in "${SVC_LIST[@]}"; do UNITS+=("fiboki-$s.service"); done
run systemctl enable "${UNITS[@]}" fiboki-backup.timer fiboki-heartbeat.timer
# Journald cap, logrotate, unattended upgrades, swap
if [ "$DO_JOURNALD" -eq 1 ]; then
  if [ "$DRY" -eq 1 ]; then say "DRY  write /etc/systemd/journald.conf.d/fiboki.conf (SystemMaxUse=1G, MaxRetentionSec=1month)"
  else
    install -d /etc/systemd/journald.conf.d
    printf '[Journal]\nSystemMaxUse=1G\nSystemKeepFree=2G\nMaxRetentionSec=1month\nCompress=yes\n' > /etc/systemd/journald.conf.d/fiboki.conf
    systemctl restart systemd-journald
  fi
fi
if [ "$DO_LOGROTATE" -eq 1 ]; then
  if [ "$DRY" -eq 1 ]; then say "DRY  write /etc/logrotate.d/fiboki ($LIB/var/logs/*.log weekly, 8 kept, compressed)"
  else
    cat > /etc/logrotate.d/fiboki <<EOF
$LIB/var/logs/*.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su $SVC_USER $SVC_USER
}
EOF
  fi
fi
if [ "$DO_UNATTENDED" -eq 1 ]; then
  if [ "$DRY" -eq 1 ]; then say "DRY  write /etc/apt/apt.conf.d/20auto-upgrades and 52fiboki-unattended (security updates, no automatic reboot)"
  else
    printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\nAPT::Periodic::AutocleanInterval "7";\n' > /etc/apt/apt.conf.d/20auto-upgrades
    # No automatic reboot: a kernel update waits for you (a reboot is safe, paper state is snapshotted,
    # but it is your decision when). `needrestart`/`/var/run/reboot-required` tells you.
    printf 'Unattended-Upgrade::Automatic-Reboot "false";\nUnattended-Upgrade::Remove-Unused-Dependencies "true";\n' > /etc/apt/apt.conf.d/52fiboki-unattended
  fi
fi
if [ "$DO_SWAP" -eq 1 ]; then
  if [ -n "$(swapon --show --noheadings 2>/dev/null || true)" ]; then
    note "swap present: $(swapon --show --noheadings | awk '{print $1" "$3}' | tr '\n' ' ')"
  elif [ "$DRY" -eq 1 ]; then
    say "DRY  create /swapfile ($SWAP_SIZE), swapon, /etc/fstab entry, vm.swappiness=10"
  else
    fallocate -l "$SWAP_SIZE" /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
    chmod 600 /swapfile; mkswap /swapfile >/dev/null; swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    printf 'vm.swappiness=10\n' > /etc/sysctl.d/99-fiboki.conf
    sysctl -q -p /etc/sysctl.d/99-fiboki.conf
    note "swap $SWAP_SIZE created"
  fi
fi

# ---- 9 firewall -------------------------------------------------------------------------
step "9  firewall (ufw): deny incoming except SSH"
if [ "$DO_FIREWALL" -eq 1 ]; then
  if [ -z "$SSH_PORT" ]; then
    SSH_PORT="$( (sshd -T 2>/dev/null || true) | awk '/^port / {print $2; exit}')"
    SSH_PORT="${SSH_PORT:-22}"
  fi
  case "$SSH_PORT" in *[!0-9]*|'') echo "bad --ssh-port '$SSH_PORT'" >&2; exit 2 ;; esac
  run ufw default deny incoming
  run ufw default allow outgoing
  run ufw limit "$SSH_PORT/tcp" comment 'ssh'
  [ "$TAILSCALE" -eq 1 ] && run ufw allow in on tailscale0 comment 'tailnet'
  run ufw --force enable
  note "SSH on $SSH_PORT is allowed (rate-limited) BEFORE the firewall is enabled. Nothing else is open."
else
  note "skipped (--no-firewall). The services bind 127.0.0.1 regardless."
fi

# ---- 10 Tailscale (optional) -------------------------------------------------------------
if [ "$TAILSCALE" -eq 1 ]; then
  step "10 Tailscale: the web app on your tailnet only (tailscale serve -> 127.0.0.1:3000)"
  if ! command -v tailscale >/dev/null 2>&1; then
    codename="${VERSION_CODENAME:-}"
    if [ "$DRY" -eq 1 ]; then
      say "DRY  add Tailscale's signed apt repository (pkgs.tailscale.com/stable/$ID/$codename) and apt-get install tailscale"
    else
      install -d -m 0755 /usr/share/keyrings
      curl -fsSL "https://pkgs.tailscale.com/stable/$ID/$codename.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
      curl -fsSL "https://pkgs.tailscale.com/stable/$ID/$codename.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
      apt-get update -y; apt-get install -y tailscale
    fi
  fi
  run systemctl enable --now tailscaled
  if [ "$DRY" -eq 1 ]; then
    say "DRY  tailscale up (interactive login URL, or --auth-key from \$TS_AUTHKEY if you exported one)"
  elif ! tailscale status >/dev/null 2>&1; then
    if [ -n "${TS_AUTHKEY:-}" ]; then
      tailscale up --auth-key "$TS_AUTHKEY" --hostname "$(hostname -s)"   # the key is never echoed
    else
      note "Tailscale needs you to log in once. Run:   sudo tailscale up"
      note "and open the URL it prints. Then re-run this script (it is idempotent) to publish the web app."
    fi
  fi
  if [ "$DRY" -eq 1 ] || tailscale status >/dev/null 2>&1; then
    # tailnet-only HTTPS -> loopback web app. NEVER `tailscale funnel` (that is the public internet).
    run tailscale serve --bg --https=443 http://127.0.0.1:3000
    [ "$DRY" -eq 1 ] || tailscale serve status || true
    note "Needs MagicDNS and HTTPS certificates enabled for the tailnet (admin console > DNS)."
    note "Then in $ENV_FILE set FIBOKI_COOKIE_SECURE=true and FIBOKI_ALLOWED_ORIGINS=https://<name>.<tailnet>.ts.net and restart api and web."
  fi
fi

# ---- 11 llama.cpp (optional) -------------------------------------------------------------
if [ "$BUILD_LLAMA" -eq 1 ]; then
  step "11 llama.cpp (CPU) -> /opt/llama.cpp/bin/llama-server"
  if [ -z "$LLAMA_REF" ]; then
    if [ "$DRY" -eq 1 ]; then LLAMA_REF="<latest bNNNN tag>"
    else
      LLAMA_REF="$(git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git 'b[0-9]*' | sed 's#.*refs/tags/##' | sort -V | tail -1)"
      [ -n "$LLAMA_REF" ] || { echo "cannot resolve the latest llama.cpp release tag; pass one: --build-llama b1234" >&2; exit 1; }
    fi
    note "using the latest release tag: $LLAMA_REF (pass --build-llama TAG to pin one)"
  fi
  run install -d -m 0755 /opt/llama.cpp
  [ -d /opt/llama.cpp/src/.git ] || run git clone --quiet https://github.com/ggml-org/llama.cpp.git /opt/llama.cpp/src
  run git -C /opt/llama.cpp/src fetch --quiet --tags origin
  run git -C /opt/llama.cpp/src checkout --quiet "$LLAMA_REF"
  # shellcheck disable=SC2086
  run cmake -S /opt/llama.cpp/src -B /opt/llama.cpp/src/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF ${LLAMA_CMAKE_ARGS:-}
  run cmake --build /opt/llama.cpp/src/build --config Release -j "$(nproc)" --target llama-server
  run install -d -m 0755 /opt/llama.cpp/bin
  run install -m 0755 /opt/llama.cpp/src/build/bin/llama-server /opt/llama.cpp/bin/llama-server
  note "Weights are NEVER downloaded by this script. Print the pinned download for this machine:"
  note "   sudo -u $SVC_USER $PREFIX/scripts/llama-server.sh --print"
fi

# ---- 12 start --------------------------------------------------------------------------------
step "12 start"
if [ "$NO_START" -eq 1 ]; then
  note "--no-start: units are enabled, nothing is started. Start with: sudo systemctl start ${UNITS[*]} fiboki-backup.timer fiboki-heartbeat.timer"
else
  run systemctl start "${UNITS[@]}" fiboki-backup.timer fiboki-heartbeat.timer
  if [ "$DRY" -eq 0 ] && printf '%s\n' "${SVC_LIST[@]}" | grep -qx api; then
    for _ in $(seq 1 30); do
      curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:8000/api/health 2>/dev/null && { note "API answered on 127.0.0.1:8000"; break; }
      sleep 2
    done
  fi
  if [ "$DRY" -eq 0 ]; then
    exposed="$(ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -E '^(0\.0\.0\.0|\[::\]|\*):(3000|8000|8080)$' || true)"
    if [ -n "$exposed" ]; then echo "WARNING: these Fiboki ports listen on all interfaces: $exposed" >&2
    else note "ports 3000, 8000, 8080 are not exposed on any public interface"; fi
    /usr/local/bin/fiboki-ctl fiboki doctor --no-hash || true
  fi
fi

cat <<EOF

Done. Next (docs/v2/SERVER.md has the full runbook):
  1. Edit the env file:        sudo -e $ENV_FILE            (operators, OANDA practice token, alert channels)
  2. Restart what you changed: sudo systemctl restart fiboki-api fiboki-web fiboki-worker
  3. Check:                    fiboki-ctl fiboki doctor          systemctl status 'fiboki-*'
  4. Test the alert path:      fiboki-ctl $PREFIX/scripts/server-heartbeat.sh --test
  5. Reach the app:            ssh -L 3000:127.0.0.1:3000 you@server   then http://localhost:3000
                               or re-run with --tailscale for https on your tailnet (no public port)
EOF
