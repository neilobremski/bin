#!/usr/bin/env bash
# checkup.sh — Verify prerequisites for the life system.
# Returns 0 if healthy, 1 if something is missing.
# Can be sourced (exports CHECKUP_ERRORS) or run standalone.
set -euo pipefail

ERRORS=0

check() {
  local label="$1"; shift
  if ! eval "$@" >/dev/null 2>&1; then
    echo "FAIL: $label" >&2
    ERRORS=$((ERRORS + 1))
  else
    echo "  ok: $label" >&2
  fi
}

echo "--- Life System Checkup ---" >&2

# Core commands
check "bash available"      "command -v bash"
check "python3 available"   "command -v python3"
check "sqlite3 module"      "python3 -c 'import sqlite3'"

# Nervous system (optional — degraded without)
if [ -n "${MQTT_HOST:-}" ]; then
  check "mqtt-pub available"  "command -v mqtt-pub"
  check "mqtt-sub available"  "command -v mqtt-sub"
  check "mosquitto broker"    "command -v mosquitto"
else
  echo "  skip: MQTT (no MQTT_HOST set)" >&2
fi

# life.conf
if [ -n "${1:-}" ] && [ -f "$1" ]; then
  CONF="$1"
elif [ -f "$PWD/life.conf" ]; then
  CONF="$PWD/life.conf"
elif [ -f "$HOME/life.conf" ]; then
  CONF="$HOME/life.conf"
else
  CONF=""
fi

if [ -n "$CONF" ]; then
  check "life.conf readable"  "[ -r '$CONF' ]"

  # shellcheck source=/dev/null
  source "$CONF" 2>/dev/null || true
  CONF_DIR="$(cd "$(dirname "$CONF")" && pwd)"

  if [ -n "${ORGANS:-}" ]; then
    IFS=':' read -ra _organs <<< "$ORGANS"
    for p in "${_organs[@]}"; do
      p="${p#"${p%%[![:space:]]*}"}"
      p="${p%"${p##*[![:space:]]}"}"
      [ -z "$p" ] && continue
      [[ "$p" != /* ]] && p="$CONF_DIR/$p"
      name="$(basename "$p")"
      check "organ $name exists"   "[ -d '$p' ]"
      check "organ $name live.sh"  "[ -x '$p/live.sh' ]"
    done
  else
    echo "FAIL: ORGANS not set in life.conf" >&2
    ERRORS=$((ERRORS + 1))
  fi
else
  echo "  skip: no life.conf found" >&2
fi

echo "---" >&2
if [ "$ERRORS" -gt 0 ]; then
  echo "CHECKUP: $ERRORS issue(s) found" >&2
  exit 1
else
  echo "CHECKUP: all clear" >&2
  exit 0
fi
