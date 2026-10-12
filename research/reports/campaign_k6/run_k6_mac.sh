#!/usr/bin/env bash
# K6: 100,000 generated documents, pooled (research/preregistration/campaign_k6.json, DRAFT).
# On the MacBook runtime checkout. Every stage runs as WORKERS interleaved shards under `nice`
# (the paper service keeps its CPU) and `caffeinate -i` (no idle sleep). Re-running a stage
# resumes every shard from its JSON-lines checkpoint. See research/reports/campaign_k6/README.md.
#
#   research/reports/campaign_k6/run_k6_mac.sh bench              # 0. calibrate the estimate (5 to 10 min)
#   research/reports/campaign_k6/run_k6_mac.sh check              # 1. generator + plan reproduce the filing
#   research/reports/campaign_k6/run_k6_mac.sh screen H4          # 2. S0 on every document, H4
#   research/reports/campaign_k6/run_k6_mac.sh status screen H4
#   research/reports/campaign_k6/run_k6_mac.sh merge screen H4    # when every shard is done
#   research/reports/campaign_k6/run_k6_mac.sh ladder H4          # 3. S1 on the screen's survivors
#   research/reports/campaign_k6/run_k6_mac.sh merge ladder H4
#   (then the same for D1, only if E-4's P0 admitted D1)
#
#   WORKERS=3 research/reports/campaign_k6/run_k6_mac.sh screen H4   # fewer shards if memory is tight
#   UNIVERSE=crypto_majors_v1 ...    # refused until that universe is defined and its P0 is filed
set -euo pipefail
cd "$(dirname "$0")/../../.."
WORKERS="${WORKERS:-4}"
NICE="${NICE:-10}"
PY="${PY:-.venv/bin/python}"
UNIVERSE="${UNIVERSE:-fx_pooled_k5}"
DATA_ROOT="${FIBOKI_DATA_ROOT:-var/datastore}"
MAX_RSS_MB="${MAX_RSS_MB:-1500}"
OUT=research/reports/campaign_k6
SHARDS="$OUT/shards/$UNIVERSE"
E4=research/reports/e4_pooled
mkdir -p "$SHARDS"
run() { nohup nice -n "$NICE" caffeinate -i "$PY" scripts/campaign_k6.py "$@"; }

case "${1:-}" in
  bench)
    nice -n "$NICE" "$PY" scripts/k6_engine_benchmark.py --bars 6000 26000 --docs 8 \
      --out "$OUT/bench_$(hostname -s).json"
    ;;
  check)
    "$PY" research/generate_families_k6.py --check
    "$PY" scripts/campaign_k6.py plan --universe "$UNIVERSE" --check --out "$OUT/plan_$UNIVERSE.json"
    ;;
  screen|ladder)
    stage="$1"; tf="${2:?usage: $0 $1 H4|D1}"
    test -f "$DATA_ROOT/.fiboki-data-root" || { echo "not a marked data root: $DATA_ROOT" >&2; exit 2; }
    p0="$E4/e4_p0_$tf.json"
    test -f "$p0" || { echo "E-4 P0 for $tf is not decided ($p0 missing); K6 waits for it" >&2; exit 3; }
    extra=()
    if [[ "$stage" == ladder ]]; then
      screen="$OUT/k6_screen_${UNIVERSE}_$tf.json"
      test -f "$screen" || { echo "merge the $tf screen first ($screen)" >&2; exit 3; }
      extra=(--screen "$screen" --reports-dir "$OUT/reports/$UNIVERSE/$tf")
    fi
    for ((w = 0; w < WORKERS; w++)); do
      out="$SHARDS/${stage}_$tf.$w-of-$WORKERS.jsonl"; log="$SHARDS/${stage}_$tf.$w-of-$WORKERS.log"
      resume=""; test -f "$out" && resume="--resume"
      run "$stage" --universe "$UNIVERSE" --timeframe "$tf" --data-root "$DATA_ROOT" --p0 "$p0" \
        --shard "$w" "$WORKERS" --max-rss-mb "$MAX_RSS_MB" $resume ${extra[@]+"${extra[@]}"} \
        --out "$out" >> "$log" 2>&1 &
      echo "K6 $stage $tf shard $w/$WORKERS -> $out (pid $!)"
    done
    ;;
  status)
    stage="${2:?usage: $0 status screen|ladder H4|D1}"; tf="${3:?}"
    "$PY" scripts/campaign_k6.py status "$SHARDS/${stage}_$tf".*-of-*.jsonl
    pgrep -fl "campaign_k6.py $stage" || echo "(no $stage process running)"
    ;;
  merge)
    stage="${2:?usage: $0 merge screen|ladder H4|D1}"; tf="${3:?}"
    "$PY" scripts/campaign_k6.py merge "$SHARDS/${stage}_$tf".*-of-*.jsonl \
      --out "$OUT/k6_${stage}_${UNIVERSE}_$tf.json"
    ;;
  *)
    sed -n '2,20p' "$0"; exit 64
    ;;
esac
