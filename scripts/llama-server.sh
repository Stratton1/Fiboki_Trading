#!/usr/bin/env bash
# Fiboki V2: start llama.cpp's llama-server for the research agents (macOS Apple silicon, or Linux CPU).
#
#   scripts/llama-server.sh                 # pick the model tier from this machine's RAM, run it
#   scripts/llama-server.sh --tier 16       # force a tier: 8 | 16 | 32 (GB of RAM)
#   scripts/llama-server.sh --model PATH    # any GGUF you choose (absolute path)
#   scripts/llama-server.sh --print         # print the command and the checks, start nothing
#   scripts/llama-server.sh --verify        # also hash the weights and compare with the pinned SHA-256
#
# The tier table (Q4_K_M, Apache-2.0 research recommendations, each file pinned to a Hugging Face
# commit and a SHA-256, so the digest the provider records is the digest reviewed here):
#     8 GB   Qwen3.5-4B   unsloth/Qwen3.5-4B-GGUF   2,740,937,888 bytes
#    16 GB   Qwen3.5-9B   unsloth/Qwen3.5-9B-GGUF   (a 16 GB VPS with Fiboki running: this tier)
#    32 GB+  Qwen3.6-27B  unsloth/Qwen3.6-27B-GGUF
# The RAM is read as the machine reports it (a "16 GB" VPS shows about 15.6) and rounded to the
# nearest GB; 28+ selects 32, 14+ selects 16, 7+ selects 8. Tiers 64 and 128 (Qwen3-32B Q5_K_M and
# Q8_0, the earlier Mac table) still work when asked for by --tier; tier 32 was Qwen3-14B before
# and is now Qwen3.6-27B, so a Mac that relied on it prints the new download once.
#
# What it guarantees, each for a reason:
#   * It NEVER downloads a model. A missing model prints the exact pinned download command and the
#     sha256sum check and exits 1: which weights answer the agents is an operator decision, and the
#     provider pins them by the SHA-256 of this file.
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
#     so b6325 covers all three. Qwen3.5 and Qwen3.6 are newer architectures:
#     use a recent release (the model cards say which). macOS: brew upgrade llama.cpp.
#     Linux: scripts/server-install.sh --build-llama builds the latest release tag from source,
#     or take the official CPU release binary (docs/v2/SERVER.md, "Local model").
#
# Models directory: ~/Models (override with --models-dir). LLAMA_THREADS=N sets -t (CPU threads;
# default llama.cpp's own). Model choices and their sources: USER_ACTIONS.md and docs/v2/OPERATIONS.md.
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
VERIFY=0
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
    --verify) VERIFY=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

# ---- tier from memory ---------------------------------------------------
OS_NAME="$(uname -s)"
if [ -z "$TIER" ] && [ -z "$MODEL" ]; then
  if [ -z "$RAM_GB" ]; then
    if [ "$OS_NAME" = "Darwin" ]; then
      bytes="$(sysctl -n hw.memsize 2>/dev/null || true)"
      [ -n "$bytes" ] && RAM_GB=$(( (bytes + 536870912) / 1073741824 ))
    elif [ -r /proc/meminfo ]; then
      kb="$(sed -n 's/^MemTotal: *\([0-9][0-9]*\) kB.*/\1/p' /proc/meminfo | head -1)"
      [ -n "$kb" ] && RAM_GB=$(( (kb + 524288) / 1048576 ))
    fi
    if [ -z "$RAM_GB" ]; then
      echo "cannot read this machine's memory; pass --tier 8|16|32 or --ram-gb N" >&2
      exit 2
    fi
  fi
  if   [ "$RAM_GB" -ge 28 ]; then TIER=32
  elif [ "$RAM_GB" -ge 14 ]; then TIER=16
  elif [ "$RAM_GB" -ge 7 ];  then TIER=8
  else
    echo "only ${RAM_GB} GB of memory: below the 8 GB tier. Pass --model with a smaller GGUF." >&2
    exit 2
  fi
fi

# ---- the recommended model per tier -------------------------------------
# PINNED=1: REPO is pinned to COMMIT and the file to SHA256 (verified by the operator after the
# download; --verify checks it again). PINNED=0: the earlier Mac table, unpinned (Apache-2.0
# Qwen/Qwen3 files; sizes as Hugging Face reported them on 2026-09-29).
PINNED=0; COMMIT=""; SHA256=""; SIZE_BYTES=""
if [ -z "$MODEL" ]; then
  case "$TIER" in
    8)  PINNED=1; REPO="unsloth/Qwen3.5-4B-GGUF"; FILE="Qwen3.5-4B-Q4_K_M.gguf"
        COMMIT="e87f176479d0855a907a41277aca2f8ee7a09523"
        SHA256="00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"
        SIZE_BYTES="2740937888"; SIZE="2.74 GB"; ALIAS="${ALIAS:-qwen3.5-4b-q4_k_m}" ;;
    16) PINNED=1; REPO="unsloth/Qwen3.5-9B-GGUF"; FILE="Qwen3.5-9B-Q4_K_M.gguf"
        COMMIT="3885219b6810b007914f3a7950a8d1b469d598a5"
        SHA256="03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
        SIZE="see the file page"; ALIAS="${ALIAS:-qwen3.5-9b-q4_k_m}" ;;
    32) PINNED=1; REPO="unsloth/Qwen3.6-27B-GGUF"; FILE="Qwen3.6-27B-Q4_K_M.gguf"
        COMMIT="82d411acf4a06cfb8d9b073a5211bf410bfc29bf"
        SHA256="5ed60d0af4650a854b1755bd392f9aef4872643dc25a254bc68043fa638392a0"
        SIZE="see the file page"; ALIAS="${ALIAS:-qwen3.6-27b-q4_k_m}" ;;
    64)  REPO="Qwen/Qwen3-32B-GGUF"; FILE="Qwen3-32B-Q5_K_M.gguf"; ALIAS="${ALIAS:-qwen3-32b-q5_k_m}"; SIZE="23.2 GB" ;;
    128) REPO="Qwen/Qwen3-32B-GGUF"; FILE="Qwen3-32B-Q8_0.gguf";   ALIAS="${ALIAS:-qwen3-32b-q8_0}";   SIZE="34.8 GB" ;;
    *) echo "unknown tier $TIER (8 | 16 | 32; legacy 64 | 128)" >&2; exit 2 ;;
  esac
  DIR="$MODELS_DIR/${REPO#*/}"
  MODEL="$DIR/$FILE"
  if [ "$OS_NAME" = "Darwin" ]; then SUMCHECK="shasum -a 256 -c -"; else SUMCHECK="sha256sum -c -"; fi
  if [ ! -f "$MODEL" ]; then
    echo "Model not found: $MODEL" >&2
    echo "This script never downloads weights. To fetch the tier-$TIER model ($SIZE), run ONE of:" >&2
    if [ "$PINNED" -eq 1 ]; then
      echo "  hf download $REPO $FILE --revision $COMMIT --local-dir \"$DIR\"" >&2
      echo "  mkdir -p \"$DIR\" && curl -L --fail -o \"$MODEL\" \"https://huggingface.co/$REPO/resolve/$COMMIT/$FILE\"" >&2
      echo "Then verify it BEFORE use (the pinned SHA-256 of $FILE at commit ${COMMIT:0:12}):" >&2
      echo "  echo \"$SHA256  $FILE\" | (cd \"$DIR\" && $SUMCHECK)" >&2
      if [ -n "$SIZE_BYTES" ]; then echo "  expected size: $SIZE_BYTES bytes" >&2; fi
    else
      echo "  hf download $REPO $FILE --local-dir \"$DIR\"" >&2
      echo "  mkdir -p \"$DIR\" && curl -L --fail -o \"$MODEL\" \"https://huggingface.co/$REPO/resolve/main/$FILE\"" >&2
      echo "Then compare the SHA-256 of the file with the one shown on its page at" >&2
      echo "  https://huggingface.co/$REPO/blob/main/$FILE" >&2
    fi
    exit 1
  fi
  if [ -n "$SIZE_BYTES" ]; then
    actual="$(stat -c %s "$MODEL" 2>/dev/null || stat -f %z "$MODEL")"
    if [ "$actual" != "$SIZE_BYTES" ]; then
      echo "$MODEL is $actual bytes; the pinned file is $SIZE_BYTES. A partial download? Re-fetch and verify." >&2
      exit 1
    fi
  fi
  if [ "$VERIFY" -eq 1 ]; then
    if [ "$PINNED" -ne 1 ]; then echo "--verify: tier $TIER is not pinned; nothing to compare with" >&2; exit 2; fi
    echo "hashing $MODEL (a few minutes for a large model)..." >&2
    if ! echo "$SHA256  $FILE" | (cd "$DIR" && $SUMCHECK >/dev/null 2>&1); then
      echo "SHA-256 of $FILE does not match the pinned $SHA256. Delete it and download again." >&2
      exit 1
    fi
    echo "sha256 verified against the pin ($SHA256)" >&2
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
  # A Linux server has no Homebrew: look where scripts/server-install.sh --build-llama and the
  # official release unpack put it, before giving up.
  for cand in /opt/llama.cpp/bin/llama-server /opt/llama.cpp/build/bin/llama-server \
              "$HOME/llama.cpp/build/bin/llama-server" "$HOME/.local/bin/llama-server"; do
    if [ -x "$cand" ]; then BIN="$cand"; break; fi
  done
fi
if ! command -v "$BIN" >/dev/null 2>&1; then
  if [ "$OS_NAME" = "Darwin" ]; then
    echo "llama-server not found. Install it: brew install llama.cpp" >&2
  else
    echo "llama-server not found. On Linux either:" >&2
    echo "  sudo scripts/server-install.sh --build-llama      # builds the latest release tag from source, CPU" >&2
    echo "  or unpack the official CPU build (llama-<tag>-bin-ubuntu-x64.zip from" >&2
    echo "  https://github.com/ggml-org/llama.cpp/releases) so that /opt/llama.cpp/bin/llama-server exists," >&2
    echo "  or set LLAMA_SERVER_BIN=/path/to/llama-server." >&2
  fi
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
  echo "llama.cpp build b$BUILD is older than b$MIN_BUILD; upgrade it (macOS: brew upgrade llama.cpp; Linux: scripts/server-install.sh --build-llama)" >&2
  exit 1
fi

CMD=("$BIN" -m "$MODEL" --alias "$ALIAS" --host "$HOST" --port "$PORT"
     --ctx-size "$CTX" -np 1 -fa on --jinja --reasoning-budget 0)
if [ -n "${LLAMA_THREADS:-}" ]; then
  case "$LLAMA_THREADS" in *[!0-9]*|0) echo "LLAMA_THREADS must be a positive whole number" >&2; exit 2 ;; esac
  CMD+=(-t "$LLAMA_THREADS")
fi

echo "llama.cpp  : b$BUILD"
echo "model      : $MODEL"
echo "alias      : $ALIAS   (set FIBOKI_AGENT_LOCAL_MODEL=$ALIAS)"
echo "listening  : http://$HOST:$PORT   (FIBOKI_AGENT_LOCAL_URL)"
printf 'command    :'; printf ' %q' "${CMD[@]}"; printf '\n'
if [ "$PRINT_ONLY" -eq 1 ]; then
  exit 0
fi
exec "${CMD[@]}"
