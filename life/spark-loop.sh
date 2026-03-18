#!/usr/bin/env bash
# spark-loop.sh — run spark continuously (no cron needed).
# Useful for Docker containers, development, and demos.
#
# Usage: spark-loop.sh [life.conf]
#   SPARK_INTERVAL=10 spark-loop.sh   # 10-second cycles
set -euo pipefail

INTERVAL="${SPARK_INTERVAL:-60}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "spark-loop: starting (interval=${INTERVAL}s)" >&2

while true; do
  "$SCRIPT_DIR/spark.sh" "$@" || true
  sleep "$INTERVAL"
done
