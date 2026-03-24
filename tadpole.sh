#!/usr/bin/env bash
# tadpole.sh — Run the tadpole organism.
#
# Two body parts in Docker, connected via MQTT:
#   Brain:  PFC + hippocampus (thinks, generates replies)
#   Comms:  email I/O (checks gmail, sends replies)
#
# Usage:
#   ./tadpole.sh              # mock gmail (default)
#   ./tadpole.sh --real       # real gmail (needs GAS_BRIDGE_* env vars)
#   ./tadpole.sh down         # stop everything
#
# Mock email interaction (separate terminal):
#   echo '{"id":"001","from":"you@example.com","subject":"Hi","body":"Hello!","labels":["UNREAD","Tadpole"]}' \
#     > tadpole/.mock_gmail/inbox/001.json
#   cat tadpole/.mock_gmail/sent/*.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/tadpole"

# Handle 'down' command
if [ "${1:-}" = "down" ]; then
    docker compose down
    exit 0
fi

# Gmail mode
if [ "${1:-}" = "--real" ]; then
    shift
    echo "Using real Gmail"
else
    GMAIL_MOCK_DIR="${GMAIL_MOCK_DIR:-.mock_gmail}"
    mkdir -p "$GMAIL_MOCK_DIR/inbox" "$GMAIL_MOCK_DIR/sent"
    [ -f "$GMAIL_MOCK_DIR/next_id.txt" ] || echo "1" > "$GMAIL_MOCK_DIR/next_id.txt"
    export GMAIL_MOCK_DIR
    echo "Mock Gmail: $(pwd)/$GMAIL_MOCK_DIR"
    echo "  Send: echo '{\"id\":\"001\",...}' > $(pwd)/$GMAIL_MOCK_DIR/inbox/001.json"
    echo "  Read: cat $(pwd)/$GMAIL_MOCK_DIR/sent/*.json"
fi

echo "Starting tadpole (Ctrl-C to stop)..."
echo "---"
exec docker compose up --build
