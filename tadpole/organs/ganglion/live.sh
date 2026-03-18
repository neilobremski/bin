#!/usr/bin/env bash
# Ganglion: drain MQTT messages, route to organ stimulus files.
# Routes by topic: direct topics (tadpole/<target>) go to that organ.
# Source topics use a routing table in ganglion.conf if present.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(cd "$DIR/../.." && pwd)"

# Drain messages with topic names (-v shows topic)
# Uses persistent session (-c) with stable client ID in production.
# GANGLION_CLIENT_ID can be overridden for testing.
CLIENT_ID="${GANGLION_CLIENT_ID:-${BODY_PART:-local}-ganglion}"
output=$(mqtt-sub -t "tadpole/#" -W 2 -C 10 -v -i "$CLIENT_ID" -c 2>/dev/null || true)

if [ -z "$output" ]; then
  echo "ok idle" > "$DIR/health.txt"
  echo "ganglion: no messages" >&2
  exit 0
fi

routed=0
dropped=0
dropped_names=""

while IFS= read -r line; do
  [ -z "$line" ] && continue
  topic="${line%% *}"
  message="${line#* }"
  source="${topic##*/}"

  # Try direct routing: does an organ with this name exist?
  target="$CONF_DIR/organs/$source"
  if [ -d "$target" ] && [ "$source" != "ganglion" ]; then
    echo "$message" >> "$target/stimulus.txt"
    routed=$((routed + 1))
    echo "ganglion: $topic → $source" >&2
  else
    # Source-based routing: check if a route exists for this source
    # Default routes for tadpole (stomach produces food → tail consumes)
    case "$source" in
      stomach) dest="$CONF_DIR/organs/tail" ;;
      *)       dest="" ;;
    esac

    if [ -n "$dest" ] && [ -d "$dest" ]; then
      echo "$message" >> "$dest/stimulus.txt"
      routed=$((routed + 1))
      echo "ganglion: $topic → $(basename "$dest") (routed from $source)" >&2
    else
      dropped=$((dropped + 1))
      dropped_names="${dropped_names}${source} "
      echo "ganglion: no route for '$source' — dropped" >&2
    fi
  fi
done <<< "$output"

if [ "$dropped" -gt 0 ]; then
  echo "degraded routed $routed dropped $dropped: $dropped_names" > "$DIR/health.txt"
else
  echo "ok routed $routed" > "$DIR/health.txt"
fi
