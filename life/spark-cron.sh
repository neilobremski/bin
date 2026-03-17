#!/usr/bin/env bash
# Cron wrapper for spark.sh — logs output, cleans old logs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME/.life/spark"
mkdir -p "$LOG_DIR"

# Remove logs older than 7 days
find "$LOG_DIR" -name '*.log' -mtime +6 -delete 2>/dev/null || true

# Run spark, append all output to today's log
"$SCRIPT_DIR/spark.sh" "$@" >> "$LOG_DIR/$(date +%Y-%m-%d).log" 2>&1
