#!/usr/bin/env bash
# E-4 pooled-universe candidates, as filed (research/preregistration/pooled_universe_e4.json),
# on the MacBook runtime checkout. Every stage runs as WORKERS shards under `nice` (the paper
# service keeps its CPU) and `caffeinate -i` (no idle sleep). Re-running a stage resumes every
# shard from its checkpoint. See research/reports/e4_pooled/README.md for the order and why.
#
#   research/reports/e4_pooled/run_e4_mac.sh plan          # the filed plan, checked (no bars read)
#   research/reports/e4_pooled/run_e4_mac.sh p0 H4         # process P0, H4 (run FIRST)
#   research/reports/e4_pooled/run_e4_mac.sh p0 D1         # process P0, D1
#   research/reports/e4_pooled/run_e4_mac.sh merge-p0 H4   # when every H4 shard says done
#   research/reports/e4_pooled/run_e4_mac.sh merge-p0 D1
#   research/reports/e4_pooled/run_e4_mac.sh main          # ONLY after both P0 files are read
#   research/reports/e4_pooled/run_e4_mac.sh merge-main
#   research/reports/e4_pooled/run_e4_mac.sh status
#
#   WORKERS=3 research/reports/e4_pooled/run_e4_mac.sh p0 H4   # fewer shards if memory is tight
set -euo pipefail
cd "$(dirname "$0")/../../.."
WORKERS="${WORKERS:-4}"
NICE="${NICE:-10}"
PY="${PY:-.venv/bin/python}"
DATA_ROOT="${FIBOKI_DATA_ROOT:-var/datastore}"
OUT=research/reports/e4_pooled
SHARDS="$OUT/shards"
REPLICATES=400
mkdir -p "$SHARDS"
test -f "$DATA_ROOT/.fiboki-data-root" || { echo "not a marked data root: $DATA_ROOT" >&2; exit 2; }
run() { nohup nice -n "$NICE" caffeinate -i "$PY" scripts/pooled_universe_study.py "$@"; }

case "${1:-}" in
  plan)
    "$PY" scripts/pooled_universe_study.py plan --check --out "$OUT/plan.json"
    ;;
  p0)
    tf="${2:?usage: $0 p0 H4|D1}"
    per=$(( (REPLICATES + WORKERS - 1) / WORKERS ))
    for ((w = 0; w < WORKERS; w++)); do
      start=$(( w * per )); end=$(( start + per )); (( end > REPLICATES )) && end=$REPLICATES
      (( start >= end )) && break
      out="$SHARDS/p0_$tf.$start-$end.json"; log="$SHARDS/p0_$tf.$start-$end.log"
      resume=""; test -f "$out" && resume="--resume"
      run p0 --timeframe "$tf" --data-root "$DATA_ROOT" --replicates $REPLICATES \
        --replicate-range "$start" "$end" $resume --out "$out" >> "$log" 2>&1 &
      echo "P0 $tf shard $start-$end -> $out (pid $!)"
    done
    ;;
  merge-p0)
    tf="${2:?usage: $0 merge-p0 H4|D1}"
    "$PY" scripts/pooled_universe_study.py merge "$SHARDS"/p0_"$tf".*.json --out "$OUT/e4_p0_$tf.json"
    ;;
  main)
    for tf in H4 D1; do
      test -f "$OUT/e4_p0_$tf.json" || { echo "merge P0 $tf first ($OUT/e4_p0_$tf.json)" >&2; exit 3; }
    done
    for ((w = 0; w < WORKERS; w++)); do
      out="$SHARDS/main.$w-of-$WORKERS.json"; log="$SHARDS/main.$w-of-$WORKERS.log"
      resume=""; test -f "$out" && resume="--resume"
      run main --data-root "$DATA_ROOT" --shard "$w" "$WORKERS" \
        --p0 "$OUT/e4_p0_H4.json" "$OUT/e4_p0_D1.json" \
        --reports-dir "$OUT/reports" $resume --out "$out" >> "$log" 2>&1 &
      echo "main shard $w of $WORKERS -> $out (pid $!)"
    done
    ;;
  merge-main)
    "$PY" scripts/pooled_universe_study.py merge "$SHARDS"/main.*-of-*.json --out "$OUT/e4_main.json"
    ;;
  status)
    grep -h "first replicate\|resuming" "$SHARDS"/*.log 2>/dev/null || true
    "$PY" - "$SHARDS" <<'PYEOF'
import glob, json, sys
for p in sorted(glob.glob(sys.argv[1] + "/*.json")):
    d = json.load(open(p))
    n = len(d["runs"]) // (len(d["config"]["sr_grid"]) if d.get("stage") == "p0" else 1)
    unit = "replicates" if d.get("stage") == "p0" else "candidates"
    print(p, n, unit, "partial" if d.get("partial") else "done")
PYEOF
    ;;
  *)
    sed -n '2,20p' "$0"; exit 1
    ;;
esac
