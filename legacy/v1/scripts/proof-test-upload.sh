#!/usr/bin/env bash
# Proof test: upload a single instrument (XAUUSD) to verify the upload pipeline.
#
# XAUUSD is chosen because:
# - It's NOT in the starter dataset (proves canonical volume works)
# - It's small enough for a quick test (~14MB for all 6 timeframes)
#
# Usage:
#   cd Fiboki_Trading
#   bash scripts/proof-test-upload.sh

set -euo pipefail

API_URL="${FIBOKI_API_URL:-https://api.fiboki.uk}"
CANONICAL_DIR="data/canonical"
TEST_SYMBOL="xauusd"

echo "=== Proof Test: Upload $TEST_SYMBOL ==="
echo "API: $API_URL"
echo ""

# Verify local data exists
if [ ! -d "$CANONICAL_DIR/histdata/$TEST_SYMBOL" ]; then
    echo "ERROR: $CANONICAL_DIR/histdata/$TEST_SYMBOL not found."
    exit 1
fi

ls -lh "$CANONICAL_DIR/histdata/$TEST_SYMBOL/"
echo ""

# Authenticate
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

# Create tar with just one instrument
echo ""
echo "Step 2: Creating tar.gz for $TEST_SYMBOL only..."
TARFILE=$(mktemp /tmp/proof-test-XXXXXX.tar.gz)
tar czf "$TARFILE" -C "$CANONICAL_DIR" "histdata/$TEST_SYMBOL"
TARSIZE=$(du -h "$TARFILE" | cut -f1)
echo "  Created: $TARFILE ($TARSIZE)"

cleanup() {
    rm -f "$TARFILE"
}
trap cleanup EXIT

# Upload
echo ""
echo "Step 3: Uploading..."
UPLOAD_RESULT=$(curl -sf -X POST "$API_URL/api/v1/data/upload-tar" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$TARFILE" \
  --max-time 120)
echo "  Result: $UPLOAD_RESULT"

# Verify via data check endpoint
echo ""
echo "Step 4: Verifying XAUUSD via API..."
for TF in M1 M5 M15 M30 H1 H4; do
  RESULT=$(curl -s "$API_URL/api/v1/data/check/XAUUSD/$TF" \
    -H "Authorization: Bearer $TOKEN")
  echo "  XAUUSD/$TF: $RESULT"
done

# Verify market data endpoint
echo ""
echo "Step 5: Fetching XAUUSD/H1 candles..."
CANDLES=$(curl -s "$API_URL/api/v1/market-data/XAUUSD/H1?limit=5" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Source: {d.get(\"source\", \"unknown\")}')
print(f'  Bars: {len(d.get(\"candles\", []))}')
print(f'  From: {d.get(\"from_date\", \"?\")}, To: {d.get(\"to_date\", \"?\")}')
")
echo "$CANDLES"

echo ""
echo "=== Proof test complete ==="
echo "If XAUUSD/H1 returned candles with source 'canonical/histdata',"
echo "the volume pipeline is working. Proceed with the full upload:"
echo "  bash scripts/seed-railway-volume.sh"
