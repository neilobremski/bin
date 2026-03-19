#!/usr/bin/env bash
# lifetime-docker.sh — Docker-isolated integration test for the life system.
# Proves the nervous system works across separate containers (body parts).
#
# Container A (body-a): heart + ganglion + lymph
# Container B (body-b): ganglion + tail + stomach
# MQTT broker: mosquitto in a third container
#
# Uses docker compose to orchestrate. Self-contained: builds, tests, cleans up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Prerequisites ---
for cmd in docker; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "FAIL: $cmd not found" >&2; exit 1; }
done

# Check for docker compose (plugin or standalone)
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "FAIL: docker compose not found (tried 'docker compose' and 'docker-compose')" >&2
  exit 1
fi

# --- TAP Helpers (same style as lifetime.sh) ---
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

# Helpers to run commands in containers
run_a() { $COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" exec -T body-a "$@"; }
run_b() { $COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" exec -T body-b "$@"; }

# --- Setup: mosquitto config, build, start ---
MOSQUITTO_CONF="$SCRIPT_DIR/mosquitto-docker.conf"
trap 'echo ""; echo "# Cleaning up..."; $COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" down -v --remove-orphans 2>/dev/null; rm -f "$MOSQUITTO_CONF"' EXIT

cat > "$MOSQUITTO_CONF" << 'EOF'
listener 1883 0.0.0.0
allow_anonymous true
EOF

echo "# Building containers..."
$COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" build --quiet 2>&1 | tail -1 || true

echo "# Starting containers..."
$COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" up -d 2>&1 | tail -3 || true
sleep 2

# Verify containers are running
if ! $COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" ps --status running | grep -q body-a; then
  echo "FAIL: body-a container not running" >&2
  $COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" logs body-a
  exit 1
fi
if ! $COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" ps --status running | grep -q body-b; then
  echo "FAIL: body-b container not running" >&2
  $COMPOSE -f "$SCRIPT_DIR/docker-compose.yml" logs body-b
  exit 1
fi

# --- Prepare work directories inside containers ---
# Each container gets its own life.conf, organs, and ganglion in /opt/bin/tadpole/work/

# Body A: heart + ganglion + lymph
run_a bash -c '
  mkdir -p /opt/bin/tadpole/work
  cd /opt/bin/tadpole/work
  cp -r /opt/bin/tadpole/organs/heart organs_heart
  cp -r /opt/bin/tadpole/organs/lymph organs_lymph
  cp -r /opt/bin/ganglion ganglion
  chmod +x organs_heart/live.sh organs_lymph/live.sh ganglion/live.sh

  # Create organ.conf for heart (cadence 1 min)
  echo "CADENCE=1" > organs_heart/organ.conf
  echo "CADENCE=1" > organs_lymph/organ.conf

  cat > life.conf << INNEREOF
ORGANS=organs_heart:ganglion:organs_lymph
MQTT_HOST=mqtt
MQTT_PORT=1883
BODY_PART=body-a
GANGLION_CLIENT_ID=ganglion-body-a
GANGLION_DB=/opt/bin/tadpole/work/ganglion.db
CIRC_LOCAL_ONLY=1
CIRC_DIR=/opt/bin/tadpole/work/.circ
INNEREOF
'

# Body B: ganglion + tail + stomach
run_b bash -c '
  mkdir -p /opt/bin/tadpole/work
  cd /opt/bin/tadpole/work
  cp -r /opt/bin/tadpole/organs/tail organs_tail
  cp -r /opt/bin/tadpole/organs/stomach organs_stomach
  cp -r /opt/bin/ganglion ganglion
  chmod +x organs_tail/live.sh organs_stomach/live.sh ganglion/live.sh

  cat > life.conf << INNEREOF
ORGANS=ganglion:organs_tail:organs_stomach
MQTT_HOST=mqtt
MQTT_PORT=1883
BODY_PART=body-b
GANGLION_CLIENT_ID=ganglion-body-b
GANGLION_DB=/opt/bin/tadpole/work/ganglion.db
CIRC_LOCAL_ONLY=1
CIRC_DIR=/opt/bin/tadpole/work/.circ
INNEREOF
'

echo "# Running tests..."
echo ""

# ===================================================================
#  TEST 1: Heart beats on body-a (spark launches organ in container)
# ===================================================================

run_a bash -c 'cd /opt/bin/tadpole/work && /opt/bin/life/spark.sh'
wait_for 6 'run_a cat /opt/bin/tadpole/work/organs_heart/health.txt 2>/dev/null | grep -q "^ok beat 1"'

if run_a cat /opt/bin/tadpole/work/organs_heart/health.txt 2>/dev/null | grep -q "^ok beat 1"; then
  pass "heart beats on body-a"
else
  fail "heart should beat on body-a, got: $(run_a cat /opt/bin/tadpole/work/organs_heart/health.txt 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  TEST 2: Ganglion on body-a scans and broadcasts registry via MQTT
# ===================================================================

run_a bash -c 'cd /opt/bin/tadpole/work && echo $(($(date +%s) - 600)) > ganglion/.spark.last && /opt/bin/life/spark.sh'
wait_for 8 'run_a cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null | grep -q "^ok scanned"'

if run_a cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null | grep -q "^ok scanned"; then
  pass "ganglion on body-a scanned and broadcast"
else
  fail "ganglion on body-a should scan, got: $(run_a cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  TEST 3: Ganglion on body-b scans and broadcasts
# ===================================================================

# Spark body-b's tail to give it a health.txt first (so ganglion has something to scan)
run_b bash -c 'cd /opt/bin/tadpole/work && echo "idle" > organs_tail/stimulus.txt && /opt/bin/life/spark.sh'
sleep 1
run_b bash -c 'cd /opt/bin/tadpole/work && echo $(($(date +%s) - 600)) > ganglion/.spark.last && /opt/bin/life/spark.sh'
wait_for 8 'run_b cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null | grep -q "^ok scanned"'

if run_b cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null | grep -q "^ok scanned"; then
  pass "ganglion on body-b scanned and broadcast"
else
  fail "ganglion on body-b should scan, got: $(run_b cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  TEST 4: Cross-body stimulus — body-a sends "swim now" to tail on body-b
# ===================================================================

# Send stimulus from body-a. Tail is NOT local to body-a, so it goes via MQTT.
run_a bash -c '
  cd /opt/bin/tadpole/work && source life.conf
  export MQTT_HOST MQTT_PORT BODY_PART GANGLION_DB ORGANS CONF_DIR=/opt/bin/tadpole/work
  stimulus send tail "swim now"
'

# Body-b's ganglion drains MQTT and delivers to local tail
run_b bash -c 'cd /opt/bin/tadpole/work && echo $(($(date +%s) - 600)) > ganglion/.spark.last && /opt/bin/life/spark.sh'
wait_for 8 'run_b cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null | grep -q "routed [1-9]"'

# Spark again so tail processes the stimulus
run_b bash -c 'cd /opt/bin/tadpole/work && /opt/bin/life/spark.sh'
wait_for 6 'run_b cat /opt/bin/tadpole/work/organs_tail/health.txt 2>/dev/null | grep -q "^ok splish splash"'

if run_b cat /opt/bin/tadpole/work/organs_tail/health.txt 2>/dev/null | grep -q "^ok splish splash"; then
  pass "cross-body stimulus: body-a -> MQTT -> body-b tail swims"
else
  fail "tail on body-b should swim, got: $(run_b cat /opt/bin/tadpole/work/organs_tail/health.txt 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  TEST 5: Stomach on body-b digests and sends to tail (intra-body)
# ===================================================================

# Reset tail state
run_b bash -c '> /opt/bin/tadpole/work/organs_tail/health.txt; > /opt/bin/tadpole/work/organs_tail/stimulus.txt 2>/dev/null || true'

# Feed stomach
run_b bash -c 'echo "eat" > /opt/bin/tadpole/work/organs_stomach/stimulus.txt'
run_b bash -c 'cd /opt/bin/tadpole/work && echo $(($(date +%s) - 600)) > ganglion/.spark.last && /opt/bin/life/spark.sh'
wait_for 10 'run_b cat /opt/bin/tadpole/work/organs_stomach/health.txt 2>/dev/null | grep -q "^ok yum yum"'

if run_b cat /opt/bin/tadpole/work/organs_stomach/health.txt 2>/dev/null | grep -q "^ok yum yum"; then
  pass "stomach on body-b digested food"
else
  fail "stomach should produce meal, got: $(run_b cat /opt/bin/tadpole/work/organs_stomach/health.txt 2>/dev/null || echo 'missing')"
fi

# Spark cycles for tail to get the circulatory payload
for cycle in 1 2 3 4; do
  run_b bash -c 'cd /opt/bin/tadpole/work && echo $(($(date +%s) - 600)) > ganglion/.spark.last && /opt/bin/life/spark.sh'
  sleep 2
  if run_b cat /opt/bin/tadpole/work/organs_tail/health.txt 2>/dev/null | grep -q "^ok splish splash"; then break; fi
done

if run_b cat /opt/bin/tadpole/work/organs_tail/health.txt 2>/dev/null | grep -q "^ok splish splash"; then
  pass "tail on body-b swam after stomach fed it"
else
  fail "tail should swim after stomach, got: $(run_b cat /opt/bin/tadpole/work/organs_tail/health.txt 2>/dev/null || echo 'missing')"
fi

# ===================================================================
#  TEST 7: Body-b's ganglion sees body-a's organs in registry
# ===================================================================

# Run both ganglions one more time to ensure registries are synced
run_a bash -c 'cd /opt/bin/tadpole/work && echo $(($(date +%s) - 600)) > ganglion/.spark.last && /opt/bin/life/spark.sh'
sleep 2
run_b bash -c 'cd /opt/bin/tadpole/work && echo $(($(date +%s) - 600)) > ganglion/.spark.last && /opt/bin/life/spark.sh'
wait_for 8 'run_b cat /opt/bin/tadpole/work/ganglion/health.txt 2>/dev/null | grep -q "^ok scanned"'

# Query body-b's registry for body-a's heart
body_b_registry=$(run_b bash -c '
  export GANGLION_DB=/opt/bin/tadpole/work/ganglion.db
  stimulus query heart 2>/dev/null || true
')

if echo "$body_b_registry" | grep -q "body-a"; then
  pass "body-b registry contains body-a organs (cross-body discovery)"
else
  fail "body-b should see body-a's heart, got: $body_b_registry"
fi

# ===================================================================
echo ""
echo "# $PASSED/$TESTS passed"
[ "$FAILED" -gt 0 ] && exit 1
exit 0
