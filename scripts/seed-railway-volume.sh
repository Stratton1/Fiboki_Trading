#!/usr/bin/env bash
# Upload the canonical dataset to a Railway persistent volume.
#
# Prerequisites:
#   - Railway CLI installed and linked to the Fiboki project
#   - Railway service set to the backend service (railway service)
#   - Volume mounted at /data on the backend service
#   - FIBOKEI_DATA_DIR=/data set in Railway variables
#
# Usage:
#   cd Fiboki_Trading
#   bash scripts/seed-railway-volume.sh
#
# This script:
#   1. Creates a compressed tarball of data/canonical/histdata/
#   2. Splits it into 50MB chunks
#   3. Uploads each chunk to the Railway volume via base64 piping
#   4. Reassembles and extracts on the remote side
#   5. Generates the manifest

set -euo pipefail

CANONICAL_DIR="data/canonical/histdata"
REMOTE_BASE="/data/canonical"
CHUNK_SIZE="50m"
TMPDIR_LOCAL=$(mktemp -d)

cleanup() {
    rm -rf "$TMPDIR_LOCAL"
}
trap cleanup EXIT

echo "=== Fiboki Canonical Data Upload to Railway ==="
echo ""

# Verify local data exists
if [ ! -d "$CANONICAL_DIR" ]; then
    echo "ERROR: $CANONICAL_DIR not found. Run from the Fiboki_Trading root."
    exit 1
fi

INSTRUMENT_COUNT=$(ls -d "$CANONICAL_DIR"/*/ 2>/dev/null | wc -l | tr -d ' ')
FILE_COUNT=$(find "$CANONICAL_DIR" -name '*.parquet' | wc -l | tr -d ' ')
echo "Local data: $INSTRUMENT_COUNT instruments, $FILE_COUNT parquet files"

# Verify Railway CLI is linked
if ! railway status >/dev/null 2>&1; then
    echo "ERROR: Railway CLI not linked. Run: railway link"
    exit 1
fi

echo ""
echo "Step 1: Creating tarball..."
tar cf "$TMPDIR_LOCAL/canonical.tar" -C data/canonical histdata
TARSIZE=$(du -h "$TMPDIR_LOCAL/canonical.tar" | cut -f1)
echo "  Tarball: $TARSIZE (uncompressed for speed)"

echo ""
echo "Step 2: Splitting into chunks..."
split -b "$CHUNK_SIZE" "$TMPDIR_LOCAL/canonical.tar" "$TMPDIR_LOCAL/chunk_"
CHUNKS=$(ls "$TMPDIR_LOCAL"/chunk_* | wc -l | tr -d ' ')
echo "  $CHUNKS chunks of ${CHUNK_SIZE}B each"

echo ""
echo "Step 3: Creating remote directory..."
railway run --service backend sh -c "mkdir -p $REMOTE_BASE" 2>/dev/null || \
    railway run sh -c "mkdir -p $REMOTE_BASE"

echo ""
echo "Step 4: Uploading chunks..."
i=0
for chunk in "$TMPDIR_LOCAL"/chunk_*; do
    i=$((i + 1))
    BASENAME=$(basename "$chunk")
    echo -n "  Uploading chunk $i/$CHUNKS ($BASENAME)... "
    # Base64 encode and pipe into railway run to decode on the remote side
    base64 < "$chunk" | railway run sh -c "base64 -d > /tmp/$BASENAME" 2>/dev/null || \
    base64 < "$chunk" | railway run --service backend sh -c "base64 -d > /tmp/$BASENAME"
    echo "done"
done

echo ""
echo "Step 5: Reassembling and extracting on remote..."
railway run sh -c "cat /tmp/chunk_* > /tmp/canonical.tar && tar xf /tmp/canonical.tar -C $REMOTE_BASE && rm -f /tmp/chunk_* /tmp/canonical.tar" 2>/dev/null || \
railway run --service backend sh -c "cat /tmp/chunk_* > /tmp/canonical.tar && tar xf /tmp/canonical.tar -C $REMOTE_BASE && rm -f /tmp/chunk_* /tmp/canonical.tar"

echo ""
echo "Step 6: Verifying remote data..."
REMOTE_COUNT=$(railway run sh -c "find $REMOTE_BASE/histdata -name '*.parquet' 2>/dev/null | wc -l" 2>/dev/null || \
    railway run --service backend sh -c "find $REMOTE_BASE/histdata -name '*.parquet' 2>/dev/null | wc -l")
echo "  Remote parquet files: $REMOTE_COUNT (expected: $FILE_COUNT)"

echo ""
echo "Step 7: Generating manifest..."
railway run python -m fibokei manifest generate 2>/dev/null || \
    railway run --service backend python -m fibokei manifest generate
echo "  Manifest generated."

echo ""
echo "=== Upload complete ==="
echo "  Local files:  $FILE_COUNT"
echo "  Remote files: $REMOTE_COUNT"
echo ""
echo "Next: verify from the frontend or run the verification curl commands"
echo "from docs/operations.md → 'Step 5: Verify Production Data'"
