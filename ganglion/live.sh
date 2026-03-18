#!/usr/bin/env bash
# Ganglion v2 — nervous system local node.
# Scans local organs, journals health changes, broadcasts via MQTT, delivers stimulus.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$(cd "$DIR/.." && pwd)"
DB="${GANGLION_DB:-$HOME/.life/ganglion.db}"
BODY="${BODY_PART:-local}"

mkdir -p "$(dirname "$DB")"

# --- Initialize SQLite schema ---
sqlite3 "$DB" <<'SQL'
CREATE TABLE IF NOT EXISTS organs (
    type TEXT NOT NULL,
    id TEXT NOT NULL,
    body_part TEXT NOT NULL,
    health_status TEXT DEFAULT '',
    health_text TEXT DEFAULT '',
    last_seen TEXT DEFAULT '',
    PRIMARY KEY (type, id)
);
CREATE TABLE IF NOT EXISTS health_log (
    type TEXT NOT NULL,
    id TEXT NOT NULL,
    ts TEXT NOT NULL,
    status TEXT NOT NULL,
    health_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_log_type ON health_log(type, id, ts);
SQL

# ================================================================
#  PHASE 1: Scan local organs
# ================================================================

scanned=0

if [ -n "${ORGANS:-}" ]; then
  IFS=':' read -ra organ_paths <<< "$ORGANS"

  # Count duplicates per type to assign IDs
  declare -A type_count
  for p in "${organ_paths[@]}"; do
    p="${p#"${p%%[![:space:]]*}"}"
    p="${p%"${p##*[![:space:]]}"}"
    [ -z "$p" ] && continue
    [[ "$p" != /* ]] && p="$CONF_DIR/$p"
    t="$(basename "$p")"
    type_count[$t]=$(( ${type_count[$t]:-0} + 1 ))
  done

  # Track per-type index for duplicate naming
  declare -A type_idx

  for p in "${organ_paths[@]}"; do
    p="${p#"${p%%[![:space:]]*}"}"
    p="${p%"${p##*[![:space:]]}"}"
    [ -z "$p" ] && continue
    [[ "$p" != /* ]] && p="$CONF_DIR/$p"
    [ -d "$p" ] || continue

    t="$(basename "$p")"

    # Skip self
    [ "$t" = "ganglion" ] && continue

    # Build organ ID
    type_idx[$t]=$(( ${type_idx[$t]:-0} + 1 ))
    if [ "${type_count[$t]}" -gt 1 ]; then
      organ_id="${t}-${BODY}-${type_idx[$t]}"
    else
      organ_id="${t}-${BODY}"
    fi

    # Read health
    health_text=""
    health_status=""
    if [ -f "$p/health.txt" ]; then
      health_text=$(cat "$p/health.txt" 2>/dev/null || true)
      health_status=$(echo "$health_text" | head -1 | awk '{print $1}')
    fi

    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Upsert into registry
    sqlite3 "$DB" "INSERT INTO organs(type,id,body_part,health_status,health_text,last_seen)
      VALUES('$t','$organ_id','$BODY','$health_status','$(echo "$health_text" | sed "s/'/''/g")','$now')
      ON CONFLICT(type,id) DO UPDATE SET
        body_part='$BODY',
        health_status='$health_status',
        health_text='$(echo "$health_text" | sed "s/'/''/g")',
        last_seen='$now';"

    scanned=$((scanned + 1))

    # --- PHASE 2: Journal health changes (duplicate collapsing) ---
    last_logged_status=$(sqlite3 "$DB" \
      "SELECT status FROM health_log WHERE type='$t' AND id='$organ_id' ORDER BY ts DESC LIMIT 1;" 2>/dev/null || true)

    if [ "$last_logged_status" != "$health_status" ]; then
      sqlite3 "$DB" "INSERT INTO health_log(type,id,ts,status,health_text)
        VALUES('$t','$organ_id','$now','$health_status','$(echo "$health_text" | sed "s/'/''/g")');"
    fi
  done
fi

# ================================================================
#  PHASE 3: MQTT broadcast + receive (if MQTT_HOST is set)
# ================================================================

routed=0

if [ -n "${MQTT_HOST:-}" ] && command -v mqtt-pub >/dev/null 2>&1 && command -v mqtt-sub >/dev/null 2>&1; then
  CLIENT_ID="${GANGLION_CLIENT_ID:-${BODY}-ganglion}"

  # Broadcast local registry as JSON
  local_json=$(sqlite3 -json "$DB" "SELECT type,id,body_part,health_status,health_text,last_seen FROM organs WHERE body_part='$BODY';" 2>/dev/null || echo "[]")
  mqtt-pub -t "life/${BODY}/registry" -m "$local_json" -r 2>/dev/null || true

  # Receive other ganglions' registries (brief listen)
  reg_output=$(mqtt-sub -t "life/+/registry" -W 1 -C 5 -v 2>/dev/null || true)
  if [ -n "$reg_output" ]; then
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      topic="${line%% *}"
      payload="${line#* }"
      # Extract body part from topic: life/<body_part>/registry
      remote_body=$(echo "$topic" | cut -d/ -f2)
      [ "$remote_body" = "$BODY" ] && continue  # skip own broadcast

      # Parse JSON array and merge (requires jq-less approach with sqlite3)
      # Each entry is a JSON object; use python or simple parsing
      # For simplicity, use sqlite3 JSON if available, otherwise skip
      if command -v python3 >/dev/null 2>&1; then
        python3 -c "
import json, sys
try:
    data = json.loads(sys.argv[1])
    for row in data:
        t = row.get('type','').replace(\"'\",\"''\")
        i = row.get('id','').replace(\"'\",\"''\")
        bp = row.get('body_part','').replace(\"'\",\"''\")
        hs = row.get('health_status','').replace(\"'\",\"''\")
        ht = row.get('health_text','').replace(\"'\",\"''\")
        ls = row.get('last_seen','').replace(\"'\",\"''\")
        print(f\"INSERT INTO organs(type,id,body_part,health_status,health_text,last_seen) VALUES('{t}','{i}','{bp}','{hs}','{ht}','{ls}') ON CONFLICT(type,id) DO UPDATE SET body_part='{bp}',health_status='{hs}',health_text='{ht}',last_seen='{ls}';\")
except: pass
" "$payload" | sqlite3 "$DB" 2>/dev/null || true
      fi
    done <<< "$reg_output"
  fi

  # Drain stimulus messages
  stim_output=$(mqtt-sub -t "life/+/stimulus/#" -W 2 -C 10 -v -i "$CLIENT_ID" -c 2>/dev/null || true)
  if [ -n "$stim_output" ]; then
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      topic="${line%% *}"
      message="${line#* }"

      # Topic format: life/<body_part>/stimulus/<type>
      target_type=$(echo "$topic" | awk -F/ '{print $NF}')

      # Skip routing to self
      [ "$target_type" = "ganglion" ] && continue

      # Find local organ of this type
      if [ -n "${ORGANS:-}" ]; then
        IFS=':' read -ra _paths <<< "$ORGANS"
        for op in "${_paths[@]}"; do
          op="${op#"${op%%[![:space:]]*}"}"
          op="${op%"${op##*[![:space:]]}"}"
          [ -z "$op" ] && continue
          [[ "$op" != /* ]] && op="$CONF_DIR/$op"
          if [ "$(basename "$op")" = "$target_type" ] && [ -d "$op" ]; then
            echo "$message" >> "$op/stimulus.txt"
            routed=$((routed + 1))
            echo "ganglion: life/stimulus/$target_type -> $target_type" >&2
            break
          fi
        done
      fi
    done <<< "$stim_output"
  fi
fi

# ================================================================
#  PHASE 4: Deliver local stimulus (from stimulus.txt written directly)
# ================================================================

# (Stimulus written directly by the stimulus CLI in local mode is already
#  in the organ's stimulus.txt — no extra routing needed.)

# ================================================================
#  Report health
# ================================================================

echo "ok scanned $scanned routed $routed" > "$DIR/health.txt"
echo "ganglion: scanned=$scanned routed=$routed" >&2
