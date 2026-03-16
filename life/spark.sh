#!/usr/bin/env bash
# Life Spark — cron-based organ launcher
# Discovers organ directories, checks cadence and singleton, launches live.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

# --- Organ discovery ---
# Priority: 1) argument (manifest file), 2) $ORGANS env var, 3) organs.conf
ORGAN_DIRS=()

if [[ $# -ge 1 ]] && [[ -f "$1" ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"            # strip comments
    line="${line#"${line%%[![:space:]]*}"}"  # trim leading whitespace
    line="${line%"${line##*[![:space:]]}"}"  # trim trailing whitespace
    [[ -z "$line" ]] && continue
    # Resolve relative paths against manifest location
    [[ "$line" != /* ]] && line="$(cd "$(dirname "$1")" && pwd)/$line"
    ORGAN_DIRS+=("$line")
  done < "$1"
elif [[ -n "${ORGANS:-}" ]]; then
  IFS=':' read -ra ORGAN_DIRS <<< "$ORGANS"
else
  CONF="$SCRIPT_DIR/organs.conf"
  if [[ -f "$CONF" ]]; then
    while IFS= read -r line; do
      line="${line%%#*}"
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [[ -z "$line" ]] && continue
      [[ "$line" != /* ]] && line="$SCRIPT_DIR/$line"
      ORGAN_DIRS+=("$line")
    done < "$CONF"
  fi
fi

if [[ ${#ORGAN_DIRS[@]} -eq 0 ]]; then
  log "No organs configured — clean exit"
  exit 0
fi

# --- Process each organ ---
for dir in "${ORGAN_DIRS[@]}"; do
  name="$(basename "$dir")"

  if [[ ! -x "$dir/live.sh" ]]; then
    log "$name: no executable live.sh — skipping"
    continue
  fi

  # Singleton check
  if [[ -f "$dir/.spark.pid" ]]; then
    pid=$(cat "$dir/.spark.pid")
    if kill -0 "$pid" 2>/dev/null; then
      log "$name: already running (PID $pid) — skipping"
      continue
    fi
  fi

  # Cadence check
  if [[ -f "$dir/organ.json" ]]; then
    cadence=$(sed -n 's/.*"cadence"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$dir/organ.json" | head -1)
    if [[ -n "$cadence" ]] && [[ -f "$dir/.spark.last" ]]; then
      last=$(cat "$dir/.spark.last")
      last_epoch=$(date -d "$last" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "$last" +%s 2>/dev/null || echo 0)
      now_epoch=$(date +%s)
      elapsed=$(( (now_epoch - last_epoch) / 60 ))
      if [[ $elapsed -lt $cadence ]]; then
        log "$name: cadence ${cadence}m, ${elapsed}m elapsed — skipping"
        continue
      fi
    fi
  fi

  # Launch
  log "$name: launching"
  nohup "$dir/live.sh" >> "$dir/.spark.log" 2>&1 &
  echo $! > "$dir/.spark.pid"
  date -u +"%Y-%m-%dT%H:%M:%S" > "$dir/.spark.last"
  log "$name: started (PID $!)"
done
