#!/usr/bin/env bash
# Fiboki V2: start llama.cpp's llama-server for the research agents (macOS, Apple silicon).
#
#   scripts/llama-server.sh                 # pick the model tier from this Mac's RAM, run it
#   scripts/llama-server.sh --tier 64       # force a tier: 32 | 64 | 128 (GB of unified memory)
#   scripts/llama-server.sh --model PATH    # any GGUF you choose (absolute path)
#   scripts/llama-server.sh --print         # print the command and the checks, start nothing
#
# What it guarantees, each for a reason:
#   * It NEVER downloads a model. A missing model prints the exact download
#     command and exits 1: which weights answer the agents is an operator
#     decision, and the provider pins them by the SHA-256 of this file.
#   * An ABSOLUTE -m path, so /props reports a path the provider can hash.
#     A relative path is refused by the provider as unpinnable.
#   * One slot (-np 1): the whole context belongs to one request, and a
#     request is never batched with another (reproducibility).
#   * --ctx-size 16384, flash attention on, thinking disabled
#     (--reasoning-budget 0) so the JSON-schema grammar constrains the answer.
#   * Binds 127.0.0.1:8080 only. Nothing here sets or reads a Fiboki live control.
#   * Refuses a llama.cpp build older than b6325: that is the first build
#     with `-fa on|off|auto` (PR #15434). The OpenAI json_schema response
#     format is reliable from b4820 and --reasoning-budget exists from b5488,
#     so b6325 covers all three. Upgrade with: brew upgrade llama.cpp
#
# Models directory: ~/Models (override with --models-dir). Model choices and
# their sources: USER_ACTIONS.md "Desktop migration" and docs/v2/OPERATIONS.md.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

MIN_BUILD=6325
HOST=127.0.0.1
PORT=8080
CTX=16384
MODELS_DIR="$HOME/Models"
TIER=""
MODEL=""
ALIAS=""
PRINT_ONLY=0
RAM_GB=""
BIN="${LLAMA_SERVER_BIN:-llama-server}"

usage() { sed -n '2,8p' "$0"; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIER="${2:?}"; shift 2 ;;
    --model) MODEL="${2:?}"; shift 2 ;;
    --alias) ALIAS="${2:?}"; shift 2 ;;
    --models-dir) MODELS_DIR="${2:?}"; shift 2 ;;
    --ram-gb) RAM_GB="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --print) PRINT_ONLY=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

# ---- tier from memory ---------------------------------------------------
if [ -z "$TIER" ] && [ -z "$MODEL" ]; then
  if [ -z "$RAM_GB" ]; then
    bytes="$(sysctl -n hw.memsize 2>/dev/null || true)"
    if [ -z "$bytes" ]; then
      echo "cannot read hw.memsize (not macOS?); pass --tier 32|64|128 or --ram-gb N" >&2
      exit 2
    fi
    RAM_GB=$(( bytes / 1024 / 1024 / 1024 ))
  fi
  if   [ "$RAM_GB" -ge 128 ]; then TIER=128
  elif [ "$RAM_GB" -ge 64 ];  then TIER=64
  elif [ "$RAM_GB" -ge 32 ];  then TIER=32
  else
    echo "only ${RAM_GB} GB of memory: below the 32 GB tier. Pass --model with a smaller GGUF." >&2
    exit 2
  fi
fi

# ---- the recommended model per tier -------------------------------------
# Sizes are the file sizes Hugging Face reports for these exact files
# (huggingface.co/api/models/<repo>?blobs=true, read 2026-09-29). All are
# Apache-2.0. Qwen3's native context is 32,768 tokens (model card), so
# 16,384 needs no RoPE scaling.
if [ -z "$MODEL" ]; then
  case "$TIER" in
    32)  REPO="Qwen/Qwen3-14B-GGUF"; FILE="Qwen3-14B-Q5_K_M.gguf"; ALIAS="${ALIAS:-qwen3-14b-q5_k_m}"; SIZE="10.5 GB" ;;
    64)  REPO="Qwen/Qwen3-32B-GGUF"; FILE="Qwen3-32B-Q5_K_M.gguf"; ALIAS="${ALIAS:-qwen3-32b-q5_k_m}"; SIZE="23.2 GB" ;;
    128) REPO="Qwen/Qwen3-32B-GGUF"; FILE="Qwen3-32B-Q8_0.gguf";   ALIAS="${ALIAS:-qwen3-32b-q8_0}";   SIZE="34.8 GB" ;;
    *) echo "unknown tier $TIER (32 | 64 | 128)" >&2; exit 2 ;;
  esac
  DIR="$MODELS_DIR/${REPO#*/}"
  MODEL="$DIR/$FILE"
  if [ ! -f "$MODEL" ]; then
    echo "Model not found: $MODEL" >&2
    echo "This script never downloads weights. To fetch the tier-$TIER model ($SIZE), run ONE of:" >&2
    echo "  hf download $REPO $FILE --local-dir \"$DIR\"" >&2
    echo "  mkdir -p \"$DIR\" && curl -L --fail -o \"$MODEL\" \"https://huggingface.co/$REPO/resolve/main/$FILE\"" >&2
    echo "Then compare 'shasum -a 256 \"$MODEL\"' with the SHA256 shown on the file's page at" >&2
    echo "  https://huggingface.co/$REPO/blob/main/$FILE" >&2
    exit 1
  fi
else
  case "$MODEL" in
    /*) ;;
    *) echo "--model must be an absolute path (the provider refuses a relative one as unpinnable)" >&2; exit 2 ;;
  esac
  if [ ! -f "$MODEL" ]; then echo "Model not found: $MODEL" >&2; exit 1; fi
  if [ -z "$ALIAS" ]; then
    ALIAS="$(basename "$MODEL" .gguf | tr '[:upper:]' '[:lower:]')"
  fi
fi

# ---- the llama.cpp build -------------------------------------------------
if ! command -v "$BIN" >/dev/null 2>&1; then
  echo "llama-server not found. Install it: brew install llama.cpp" >&2
  exit 1
fi
VERSION_TEXT="$("$BIN" --version 2>&1 || true)"
# Current builds print "version: X (build N, commit H)"; older ones "version: N (H)".
BUILD="$(printf '%s\n' "$VERSION_TEXT" | sed -n 's/.*(build \([0-9][0-9]*\),.*/\1/p' | head -1)"
if [ -z "$BUILD" ]; then
  BUILD="$(printf '%s\n' "$VERSION_TEXT" | sed -n 's/^version: \([0-9][0-9]*\).*/\1/p' | head -1)"
fi
if [ -z "$BUILD" ]; then
  echo "cannot read the llama.cpp build number from '$BIN --version':" >&2
  printf '%s\n' "$VERSION_TEXT" >&2
  exit 1
fi
if [ "$BUILD" -lt "$MIN_BUILD" ]; then
  echo "llama.cpp build b$BUILD is older than b$MIN_BUILD; brew upgrade llama.cpp" >&2
  exit 1
fi

CMD=("$BIN" -m "$MODEL" --alias "$ALIAS" --host "$HOST" --port "$PORT"
     --ctx-size "$CTX" -np 1 -fa on --jinja --reasoning-budget 0)

echo "llama.cpp  : b$BUILD"
echo "model      : $MODEL"
echo "alias      : $ALIAS   (set FIBOKI_AGENT_LOCAL_MODEL=$ALIAS)"
echo "listening  : http://$HOST:$PORT   (FIBOKI_AGENT_LOCAL_URL)"
printf 'command    :'; printf ' %q' "${CMD[@]}"; printf '\n'
if [ "$PRINT_ONLY" -eq 1 ]; then
  exit 0
fi
exec "${CMD[@]}"
