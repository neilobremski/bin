#!/usr/bin/env bash
# Stomach: produces a payload and sends it via the circulatory system.
# Publishes to source-based topic — the ganglion routes it.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

# Produce a payload (digest food)
meals=0
[ -f "$DIR/meals.count" ] && meals=$(cat "$DIR/meals.count")
meals=$((meals + 1))
echo "$meals" > "$DIR/meals.count"

payload="meal $meals digested at $(date -u +%H:%M:%S)"
ref=$(echo "$payload" | circ-put -)

# Publish to nervous system (source-based topic, not retained — this is an event)
if [ -n "${MQTT_HOST:-}" ] && command -v mqtt-pub >/dev/null 2>&1; then
  mqtt-pub -t "tadpole/stomach" -m "food circ:$ref" 2>/dev/null || true
fi

echo "ok meal $meals circ:$ref" > "$DIR/health.txt"
echo "stomach: produced meal $meals, ref=$ref" >&2
