#!/usr/bin/env bash
# Ganglion: drain MQTT messages, route to organ stimulus files.
# Routes by topic name: tadpole/<organ> → organs/<organ>/stimulus.txt
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(cd "$DIR/../.." && pwd)"

# Drain messages with topic names (-v shows topic)
output=$(mqtt-sub -t "tadpole/#" -W 2 -C 10 -v 2>/dev/null || true)

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
  organ="${topic##*/}"

  target="$CONF_DIR/organs/$organ"
  if [ -d "$target" ]; then
    echo "$message" >> "$target/stimulus.txt"
    routed=$((routed + 1))
    echo "ganglion: $topic → $organ" >&2
  else
    dropped=$((dropped + 1))
    dropped_names="${dropped_names}${organ} "
    echo "ganglion: no organ '$organ' for topic $topic — dropped" >&2
  fi
done <<< "$output"

if [ "$dropped" -gt 0 ]; then
  echo "degraded routed $routed dropped $dropped: $dropped_names" > "$DIR/health.txt"
else
  echo "ok routed $routed" > "$DIR/health.txt"
fi
