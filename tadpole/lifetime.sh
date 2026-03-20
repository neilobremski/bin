#!/usr/bin/env bash
# lifetime.sh — Integration test for the life system (ganglion v2).
# Tests: heartbeat + cadence, stimulus routing, source routing, immune system.
# Requires: mosquitto, mqtt-pub, mqtt-sub, sqlite3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SPARK="$BIN_ROOT/life/spark.sh"
export PATH="$BIN_ROOT:$PATH"

# --- Prerequisites ---
for cmd in mosquitto mqtt-pub mqtt-sub sqlite3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "FAIL: $cmd not found" >&2; exit 1; }
done
[ -x "$SPARK" ] || { echo "FAIL: spark.sh not found" >&2; exit 1; }

# --- Helpers ---
TESTS=0; PASSED=0; FAILED=0
pass() { TESTS=$((TESTS+1)); PASSED=$((PASSED+1)); echo "ok $TESTS - $1"; }
fail() { TESTS=$((TESTS+1)); FAILED=$((FAILED+1)); echo "not ok $TESTS - $1"; }

wait_for() {
  local timeout=$1; shift; local i=0
  while [ $i -lt $timeout ]; do
    if eval "$@" 2>/dev/null; then return 0; fi
    sleep 0.5; i=$((i + 1))
  done
  return 1
}

# --- Setup: fresh copy + local MQTT broker ---
TDIR=$(mktemp -d)
trap 'kill $MQTT_PID 2>/dev/null; rm -rf "$TDIR"' EXIT

# Copy organs and ganglion into test dir
cp -r "$SCRIPT_DIR/organs" "$SCRIPT_DIR/life.conf" "$TDIR/"
cp -r "$BIN_ROOT/ganglion" "$TDIR/ganglion"
chmod +x "$TDIR/organs/"*/live.sh "$TDIR/ganglion/live.sh"

MQTT_PORT=$(free-port)
echo "listener $MQTT_PORT 0.0.0.0" > "$TDIR/mosquitto.conf"
echo "allow_anonymous true" >> "$TDIR/mosquitto.conf"
mosquitto -c "$TDIR/mosquitto.conf" &
MQTT_PID=$!
sleep 0.5

# Configure for test: local MQTT, ganglion DB in temp dir, test body part
cat >> "$TDIR/life.conf" << EOF
MQTT_PORT=$MQTT_PORT
GANGLION_CLIENT_ID=test-$$
GANGLION_DB=$TDIR/ganglion.db
BODY_PART=test
CIRC_LOCAL_ONLY=1
CIRC_DIR=$TDIR/.circ
# brain/comms/eye excluded: they need external services (claude CLI, gmail, GAS bridge)
ORGANS=organs/heart:ganglion:organs/tail:organs/lymph:organs/stomach:organs/hippocampus
MEMORY_DB=$TDIR/organs/hippocampus/memory.db
EOF

HEART="$TDIR/organs/heart"
TAIL="$TDIR/organs/tail"
GANGLION="$TDIR/ganglion"
LYMPH="$TDIR/organs/lymph"
HIPPO="$TDIR/organs/hippocampus"
STOMACH="$TDIR/organs/stomach"

# ===================================================================
#  PART 1: Heartbeat (cron-sparked organ, cadence enforcement)
# ===================================================================

cd "$TDIR"
"$SPARK"
wait_for 6 '[ -f "$HEART/health.txt" ]'

if grep -q "^ok beat 1" "$HEART/health.txt" 2>/dev/null; then
  pass "heart beats (cron-sparked)"
else
  fail "heart should beat, got: $(cat "$HEART/health.txt" 2>/dev/null || echo 'missing')"
fi

# Cadence blocks re-spark
wait_for 4 '[ -f "$HEART/.spark.last" ]'
"$SPARK" > /dev/null 2>&1 || true
sleep 1

if [ "$(cat "$HEART/beats.count")" = "1" ]; then
  pass "cadence blocks re-spark"
else
  fail "cadence should block, beats is $(cat "$HEART/beats.count")"
fi

# ===================================================================
#  PART 2: Nervous system (stimulus send → ganglion MQTT → tail)
# ===================================================================

# Send stimulus to tail via the stimulus CLI (uses MQTT)
cd "$TDIR"
source "$TDIR/life.conf"
export MQTT_HOST MQTT_PORT BODY_PART GANGLION_DB ORGANS
stimulus send tail "swim now"

# Let ganglion drain and route it
echo $(($(date +%s) - 600)) > "$GANGLION/.spark.last"
"$SPARK"
wait_for 8 'grep -q "^ok scanned" "$GANGLION/health.txt"'

if grep -q "^ok scanned" "$GANGLION/health.txt" 2>/dev/null; then
  pass "ganglion scanned and routed"
else
  fail "ganglion should scan, got: $(cat "$GANGLION/health.txt" 2>/dev/null || echo 'missing')"
fi

# Tail wakes on stimulus (dormant organ, no cadence)
"$SPARK"
wait_for 4 'grep -q "^ok splish splash" "$TAIL/health.txt"'

if grep -q "^ok splish splash" "$TAIL/health.txt" 2>/dev/null; then
  pass "tail woke on stimulus (event-driven)"
else
  fail "tail should swim, got: $(cat "$TAIL/health.txt" 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  PART 3: Source routing (stomach → stimulus send tail → tail)
# ===================================================================

# Feed the stomach (dormant until stimulus)
echo "eat" > "$STOMACH/stimulus.txt"
echo $(($(date +%s) - 600)) > "$GANGLION/.spark.last"
> "$TAIL/stimulus.txt"
> "$TAIL/health.txt"

"$SPARK"
wait_for 10 'grep -q "^ok yum yum" "$STOMACH/health.txt"'

if grep -q "^ok yum yum" "$STOMACH/health.txt" 2>/dev/null; then
  pass "stomach produced meal via stimulus send"
else
  fail "stomach should produce meal, got: $(cat "$STOMACH/health.txt" 2>/dev/null || echo 'missing')"
fi

# Run spark cycles until tail gets the circulatory payload
for cycle in 1 2 3 4; do
  echo $(($(date +%s) - 600)) > "$GANGLION/.spark.last"
  "$SPARK"
  sleep 2
  if grep -q "^ok splish splash (payload:" "$TAIL/health.txt" 2>/dev/null; then break; fi
done

if grep -q "^ok splish splash (payload:" "$TAIL/health.txt" 2>/dev/null; then
  pass "tail retrieved circulatory payload via nervous system"
else
  fail "tail should have payload, got: $(cat "$TAIL/health.txt" 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  PART 4: Immune system (lymph node health scan + cleanup)
# ===================================================================

echo $(($(date +%s) - 600)) > "$LYMPH/.spark.last"
"$SPARK"
wait_for 4 '[ -f "$LYMPH/health.txt" ]'

if grep -q "^ok" "$LYMPH/health.txt" 2>/dev/null; then
  pass "lymph node reports healthy"
else
  fail "lymph should report ok, got: $(cat "$LYMPH/health.txt" 2>/dev/null || echo 'missing')"
fi

# Inject sick organ
echo "error cardiac arrest" > "$HEART/health.txt"
echo $(($(date +%s) - 600)) > "$LYMPH/.spark.last"
"$SPARK"
wait_for 4 'grep -q "degraded" "$LYMPH/health.txt"'

if grep -q "degraded" "$LYMPH/health.txt" 2>/dev/null; then
  pass "lymph node detected sick organ"
else
  fail "lymph should detect error, got: $(cat "$LYMPH/health.txt" 2>/dev/null)"
fi

# Stimulus overflow cleanup
for i in $(seq 1 200); do echo "noise $i" >> "$TAIL/stimulus.txt"; done
STALE_SECONDS=9999 MAX_STIMULUS_LINES=50 "$TDIR/organs/lymph/live.sh" 2>/dev/null
stim_lines=$(wc -l < "$TAIL/stimulus.txt")

if [ "$stim_lines" -le 50 ]; then
  pass "lymph truncated stimulus overflow ($stim_lines lines)"
else
  fail "should truncate to 50, got: $stim_lines"
fi

# ===================================================================
#  PART 5: Memory system (hippocampus stores and recalls)
# ===================================================================

# Store memories via stimulus
echo "remember: the tadpole ate its first meal" > "$HIPPO/stimulus.txt"
echo "remember important: swimming is the best activity" >> "$HIPPO/stimulus.txt"
> "$HIPPO/health.txt"
echo $(($(date +%s) - 600)) > "$HIPPO/.spark.last"
"$SPARK"
wait_for 8 'grep -q "stored 2" "$HIPPO/health.txt"'

if grep -q "stored 2" "$HIPPO/health.txt" 2>/dev/null; then
  pass "hippocampus stored memories from stimulus"
else
  fail "hippocampus should store 2 memories, got: $(cat "$HIPPO/health.txt" 2>/dev/null || echo 'missing')"
fi

# Dedup: same content should not increase count
echo "remember: the tadpole ate its first meal" > "$HIPPO/stimulus.txt"
> "$HIPPO/health.txt"
echo $(($(date +%s) - 600)) > "$HIPPO/.spark.last"
"$SPARK"
wait_for 8 'grep -q "memories" "$HIPPO/health.txt"'

total=$(sqlite3 "$TDIR/organs/hippocampus/memory.db" "SELECT COUNT(*) FROM memories;" 2>/dev/null || echo 0)
if [ "$total" = "2" ]; then
  pass "hippocampus deduplicates by content hash"
else
  fail "dedup should keep count at 2, got: $total"
fi

# ===================================================================
#  PART 6: Comms organ structure check
# ===================================================================

COMMS="$TDIR/organs/comms"
if [ -x "$COMMS/live.sh" ] && [ -f "$COMMS/organ.conf" ] && [ -f "$COMMS/comms.py" ]; then
  pass "comms organ has correct structure (live.sh, organ.conf, comms.py)"
else
  fail "comms organ missing files"
fi

cadence=$(CADENCE=""; source "$COMMS/organ.conf"; echo "${CADENCE:-}")
if [ "$cadence" = "1" ]; then
  pass "comms organ cadence is 1 minute"
else
  fail "comms cadence should be 1, got: $cadence"
fi

# ===================================================================
#  PART 7: Brain organ structure check
# ===================================================================

BRAIN="$TDIR/organs/brain"
if [ -x "$BRAIN/live.sh" ] && [ -f "$BRAIN/organ.conf" ] && [ -f "$BRAIN/brain.py" ]; then
  pass "brain organ has correct structure (live.sh, organ.conf, brain.py)"
else
  fail "brain organ missing files"
fi

brain_cadence=$(CADENCE=""; source "$BRAIN/organ.conf"; echo "${CADENCE:-}")
if [ "$brain_cadence" = "15" ]; then
  pass "brain organ cadence is 15 minutes"
else
  fail "brain cadence should be 15, got: $brain_cadence"
fi

# ===================================================================
#  PART 8: Gmail muscle structure check
# ===================================================================

if [ -x "$BIN_ROOT/gmail" ]; then
  pass "gmail muscle is executable"
else
  fail "gmail muscle not found or not executable"
fi

# ===================================================================
echo ""
echo "# $PASSED/$TESTS passed"
[ "$FAILED" -gt 0 ] && exit 1
exit 0
