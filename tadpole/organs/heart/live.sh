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
if [ -n "${MQTT_HOST:-}" ] && command -v mqtt-pub >/dev/null 2>&1; then
  mqtt-pub -t "tadpole/tail" -m "beat $beats" -r 2>/dev/null || true
fi

echo "heart beat #$beats" >&2
