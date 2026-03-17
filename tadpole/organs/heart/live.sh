#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

# Increment beat counter
beats=0
[ -f "$DIR/beats.count" ] && beats=$(cat "$DIR/beats.count")
beats=$((beats + 1))
echo "$beats" > "$DIR/beats.count"

# Report health
echo "ok beat $beats" > "$DIR/health.txt"

# Publish heartbeat to nervous system
if [ -n "${MQTT_HOST:-}" ]; then
  # Source mqtt helper from life/ (relative to this organ's position)
  LIFE_DIR="${LIFE_DIR:-$(cd "$DIR/../../.." && pwd)/life}"
  [ -f "$LIFE_DIR/mqtt.sh" ] && . "$LIFE_DIR/mqtt.sh"
  if command -v mqtt_pub >/dev/null 2>&1; then
    mqtt_pub -t "tadpole/heartbeat" -m "beat $beats" -r 2>/dev/null || true
  fi
fi

echo "heart beat #$beats" >&2
