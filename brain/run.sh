#!/usr/bin/env bash
# run.sh — Boot a brain body part in Docker.
#
# Usage:
#   brain/run.sh <identity-dir>
#   brain/run.sh ../tadpole     # run as tadpole
#   brain/run.sh ~/.claude      # run with global identity from ~/.claude/CLAUDE.md
#
# The identity-dir must contain a CLAUDE.md file. It gets mounted as
# /brain/.claude/ inside the container, layering identity onto the
# primal brain functioning.
#
# Environment variables (optional, override via .env or export):
#   MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS
#   ANTHROPIC_* (Bedrock credentials for claude CLI)
#   SPARK_INTERVAL (default: 60)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IDENTITY_DIR="${1:?Usage: brain/run.sh <identity-dir>}"
IDENTITY_DIR="$(cd "$IDENTITY_DIR" && pwd)"

if [ ! -f "$IDENTITY_DIR/CLAUDE.md" ]; then
    echo "error: $IDENTITY_DIR/CLAUDE.md not found" >&2
    exit 1
fi

IMAGE_NAME="brain:latest"

# Build if image doesn't exist
if ! docker image inspect "$IMAGE_NAME" > /dev/null 2>&1; then
    echo "Building brain image..."
    docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR/Dockerfile" "$BIN_ROOT"
fi

# Name the container after the identity dir
CONTAINER_NAME="brain-$(basename "$IDENTITY_DIR")"

CIRC_DIR="${CIRC_DIR:-$HOME/.life/circ}"
mkdir -p "$CIRC_DIR"
chmod 1777 "$CIRC_DIR" 2>/dev/null || true

echo "Starting $CONTAINER_NAME (identity: $IDENTITY_DIR/CLAUDE.md)"

exec docker run --rm -it \
    --name "$CONTAINER_NAME" \
    -v "$IDENTITY_DIR:/brain/.claude" \
    -v "brain-${CONTAINER_NAME}-memory:/brain/organs/hippocampus" \
    -v "${CIRC_DIR:-$HOME/.life/circ}:/brain/.life/circ" \
    -e "MQTT_HOST=${MQTT_HOST:-localhost}" \
    -e "MQTT_PORT=${MQTT_PORT:-1883}" \
    -e "MQTT_USER=${MQTT_USER:-}" \
    -e "MQTT_PASS=${MQTT_PASS:-}" \
    -e "BODY_PART=brain" \
    -e "MEMORY_DB=/brain/organs/hippocampus/memory.db" \
    -e "CIRC_DIR=/brain/.life/circ" \
    -e "CIRC_LOCAL_ONLY=1" \
    -e "SPARK_INTERVAL=${SPARK_INTERVAL:-60}" \
    -e "GANGLION_LISTEN_DURATION=${GANGLION_LISTEN_DURATION:-50}" \
    ${ANTHROPIC_BEDROCK_BASE_URL:+-e "ANTHROPIC_BEDROCK_BASE_URL=$ANTHROPIC_BEDROCK_BASE_URL"} \
    ${ANTHROPIC_CUSTOM_HEADERS:+-e "ANTHROPIC_CUSTOM_HEADERS=$ANTHROPIC_CUSTOM_HEADERS"} \
    ${ANTHROPIC_API_KEY:+-e "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"} \
    ${CLAUDE_CODE_USE_BEDROCK:+-e "CLAUDE_CODE_USE_BEDROCK=$CLAUDE_CODE_USE_BEDROCK"} \
    ${CLAUDE_CODE_SKIP_BEDROCK_AUTH:+-e "CLAUDE_CODE_SKIP_BEDROCK_AUTH=$CLAUDE_CODE_SKIP_BEDROCK_AUTH"} \
    --network host \
    "$IMAGE_NAME"
