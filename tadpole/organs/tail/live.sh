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
  echo "ok swimming ($stimulus)" > "$DIR/health.txt"
else
  echo "ok idle" > "$DIR/health.txt"
fi

echo "tail: swims=$swims stimulus='$stimulus'" >&2
