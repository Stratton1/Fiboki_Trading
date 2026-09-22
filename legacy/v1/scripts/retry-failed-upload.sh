#!/usr/bin/env bash
# Retry upload for instruments that failed in the initial run.
set -euo pipefail

API_URL="${FIBOKI_API_URL:-https://api.fiboki.uk}"
CANONICAL_DIR="data/canonical"

FAILED_INSTRUMENTS="cadchf cadjpy us100 us500 usdhuf usdjpy usdmxn usdnok usdsek usdsgd usdzar wtiusd xauusd"
TOTAL=$(echo "$FAILED_INSTRUMENTS" | wc -w | tr -d ' ')

echo "=== Retry Upload: $TOTAL failed instruments ==="
echo "API: $API_URL"
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

# Upload each instrument
TMPTAR=$(mktemp /tmp/fiboki-retry-XXXXXX.tar.gz)
cleanup() { rm -f "$TMPTAR"; }
trap cleanup EXIT

echo ""
echo "Step 2: Uploading instruments..."
i=0
STILL_FAILED=""
for INST in $FAILED_INSTRUMENTS; do
    i=$((i + 1))
    printf "  [%d/%d] %s ... " "$i" "$TOTAL" "$INST"

    tar czf "$TMPTAR" -C "$CANONICAL_DIR" "histdata/$INST"
    SIZE=$(du -h "$TMPTAR" | cut -f1)

    RESULT=$(curl -sf -X POST "$API_URL/api/v1/data/upload-tar" \
      -H "Authorization: Bearer $TOKEN" \
      -F "file=@$TMPTAR" \
      --max-time 180 2>&1) || {
        echo "FAILED"
        STILL_FAILED="$STILL_FAILED $INST"
        continue
    }

    COUNT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('parquet_files','?'))" 2>/dev/null || echo "?")
    echo "OK ($SIZE, $COUNT files total)"
done

if [ -n "$STILL_FAILED" ]; then
    echo ""
    echo "WARNING: Still failed:$STILL_FAILED"
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
echo "Step 5: Verifying previously-failed instruments..."
for INST in cadchf usdjpy xauusd; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "$API_URL/api/v1/market-data/$(echo $INST | tr 'a-z' 'A-Z')/H1?limit=10" \
    -H "Authorization: Bearer $TOKEN")
  echo "  $(echo $INST | tr 'a-z' 'A-Z')/H1: $STATUS"
done

echo ""
echo "=== Retry complete ==="
