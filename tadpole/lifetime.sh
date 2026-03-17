#!/usr/bin/env bash
# lifetime.sh — Run the tadpole through its entire lifecycle.
# Tests heartbeat, cadence, AND the nervous system (MQTT).
# Requires: mosquitto, mosquitto_pub, mosquitto_sub
#
# Usage: ./lifetime.sh [path/to/spark.sh]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SPARK="${1:-$BIN_ROOT/life/spark.sh}"
export PATH="$BIN_ROOT:$PATH"

# --- Prerequisites ---
for cmd in mosquitto mqtt-pub mqtt-sub; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "FAIL: $cmd not found. Install mosquitto: apt install mosquitto mosquitto-clients" >&2
    exit 1
  fi
done

if [ ! -x "$SPARK" ]; then
  echo "FAIL: spark.sh not found at $SPARK" >&2
  exit 1
fi

# --- Helpers ---
TESTS=0; PASSED=0; FAILED=0
pass() { TESTS=$((TESTS+1)); PASSED=$((PASSED+1)); echo "ok $TESTS - $1"; }
fail() { TESTS=$((TESTS+1)); FAILED=$((FAILED+1)); echo "not ok $TESTS - $1"; }

# --- Setup: copy tadpole to temp dir, start local MQTT broker ---
TDIR=$(mktemp -d)
trap 'kill $MQTT_PID 2>/dev/null; rm -rf "$TDIR"' EXIT
cp -r "$SCRIPT_DIR/organs" "$SCRIPT_DIR/life.conf" "$TDIR/"
chmod +x "$TDIR/organs/"*/live.sh

# Find a free port for the broker
MQTT_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")

# Start mosquitto on that port (no config file, no auth)
mosquitto -p "$MQTT_PORT" &
MQTT_PID=$!
sleep 0.5

# Override MQTT_PORT for our test broker
echo "MQTT_PORT=$MQTT_PORT" >> "$TDIR/life.conf"

HEART="$TDIR/organs/heart"
TAIL="$TDIR/organs/tail"
SPINE="$TDIR/organs/spine"

# ===================================================================
#  PART 1: Heartbeat (same as before)
# ===================================================================

cd "$TDIR"
"$SPARK"
sleep 1

if [ -f "$HEART/health.txt" ] && grep -q "^ok " "$HEART/health.txt"; then
  pass "heart beats and reports health"
else
  fail "heart health.txt missing or wrong: $(cat "$HEART/health.txt" 2>/dev/null || echo 'missing')"
fi

if [ "$(cat "$HEART/beats.count" 2>/dev/null)" = "1" ]; then
  pass "beat count is 1"
else
  fail "beat count should be 1, got: $(cat "$HEART/beats.count" 2>/dev/null || echo 'missing')"
fi

# Cadence blocks re-spark
sleep 1
"$SPARK" > "$TDIR/spark-out.txt" 2>&1 || true
sleep 1

if [ "$(cat "$HEART/beats.count")" = "1" ]; then
  pass "cadence blocks immediate re-spark"
else
  fail "cadence should block, but beats is $(cat "$HEART/beats.count")"
fi

# ===================================================================
#  PART 2: Nervous system (heart → MQTT → spine → stimulus → tail)
# ===================================================================

# Cycle 1: heart publishes to MQTT, spine drains and writes stimulus.txt
echo $(($(date +%s) - 600)) > "$HEART/.spark.last"
echo $(($(date +%s) - 600)) > "$SPINE/.spark.last"
"$SPARK"
sleep 3

if [ "$(cat "$HEART/beats.count")" = "2" ]; then
  pass "heart beat 2 (published to MQTT)"
else
  fail "expected beat 2, got: $(cat "$HEART/beats.count" 2>/dev/null)"
fi

if grep -q "^ok routed" "$SPINE/health.txt" 2>/dev/null; then
  pass "spine routed MQTT message to tail stimulus.txt"
else
  fail "spine should have routed, got: $(cat "$SPINE/health.txt" 2>/dev/null || echo 'missing')"
fi

# Cycle 2: cron fires again, spark sees tail has stimulus, wakes it
"$SPARK"
sleep 1

if [ -f "$TAIL/health.txt" ] && grep -q "^ok swimming" "$TAIL/health.txt"; then
  pass "tail woke on stimulus (no cadence, just signal)"
else
  fail "tail should be swimming, got: $(cat "$TAIL/health.txt" 2>/dev/null || echo 'missing')"
fi

# ===================================================================
echo ""
echo "# $PASSED/$TESTS passed"
[ "$FAILED" -gt 0 ] && exit 1
exit 0
