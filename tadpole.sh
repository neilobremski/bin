#!/usr/bin/env bash
# tadpole.sh — launch the tadpole v4 container.
# Single docker run. No docker-compose. No MQTT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_DIR="${MOCK_DIR:-$SCRIPT_DIR/tadpole/mock-gmail}"

# Create mock gmail dirs if needed
mkdir -p "$MOCK_DIR/inbox" "$MOCK_DIR/inbox/.processed" "$MOCK_DIR/sent"

# Build image
docker build -t tadpole:latest -f "$SCRIPT_DIR/tadpole/Dockerfile" "$SCRIPT_DIR"

# Run container
exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "$MOCK_DIR:/mock-gmail" \
    -e GMAIL_MOCK_DIR=/mock-gmail \
    -e COMMS_AUTO_CHECK=1 \
    -e SPARK_INTERVAL="${SPARK_INTERVAL:-10}" \
    -e CIRC_LOCAL_ONLY=1 \
    ${ANTHROPIC_API_KEY:+-e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"} \
    ${CLAUDE_CODE_USE_BEDROCK:+-e CLAUDE_CODE_USE_BEDROCK="$CLAUDE_CODE_USE_BEDROCK"} \
    ${AWS_REGION:+-e AWS_REGION="$AWS_REGION"} \
    ${AWS_ACCESS_KEY_ID:+-e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID"} \
    ${AWS_SECRET_ACCESS_KEY:+-e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY"} \
    tadpole:latest
