#!/usr/bin/env bash
# Stomach: reads stimulus (food), produces a circulatory payload,
# publishes to source-based topic for the ganglion to route.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

# Consume stimulus
if [ -f "$DIR/stimulus.txt" ]; then
  cat "$DIR/stimulus.txt" > /dev/null
  > "$DIR/stimulus.txt"
fi

# Produce a payload
meals=0
[ -f "$DIR/meals.count" ] && meals=$(cat "$DIR/meals.count")
meals=$((meals + 1))
echo "$meals" > "$DIR/meals.count"

payload="meal $meals digested at $(date -u +%H:%M:%S)"
ref=$(echo "$payload" | circ-put -)

# Publish event to nervous system
if [ -n "${MQTT_HOST:-}" ] && command -v mqtt-pub >/dev/null 2>&1; then
  mqtt-pub -t "tadpole/stomach" -m "food circ:$ref" -q 1 2>/dev/null || true
fi

echo "ok meal $meals circ:$ref" > "$DIR/health.txt"
echo "stomach: produced meal $meals, ref=$ref" >&2
