#!/usr/bin/env bash
# Life Spark — cron-based organ launcher
# Sources life.conf for configuration, launches organs with flock singleton.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_DIR="${HOME}/.life/locks"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

# --- Configuration ---
# Find and source life.conf. Priority: argument > cwd > home.
# life.conf is a sourceable shell file. ORGANS is colon-separated paths.
CONF=""
if [[ $# -ge 1 ]] && [[ -f "$1" ]]; then
  CONF="$1"
elif [[ -f "$PWD/life.conf" ]]; then
  CONF="$PWD/life.conf"
elif [[ -f "$HOME/life.conf" ]]; then
  CONF="$HOME/life.conf"
fi

if [[ -z "$CONF" ]]; then
  log "No life.conf found — clean exit"
  exit 0
fi

CONF_DIR="$(cd "$(dirname "$CONF")" && pwd)"

# Export all vars so organs inherit them
set -a
# shellcheck source=/dev/null
source "$CONF"
set +a

# --- Build organ list ---
ORGAN_DIRS=()
if [[ -n "${ORGANS:-}" ]]; then
  IFS=':' read -ra _raw <<< "$ORGANS"
  for p in "${_raw[@]}"; do
    p="${p#"${p%%[![:space:]]*}"}"  # trim
    p="${p%"${p##*[![:space:]]}"}"
    [[ -z "$p" ]] && continue
    [[ "$p" != /* ]] && p="$CONF_DIR/$p"
    ORGAN_DIRS+=("$p")
  done
fi

if [[ ${#ORGAN_DIRS[@]} -eq 0 ]]; then
  log "No ORGANS in life.conf — clean exit"
  exit 0
fi

# --- Ensure lock directory exists ---
mkdir -p "$LOCK_DIR"

# --- Process each organ ---
for dir in "${ORGAN_DIRS[@]}"; do
  name="$(basename "$dir")"

  if [[ ! -x "$dir/live.sh" ]]; then
    log "$name: no executable live.sh — skipping"
    continue
  fi

  # Cadence check (optional — only if organ.json exists with cadence)
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

  # Singleton via flock — spark owns this, not the organ.
  # Uses a per-organ lock file. flock is released automatically when the
  # process exits, so no stale PID problem.
  lock_file="$LOCK_DIR/$name.lock"

  (
    if ! flock -n 9; then
      log "$name: already running — skipping"
      exit 0
    fi

    log "$name: launching"
    date +%s > "$dir/.spark.last"
    "$dir/live.sh" >> "$dir/.spark.log" 2>&1
    log "$name: finished"
  ) 9>"$lock_file" &

  log "$name: started (PID $!)"
done
