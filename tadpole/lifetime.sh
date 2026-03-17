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
    echo "FAIL: $cmd not found. Install: apt install mosquitto mosquitto-clients" >&2
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
MQTT_PORT=$(free-port)

# Start mosquitto on that port, listening on all interfaces (for Docker)
MQTT_CONF="$TDIR/mosquitto.conf"
echo "listener $MQTT_PORT 0.0.0.0" > "$MQTT_CONF"
echo "allow_anonymous true" >> "$MQTT_CONF"
mosquitto -c "$MQTT_CONF" &
MQTT_PID=$!
sleep 0.5

# Override MQTT_PORT for our test broker
echo "MQTT_PORT=$MQTT_PORT" >> "$TDIR/life.conf"

HEART="$TDIR/organs/heart"
TAIL="$TDIR/organs/tail"
GANGLION="$TDIR/organs/ganglion"

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
#  PART 2: Nervous system (heart → MQTT → ganglion → stimulus → tail)
# ===================================================================

# Cycle 1: heart publishes to MQTT, ganglion drains and writes stimulus.txt
echo $(($(date +%s) - 600)) > "$HEART/.spark.last"
echo $(($(date +%s) - 600)) > "$GANGLION/.spark.last"
"$SPARK"
sleep 3

if [ "$(cat "$HEART/beats.count")" = "2" ]; then
  pass "heart beat 2 (published to MQTT)"
else
  fail "expected beat 2, got: $(cat "$HEART/beats.count" 2>/dev/null)"
fi

if grep -q "^ok routed" "$GANGLION/health.txt" 2>/dev/null; then
  pass "ganglion routed MQTT message to tail stimulus.txt"
else
  fail "ganglion should have routed, got: $(cat "$GANGLION/health.txt" 2>/dev/null || echo 'missing')"
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
#  PART 3: Distributed body parts
#  Two separate directories share one broker. Heart on body-a, tail on body-b.
#  Same pattern as two machines — different organ sets, same MQTT.
# ===================================================================

# Build body-a: heart + ganglion (no tail)
BODY_A=$(mktemp -d)
mkdir -p "$BODY_A/organs"
cp -r "$SCRIPT_DIR/organs/heart" "$BODY_A/organs/"
cp -r "$SCRIPT_DIR/organs/ganglion" "$BODY_A/organs/"
chmod +x "$BODY_A/organs/"*/live.sh
cat > "$BODY_A/life.conf" <<EOF
ORGANS=organs/heart:organs/ganglion
MQTT_HOST=localhost
MQTT_PORT=$MQTT_PORT
EOF

# Build body-b: ganglion + tail (no heart)
BODY_B=$(mktemp -d)
mkdir -p "$BODY_B/organs"
cp -r "$SCRIPT_DIR/organs/ganglion" "$BODY_B/organs/"
cp -r "$SCRIPT_DIR/organs/tail" "$BODY_B/organs/"
chmod +x "$BODY_B/organs/"*/live.sh
cat > "$BODY_B/life.conf" <<EOF
ORGANS=organs/ganglion:organs/tail
MQTT_HOST=localhost
MQTT_PORT=$MQTT_PORT
EOF

trap 'kill $MQTT_PID 2>/dev/null; rm -rf "$TDIR" "$BODY_A" "$BODY_B"' EXIT

# Cycle 1: body-a heart beats, publishes to MQTT
cd "$BODY_A" && "$SPARK"
sleep 3

if [ -f "$BODY_A/organs/heart/health.txt" ] && grep -q "^ok beat" "$BODY_A/organs/heart/health.txt"; then
  pass "body-a: heart beat (separate dir)"
else
  fail "body-a: heart should beat, got: $(cat "$BODY_A/organs/heart/health.txt" 2>/dev/null || echo 'missing')"
fi

# Cycle 2: body-b ganglion drains MQTT, routes to tail
cd "$BODY_B" && "$SPARK"
sleep 3

if grep -q "^ok routed" "$BODY_B/organs/ganglion/health.txt" 2>/dev/null; then
  pass "body-b: ganglion received signal from body-a"
else
  fail "body-b: ganglion should route, got: $(cat "$BODY_B/organs/ganglion/health.txt" 2>/dev/null || echo 'missing')"
fi

# Cycle 3: body-b spark sees tail stimulus, wakes tail
cd "$BODY_B" && "$SPARK"
sleep 1

if [ -f "$BODY_B/organs/tail/health.txt" ] && grep -q "^ok swimming" "$BODY_B/organs/tail/health.txt"; then
  pass "body-b: tail swam from body-a signal (distributed!)"
else
  fail "body-b: tail should swim, got: $(cat "$BODY_B/organs/tail/health.txt" 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  PART 4: Immune system (lymph node)
#  Scans organ health, detects issues, cleans overflows.
# ===================================================================

cd "$TDIR"
LYMPH="$TDIR/organs/lymph"

# Reset cadences so lymph can fire
echo $(($(date +%s) - 600)) > "$LYMPH/.spark.last"
"$SPARK"
sleep 1

if [ -f "$LYMPH/health.txt" ] && grep -q "^ok" "$LYMPH/health.txt"; then
  pass "lymph node reports healthy"
else
  fail "lymph should report ok, got: $(cat "$LYMPH/health.txt" 2>/dev/null || echo 'missing')"
fi

# Inject a sick organ: write "error" to heart's health.txt
echo "error cardiac arrest" > "$HEART/health.txt"
echo $(($(date +%s) - 600)) > "$LYMPH/.spark.last"
"$SPARK"
sleep 1

if grep -q "degraded" "$LYMPH/health.txt" && grep -q "heart:error" "$LYMPH/health.txt"; then
  pass "lymph node detected sick organ"
else
  fail "lymph should detect heart:error, got: $(cat "$LYMPH/health.txt" 2>/dev/null)"
fi

# Inject stimulus overflow: 200 lines into tail
for i in $(seq 1 200); do echo "noise $i" >> "$TAIL/stimulus.txt"; done
STALE_SECONDS=9999 MAX_STIMULUS_LINES=50 "$TDIR/organs/lymph/live.sh" 2>/dev/null
stim_lines=$(wc -l < "$TAIL/stimulus.txt")

if [ "$stim_lines" -le 50 ]; then
  pass "lymph node truncated stimulus overflow ($stim_lines lines)"
else
  fail "lymph should truncate to 50 lines, got: $stim_lines"
fi

# ===================================================================
#  PART 5: Circulatory system (payload transfer between organs)
#  Stomach produces a payload → circ-put → ref in stimulus → tail retrieves
# ===================================================================

cd "$TDIR"
STOMACH="$TDIR/organs/stomach"
export CIRC_DIR="$TDIR/.circ"

# Reset cadences
echo $(($(date +%s) - 600)) > "$STOMACH/.spark.last"
echo $(($(date +%s) - 600)) > "$HEART/.spark.last"
> "$TAIL/stimulus.txt"  # clear any leftover stimulus

"$SPARK"
sleep 1

if [ -f "$STOMACH/health.txt" ] && grep -q "^ok meal" "$STOMACH/health.txt"; then
  pass "stomach produced a meal"
else
  fail "stomach should produce meal, got: $(cat "$STOMACH/health.txt" 2>/dev/null || echo 'missing')"
fi

# Check the circulatory system has a file
circ_files=$(ls "$CIRC_DIR" 2>/dev/null | wc -l)
if [ "$circ_files" -ge 1 ]; then
  pass "circulatory system stored payload ($circ_files files)"
else
  fail "circulatory system should have files, got: $circ_files"
fi

# Tail may have already consumed stimulus (both ran in same cycle).
# Check health.txt for proof of payload retrieval.
if grep -q "^ok swimming (payload:" "$TAIL/health.txt" 2>/dev/null; then
  pass "tail retrieved payload via circulatory reference"
else
  # Tail hasn't run yet — spark it
  "$SPARK"
  sleep 1
  if grep -q "^ok swimming (payload:" "$TAIL/health.txt" 2>/dev/null; then
    pass "tail retrieved payload via circulatory reference"
  else
    fail "tail should have payload, got: $(cat "$TAIL/health.txt" 2>/dev/null || echo 'missing')"
  fi
fi

# Dedup: put identical content twice, should not create a new file
echo "dedup test payload" | circ-put - >/dev/null
circ_before=$(ls "$CIRC_DIR" | wc -l)
echo "dedup test payload" | circ-put - >/dev/null
circ_after=$(ls "$CIRC_DIR" | wc -l)
if [ "$circ_after" -eq "$circ_before" ]; then
  pass "circulatory system deduplicates same content"
else
  fail "dedup failed: had $circ_before files, now $circ_after"
fi

# ===================================================================
echo ""
echo "# $PASSED/$TESTS passed"
[ "$FAILED" -gt 0 ] && exit 1
exit 0
