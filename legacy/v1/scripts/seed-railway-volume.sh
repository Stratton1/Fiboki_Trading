#!/usr/bin/env bash
# Upload the canonical dataset to Railway production via the API upload endpoint.
#
# Uploads one instrument at a time (~15MB each) to avoid Railway's request
# body size limits. Total: 60 instruments, ~741MB compressed.
#
# Prerequisites:
#   - Railway volume mounted at /data on the backend service
#   - FIBOKEI_DATA_DIR=/data set in Railway variables
#   - Backend deployed with the /data/upload-tar endpoint
#   - Valid login credentials
#
# Usage:
#   cd Fiboki_Trading
#   bash scripts/seed-railway-volume.sh

set -euo pipefail

API_URL="${FIBOKI_API_URL:-https://api.fiboki.uk}"
CANONICAL_DIR="data/canonical"

echo "=== Fiboki Canonical Data Upload (per-instrument) ==="
echo "API: $API_URL"
echo ""

# Verify local data exists
if [ ! -d "$CANONICAL_DIR/histdata" ]; then
    echo "ERROR: $CANONICAL_DIR/histdata not found. Run from the Fiboki_Trading root."
    exit 1
fi

INSTRUMENTS=$(ls -d "$CANONICAL_DIR/histdata"/*/ | xargs -n1 basename)
TOTAL=$(echo "$INSTRUMENTS" | wc -l | tr -d ' ')
echo "Found $TOTAL instruments to upload"

# Authenticate
echo ""
echo "Step 1: Authenticating..."
read -rp "Username: " USERNAME
read -rsp "Password: " PASSWORD
echo ""

TOKEN=$(curl -sf -X POST "$API_URL/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=$USERNAME&password=$PASSWORD" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

if [ -z "$TOKEN" ]; then
    echo "ERROR: Authentication failed"
    exit 1
fi
echo "  Authenticated."

# Upload each instrument
TMPTAR=$(mktemp /tmp/fiboki-upload-XXXXXX.tar.gz)
cleanup() { rm -f "$TMPTAR"; }
trap cleanup EXIT

echo ""
echo "Step 2: Uploading instruments..."
i=0
FAILED=""
for INST in $INSTRUMENTS; do
    i=$((i + 1))
    printf "  [%d/%d] %s ... " "$i" "$TOTAL" "$INST"

    tar czf "$TMPTAR" -C "$CANONICAL_DIR" "histdata/$INST"
    SIZE=$(du -h "$TMPTAR" | cut -f1)

    RESULT=$(curl -sf -X POST "$API_URL/api/v1/data/upload-tar" \
      -H "Authorization: Bearer $TOKEN" \
      -F "file=@$TMPTAR" \
      --max-time 120 2>&1) || {
        echo "FAILED"
        FAILED="$FAILED $INST"
        continue
    }

    # Extract parquet count from result
    COUNT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('parquet_files','?'))" 2>/dev/null || echo "?")
    echo "OK ($SIZE, $COUNT files total)"
done

if [ -n "$FAILED" ]; then
    echo ""
    echo "WARNING: Failed instruments:$FAILED"
fi

# Refresh manifest
echo ""
echo "Step 3: Refreshing manifest..."
MANIFEST_RESULT=$(curl -sf -X POST "$API_URL/api/v1/data/manifest/refresh" \
  -H "Authorization: Bearer $TOKEN")
echo "  $MANIFEST_RESULT"

# Verify
echo ""
echo "Step 4: Verifying EURUSD across all timeframes..."
for TF in M1 M5 M15 M30 H1 H4; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "$API_URL/api/v1/market-data/EURUSD/$TF?limit=10" \
    -H "Authorization: Bearer $TOKEN")
  echo "  EURUSD/$TF: $STATUS"
done

echo ""
echo "Step 5: Verifying non-starter instrument (XAUUSD)..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  "$API_URL/api/v1/market-data/XAUUSD/H1?limit=10" \
  -H "Authorization: Bearer $TOKEN")
echo "  XAUUSD/H1: $STATUS"

echo ""
echo "=== Upload complete ($i instruments) ==="
