#!/usr/bin/env bash
# Build the V2 data store the running platform serves, from the V1 HistData
# canonical parquet directory.
#
# This is the K2 campaign's ingest path (research/ingest_histdata_universe.py),
# not a loop around pd.read_parquet: every series goes
#   provider -> RAW (immutable, checksummed) -> integrity -> CANONICAL
# so the four known V1 defects are DECLARED rather than absorbed:
#
#   * timestamps are EST-without-DST stamped as UTC. The provider applies a +5h
#     correction and records it as a declared adjustment on the dataset.
#   * prices are BID, not mid. Recorded as price_basis=BID.
#   * volume is identically zero. Reported as a volume_always_zero defect on
#     every series; nothing pretends there is volume.
#   * EURUSD carries a sentinel bar with all of OHLC at -0.0001. Integrity
#     rejects it. It is dropped under ONE explicit, versioned RepairPlan
#     (DROP_NON_POSITIVE) that names its reason and its actor, and the repaired
#     frame is a CANONICAL dataset whose lineage points back at the unrepaired
#     RAW bytes. Nothing else is repaired: gaps, off-session bars, misaligned
#     bar starts, stale runs and return outliers are reported and left alone.
#
# A series whose blocking defects are NOT exactly the non-positive-price defect
# is stored SUSPECT and excluded. That exclusion is printed and written to the
# manifest.
#
# Usage:   scripts/migrate-store.sh [TIMEFRAME ...]      (default: H4 H1)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

HISTDATA="${FIBOKI_V1_HISTDATA:-$ROOT/data/canonical/histdata}"
DEST="${FIBOKI_DATA_ROOT:-$ROOT/var/datastore}"
OUT="$ROOT/research/reports/v2_store_migration"
TIMEFRAMES=("$@")
[ ${#TIMEFRAMES[@]} -eq 0 ] && TIMEFRAMES=(H4 H1)

if [ ! -d "$HISTDATA" ]; then
  echo "no V1 histdata root at $HISTDATA" >&2
  exit 2
fi

mkdir -p "$DEST" "$OUT"
for tf in "${TIMEFRAMES[@]}"; do
  lower="$(printf '%s' "$tf" | tr '[:upper:]' '[:lower:]')"
  echo "== ingesting $tf =="
  "$ROOT/.venv/bin/python" research/ingest_histdata_universe.py \
    --histdata "$HISTDATA" \
    --data-root "$DEST" \
    --out "$OUT/$lower" \
    --timeframes "$tf" | tee "$ROOT/var/migrate_${lower}.log"
done

# fiboki.data.store names the catalogue catalogue.db; the API's /research/datasets
# route opens <data_root>/catalogue.sqlite. Until that route is corrected the
# alias keeps one catalogue rather than letting the route mint a second, empty
# one and render "0 dataset versions" over a store that holds hundreds.
ln -sfn catalogue.db "$DEST/catalogue.sqlite"

echo
echo "store: $DEST"
"$ROOT/.venv/bin/fiboki" data version --catalogue "$DEST/catalogue.db" --limit 5 || true
