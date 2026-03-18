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

echo "heart beat #$beats" >&2
