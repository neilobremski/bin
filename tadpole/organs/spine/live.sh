#!/usr/bin/env bash
# Spinal cord: drain MQTT messages, route to organ stimulus files.
# Subscribes to tadpole/#, writes lines to the target organ's stimulus.txt.
# Runs, drains, exits. Not persistent.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(cd "$DIR/../.." && pwd)"
HOST="${MQTT_HOST:-localhost}"
PORT="${MQTT_PORT:-1883}"

# Subscribe with a short timeout — drain whatever is waiting, then exit.
# -W 2 = wait max 2 seconds for a message. -C 10 = exit after 10 messages max.
messages=$(mosquitto_sub -h "$HOST" -p "$PORT" -t "tadpole/#" -W 2 -C 10 2>/dev/null || true)

if [ -z "$messages" ]; then
  echo "ok idle" > "$DIR/health.txt"
  echo "spine: no messages" >&2
  exit 0
fi

routed=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  # Route all messages to the tail's stimulus
  echo "$line" >> "$CONF_DIR/organs/tail/stimulus.txt"
  routed=$((routed + 1))
done <<< "$messages"

echo "ok routed $routed" > "$DIR/health.txt"
echo "spine: routed $routed messages to tail" >&2
