#!/usr/bin/env bash
# Lymph node: immune system organ.
# Scans local organs for health issues and cleans up overflows.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="${CONF_DIR:-$(cd "$DIR/../.." && pwd)}"

# Thresholds
STALE_SECONDS="${STALE_SECONDS:-300}"   # health.txt older than 5 min = stale
MAX_STIMULUS_LINES="${MAX_STIMULUS_LINES:-100}"  # truncate stimulus.txt beyond this
MAX_LOG_BYTES="${MAX_LOG_BYTES:-1048576}"  # truncate .spark.log beyond 1MB

issues=0
total=0
report=""

# Resolve organ directories from ORGANS env (same as ganglion and stimulus)
organ_dirs=()
if [ -n "${ORGANS:-}" ]; then
  IFS=':' read -ra _raw <<< "$ORGANS"
  for p in "${_raw[@]}"; do
    p="${p#"${p%%[![:space:]]*}"}"
    p="${p%"${p##*[![:space:]]}"}"
    [ -z "$p" ] && continue
    [[ "$p" != /* ]] && p="$CONF_DIR/$p"
    [ -d "$p" ] && organ_dirs+=("$p")
  done
fi

for organ_dir in "${organ_dirs[@]}"; do
  organ=$(basename "$organ_dir")

  # Skip self
  [ "$organ" = "lymph" ] && continue

  total=$((total + 1))

  # Check health.txt
  health_file="$organ_dir/health.txt"
  if [ ! -f "$health_file" ]; then
    report="${report}${organ}:no-health "
  else
    status=$(head -c 20 "$health_file" | awk '{print $1}')
    if [ "$status" = "error" ] || [ "$status" = "degraded" ]; then
      issues=$((issues + 1))
      report="${report}${organ}:${status} "
    fi

    # Check staleness
    if [ -n "$(find "$health_file" -mmin +$((STALE_SECONDS / 60)) 2>/dev/null)" ]; then
      issues=$((issues + 1))
      report="${report}${organ}:stale "
    fi
  fi

  # Drain oversized stimulus.txt
  stim_file="$organ_dir/stimulus.txt"
  if [ -f "$stim_file" ]; then
    line_count=$(wc -l < "$stim_file")
    if [ "$line_count" -gt "$MAX_STIMULUS_LINES" ]; then
      tail -n "$MAX_STIMULUS_LINES" "$stim_file" > "$stim_file.tmp"
      mv "$stim_file.tmp" "$stim_file"
      dropped=$((line_count - MAX_STIMULUS_LINES))
      report="${report}${organ}:stimulus-overflow(${dropped}-dropped) "
    fi
  fi

  # Truncate oversized .spark.log
  log_file="$organ_dir/.spark.log"
  if [ -f "$log_file" ]; then
    log_size=$(stat -c%s "$log_file" 2>/dev/null || stat -f%z "$log_file" 2>/dev/null || echo 0)
    if [ "$log_size" -gt "$MAX_LOG_BYTES" ]; then
      tail -c "$MAX_LOG_BYTES" "$log_file" > "$log_file.tmp"
      mv "$log_file.tmp" "$log_file"
      report="${report}${organ}:log-trimmed "
    fi
  fi
done

# Write health summary
if [ "$issues" -eq 0 ]; then
  echo "ok ${total} organs checked" > "$DIR/health.txt"
else
  echo "degraded ${issues} issues: ${report}" > "$DIR/health.txt"
fi

echo "lymph: checked $total organs, $issues issues" >&2
