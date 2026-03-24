#!/usr/bin/env bash
# tadpole.sh — Run the tadpole organism in a single Docker container.
#
# All organs run together: comms + PFC + hippocampus.
# No MQTT needed — stimulus routes locally via file delivery.
#
# Usage:
#   ./tadpole.sh              # mock gmail
#   ./tadpole.sh --real       # real gmail (needs GAS_BRIDGE_* env vars)
#   ./tadpole.sh down         # stop
#
# Mock emails (separate terminal):
#   echo '{"id":"001","from":"you@example.com","subject":"Hi","body":"Hello!","labels":["UNREAD","Tadpole"]}' \
#     > tadpole/.mock_gmail/inbox/001.json
#   cat tadpole/.mock_gmail/sent/*.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

command -v docker >/dev/null || { echo "error: docker not found" >&2; exit 1; }

[ "${1:-}" = "down" ] && { docker rm -f tadpole 2>/dev/null; exit 0; }

# Gmail mode
MOCK_ARGS=""
if [ "${1:-}" = "--real" ]; then
    shift
    echo "Using real Gmail"
else
    MOCK_DIR="tadpole/.mock_gmail"
    mkdir -p "$MOCK_DIR/inbox" "$MOCK_DIR/sent"
    [ -f "$MOCK_DIR/next_id.txt" ] || echo "1" > "$MOCK_DIR/next_id.txt"
    MOCK_ARGS="-v $(pwd)/$MOCK_DIR:/mock_gmail -e GMAIL_MOCK_DIR=/mock_gmail"
    echo "Mock Gmail: $(pwd)/$MOCK_DIR"
    echo "  Send: echo '{\"id\":\"001\",...}' > $(pwd)/$MOCK_DIR/inbox/001.json"
    echo "  Read: cat $(pwd)/$MOCK_DIR/sent/*.json"
fi

# Build
IMAGE="tadpole:latest"
docker build -q -t "$IMAGE" -f tadpole/Dockerfile . > /dev/null 2>&1 || \
    docker build -t "$IMAGE" -f tadpole/Dockerfile .

echo "Starting tadpole (Ctrl-C to stop)..."
echo "---"

# shellcheck disable=SC2086
exec docker run --rm \
    --name tadpole \
    --user "$(id -u):$(id -g)" \
    --entrypoint bash \
    -v "$(pwd)/tadpole:/tadpole/.claude" \
    $MOCK_ARGS \
    -e "COMMS_AUTO_CHECK=1" \
    -e "SPARK_INTERVAL=${SPARK_INTERVAL:-30}" \
    ${GAS_BRIDGE_URL:+-e "GAS_BRIDGE_URL=$GAS_BRIDGE_URL"} \
    ${GAS_BRIDGE_KEY:+-e "GAS_BRIDGE_KEY=$GAS_BRIDGE_KEY"} \
    ${ANTHROPIC_BEDROCK_BASE_URL:+-e "ANTHROPIC_BEDROCK_BASE_URL=$ANTHROPIC_BEDROCK_BASE_URL"} \
    ${ANTHROPIC_CUSTOM_HEADERS:+-e "ANTHROPIC_CUSTOM_HEADERS=$ANTHROPIC_CUSTOM_HEADERS"} \
    ${ANTHROPIC_API_KEY:+-e "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"} \
    ${CLAUDE_CODE_USE_BEDROCK:+-e "CLAUDE_CODE_USE_BEDROCK=$CLAUDE_CODE_USE_BEDROCK"} \
    ${CLAUDE_CODE_SKIP_BEDROCK_AUTH:+-e "CLAUDE_CODE_SKIP_BEDROCK_AUTH=$CLAUDE_CODE_SKIP_BEDROCK_AUTH"} \
    "$IMAGE" \
    -c '
cat > /tmp/life.conf <<CONF
ORGANS=/opt/bin/comms:/opt/bin/brain/organs/pfc:/opt/bin/tadpole/organs/hippocampus
BODY_PART=tadpole
DEFAULT_ORGAN=pfc
MEMORY_DB=/opt/bin/tadpole/organs/hippocampus/memory.db
CIRC_LOCAL_ONLY=1
CONF
echo "CADENCE=1" > /opt/bin/brain/organs/pfc/organ.conf
echo "CADENCE=1" > /opt/bin/comms/organ.conf
exec bash /opt/bin/life/spark-loop.sh /tmp/life.conf
'
