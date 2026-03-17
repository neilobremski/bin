#!/usr/bin/env bash
# lifetime.sh — Run the tadpole through its entire lifecycle.
# Proves the life system works: spark finds organs, respects cadence,
# and the heart beats on schedule.
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
cp -r "$SCRIPT_DIR/organs" "$SCRIPT_DIR/organs.conf" "$TDIR/"

HEART="$TDIR/organs/heart"
CONF="$TDIR/organs.conf"

# --- Test 1: First heartbeat ---
"$SPARK" "$CONF"
sleep 1  # let nohup'd process run

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

# --- Clean up PID so next spark can check cadence ---
[ -f "$HEART/.spark.pid" ] && {
  pid=$(cat "$HEART/.spark.pid")
  kill "$pid" 2>/dev/null || true
  rm -f "$HEART/.spark.pid"
}

# --- Test 2: Cadence blocks immediate re-spark ---
"$SPARK" "$CONF" > "$TDIR/spark-out.txt" 2>&1 || true
sleep 1

if [ "$(cat "$HEART/beats.count")" = "1" ]; then
  pass "cadence prevented immediate re-spark (beats still 1)"
else
  fail "cadence should block re-spark, but beats is $(cat "$HEART/beats.count")"
fi

# --- Test 3: Expired cadence allows re-spark ---
# Set .spark.last to 10 minutes ago
echo $(($(date +%s) - 600)) > "$HEART/.spark.last"

"$SPARK" "$CONF"
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
