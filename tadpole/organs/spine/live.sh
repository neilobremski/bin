#!/usr/bin/env bash
# Spinal cord: drain MQTT messages, route to organ stimulus files,
# then spark the target organ directly.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(cd "$DIR/../.." && pwd)"
TAIL_DIR="$CONF_DIR/organs/tail"

# Drain messages (2-second timeout, max 10)
messages=$(mqtt-sub -t "tadpole/#" -W 2 -C 10 2>/dev/null || true)

if [ -z "$messages" ]; then
  echo "ok idle" > "$DIR/health.txt"
  echo "spine: no messages" >&2
  exit 0
fi

routed=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  echo "$line" >> "$TAIL_DIR/stimulus.txt"
  routed=$((routed + 1))
done <<< "$messages"

echo "ok routed $routed" > "$DIR/health.txt"
echo "spine: routed $routed messages to tail" >&2

# Spark the tail — it's event-driven, not on cron
if [ -x "$TAIL_DIR/live.sh" ] && [ -s "$TAIL_DIR/stimulus.txt" ]; then
  "$TAIL_DIR/live.sh" >> "$TAIL_DIR/.spark.log" 2>&1 &
  echo "spine: sparked tail" >&2
fi
