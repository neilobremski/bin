#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
COUNT_FILE="$DIR/beats.count"
HEALTH_FILE="$DIR/health.json"

# Read current beat count (0 if first beat)
beats=0
[ -f "$COUNT_FILE" ] && beats=$(cat "$COUNT_FILE")
beats=$((beats + 1))

# Write beat count
echo "$beats" > "$COUNT_FILE"

# Write health
cat > "$HEALTH_FILE" <<EOF
{"status":"ok","ts":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","organ":"heart","beats":$beats}
EOF

echo "heart beat #$beats" >&2
