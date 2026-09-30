#!/usr/bin/env bash
# E-1 block_bootstrap_real_returns, as filed (research/preregistration/gate_calibration_e1.json).
set -euo pipefail
cd "$(dirname "$0")/../../.."
exec .venv/bin/python scripts/gate_power_study.py \
  --process block_bootstrap_real_returns \
  --data-root /home/claude/e1_data \
  --replicates 400 --sr 0 0.03 0.05 0.08 0.12 \
  --external-trials 20896 \
  --out research/reports/e1/e1_block_bootstrap_real_returns.json
