#!/usr/bin/env bash
# E-1 perturbed_price_paths, as filed (research/preregistration/gate_calibration_e1.json),
# in measuring mode (every gate set judged on one measurement; E-2 reads the same file),
# as N shards on the Mac. Re-running this script resumes every shard from its checkpoint.
#
#   research/reports/e1/run_paths_mac.sh            # 4 workers (default)
#   WORKERS=6 research/reports/e1/run_paths_mac.sh
#
# When every shard prints "done", merge:
#   .venv/bin/python scripts/gate_power_study.py --merge research/reports/e1/shards/e1_perturbed_price_paths.*.json \
#       --out research/reports/e1/e1_perturbed_price_paths.json
set -euo pipefail
cd "$(dirname "$0")/../../.."
WORKERS="${WORKERS:-4}"
REPLICATES=400
DATA_ROOT="${FIBOKI_E1_DATA_ROOT:-var/datastore}"
OUT_DIR=research/reports/e1/shards
mkdir -p "$OUT_DIR"
test -f "$DATA_ROOT/.fiboki-data-root" || { echo "not a marked data root: $DATA_ROOT" >&2; exit 2; }

per=$(( (REPLICATES + WORKERS - 1) / WORKERS ))
for ((w = 0; w < WORKERS; w++)); do
  start=$(( w * per ))
  end=$(( start + per )); (( end > REPLICATES )) && end=$REPLICATES
  (( start >= end )) && break
  out="$OUT_DIR/e1_perturbed_price_paths.$start-$end.json"
  log="$OUT_DIR/e1_perturbed_price_paths.$start-$end.log"
  resume=""
  test -f "$out" && resume="--resume"
  # caffeinate -i: no idle sleep while a shard runs (display may still sleep).
  nohup caffeinate -i .venv/bin/python scripts/gate_power_study.py \
    --process perturbed_price_paths \
    --data-root "$DATA_ROOT" \
    --replicates $REPLICATES --replicate-range "$start" "$end" \
    --sr 0 0.03 0.05 0.08 0.12 \
    --external-trials 20896 \
    $resume \
    --out "$out" >> "$log" 2>&1 &
  echo "shard $start-$end -> $out (pid $!)"
done
echo "progress: grep -h 'first replicate\|resuming' $OUT_DIR/*.log; python3 -c \"import json,glob;[print(p,len(json.load(open(p))['runs'])//5,'replicates',json.load(open(p))['partial']) for p in sorted(glob.glob('$OUT_DIR/*.json'))]\""
