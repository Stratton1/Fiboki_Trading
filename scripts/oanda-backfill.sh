#!/usr/bin/env bash
# Run `fiboki data oanda-backfill` with the practice credentials from
# ~/.fiboki/env (read without shell expansion, like scripts/fiboki-service.sh)
# against this checkout's research store. Arguments pass straight through:
#
#   scripts/oanda-backfill.sh -i GBPNZD
#   scripts/oanda-backfill.sh -i EURUSD,GBPUSD -g H4,D
#   scripts/oanda-backfill.sh --all-registered --max-requests 1500
#
# Never prints a credential. Paper/research only: the practice host is the
# only host the transport will speak to.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${FIBOKI_HOME:-$HOME/.fiboki}/env"
_read() {  # $1 = KEY; value without surrounding quotes, no expansion
  local line
  line="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 || true)"
  line="${line#*=}"
  case "$line" in
    \'*\') line="${line#\'}"; line="${line%\'}" ;;
    \"*\") line="${line#\"}"; line="${line%\"}" ;;
  esac
  printf '%s' "$line"
}
FIBOKI_OANDA_PRACTICE_TOKEN="$(_read FIBOKI_OANDA_PRACTICE_TOKEN)"
FIBOKI_OANDA_PRACTICE_ACCOUNT_ID="$(_read FIBOKI_OANDA_PRACTICE_ACCOUNT_ID)"
if [ -z "$FIBOKI_OANDA_PRACTICE_TOKEN" ]; then
  echo "FIBOKI_OANDA_PRACTICE_TOKEN is not set in $ENV_FILE" >&2
  exit 2
fi
export FIBOKI_OANDA_PRACTICE_TOKEN FIBOKI_OANDA_PRACTICE_ACCOUNT_ID
exec .venv/bin/fiboki data oanda-backfill --data-root "${FIBOKI_DATA_ROOT:-var/datastore}" "$@"
