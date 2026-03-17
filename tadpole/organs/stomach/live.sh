#!/usr/bin/env bash
# Stomach: produces a payload and sends it via the circulatory system.
# Demonstrates organ-to-organ data transfer through circ-put/circ-get.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(cd "$DIR/../.." && pwd)"

# Produce a payload (digest food)
meals=0
[ -f "$DIR/meals.count" ] && meals=$(cat "$DIR/meals.count")
meals=$((meals + 1))
echo "$meals" > "$DIR/meals.count"

payload="meal $meals digested at $(date -u +%H:%M:%S)"
ref=$(echo "$payload" | circ-put -)

# Stimulate the tail with a reference to the payload
echo "food ref:$ref" >> "$CONF_DIR/organs/tail/stimulus.txt"

echo "ok meal $meals ref:$ref" > "$DIR/health.txt"
echo "stomach: produced meal $meals, ref=$ref" >&2
