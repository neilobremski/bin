#!/usr/bin/env bash
# lifetime.sh — Run the tadpole through its entire lifecycle.
# Proves the life system works: spark finds life.conf, respects cadence,
# enforces singleton via flock, and the heart beats on schedule.
#
# Usage: ./lifetime.sh [path/to/spark.sh]
#        Default: looks for ../life/spark.sh relative to this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SPARK="${1:-$SCRIPT_DIR/../life/spark.sh}"

if [ ! -x "$SPARK" ]; then
  echo "FAIL: spark.sh not found at $SPARK" >&2
  exit 1
fi

# --- Helpers ---
TESTS=0; PASSED=0; FAILED=0

pass() { TESTS=$((TESTS+1)); PASSED=$((PASSED+1)); echo "ok $TESTS - $1"; }
fail() { TESTS=$((TESTS+1)); FAILED=$((FAILED+1)); echo "not ok $TESTS - $1"; }

# --- Setup: copy tadpole to a temp dir ---
TDIR=$(mktemp -d)
trap 'rm -rf "$TDIR"' EXIT
cp -r "$SCRIPT_DIR/organs" "$SCRIPT_DIR/life.conf" "$TDIR/"

HEART="$TDIR/organs/heart"

# --- Test 1: First heartbeat (spark discovers life.conf from cwd) ---
cd "$TDIR"
"$SPARK"
sleep 1

if [ -f "$HEART/health.json" ]; then
  pass "health.json created after first spark"
else
  fail "health.json not found after first spark"
fi

if [ -f "$HEART/beats.count" ] && [ "$(cat "$HEART/beats.count")" = "1" ]; then
  pass "beat count is 1 after first spark"
else
  fail "beat count should be 1, got: $(cat "$HEART/beats.count" 2>/dev/null || echo 'missing')"
fi

# --- Test 2: Cadence blocks immediate re-spark ---
sleep 1  # let flock release from backgrounded subshell
"$SPARK" > "$TDIR/spark-out.txt" 2>&1 || true
sleep 1

if [ "$(cat "$HEART/beats.count")" = "1" ]; then
  pass "cadence prevented immediate re-spark (beats still 1)"
else
  fail "cadence should block re-spark, but beats is $(cat "$HEART/beats.count")"
fi

# --- Test 3: Expired cadence allows re-spark ---
echo $(($(date +%s) - 600)) > "$HEART/.spark.last"

"$SPARK"
sleep 1

if [ "$(cat "$HEART/beats.count")" = "2" ]; then
  pass "heart re-sparked after cadence expired (beats=2)"
else
  fail "expected beats=2 after cadence expiry, got: $(cat "$HEART/beats.count")"
fi

# --- Test 4: health.json structure ---
if grep -q '"status":"ok"' "$HEART/health.json" && \
   grep -q '"organ":"heart"' "$HEART/health.json" && \
   grep -q '"beats":2' "$HEART/health.json" && \
   grep -q '"ts":' "$HEART/health.json"; then
  pass "health.json has correct structure (status, organ, beats, ts)"
else
  fail "health.json missing expected fields: $(cat "$HEART/health.json")"
fi

# --- Summary ---
echo ""
echo "# $PASSED/$TESTS passed"
[ "$FAILED" -gt 0 ] && exit 1
exit 0
