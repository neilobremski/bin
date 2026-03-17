#!/usr/bin/env bash
# Spinal cord: drain MQTT messages, route to organ stimulus files.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(cd "$DIR/../.." && pwd)"

# Source mqtt helper
LIFE_DIR="${LIFE_DIR:-$(cd "$DIR/../../.." && pwd)/life}"
[ -f "$LIFE_DIR/mqtt.sh" ] && . "$LIFE_DIR/mqtt.sh"

# Drain messages (2-second timeout, max 10)
messages=$(mqtt_sub -t "tadpole/#" -W 2 -C 10 2>/dev/null || true)

if [ -z "$messages" ]; then
  echo "ok idle" > "$DIR/health.txt"
  echo "spine: no messages" >&2
  exit 0
fi

routed=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  echo "$line" >> "$CONF_DIR/organs/tail/stimulus.txt"
  routed=$((routed + 1))
done <<< "$messages"

echo "ok routed $routed" > "$DIR/health.txt"
echo "spine: routed $routed messages to tail" >&2
