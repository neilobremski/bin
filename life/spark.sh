#!/usr/bin/env bash
# Life Spark — cron-based organ launcher
# Discovers organ directories, checks cadence and singleton, launches live.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

# --- Organ discovery ---
# Priority: 1) argument, 2) $ORGANS env, 3) organs.conf next to script, 4) ~/organs.conf
ORGAN_DIRS=()

read_manifest() {
  local file="$1" base_dir="$2"
  while IFS= read -r line; do
    line="${line%%#*}"            # strip comments
    line="${line#"${line%%[![:space:]]*}"}"  # trim leading whitespace
    line="${line%"${line##*[![:space:]]}"}"  # trim trailing whitespace
    [[ -z "$line" ]] && continue
    [[ "$line" != /* ]] && line="$base_dir/$line"
    ORGAN_DIRS+=("$line")
  done < "$file"
}

if [[ $# -ge 1 ]] && [[ -f "$1" ]]; then
  read_manifest "$1" "$(cd "$(dirname "$1")" && pwd)"
elif [[ -n "${ORGANS:-}" ]]; then
  IFS=':' read -ra ORGAN_DIRS <<< "$ORGANS"
elif [[ -f "$PWD/organs.conf" ]]; then
  read_manifest "$PWD/organs.conf" "$PWD"
elif [[ -f "$HOME/organs.conf" ]]; then
  read_manifest "$HOME/organs.conf" "$HOME"
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
      last_epoch=$(cat "$dir/.spark.last")
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
  date +%s > "$dir/.spark.last"
  log "$name: started (PID $!)"
done
