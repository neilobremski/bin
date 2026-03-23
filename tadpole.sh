#!/usr/bin/env bash
# tadpole.sh — Run the tadpole organism.
#
# Two body parts:
#   Brain (Docker): PFC + hippocampus — thinks, generates replies
#   Body (host):    comms + ganglion + heart + hippocampus + lymph + stomach + tail
#
# Usage:
#   ./tadpole.sh              # mock gmail (default)
#   ./tadpole.sh --real       # real gmail (needs GAS_BRIDGE_URL/KEY)
#
# Prerequisites:
#   - docker
#   - mosquitto (apt install mosquitto mosquitto-clients)
#   - Bedrock env vars: ANTHROPIC_BEDROCK_BASE_URL, ANTHROPIC_CUSTOM_HEADERS, etc.
#
# Mock email interaction (separate terminal):
#   # Send an email:
#   echo '{"id":"001","from":"you@example.com","subject":"Hello","body":"Hi tadpole!","labels":["UNREAD","Tadpole"]}' \
#     > ~/.tadpole/gmail/inbox/001.json
#   # Check replies:
#   cat ~/.tadpole/gmail/sent/*.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Prerequisites ---
command -v docker >/dev/null || { echo "error: docker not found" >&2; exit 1; }
command -v mosquitto >/dev/null || { echo "error: mosquitto not found (apt install mosquitto mosquitto-clients)" >&2; exit 1; }

# --- Shared directories ---
CIRC_DIR="${CIRC_DIR:-$HOME/.life/circ}"
mkdir -p "$CIRC_DIR"
chmod 1777 "$CIRC_DIR" 2>/dev/null || true

# Mock gmail setup (default unless --real)
if [ "${1:-}" = "--real" ]; then
    shift
    echo "Using real Gmail (GAS bridge)"
    unset GMAIL_MOCK_DIR 2>/dev/null || true
else
    GMAIL_MOCK_DIR="${GMAIL_MOCK_DIR:-$HOME/.tadpole/gmail}"
    mkdir -p "$GMAIL_MOCK_DIR/inbox" "$GMAIL_MOCK_DIR/sent"
    [ -f "$GMAIL_MOCK_DIR/next_id.txt" ] || echo "1" > "$GMAIL_MOCK_DIR/next_id.txt"
    export GMAIL_MOCK_DIR
    echo "Using mock Gmail at $GMAIL_MOCK_DIR"
fi

# --- Mosquitto broker ---
MQTT_HOST="${MQTT_HOST:-localhost}"
MQTT_PORT="${MQTT_PORT:-1883}"
MOSQUITTO_PID=""
MOSQUITTO_CONF=""

if ! mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" ${MQTT_USER:+-u "$MQTT_USER"} ${MQTT_PASS:+-P "$MQTT_PASS"} -t "test/startup" -m "ping" -q 0 2>/dev/null; then
    echo "Starting temporary mosquitto broker on port $MQTT_PORT..."
    MOSQUITTO_CONF=$(mktemp)
    cat > "$MOSQUITTO_CONF" <<EOF
listener $MQTT_PORT
allow_anonymous true
EOF
    mosquitto -c "$MOSQUITTO_CONF" &
    MOSQUITTO_PID=$!
    sleep 1
fi

export MQTT_HOST MQTT_PORT
[ -n "${MQTT_USER:-}" ] && export MQTT_USER
[ -n "${MQTT_PASS:-}" ] && export MQTT_PASS

# --- Build brain image if needed ---
IMAGE="brain:latest"
if ! docker image inspect "$IMAGE" > /dev/null 2>&1; then
    echo "Building brain Docker image..."
    docker build -t "$IMAGE" -f "$SCRIPT_DIR/brain/Dockerfile" "$SCRIPT_DIR"
fi

# --- Cleanup handler ---
BRAIN_CONTAINER="tadpole-brain-$$"
BODY_PID=""
cleanup() {
    echo ""
    echo "Shutting down tadpole..."
    [ -n "$BODY_PID" ] && kill "$BODY_PID" 2>/dev/null
    docker rm -f "$BRAIN_CONTAINER" 2>/dev/null
    [ -n "$MOSQUITTO_PID" ] && kill "$MOSQUITTO_PID" 2>/dev/null
    [ -n "$MOSQUITTO_CONF" ] && rm -f "$MOSQUITTO_CONF"
    echo "Tadpole stopped."
}
trap cleanup EXIT INT TERM

# --- Start body (host) ---
echo "Starting body (comms + ganglion + organs)..."
SPARK_INTERVAL="${SPARK_INTERVAL:-60}" \
CIRC_DIR="$CIRC_DIR" \
CIRC_LOCAL_ONLY=1 \
    bash "$SCRIPT_DIR/life/spark-loop.sh" "$SCRIPT_DIR/tadpole/life.conf" &
BODY_PID=$!

# --- Start brain (Docker) ---
echo "Starting brain (PFC + hippocampus in Docker)..."
echo "---"
docker run --rm \
    --name "$BRAIN_CONTAINER" \
    -v "$SCRIPT_DIR/tadpole:/brain/.claude" \
    -v "$CIRC_DIR:/brain/.life/circ" \
    -e "MQTT_HOST=$MQTT_HOST" \
    -e "MQTT_PORT=$MQTT_PORT" \
    ${MQTT_USER:+-e "MQTT_USER=$MQTT_USER"} \
    ${MQTT_PASS:+-e "MQTT_PASS=$MQTT_PASS"} \
    -e "BODY_PART=brain" \
    -e "DEFAULT_ORGAN=pfc" \
    -e "MEMORY_DB=/brain/organs/hippocampus/memory.db" \
    -e "CIRC_DIR=/brain/.life/circ" \
    -e "CIRC_LOCAL_ONLY=1" \
    -e "SPARK_INTERVAL=${SPARK_INTERVAL:-60}" \
    -e "GANGLION_LISTEN_DURATION=${GANGLION_LISTEN_DURATION:-30}" \
    ${ANTHROPIC_BEDROCK_BASE_URL:+-e "ANTHROPIC_BEDROCK_BASE_URL=$ANTHROPIC_BEDROCK_BASE_URL"} \
    ${ANTHROPIC_CUSTOM_HEADERS:+-e "ANTHROPIC_CUSTOM_HEADERS=$ANTHROPIC_CUSTOM_HEADERS"} \
    ${ANTHROPIC_API_KEY:+-e "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"} \
    ${CLAUDE_CODE_USE_BEDROCK:+-e "CLAUDE_CODE_USE_BEDROCK=$CLAUDE_CODE_USE_BEDROCK"} \
    ${CLAUDE_CODE_SKIP_BEDROCK_AUTH:+-e "CLAUDE_CODE_SKIP_BEDROCK_AUTH=$CLAUDE_CODE_SKIP_BEDROCK_AUTH"} \
    --network host \
    "$IMAGE"
