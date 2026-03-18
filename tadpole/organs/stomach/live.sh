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

# Send food to tail via stimulus
stimulus send tail "food circ:$ref" 2>/dev/null || true

echo "ok meal $meals circ:$ref" > "$DIR/health.txt"
echo "stomach: produced meal $meals, ref=$ref" >&2
