#!/usr/bin/env bash
# Life Spark — cron-based organ launcher
# Sources life.conf for configuration, launches organs with flock singleton.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_DIR="${HOME}/.life/locks"

# Make repo-root scripts (mqtt-pub, etc.) available to organs
export PATH="$BIN_ROOT:$PATH"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

# --- Configuration ---
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

set -a
# shellcheck source=/dev/null
source "$CONF"
CONF_DIR="$CONF_DIR"
set +a

# --- Build organ list ---
ORGAN_DIRS=()
if [[ -n "${ORGANS:-}" ]]; then
  IFS=':' read -ra _raw <<< "$ORGANS"
  for p in "${_raw[@]}"; do
    p="${p#"${p%%[![:space:]]*}"}"
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

mkdir -p "$LOCK_DIR"

# --- Process each organ ---
for dir in "${ORGAN_DIRS[@]}"; do
  name="$(basename "$dir")"

  if [[ ! -x "$dir/live.sh" ]]; then
    log "$name: no executable live.sh — skipping"
    continue
  fi

  # Determine if this organ should run:
  # - Has cadence and it's due → yes
  # - Has cadence but not due, AND has stimulus → yes
  # - Has cadence but not due, no stimulus → skip
  # - No cadence, has stimulus → yes
  # - No cadence, no stimulus → skip (dormant)
  has_stimulus=false
  [[ -f "$dir/stimulus.txt" ]] && [[ -s "$dir/stimulus.txt" ]] && has_stimulus=true

  cadence=""
  if [[ -f "$dir/organ.conf" ]]; then
    # Source in subshell to prevent variable leakage between organs
    cadence=$(CADENCE=""; source "$dir/organ.conf"; echo "${CADENCE:-}")
  fi

  if [[ -n "$cadence" ]]; then
    # Has cadence — check if due
    cadence_due=true
    if [[ -f "$dir/.spark.last" ]]; then
      last_epoch=$(cat "$dir/.spark.last")
      now_epoch=$(date +%s)
      elapsed=$(( (now_epoch - last_epoch) / 60 ))
      if [[ $elapsed -lt $cadence ]]; then
        cadence_due=false
      fi
    fi

    if [[ "$cadence_due" = false ]] && [[ "$has_stimulus" = false ]]; then
      log "$name: cadence ${cadence}m, ${elapsed}m elapsed — skipping"
      continue
    fi
  else
    # No cadence — only run if stimulus present
    if [[ "$has_stimulus" = false ]]; then
      continue  # dormant, silent skip
    fi
    log "$name: stimulus present"
  fi

  # Singleton via flock
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
