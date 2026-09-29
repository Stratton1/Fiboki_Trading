#!/usr/bin/env bash
# Fiboki V2 — stop everything dev-up.sh started. Safe to run when nothing is running.
set -uo pipefail
pkill -f "uvicorn.*fiboki" 2>/dev/null && echo "api  : stopped" || echo "api  : not running"
pkill -f "fiboki worker run" 2>/dev/null && echo "wkr  : stopped" || echo "wkr  : not running"
pkill -f "fiboki news record" 2>/dev/null && echo "news : stopped" || true
pkill -f "next dev" 2>/dev/null && echo "web  : stopped" || echo "web  : not running"
