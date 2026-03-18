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

swims=0
[ -f "$DIR/swims.count" ] && swims=$(cat "$DIR/swims.count")

if [ -n "$stimulus" ]; then
  swims=$((swims + 1))
  echo "$swims" > "$DIR/swims.count"

  # Check each line for circulatory references
  payload=""
  while IFS= read -r line; do
    if echo "$line" | grep -q "circ:"; then
      ref=$(echo "$line" | grep -o 'circ:[^ ]*' | head -1 | cut -d: -f2)
      if [ -n "$ref" ] && command -v circ-get >/dev/null 2>&1; then
        payload=$(circ-get "$ref" 2>/dev/null || echo "")
      fi
    fi
  done <<< "$stimulus"

  if [ -n "$payload" ]; then
    echo "ok splish splash (payload: $payload)" > "$DIR/health.txt"
  else
    echo "ok splish splash" > "$DIR/health.txt"
  fi
else
  echo "ok idle" > "$DIR/health.txt"
fi

echo "tail: swims=$swims" >&2
