#!/usr/bin/env bash
# tadpole.sh — launch or stop the tadpole organism.
# Usage: ./tadpole.sh [up|down|logs]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
export HOST_UID="$(id -u)"
export HOST_GID="$(id -g)"

# Pass through Anthropic env vars (already exported by caller or shell)
# docker-compose.yml references ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL

CMD="${1:-up}"

case "$CMD" in
  down|stop)
    docker compose -f "$DIR/tadpole/docker-compose.yml" down "$@"
    ;;
  logs)
    shift
    docker compose -f "$DIR/tadpole/docker-compose.yml" logs "$@"
    ;;
  up|start|"")
    # Ensure mock gmail directories exist
    mkdir -p "$DIR/tadpole/mock-gmail"/{inbox,sent,drafts}

    echo "=== Tadpole Organism ==="
    echo "Starting mqtt + brain + body..."
    echo ""
    echo "Mock gmail: $DIR/tadpole/mock-gmail/"
    echo "  Drop a .json file in inbox/ to simulate an incoming email."
    echo "  Replies appear in sent/"
    echo ""

    docker compose -f "$DIR/tadpole/docker-compose.yml" up --build "$@"
    ;;
  *)
    docker compose -f "$DIR/tadpole/docker-compose.yml" "$@"
    ;;
esac
