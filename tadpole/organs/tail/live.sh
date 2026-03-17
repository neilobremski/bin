#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

# Read and consume stimulus
if [ -f "$DIR/stimulus.txt" ]; then
  stimulus=$(cat "$DIR/stimulus.txt")
  > "$DIR/stimulus.txt"
else
  stimulus=""
fi

# Count swims
swims=0
[ -f "$DIR/swims.count" ] && swims=$(cat "$DIR/swims.count")

if [ -n "$stimulus" ]; then
  swims=$((swims + 1))
  echo "$swims" > "$DIR/swims.count"

  # If stimulus has a circulatory reference, retrieve the payload
  payload=""
  if echo "$stimulus" | grep -q "ref:"; then
    ref=$(echo "$stimulus" | grep -o 'ref:[^ ]*' | head -1 | cut -d: -f2)
    if [ -n "$ref" ] && command -v circ-get >/dev/null 2>&1; then
      payload=$(circ-get "$ref" 2>/dev/null || echo "")
    fi
  fi

  if [ -n "$payload" ]; then
    echo "ok swimming (payload: $payload)" > "$DIR/health.txt"
  else
    echo "ok swimming ($stimulus)" > "$DIR/health.txt"
  fi
else
  echo "ok idle" > "$DIR/health.txt"
fi

echo "tail: swims=$swims" >&2
